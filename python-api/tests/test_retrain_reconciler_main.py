from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


PYTHON_API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_API))
entrypoint = importlib.import_module("retrain_reconciler_main")


def _values() -> dict[str, str]:
    return {
        "APP_ENV": "staging",
        "MODEL_RETRAIN_ORPHAN_RECONCILIATION_ENABLED": "true",
        "MODEL_RETRAIN_RECONCILER_ID": "staging-reconciler-01",
        "MODEL_RETRAIN_ORPHAN_MIN_AGE_SECONDS": "3600",
        "MODEL_RETRAIN_RECONCILIATION_LIMIT": "20",
    }


def test_valid_reconciler_config_is_explicit_and_bounded() -> None:
    config = entrypoint.load_config(_values())
    assert config.reconciler_id == "staging-reconciler-01"
    assert config.min_age_seconds == 3600
    assert config.limit == 20


@pytest.mark.parametrize("environment", ["", "local", "development", "production"])
def test_reconciler_is_forbidden_outside_isolated_environments(environment: str) -> None:
    values = _values()
    values["APP_ENV"] = environment
    with pytest.raises(entrypoint.RetrainReconcilerConfigError, match="environment-forbidden"):
        entrypoint.load_config(values)


def test_reconciler_requires_separate_exact_enable() -> None:
    values = _values()
    values["MODEL_RETRAIN_ORPHAN_RECONCILIATION_ENABLED"] = "false"
    with pytest.raises(entrypoint.RetrainReconcilerConfigError, match="not-enabled"):
        entrypoint.load_config(values)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("MODEL_RETRAIN_ORPHAN_MIN_AGE_SECONDS", "3599"),
        ("MODEL_RETRAIN_ORPHAN_MIN_AGE_SECONDS", "604801"),
        ("MODEL_RETRAIN_RECONCILIATION_LIMIT", "0"),
        ("MODEL_RETRAIN_RECONCILIATION_LIMIT", "51"),
        ("MODEL_RETRAIN_RECONCILIATION_LIMIT", "many"),
    ],
)
def test_reconciler_bounds_fail_closed(key: str, value: str) -> None:
    values = _values()
    values[key] = value
    with pytest.raises(entrypoint.RetrainReconcilerConfigError, match="bounds-invalid"):
        entrypoint.load_config(values)
