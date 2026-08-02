from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PYTHON_API = ROOT / "python-api"
if str(PYTHON_API) not in sys.path:
    sys.path.insert(0, str(PYTHON_API))

from observation.cache_integrity import verify_cache, write_cache_atomic  # noqa: E402
from observation.contracts import ObservationContractError  # noqa: E402
from observation.export import build_model_acceptance_source  # noqa: E402
from observation.progress import build_progress_report, render_progress_markdown  # noqa: E402
from observation.service import (  # noqa: E402
    ObservationConfig,
    build_prediction_payload,
    build_result_payload,
)


COMMIT = "a" * 40
PROJECT_REF = "btegligclxkwzefikbzm"


def test_observation_config_is_explicit_staging_only_and_project_bound() -> None:
    assert ObservationConfig.from_env({}).enabled is False
    valid = {
        "PHASE3N_OBSERVATION_ENABLED": "true",
        "APP_ENV": "staging",
        "PHASE3N_STAGING_PROJECT_REF": PROJECT_REF,
        "SUPABASE_URL": f"https://{PROJECT_REF}.supabase.co",
        "PHASE3N_CANDIDATE_COMMIT_SHA": COMMIT,
        "PHASE3N_EXPANDING_WINDOW_CHECKS_PASSED": "true",
    }
    config = ObservationConfig.from_env(valid)
    config.require_staging_boundary()
    for key, replacement in (
        ("APP_ENV", "production"),
        ("SUPABASE_URL", "https://example.supabase.co"),
        ("PHASE3N_CANDIDATE_COMMIT_SHA", "not-a-commit"),
    ):
        changed = {**valid, key: replacement}
        with pytest.raises(ObservationContractError):
            ObservationConfig.from_env(changed)
    with pytest.raises(ObservationContractError, match="expanding-window-check-required"):
        ObservationConfig.from_env(
            {**valid, "PHASE3N_EXPANDING_WINDOW_CHECKS_PASSED": "false"}
        )


def test_prediction_and_result_payloads_are_digest_bound_and_temporally_strict() -> None:
    observed = datetime.now(timezone.utc) - timedelta(minutes=2)
    prediction = build_prediction_payload(
        manifest_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        race_id="202608020101",
        horse_id="horse-01",
        horse_number=1,
        race_date="2026-08-02",
        data_observed_at=observed,
        data_cutoff_at=observed + timedelta(seconds=1),
        feature_columns=["horse_number", "odds"],
        feature_values=[1, 2.5],
        predicted_value=0.4,
        predicted_probability=0.4,
        predicted_rank=1,
        odds_at_prediction=2.5,
        latency_ms=25.0,
    )
    assert prediction["idempotency_key"].startswith("prediction:")
    assert len(prediction["payload_sha256"]) == 64
    assert prediction["qualifying_bet"] is False
    result = build_result_payload(
        observation_id=prediction["observation_id"],
        settled_at=datetime.now(timezone.utc),
        y_true=1,
        finish_order=1,
        bet_outcome="not-bet",
        return_amount=0,
        baseline_return_amount=0,
    )
    assert result["idempotency_key"].startswith("result:")
    with pytest.raises(ObservationContractError, match="prediction-source-temporal-order-invalid"):
        build_prediction_payload(
            manifest_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            race_id="202608020101",
            horse_id="horse-01",
            horse_number=1,
            race_date="2026-08-02",
            data_observed_at=observed,
            data_cutoff_at=observed - timedelta(seconds=1),
            feature_columns=["odds"],
            feature_values=[2.5],
            predicted_value=0.4,
            predicted_probability=0.4,
            predicted_rank=1,
            odds_at_prediction=2.5,
        )


def _progress_rows(days: int = 1) -> tuple[list[dict], list[dict]]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    predictions = []
    results = []
    for index in range(days):
        predicted = start + timedelta(days=index, hours=1)
        observation_id = f"observation-{index}"
        predictions.append(
            {
                "observation_id": observation_id,
                "race_id": f"race-{index}",
                "horse_id": f"horse-{index}",
                "race_date": predicted.date().isoformat(),
                "prediction_at": predicted.isoformat(),
                "data_observed_at": (predicted - timedelta(minutes=5)).isoformat(),
                "data_cutoff_at": (predicted - timedelta(minutes=1)).isoformat(),
                "qualifying_bet": index < 100,
                "wager_amount": 100 if index < 100 else 0,
                "leakage_violation_count": 0,
                "model_version": "v1",
                "candidate_commit_sha": COMMIT,
            }
        )
        results.append(
            {
                "observation_id": observation_id,
                "settled_at": (predicted + timedelta(hours=1)).isoformat(),
            }
        )
    return predictions, results


