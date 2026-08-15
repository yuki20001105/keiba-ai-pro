from __future__ import annotations

import sys
from pathlib import Path

import pytest


PYTHON_API = Path(__file__).resolve().parents[1]
if str(PYTHON_API) not in sys.path:
    sys.path.insert(0, str(PYTHON_API))

from betting.ipat import IPATVoter  # noqa: E402


def test_real_betting_is_denied_without_explicit_active_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOMATED_BETTING_ENABLED", "false")
    monkeypatch.setenv("MODEL_RUNTIME_STATUS", "observation")
    with pytest.raises(RuntimeError, match="automated-betting-explicit-opt-in-required"):
        IPATVoter(dry_run=False)

    monkeypatch.setenv("AUTOMATED_BETTING_ENABLED", "true")
    with pytest.raises(RuntimeError, match="automated-betting-active-model-required"):
        IPATVoter(dry_run=False)


def test_dry_run_remains_available_in_observation_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOMATED_BETTING_ENABLED", "false")
    monkeypatch.setenv("MODEL_RUNTIME_STATUS", "observation")
    assert IPATVoter(dry_run=True).dry_run is True
