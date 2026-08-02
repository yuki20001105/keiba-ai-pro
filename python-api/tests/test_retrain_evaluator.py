from __future__ import annotations

import copy
import importlib
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest


PYTHON_API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_API))
evaluator = importlib.import_module("training.retrain_evaluator")

COMMIT = "c" * 40
JOB_ID = str(uuid.uuid4())
EVALUATOR_ID = "staging-evaluator-01"
JRA_TIMEZONE = ZoneInfo("Asia/Tokyo")


def _report(**updates: Any) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    value: dict[str, Any] = {
        "report_schema": "model-acceptance-gate-report",
        "schema_version": 1,
        "success": True,
        "verdict": "accepted",
        "verdict_reason": "all-approved-thresholds-pass",
        "accepted": True,
        "acceptance_required": True,
        "evaluated_commit_sha": COMMIT,
        "contract": {
            "contract_id": "model-acceptance-v1",
            "sha256": "a" * 64,
            "status": "approved",
        },
        "evidence": {
            "model_id": "candidate-model",
            "model_artifact_sha256": "b" * 64,
            "model_feature_columns_sha256": "d" * 64,
            "observations_sha256": "e" * 64,
            "observed_at": now.isoformat(),
        },
        "blockers": [],
        "checks": {
            "contract_schema": True,
            "contract_approved": True,
            "evidence_schema_and_binding": True,
            "metrics_against_thresholds": True,
            "promotion_policy": True,
        },
        "failure_codes": [],
    }
    value.update(updates)
    return value


class FakePipeline:
    def __init__(self, report: dict[str, Any]) -> None:
        self.report = report
        self.calls: list[tuple[Path, str]] = []

    def accepted_report(
        self, observations_path: Path, *, expected_commit: str
    ) -> dict[str, Any]:
        self.calls.append((observations_path, expected_commit))
        return self.report


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def register_accepted(self, **kwargs: Any) -> evaluator.RecordedEvaluation:
        self.calls.append(kwargs)
        return evaluator.RecordedEvaluation(
            job_id=kwargs["job_id"],
            record_version=kwargs["expected_version"] + 1,
            evaluation_report_sha256="f" * 64,
            evaluated_at=datetime.now(timezone.utc),
        )


def test_evaluator_builds_then_registers_only_accepted_sanitized_report(
    tmp_path: Path,
) -> None:
    observations = tmp_path / "observations.json"
    observations.write_text("{}", encoding="utf-8")
    pipeline = FakePipeline(_report())
    gateway = FakeGateway()

    result = evaluator.ApprovedRetrainEvaluator(
        gateway,
        evaluator_id=EVALUATOR_ID,
        candidate_commit_sha=COMMIT,
        pipeline=pipeline,
    ).run(job_id=JOB_ID, expected_version=7, observations_path=observations.resolve())

    assert result.job_id == JOB_ID
    assert result.record_version == 8
    assert pipeline.calls == [(observations.resolve(), COMMIT)]
    assert gateway.calls == [
        {
            "evaluator_id": EVALUATOR_ID,
            "job_id": JOB_ID,
            "expected_version": 7,
            "report": pipeline.report,
        }
    ]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(success=False),
        lambda value: value.update(evaluated_commit_sha="d" * 40),
        lambda value: value.update(blockers=["metric-failed"]),
        lambda value: value["contract"].update(status="draft"),
        lambda value: value["evidence"].update(model_artifact_sha256="0" * 63),
        lambda value: value["checks"].update(promotion_policy=False),
        lambda value: value["evidence"].update(observed_at="2020-01-01T00:00:00+00:00"),
        lambda value: value.update(extra=True),
    ],
)
def test_evaluator_revalidates_pipeline_report_before_rpc(
    tmp_path: Path,
    mutation: Any,
) -> None:
    report = copy.deepcopy(_report())
    mutation(report)
    gateway = FakeGateway()
    with pytest.raises(evaluator.RetrainEvaluationError, match="report-invalid"):
        evaluator.ApprovedRetrainEvaluator(
            gateway,
            evaluator_id=EVALUATOR_ID,
            candidate_commit_sha=COMMIT,
            pipeline=FakePipeline(report),
        ).run(
            job_id=JOB_ID,
            expected_version=7,
            observations_path=(tmp_path / "observations.json").resolve(),
        )
    assert gateway.calls == []


