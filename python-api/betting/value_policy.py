"""Fail-closed live ticket selection, separate from frozen research strategies.

Scores are not probabilities. A model-specific, out-of-sample validation sidecar
is required for the *race-normalized* win probability actually served by the API.
Combination tickets require their own joint probability and market odds; neither
win-probability products nor the average of single-win EVs are used.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
from typing import Any
from training.db_shared_profiles import SHARED_PIPELINES

# Invalidate cached v1 tickets: that version did not require verified inputs.
POLICY_VERSION = "value-selection-v2"
# The blog contributes selection heuristics only. Neither it nor Kelly proves
# a market edge. Empirical model validation remains a separate requirement.
POLICY_REFERENCES = (
    "https://umabi.jp/betting/how-to-buy-profitably/",
    "https://www.jra.go.jp/kouza/baken/",
    "https://www.edwardothorp.com/wp-content/uploads/2016/11/TheKellyCriterionAndTheStockMarket.pdf",
    "https://gwern.net/doc/statistics/decision/1994-benter.pdf",
)
BET_TYPES = {"単勝": 1, "馬連": 2, "ワイド": 2, "馬単": 2, "三連複": 3, "三連単": 3}
ORDERED_TYPES = {"馬単", "三連単"}
RISK_SETTINGS = {
    "conservative": (0.02, 0.125, 1),
    "balanced": (0.035, 0.25, 3),
    "aggressive": (0.05, 1 / 3, 5),
}
# Predeclared safety buffers, not fitted to viewed historical ROI. They are not
# confidence intervals and do not turn an unvalidated model into a safe one.
PROBABILITY_HAIRCUT = 0.10
ODDS_HAIRCUT = 0.05
MAX_ODDS_AGE = timedelta(minutes=5)
JST = timezone(timedelta(hours=9))


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _instant(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return result.astimezone(timezone.utc) if result.tzinfo else None


def verified_prediction_input(diagnostics: Any) -> bool:
    """Alignment alone is not proof of the trained input's meaning.

    This evidence is supplied only by the server, never request parameters.
    The diagnostic-only legacy adapter deliberately does not grant semantic
    verification; an audited migration must establish it before live selection.
    """
    if not isinstance(diagnostics, dict):
        return False
    count = diagnostics.get("expected_count")
    return (
        diagnostics.get("schema") == "prediction-input-contract-v1"
        and diagnostics.get("status") == "aligned"
        and type(count) is int and count > 0
        and type(diagnostics.get("actual_count")) is int
        and diagnostics["actual_count"] == count
        and diagnostics.get("missing_columns") == []
        and diagnostics.get("blocked_columns") == []
        and diagnostics.get("issue_codes") == []
        and diagnostics.get("source_hash_matches") is True
        and diagnostics.get("history_available") is True
        and diagnostics.get("semantic_parity_verified") is True
    )


def _post_time(info: dict) -> datetime | None:
    date = str(info.get("date") or "").replace("/", "").replace("-", "")
    post = str(info.get("post_time") or "")
    try:
        return datetime.strptime(date + " " + post, "%Y%m%d %H:%M").replace(tzinfo=JST)
    except ValueError:
        return None


def _hundreds(value: float) -> int:
    return max(0, int(math.floor((value + 1e-8) / 100)) * 100)


@lru_cache(maxsize=16)
def _artifact_hash(path: str, size: int, modified_ns: int) -> str:
    del size, modified_ns  # part of cache key: changed files must be rehashed
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_probability_validation(model_path: Path, *, target: str, calibrated: bool) -> dict:
    """Never borrow calibration from H04 or another model.

    Optional <model>.betting-validation.json must bind to the exact joblib hash,
    declare an out-of-sample evaluation of normalized win probabilities, record
    NLL/Brier and its validation period, and predate the evaluated race. Absence
    means ranking may still be displayed, but no purchase recommendation.
    """
    unavailable = {"validated": False, "eligible_bet_types": []}
    if target != "win" or not calibrated:
        return unavailable
    try:
        report = json.loads(model_path.with_suffix(".betting-validation.json").read_text(encoding="utf-8"))
        if not isinstance(report, dict) or not isinstance(report.get("metrics"), dict):
            return unavailable
        stat = model_path.stat()
        metrics = report.get("metrics", {})
        period_start = datetime.strptime(str(report.get("validation_period_start")), "%Y-%m-%d").date()
        period_end = datetime.strptime(str(report.get("validation_period_end")), "%Y-%m-%d").date()
        validated_at = _instant(report.get("validated_at"))
        valid = (
            report.get("schema_version") == 1
            and report.get("status") == "validated"
            and report.get("model_id") == model_path.stem
            and report.get("model_sha256") == _artifact_hash(str(model_path), stat.st_size, stat.st_mtime_ns)
            and report.get("probability_kind") == "race_normalized_win"
            and report.get("evaluation_method") == "out_of_sample"
            and type(report.get("race_count")) is int and report["race_count"] >= 200
            and report.get("selection_calibration_reviewed") is True
            and _number(metrics.get("nll")) is not None and float(metrics["nll"]) >= 0
            and _number(metrics.get("brier")) is not None and 0 <= float(metrics["brier"]) <= 2
            and period_start <= period_end
            and validated_at is not None and validated_at.astimezone(JST).date() > period_end
            and isinstance(report.get("eligible_bet_types"), list)
            and "単勝" in report.get("eligible_bet_types", [])
        )
        return {**report, "validated": bool(valid)} if valid else unavailable
    except (OSError, ValueError, TypeError, KeyError):
        return unavailable


def build_live_context(
    *, model_path: Path, predictor: Any, source_records: list[dict], feature_frame: Any,
) -> dict:
    """Internal evidence adapter; clients cannot supply validation flags."""
    validation = load_probability_validation(
        model_path, target=predictor.target,
        calibrated=bool(getattr(predictor, "calibrator_applied", False)),
    )
    if (getattr(predictor, "bundle", {}).get("feature_pipeline") in SHARED_PIPELINES
            and getattr(predictor, "calibrator_applied", False)):
        from services.db_shared_input import embedded_probability_report
        validation = embedded_probability_report(predictor.bundle, model_path)
    missing_rate = float(feature_frame.isna().to_numpy().mean()) if feature_frame.size else 1.0
    return {
        "probability_validation": validation,
        "input_diagnostics": getattr(predictor, "input_diagnostics", None),
        "feature_missing_rate": missing_rate,
        "sources": [{
            "horse_number": row.get("horse_number", row.get("horse_no")),
            "odds_status": row.get("odds_status"),
            "observed_at": row.get("data_observed_at"),
            "has_result": any(row.get(key) not in (None, "", 0, "0")
                              for key in ("finish_position", "finish")),
        } for row in source_records],
        # No joint-probability producer is validated in the current live stack.
        "joint_tickets": [],
    }


def recommend(
    predictions: list[dict], race_info: dict, *, bankroll: int, risk_mode: str,
    min_ev: float, use_kelly: bool, dynamic_unit: bool, context: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """Select exact tickets under a race cap; never simulate an actual purchase."""
    context = context or {}
    now = now or datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc)
    rate, kelly_fraction, max_points = RISK_SETTINGS.get(risk_mode, RISK_SETTINGS["balanced"])
    bankroll = max(0, int(bankroll))
    cap = _hundreds(bankroll * rate)
    validation = context.get("probability_validation") or {}
    calibrated = validation.get("validated") is True
    reason_codes: list[str] = []
    reasons: list[str] = []

    def reject(code: str, reason: str) -> None:
        if code not in reason_codes:
            reason_codes.append(code)
            reasons.append(reason)

    if not calibrated:
        reject("probability_unvalidated", "勝率の検証が未完了")
    input_diagnostics = context.get("input_diagnostics")
    input_verified = verified_prediction_input(input_diagnostics)
    if not input_verified:
        reject("input_contract_unverified", "モデルの入力互換性が未確認")
    post = _post_time(race_info)
    deadline = post - timedelta(minutes=1) if post else None
    if post is None:
        reject("post_time_unknown", "発走時刻が未確認")
    elif deadline <= now:
        reject("race_closed", "発走済みのレース")
    elif post.astimezone(JST).date() != now.astimezone(JST).date():
        reject("not_today", "当日のオッズで再判定が必要")
    if calibrated:
        validated_at = _instant(validation.get("validated_at"))
        # OOS results cannot be retroactively used to recommend bets in that
        # same validation period, even when the model hash is correct.
        try:
            validation_end = datetime.strptime(str(validation.get("validation_period_end")), "%Y-%m-%d").date()
        except ValueError:
            validation_end = None
        if validated_at is None or validated_at > now or validation_end is None or (
            post is not None and validation_end >= post.astimezone(JST).date()
        ):
            reject("validation_timing_unknown", "勝率の検証時点が未確認")
    if cap < 100:
        reject("budget_below_minimum", "予算が100円未満")
    missing_rate = _number(context.get("feature_missing_rate"))
    if missing_rate is None or not 0 <= missing_rate <= 0.20:
        reject("input_incomplete", "入力データの不足")

    numbers: list[int] = []
    probabilities: list[float] = []
    for prediction in predictions:
        number = _number(prediction.get("horse_number", prediction.get("horse_no")))
        probability = _number(prediction.get("p_norm"))
        if number is None or number < 1 or not number.is_integer():
            reject("runner_invalid", "出走馬データが不整合")
        else:
            numbers.append(int(number))
        if probability is None or not 0 <= probability <= 1:
            reject("probabilities_invalid", "勝率の値が不正")
        else:
            probabilities.append(probability)
    if len(numbers) < 2 or len(set(numbers)) != len(numbers):
        reject("runner_invalid", "出走馬データが不整合")
    expected_count = _number(race_info.get("num_horses"))
    if expected_count is None or not 2 <= expected_count <= 18 or not expected_count.is_integer() or expected_count != len(predictions):
        reject("runner_count_mismatch", "出走馬数が一致しません")
    if not probabilities or not math.isclose(sum(probabilities), 1.0, abs_tol=1e-5):
        reject("probabilities_invalid", "勝率の値が不正")

    sources = context.get("sources") or []
    source_map = {str(row.get("horse_number")): row for row in sources}
    if len(source_map) != len(sources) or len(sources) != len(predictions):
        reject("odds_provenance_missing", "オッズの取得時点が未確認")

    def check_market(source: dict) -> bool:
        observed = _instant(source.get("observed_at"))
        if source.get("has_result") or source.get("odds_status") in {"result", "settled"}:
            reject("result_odds", "確定後のオッズは対象外")
            return False
        if source.get("odds_status") != "middle":
            reject("odds_not_market", "実オッズが未確認")
            return False
        if observed is None:
            reject("odds_provenance_missing", "オッズの取得時点が未確認")
            return False
        if observed > now or now - observed > MAX_ODDS_AGE or (post is not None and observed >= post):
            reject("odds_stale", "オッズを更新して再判定してください")
            return False
        return True

    for number in numbers:
        check_market(source_map.get(str(number), {}))
    if any(_number(pred.get("odds")) is None or float(pred["odds"]) < 1 for pred in predictions):
        reject("odds_incomplete", "オッズが未取得または無効")

    candidate_tickets: list[dict] = []
    eligible = validation.get("eligible_bet_types", [])
    if not reason_codes:
        for prediction in predictions:
            if "単勝" in eligible:
                number = int(prediction.get("horse_number", prediction.get("horse_no")))
                candidate_tickets.append({"bet_type": "単勝", "horse_numbers": [number],
                    "probability": float(prediction["p_norm"]), "odds": float(prediction["odds"])})
        for item in context.get("joint_tickets", []):
            # Joint-probability and odds evidence must be explicitly bound to
            # this race and exact model, not derived by multiplying marginals.
            bet_type = item.get("bet_type")
            if bet_type not in eligible or bet_type not in BET_TYPES or bet_type == "単勝":
                continue
            members = item.get("horse_numbers", [])
            p, odds = _number(item.get("probability")), _number(item.get("odds"))
            if (len(members) != BET_TYPES[bet_type] or len(set(members)) != len(members)
                or any(n not in numbers for n in members) or p is None or not 0 < p <= 1
                or odds is None or odds <= 1 or item.get("probability_validated") is not True
                or item.get("model_id") != validation.get("model_id")
                or item.get("race_id") != race_info.get("race_id")):
                continue
            # A bad optional combination observation must not poison valid win
            # tickets; simply exclude that combination.
            observed = _instant(item.get("observed_at"))
            if (item.get("odds_status") != "middle" or observed is None or observed > now
                or now - observed > MAX_ODDS_AGE or (post is not None and observed >= post)):
                continue
            candidate_tickets.append({"bet_type": bet_type, "horse_numbers": list(members), "probability": p, "odds": odds})

    selected: list[dict] = []
    seen: set[tuple] = set()
    threshold = max(1.05, min_ev)
    for ticket in candidate_tickets:
        members = ticket["horse_numbers"]
        if ticket["bet_type"] not in ORDERED_TYPES:
            members = sorted(members)
        key = (ticket["bet_type"], *members)
        if key in seen:
            continue
        seen.add(key)
        p, odds = ticket["probability"], ticket["odds"]
        conservative_p = p * (1 - PROBABILITY_HAIRCUT)
        conservative_odds = odds * (1 - ODDS_HAIRCUT)
        ev = conservative_p * conservative_odds
        if ev < threshold or conservative_odds <= 1:
            continue
        kelly = bankroll * kelly_fraction * max(0, (ev - 1) / (conservative_odds - 1))
        amount = _hundreds(min(cap, kelly)) if use_kelly else min(cap, 100)
        if not dynamic_unit:
            amount = min(amount, 100)
        if amount < 100:
            continue
        selected.append({**ticket, "horse_numbers": members,
            "combination": ("→" if ticket["bet_type"] in ORDERED_TYPES else "-").join(map(str, members)),
            "expected_value": p * odds, "conservative_expected_value": ev,
            "stake": amount, "_conservative_return": ev})
    selected.sort(key=lambda ticket: (-ticket["conservative_expected_value"], ticket["bet_type"], ticket["horse_numbers"]))
    selected = selected[:max_points]
    # Kelly for an individual ticket is not joint portfolio Kelly. To limit
    # overlapping-outcome exposure, all tickets share at most the largest
    # individual allowance (and the race cap), not the sum of allowances.
    remaining = min(cap, max((ticket["stake"] for ticket in selected), default=0))
    tickets: list[dict] = []
    for ticket in selected:
        if remaining < 100:
            break
        tickets.append({**ticket, "stake": 100})
        remaining -= 100
    for ticket, allowance in zip(tickets, selected):
        extra = min(allowance["stake"] - 100, remaining)
        ticket["stake"] += extra
        remaining -= extra
    tickets = [{k: v for k, v in t.items() if not k.startswith("_")} for t in tickets]
    if not tickets and not reason_codes:
        reject("no_value_or_stake", "条件を満たす買い目なし")

    total = sum(ticket["stake"] for ticket in tickets)
    priority = bool(tickets) and tickets[0]["conservative_expected_value"] >= 1.5 and tickets[0]["probability"] >= 0.15
    decision = "priority" if priority else "buy" if tickets else "skip"
    action = "勝負候補" if priority else "購入候補" if tickets else "見送り"
    reason = "安全側の期待値と予算条件を満たしています" if tickets else reasons[0]
    unit_prices = {ticket["stake"] for ticket in tickets}
    observation_expiries = [
        observed + MAX_ODDS_AGE for source in sources
        if (observed := _instant(source.get("observed_at"))) is not None
    ]
    valid_until = min([deadline, *observation_expiries]) if deadline and observation_expiries else now
    recommendation = {
        "action": action, "decision": decision, "reason": reason, "reason_codes": reason_codes,
        "reasons": reasons, "policy_version": POLICY_VERSION,
        "input_verified": input_verified,
        "probability_basis": "calibrated" if calibrated else "uncalibrated_score",
        "tickets": tickets, "purchase_count": len(tickets),
        "unit_price": unit_prices.pop() if len(unit_prices) == 1 else 0,
        "total_cost": total, "expected_return": sum(t["stake"] * t["expected_value"] for t in tickets),
        "budget": cap, "per_race_limit": cap, "max_points": max_points,
        "risk_mode": risk_mode, "bankroll": bankroll, "evaluated_at": now.isoformat(),
        "valid_until": valid_until.isoformat(),
        "budget_usage_rate": round(total / cap * 100, 1) if cap else 0,
        "strategy_explanation": reason,
        "safety_buffers": {"probability": PROBABILITY_HAIRCUT, "odds": ODDS_HAIRCUT},
        "excluded_bet_types": [kind for kind in BET_TYPES if kind not in {t["bet_type"] for t in candidate_tickets}],
    }
    displayed = [{**pred, "expected_value": (
        float(pred["p_norm"]) * float(pred["odds"])
        if calibrated and input_verified and _number(pred.get("p_norm")) is not None and _number(pred.get("odds")) is not None
        else None)} for pred in predictions]
    bet_types = {kind: [ticket for ticket in tickets if ticket["bet_type"] == kind] for kind in BET_TYPES}
    return {
        "race_info": dict(race_info), "predictions": displayed, "bet_types": bet_types,
        "best_bet_type": tickets[0]["bet_type"] if tickets else "", "race_level": "decisive" if priority else "normal" if tickets else "skip",
        "best_bet_info": {"平均期待値": sum(t["expected_value"] for t in tickets) / len(tickets) if tickets else 0,
            "最大期待値": max((t["expected_value"] for t in tickets), default=0),
            "候補数": len(tickets), "最高確率": max((t["probability"] for t in tickets), default=0)},
        "pro_evaluation": {"recommended_action": action, "probability_basis": recommendation["probability_basis"],
            "confidence_level": "validated" if calibrated and input_verified else "unverified", "reason": reason},
        "recommendation": recommendation,
    }
