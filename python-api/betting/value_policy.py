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
from urllib.parse import parse_qs, urlsplit
from training.db_shared_profiles import SHARED_PIPELINES

# Invalidate cached v2 tickets: they did not bind market odds to the fetched
# response body and the exact race/runner/value.
POLICY_VERSION = "value-selection-v4"
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


def _verified_odds_capture(evidence: Any, *, race_id: Any, horse_number: int, odds: Any) -> bool:
    if not isinstance(evidence, dict) or evidence.get("schema") != "netkeiba-win-odds-capture-v1":
        return False
    if (str(evidence.get("race_id") or "") != str(race_id or "")
            or evidence.get("http_status") != 200 or evidence.get("odds_status") != "middle"):
        return False
    digest = evidence.get("response_sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        return False
    parsed = urlsplit(str(evidence.get("source_url") or ""))
    query = parse_qs(parsed.query)
    if (parsed.scheme != "https" or parsed.hostname != "race.netkeiba.com"
            or parsed.path != "/api/api_get_jra_odds.html"
            or query.get("race_id") != [str(race_id)] or query.get("type") != ["1"]):
        return False
    captured_odds = evidence.get("odds_by_horse_number")
    captured = _number(captured_odds.get(str(horse_number))) if isinstance(captured_odds, dict) else None
    current = _number(odds)
    return captured is not None and current is not None and math.isclose(captured, current, rel_tol=1e-9, abs_tol=1e-9)


def _verified_joint_odds_capture(
    evidence: Any, *, race_id: Any, bet_type: str, horse_numbers: list[int], odds: Any,
    observed_at: datetime,
) -> bool:
    """Require exact response-bound market evidence for a combination ticket.

    The current live adapter does not produce this evidence, so combinations
    remain unavailable until a server-side odds capture is implemented. This
    guard prevents an internal candidate from bypassing the win-odds proof.
    """
    if not isinstance(evidence, dict) or evidence.get("schema") != "netkeiba-combination-odds-capture-v1":
        return False
    if (str(evidence.get("race_id") or "") != str(race_id or "")
            or evidence.get("bet_type") != bet_type or evidence.get("http_status") != 200
            or evidence.get("odds_status") != "middle"):
        return False
    digest = evidence.get("response_sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        return False
    parsed = urlsplit(str(evidence.get("source_url") or ""))
    query = parse_qs(parsed.query)
    expected_market_type = {"馬連": "b6", "三連複": "b8", "ワイド": "b5", "三連単": "b8"}.get(bet_type)
    if bet_type == "ワイド" and evidence.get("price_basis") != "lower_bound":
        return False
    if (parsed.scheme != "https" or parsed.hostname != "race.netkeiba.com"
            or parsed.path != "/odds/index.html"
            or query.get("race_id") != [str(race_id)]
            or query.get("type") != [expected_market_type] or expected_market_type is None
            or _instant(evidence.get("observed_at")) != observed_at):
        return False
    key = ("→" if bet_type in ORDERED_TYPES else "-").join(map(str, horse_numbers))
    captured_odds = evidence.get("odds_by_combination")
    captured = _number(captured_odds.get(key)) if isinstance(captured_odds, dict) else None
    current = _number(odds)
    return captured is not None and current is not None and math.isclose(captured, current, rel_tol=1e-9, abs_tol=1e-9)


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
        nll = _number(metrics.get("nll"))
        brier = _number(metrics.get("brier"))
        uniform_nll = _number(metrics.get("uniform_nll"))
        uniform_brier = _number(metrics.get("uniform_brier"))
        selection_ece = _number(metrics.get("selection_ece"))
        selection_bias = _number(metrics.get("selection_absolute_bias"))
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
            # Merely finite non-negative losses are not evidence of a useful
            # win-probability model. Require the same predeclared gates used
            # by the local DB training pipeline, including uniform baselines
            # and top-pick calibration checks.
            and nll is not None and uniform_nll is not None and 0 <= nll <= uniform_nll
            and brier is not None and uniform_brier is not None and 0 <= brier <= uniform_brier
            and selection_ece is not None and 0 <= selection_ece <= 0.08
            and selection_bias is not None and 0 <= selection_bias <= 0.05
            and period_start <= period_end
            and validated_at is not None and validated_at.astimezone(JST).date() > period_end
            and isinstance(report.get("eligible_bet_types"), list)
            and "単勝" in report.get("eligible_bet_types", [])
        )
        return {**report, "validated": bool(valid)} if valid else unavailable
    except (OSError, ValueError, TypeError, KeyError):
        return unavailable


def load_axis_forecast(model_path: Path, predictions: list[dict], race_info: dict,
                       *, now: datetime | None = None) -> dict | None:
    """A research-only export never supplies live axis approval."""
    try:
        bundle = json.loads(model_path.with_suffix(".fixed-two-axis.json").read_text(encoding="utf-8"))
        review = bundle.get("live_axis_validation", {})
        metrics = review.get("metrics", {})
        current = now if now is not None else datetime.now(timezone.utc)
        day = str(race_info.get("date")).replace("-", "").replace("/", "")
        race_day = datetime.strptime(day, "%Y%m%d").date()
        end = datetime.strptime(str(review.get("validation_period_end")), "%Y-%m-%d").date()
        approved = _instant(review.get("validated_at"))
        stat = model_path.stat()
        digest = _artifact_hash(str(model_path), stat.st_size, stat.st_mtime_ns)
        if (review.get("status") != "validated" or review.get("evaluation_method") != "out_of_sample"
                or review.get("selection_calibration_reviewed") is not True
                or type(review.get("race_count")) is not int or review["race_count"] < 200
                or end >= race_day or approved is None or approved > current
                or approved.astimezone(JST).date() <= end
                or bundle.get("base_model_sha256") != digest):
            return None
        for loss, baseline in (("nll", "baseline_nll"), ("brier", "baseline_brier")):
            value, base = _number(metrics.get(loss)), _number(metrics.get(baseline))
            if value is None or base is None or not 0 <= value <= base:
                return None
        if (_number(metrics.get("selection_ece")) is None or not 0 <= metrics["selection_ece"] <= .08
                or _number(metrics.get("selection_absolute_bias")) is None
                or not 0 <= metrics["selection_absolute_bias"] <= .05):
            return None
        from betting.adaptive_strategy import race_features, predict_bundle
        from betting.research_opponent_pairs import opponent_orders
        import pandas as pd
        field = pd.DataFrame([{
            "horse_number": row.get("horse_number"), "probability": row.get("p_norm"), "odds": row.get("odds"),
            "race_id": race_info.get("race_id"), "race_date": day, "num_horses": race_info.get("num_horses"),
            "distance": race_info.get("distance"), "surface": race_info.get("track_type"),
            "venue": race_info.get("venue"), "field_condition": race_info.get("field_condition"),
            "race_class": race_info.get("race_class"),
        } for row in predictions])
        forecast = predict_bundle(bundle, race_features(field, partners=True), day, digest)
        axis = opponent_orders(field.to_dict("records"))[0]
        return {
            "schema": "fixed-two-axis-forecast-v1", "race_id": race_info.get("race_id"),
            "model_sha256": digest, "axis": axis, "probability": forecast["axis"], "validated": True,
            "fitted_through": bundle["fitted_through"], "validation_period_end": review["validation_period_end"],
            "validated_at": review["validated_at"],
        }
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
        return None


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
    missing = feature_frame.isna() if hasattr(feature_frame, "isna") else None
    missing_rate = float(missing.to_numpy().mean()) if missing is not None and feature_frame.size else 1.0
    missing_by_runner = (
        [float(value) for value in missing.mean(axis=1).tolist()]
        if missing is not None and feature_frame.shape[1] else [1.0] * len(source_records)
    )
    all_missing_features = (
        [str(column) for column, absent in missing.all(axis=0).items() if bool(absent)]
        if missing is not None else []
    )
    return {
        "probability_validation": validation,
        "input_diagnostics": getattr(predictor, "input_diagnostics", None),
        "feature_missing_rate": missing_rate,
        "feature_missing_by_runner": missing_by_runner,
        "all_missing_features": all_missing_features,
        "sources": [{
            "horse_number": row.get("horse_number", row.get("horse_no")),
            "odds_status": row.get("odds_status"),
            "observed_at": (row.get("odds_observation") or {}).get("observed_at")
                if isinstance(row.get("odds_observation"), dict) else None,
            "odds_evidence": row.get("odds_observation"),
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
    betting_strategy: str = "value",
) -> dict:
    """Select exact tickets under a race cap; never simulate an actual purchase."""
    context = context or {}
    now = now or datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc)
    rate, kelly_fraction, max_points = RISK_SETTINGS.get(risk_mode, RISK_SETTINGS["balanced"])
    bankroll = max(0, int(bankroll))
    cap = _hundreds(bankroll * rate)
    fixed = betting_strategy == "fixed-two-v1"
    if betting_strategy not in {"value", "fixed-two-v1"}:
        raise ValueError("unknown_betting_strategy")
    if fixed:
        cap, max_points = min(cap, 1500), 3
    validation = context.get("probability_validation") or {}
    calibrated = validation.get("validated") is True
    reason_codes: list[str] = []
    reasons: list[str] = []

    def reject(code: str, reason: str) -> None:
        if code not in reason_codes:
            reason_codes.append(code)
            reasons.append(reason)

    fixed_selection = None
    fixed_book = []
    if fixed:
        from betting.research_opponent_pairs import opponent_orders
        from betting.adaptive_strategy import numeric_priority
        import pandas as pd
        try:
            field = [{"horse_number": row.get("horse_number"), "probability": row.get("p_norm"),
                      "odds": row.get("odds")} for row in predictions]
            axis, _, hole = opponent_orders(field)
            h2, h3 = hole[1:3]
            fixed_book = [("ワイド", sorted([axis, h3])),
                          ("三連単", [h2, axis, h3]), ("三連単", [h3, axis, h2])]
            fixed_selection = {"axis": axis, "h2": h2, "h3": h3, "axis_probability": None,
                               "numeric_priority": bool(numeric_priority(pd.DataFrame(field)))}
            if not fixed_selection["numeric_priority"]:
                reject("not_numeric_priority", "数値的な勝負候補の条件を満たしません")
        except (KeyError, TypeError, ValueError, IndexError):
            reject("fixed_field_invalid", "②には全出走馬の正常な勝率・単勝オッズが必要です")
        forecast = context.get("axis_forecast")
        vp = _number(forecast.get("probability")) if isinstance(forecast, dict) else None
        valid_axis = False
        if isinstance(forecast, dict) and fixed_selection:
            try:
                through = datetime.strptime(str(forecast.get("fitted_through")), "%Y%m%d").date()
                end = datetime.strptime(str(forecast.get("validation_period_end")), "%Y-%m-%d").date()
                race_day = datetime.strptime(str(race_info.get("date")).replace("-", "").replace("/", ""), "%Y%m%d").date()
                approved = _instant(forecast.get("validated_at"))
                digest = (context.get("probability_validation") or {}).get("model_sha256")
                valid_axis = (forecast.get("schema") == "fixed-two-axis-forecast-v1"
                              and forecast.get("validated") is True
                              and forecast.get("race_id") == race_info.get("race_id")
                              and forecast.get("axis") == fixed_selection["axis"]
                              and isinstance(digest, str) and len(digest) == 64
                              and forecast.get("model_sha256") == digest
                              and through < race_day and end < race_day
                              and approved is not None and approved <= now
                              and vp is not None and 0 <= vp <= 1)
            except (ValueError, TypeError):
                pass
        if not valid_axis:
            reject("axis_probability_unvalidated", "このモデルのAの3着内確率が未検証です")
        else:
            fixed_selection["axis_probability"] = vp
            if vp < .65:
                reject("axis_unlikely", "Aの3着内確率が65%未満です")
        if cap < 300:
            reject("fixed_budget_below_minimum", "②の3点にはレース予算300円以上が必要です")

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
    runner_missing = context.get("feature_missing_by_runner")
    if isinstance(runner_missing, list) and (
        len(runner_missing) != len(predictions)
        or any(_number(rate) is None or not 0 <= float(rate) <= 0.20 for rate in runner_missing)
    ):
        reject("input_incomplete", "一部の馬で特徴量が20%を超えて欠損")
    all_missing_features = context.get("all_missing_features")
    if isinstance(all_missing_features, list) and all_missing_features:
        reject("input_incomplete", f"全馬で欠損している特徴量: {', '.join(map(str, all_missing_features[:5]))}")

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

    prediction_by_number = {
        str(int(number)): next((pred for pred in predictions
                               if _number(pred.get("horse_number", pred.get("horse_no"))) == number), {})
        for number in numbers
    }
    verified_market_numbers: set[int] = set()
    market_observations: list[datetime] = []

    def check_market(source: dict, number: int) -> bool:
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
        prediction = prediction_by_number.get(str(number), {})
        if not _verified_odds_capture(source.get("odds_evidence"), race_id=race_info.get("race_id"),
                                      horse_number=number, odds=prediction.get("odds")):
            reject("odds_provenance_missing", "レース・馬番・実オッズに結び付く取得証拠が未確認")
            return False
        if observed > now or now - observed > MAX_ODDS_AGE or (post is not None and observed >= post):
            reject("odds_stale", "オッズを更新して再判定してください")
            return False
        verified_market_numbers.add(number)
        market_observations.append(observed)
        return True

    for number in numbers:
        check_market(source_map.get(str(number), {}), number)
    if any(_number(pred.get("odds")) is None or float(pred["odds"]) < 1 for pred in predictions):
        reject("odds_incomplete", "オッズが未取得または無効")

    candidate_tickets: list[dict] = []
    eligible = validation.get("eligible_bet_types", [])
    if not reason_codes:
        for prediction in predictions:
            if not fixed and "単勝" in eligible:
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
            normalized_members = list(members) if bet_type in ORDERED_TYPES else sorted(members)
            # A bad optional combination observation must not poison valid win
            # tickets; simply exclude that combination.
            observed = _instant(item.get("observed_at"))
            if (item.get("odds_status") != "middle" or observed is None or observed > now
                or now - observed > MAX_ODDS_AGE or (post is not None and observed >= post)
                or not _verified_joint_odds_capture(
                    item.get("odds_evidence"), race_id=race_info.get("race_id"),
                    bet_type=bet_type, horse_numbers=normalized_members, odds=odds,
                    observed_at=observed,
                )):
                continue
            candidate_tickets.append({"bet_type": bet_type, "horse_numbers": normalized_members, "probability": p, "odds": odds})

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
        # The chosen recipe uses fixed equal stakes, not individual-ticket Kelly.
        amount = _hundreds(cap / 3) if fixed else _hundreds(min(cap, kelly)) if use_kelly else min(cap, 100)
        if not dynamic_unit:
            amount = min(amount, 100)
        if amount < 100:
            continue
        selected.append({**ticket, "horse_numbers": members,
            "combination": ("→" if ticket["bet_type"] in ORDERED_TYPES else "-").join(map(str, members)),
            "expected_value": p * odds, "conservative_expected_value": ev,
            "stake": amount, "_conservative_return": ev})
    selected.sort(key=lambda ticket: (-ticket["conservative_expected_value"], ticket["bet_type"], ticket["horse_numbers"]))
    if fixed:
        by_key = {(t["bet_type"], tuple(t["horse_numbers"])): t for t in selected}
        selected = [by_key[key] for kind, members in fixed_book if (key := (kind, tuple(members))) in by_key]
        if len(selected) != 3:
            reject("fixed_book_unavailable", "②の3点すべての検証済み確率・発走前オッズ・期待値が揃いません")
            selected = []
    else:
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
    if fixed:
        unit = _hundreds(min(cap / 3, min((t["stake"] for t in selected), default=0)))
        tickets = [{**t, "stake": unit} for t in selected] if unit >= 100 and len(selected) == 3 else []
        if selected and not tickets:
            reject("fixed_budget_below_minimum", "②の3点を均等配分できる安全側の金額が不足しています")
    tickets = [{k: v for k, v in t.items() if not k.startswith("_")} for t in tickets]
    if not tickets and not reason_codes:
        reject("no_value_or_stake", "条件を満たす買い目なし")

    total = sum(ticket["stake"] for ticket in tickets)
    priority = bool(tickets) and (fixed or (
        tickets[0]["conservative_expected_value"] >= 1.5 and tickets[0]["probability"] >= 0.15))
    decision = "priority" if priority else "buy" if tickets else "skip"
    action = "勝負候補" if priority else "購入候補" if tickets else "見送り"
    reason = "安全側の期待値と予算条件を満たしています" if tickets else reasons[0]
    unit_prices = {ticket["stake"] for ticket in tickets}
    observation_expiries = [
        observed + MAX_ODDS_AGE for source in sources
        if (observed := _instant(source.get("observed_at"))) is not None
    ]
    if fixed and tickets:
        keys = {(t["bet_type"], tuple(t["horse_numbers"])) for t in tickets}
        observation_expiries += [
            observed + MAX_ODDS_AGE for t in context.get("joint_tickets", [])
            if (t.get("bet_type"), tuple(t.get("horse_numbers", []) if t.get("bet_type") in ORDERED_TYPES
                                        else sorted(t.get("horse_numbers", [])))) in keys
            and (observed := _instant(t.get("observed_at"))) is not None
        ]
    valid_until = min([deadline, *observation_expiries]) if deadline and observation_expiries else now
    recommendation = {
        "action": action, "decision": decision, "reason": reason, "reason_codes": reason_codes,
        "reasons": reasons, "policy_version": POLICY_VERSION,
        "betting_strategy": betting_strategy, "fixed_selection": fixed_selection,
        "input_quality": {
            "feature_missing_rate": missing_rate,
            "max_runner_missing_rate": max(
                (float(rate) for rate in runner_missing if _number(rate) is not None), default=None,
            ) if isinstance(runner_missing, list) else None,
            "all_missing_features": list(all_missing_features[:20]) if isinstance(all_missing_features, list) else [],
        },
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
        "odds_evidence": {
            "verified": len(verified_market_numbers) == len(numbers) and bool(numbers),
            "covered_horses": len(verified_market_numbers), "runner_count": len(numbers),
            "observed_at": min(market_observations).isoformat() if market_observations else None,
            "source": "netkeiba-win-odds-capture-v1",
        },
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
