from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import stat
import tempfile
import threading
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO, Callable, Collection, Iterable, Mapping

from training.approved_execution import ApprovedExecutionError, MAX_SNAPSHOT_BYTES
from training.retrain_worker import (
    ARTIFACT_MEDIA_TYPE,
    FAILURE_CODES,
    MAX_ARTIFACT_BYTES,
    RegisteredArtifact,
    RetrainGatewayUnavailable,
    RetrainLease,
    RetrainLeaseLost,
    RetrainWorkerError,
)


LOCAL_EXECUTION_POLICY = "local-admin-train"
LOCAL_JOB_STATES = frozenset(
    {
        "preparing",
        "queued",
        "claimed",
        "running",
        "artifact-registered",
        "completed",
        "failed",
    }
)
LOCAL_ACTIVE_STATES = frozenset(
    {"preparing", "queued", "claimed", "running", "artifact-registered"}
)
LOCAL_TARGETS = frozenset({"win", "place3", "win_tie", "speed_deviation", "rank"})
LOCAL_TRAINING_PARAMETER_KEYS = frozenset(
    {
        "force_sync",
        "test_size",
        "cv_folds",
        "use_sqlite",
        "ultimate_mode",
        "use_optimizer",
        "use_optuna",
        "optuna_trials",
        "optuna_timeout",
        "training_date_from",
        "training_date_to",
    }
)
LOCAL_CONTRACT_KEYS = frozenset(
    {
        "schema_version",
        "job_id",
        "authorization_id",
        "contract_id",
        "created_at",
        "execution_policy",
        "owner_id",
        "target",
        "model_type",
        "selected_features",
        "removed_features",
        "data_snapshot_sha256",
        "feature_contract_sha256",
        "candidate_commit_sha",
        "source_tree_sha256",
        "active_model_id",
        "active_model_sha256",
        "training_parameters",
    }
)
LOCAL_BUNDLE_KEYS = frozenset(
    {
        "schema_version",
        "execution_policy",
        "contract",
        "contract_sha256",
        "worker_id",
        "fencing_token",
        "record_version",
        "lease_expires_at",
    }
)

DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
WORKER_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{2,79}$")
DATE_MONTH_RE = re.compile(r"^[0-9]{4}-[0-9]{2}$")
OBJECT_RE = re.compile(
    r"^retrain/([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})/"
    r"([0-9a-f]{64})\.joblib$"
)
VISIBLE_MODEL_RE = re.compile(
    r"^model_(win|place3|win_tie|speed_deviation|rank)_lightgbm_"
    r"([A-Za-z0-9][A-Za-z0-9_.:-]{0,127})\.joblib$"
)
MAX_REQUEST_BYTES = 256 * 1024
PREPARATION_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")
MIN_PREPARATION_TTL_SECONDS = 30
MAX_PREPARATION_TTL_SECONDS = 900
MAX_SOURCE_FILE_BYTES = 32 * 1024 * 1024
MAX_SOURCE_TREE_BYTES = 512 * 1024 * 1024
SOURCE_SUFFIXES = frozenset({".py", ".json", ".yaml", ".yml", ".toml", ".txt"})
SOURCE_EXCLUDED_PARTS = frozenset(
    {
        ".git",
        ".next",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "cache",
        ".cache",
        "data",
        "models",
        "logs",
        "tests",
        "test-results",
    }
)
SOURCE_ROOT_FILES = frozenset(
    {
        "python-api/requirements-lock.txt",
        "python-api/requirements.txt",
        "python-api/runtime.txt",
        "keiba/config.yaml",
        "keiba/feature_catalog.yaml",
        "keiba/requirements.txt",
    }
)
SOURCE_ROOT_DIRECTORIES = ("python-api", "keiba/keiba_ai")


class LocalRetrainError(ApprovedExecutionError):
    """Sanitized local retraining contract or persistence failure."""


