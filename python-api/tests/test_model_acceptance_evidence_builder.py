from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_model_acceptance_evidence.py"
CONTRACT_PATH = ROOT / "config" / "model_acceptance_contract.v1.json"
STAKING_POLICY_PATH = (
    ROOT / "python-api" / "tests" / "fixtures" / "phase3n_staking_payout_policy_approved_v1.json"
)
COMMIT = "a" * 40
FIXED_NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)

SPEC = importlib.util.spec_from_file_location("model_acceptance_evidence_builder", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)
builder.STAKING_POLICY_PATH = STAKING_POLICY_PATH


def _contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _source() -> dict:
    staking_policy = json.loads(STAKING_POLICY_PATH.read_text(encoding="utf-8"))
    rows = []
    probabilities = [0.1, 0.9, 0.8, 0.2]
    labels = [0, 1, 0, 1]
    returns = [0.0, 20.0, 0.0, 30.0]
    baseline_returns = [0.0, 20.0, 10.0, 10.0]
    latencies = [10.0, 20.0, 30.0, 40.0]
    freshness = [5, 10, 15, 20]
    for index in range(4):
        prediction_at = FIXED_NOW - timedelta(days=4 - index, hours=1)
        rows.append(
            {
                "observation_id": f"race-{index}",
                "race_date": prediction_at.date().isoformat(),
                "prediction_at": prediction_at.isoformat(),
                "data_observed_at": (
                    prediction_at - timedelta(minutes=freshness[index])
                ).isoformat(),
                "settled_at": (prediction_at + timedelta(hours=1)).isoformat(),
                "y_true": labels[index],
                "predicted_probability": probabilities[index],
                "wager_amount": 10.0,
                "return_amount": returns[index],
                "baseline_wager_amount": 10.0,
                "baseline_return_amount": baseline_returns[index],
                "latency_ms": latencies[index],
            }
        )
    return {
        "schema": builder.SOURCE_SCHEMA,
        "schema_version": builder.SOURCE_SCHEMA_VERSION,
        "candidate_commit_sha": COMMIT,
        "model_id": "lightgbm-20260802",
        "model_artifact_sha256": "b" * 64,
        "generated_at": FIXED_NOW.isoformat(),
        "training_data_ended_at": (FIXED_NOW - timedelta(days=10)).isoformat(),
        "holdout_kind": "out_of_time",
        "model_feature_columns": ["horse_age", "jockey_win_rate"],
        "expanding_window_checks_passed": True,
        "initial_bankroll": 100.0,
        "staking_payout_policy_id": staking_policy["policy_id"],
        "staking_payout_policy_sha256": builder._canonical_sha256(staking_policy),
        "staking_payout_approval_reference": staking_policy["approval_reference"],
        "rows": rows,
    }


def _source_at(now: datetime) -> dict:
    source = _source()
    offset = now - FIXED_NOW
    source["generated_at"] = now.isoformat()
    source["training_data_ended_at"] = (
        datetime.fromisoformat(source["training_data_ended_at"]) + offset
    ).isoformat()
    for row in source["rows"]:
        for field in ("prediction_at", "data_observed_at", "settled_at"):
            row[field] = (datetime.fromisoformat(row[field]) + offset).isoformat()
        row["race_date"] = (
            datetime.fromisoformat(row["prediction_at"])
            .astimezone(builder.JRA_TIMEZONE)
            .date()
            .isoformat()
        )
    return source


def _build(source: dict | None = None) -> dict:
    return builder.build_evidence(
        source or _source(),
        _contract(),
        expected_commit=COMMIT,
        now=FIXED_NOW,
    )


def _failure(source: dict) -> tuple[str, ...]:
    with pytest.raises(builder.EvidenceBuildError) as captured:
        _build(source)
    return captured.value.failure_codes


