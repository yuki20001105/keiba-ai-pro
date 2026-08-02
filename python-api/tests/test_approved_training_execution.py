from __future__ import annotations

import asyncio
import hashlib
import importlib
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException


PYTHON_API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_API))
approved = importlib.import_module("training.approved_execution")
models = importlib.import_module("models")
train = importlib.import_module("routers.train")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _create_execution(
    tmp_path: Path,
    *,
    snapshot_path: Path | None = None,
    snapshot_sha256: str | None = None,
    selected_features: tuple[str, ...] = ("feature_a", "feature_b"),
    removed_features: tuple[str, ...] = ("feature_b",),
    train_period_start: str = "20250101",
    train_period_end: str = "20251231",
    validation_period_start: str = "20260101",
    validation_period_end: str = "20260331",
) -> approved.ApprovedTrainingExecution:
    workspace = tmp_path / "approved-job"
    workspace.mkdir(exist_ok=True)
    snapshot = snapshot_path or workspace / "snapshot.db"
    if snapshot_path is None:
        snapshot.write_bytes(b"approved immutable sqlite snapshot")
    return approved.ApprovedTrainingExecution.create(
        job_id=str(uuid.uuid4()),
        approved_payload_hash="a" * 64,
        data_snapshot_sha256=snapshot_sha256 or _sha256(snapshot),
        feature_contract_sha256="b" * 64,
        candidate_commit_sha="c" * 40,
        target="win",
        model_type="lightgbm",
        train_period_start=train_period_start,
        train_period_end=train_period_end,
        validation_period_start=validation_period_start,
        validation_period_end=validation_period_end,
        selected_features=selected_features,
        removed_features=removed_features,
        workspace=workspace,
        snapshot_path=snapshot,
    )


def test_valid_execution_binds_snapshot_features_and_artifact_directory(tmp_path: Path) -> None:
    execution = _create_execution(tmp_path)

    execution.validate_request(
        target="win",
        model_type="lightgbm",
        force_sync=False,
        test_size=0.2,
        cv_folds=5,
        use_optuna=False,
        training_date_from=None,
        training_date_to=None,
    )
    execution.verify_snapshot()

    assert execution.effective_features == ("feature_a",)
    assert execution.select_feature_columns(
        ["unapproved_extra", "feature_a"],
        future_fields={"finish_position"},
    ) == ("feature_a",)
    artifact_directory = execution.prepare_artifact_directory()
    assert artifact_directory == execution.workspace / "artifacts"
    assert artifact_directory.is_dir()


@pytest.mark.parametrize(
    ("target", "model_type", "force_sync"),
    [
        ("place", "lightgbm", False),
        ("win", "random_forest", False),
        ("win", "lightgbm", True),
    ],
)
def test_request_must_match_approved_training_shape(
    tmp_path: Path,
    target: str,
    model_type: str,
    force_sync: bool,
) -> None:
    execution = _create_execution(tmp_path)

    with pytest.raises(approved.ApprovedExecutionError):
        execution.validate_request(
            target=target,
            model_type=model_type,
            force_sync=force_sync,
            test_size=0.2,
            cv_folds=5,
            use_optuna=False,
            training_date_from=None,
            training_date_to=None,
        )


def test_unapproved_tuning_or_date_overrides_are_rejected(tmp_path: Path) -> None:
    execution = _create_execution(tmp_path)

    with pytest.raises(approved.ApprovedExecutionError, match="parameters-not-fixed"):
        execution.validate_request(
            target="win",
            model_type="lightgbm",
            force_sync=False,
            test_size=0.2,
            cv_folds=5,
            use_optuna=True,
            training_date_from=None,
            training_date_to=None,
        )


def test_snapshot_must_be_inside_workspace_and_match_digest(tmp_path: Path) -> None:
    outside_snapshot = tmp_path / "outside.db"
    outside_snapshot.write_bytes(b"outside")

    with pytest.raises(approved.ApprovedExecutionError, match="snapshot-path-invalid"):
        _create_execution(tmp_path, snapshot_path=outside_snapshot)

    with pytest.raises(approved.ApprovedExecutionError, match="data-snapshot-hash-mismatch"):
        _create_execution(tmp_path, snapshot_sha256="0" * 64)


def test_snapshot_is_rechecked_immediately_before_training(tmp_path: Path) -> None:
    execution = _create_execution(tmp_path)
    execution.snapshot_path.write_bytes(b"changed after approval")

    with pytest.raises(approved.ApprovedExecutionError, match="data-snapshot-hash-mismatch"):
        execution.verify_snapshot()


