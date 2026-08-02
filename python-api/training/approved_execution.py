from __future__ import annotations

import hashlib
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Collection, Iterable


DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
MAX_FEATURES = 2_048
MAX_SNAPSHOT_BYTES = 10 * 1024 * 1024 * 1024


class ApprovedExecutionError(ValueError):
    """Fail-closed approved training context validation error."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_features(values: Iterable[str], *, label: str) -> tuple[str, ...]:
    items = tuple(values)
    if not items or len(items) > MAX_FEATURES:
        raise ApprovedExecutionError(f"{label}-count-invalid")
    if any(not isinstance(item, str) or IDENTIFIER_RE.fullmatch(item) is None for item in items):
        raise ApprovedExecutionError(f"{label}-identifier-invalid")
    if len(set(items)) != len(items):
        raise ApprovedExecutionError(f"{label}-duplicate")
    return items


@dataclass(frozen=True)
class ApprovedTrainingExecution:
    job_id: str
    approved_payload_hash: str
    data_snapshot_sha256: str
    feature_contract_sha256: str
    candidate_commit_sha: str
    target: str
    model_type: str
    selected_features: tuple[str, ...]
    removed_features: tuple[str, ...]
    workspace: Path
    snapshot_path: Path

    @classmethod
    def create(
        cls,
        *,
        job_id: str,
        approved_payload_hash: str,
        data_snapshot_sha256: str,
        feature_contract_sha256: str,
        candidate_commit_sha: str,
        target: str,
        model_type: str,
        selected_features: Iterable[str],
        removed_features: Iterable[str],
        workspace: Path,
        snapshot_path: Path,
    ) -> "ApprovedTrainingExecution":
        try:
            normalized_job_id = str(uuid.UUID(job_id))
        except (ValueError, AttributeError) as exc:
            raise ApprovedExecutionError("job-id-invalid") from exc
        if DIGEST_RE.fullmatch(approved_payload_hash or "") is None:
            raise ApprovedExecutionError("approved-payload-hash-invalid")
        if DIGEST_RE.fullmatch(data_snapshot_sha256 or "") is None:
            raise ApprovedExecutionError("data-snapshot-hash-invalid")
        if DIGEST_RE.fullmatch(feature_contract_sha256 or "") is None:
            raise ApprovedExecutionError("feature-contract-hash-invalid")
        if COMMIT_RE.fullmatch(candidate_commit_sha or "") is None:
            raise ApprovedExecutionError("candidate-commit-invalid")
        if target != "win" or model_type != "lightgbm":
            raise ApprovedExecutionError("training-shape-not-approved")

        selected = _validate_features(selected_features, label="selected-features")
        removed = tuple(removed_features)
        if len(removed) > MAX_FEATURES:
            raise ApprovedExecutionError("removed-features-count-invalid")
        if any(not isinstance(item, str) or IDENTIFIER_RE.fullmatch(item) is None for item in removed):
            raise ApprovedExecutionError("removed-features-identifier-invalid")
        if len(set(removed)) != len(removed) or any(item not in selected for item in removed):
            raise ApprovedExecutionError("removed-features-binding-invalid")
        if not [item for item in selected if item not in set(removed)]:
            raise ApprovedExecutionError("effective-features-empty")

        temp_root = Path(tempfile.gettempdir()).resolve()
        workspace_path = Path(workspace)
        snapshot_source = Path(snapshot_path)
        if workspace_path.is_symlink() or snapshot_source.is_symlink():
            raise ApprovedExecutionError("approved-path-symlink-forbidden")
        try:
            resolved_workspace = workspace_path.resolve(strict=True)
            resolved_snapshot = snapshot_source.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise ApprovedExecutionError("approved-path-unavailable") from exc
        if (
            not resolved_workspace.is_dir()
            or resolved_workspace == temp_root
            or not resolved_workspace.is_relative_to(temp_root)
        ):
            raise ApprovedExecutionError("workspace-not-isolated")
        if (
            not resolved_snapshot.is_file()
            or not resolved_snapshot.is_relative_to(resolved_workspace)
            or resolved_snapshot.suffix.lower() != ".db"
        ):
            raise ApprovedExecutionError("snapshot-path-invalid")
        snapshot_size = resolved_snapshot.stat().st_size
        if snapshot_size < 1 or snapshot_size > MAX_SNAPSHOT_BYTES:
            raise ApprovedExecutionError("snapshot-size-invalid")
        if _sha256_file(resolved_snapshot) != data_snapshot_sha256:
            raise ApprovedExecutionError("data-snapshot-hash-mismatch")

        return cls(
            job_id=normalized_job_id,
            approved_payload_hash=approved_payload_hash,
            data_snapshot_sha256=data_snapshot_sha256,
            feature_contract_sha256=feature_contract_sha256,
            candidate_commit_sha=candidate_commit_sha,
            target=target,
            model_type=model_type,
            selected_features=selected,
            removed_features=removed,
            workspace=resolved_workspace,
            snapshot_path=resolved_snapshot,
        )

    @property
    def artifact_directory(self) -> Path:
        return self.workspace / "artifacts"

    def verify_snapshot(self) -> None:
        if self.snapshot_path.is_symlink() or not self.snapshot_path.is_file():
            raise ApprovedExecutionError("snapshot-path-invalid")
        snapshot_size = self.snapshot_path.stat().st_size
        if snapshot_size < 1 or snapshot_size > MAX_SNAPSHOT_BYTES:
            raise ApprovedExecutionError("snapshot-size-invalid")
        if _sha256_file(self.snapshot_path) != self.data_snapshot_sha256:
            raise ApprovedExecutionError("data-snapshot-hash-mismatch")

    def prepare_artifact_directory(self) -> Path:
        artifact_directory = self.artifact_directory
        if artifact_directory.is_symlink():
            raise ApprovedExecutionError("artifact-directory-symlink-forbidden")
        artifact_directory.mkdir(parents=False, exist_ok=True)
        resolved = artifact_directory.resolve(strict=True)
        if not resolved.is_dir() or not resolved.is_relative_to(self.workspace):
            raise ApprovedExecutionError("artifact-directory-not-isolated")
        return resolved

    @property
    def effective_features(self) -> tuple[str, ...]:
        removed = set(self.removed_features)
        return tuple(item for item in self.selected_features if item not in removed)

    def validate_request(
        self,
        *,
        target: str,
        model_type: str,
        force_sync: bool,
    ) -> None:
        if target != self.target or model_type != self.model_type:
            raise ApprovedExecutionError("training-request-binding-mismatch")
        if force_sync:
            raise ApprovedExecutionError("approved-training-sync-forbidden")

    def select_feature_columns(
        self,
        produced_columns: Collection[str],
        *,
        future_fields: Collection[str],
    ) -> tuple[str, ...]:
        effective = self.effective_features
        future = set(future_fields)
        if any(feature in future for feature in effective):
            raise ApprovedExecutionError("approved-features-contain-future-field")
        available = set(produced_columns)
        missing = [feature for feature in effective if feature not in available]
        if missing:
            raise ApprovedExecutionError("approved-features-unavailable")
        return effective
