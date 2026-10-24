"""Local display-only win-value estimates, never ticket-selection authority.

These are a calibrated model's probability times the *displayed* odds. The
input formula must match, but neither capture time nor executable odds is
claimed. Keep them outside predictions.expected_value, recommendations,
performance accounting and historical/forward validation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import re
from typing import Any
from training.db_shared_profiles import PROFILE_FEATURE_COUNTS

SCHEMA = "calibrated-win-reference-v1"
DISPLAY_TTL = timedelta(minutes=5)
_CAPTURE_ISSUES = {"pre_race_db_capture_unverified", "pre_race_db_capture_failed"}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _instant(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def build_reference_expectation(
    predictions: list[dict], race_info: dict, recommendation: dict, *,
    context: dict, model_id: str, environment: str, now: datetime | None = None,
) -> dict | None:
    """Use only internal, artifact-bound validation; do not repair any evidence.

    A completed race may be inspected as a *current-model reference trial*,
    not as proof that a prediction or these odds existed before its start.
    The display TTL is measured from calculation, NOT from odds observation.
    """
    if not isinstance(environment, str) or environment not in {"development", "local", "test"}:
        return None
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None:
        return None
    try:
        now = now.astimezone(timezone.utc)
        valid_until = now + DISPLAY_TTL
    except (ValueError, OverflowError):
        return None
    if not all(isinstance(value, dict) for value in (race_info, recommendation, context)):
        return None
    diagnostics = context.get("input_diagnostics")
    if not isinstance(diagnostics, dict):
        return None
    issues = diagnostics.get("issue_codes")
    pipeline = diagnostics.get("feature_pipeline")
    expected_count = PROFILE_FEATURE_COUNTS.get(pipeline) if isinstance(pipeline, str) else None
    if not (
        diagnostics.get("schema") == "prediction-input-contract-v1"
        and expected_count is not None
        and diagnostics.get("status") == "unverified"
        and type(diagnostics.get("expected_count")) is int
        and diagnostics["expected_count"] == expected_count
        and type(diagnostics.get("actual_count")) is int
        and diagnostics["actual_count"] == expected_count
        and diagnostics.get("missing_columns") == []
        and diagnostics.get("blocked_columns") == []
        and diagnostics.get("source_hash_matches") is True
        and diagnostics.get("semantic_parity_verified") is True
        and diagnostics.get("history_available") is True
        and diagnostics.get("live_capture_hash_bound") is False
        and diagnostics.get("current_outcomes_consumed") is False
        and isinstance(issues, list) and len(issues) > 0
        and all(isinstance(code, str) and code in _CAPTURE_ISSUES for code in issues)
    ):
        return None
    missing_rate = _number(context.get("feature_missing_rate"))
    diagnostic_missing_rate = _number(diagnostics.get("missing_value_rate"))
    if (missing_rate is None or not 0 <= missing_rate <= .20
            or diagnostic_missing_rate is None
            or not math.isclose(missing_rate, diagnostic_missing_rate, rel_tol=0., abs_tol=1e-12)):
        return None
    # A reference value cannot revive a recommendation, even a contradictory
    # payload that claims skip while retaining a priced ticket or amount.
    if not (
        recommendation.get("policy_version") == "value-selection-v2"
        and recommendation.get("probability_basis") == "calibrated"
        and recommendation.get("input_verified") is False
        and recommendation.get("decision") == "skip"
        and recommendation.get("tickets") == []
        and all(_number(recommendation.get(key)) == 0 for key in
                ("purchase_count", "unit_price", "total_cost", "expected_return"))
    ):
        return None
    validation = context.get("probability_validation")
    if not isinstance(validation, dict) or not isinstance(model_id, str) or not model_id:
        return None
    if not (
        validation.get("validated") is True
        and validation.get("model_id") == model_id
        and isinstance(validation.get("model_sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", validation["model_sha256"])
        and validation.get("probability_kind") == "race_normalized_win"
        and validation.get("evaluation_method") == "out_of_sample"
        and validation.get("selection_calibration_reviewed") is True
        and isinstance(validation.get("eligible_bet_types"), list)
        and "単勝" in validation["eligible_bet_types"]
    ):
        return None
    validated_at = _instant(validation.get("validated_at"))
    try:
        validation_end = datetime.strptime(validation["validation_period_end"], "%Y-%m-%d").date()
        date = str(race_info["date"]).replace("-", "").replace("/", "")
        target_date = datetime.strptime(date, "%Y%m%d").date()
    except (ValueError, TypeError, KeyError):
        return None
    race_id = race_info.get("race_id")
    if (validated_at is None or validated_at > now or validation_end >= target_date
            or not isinstance(race_id, str) or not race_id):
        return None
    runner_count = race_info.get("num_horses")
    if (type(runner_count) is not int or not 2 <= runner_count <= 18
            or not isinstance(predictions, list) or len(predictions) != runner_count):
        return None
    numbers, probabilities, items = [], [], []
    for prediction in predictions:
        if not isinstance(prediction, dict):
            return None
        number, probability = prediction.get("horse_number"), _number(prediction.get("p_norm"))
        if (type(number) is not int or not 1 <= number <= 18
                or probability is None or not 0 <= probability <= 1):
            return None
        numbers.append(number)
        probabilities.append(probability)
        odds = _number(prediction.get("odds"))
        if odds is not None and odds >= 1:
            value = probability * odds
            if math.isfinite(value):
                items.append({"horse_number": number, "probability": probability,
                              "odds": odds, "expected_value": value})
    if (len(set(numbers)) != runner_count or not items
            or not math.isclose(sum(probabilities), 1., rel_tol=0., abs_tol=1e-5)):
        return None
    return {
        "schema": SCHEMA, "basis": "calibrated_probability_displayed_odds",
        "purchase_eligible": False, "capture_verified": False,
        "odds_freshness_verified": False, "model_id": model_id, "race_id": race_id,
        "computed_at": now.isoformat(), "valid_until": valid_until.isoformat(),
        "items": items,
    }