def test_progress_report_is_honest_and_production_not_ready_until_all_thresholds() -> None:
    predictions, results = _progress_rows(90)
    report = build_progress_report(predictions, results, [])
    assert report["elapsed_days"] == 90
    assert report["observation_period_90_days_met"] is True
    assert report["valid_samples_1000_met"] is False
    assert report["qualifying_bets_100_met"] is False
    assert report["readiness_verdict"] == "PRODUCTION_NOT_READY"
    assert "PRODUCTION_NOT_READY" in render_progress_markdown(report)
    with pytest.raises(ObservationContractError, match="observation-readiness-not-met"):
        build_model_acceptance_source(predictions, results, report, initial_bankroll=100_000)


def test_safe_idempotent_replays_are_reported_but_do_not_fabricate_duplicate_rows() -> None:
    predictions, results = _progress_rows(1)
    report = build_progress_report(
        predictions,
        results,
        [{"outcome": "duplicate"}, {"outcome": "duplicate"}],
    )
    assert report["duplicate_count"] == 0
    assert report["idempotent_replay_count"] == 2
    assert report["payload_conflict_count"] == 0


def test_cache_is_rebuildable_with_identical_digest_and_no_missing_duplicate_or_stale_rows(
    tmp_path: Path,
) -> None:
    rows = [
        {"observation_id": "b", "race_id": "r2", "horse_id": "h2"},
        {"observation_id": "a", "race_id": "r1", "horse_id": "h1"},
    ]
    cache = tmp_path / "cache.json"
    before = write_cache_atomic(cache, rows)
    cache.unlink()
    after = write_cache_atomic(cache, list(reversed(rows)))
    verification = verify_cache(cache, rows)
    assert before == after
    assert verification == {
        **verification,
        "digest_match": True,
        "missing_count": 0,
        "duplicate_count": 0,
        "stale_count": 0,
        "success": True,
    }


def test_migration_and_compose_contract_are_append_only_least_privilege_and_diskless() -> None:
    manifest = json.loads((ROOT / "supabase/bootstrap/v1/manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["migrations"]) == 21
    assert [entry["version"] for entry in manifest["migrations"][-2:]] == [
        "20260802148000",
        "20260802149000",
    ]
    sql = (ROOT / "supabase/migrations/20260802_phase3n_observation_ha.sql").read_text(
        encoding="utf-8"
    )
    assert "_phase3n_reject_immutable_mutation" in sql
    assert "clock_timestamp()" in sql
    assert "stale-fence-rejected" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "GRANT EXECUTE" in sql
    assert "TO service_role" in sql
    assert "TO anon" not in "\n".join(
        line for line in sql.splitlines() if "GRANT EXECUTE" in line
    )
    compose = (ROOT / "docker-compose.phase3n-ha.yml").read_text(encoding="utf-8")
    for migration in manifest["migrations"]:
        assert migration["path"].removeprefix("supabase/") in compose
    assert "Persistent Disk" not in compose
    assert "ports:" not in compose
    assert "tmpfs:" in compose
    harness = (ROOT / "scripts/run_phase3n_ha_compose.py").read_text(encoding="utf-8")
    for required in ('"KILL"', '"TERM"', '"disconnect"', '"connect"', "database_unchanged"):
        assert required in harness


def test_b64_names_are_not_written_by_observation_implementation() -> None:
    paths = [
        ROOT / "scripts/phase3n_observation.py",
        ROOT / "python-api/observation/export.py",
        ROOT / "scripts/run_phase3n_ha_compose.py",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert "PHASE3N_STAGING_OBSERVATION_B64" not in source
    assert "MODEL_EVALUATION_OBSERVATIONS_GZIP_B64" not in source
