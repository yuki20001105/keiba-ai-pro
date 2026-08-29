from __future__ import annotations

import importlib
import sys
import uuid
from pathlib import Path

import pytest


PYTHON_API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_API))
entrypoint = importlib.import_module("retrain_worker_main")


def _values(tmp_path: Path) -> dict[str, str]:
    return {
        "APP_ENV": "staging",
        "MODEL_RETRAIN_EXECUTION_ENABLED": "true",
        "MODEL_RETRAIN_WORKER_ID": "staging-worker-01",
        "MODEL_RETRAIN_JOB_ID": str(uuid.uuid4()),
        "MODEL_RETRAIN_JOB_VERSION": "1",
        "APP_COMMIT_SHA": "a" * 40,
        "MODEL_RETRAIN_SNAPSHOT_PATH": str((tmp_path / "snapshot.db").resolve()),
        "MODEL_RETRAIN_LEASE_TTL_SECONDS": "120",
    }


def test_valid_staging_config_is_exact_and_policy_bound(tmp_path: Path) -> None:
    config = entrypoint.load_config(_values(tmp_path))

    assert config.execution_policy == "staging-train"
    assert config.lease_ttl_seconds == 120
    assert config.snapshot_source.is_absolute()


@pytest.mark.parametrize("environment", ["", "local", "development", "production", "prod"])
def test_non_isolated_environment_cannot_enable_worker(
    tmp_path: Path,
    environment: str,
) -> None:
    values = _values(tmp_path)
    values["APP_ENV"] = environment

    with pytest.raises(entrypoint.RetrainWorkerConfigError, match="environment-forbidden"):
        entrypoint.load_config(values)


def test_worker_requires_explicit_enable_and_exact_commit(tmp_path: Path) -> None:
    disabled = _values(tmp_path)
    disabled["MODEL_RETRAIN_EXECUTION_ENABLED"] = "false"
    with pytest.raises(entrypoint.RetrainWorkerConfigError, match="not-enabled"):
        entrypoint.load_config(disabled)

    invalid_commit = _values(tmp_path)
    invalid_commit["APP_COMMIT_SHA"] = "0" * 39
    with pytest.raises(entrypoint.RetrainWorkerConfigError, match="commit-invalid"):
        entrypoint.load_config(invalid_commit)


def test_snapshot_path_must_be_absolute_and_ttl_is_bounded(tmp_path: Path) -> None:
    relative = _values(tmp_path)
    relative["MODEL_RETRAIN_SNAPSHOT_PATH"] = "snapshot.db"
    with pytest.raises(entrypoint.RetrainWorkerConfigError, match="snapshot-path-invalid"):
        entrypoint.load_config(relative)

    excessive_ttl = _values(tmp_path)
    excessive_ttl["MODEL_RETRAIN_LEASE_TTL_SECONDS"] = "301"
    with pytest.raises(entrypoint.RetrainWorkerConfigError, match="ttl-invalid"):
        entrypoint.load_config(excessive_ttl)


def test_sandbox_environment_maps_only_to_sandbox_policy(tmp_path: Path) -> None:
    values = _values(tmp_path)
    values["APP_ENV"] = "sandbox"

    assert entrypoint.load_config(values).execution_policy == "sandbox-train"