def _observations(now: datetime) -> dict[str, Any]:
    rows = []
    for index, (probability, label) in enumerate(
        ((0.1, 0), (0.9, 1), (0.2, 0), (0.8, 1)), start=1
    ):
        prediction_at = now - timedelta(days=5 - index, hours=2)
        rows.append(
            {
                "observation_id": f"observation-{index}",
                "race_date": prediction_at.astimezone(JRA_TIMEZONE).date().isoformat(),
                "prediction_at": prediction_at.isoformat(),
                "data_observed_at": (prediction_at - timedelta(minutes=10)).isoformat(),
                "settled_at": (prediction_at + timedelta(hours=1)).isoformat(),
                "y_true": label,
                "predicted_probability": probability,
                "wager_amount": 10.0,
                "return_amount": 20.0 if label else 0.0,
                "baseline_wager_amount": 10.0,
                "baseline_return_amount": 20.0 if label else 0.0,
                "latency_ms": 100.0,
            }
        )
    return {
        "schema": "model-evaluation-observations",
        "schema_version": 1,
        "candidate_commit_sha": COMMIT,
        "model_id": "candidate-model",
        "model_artifact_sha256": "b" * 64,
        "generated_at": now.isoformat(),
        "training_data_ended_at": (now - timedelta(days=10)).isoformat(),
        "holdout_kind": "out_of_time",
        "model_feature_columns": ["horse_age"],
        "expanding_window_checks_passed": True,
        "initial_bankroll": 100.0,
        "rows": rows,
    }


def test_canonical_draft_contract_cannot_register_even_valid_rows(tmp_path: Path) -> None:
    observations = tmp_path / "observations.json"
    observations.write_text(
        json.dumps(_observations(datetime.now(timezone.utc))), encoding="utf-8"
    )
    pipeline = evaluator.ModelAcceptancePipeline(evaluator.CANONICAL_CONTRACT_PATH)
    with pytest.raises(evaluator.RetrainEvaluationError, match="not-accepted"):
        pipeline.accepted_report(observations.resolve(), expected_commit=COMMIT)


def test_pipeline_recomputes_strict_rows_into_accepted_sanitized_report(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    observations = tmp_path / "observations.json"
    observations.write_text(json.dumps(_observations(now)), encoding="utf-8")
    contract = json.loads(evaluator.CANONICAL_CONTRACT_PATH.read_text(encoding="utf-8"))
    contract.update(
        {
            "status": "approved",
            "approved_at": now.isoformat(),
            "approved_by": "business-owner",
            "approval_reference": "approval/model-acceptance-v1",
        }
    )
    thresholds = {
        "auc": 0.85,
        "brier_score": 0.20,
        "expected_calibration_error": 0.20,
        "roi_percent": 0.0,
        "max_drawdown_percent": 100.0,
        "bet_count": 4,
        "sample_count": 4,
        "p95_latency_ms": 500.0,
        "data_freshness_minutes": 30.0,
        "observation_period_days": 4,
        "baseline_roi_delta_percent": 0.0,
    }
    for metric, threshold in thresholds.items():
        contract["thresholds"][metric]["value"] = threshold
    contract_path = tmp_path / "approved-contract.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    report = evaluator.ModelAcceptancePipeline(
        contract_path.resolve()
    ).accepted_report(observations.resolve(), expected_commit=COMMIT)

    assert report["accepted"] is True
    assert report["verdict"] == "accepted"
    assert report["blockers"] == report["failure_codes"] == []
    assert "metrics" not in report


class RpcClient:
    def __init__(self, data: Any) -> None:
        self.data = data
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def rpc(self, name: str, params: dict[str, Any]) -> SimpleNamespace:
        self.calls.append((name, params))
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=self.data))


def _rpc_row(**updates: Any) -> dict[str, Any]:
    row = {
        "job_id": JOB_ID,
        "record_version": 8,
        "job_state": "evaluation-recorded",
        "evaluation_recorded": True,
        "acceptance_passed": True,
        "promotion_eligible": False,
        "evaluator_id": EVALUATOR_ID,
        "evaluation_report_sha256": "f" * 64,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }
    row.update(updates)
    return row


def test_supabase_gateway_uses_exact_registration_rpc() -> None:
    client = RpcClient([_rpc_row()])
    gateway = evaluator.SupabaseRetrainEvaluationGateway(client)
    report = _report()

    result = gateway.register_accepted(
        evaluator_id=EVALUATOR_ID,
        job_id=JOB_ID,
        expected_version=7,
        report=report,
    )

    assert result.record_version == 8
    assert client.calls == [
        (
            "register_model_retrain_accepted_evaluation",
            {
                "p_evaluator_id": EVALUATOR_ID,
                "p_job_id": JOB_ID,
                "p_expected_version": 7,
                "p_sanitized_report": report,
            },
        )
    ]


@pytest.mark.parametrize(
    "updates",
    [
        {"record_version": True},
        {"job_state": "artifact-registered"},
        {"promotion_eligible": True},
        {"evaluator_id": "other-evaluator"},
        {"evaluation_report_sha256": "f" * 63},
        {"evaluated_at": "naive"},
    ],
)
def test_supabase_gateway_rejects_invalid_registration_response(
    updates: dict[str, Any],
) -> None:
    gateway = evaluator.SupabaseRetrainEvaluationGateway(RpcClient([_rpc_row(**updates)]))
    with pytest.raises(evaluator.RetrainEvaluationError, match="response-invalid"):
        gateway.register_accepted(
            evaluator_id=EVALUATOR_ID,
            job_id=JOB_ID,
            expected_version=7,
            report=_report(),
        )
