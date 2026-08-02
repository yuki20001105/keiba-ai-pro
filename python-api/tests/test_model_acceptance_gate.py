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
SCRIPT = ROOT / "scripts" / "verify_model_acceptance.py"
CONTRACT_PATH = ROOT / "config" / "model_acceptance_contract.v1.json"
FIXED_NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
COMMIT = "a" * 40

SPEC = importlib.util.spec_from_file_location("model_acceptance_gate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def _contract(*, approved: bool = False) -> dict:
    value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if approved:
        value.update(
            {
                "status": "approved",
                "approved_at": "2026-08-02T00:00:00+00:00",
                "approved_by": "business-owner",
                "approval_reference": "docs/approvals/model-v1",
            }
        )
        thresholds = {
            "auc": 0.85,
            "brier_score": 0.20,
            "expected_calibration_error": 0.05,
            "roi_percent": 3.0,
            "max_drawdown_percent": 20.0,
            "bet_count": 100,
            "sample_count": 1000,
            "p95_latency_ms": 500.0,
            "data_freshness_minutes": 30.0,
            "observation_period_days": 90.0,
            "baseline_roi_delta_percent": 1.0,
        }
        for metric, threshold in thresholds.items():
            value["thresholds"][metric]["value"] = threshold
    return value


def _evidence(contract: dict) -> dict:
    return {
        "schema": gate.EVIDENCE_SCHEMA,
        "schema_version": gate.SCHEMA_VERSION,
        "candidate_commit_sha": COMMIT,
        "model_id": "lightgbm-20260802",
        "model_artifact_sha256": "b" * 64,
        "model_feature_columns_sha256": "c" * 64,
        "observations_sha256": "d" * 64,
        "contract_id": contract["contract_id"],
        "contract_sha256": gate.contract_sha256(contract),
        "observed_at": FIXED_NOW.isoformat(),
        "evaluation": {
            "holdout_kind": "out_of_time",
            "future_field_leakage_detected": False,
            "started_at": (FIXED_NOW - timedelta(days=100)).isoformat(),
            "ended_at": (FIXED_NOW - timedelta(minutes=1)).isoformat(),
        },
        "metrics": {
            "auc": 0.90,
            "brier_score": 0.15,
            "expected_calibration_error": 0.03,
            "roi_percent": 5.0,
            "max_drawdown_percent": 10.0,
            "bet_count": 150,
            "sample_count": 1500,
            "p95_latency_ms": 300.0,
            "data_freshness_minutes": 10.0,
            "observation_period_days": 100.0,
            "baseline_roi_delta_percent": 2.0,
        },
    }


def _report(contract: dict, evidence: dict, **kwargs: object) -> dict:
    return gate.build_report(
        contract,
        evidence,
        expected_commit=kwargs.pop("expected_commit", COMMIT),
        max_age_seconds=kwargs.pop("max_age_seconds", 7 * 24 * 60 * 60),
        now=kwargs.pop("now", FIXED_NOW),
        **kwargs,
    )


def test_repository_contract_is_valid_but_deliberately_not_approved() -> None:
    contract = _contract()
    report = _report(contract, _evidence(contract))

    assert report["success"] is True
    assert report["verdict"] == "not-accepted"
    assert report["accepted"] is False
    assert report["checks"]["contract_schema"] is True
    assert report["checks"]["contract_approved"] is False
    assert report["blockers"][0] == "threshold-brier-score-unapproved"
    assert "contract-approval-required" in report["blockers"]
    assert report["failure_codes"] == []


def test_promotion_mode_rejects_draft_contract() -> None:
    contract = _contract()
    report = _report(contract, _evidence(contract), require_accepted=True)

    assert report["success"] is False
    assert report["accepted"] is False
    assert report["verdict"] == "not-accepted"
    assert report["failure_codes"] == ["model-acceptance-required"]
    assert report["checks"]["promotion_policy"] is False


def test_approved_contract_and_passing_evidence_are_accepted() -> None:
    contract = _contract(approved=True)
    report = _report(contract, _evidence(contract), require_accepted=True)

    assert report["success"] is True
    assert report["verdict"] == "accepted"
    assert report["accepted"] is True
    assert report["blockers"] == []
    assert report["failure_codes"] == []
    assert all(report["checks"].values())


@pytest.mark.parametrize("metric", sorted(gate.THRESHOLD_KEYS))
def test_each_metric_threshold_is_release_blocking(metric: str) -> None:
    contract = _contract(approved=True)
    evidence = _evidence(contract)
    operator = gate.THRESHOLD_KEYS[metric]
    threshold = contract["thresholds"][metric]["value"]
    evidence["metrics"][metric] = threshold - 1 if operator == "gte" else threshold + 1

    report = _report(contract, evidence)

    assert report["accepted"] is False
    assert f"metric-{metric.replace('_', '-')}-below-contract" in report["blockers"]


@pytest.mark.parametrize("metric", sorted(gate.THRESHOLD_KEYS))
def test_unapproved_threshold_cannot_hide_inside_approved_contract(metric: str) -> None:
    contract = _contract(approved=True)
    contract["thresholds"][metric]["value"] = None
    evidence = _evidence(contract)

    report = _report(contract, evidence)

    assert report["success"] is False
    assert report["accepted"] is False
    assert "approved-contract-has-unapproved-thresholds" in report["failure_codes"]


@pytest.mark.parametrize(
    ("mutation", "failure"),
    [
        (lambda value: value.update(candidate_commit_sha="b" * 40), "evidence-candidate-commit-mismatch"),
        (lambda value: value.update(contract_sha256="0" * 64), "evidence-contract-digest-mismatch"),
        (
            lambda value: value.update(model_artifact_sha256="not-a-digest"),
            "evidence-model-artifact-sha256-invalid",
        ),
        (lambda value: value["evaluation"].update(holdout_kind="random"), "evidence-holdout-not-out-of-time"),
        (lambda value: value["evaluation"].update(future_field_leakage_detected=True), "evidence-leakage-check-failed"),
        (lambda value: value["metrics"].update(auc=True), "evidence-auc-invalid"),
    ],
)
def test_binding_leakage_and_numeric_failures_fail_closed(mutation: object, failure: str) -> None:
    contract = _contract(approved=True)
    evidence = _evidence(contract)
    mutation(evidence)  # type: ignore[operator]

    report = _report(contract, evidence)

    assert report["success"] is False
    assert report["accepted"] is False
    assert failure in report["failure_codes"]


def test_stale_evidence_fails_closed() -> None:
    contract = _contract(approved=True)
    evidence = _evidence(contract)
    evidence["observed_at"] = (FIXED_NOW - timedelta(days=8)).isoformat()

    assert "evidence-stale" in _report(contract, evidence)["failure_codes"]


def test_unknown_fields_and_wrong_operator_fail_closed() -> None:
    contract = _contract(approved=True)
    contract["operator_note"] = "looks good"
    evidence = _evidence(contract)
    assert "contract-schema-invalid" in _report(contract, evidence)["failure_codes"]

    contract = _contract(approved=True)
    contract["thresholds"]["auc"]["operator"] = "lte"
    evidence = _evidence(contract)
    assert "contract-auc-operator-invalid" in _report(contract, evidence)["failure_codes"]


def test_report_projection_does_not_copy_raw_metrics_or_unknown_data() -> None:
    contract = _contract(approved=True)
    evidence = _evidence(contract)
    report = _report(contract, evidence)

    serialized = json.dumps(report, sort_keys=True)
    assert "metrics" not in report["evidence"]
    assert "roi_percent" not in serialized
    assert "lightgbm-20260802" in serialized
    assert report["evidence"]["model_artifact_sha256"] == "b" * 64

    contract["contract_id"] = "secret value that must not be projected"
    evidence["model_id"] = "secret value that must not be projected"
    evidence["observed_at"] = "secret value that must not be projected"
    report = _report(contract, evidence)
    serialized = json.dumps(report, sort_keys=True)
    assert "secret value" not in serialized


def test_trusted_report_validator_binds_accepted_report_to_commit_and_freshness() -> None:
    contract = _contract(approved=True)
    report = _report(contract, _evidence(contract), require_accepted=True)

    valid, failures = gate.validate_gate_report(
        report,
        expected_commit=COMMIT,
        now=FIXED_NOW,
    )
    assert valid is True
    assert failures == ()

    stale = copy.deepcopy(report)
    stale["evidence"]["observed_at"] = (FIXED_NOW - timedelta(days=8)).isoformat()
    assert gate.validate_gate_report(stale, expected_commit=COMMIT, now=FIXED_NOW) == (
        False,
        ("report-stale",),
    )


@pytest.mark.parametrize(
    ("path", "value", "failure"),
    [
        (("success",), False, "report-success-required"),
        (("accepted",), False, "report-acceptance-policy-invalid"),
        (("acceptance_required",), False, "report-acceptance-policy-invalid"),
        (("evaluated_commit_sha",), "b" * 40, "report-candidate-commit-mismatch"),
        (("contract", "status"), "draft", "report-contract-not-approved"),
        (("evidence", "observations_sha256"), "bad", "report-evidence-projection-invalid"),
        (("checks", "promotion_policy"), False, "report-checks-invalid"),
    ],
)
def test_trusted_report_validator_rejects_self_assertion_mutations(
    path: tuple[str, ...], value: object, failure: str
) -> None:
    contract = _contract(approved=True)
    report = _report(contract, _evidence(contract), require_accepted=True)
    target = report
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    valid, failures = gate.validate_gate_report(report, expected_commit=COMMIT, now=FIXED_NOW)
    assert valid is False
    assert failure in failures


def test_cli_writes_sanitized_report_and_promotion_exit_code(tmp_path: Path) -> None:
    contract = _contract()
    evidence = _evidence(contract)
    contract_path = tmp_path / "contract.json"
    evidence_path = tmp_path / "evidence.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--contract",
            str(contract_path),
            "--evidence",
            str(evidence_path),
            "--expected-commit",
            COMMIT,
            "--require-accepted",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    report = json.loads((ROOT / "reports" / "model_acceptance_gate.json").read_text(encoding="utf-8"))
    assert report["accepted"] is False
    assert "metrics" not in report["evidence"]


def test_duplicate_json_keys_and_nonfinite_numbers_are_rejected(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema": "one", "schema": "two"}', encoding="utf-8")
    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"value": NaN}', encoding="utf-8")

    assert gate.load_json(duplicate, prefix="input")[1] == ["input-duplicate-json-key"]
    assert gate.load_json(nonfinite, prefix="input")[1] == ["input-invalid-json"]


def test_ci_runs_model_acceptance_contract_tests() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "Model acceptance contract (release-blocking)" in workflow
    assert "python-api/tests/test_model_acceptance_gate.py" in workflow