class LocalRetrainConflict(LocalRetrainError):
    def __init__(self, code: str, *, job_id: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.job_id = job_id


class LocalRetrainNotFound(LocalRetrainError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise LocalRetrainError("local-retrain-clock-invalid")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: object, code: str) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise LocalRetrainError(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LocalRetrainError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LocalRetrainError(code)
    return parsed.astimezone(timezone.utc)


def _uuid4(value: object, code: str) -> str:
    if not isinstance(value, str):
        raise LocalRetrainError(code)
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise LocalRetrainError(code) from exc
    canonical = str(parsed)
    if parsed.version != 4 or canonical != value.lower():
        raise LocalRetrainError(code)
    return canonical


def new_preparation_token() -> str:
    """Return an unguessable capability for one preparation worker."""

    return secrets.token_hex(32)


def _preparation_token(value: object) -> str:
    if not isinstance(value, str) or PREPARATION_TOKEN_RE.fullmatch(value) is None:
        raise LocalRetrainError("local-preparation-token-invalid")
    return value


def _validate_preparation_ttl(ttl_seconds: int) -> None:
    if (
        type(ttl_seconds) is not int
        or not MIN_PREPARATION_TTL_SECONDS
        <= ttl_seconds
        <= MAX_PREPARATION_TTL_SECONDS
    ):
        raise LocalRetrainError("local-preparation-ttl-invalid")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(
    path: Path,
    *,
    max_bytes: int | None = None,
    heartbeat: Callable[[], None] | None = None,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            if max_bytes is not None and size > max_bytes:
                raise LocalRetrainError("local-retrain-file-too-large")
            digest.update(chunk)
            if heartbeat is not None:
                heartbeat()
    return digest.hexdigest(), size


def _is_excluded(relative: Path) -> bool:
    return any(part.lower() in SOURCE_EXCLUDED_PARTS for part in relative.parts)


def _source_files(root: Path) -> tuple[Path, ...]:
    candidates: dict[str, Path] = {}
    for relative_text in SOURCE_ROOT_FILES:
        candidate = root / Path(relative_text)
        if candidate.exists():
            candidates[relative_text] = candidate
    for directory_text in SOURCE_ROOT_DIRECTORIES:
        directory = root / Path(directory_text)
        if not directory.exists():
            continue
        for current_root, directory_names, file_names in os.walk(directory, followlinks=False):
            current = Path(current_root)
            relative_current = current.relative_to(root)
            kept_directories: list[str] = []
            for name in directory_names:
                child = current / name
                relative_child = child.relative_to(root)
                if _is_excluded(relative_child):
                    continue
                if child.is_symlink():
                    raise LocalRetrainError("local-source-tree-symlink-forbidden")
                kept_directories.append(name)
            directory_names[:] = kept_directories
            for name in file_names:
                path = current / name
                relative = path.relative_to(root)
                if _is_excluded(relative) or path.suffix.lower() not in SOURCE_SUFFIXES:
                    continue
                candidates[relative.as_posix()] = path
    return tuple(candidates[key] for key in sorted(candidates))


def compute_source_tree_sha256(
    source_root: Path,
    *,
    heartbeat: Callable[[], None] | None = None,
) -> str:
    """Hash canonical execution-source paths and bytes, excluding mutable data."""

    root = Path(source_root)
    if not root.is_absolute() or root.is_symlink():
        raise LocalRetrainError("local-source-tree-root-invalid")
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as exc:
        raise LocalRetrainError("local-source-tree-root-invalid") from exc
    if not resolved_root.is_dir():
        raise LocalRetrainError("local-source-tree-root-invalid")

    files = _source_files(resolved_root)
    if not files:
        raise LocalRetrainError("local-source-tree-empty")
    digest = hashlib.sha256()
    total_size = 0
    for source in files:
        if source.is_symlink():
            raise LocalRetrainError("local-source-tree-symlink-forbidden")
        resolved = source.resolve(strict=True)
        if not resolved.is_file() or not resolved.is_relative_to(resolved_root):
            raise LocalRetrainError("local-source-tree-entry-invalid")
        relative_bytes = resolved.relative_to(resolved_root).as_posix().encode("utf-8")
        size = resolved.stat().st_size
        if size < 0 or size > MAX_SOURCE_FILE_BYTES:
            raise LocalRetrainError("local-source-tree-entry-size-invalid")
        total_size += size
        if total_size > MAX_SOURCE_TREE_BYTES:
            raise LocalRetrainError("local-source-tree-size-invalid")
        digest.update(len(relative_bytes).to_bytes(4, "big"))
        digest.update(relative_bytes)
        digest.update(size.to_bytes(8, "big"))
        with resolved.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                if heartbeat is not None:
                    heartbeat()
    return digest.hexdigest()


@dataclass(frozen=True)
class LocalSnapshot:
    path: Path
    sha256: str
    size_bytes: int


SnapshotProgress = Callable[[int, int], None]


def create_sqlite_snapshot(
    source_path: Path,
    catalog_root: Path,
    *,
    progress: SnapshotProgress | None = None,
    heartbeat: Callable[[], None] | None = None,
) -> LocalSnapshot:
    """Create a consistent, digest-named SQLite snapshot using online backup."""

    source = Path(source_path)
    root = Path(catalog_root)
    if not source.is_absolute() or source.is_symlink():
        raise LocalRetrainError("local-snapshot-source-invalid")
    if not root.is_absolute() or root.is_symlink():
        raise LocalRetrainError("local-snapshot-catalog-invalid")
    try:
        resolved_source = source.resolve(strict=True)
    except OSError as exc:
        raise LocalRetrainError("local-snapshot-source-invalid") from exc
    if (
        not resolved_source.is_file()
        or resolved_source.suffix.lower() != ".db"
        or resolved_source.stat().st_size < 1
        or resolved_source.stat().st_size > MAX_SNAPSHOT_BYTES
    ):
        raise LocalRetrainError("local-snapshot-source-invalid")

    root.mkdir(parents=True, exist_ok=True)
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as exc:
        raise LocalRetrainError("local-snapshot-catalog-invalid") from exc
    if not resolved_root.is_dir():
        raise LocalRetrainError("local-snapshot-catalog-invalid")

    temporary = resolved_root / f".snapshot.{uuid.uuid4().hex}.tmp"
    try:
        if heartbeat is not None:
            heartbeat()
        source_uri = f"{resolved_source.as_uri()}?mode=ro"
        with closing(
            sqlite3.connect(source_uri, uri=True, timeout=30)
        ) as source_connection:
            with closing(sqlite3.connect(str(temporary), timeout=30)) as destination:
                def report(status: int, remaining: int, total: int) -> None:
                    del status
                    if heartbeat is not None:
                        heartbeat()
                    if progress is not None:
                        progress(max(total - remaining, 0), max(total, 0))

                source_connection.backup(
                    destination,
                    pages=4096,
                    progress=report,
                    sleep=0.01,
                )
                destination.execute("PRAGMA journal_mode=DELETE")
                result = destination.execute("PRAGMA quick_check").fetchone()
                if result is None or result[0] != "ok":
                    raise LocalRetrainError("local-snapshot-integrity-check-failed")
                destination.commit()
        if heartbeat is not None:
            heartbeat()
        digest, size = sha256_file(
            temporary,
            max_bytes=MAX_SNAPSHOT_BYTES,
            heartbeat=heartbeat,
        )
        if size < 1:
            raise LocalRetrainError("local-snapshot-empty")
        destination_path = resolved_root / f"{digest}.db"
        if destination_path.exists():
            if destination_path.is_symlink():
                raise LocalRetrainError("local-snapshot-catalog-entry-invalid")
            existing_digest, existing_size = sha256_file(
                destination_path,
                max_bytes=MAX_SNAPSHOT_BYTES,
                heartbeat=heartbeat,
            )
            if existing_digest != digest or existing_size != size:
                raise LocalRetrainError("local-snapshot-catalog-collision")
        else:
            try:
                os.link(temporary, destination_path)
            except FileExistsError:
                existing_digest, existing_size = sha256_file(
                    destination_path,
                    max_bytes=MAX_SNAPSHOT_BYTES,
                    heartbeat=heartbeat,
                )
                if existing_digest != digest or existing_size != size:
                    raise LocalRetrainError("local-snapshot-catalog-collision")
            except OSError as exc:
                raise LocalRetrainError("local-snapshot-publish-failed") from exc
        try:
            temporary.unlink()
        except OSError as exc:
            raise LocalRetrainError("local-snapshot-temp-cleanup-failed") from exc
        try:
            destination_path.chmod(stat.S_IREAD)
        except OSError:
            pass
        return LocalSnapshot(destination_path, digest, size)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def copy_local_snapshot(source_path: Path, destination_path: Path) -> Path:
    """Bind an immutable catalog snapshot into a worker workspace by hard link."""

    source = Path(source_path)
    destination = Path(destination_path)
    if (
        not source.is_absolute()
        or source.is_symlink()
        or not destination.is_absolute()
        or destination.is_symlink()
        or destination.exists()
        or destination.suffix.lower() != ".db"
    ):
        raise LocalRetrainError("local-snapshot-copy-path-invalid")
    try:
        resolved_source = source.resolve(strict=True)
        resolved_parent = destination.parent.resolve(strict=True)
    except OSError as exc:
        raise LocalRetrainError("local-snapshot-copy-path-invalid") from exc
    if not resolved_source.is_file() or not resolved_parent.is_dir():
        raise LocalRetrainError("local-snapshot-copy-path-invalid")
    resolved_destination = resolved_parent / destination.name
    try:
        os.link(resolved_source, resolved_destination)
    except OSError as exc:
        raise LocalRetrainError("local-snapshot-copy-hardlink-failed") from exc
    try:
        resolved_destination.chmod(stat.S_IREAD)
    except OSError:
        pass
    return resolved_destination


def reconcile_snapshot_catalog(catalog_root: Path) -> int:
    """Remove only interrupted online-backup temp files after process restart."""

    root = Path(catalog_root)
    if not root.is_absolute() or root.is_symlink():
        raise LocalRetrainError("local-snapshot-catalog-invalid")
    root.mkdir(parents=True, exist_ok=True)
    resolved_root = root.resolve(strict=True)
    removed = 0
    for entry in sorted(resolved_root.iterdir(), key=lambda item: item.name):
        if entry.is_symlink() or not entry.is_file():
            raise LocalRetrainError("local-snapshot-catalog-entry-invalid")
        if re.fullmatch(r"[0-9a-f]{64}\.db", entry.name):
            continue
        if re.fullmatch(r"\.snapshot\.[0-9a-f]{32}\.tmp", entry.name) is None:
            raise LocalRetrainError("local-snapshot-catalog-entry-invalid")
        try:
            entry.chmod(stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass
        entry.unlink()
        removed += 1
    return removed


def _validate_month(value: object, code: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or DATE_MONTH_RE.fullmatch(value) is None:
        raise LocalRetrainError(code)
    try:
        datetime.strptime(value, "%Y-%m")
    except ValueError as exc:
        raise LocalRetrainError(code) from exc
    return value


def _validate_features(
    values: object,
    *,
    label: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise LocalRetrainError(f"local-{label}-invalid")
    items = tuple(values)
    if (not allow_empty and not items) or len(items) > 2048:
        raise LocalRetrainError(f"local-{label}-invalid")
    if any(not isinstance(item, str) or IDENTIFIER_RE.fullmatch(item) is None for item in items):
        raise LocalRetrainError(f"local-{label}-invalid")
    if len(set(items)) != len(items):
        raise LocalRetrainError(f"local-{label}-duplicate")
    return items


def validate_local_training_parameters(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != LOCAL_TRAINING_PARAMETER_KEYS:
        raise LocalRetrainError("local-training-parameters-schema-invalid")
    force_sync = value.get("force_sync")
    test_size = value.get("test_size")
    cv_folds = value.get("cv_folds")
    use_optuna = value.get("use_optuna")
    optuna_trials = value.get("optuna_trials")
    optuna_timeout = value.get("optuna_timeout")
    if force_sync is not False:
        raise LocalRetrainError("local-training-force-sync-forbidden")
    if (
        isinstance(test_size, bool)
        or not isinstance(test_size, (int, float))
        or not 0.1 <= float(test_size) <= 0.5
        or type(cv_folds) is not int
        or not 2 <= cv_folds <= 10
        or type(use_optuna) is not bool
        or type(optuna_trials) is not int
        or not 1 <= optuna_trials <= 1000
        or type(optuna_timeout) is not int
        or not 30 <= optuna_timeout <= 3600
    ):
        raise LocalRetrainError("local-training-parameters-invalid")
    for key in ("use_sqlite", "ultimate_mode", "use_optimizer"):
        if value.get(key) is not True:
            raise LocalRetrainError("local-training-mode-invalid")
    date_from = _validate_month(value.get("training_date_from"), "local-training-period-invalid")
    date_to = _validate_month(value.get("training_date_to"), "local-training-period-invalid")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise LocalRetrainError("local-training-period-invalid")
    return {
        "force_sync": False,
        "test_size": float(test_size),
        "cv_folds": cv_folds,
        "use_sqlite": True,
        "ultimate_mode": True,
        "use_optimizer": True,
        "use_optuna": use_optuna,
        "optuna_trials": optuna_trials,
        "optuna_timeout": optuna_timeout,
        "training_date_from": date_from,
        "training_date_to": date_to,
    }


def _feature_contract_sha256(
    *,
    target: str,
    model_type: str,
    selected_features: Iterable[str],
    removed_features: Iterable[str],
) -> str:
    value = {
        "model_type": model_type,
        "removed_features": sorted(removed_features),
        # LightGBM consumes columns positionally.  Preserve their order in the
        # dedicated feature hash instead of treating the schema as a set.
        "selected_features": list(selected_features),
        "target": target,
    }
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


@dataclass(frozen=True)
class LocalTrainingContract:
    job_id: str
    authorization_id: str
    contract_id: str
    created_at: datetime
    owner_id: str
    target: str
    model_type: str
    selected_features: tuple[str, ...]
    removed_features: tuple[str, ...]
    data_snapshot_sha256: str
    feature_contract_sha256: str
    candidate_commit_sha: str
    source_tree_sha256: str
    active_model_id: str
    active_model_sha256: str
    training_parameters: Mapping[str, object]

    @classmethod
    def create(
        cls,
        *,
        job_id: str,
        owner_id: str,
        snapshot: LocalSnapshot,
        target: str,
        model_type: str,
        selected_features: Iterable[str],
        removed_features: Iterable[str],
        candidate_commit_sha: str,
        source_tree_sha256: str,
        active_model_id: str,
        active_model_sha256: str,
        training_parameters: Mapping[str, object],
        future_fields: Collection[str],
        authorization_id: str | None = None,
        contract_id: str | None = None,
        created_at: datetime | None = None,
    ) -> "LocalTrainingContract":
        canonical_job_id = _uuid4(job_id, "local-contract-job-id-invalid")
        canonical_owner = _uuid4(owner_id, "local-contract-owner-id-invalid")
        canonical_authorization = _uuid4(
            authorization_id or str(uuid.uuid4()),
            "local-contract-authorization-id-invalid",
        )
        canonical_contract = _uuid4(
            contract_id or str(uuid.uuid4()),
            "local-contract-id-invalid",
        )
        if target not in LOCAL_TARGETS:
            raise LocalRetrainError("local-contract-target-invalid")
        if model_type != "lightgbm":
            raise LocalRetrainError("local-contract-model-type-invalid")
        selected = _validate_features(
            tuple(selected_features),
            label="selected-features",
            allow_empty=False,
        )
        removed = _validate_features(
            tuple(removed_features),
            label="removed-features",
            allow_empty=True,
        )
        if any(item not in selected for item in removed) or len(removed) == len(selected):
            raise LocalRetrainError("local-removed-features-binding-invalid")
        if set(selected).intersection(future_fields):
            raise LocalRetrainError("local-selected-features-contain-future-field")
        if (
            DIGEST_RE.fullmatch(snapshot.sha256 or "") is None
            or snapshot.sha256 == "0" * 64
            or snapshot.size_bytes < 1
            or snapshot.size_bytes > MAX_SNAPSHOT_BYTES
        ):
            raise LocalRetrainError("local-contract-snapshot-invalid")
        snapshot_digest, snapshot_size = sha256_file(
            snapshot.path,
            max_bytes=MAX_SNAPSHOT_BYTES,
        )
        if snapshot_digest != snapshot.sha256 or snapshot_size != snapshot.size_bytes:
            raise LocalRetrainError("local-contract-snapshot-mismatch")
        if (
            COMMIT_RE.fullmatch(candidate_commit_sha or "") is None
            or candidate_commit_sha == "0" * 40
        ):
            raise LocalRetrainError("local-contract-commit-invalid")
        if (
            DIGEST_RE.fullmatch(source_tree_sha256 or "") is None
            or source_tree_sha256 == "0" * 64
        ):
            raise LocalRetrainError("local-contract-source-tree-invalid")
        if IDENTIFIER_RE.fullmatch(active_model_id or "") is None:
            raise LocalRetrainError("local-contract-active-model-invalid")
        if (
            DIGEST_RE.fullmatch(active_model_sha256 or "") is None
            or active_model_sha256 == "0" * 64
        ):
            raise LocalRetrainError("local-contract-active-model-hash-invalid")
        parameters = validate_local_training_parameters(training_parameters)
        observed_at = created_at or _utc_now()
        _iso(observed_at)
        return cls(
            job_id=canonical_job_id,
            authorization_id=canonical_authorization,
            contract_id=canonical_contract,
            created_at=observed_at.astimezone(timezone.utc),
            owner_id=canonical_owner,
            target=target,
            model_type=model_type,
            selected_features=selected,
            removed_features=removed,
            data_snapshot_sha256=snapshot.sha256,
            feature_contract_sha256=_feature_contract_sha256(
                target=target,
                model_type=model_type,
                selected_features=selected,
                removed_features=removed,
            ),
            candidate_commit_sha=candidate_commit_sha,
            source_tree_sha256=source_tree_sha256,
            active_model_id=active_model_id,
            active_model_sha256=active_model_sha256,
            training_parameters=parameters,
        )

    @classmethod
    def from_mapping(cls, value: object) -> "LocalTrainingContract":
        if not isinstance(value, Mapping) or set(value) != LOCAL_CONTRACT_KEYS:
            raise LocalRetrainError("local-contract-schema-invalid")
        if value.get("schema_version") != 1 or value.get("execution_policy") != LOCAL_EXECUTION_POLICY:
            raise LocalRetrainError("local-contract-version-invalid")
        target = value.get("target")
        model_type = value.get("model_type")
        if target not in LOCAL_TARGETS or model_type != "lightgbm":
            raise LocalRetrainError("local-contract-training-shape-invalid")
        selected = _validate_features(
            value.get("selected_features"),
            label="selected-features",
            allow_empty=False,
        )
        removed = _validate_features(
            value.get("removed_features"),
            label="removed-features",
            allow_empty=True,
        )
        if any(item not in selected for item in removed) or len(removed) == len(selected):
            raise LocalRetrainError("local-removed-features-binding-invalid")
        data_digest = value.get("data_snapshot_sha256")
        feature_digest = value.get("feature_contract_sha256")
        source_digest = value.get("source_tree_sha256")
        if any(
            not isinstance(item, str)
            or DIGEST_RE.fullmatch(item) is None
            or item == "0" * 64
            for item in (data_digest, feature_digest, source_digest)
        ):
            raise LocalRetrainError("local-contract-digest-invalid")
        expected_feature_digest = _feature_contract_sha256(
            target=str(target),
            model_type=str(model_type),
            selected_features=selected,
            removed_features=removed,
        )
        if feature_digest != expected_feature_digest:
            raise LocalRetrainError("local-contract-feature-hash-mismatch")
        commit = value.get("candidate_commit_sha")
        active_model_id = value.get("active_model_id")
        active_model_sha256 = value.get("active_model_sha256")
        if (
            not isinstance(commit, str)
            or COMMIT_RE.fullmatch(commit) is None
            or commit == "0" * 40
            or not isinstance(active_model_id, str)
            or IDENTIFIER_RE.fullmatch(active_model_id) is None
            or not isinstance(active_model_sha256, str)
            or DIGEST_RE.fullmatch(active_model_sha256) is None
            or active_model_sha256 == "0" * 64
        ):
            raise LocalRetrainError("local-contract-runtime-binding-invalid")
        return cls(
            job_id=_uuid4(value.get("job_id"), "local-contract-job-id-invalid"),
            authorization_id=_uuid4(
                value.get("authorization_id"),
                "local-contract-authorization-id-invalid",
            ),
            contract_id=_uuid4(value.get("contract_id"), "local-contract-id-invalid"),
            created_at=_parse_time(value.get("created_at"), "local-contract-time-invalid"),
            owner_id=_uuid4(value.get("owner_id"), "local-contract-owner-id-invalid"),
            target=str(target),
            model_type=str(model_type),
            selected_features=selected,
            removed_features=removed,
            data_snapshot_sha256=str(data_digest),
            feature_contract_sha256=str(feature_digest),
            candidate_commit_sha=commit,
            source_tree_sha256=str(source_digest),
            active_model_id=active_model_id,
            active_model_sha256=active_model_sha256,
            training_parameters=validate_local_training_parameters(
                value.get("training_parameters")
            ),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "job_id": self.job_id,
            "authorization_id": self.authorization_id,
            "contract_id": self.contract_id,
            "created_at": _iso(self.created_at),
            "execution_policy": LOCAL_EXECUTION_POLICY,
            "owner_id": self.owner_id,
            "target": self.target,
            "model_type": self.model_type,
            "selected_features": list(self.selected_features),
            "removed_features": list(self.removed_features),
            "data_snapshot_sha256": self.data_snapshot_sha256,
            "feature_contract_sha256": self.feature_contract_sha256,
            "candidate_commit_sha": self.candidate_commit_sha,
            "source_tree_sha256": self.source_tree_sha256,
            "active_model_id": self.active_model_id,
            "active_model_sha256": self.active_model_sha256,
            "training_parameters": dict(self.training_parameters),
        }

    @property
    def sha256(self) -> str:
        return _sha256_bytes(_canonical_json(self.to_mapping()).encode("utf-8"))


@dataclass(frozen=True)
class LocalTrainingExecution:
    contract: LocalTrainingContract
    workspace: Path
    snapshot_path: Path

    execution_policy = LOCAL_EXECUTION_POLICY
    uses_explicit_oot_split = False
    allows_calibration = True
    allows_remote_side_effects = False

    @classmethod
    def create(
        cls,
        *,
        contract: LocalTrainingContract,
        workspace: Path,
        snapshot_path: Path,
    ) -> "LocalTrainingExecution":
        temp_root = Path(tempfile.gettempdir()).resolve()
        if workspace.is_symlink() or snapshot_path.is_symlink():
            raise LocalRetrainError("local-execution-symlink-forbidden")
        try:
            resolved_workspace = workspace.resolve(strict=True)
            resolved_snapshot = snapshot_path.resolve(strict=True)
        except OSError as exc:
            raise LocalRetrainError("local-execution-path-unavailable") from exc
        if (
            not resolved_workspace.is_dir()
            or resolved_workspace == temp_root
            or not resolved_workspace.is_relative_to(temp_root)
            or not resolved_snapshot.is_file()
            or not resolved_snapshot.is_relative_to(resolved_workspace)
            or resolved_snapshot.suffix.lower() != ".db"
        ):
            raise LocalRetrainError("local-execution-path-invalid")
        execution = cls(contract, resolved_workspace, resolved_snapshot)
        execution.verify_snapshot()
        return execution

    @property
    def job_id(self) -> str:
        return self.contract.job_id

    @property
    def approved_payload_hash(self) -> str:
        # `_do_train` historically names this field after the approved RPC
        # payload.  For the separate local policy it is the immutable local
        # contract hash, never an approved-environment authorization hash.
        return self.contract.sha256

    @property
    def data_snapshot_sha256(self) -> str:
        return self.contract.data_snapshot_sha256

    @property
    def feature_contract_sha256(self) -> str:
        return self.contract.feature_contract_sha256

    @property
    def candidate_commit_sha(self) -> str:
        return self.contract.candidate_commit_sha

    @property
    def source_tree_sha256(self) -> str:
        return self.contract.source_tree_sha256

    @property
    def active_model_id(self) -> str:
        return self.contract.active_model_id

    @property
    def active_model_sha256(self) -> str:
        return self.contract.active_model_sha256

    @property
    def train_period_start(self) -> str | None:
        return self.contract.training_parameters.get("training_date_from")  # type: ignore[return-value]

    @property
    def train_period_end(self) -> str | None:
        return self.contract.training_parameters.get("training_date_to")  # type: ignore[return-value]

    @property
    def validation_period_start(self) -> None:
        return None

    @property
    def validation_period_end(self) -> None:
        return None

    @property
    def artifact_directory(self) -> Path:
        return self.workspace / "artifacts"

    @property
    def effective_features(self) -> tuple[str, ...]:
        removed = set(self.contract.removed_features)
        return tuple(item for item in self.contract.selected_features if item not in removed)

    def verify_snapshot(self) -> None:
        if self.snapshot_path.is_symlink() or not self.snapshot_path.is_file():
            raise LocalRetrainError("local-execution-snapshot-invalid")
        digest, size = sha256_file(self.snapshot_path, max_bytes=MAX_SNAPSHOT_BYTES)
        if size < 1 or digest != self.contract.data_snapshot_sha256:
            raise LocalRetrainError("local-execution-snapshot-hash-mismatch")

    def prepare_artifact_directory(self) -> Path:
        path = self.artifact_directory
        if path.is_symlink():
            raise LocalRetrainError("local-execution-artifact-directory-invalid")
        path.mkdir(parents=False, exist_ok=True)
        resolved = path.resolve(strict=True)
        if not resolved.is_dir() or not resolved.is_relative_to(self.workspace):
            raise LocalRetrainError("local-execution-artifact-directory-invalid")
        return resolved

    def validate_request(self, **values: object) -> None:
        expected = {
            "target": self.contract.target,
            "model_type": self.contract.model_type,
            **dict(self.contract.training_parameters),
        }
        for key, actual in values.items():
            if key not in expected or actual != expected[key]:
                raise LocalRetrainError("local-training-request-binding-mismatch")

    def select_feature_columns(
        self,
        produced_columns: Collection[str],
        *,
        future_fields: Collection[str],
    ) -> tuple[str, ...]:
        effective = self.effective_features
        produced = _validate_features(
            tuple(produced_columns),
            label="produced-features",
            allow_empty=False,
        )
        if set(effective).intersection(future_fields) or set(produced).intersection(
            future_fields
        ):
            raise LocalRetrainError("local-features-contain-future-field")
        if produced != effective:
            raise LocalRetrainError("local-feature-schema-mismatch")
        return effective


@dataclass(frozen=True)
class LocalExecutionBundle:
    contract: LocalTrainingContract
    contract_sha256: str
    worker_id: str
    fencing_token: int
    record_version: int
    lease_expires_at: datetime

    @property
    def execution_policy(self) -> str:
        return LOCAL_EXECUTION_POLICY

    @property
    def target(self) -> str:
        return self.contract.target

    @property
    def model_type(self) -> str:
        return self.contract.model_type

    @classmethod
    def from_gateway(
        cls,
        value: Mapping[str, Any],
        *,
        expected_job_id: str,
        expected_worker_id: str,
        expected_fencing_token: int,
        expected_candidate_commit_sha: str,
        expected_active_model_id: str,
        now: datetime,
        expected_source_tree_sha256: str | None = None,
        expected_active_model_sha256: str | None = None,
    ) -> "LocalExecutionBundle":
        if not isinstance(value, Mapping) or set(value) != LOCAL_BUNDLE_KEYS:
            raise LocalRetrainError("local-execution-bundle-schema-invalid")
        if value.get("schema_version") != 1 or value.get("execution_policy") != LOCAL_EXECUTION_POLICY:
            raise LocalRetrainError("local-execution-bundle-policy-invalid")
        contract = LocalTrainingContract.from_mapping(value.get("contract"))
        if (
            contract.job_id != _uuid4(expected_job_id, "local-execution-job-id-invalid")
            or contract.candidate_commit_sha != expected_candidate_commit_sha
            or contract.active_model_id != expected_active_model_id
            or (
                expected_source_tree_sha256 is not None
                and contract.source_tree_sha256 != expected_source_tree_sha256
            )
            or (
                expected_active_model_sha256 is not None
                and contract.active_model_sha256 != expected_active_model_sha256
            )
        ):
            raise LocalRetrainError("local-execution-bundle-runtime-binding-mismatch")
        contract_sha = value.get("contract_sha256")
        worker_id = value.get("worker_id")
        fencing_token = value.get("fencing_token")
        record_version = value.get("record_version")
        if (
            not isinstance(contract_sha, str)
            or contract_sha != contract.sha256
            or not isinstance(worker_id, str)
            or WORKER_RE.fullmatch(worker_id) is None
            or worker_id != expected_worker_id
            or type(fencing_token) is not int
            or fencing_token != expected_fencing_token
            or fencing_token < 1
            or type(record_version) is not int
            or record_version < 1
        ):
            raise LocalRetrainError("local-execution-bundle-binding-invalid")
        if now.tzinfo is None or now.utcoffset() is None:
            raise LocalRetrainError("local-execution-clock-invalid")
        observed = now.astimezone(timezone.utc)
        expires = _parse_time(value.get("lease_expires_at"), "local-execution-lease-invalid")
        if expires <= observed or expires > observed + timedelta(seconds=305):
            raise LocalRetrainError("local-execution-lease-invalid")
        return cls(contract, contract_sha, worker_id, fencing_token, record_version, expires)

    def create_execution(self, *, workspace: Path, snapshot_path: Path) -> LocalTrainingExecution:
        return LocalTrainingExecution.create(
            contract=self.contract,
            workspace=workspace,
            snapshot_path=snapshot_path,
        )

    def training_request_payload(self) -> dict[str, object]:
        return {
            "target": self.contract.target,
            "model_type": self.contract.model_type,
            **dict(self.contract.training_parameters),
        }


@dataclass(frozen=True)
class LocalJobRecord:
    job_id: str
    owner_id: str
    state: str
    progress: str
    pct: int
    record_version: int
    preparation_expires_at: datetime | None
    worker_id: str | None
    fencing_token: int
    lease_expires_at: datetime | None
    request_payload: Mapping[str, object]
    contract: LocalTrainingContract | None
    contract_sha256: str | None
    snapshot_path: Path | None
    snapshot_sha256: str | None
    artifact_object_name: str | None
    artifact_path: Path | None
    artifact_sha256: str | None
    artifact_size_bytes: int | None
    artifact_media_type: str | None
    failure_code: str | None
    result: Mapping[str, object] | None

    def to_status(self) -> dict[str, object]:
        status = (
            "completed"
            if self.state == "completed" and self.result is not None
            else "error"
            if self.state == "failed"
            else "running"
            if self.state in {"claimed", "running", "artifact-registered", "completed"}
            else "queued"
        )
        return {
            "job_id": self.job_id,
            "status": status,
            "progress": self.progress,
            "pct": self.pct,
            "result": dict(self.result) if self.result is not None else None,
            "error": self.failure_code,
            "state": self.state,
            "record_version": self.record_version,
            "snapshot_sha256": self.snapshot_sha256,
            "artifact_sha256": self.artifact_sha256,
        }


@dataclass(frozen=True)
class LocalPublicationRecord:
    job_id: str
    artifact_sha256: str
    published_path: Path
    result: Mapping[str, object]
    state: str


@dataclass(frozen=True)
class LocalReconcileReport:
    redispatch_job_ids: tuple[str, ...]
    failed_job_ids: tuple[str, ...]
    completed_publications: int
    restored_publications: int
    removed_orphans: int
    retained_artifacts: int


class LocalRetrainStore:
    """Durable local CAS/lease/fence ledger for Admin-triggered retraining."""

    def __init__(
        self,
        db_path: Path,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.db_path = Path(db_path)
        self._clock = clock
        self._lock = threading.RLock()
        self.init_schema()

    def now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise LocalRetrainError("local-retrain-clock-invalid")
        return value.astimezone(timezone.utc)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def init_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS local_retrain_jobs (
                    job_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN (
                        'preparing','queued','claimed','running','artifact-registered',
                        'completed','failed'
                    )),
                    request_json TEXT NOT NULL,
                    contract_json TEXT,
                    contract_sha256 TEXT,
                    snapshot_path TEXT,
                    snapshot_sha256 TEXT,
                    preparation_token TEXT,
                    preparation_expires_at TEXT,
                    worker_id TEXT,
                    fencing_token INTEGER NOT NULL DEFAULT 0 CHECK(fencing_token >= 0),
                    lease_expires_at TEXT,
                    artifact_object_name TEXT,
                    artifact_path TEXT,
                    artifact_sha256 TEXT,
                    artifact_size_bytes INTEGER,
                    artifact_media_type TEXT,
                    failure_code TEXT,
                    record_version INTEGER NOT NULL DEFAULT 1 CHECK(record_version >= 1),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS uq_local_retrain_single_active
                ON local_retrain_jobs((1))
                WHERE state IN ('preparing','queued','claimed','running','artifact-registered');

                CREATE TABLE IF NOT EXISTS local_retrain_progress (
                    job_id TEXT PRIMARY KEY REFERENCES local_retrain_jobs(job_id),
                    progress TEXT NOT NULL DEFAULT '',
                    pct INTEGER NOT NULL DEFAULT 0 CHECK(pct BETWEEN 0 AND 100),
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS local_retrain_fencing_sequence (
                    fencing_token INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES local_retrain_jobs(job_id),
                    issued_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS local_retrain_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES local_retrain_jobs(job_id),
                    event_type TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    record_version INTEGER NOT NULL,
                    fencing_token INTEGER,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS local_retrain_results (
                    job_id TEXT PRIMARY KEY REFERENCES local_retrain_jobs(job_id),
                    artifact_sha256 TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS local_retrain_publications (
                    job_id TEXT PRIMARY KEY REFERENCES local_retrain_jobs(job_id),
                    artifact_sha256 TEXT NOT NULL,
                    published_path TEXT NOT NULL UNIQUE,
                    result_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('prepared','published')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS local_retrain_staged_outputs (
                    job_id TEXT PRIMARY KEY REFERENCES local_retrain_jobs(job_id),
                    fencing_token INTEGER NOT NULL,
                    artifact_object_name TEXT NOT NULL,
                    artifact_sha256 TEXT NOT NULL,
                    artifact_size_bytes INTEGER NOT NULL,
                    artifact_media_type TEXT NOT NULL,
                    published_path TEXT NOT NULL UNIQUE,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TRIGGER IF NOT EXISTS trg_local_retrain_jobs_no_delete
                BEFORE DELETE ON local_retrain_jobs
                BEGIN SELECT RAISE(ABORT, 'local retrain jobs cannot be deleted'); END;
                CREATE TRIGGER IF NOT EXISTS trg_local_retrain_events_immutable_update
                BEFORE UPDATE ON local_retrain_events
                BEGIN SELECT RAISE(ABORT, 'local retrain events are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS trg_local_retrain_events_immutable_delete
                BEFORE DELETE ON local_retrain_events
                BEGIN SELECT RAISE(ABORT, 'local retrain events are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS trg_local_retrain_results_immutable_update
                BEFORE UPDATE ON local_retrain_results
                BEGIN SELECT RAISE(ABORT, 'local retrain results are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS trg_local_retrain_results_immutable_delete
                BEFORE DELETE ON local_retrain_results
                BEGIN SELECT RAISE(ABORT, 'local retrain results are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS trg_local_retrain_publications_no_delete
                BEFORE DELETE ON local_retrain_publications
                BEGIN SELECT RAISE(ABORT, 'local retrain publications cannot be deleted'); END;
                CREATE TRIGGER IF NOT EXISTS trg_local_retrain_publications_binding_immutable
                BEFORE UPDATE ON local_retrain_publications
                WHEN NEW.job_id != OLD.job_id
                  OR NEW.artifact_sha256 != OLD.artifact_sha256
                  OR NEW.published_path != OLD.published_path
                  OR NEW.result_json != OLD.result_json
                  OR NEW.created_at != OLD.created_at
                BEGIN SELECT RAISE(ABORT, 'local retrain publication binding is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS trg_local_retrain_staged_outputs_immutable_update
                BEFORE UPDATE ON local_retrain_staged_outputs
                BEGIN SELECT RAISE(ABORT, 'local retrain staged outputs are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS trg_local_retrain_staged_outputs_immutable_delete
                BEFORE DELETE ON local_retrain_staged_outputs
                BEGIN SELECT RAISE(ABORT, 'local retrain staged outputs are immutable'); END;
                """
            )
            # Additive migration for ledgers created before preparation had its
            # own expiring capability. Existing preparing rows are recovered by
            # startup reconciliation because their deadline is NULL.
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(local_retrain_jobs)")
            }
            if "preparation_token" not in columns:
                connection.execute(
                    "ALTER TABLE local_retrain_jobs ADD COLUMN preparation_token TEXT"
                )
            if "preparation_expires_at" not in columns:
                connection.execute(
                    "ALTER TABLE local_retrain_jobs ADD COLUMN preparation_expires_at TEXT"
                )

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        *,
        job_id: str,
        event_type: str,
        from_state: str | None,
        to_state: str,
        version: int,
        fence: int | None,
        observed_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO local_retrain_events (
                job_id,event_type,from_state,to_state,record_version,fencing_token,created_at
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (job_id, event_type, from_state, to_state, version, fence, observed_at),
        )

    @classmethod
    def _recover_expired_preparations_locked(
        cls,
        connection: sqlite3.Connection,
        *,
        now: datetime,
    ) -> list[str]:
        """Fence expired preparation owners inside the caller's write txn."""

        now_text = _iso(now)
        recovered: list[str] = []
        rows = connection.execute(
            """
            SELECT job_id,record_version,preparation_token,preparation_expires_at
            FROM local_retrain_jobs
            WHERE state='preparing'
            ORDER BY created_at,job_id
            """
        ).fetchall()
        for row in rows:
            raw_deadline = row["preparation_expires_at"]
            if raw_deadline is not None:
                try:
                    if _parse_time(
                        raw_deadline,
                        "local-preparation-lease-invalid",
                    ) > now:
                        continue
                except LocalRetrainError:
                    # Invalid/migrated preparation metadata cannot own the
                    # process-wide active slot indefinitely.
                    pass
            version = int(row["record_version"]) + 1
            changed = connection.execute(
                """
                UPDATE local_retrain_jobs
                SET state='failed',preparation_token=NULL,preparation_expires_at=NULL,
                    failure_code='preparation-lease-expired',record_version=?,
                    updated_at=?,finished_at=?
                WHERE job_id=? AND state='preparing' AND record_version=?
                  AND (
                    preparation_token=?
                    OR (preparation_token IS NULL AND ? IS NULL)
                  )
                  AND (
                    preparation_expires_at=?
                    OR (preparation_expires_at IS NULL AND ? IS NULL)
                  )
                """,
                (
                    version,
                    now_text,
                    now_text,
                    row["job_id"],
                    row["record_version"],
                    row["preparation_token"],
                    row["preparation_token"],
                    row["preparation_expires_at"],
                    row["preparation_expires_at"],
                ),
            ).rowcount
            if changed != 1:
                raise LocalRetrainConflict("local-preparation-recovery-conflict")
            connection.execute(
                """
                UPDATE local_retrain_progress
                SET progress='中断',pct=100,updated_at=? WHERE job_id=?
                """,
                (now_text, row["job_id"]),
            )
            cls._event(
                connection,
                job_id=str(row["job_id"]),
                event_type="preparation-lease-expired",
                from_state="preparing",
                to_state="failed",
                version=version,
                fence=None,
                observed_at=now_text,
            )
            recovered.append(str(row["job_id"]))
        return recovered

    def create_job(
        self,
        *,
        job_id: str,
        owner_id: str,
        request_payload: Mapping[str, object],
        preparation_token: str,
        preparation_ttl_seconds: int,
    ) -> LocalJobRecord:
        canonical_job = _uuid4(job_id, "local-job-id-invalid")
        canonical_owner = _uuid4(owner_id, "local-owner-id-invalid")
        canonical_preparation_token = _preparation_token(preparation_token)
        _validate_preparation_ttl(preparation_ttl_seconds)
        encoded_request = _canonical_json(dict(request_payload))
        if not encoded_request or len(encoded_request.encode("utf-8")) > MAX_REQUEST_BYTES:
            raise LocalRetrainError("local-request-size-invalid")
        now = self.now()
        now_text = _iso(now)
        preparation_expires_at = _iso(
            now + timedelta(seconds=preparation_ttl_seconds)
        )
        with self._lock, self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                # A new start request is also a recovery trigger. Keep expiry
                # and active-slot acquisition in one SQLite write transaction.
                self._recover_expired_preparations_locked(
                    connection,
                    now=now,
                )
                active = connection.execute(
                    """
                    SELECT job_id,owner_id FROM local_retrain_jobs
                    WHERE state IN (
                        'preparing','queued','claimed','running','artifact-registered'
                    ) LIMIT 1
                    """
                ).fetchone()
                if active is not None:
                    raise LocalRetrainConflict(
                        "local-train-job-active",
                        job_id=str(active["job_id"])
                        if str(active["owner_id"]) == canonical_owner
                        else None,
                    )
                connection.execute(
                    """
                    INSERT INTO local_retrain_jobs (
                        job_id,owner_id,state,request_json,preparation_token,
                        preparation_expires_at,created_at,updated_at
                    ) VALUES (?,?,'preparing',?,?,?,?,?)
                    """,
                    (
                        canonical_job,
                        canonical_owner,
                        encoded_request,
                        canonical_preparation_token,
                        preparation_expires_at,
                        now_text,
                        now_text,
                    ),
                )
                connection.execute(
                    "INSERT INTO local_retrain_progress(job_id,progress,pct,updated_at) VALUES (?,?,?,?)",
                    (canonical_job, "準備中", 0, now_text),
                )
                self._event(
                    connection,
                    job_id=canonical_job,
                    event_type="created",
                    from_state=None,
                    to_state="preparing",
                    version=1,
                    fence=None,
                    observed_at=now_text,
                )
                connection.commit()
            except LocalRetrainConflict:
                connection.rollback()
                raise
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise LocalRetrainConflict("local-train-job-conflict") from exc
        return self.get_job(canonical_job)

    def queue_job(
        self,
        *,
        job_id: str,
        expected_version: int,
        preparation_token: str,
        snapshot: LocalSnapshot,
        contract: LocalTrainingContract,
    ) -> LocalJobRecord:
        canonical_job = _uuid4(job_id, "local-job-id-invalid")
        canonical_preparation_token = _preparation_token(preparation_token)
        if contract.job_id != canonical_job or contract.data_snapshot_sha256 != snapshot.sha256:
            raise LocalRetrainError("local-job-contract-binding-mismatch")
        contract_mapping = contract.to_mapping()
        encoded_contract = _canonical_json(contract_mapping)
        now = self.now()
        now_text = _iso(now)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT owner_id,state,record_version,preparation_token,
                       preparation_expires_at
                FROM local_retrain_jobs WHERE job_id=?
                """,
                (canonical_job,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise LocalRetrainNotFound("local-job-not-found")
            if (
                row["state"] != "preparing"
                or row["record_version"] != expected_version
                or row["owner_id"] != contract.owner_id
                or row["preparation_token"] != canonical_preparation_token
                or not row["preparation_expires_at"]
                or _parse_time(
                    row["preparation_expires_at"],
                    "local-preparation-lease-invalid",
                )
                <= now
            ):
                connection.rollback()
                raise LocalRetrainConflict("local-job-queue-conflict")
            version = expected_version + 1
            changed = connection.execute(
                """
                UPDATE local_retrain_jobs
                SET state='queued',contract_json=?,contract_sha256=?,snapshot_path=?,
                    snapshot_sha256=?,preparation_token=NULL,
                    preparation_expires_at=NULL,record_version=?,updated_at=?
                WHERE job_id=? AND state='preparing' AND record_version=?
                  AND preparation_token=?
                """,
                (
                    encoded_contract,
                    contract.sha256,
                    str(snapshot.path),
                    snapshot.sha256,
                    version,
                    now_text,
                    canonical_job,
                    expected_version,
                    canonical_preparation_token,
                ),
            ).rowcount
            if changed != 1:
                connection.rollback()
                raise LocalRetrainConflict("local-job-queue-conflict")
            self._event(
                connection,
                job_id=canonical_job,
                event_type="queued",
                from_state="preparing",
                to_state="queued",
                version=version,
                fence=None,
                observed_at=now_text,
            )
            connection.commit()
        self.update_progress(canonical_job, "待機中", 5)
        return self.get_job(canonical_job)

    def heartbeat_preparation(
        self,
        *,
        job_id: str,
        expected_version: int,
        preparation_token: str,
        ttl_seconds: int,
    ) -> LocalJobRecord:
        """Renew an active preparation without allowing an expired owner back in."""

        canonical_job = _uuid4(job_id, "local-job-id-invalid")
        canonical_preparation_token = _preparation_token(preparation_token)
        _validate_preparation_ttl(ttl_seconds)
        now = self.now()
        now_text = _iso(now)
        expires_at = _iso(now + timedelta(seconds=ttl_seconds))
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT state,record_version,preparation_token,preparation_expires_at
                FROM local_retrain_jobs WHERE job_id=?
                """,
                (canonical_job,),
            ).fetchone()
            if (
                row is None
                or row["state"] != "preparing"
                or int(row["record_version"]) != expected_version
                or row["preparation_token"] != canonical_preparation_token
                or not row["preparation_expires_at"]
                or _parse_time(
                    row["preparation_expires_at"],
                    "local-preparation-lease-invalid",
                )
                <= now
            ):
                connection.rollback()
                raise LocalRetrainConflict("local-preparation-lease-lost")
            changed = connection.execute(
                """
                UPDATE local_retrain_jobs
                SET preparation_expires_at=?,updated_at=?
                WHERE job_id=? AND state='preparing' AND record_version=?
                  AND preparation_token=?
                """,
                (
                    expires_at,
                    now_text,
                    canonical_job,
                    expected_version,
                    canonical_preparation_token,
                ),
            ).rowcount
            if changed != 1:
                connection.rollback()
                raise LocalRetrainConflict("local-preparation-lease-lost")
            self._event(
                connection,
                job_id=canonical_job,
                event_type="preparation-heartbeat",
                from_state="preparing",
                to_state="preparing",
                version=expected_version,
                fence=None,
                observed_at=now_text,
            )
            connection.commit()
        return self.get_job(canonical_job)

    def fail_preparation(
        self,
        *,
        job_id: str,
        expected_version: int,
        preparation_token: str,
        failure_code: str,
    ) -> LocalJobRecord:
        canonical_job = _uuid4(job_id, "local-job-id-invalid")
        canonical_preparation_token = _preparation_token(preparation_token)
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", failure_code or ""):
            raise LocalRetrainError("local-failure-code-invalid")
        now_text = _iso(self.now())
        version = expected_version + 1
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """
                UPDATE local_retrain_jobs
                SET state='failed',preparation_token=NULL,preparation_expires_at=NULL,
                    failure_code=?,record_version=?,updated_at=?,finished_at=?
                WHERE job_id=? AND state='preparing' AND record_version=?
                  AND preparation_token=?
                """,
                (
                    failure_code,
                    version,
                    now_text,
                    now_text,
                    canonical_job,
                    expected_version,
                    canonical_preparation_token,
                ),
            ).rowcount
            if changed != 1:
                connection.rollback()
                raise LocalRetrainConflict("local-preparation-lease-lost")
            self._event(
                connection,
                job_id=canonical_job,
                event_type="preparation-failed",
                from_state="preparing",
                to_state="failed",
                version=version,
                fence=None,
                observed_at=now_text,
            )
            connection.execute(
                """
                UPDATE local_retrain_progress
                SET progress='失敗',pct=100,updated_at=? WHERE job_id=?
                """,
                (now_text, canonical_job),
            )
            connection.commit()
        return self.get_job(canonical_job)

    def fail_queued(
        self,
        *,
        job_id: str,
        expected_version: int,
        failure_code: str,
    ) -> LocalJobRecord:
        """CAS a never-claimed queued job to terminal failure."""

        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", failure_code or ""):
            raise LocalRetrainError("local-failure-code-invalid")
        return self._simple_transition(
            job_id=job_id,
            expected_version=expected_version,
            expected_state="queued",
            target_state="failed",
            event_type="claim-failed",
            failure_code=failure_code,
        )

    def _simple_transition(
        self,
        *,
        job_id: str,
        expected_version: int,
        expected_state: str,
        target_state: str,
        event_type: str,
        failure_code: str | None = None,
    ) -> LocalJobRecord:
        canonical_job = _uuid4(job_id, "local-job-id-invalid")
        now_text = _iso(self.now())
        version = expected_version + 1
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """
                UPDATE local_retrain_jobs
                SET state=?,failure_code=?,record_version=?,updated_at=?,finished_at=?
                WHERE job_id=? AND state=? AND record_version=?
                """,
                (
                    target_state,
                    failure_code,
                    version,
                    now_text,
                    now_text if target_state == "failed" else None,
                    canonical_job,
                    expected_state,
                    expected_version,
                ),
            ).rowcount
            if changed != 1:
                connection.rollback()
                raise LocalRetrainConflict("local-job-transition-conflict")
            self._event(
                connection,
                job_id=canonical_job,
                event_type=event_type,
                from_state=expected_state,
                to_state=target_state,
                version=version,
                fence=None,
                observed_at=now_text,
            )
            connection.commit()
        if target_state == "failed":
            self.update_progress(canonical_job, "失敗", 100)
        return self.get_job(canonical_job)

    def update_progress(self, job_id: str, message: str, pct: int | None = None) -> None:
        canonical_job = _uuid4(job_id, "local-job-id-invalid")
        normalized = " ".join(str(message).split())[:500]
        if not normalized:
            return
        now_text = _iso(self.now())
        with self._lock, self._connect() as connection:
            if pct is None:
                changed = connection.execute(
                    "UPDATE local_retrain_progress SET progress=?,updated_at=? WHERE job_id=?",
                    (normalized, now_text, canonical_job),
                ).rowcount
            else:
                bounded = max(0, min(int(pct), 100))
                changed = connection.execute(
                    """
                    UPDATE local_retrain_progress
                    SET progress=?,pct=MAX(pct,?),updated_at=? WHERE job_id=?
                    """,
                    (normalized, bounded, now_text, canonical_job),
                ).rowcount
            if changed != 1:
                raise LocalRetrainNotFound("local-job-not-found")

    def _row_to_record(self, row: sqlite3.Row) -> LocalJobRecord:
        contract = (
            LocalTrainingContract.from_mapping(json.loads(row["contract_json"]))
            if row["contract_json"]
            else None
        )
        if contract is not None and contract.sha256 != row["contract_sha256"]:
            raise LocalRetrainError("local-job-contract-hash-mismatch")
        request_payload = json.loads(row["request_json"])
        result = json.loads(row["result_json"]) if row["result_json"] else None
        return LocalJobRecord(
            job_id=str(row["job_id"]),
            owner_id=str(row["owner_id"]),
            state=str(row["state"]),
            progress=str(row["progress"] or ""),
            pct=int(row["pct"] or 0),
            record_version=int(row["record_version"]),
            preparation_expires_at=(
                _parse_time(
                    row["preparation_expires_at"],
                    "local-preparation-lease-invalid",
                )
                if row["preparation_expires_at"]
                else None
            ),
            worker_id=str(row["worker_id"]) if row["worker_id"] else None,
            fencing_token=int(row["fencing_token"] or 0),
            lease_expires_at=(
                _parse_time(row["lease_expires_at"], "local-job-lease-invalid")
                if row["lease_expires_at"]
                else None
            ),
            request_payload=request_payload,
            contract=contract,
            contract_sha256=str(row["contract_sha256"]) if row["contract_sha256"] else None,
            snapshot_path=Path(row["snapshot_path"]) if row["snapshot_path"] else None,
            snapshot_sha256=str(row["snapshot_sha256"]) if row["snapshot_sha256"] else None,
            artifact_object_name=(
                str(row["artifact_object_name"]) if row["artifact_object_name"] else None
            ),
            artifact_path=Path(row["artifact_path"]) if row["artifact_path"] else None,
            artifact_sha256=str(row["artifact_sha256"]) if row["artifact_sha256"] else None,
            artifact_size_bytes=(
                int(row["artifact_size_bytes"])
                if row["artifact_size_bytes"] is not None
                else None
            ),
            artifact_media_type=(
                str(row["artifact_media_type"]) if row["artifact_media_type"] else None
            ),
            failure_code=str(row["failure_code"]) if row["failure_code"] else None,
            result=result,
        )

    def get_job(self, job_id: str) -> LocalJobRecord:
        canonical_job = _uuid4(job_id, "local-job-id-invalid")
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT j.*,p.progress,p.pct,r.result_json
                FROM local_retrain_jobs j
                JOIN local_retrain_progress p ON p.job_id=j.job_id
                LEFT JOIN local_retrain_results r ON r.job_id=j.job_id
                WHERE j.job_id=?
                """,
                (canonical_job,),
            ).fetchone()
        if row is None:
            raise LocalRetrainNotFound("local-job-not-found")
        return self._row_to_record(row)

    def get_job_for_owner(self, job_id: str, owner_id: str) -> LocalJobRecord:
        record = self.get_job(job_id)
        if record.owner_id != _uuid4(owner_id, "local-owner-id-invalid"):
            raise LocalRetrainNotFound("local-job-not-found")
        return record

    @staticmethod
    def _validate_result_projection(
        record: LocalJobRecord,
        artifact: RegisteredArtifact,
        result: Mapping[str, object],
        published_model_path: Path,
        *,
        require_registered_binding: bool = True,
    ) -> str:
        if (
            artifact.job_id != record.job_id
            or (
                require_registered_binding
                and (
                    record.state not in {"artifact-registered", "completed"}
                    or artifact.sha256 != record.artifact_sha256
                    or artifact.object_name != record.artifact_object_name
                    or artifact.size_bytes != record.artifact_size_bytes
                    or artifact.media_type != record.artifact_media_type
                    or record.artifact_path is None
                )
            )
        ):
            raise LocalRetrainConflict("local-result-artifact-binding-mismatch")
        path = Path(published_model_path)
        if not path.is_absolute() or path.is_symlink():
            raise LocalRetrainError("local-result-model-path-invalid")
        projected = dict(result)
        model_id = projected.get("model_id")
        metrics = projected.get("metrics")
        if (
            projected.get("success") is not True
            or not isinstance(model_id, str)
            or IDENTIFIER_RE.fullmatch(model_id) is None
            or not isinstance(metrics, Mapping)
            or any(
                isinstance(metric, bool)
                or not isinstance(metric, (int, float))
                or not math.isfinite(float(metric))
                for metric in metrics.values()
            )
        ):
            raise LocalRetrainError("local-result-invalid")
        projected.update(
            {
                "model_path": str(path),
                "artifact_sha256": artifact.sha256,
                "artifact_size_bytes": artifact.size_bytes,
                "artifact_object_name": artifact.object_name,
            }
        )
        try:
            encoded = _canonical_json(projected)
        except (TypeError, ValueError) as exc:
            raise LocalRetrainError("local-result-invalid") from exc
        if len(encoded.encode("utf-8")) > MAX_REQUEST_BYTES:
            raise LocalRetrainError("local-result-size-invalid")
        return encoded

    def prepare_publication(
        self,
        *,
        job_id: str,
        artifact: RegisteredArtifact,
        result: Mapping[str, object],
        published_model_path: Path,
    ) -> LocalPublicationRecord:
        record = self.get_job(job_id)
        encoded = self._validate_result_projection(
            record,
            artifact,
            result,
            published_model_path,
        )
        now_text = _iso(self.now())
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT state,artifact_sha256,artifact_object_name,artifact_size_bytes,
                       artifact_media_type FROM local_retrain_jobs WHERE job_id=?
                """,
                (record.job_id,),
            ).fetchone()
            if (
                row is None
                or row["state"] not in {"artifact-registered", "completed"}
                or row["artifact_sha256"] != artifact.sha256
                or row["artifact_object_name"] != artifact.object_name
                or row["artifact_size_bytes"] != artifact.size_bytes
                or row["artifact_media_type"] != artifact.media_type
            ):
                connection.rollback()
                raise LocalRetrainConflict("local-result-artifact-binding-mismatch")
            existing = connection.execute(
                "SELECT * FROM local_retrain_publications WHERE job_id=?",
                (record.job_id,),
            ).fetchone()
            if existing is None:
                try:
                    connection.execute(
                        """
                        INSERT INTO local_retrain_publications(
                            job_id,artifact_sha256,published_path,result_json,state,
                            created_at,updated_at
                        ) VALUES (?,?,?,?,'prepared',?,?)
                        """,
                        (
                            record.job_id,
                            artifact.sha256,
                            str(Path(published_model_path)),
                            encoded,
                            now_text,
                            now_text,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    connection.rollback()
                    raise LocalRetrainConflict("local-publication-path-conflict") from exc
            elif (
                existing["artifact_sha256"] != artifact.sha256
                or existing["published_path"] != str(Path(published_model_path))
                or existing["result_json"] != encoded
            ):
                connection.rollback()
                raise LocalRetrainConflict("local-publication-binding-conflict")
            connection.commit()
        return self.get_publication(record.job_id)

    def get_publication(self, job_id: str) -> LocalPublicationRecord:
        canonical_job = _uuid4(job_id, "local-job-id-invalid")
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM local_retrain_publications WHERE job_id=?",
                (canonical_job,),
            ).fetchone()
        if row is None:
            raise LocalRetrainNotFound("local-publication-not-found")
        return LocalPublicationRecord(
            job_id=canonical_job,
            artifact_sha256=str(row["artifact_sha256"]),
            published_path=Path(row["published_path"]),
            result=json.loads(row["result_json"]),
            state=str(row["state"]),
        )

    def pending_publications(self) -> list[LocalPublicationRecord]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT job_id FROM local_retrain_publications WHERE state='prepared' ORDER BY created_at"
            ).fetchall()
        return [self.get_publication(str(row["job_id"])) for row in rows]

    def published_publications(self) -> list[LocalPublicationRecord]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT job_id FROM local_retrain_publications WHERE state='published' ORDER BY created_at"
            ).fetchall()
        return [self.get_publication(str(row["job_id"])) for row in rows]

    def complete_publication(
        self,
        *,
        job_id: str,
        artifact_sha256: str,
        published_model_path: Path,
    ) -> LocalJobRecord:
        canonical_job = _uuid4(job_id, "local-job-id-invalid")
        path_text = str(Path(published_model_path))
        now_text = _iso(self.now())
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                "SELECT * FROM local_retrain_jobs WHERE job_id=?",
                (canonical_job,),
            ).fetchone()
            publication = connection.execute(
                "SELECT * FROM local_retrain_publications WHERE job_id=?",
                (canonical_job,),
            ).fetchone()
            if job is None or publication is None:
                connection.rollback()
                raise LocalRetrainConflict("local-publication-binding-mismatch")
            if (
                publication["artifact_sha256"] != artifact_sha256
                or publication["published_path"] != path_text
                or job["artifact_sha256"] != artifact_sha256
            ):
                connection.rollback()
                raise LocalRetrainConflict("local-publication-binding-mismatch")
            if job["state"] == "completed" and publication["state"] == "published":
                connection.commit()
                return self.get_job(canonical_job)
            if job["state"] != "artifact-registered" or publication["state"] != "prepared":
                connection.rollback()
                raise LocalRetrainConflict("local-publication-state-conflict")
            version = int(job["record_version"]) + 1
            try:
                connection.execute(
                    """
                    INSERT INTO local_retrain_results(job_id,artifact_sha256,result_json,recorded_at)
                    VALUES (?,?,?,?)
                    """,
                    (
                        canonical_job,
                        artifact_sha256,
                        publication["result_json"],
                        now_text,
                    ),
                )
                changed = connection.execute(
                    """
                    UPDATE local_retrain_jobs
                    SET state='completed',record_version=?,updated_at=?,finished_at=?
                    WHERE job_id=? AND state='artifact-registered' AND record_version=?
                    """,
                    (version, now_text, now_text, canonical_job, job["record_version"]),
                ).rowcount
                if changed != 1:
                    raise sqlite3.IntegrityError("completion CAS failed")
                connection.execute(
                    """
                    UPDATE local_retrain_publications SET state='published',updated_at=?
                    WHERE job_id=? AND state='prepared'
                    """,
                    (now_text, canonical_job),
                )
                connection.execute(
                    """
                    UPDATE local_retrain_progress SET progress='完了',pct=100,updated_at=?
                    WHERE job_id=?
                    """,
                    (now_text, canonical_job),
                )
                self._event(
                    connection,
                    job_id=canonical_job,
                    event_type="completed",
                    from_state="artifact-registered",
                    to_state="completed",
                    version=version,
                    fence=int(job["fencing_token"]),
                    observed_at=now_text,
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise LocalRetrainConflict("local-publication-completion-conflict") from exc
        return self.get_job(canonical_job)

    def events(self, job_id: str) -> list[dict[str, object]]:
        canonical_job = _uuid4(job_id, "local-job-id-invalid")
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_type,from_state,to_state,record_version,fencing_token,created_at
                FROM local_retrain_events WHERE job_id=? ORDER BY event_id
                """,
                (canonical_job,),
            ).fetchall()
        return [dict(row) for row in rows]

    def recover_expired_jobs(self) -> list[LocalJobRecord]:
        now = self.now()
        now_text = _iso(now)
        recovered: list[str] = []
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            recovered.extend(
                self._recover_expired_preparations_locked(
                    connection,
                    now=now,
                )
            )
            rows = connection.execute(
                """
                SELECT job_id,state,record_version,fencing_token FROM local_retrain_jobs
                WHERE state IN ('claimed','running') AND lease_expires_at<=?
                ORDER BY created_at,job_id
                """,
                (now_text,),
            ).fetchall()
            for row in rows:
                from_state = str(row["state"])
                target_state = "queued" if from_state == "claimed" else "failed"
                version = int(row["record_version"]) + 1
                failure = None if target_state == "queued" else "lease-expired-running"
                changed = connection.execute(
                    """
                    UPDATE local_retrain_jobs
                    SET state=?,preparation_token=NULL,preparation_expires_at=NULL,
                        worker_id=NULL,lease_expires_at=NULL,failure_code=?,
                        record_version=?,updated_at=?,finished_at=?
                    WHERE job_id=? AND state=? AND record_version=? AND fencing_token=?
                    """,
                    (
                        target_state,
                        failure,
                        version,
                        now_text,
                        now_text if target_state == "failed" else None,
                        row["job_id"],
                        from_state,
                        row["record_version"],
                        row["fencing_token"],
                    ),
                ).rowcount
                if changed != 1:
                    connection.rollback()
                    raise LocalRetrainConflict("local-job-recovery-conflict")
                self._event(
                    connection,
                    job_id=str(row["job_id"]),
                    event_type="lease-expired",
                    from_state=from_state,
                    to_state=target_state,
                    version=version,
                    fence=int(row["fencing_token"]),
                    observed_at=now_text,
                )
                recovered.append(str(row["job_id"]))
            connection.commit()
        for job_id in recovered:
            record = self.get_job(job_id)
            self.update_progress(
                job_id,
                "待機中" if record.state == "queued" else "中断",
                None if record.state == "queued" else 100,
            )
        return [self.get_job(job_id) for job_id in recovered]

    def recover_after_process_restart(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Fence jobs owned by the prior process and return safe redispatch IDs.

        This is intentionally separate from lease-expiry recovery and must only
        be called by the local application's startup lifecycle.
        """

        now_text = _iso(self.now())
        redispatch: list[str] = []
        failed: list[str] = []
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT job_id,state,record_version,fencing_token
                FROM local_retrain_jobs
                WHERE state IN (
                    'preparing','queued','claimed','running','artifact-registered'
                )
                ORDER BY created_at,job_id
                """
            ).fetchall()
            for row in rows:
                job_id = str(row["job_id"])
                state = str(row["state"])
                version = int(row["record_version"])
                fence = int(row["fencing_token"])
                if state == "queued":
                    redispatch.append(job_id)
                    continue
                if state == "artifact-registered":
                    publication = connection.execute(
                        "SELECT state FROM local_retrain_publications WHERE job_id=?",
                        (job_id,),
                    ).fetchone()
                    if publication is not None and publication["state"] == "prepared":
                        continue
                    target_state = "failed"
                    failure_code = "restart-before-result-publication"
                elif state == "claimed":
                    target_state = "queued"
                    failure_code = None
                elif state == "preparing":
                    target_state = "failed"
                    failure_code = "restart-during-snapshot"
                else:
                    target_state = "failed"
                    failure_code = "restart-during-training"
                next_version = version + 1
                changed = connection.execute(
                    """
                UPDATE local_retrain_jobs
                    SET state=?,preparation_token=NULL,preparation_expires_at=NULL,
                        worker_id=NULL,lease_expires_at=NULL,failure_code=?,
                        record_version=?,updated_at=?,finished_at=?
                    WHERE job_id=? AND state=? AND record_version=? AND fencing_token=?
                    """,
                    (
                        target_state,
                        failure_code,
                        next_version,
                        now_text,
                        now_text if target_state == "failed" else None,
                        job_id,
                        state,
                        version,
                        fence,
                    ),
                ).rowcount
                if changed != 1:
                    connection.rollback()
                    raise LocalRetrainConflict("local-startup-recovery-conflict")
                progress = "待機中" if target_state == "queued" else "中断"
                pct = 5 if target_state == "queued" else 100
                connection.execute(
                    """
                    UPDATE local_retrain_progress SET progress=?,pct=?,updated_at=?
                    WHERE job_id=?
                    """,
                    (progress, pct, now_text, job_id),
                )
                self._event(
                    connection,
                    job_id=job_id,
                    event_type="process-restarted",
                    from_state=state,
                    to_state=target_state,
                    version=next_version,
                    fence=fence,
                    observed_at=now_text,
                )
                if target_state == "queued":
                    redispatch.append(job_id)
                else:
                    failed.append(job_id)
            connection.commit()
        return tuple(redispatch), tuple(failed)


class LocalRetrainGateway:
    """Local filesystem adapter with the same fenced worker protocol as Staging."""

    def __init__(
        self,
        store: LocalRetrainStore,
        *,
        artifact_root: Path,
        published_model_root: Path,
        source_root: Path,
        candidate_commit_provider: Callable[[], str],
        active_model_binding_provider: Callable[[], tuple[str, str]],
    ) -> None:
        root = Path(artifact_root)
        source = Path(source_root)
        if not root.is_absolute() or root.is_symlink():
            raise LocalRetrainError("local-artifact-root-invalid")
        root.mkdir(parents=True, exist_ok=True)
        self._artifact_root = root.resolve(strict=True)
        published_root = Path(published_model_root)
        if not published_root.is_absolute() or published_root.is_symlink():
            raise LocalRetrainError("local-published-model-root-invalid")
        published_root.mkdir(parents=True, exist_ok=True)
        self._published_model_root = published_root.resolve(strict=True)
        if (
            not self._published_model_root.is_dir()
            or self._artifact_root == self._published_model_root
        ):
            raise LocalRetrainError("local-published-model-root-invalid")
        if not source.is_absolute() or source.is_symlink():
            raise LocalRetrainError("local-source-tree-root-invalid")
        self._source_root = source.resolve(strict=True)
        if not callable(candidate_commit_provider):
            raise LocalRetrainError("local-candidate-commit-provider-invalid")
        self._candidate_commit_provider = candidate_commit_provider
        self._active_model_binding_provider = active_model_binding_provider
        self._store = store
        self._lock = threading.RLock()

    @staticmethod
    def _validate_ttl(ttl_seconds: int) -> None:
        if type(ttl_seconds) is not int or not 30 <= ttl_seconds <= 300:
            raise RetrainWorkerError("retrain-worker-ttl-invalid")

    def _artifact_path(self, object_name: str) -> tuple[str, str, Path]:
        match = OBJECT_RE.fullmatch(object_name or "")
        if match is None:
            raise RetrainGatewayUnavailable("local-artifact-object-name-invalid")
        job_id, digest = match.groups()
        directory = self._artifact_root / job_id
        if directory.exists() and directory.is_symlink():
            raise RetrainGatewayUnavailable("local-artifact-directory-invalid")
        directory.mkdir(parents=False, exist_ok=True)
        resolved_directory = directory.resolve(strict=True)
        if not resolved_directory.is_relative_to(self._artifact_root):
            raise RetrainGatewayUnavailable("local-artifact-directory-invalid")
        # Keep the physical name short enough for default Windows MAX_PATH.
        # The full digest remains bound in the object name and durable ledger.
        path = resolved_directory / "artifact.joblib"
        return job_id, digest, path

    def _require_runtime_bindings(self, contract: LocalTrainingContract) -> None:
        candidate_commit = self._candidate_commit_provider()
        if candidate_commit != contract.candidate_commit_sha:
            raise RetrainLeaseLost("local-candidate-commit-binding-changed")
        active_binding = self._active_model_binding_provider()
        if (
            not isinstance(active_binding, tuple)
            or len(active_binding) != 2
            or active_binding[0] != contract.active_model_id
            or active_binding[1] != contract.active_model_sha256
        ):
            raise RetrainLeaseLost("local-active-model-binding-changed")
        if compute_source_tree_sha256(self._source_root) != contract.source_tree_sha256:
            raise RetrainLeaseLost("local-source-tree-binding-changed")

    def claim(
        self,
        *,
        worker_id: str,
        job_id: str,
        expected_version: int,
        ttl_seconds: int,
    ) -> RetrainLease:
        self._validate_ttl(ttl_seconds)
        if WORKER_RE.fullmatch(worker_id or "") is None:
            raise RetrainGatewayUnavailable("local-worker-id-invalid")
        canonical_job = _uuid4(job_id, "local-job-id-invalid")
        now = self._store.now()
        now_text = _iso(now)
        expires = now + timedelta(seconds=ttl_seconds)
        with self._store._lock, self._store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM local_retrain_jobs WHERE job_id=?",
                (canonical_job,),
            ).fetchone()
            if (
                row is None
                or row["state"] != "queued"
                or int(row["record_version"]) != expected_version
                or not row["contract_json"]
            ):
                connection.rollback()
                raise RetrainGatewayUnavailable("local-retrain-claim-conflict")
            contract = LocalTrainingContract.from_mapping(json.loads(row["contract_json"]))
            if contract.sha256 != row["contract_sha256"]:
                connection.rollback()
                raise RetrainGatewayUnavailable("local-retrain-contract-invalid")
            self._require_runtime_bindings(contract)
            cursor = connection.execute(
                "INSERT INTO local_retrain_fencing_sequence(job_id,issued_at) VALUES (?,?)",
                (canonical_job, now_text),
            )
            fence = int(cursor.lastrowid)
            version = expected_version + 1
            changed = connection.execute(
                """
                UPDATE local_retrain_jobs
                SET state='claimed',worker_id=?,fencing_token=?,lease_expires_at=?,
                    record_version=?,updated_at=?
                WHERE job_id=? AND state='queued' AND record_version=?
                """,
                (
                    worker_id,
                    fence,
                    _iso(expires),
                    version,
                    now_text,
                    canonical_job,
                    expected_version,
                ),
            ).rowcount
            if changed != 1:
                connection.rollback()
                raise RetrainGatewayUnavailable("local-retrain-claim-conflict")
            self._store._event(
                connection,
                job_id=canonical_job,
                event_type="claimed",
                from_state="queued",
                to_state="claimed",
                version=version,
                fence=fence,
                observed_at=now_text,
            )
            connection.commit()
        self._store.update_progress(canonical_job, "開始待ち", 8)
        return RetrainLease(canonical_job, worker_id, fence, version, expires, "claimed")

    def _require_lease_row(
        self,
        connection: sqlite3.Connection,
        lease: RetrainLease,
        *,
        states: Collection[str],
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM local_retrain_jobs WHERE job_id=?",
            (lease.job_id,),
        ).fetchone()
        now = self._store.now()
        if (
            row is None
            or row["state"] not in states
            or row["worker_id"] != lease.worker_id
            or int(row["fencing_token"]) != lease.fencing_token
            or int(row["record_version"]) != lease.record_version
            or not row["lease_expires_at"]
            or _parse_time(row["lease_expires_at"], "local-job-lease-invalid") <= now
        ):
            raise RetrainLeaseLost("local-retrain-lease-lost")
        return row

    def execution_bundle(
        self,
        *,
        lease: RetrainLease,
        candidate_commit_sha: str,
        active_model_id: str,
    ) -> Mapping[str, Any]:
        with self._store._lock, self._store._connect() as connection:
            row = self._require_lease_row(connection, lease, states={"claimed"})
            contract = LocalTrainingContract.from_mapping(json.loads(row["contract_json"]))
            if (
                contract.sha256 != row["contract_sha256"]
                or contract.candidate_commit_sha != candidate_commit_sha
                or contract.active_model_id != active_model_id
            ):
                raise RetrainGatewayUnavailable("local-retrain-bundle-binding-mismatch")
            self._require_runtime_bindings(contract)
            return {
                "schema_version": 1,
                "execution_policy": LOCAL_EXECUTION_POLICY,
                "contract": contract.to_mapping(),
                "contract_sha256": contract.sha256,
                "worker_id": lease.worker_id,
                "fencing_token": lease.fencing_token,
                "record_version": lease.record_version,
                "lease_expires_at": str(row["lease_expires_at"]),
            }

    def heartbeat(self, *, lease: RetrainLease, ttl_seconds: int) -> RetrainLease:
        self._validate_ttl(ttl_seconds)
        now = self._store.now()
        expires = now + timedelta(seconds=ttl_seconds)
        now_text = _iso(now)
        with self._store._lock, self._store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._require_lease_row(connection, lease, states={"claimed", "running"})
            version = lease.record_version + 1
            changed = connection.execute(
                """
                UPDATE local_retrain_jobs SET lease_expires_at=?,record_version=?,updated_at=?
                WHERE job_id=? AND state=? AND worker_id=? AND fencing_token=? AND record_version=?
                """,
                (
                    _iso(expires),
                    version,
                    now_text,
                    lease.job_id,
                    row["state"],
                    lease.worker_id,
                    lease.fencing_token,
                    lease.record_version,
                ),
            ).rowcount
            if changed != 1:
                connection.rollback()
                raise RetrainLeaseLost("local-retrain-lease-lost")
            self._store._event(
                connection,
                job_id=lease.job_id,
                event_type="heartbeat",
                from_state=str(row["state"]),
                to_state=str(row["state"]),
                version=version,
                fence=lease.fencing_token,
                observed_at=now_text,
            )
            connection.commit()
        return RetrainLease(
            lease.job_id,
            lease.worker_id,
            lease.fencing_token,
            version,
            expires,
            str(row["state"]),
        )

    def start(self, *, lease: RetrainLease) -> RetrainLease:
        now = self._store.now()
        now_text = _iso(now)
        with self._store._lock, self._store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_lease_row(connection, lease, states={"claimed"})
            version = lease.record_version + 1
            changed = connection.execute(
                """
                UPDATE local_retrain_jobs
                SET state='running',record_version=?,updated_at=?,started_at=?
                WHERE job_id=? AND state='claimed' AND worker_id=? AND fencing_token=?
                  AND record_version=?
                """,
                (
                    version,
                    now_text,
                    now_text,
                    lease.job_id,
                    lease.worker_id,
                    lease.fencing_token,
                    lease.record_version,
                ),
            ).rowcount
            if changed != 1:
                connection.rollback()
                raise RetrainLeaseLost("local-retrain-lease-lost")
            self._store._event(
                connection,
                job_id=lease.job_id,
                event_type="started",
                from_state="claimed",
                to_state="running",
                version=version,
                fence=lease.fencing_token,
                observed_at=now_text,
            )
            connection.commit()
        self._store.update_progress(lease.job_id, "作成中", 10)
        return RetrainLease(
            lease.job_id,
            lease.worker_id,
            lease.fencing_token,
            version,
            lease.lease_expires_at,
            "running",
        )

    def fail(self, *, lease: RetrainLease, failure_code: str) -> RetrainLease:
        if failure_code not in FAILURE_CODES:
            raise RetrainWorkerError("retrain-failure-code-invalid")
        now = self._store.now()
        now_text = _iso(now)
        with self._store._lock, self._store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._require_lease_row(connection, lease, states={"claimed", "running"})
            version = lease.record_version + 1
            changed = connection.execute(
                """
                UPDATE local_retrain_jobs
                SET state='failed',failure_code=?,record_version=?,updated_at=?,finished_at=?
                WHERE job_id=? AND state=? AND worker_id=? AND fencing_token=? AND record_version=?
                """,
                (
                    failure_code,
                    version,
                    now_text,
                    now_text,
                    lease.job_id,
                    row["state"],
                    lease.worker_id,
                    lease.fencing_token,
                    lease.record_version,
                ),
            ).rowcount
            if changed != 1:
                connection.rollback()
                raise RetrainLeaseLost("local-retrain-lease-lost")
            self._store._event(
                connection,
                job_id=lease.job_id,
                event_type="failed",
                from_state=str(row["state"]),
                to_state="failed",
                version=version,
                fence=lease.fencing_token,
                observed_at=now_text,
            )
            connection.commit()
        self._store.update_progress(lease.job_id, "失敗", 100)
        return RetrainLease(
            lease.job_id,
            lease.worker_id,
            lease.fencing_token,
            version,
            lease.lease_expires_at,
            "failed",
        )

    def upload(self, *, artifact_file: BinaryIO, object_name: str, media_type: str) -> None:
        if media_type != ARTIFACT_MEDIA_TYPE:
            raise RetrainGatewayUnavailable("local-artifact-media-type-invalid")
        _job_id, expected_digest, destination = self._artifact_path(object_name)
        temporary = self._artifact_root / f".upload.{uuid.uuid4().hex}.tmp"
        size = 0
        digest = hashlib.sha256()
        try:
            with temporary.open("xb") as output:
                for chunk in iter(lambda: artifact_file.read(1024 * 1024), b""):
                    size += len(chunk)
                    if size > MAX_ARTIFACT_BYTES:
                        raise RetrainGatewayUnavailable("local-artifact-size-invalid")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if size < 1 or digest.hexdigest() != expected_digest:
                raise RetrainGatewayUnavailable("local-artifact-digest-mismatch")
            try:
                os.link(temporary, destination)
            except FileExistsError as exc:
                raise RetrainGatewayUnavailable("local-artifact-already-exists") from exc
            except OSError as exc:
                raise RetrainGatewayUnavailable("local-artifact-publish-failed") from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def stage_result(
        self,
        lease: RetrainLease,
        artifact: RegisteredArtifact,
        response: object,
    ) -> RetrainLease:
        """Durably bind the trainer result before any artifact upload/register."""

        if isinstance(response, Mapping):
            result = dict(response)
        else:
            serializer = getattr(response, "model_dump", None)
            if not callable(serializer):
                raise LocalRetrainError("local-result-invalid")
            serialized = serializer()
            if not isinstance(serialized, Mapping):
                raise LocalRetrainError("local-result-invalid")
            result = dict(serialized)
        if artifact.job_id != lease.job_id or artifact.media_type != ARTIFACT_MEDIA_TYPE:
            raise RetrainGatewayUnavailable("local-artifact-binding-invalid")
        match = OBJECT_RE.fullmatch(artifact.object_name or "")
        if (
            match is None
            or match.group(1) != lease.job_id
            or match.group(2) != artifact.sha256
            or DIGEST_RE.fullmatch(artifact.sha256 or "") is None
            or artifact.size_bytes < 1
            or artifact.size_bytes > MAX_ARTIFACT_BYTES
        ):
            raise RetrainGatewayUnavailable("local-artifact-binding-invalid")
        record = self._store.get_job(lease.job_id)
        if record.state != "running" or record.contract is None:
            raise RetrainLeaseLost("local-retrain-lease-lost")
        self._require_runtime_bindings(record.contract)
        destination = self._publication_destination(
            record,
            result,
            require_original_path=True,
        )
        if destination.exists():
            raise LocalRetrainConflict("local-published-model-already-exists")
        encoded = self._store._validate_result_projection(
            record,
            artifact,
            result,
            destination,
            require_registered_binding=False,
        )
        now_text = _iso(self._store.now())
        with self._store._lock, self._store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._require_lease_row(connection, lease, states={"running"})
            contract = LocalTrainingContract.from_mapping(json.loads(row["contract_json"]))
            if contract.sha256 != row["contract_sha256"]:
                connection.rollback()
                raise RetrainGatewayUnavailable("local-retrain-contract-invalid")
            self._require_runtime_bindings(contract)
            existing = connection.execute(
                "SELECT * FROM local_retrain_staged_outputs WHERE job_id=?",
                (lease.job_id,),
            ).fetchone()
            expected = (
                lease.fencing_token,
                artifact.object_name,
                artifact.sha256,
                artifact.size_bytes,
                artifact.media_type,
                str(destination),
                encoded,
            )
            if existing is None:
                try:
                    connection.execute(
                        """
                        INSERT INTO local_retrain_staged_outputs(
                            job_id,fencing_token,artifact_object_name,artifact_sha256,
                            artifact_size_bytes,artifact_media_type,published_path,
                            result_json,created_at
                        ) VALUES (?,?,?,?,?,?,?,?,?)
                        """,
                        (lease.job_id, *expected, now_text),
                    )
                except sqlite3.IntegrityError as exc:
                    connection.rollback()
                    raise LocalRetrainConflict("local-staged-output-conflict") from exc
            else:
                observed = (
                    int(existing["fencing_token"]),
                    str(existing["artifact_object_name"]),
                    str(existing["artifact_sha256"]),
                    int(existing["artifact_size_bytes"]),
                    str(existing["artifact_media_type"]),
                    str(existing["published_path"]),
                    str(existing["result_json"]),
                )
                if observed != expected:
                    connection.rollback()
                    raise LocalRetrainConflict("local-staged-output-conflict")
            connection.commit()
        return lease

    def register(
        self,
        *,
        lease: RetrainLease,
        artifact: RegisteredArtifact,
    ) -> RetrainLease:
        if artifact.job_id != lease.job_id or artifact.media_type != ARTIFACT_MEDIA_TYPE:
            raise RetrainGatewayUnavailable("local-artifact-binding-invalid")
        parsed_job_id, parsed_digest, path = self._artifact_path(artifact.object_name)
        if parsed_job_id != lease.job_id or parsed_digest != artifact.sha256:
            raise RetrainGatewayUnavailable("local-artifact-binding-invalid")
        if path.is_symlink() or not path.is_file():
            raise RetrainGatewayUnavailable("local-artifact-unavailable")
        digest, size = sha256_file(path, max_bytes=MAX_ARTIFACT_BYTES)
        if digest != artifact.sha256 or size != artifact.size_bytes or size < 1:
            raise RetrainGatewayUnavailable("local-artifact-digest-mismatch")

        now = self._store.now()
        now_text = _iso(now)
        with self._store._lock, self._store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._require_lease_row(connection, lease, states={"running"})
            contract = LocalTrainingContract.from_mapping(json.loads(row["contract_json"]))
            if contract.sha256 != row["contract_sha256"]:
                connection.rollback()
                raise RetrainGatewayUnavailable("local-retrain-contract-invalid")
            staged = connection.execute(
                "SELECT * FROM local_retrain_staged_outputs WHERE job_id=?",
                (lease.job_id,),
            ).fetchone()
            if (
                staged is None
                or int(staged["fencing_token"]) != lease.fencing_token
                or staged["artifact_object_name"] != artifact.object_name
                or staged["artifact_sha256"] != artifact.sha256
                or int(staged["artifact_size_bytes"]) != artifact.size_bytes
                or staged["artifact_media_type"] != artifact.media_type
            ):
                connection.rollback()
                raise RetrainGatewayUnavailable("local-staged-output-binding-mismatch")
            # This is intentionally immediately before the authoritative
            # registration transition. A stale code tree or active model can
            # never publish a candidate, even when training already finished.
            self._require_runtime_bindings(contract)
            version = lease.record_version + 1
            changed = connection.execute(
                """
                UPDATE local_retrain_jobs
                SET state='artifact-registered',artifact_object_name=?,artifact_path=?,
                    artifact_sha256=?,artifact_size_bytes=?,artifact_media_type=?,
                    record_version=?,updated_at=?,finished_at=?
                WHERE job_id=? AND state='running' AND worker_id=? AND fencing_token=?
                  AND record_version=?
                """,
                (
                    artifact.object_name,
                    str(path),
                    artifact.sha256,
                    artifact.size_bytes,
                    artifact.media_type,
                    version,
                    now_text,
                    now_text,
                    lease.job_id,
                    lease.worker_id,
                    lease.fencing_token,
                    lease.record_version,
                ),
            ).rowcount
            if changed != 1:
                connection.rollback()
                raise RetrainLeaseLost("local-retrain-lease-lost")
            self._store._event(
                connection,
                job_id=lease.job_id,
                event_type="artifact-registered",
                from_state="running",
                to_state="artifact-registered",
                version=version,
                fence=lease.fencing_token,
                observed_at=now_text,
            )
            try:
                connection.execute(
                    """
                    INSERT INTO local_retrain_publications(
                        job_id,artifact_sha256,published_path,result_json,state,
                        created_at,updated_at
                    ) VALUES (?,?,?,?,'prepared',?,?)
                    """,
                    (
                        lease.job_id,
                        artifact.sha256,
                        staged["published_path"],
                        staged["result_json"],
                        now_text,
                        now_text,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise RetrainGatewayUnavailable("local-publication-intent-conflict") from exc
            connection.execute(
                """
                UPDATE local_retrain_progress
                SET progress='成果物を登録中',pct=MAX(pct,99),updated_at=?
                WHERE job_id=?
                """,
                (now_text, lease.job_id),
            )
            connection.commit()
        return RetrainLease(
            lease.job_id,
            lease.worker_id,
            lease.fencing_token,
            version,
            lease.lease_expires_at,
            "artifact-registered",
        )

    def remove(self, *, object_name: str) -> None:
        job_id, _digest, path = self._artifact_path(object_name)
        try:
            record = self._store.get_job(job_id)
        except LocalRetrainNotFound as exc:
            raise RetrainGatewayUnavailable("local-artifact-job-unavailable") from exc
        if (
            record.state == "artifact-registered"
            or record.artifact_object_name is not None
            or record.artifact_sha256 is not None
        ):
            raise RetrainGatewayUnavailable("local-registered-artifact-removal-forbidden")
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise RetrainGatewayUnavailable("local-artifact-cleanup-failed") from exc

    @staticmethod
    def _artifact_from_record(record: LocalJobRecord) -> RegisteredArtifact:
        if (
            record.artifact_object_name is None
            or record.artifact_sha256 is None
            or record.artifact_size_bytes is None
            or record.artifact_media_type is None
        ):
            raise LocalRetrainError("local-registered-artifact-binding-invalid")
        return RegisteredArtifact(
            job_id=record.job_id,
            object_name=record.artifact_object_name,
            sha256=record.artifact_sha256,
            size_bytes=record.artifact_size_bytes,
            media_type=record.artifact_media_type,
        )

    def _publication_destination(
        self,
        record: LocalJobRecord,
        result: Mapping[str, object],
        *,
        require_original_path: bool,
    ) -> Path:
        if record.contract is None:
            raise LocalRetrainError("local-job-contract-unavailable")
        model_id = result.get("model_id")
        if not isinstance(model_id, str) or IDENTIFIER_RE.fullmatch(model_id) is None:
            raise LocalRetrainError("local-result-model-id-invalid")
        filename = f"model_{record.contract.target}_{record.contract.model_type}_{model_id}.joblib"
        match = VISIBLE_MODEL_RE.fullmatch(filename)
        if (
            match is None
            or match.group(1) != record.contract.target
            or match.group(2) != model_id
        ):
            raise LocalRetrainError("local-result-model-name-invalid")
        original_path = result.get("model_path")
        if require_original_path and (
            not isinstance(original_path, str) or Path(original_path).name != filename
        ):
            raise LocalRetrainError("local-result-model-name-invalid")
        destination = self._published_model_root / filename
        if destination.parent.resolve(strict=True) != self._published_model_root:
            raise LocalRetrainError("local-result-model-path-invalid")
        return destination

    def _verify_registered_artifact(self, record: LocalJobRecord) -> Path:
        if record.artifact_path is None or record.artifact_sha256 is None:
            raise LocalRetrainError("local-registered-artifact-binding-invalid")
        path = record.artifact_path
        if path.is_symlink():
            raise LocalRetrainError("local-registered-artifact-path-invalid")
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise LocalRetrainError("local-registered-artifact-path-invalid") from exc
        if not resolved.is_file() or not resolved.is_relative_to(self._artifact_root):
            raise LocalRetrainError("local-registered-artifact-path-invalid")
        digest, size = sha256_file(resolved, max_bytes=MAX_ARTIFACT_BYTES)
        if digest != record.artifact_sha256 or size != record.artifact_size_bytes:
            raise LocalRetrainError("local-registered-artifact-digest-mismatch")
        return resolved

    @staticmethod
    def _sync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _publish_prepared(
        self,
        publication: LocalPublicationRecord,
        *,
        allow_existing: bool,
    ) -> LocalJobRecord:
        record = self._store.get_job(publication.job_id)
        if record.state not in {"artifact-registered", "completed"}:
            raise LocalRetrainConflict("local-publication-state-conflict")
        if record.contract is None:
            raise LocalRetrainError("local-job-contract-unavailable")
        source = self._verify_registered_artifact(record)
        expected_destination = self._publication_destination(
            record,
            publication.result,
            require_original_path=False,
        )
        try:
            destination = publication.published_path.resolve(strict=False)
        except OSError as exc:
            raise LocalRetrainError("local-publication-path-invalid") from exc
        if destination != expected_destination:
            raise LocalRetrainError("local-publication-path-invalid")
        if destination.exists():
            if not allow_existing or destination.is_symlink() or not destination.is_file():
                raise LocalRetrainConflict("local-published-model-already-exists")
            digest, size = sha256_file(destination, max_bytes=MAX_ARTIFACT_BYTES)
            if digest != publication.artifact_sha256 or size != record.artifact_size_bytes:
                raise LocalRetrainError("local-published-model-digest-mismatch")
        else:
            try:
                os.link(source, destination)
            except FileExistsError:
                digest, size = sha256_file(destination, max_bytes=MAX_ARTIFACT_BYTES)
                if digest != publication.artifact_sha256 or size != record.artifact_size_bytes:
                    raise LocalRetrainError("local-published-model-digest-mismatch")
            except OSError as exc:
                raise LocalRetrainError("local-published-model-publish-failed") from exc
            self._sync_directory(destination.parent)
        digest, size = sha256_file(destination, max_bytes=MAX_ARTIFACT_BYTES)
        if digest != publication.artifact_sha256 or size != record.artifact_size_bytes:
            raise LocalRetrainError("local-published-model-digest-mismatch")
        try:
            destination.chmod(stat.S_IREAD)
        except OSError:
            pass
        return self._store.complete_publication(
            job_id=record.job_id,
            artifact_sha256=publication.artifact_sha256,
            published_model_path=destination,
        )

    def record_result(
        self,
        *,
        job_id: str,
        artifact: RegisteredArtifact,
        result: Mapping[str, object] | None = None,
    ) -> LocalJobRecord:
        """Publish the staged result for an already-registered artifact."""

        with self._lock:
            record = self._store.get_job(job_id)
            if record.state not in {"artifact-registered", "completed"}:
                raise LocalRetrainConflict("local-publication-state-conflict")
            if record.contract is None:
                raise LocalRetrainError("local-job-contract-unavailable")
            self._verify_registered_artifact(record)
            registered_artifact = self._artifact_from_record(record)
            if registered_artifact != artifact:
                raise LocalRetrainConflict("local-result-artifact-binding-mismatch")
            try:
                publication = self._store.get_publication(record.job_id)
            except LocalRetrainNotFound as exc:
                raise LocalRetrainConflict("local-publication-intent-missing") from exc
            if result is not None:
                destination = self._publication_destination(
                    record,
                    result,
                    require_original_path=True,
                )
                publication = self._store.prepare_publication(
                    job_id=record.job_id,
                    artifact=artifact,
                    result=result,
                    published_model_path=destination,
                )
            return self._publish_prepared(
                publication,
                allow_existing=True,
            )

    def finalize_registered_result(self, *, job_id: str) -> LocalJobRecord:
        """Idempotently publish an already-registered durable result.

        Registration has already fenced the worker and atomically persisted
        the staged result plus publication intent.  This recovery entry point
        deliberately accepts neither a trainer response nor a lease: callers
        can only finish the immutable intent that is already bound in the
        ledger, so it can never rerun training or replace the registered
        artifact.
        """

        with self._lock:
            record = self._store.get_job(job_id)
            if record.state not in {"artifact-registered", "completed"}:
                raise LocalRetrainConflict("local-publication-state-conflict")
            artifact = self._artifact_from_record(record)
            self._verify_registered_artifact(record)
            try:
                publication = self._store.get_publication(record.job_id)
            except LocalRetrainNotFound as exc:
                raise LocalRetrainConflict("local-publication-intent-missing") from exc
            if publication.artifact_sha256 != artifact.sha256:
                raise LocalRetrainConflict("local-publication-artifact-mismatch")
            return self._publish_prepared(
                publication,
                allow_existing=True,
            )

    def reconcile_orphan_artifacts(self) -> LocalReconcileReport:
        """Reconcile durable publication intents and safely remove known orphans."""

        with self._lock:
            redispatch, failed = self._store.recover_after_process_restart()
            completed_publications = 0
            for publication in self._store.pending_publications():
                self._publish_prepared(publication, allow_existing=True)
                completed_publications += 1
            restored_publications = 0
            for publication in self._store.published_publications():
                was_missing = not publication.published_path.exists()
                self._publish_prepared(publication, allow_existing=True)
                if was_missing:
                    restored_publications += 1

            retained = 0
            removed = 0
            for directory in sorted(self._artifact_root.iterdir(), key=lambda item: item.name):
                if directory.is_file() and not directory.is_symlink():
                    if re.fullmatch(r"\.upload\.[0-9a-f]{32}\.tmp", directory.name) is None:
                        raise LocalRetrainError("local-artifact-catalog-entry-invalid")
                    try:
                        directory.chmod(stat.S_IWRITE | stat.S_IREAD)
                    except OSError:
                        pass
                    directory.unlink()
                    removed += 1
                    continue
                if directory.is_symlink() or not directory.is_dir():
                    raise LocalRetrainError("local-artifact-catalog-entry-invalid")
                try:
                    job_id = _uuid4(directory.name, "local-artifact-job-directory-invalid")
                    record = self._store.get_job(job_id)
                except LocalRetrainNotFound as exc:
                    raise LocalRetrainError("local-artifact-job-unavailable") from exc
                for path in sorted(directory.iterdir(), key=lambda item: item.name):
                    if path.is_symlink() or not path.is_file():
                        raise LocalRetrainError("local-artifact-catalog-entry-invalid")
                    digest_match = re.fullmatch(r"artifact\.joblib", path.name)
                    temporary_match = re.fullmatch(
                        r"\.[0-9a-f]{64}\.[0-9a-f]{32}\.tmp",
                        path.name,
                    )
                    if digest_match is None and temporary_match is None:
                        raise LocalRetrainError("local-artifact-catalog-entry-invalid")
                    if record.state == "failed" and record.artifact_sha256 is None:
                        try:
                            path.chmod(stat.S_IWRITE | stat.S_IREAD)
                        except OSError:
                            pass
                        path.unlink()
                        removed += 1
                        continue
                    if temporary_match is not None:
                        try:
                            path.chmod(stat.S_IWRITE | stat.S_IREAD)
                        except OSError:
                            pass
                        path.unlink()
                        removed += 1
                        continue
                    expected = record.artifact_path
                    if (
                        record.state not in {"artifact-registered", "completed", "failed"}
                        or record.artifact_sha256 is None
                        or expected is None
                        or expected.resolve(strict=False) != path.resolve(strict=True)
                    ):
                        raise LocalRetrainError("local-artifact-binding-mismatch")
                    digest, size = sha256_file(path, max_bytes=MAX_ARTIFACT_BYTES)
                    if digest != record.artifact_sha256 or size != record.artifact_size_bytes:
                        raise LocalRetrainError("local-artifact-digest-mismatch")
                    retained += 1
                try:
                    directory.rmdir()
                except OSError:
                    pass
            return LocalReconcileReport(
                redispatch_job_ids=redispatch,
                failed_job_ids=failed,
                completed_publications=completed_publications,
                restored_publications=restored_publications,
                removed_orphans=removed,
                retained_artifacts=retained,
            )

    def progress_observer(
        self,
        job_id: str,
    ) -> Callable[[RetrainLease, str, int | None], RetrainLease]:
        canonical_job = _uuid4(job_id, "local-job-id-invalid")

        def observe(
            lease: RetrainLease,
            message: str,
            pct: int | None = None,
        ) -> RetrainLease:
            if lease.job_id != canonical_job:
                raise RetrainLeaseLost("local-retrain-lease-lost")
            normalized = " ".join(str(message).split())[:500]
            if not normalized:
                return lease
            now_text = _iso(self._store.now())
            with self._store._lock, self._store._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._require_lease_row(connection, lease, states={"running"})
                if pct is None:
                    changed = connection.execute(
                        """
                        UPDATE local_retrain_progress
                        SET progress=?,updated_at=? WHERE job_id=?
                        """,
                        (normalized, now_text, canonical_job),
                    ).rowcount
                else:
                    bounded = max(0, min(int(pct), 100))
                    changed = connection.execute(
                        """
                        UPDATE local_retrain_progress
                        SET progress=?,pct=MAX(pct,?),updated_at=? WHERE job_id=?
                        """,
                        (normalized, bounded, now_text, canonical_job),
                    ).rowcount
                if changed != 1:
                    connection.rollback()
                    raise LocalRetrainNotFound("local-job-not-found")
                connection.commit()
            return lease

        return observe
