from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PYTHON_API = ROOT / "python-api"
if str(PYTHON_API) not in sys.path:
    sys.path.insert(0, str(PYTHON_API))

from observation.contracts import ObservationContractError
from observation.reconcile import reconcile_result_snapshot


SCRIPT = ROOT / "scripts" / "phase3n_production_observation.py"
SPEC = importlib.util.spec_from_file_location("phase3n_production_observation", SCRIPT)
assert SPEC and SPEC.loader
ops = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ops
SPEC.loader.exec_module(ops)


def test_production_ops_refuse_staging_boundary(monkeypatch) -> None:
    values = {
        "PHASE3N_OBSERVATION_ENABLED": "true",
        "APP_ENV": "staging",
        "PHASE3N_STAGING_PROJECT_REF": "abcdefghijklmnopqrst",
        "SUPABASE_URL": "https://abcdefghijklmnopqrst.supabase.co",
        "PHASE3N_CANDIDATE_COMMIT_SHA": "a" * 40,
        "PHASE3N_EXPANDING_WINDOW_CHECKS_PASSED": "true",
        "AUTOMATED_BETTING_ENABLED": "false",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ObservationContractError, match="production-observation-boundary-required"):
        ops._production_gateway()


def test_production_reconcile_requires_durable_approval_reference() -> None:
    assert ops.APPROVAL_RE.fullmatch("short") is None
    assert (
        ops.APPROVAL_RE.fullmatch(
            "codex-user-instruction-2026-08-23-production-settlement"
        )
        is not None
    )


def test_production_cache_paths_cannot_escape_reports(tmp_path) -> None:
    with pytest.raises(ObservationContractError, match="output-must-be-under-reports"):
        ops._safe_report_path(tmp_path / "outside.json")


def test_snapshot_settlement_is_append_only_and_environment_scoped() -> None:
    class Gateway:
        def __init__(self) -> None:
            self.payloads = []

        def select(self, table, columns="*"):
            if table == "phase3n_result_observation_events":
                return []
            return [
                {
                    "observation_id": "obs-1",
                    "race_id": "202601020211",
                    "horse_id": "horse-1",
                    "horse_number": 1,
                    "qualifying_bet": True,
                    "wager_amount": 100,
                    "baseline_wager_amount": 100,
                    "source_environment": "production",
                },
                {
                    "observation_id": "obs-2",
                    "race_id": "202601020211",
                    "horse_id": "horse-2",
                    "horse_number": 2,
                    "qualifying_bet": False,
                    "wager_amount": 0,
                    "baseline_wager_amount": 0,
                    "source_environment": "production",
                },
                {
                    "observation_id": "staging-row",
                    "race_id": "202601020211",
                    "horse_id": "horse-3",
                    "horse_number": 3,
                    "qualifying_bet": False,
                    "wager_amount": 0,
                    "baseline_wager_amount": 0,
                    "source_environment": "staging",
                },
            ]

        def record_result(self, payload):
            self.payloads.append(payload)
            return {"mutation_code": "inserted"}

    gateway = Gateway()
    result = reconcile_result_snapshot(
        gateway,
        race_id="202601020211",
        horse_rows=[
            {"horse_number": 1, "finish_order": 1},
            {"horse_number": 2, "finish_order": 2},
        ],
        payout_rows=[{"bet_type": "tansho", "combination": "1", "payout": 250}],
        settled_at=datetime.now(timezone.utc),
        source_environment="production",
    )
    assert result == {"pending": 2, "inserted": 2, "duplicates": 0, "skipped": 0}
    assert len(gateway.payloads) == 2
    assert gateway.payloads[0]["return_amount"] == 250
    assert all(payload["observation_id"] != "staging-row" for payload in gateway.payloads)


def test_snapshot_settlement_fails_closed_on_incomplete_result() -> None:
    class Gateway:
        def select(self, table, columns="*"):
            if table == "phase3n_result_observation_events":
                return []
            return [
                {
                    "observation_id": "obs-1",
                    "race_id": "202601020211",
                    "horse_id": "horse-1",
                    "horse_number": 1,
                    "qualifying_bet": False,
                    "wager_amount": 0,
                    "baseline_wager_amount": 0,
                    "source_environment": "production",
                }
            ]

    with pytest.raises(ObservationContractError, match="settlement-result-incomplete"):
        reconcile_result_snapshot(
            Gateway(),
            race_id="202601020211",
            horse_rows=[{"horse_number": 1}],
            payout_rows=[],
            settled_at=datetime.now(timezone.utc),
            source_environment="production",
        )