def test_feature_contract_rejects_invalid_or_unavailable_columns(tmp_path: Path) -> None:
    with pytest.raises(approved.ApprovedExecutionError, match="removed-features-binding-invalid"):
        _create_execution(tmp_path, removed_features=("not_selected",))

    execution = _create_execution(tmp_path)
    with pytest.raises(approved.ApprovedExecutionError, match="approved-features-unavailable"):
        execution.select_feature_columns([], future_fields=set())
    with pytest.raises(approved.ApprovedExecutionError, match="future-field"):
        execution.select_feature_columns(["feature_a"], future_fields={"feature_a"})


@pytest.mark.parametrize(
    (
        "train_period_start",
        "train_period_end",
        "validation_period_start",
        "validation_period_end",
    ),
    [
        ("20250230", "20251231", "20260101", "20260331"),
        ("20250101", "20260101", "20260101", "20260331"),
    ],
)
def test_invalid_or_overlapping_period_contract_is_rejected(
    tmp_path: Path,
    train_period_start: str,
    train_period_end: str,
    validation_period_start: str,
    validation_period_end: str,
) -> None:
    with pytest.raises(approved.ApprovedExecutionError, match="training-period-invalid"):
        _create_execution(
            tmp_path,
            train_period_start=train_period_start,
            train_period_end=train_period_end,
            validation_period_start=validation_period_start,
            validation_period_end=validation_period_end,
        )


def test_workspace_must_be_an_isolated_system_temp_child() -> None:
    snapshot = Path(__file__).resolve()

    with pytest.raises(approved.ApprovedExecutionError, match="workspace-not-isolated"):
        approved.ApprovedTrainingExecution.create(
            job_id=str(uuid.uuid4()),
            approved_payload_hash="a" * 64,
            data_snapshot_sha256=_sha256(snapshot),
            feature_contract_sha256="b" * 64,
            candidate_commit_sha="c" * 40,
            target="win",
            model_type="lightgbm",
            train_period_start="20250101",
            train_period_end="20251231",
            validation_period_start="20260101",
            validation_period_end="20260331",
            selected_features=("feature_a",),
            removed_features=(),
            workspace=PYTHON_API,
            snapshot_path=snapshot,
        )


def test_snapshot_symlink_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "approved-job"
    workspace.mkdir()
    linked_snapshot = workspace / "snapshot.db"
    linked_snapshot.write_bytes(b"snapshot")
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == linked_snapshot or original_is_symlink(path),
    )

    with pytest.raises(approved.ApprovedExecutionError, match="symlink-forbidden"):
        _create_execution(tmp_path, snapshot_path=linked_snapshot)


def test_artifact_directory_symlink_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _create_execution(tmp_path)
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == execution.artifact_directory or original_is_symlink(path),
    )

    with pytest.raises(approved.ApprovedExecutionError, match="symlink-forbidden"):
        execution.prepare_artifact_directory()


def test_router_sanitizes_approved_contract_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    class InvalidExecution:
        def validate_request(self, **_kwargs: object) -> None:
            raise approved.ApprovedExecutionError("sensitive-internal-reason")

    monkeypatch.setattr(
        train,
        "_require_legacy_model_training_allowed",
        lambda: pytest.fail("approved execution must not use the legacy guard"),
    )
    request = models.TrainRequest(
        target="win",
        model_type="lightgbm",
        force_sync=False,
    )

    with pytest.raises(HTTPException) as captured:
        asyncio.run(
            train._do_train(
                request,
                {"user_id": "service-worker"},
                approved_execution=InvalidExecution(),
            )
        )

    assert captured.value.status_code == 409
    assert captured.value.detail == "approved training contract mismatch"


def test_router_source_gates_legacy_side_effects_for_approved_execution() -> None:
    source = (PYTHON_API / "routers" / "train.py").read_text(encoding="utf-8")

    assert source.count(
        "if approved_execution is None and SUPABASE_DATA_ENABLED and get_supabase_client():"
    ) == 2
    assert "if approved_execution is None and _catalog_path.exists():" in source
    assert "approved_execution.select_feature_columns(" in source
    assert "approved_execution.prepare_artifact_directory()" in source
    assert "df_train_source" in source
    assert "df_validation_source" in source
    assert "is_training=False" in source
    assert "optimizer=optimizer" in source
    assert "X.iloc[:approved_train_count]" in source
    assert 'approved_execution is None\n            and request.target not in ("speed_deviation", "rank")' in source
    assert 'bundle["approved_execution"]' in source
