from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


PYTHON_API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_API))
entrypoint = importlib.import_module("retrain_dispatcher_main")


def _values(tmp_path: Path) -> dict[str, str]:
    return {
        "APP_ENV": "staging",
        "MODEL_RETRAIN_DISPATCH_ENABLED": "true",
        "MODEL_RETRAIN_DISPATCHER_ID": "staging-dispatcher-01",
        "APP_COMMIT_SHA": "a" * 40,
        "MODEL_RETRAIN_SNAPSHOT_DIRECTORY": str(tmp_path.resolve()),
        "MODEL_RETRAIN_DISPATCH_LIMIT": "2",
        "MODEL_RETRAIN_LEASE_TTL_SECONDS": "120",
    }


def test_valid_dispatcher_config_is_environment_bound(tmp_path: Path) -> None:
    config = entrypoint.load_config(_values(tmp_path))
    assert config.execution_policy == "staging-train"
    assert config.limit == 2
    assert config.snapshot_directory.is_absolute()


def test_sandbox_maps_only_to_sandbox_policy(tmp_path: Path) -> None:
    values = _values(tmp_path)
    values["APP_ENV"] = "sandbox"
    assert entrypoint.load_config(values).execution_policy == "sandbox-train"


@pytest.mark.parametrize("environment", ["", "local", "development", "production"])
def test_dispatcher_is_forbidden_outside_isolated_environments(
    tmp_path: Path,
    environment: str,
) -> None:
    values = _values(tmp_path)
    values["APP_ENV"] = environment
    with pytest.raises(entrypoint.RetrainDispatcherConfigError, match="environment-forbidden"):
        entrypoint.load_config(values)


def test_dispatcher_requires_separate_enable(tmp_path: Path) -> None:
    values = _values(tmp_path)
    values["MODEL_RETRAIN_DISPATCH_ENABLED"] = "false"
    with pytest.raises(entrypoint.RetrainDispatcherConfigError, match="not-enabled"):
        entrypoint.load_config(values)


@pytest.mark.parametrize(
    ("key", "value", "code"),
    [
        ("APP_COMMIT_SHA", "a" * 39, "commit-invalid"),
        ("APP_COMMIT_SHA", "0" * 40, "commit-invalid"),
        ("MODEL_RETRAIN_SNAPSHOT_DIRECTORY", "relative", "snapshot-directory-invalid"),
        ("MODEL_RETRAIN_DISPATCH_LIMIT", "0", "bounds-invalid"),
        ("MODEL_RETRAIN_DISPATCH_LIMIT", "6", "bounds-invalid"),
        ("MODEL_RETRAIN_LEASE_TTL_SECONDS", "301", "bounds-invalid"),
    ],
)
def test_dispatcher_config_bounds_fail_closed(
    tmp_path: Path,
    key: str,
    value: str,
    code: str,
) -> None:
    values = _values(tmp_path)
    values[key] = value
    with pytest.raises(entrypoint.RetrainDispatcherConfigError, match=code):
        entrypoint.load_config(values)