def test_recomputes_every_metric_from_rows_and_binds_contract() -> None:
    evidence = _build()

    assert evidence["candidate_commit_sha"] == COMMIT
    assert evidence["contract_id"] == _contract()["contract_id"]
    assert evidence["contract_sha256"] == builder.acceptance_gate.contract_sha256(_contract())
    assert evidence["model_artifact_sha256"] == "b" * 64
    assert evidence["model_feature_columns_sha256"] == builder._canonical_sha256(
        _source()["model_feature_columns"]
    )
    assert evidence["observations_sha256"] == builder._canonical_sha256(_source())
    assert evidence["evaluation"] == {
        "holdout_kind": "out_of_time",
        "future_field_leakage_detected": False,
        "started_at": "2026-07-29T11:00:00Z",
        "ended_at": "2026-08-01T12:00:00Z",
    }
    assert evidence["metrics"] == {
        "auc": 0.75,
        "brier_score": 0.325,
        "expected_calibration_error": 0.45,
        "roi_percent": 25.0,
        "max_drawdown_percent": 10.0,
        "bet_count": 4,
        "sample_count": 4,
        "p95_latency_ms": 40.0,
        "data_freshness_minutes": 20.0,
        "observation_period_days": 4,
        "baseline_roi_delta_percent": 25.0,
    }


def test_tied_auc_probabilities_use_average_ranks() -> None:
    source = _source()
    for row in source["rows"]:
        row["predicted_probability"] = 0.5
    assert _build(source)["metrics"]["auc"] == 0.5


def test_drawdown_groups_simultaneous_settlements_without_row_order_bias() -> None:
    source = _source()
    common_time = source["rows"][1]["settled_at"]
    source["rows"][0]["settled_at"] = common_time
    source["rows"][1]["return_amount"] = 30.0

    first = _build(source)["metrics"]["max_drawdown_percent"]
    source["rows"][0], source["rows"][1] = source["rows"][1], source["rows"][0]
    second = _build(source)["metrics"]["max_drawdown_percent"]

    assert first == second


@pytest.mark.parametrize(
    ("mutate", "failure"),
    [
        (lambda value: value.update(extra=True), "observations-schema-invalid"),
        (
            lambda value: value.update(candidate_commit_sha="b" * 40),
            "observations-candidate-commit-mismatch",
        ),
        (
            lambda value: value.update(model_artifact_sha256="bad"),
            "observations-model-artifact-digest-invalid",
        ),
        (lambda value: value.update(holdout_kind="random"), "observations-holdout-not-out-of-time"),
        (
            lambda value: value.update(expanding_window_checks_passed=False),
            "observations-expanding-window-check-required",
        ),
        (
            lambda value: value["model_feature_columns"].append("finish_position"),
            "observations-future-field-leakage-detected",
        ),
        (
            lambda value: value["model_feature_columns"].append("horse_age"),
            "observations-model-feature-columns-duplicate",
        ),
        (
            lambda value: value["rows"][1].update(
                observation_id=value["rows"][0]["observation_id"]
            ),
            "observation-id-duplicate",
        ),
        (
            lambda value: [row.update(y_true=1) for row in value["rows"]],
            "observations-auc-class-balance-invalid",
        ),
        (
            lambda value: [row.update(wager_amount=0.0, return_amount=0.0) for row in value["rows"]],
            "observations-candidate-wagers-required",
        ),
        (
            lambda value: [
                row.update(baseline_wager_amount=0.0, baseline_return_amount=0.0)
                for row in value["rows"]
            ],
            "observations-baseline-wagers-required",
        ),
        (lambda value: value["rows"][0].update(latency_ms=True), "observation-numeric-value-invalid"),
        (
            lambda value: value["rows"][0].update(
                data_observed_at=(FIXED_NOW + timedelta(days=1)).isoformat()
            ),
            "observation-data-after-prediction",
        ),
        (
            lambda value: value["rows"][0].update(race_date="2020-01-01"),
            "observation-race-date-prediction-mismatch",
        ),
        (
            lambda value: value["rows"][0].update(
                prediction_at=(FIXED_NOW - timedelta(days=11)).isoformat()
            ),
            "observation-not-after-training-cutoff",
        ),
        (
            lambda value: value["rows"][0].update(
                settled_at=(FIXED_NOW - timedelta(days=5)).isoformat()
            ),
            "observation-settled-before-prediction",
        ),
        (
            lambda value: value["rows"][0].update(
                settled_at=(FIXED_NOW + timedelta(seconds=1)).isoformat()
            ),
            "observation-settled-after-generation",
        ),
        (
            lambda value: value["rows"][0].update(wager_amount=0.0, return_amount=1.0),
            "observation-return-without-wager",
        ),
        (
            lambda value: value.update(generated_at=(FIXED_NOW + timedelta(minutes=6)).isoformat()),
            "observations-generated-in-future",
        ),
    ],
)
def test_malformed_temporal_leakage_and_financial_inputs_fail_closed(
    mutate: object,
    failure: str,
) -> None:
    source = _source()
    mutate(source)  # type: ignore[operator]
    assert failure in _failure(source)


