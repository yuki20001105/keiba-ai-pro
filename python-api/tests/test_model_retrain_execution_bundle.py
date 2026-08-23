from __future__ import annotations

import hashlib
import importlib
import sys
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


PYTHON_API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_API))
approved = importlib.import_module("training.approved_execution")
bundle_module = importlib.import_module("training.execution_bundle")

NOW = datetime(2026, 8, 2, 8, 0, tzinfo=timezone.utc)
JOB_ID = str(uuid.uuid4())
WORKER_ID = "staging-worker-01"
FENCE = 42
COMMIT = "c" * 40
ACTIVE_MODEL = "baseline-model"


def _bundle() -> dict:
    return {
        "schema_version": 1,
        "job_id": JOB_ID,
        "approval_id": str(uuid.uuid4()),
        "dry_run_id": str(uuid.uuid4()),
        "approved_payload_hash": "a" * 64,
        "worker_id": WORKER_ID,
        "fencing_token": FENCE,
        "record_version": 3,
        "lease_expires_at": (NOW + timedelta(seconds=120)).isoformat(),
        "execution_policy": "staging-train",
        "target": "win",
        "model_type": "lightgbm",
        "train_period": {"start": "20250101", "end": "20251231"},
        "validation_period": {"start": "20260101", "end": "20260331"},
        "selected_features": ["feature_a", "feature_b"],
        "removed_features": ["feature_b"],
        "data_snapshot_sha256": "b" * 64,
        "feature_contract_sha256": "d" * 64,
        "candidate_commit_sha": COMMIT,
        "active_model_id": ACTIVE_MODEL,
        "training_parameters": {
            "force_sync": False,
            "test_size": 0.2,
            "cv_folds": 5,
            "use_optuna": False,
        },
    }


def _parse(value: dict) -> bundle_module.ApprovedExecutionBundle:
    return bundle_module.ApprovedExecutionBundle.from_rpc(
        value,
        expected_job_id=JOB_ID,
        expected_worker_id=WORKER_ID,
        expected_fencing_token=FENCE,
        expected_candidate_commit_sha=COMMIT,
        expected_active_model_id=ACTIVE_MODEL,
        now=NOW,
    )


def test_valid_bundle_projects_to_verified_isolated_execution(tmp_path: Path) -> None:
    value = _bundle()
    workspace = tmp_path / "approved-job"
    workspace.mkdir()
    snapshot = workspace / "snapshot.db"
    snapshot.write_bytes(b"approved snapshot")
    value["data_snapshot_sha256"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()

    parsed = _parse(value)
    execution = parsed.create_execution(workspace=workspace, snapshot_path=snapshot)

    assert parsed.record_version == 3
    assert parsed.fencing_token == FENCE
    assert execution.effective_features == ("feature_a",)
    assert execution.snapshot_path == snapshot.resolve()


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("job_id", str(uuid.uuid4()), "job-binding-mismatch"),
        ("worker_id", "other-worker", "worker-binding-mismatch"),
        ("fencing_token", FENCE + 1, "fence-binding-mismatch"),
        ("candidate_commit_sha", "e" * 40, "commit-binding-mismatch"),
        ("active_model_id", "other-model", "active-model-binding-mismatch"),
        ("execution_policy", "production-train", "policy-invalid"),
        ("target", "place", "training-shape-invalid"),
    ],
)
def test_critical_binding_mismatches_fail_closed(field: str, value: object, error: str) -> None:
    payload = _bundle()
    payload[field] = value

    with pytest.raises(approved.ApprovedExecutionError, match=error):
        _parse(payload)


def test_expired_or_unbounded_lease_fails_closed() -> None:
    for expiry in (NOW - timedelta(seconds=1), NOW + timedelta(seconds=306)):
        payload = _bundle()
        payload["lease_expires_at"] = expiry.isoformat()
        with pytest.raises(approved.ApprovedExecutionError, match="lease-invalid"):
            _parse(payload)


def test_extra_key_and_parameter_drift_fail_closed() -> None:
    extra = _bundle()
    extra["snapshot_path"] = "C:/unsafe.db"
    with pytest.raises(approved.ApprovedExecutionError, match="schema-invalid"):
        _parse(extra)

    changed = deepcopy(_bundle())
    changed["training_parameters"]["use_optuna"] = True
    with pytest.raises(approved.ApprovedExecutionError, match="training-parameters-invalid"):
        _parse(changed)


def test_invalid_feature_or_period_contract_is_rejected_when_materialized(tmp_path: Path) -> None:
    payload = _bundle()
    payload["removed_features"] = ["not-selected"]
    workspace = tmp_path / "approved-job"
    workspace.mkdir()
    snapshot = workspace / "snapshot.db"
    snapshot.write_bytes(b"snapshot")
    payload["data_snapshot_sha256"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    parsed = _parse(payload)

    with pytest.raises(approved.ApprovedExecutionError, match="removed-features-binding-invalid"):
        parsed.create_execution(workspace=workspace, snapshot_path=snapshot)