def test_contract_must_be_structurally_valid_but_may_remain_draft() -> None:
    contract = _contract()
    contract["thresholds"]["auc"]["operator"] = "lte"
    with pytest.raises(builder.EvidenceBuildError) as captured:
        builder.build_evidence(_source(), contract, expected_commit=COMMIT, now=FIXED_NOW)
    assert "contract-invalid" in captured.value.failure_codes


def test_duplicate_json_keys_nonfinite_values_and_size_limit_fail_closed(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema":"one","schema":"two"}', encoding="utf-8")
    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"value":NaN}', encoding="utf-8")
    too_large = tmp_path / "large.json"
    too_large.write_text("{}", encoding="utf-8")

    with pytest.raises(builder.EvidenceBuildError) as duplicate_error:
        builder.load_json(duplicate, prefix="observations")
    with pytest.raises(builder.EvidenceBuildError) as nonfinite_error:
        builder.load_json(nonfinite, prefix="observations")
    with pytest.raises(builder.EvidenceBuildError) as size_error:
        builder.load_json(too_large, prefix="observations", max_bytes=1)

    assert duplicate_error.value.failure_codes == ("observations-duplicate-json-key",)
    assert nonfinite_error.value.failure_codes == ("observations-invalid-json",)
    assert size_error.value.failure_codes == ("observations-file-too-large",)


def test_cli_writes_atomic_evidence_consumable_by_existing_gate(tmp_path: Path) -> None:
    input_path = tmp_path / "observations.json"
    output_path = tmp_path / "evidence.json"
    current_now = datetime.now(timezone.utc)
    input_path.write_text(json.dumps(_source_at(current_now)), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--input",
            str(input_path),
            "--contract",
            str(CONTRACT_PATH),
            "--expected-commit",
            COMMIT,
            "--output",
            str(output_path),
            "--staking-policy",
            str(STAKING_POLICY_PATH),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    evidence = json.loads(output_path.read_text(encoding="utf-8"))
    report = builder.acceptance_gate.build_report(
        _contract(),
        evidence,
        expected_commit=COMMIT,
        now=current_now,
    )
    assert report["success"] is True
    assert report["verdict"] == "not-accepted"
    assert report["checks"]["evidence_schema_and_binding"] is True


def test_failed_cli_does_not_overwrite_existing_output(tmp_path: Path) -> None:
    source = _source()
    source["model_feature_columns"].append("actual_finish")
    input_path = tmp_path / "observations.json"
    output_path = tmp_path / "evidence.json"
    input_path.write_text(json.dumps(source), encoding="utf-8")
    output_path.write_text("keep", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--input",
            str(input_path),
            "--contract",
            str(CONTRACT_PATH),
            "--expected-commit",
            COMMIT,
            "--output",
            str(output_path),
            "--staking-policy",
            str(STAKING_POLICY_PATH),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert output_path.read_text(encoding="utf-8") == "keep"
    assert "observations-future-field-leakage-detected" in result.stdout


def test_builder_does_not_mutate_source() -> None:
    source = _source()
    before = copy.deepcopy(source)
    _build(source)
    assert source == before
