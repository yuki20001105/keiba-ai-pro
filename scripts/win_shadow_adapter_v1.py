"""Pure research-only comparison of an identically calibrated market and saved NLL model.

No fetch, DB write, model fit, stake, EV, purchase or production publication API.
The evidence loader seals files; this adapter validates its exact binding before
using the frozen 46+4 generator and frozen TRAIN-only encoder. Retrospective
connection replay cannot acquire prospective, freshness or profit authority.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

import db_shared_win_four_worker as worker
import db_shared_win_direct_features as features

SCHEMA = "win-shadow-input-v1"
MODE = "retrospective_connection_replay"
CANDIDATE = "expanding_race_nll"
FOLD = 2026
GENERATOR_VERSION = "frozen-db-shared-46-plus-market4-v1"
OUTCOMES = frozenset({"finish", "finish_position", "finish_time", "time_seconds",
    "last_3f_time", "margin", "tansho_payout", "speed_deviation", "result", "payout",
    "finish_order", "actual_finish", "actual_return", "is_hit", "race_payout_complete"})
METADATA = ("race_id", "race_date", "horse_number", "horse_id", "venue", "surface", "num_horses", "expected_runner_count", "odds")


def instant(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("naive")
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError) as error:
        raise ValueError("shadow-timezone-aware-timestamp-required") from error


def validate_market_calibration(settings):
    import math
    if (not isinstance(settings, dict) or set(settings) != {"kind", "a", "b"}
            or settings["kind"] != worker.direct.CALIBRATION_SCHEMA
            or any(type(settings[key]) not in (int, float) or not math.isfinite(settings[key]) for key in ("a", "b"))
            or not .5 <= settings["a"] <= 2. or not -1. <= settings["b"] <= 1.):
        raise ValueError("shadow-identical-bounded-market-calibration-required")


def validate_shadow_input(frame, receipt, *, expected_input_sha256, mode=MODE):
    """Validate a hash-bound whole card, quote vector, generator and prior history.

    Inherent historical missing values remain NaN under the original encoder;
    missing columns, identities, current required fields or quotes are rejected.
    This phase intentionally has no live mode until a future target is fixed.
    """
    import numpy as np
    import pandas as pd
    if mode != MODE:
        raise ValueError("shadow-future-target-not-yet-registered")
    if (not isinstance(receipt, dict) or receipt.get("schema") != SCHEMA
            or not isinstance(expected_input_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected_input_sha256) is None
            or worker.canonical_hash(receipt) != expected_input_sha256):
        raise ValueError("shadow-registered-input-evidence-binding-required")
    columns, _ = worker.feature_contract(CANDIDATE)
    if (not isinstance(frame, pd.DataFrame) or frame.empty or frame.columns.duplicated().any()
            or set(frame) != set(columns + list(METADATA))
            or OUTCOMES & set(frame)
            or worker.frame_hash(frame) != receipt.get("frame_sha256")
            or receipt.get("columns") != columns or receipt.get("generator_version") != GENERATOR_VERSION):
        raise ValueError("shadow-frozen50-outcome-free-frame-required")
    worker.validate_frame(frame, require_results=False)
    if frame.race_id.nunique() != 1 or frame.race_date.nunique() != 1:
        raise ValueError("shadow-single-whole-target-race-required")
    target, day = str(frame.race_id.iloc[0]), str(frame.race_date.iloc[0])
    if (receipt.get("race_id") != target or receipt.get("race_date") != day
            or not "01" <= target[4:6] <= "10" or not 1 <= int(target[-2:]) <= 12
            or frame.surface.iloc[0] not in ("turf", "dirt")):
        raise ValueError("shadow-target-identity-or-flat-surface-required")
    identities = frame[["race_id", "race_date", "horse_number", "horse_id"]].to_dict("records")
    if (receipt.get("identities") != identities or frame.horse_id.duplicated().any()
            or not frame.horse_id.map(lambda value: isinstance(value, str) and bool(value.strip())).to_numpy(dtype=bool).all()):
        raise ValueError("shadow-full-horse-identity-binding-required")
    formula = receipt.get("formula_receipt", {})
    if not isinstance(formula, dict) or not isinstance(formula.get("base46_parity"), dict):
        raise ValueError("shadow-formula-receipt-mapping-required")
    if (formula.get("base46_parity", {}).get("verified") is not True
            or formula.get("source_hashes") != receipt.get("generator_source_hashes")
            or receipt.get("generator_source_hashes") != features.source_hashes()
            or formula.get("history_contract") != features.HISTORY_CONTRACT
            or any(formula.get(key) is not False for key in
                   ("input_outcomes_consumed", "same_day_or_future_history_used", "dummy_current_results_used"))):
        raise ValueError("shadow-generator-version-parity-or-source-mismatch")
    history = receipt.get("history", {})
    if not isinstance(history, dict):
        raise ValueError("shadow-history-receipt-mapping-required")
    minimum, maximum = history.get("min_date"), history.get("max_date")
    if (type(history.get("rows")) is not int or history["rows"] <= 0
            or not isinstance(minimum, str) or not isinstance(maximum, str)
            or re.fullmatch(r"\d{8}", minimum) is None or re.fullmatch(r"\d{8}", maximum) is None
            or not "20130101" <= minimum <= maximum < day
            or history.get("strict_prior_day_verified") is not True
            or history.get("target_or_future_rows") != 0
            or any(re.fullmatch(r"[0-9a-f]{64}", str(receipt.get(key, ""))) is None
                   for key in ("capture_manifest_sha256", "capture_bundle_sha256", "history_sha256", "canonical_sha256"))):
        raise ValueError("shadow-strict-prior-history-and-sealed-objects-required")
    capture = receipt.get("capture", {})
    if not isinstance(capture, dict):
        raise ValueError("shadow-capture-receipt-mapping-required")
    if (capture.get("source_mode") != "live" or capture.get("point_in_time_verified") is not True
            or capture.get("odds_kind") != "actual_market_single_win" or capture.get("odds_status") != "middle"
            or capture.get("quote_key_join_verified") is not True):
        raise ValueError("shadow-prestart-card-and-actual-middle-quotes-required")
    post, captured, generated = (instant(capture.get(name)) for name in ("post_time", "captured_at", "feature_generated_at"))
    opened, deadline = (instant(capture.get(name)) for name in ("window_opens_at", "window_deadline"))
    if (post.astimezone(timezone(timedelta(hours=9))).strftime("%Y%m%d") != day
            or not opened <= generated <= captured <= deadline < post):
        raise ValueError("shadow-feature-input-after-window-or-post")
    sources = capture.get("source_receipts")
    if (not isinstance(sources, list) or len(sources) != 2 or not all(isinstance(row, dict) for row in sources)
            or {row.get("kind") for row in sources} != {"card", "odds"}):
        raise ValueError("shadow-two-exact-source-receipts-required")
    observed = []
    for source in sources:
        start, seen = instant(source.get("request_started_at")), instant(source.get("observed_at"))
        if (source.get("status") != 200 or source.get("source") != "network"
                or not opened <= start <= seen <= captured < post
                or (captured - seen).total_seconds() > 300
                or re.fullmatch(r"[0-9a-f]{64}", str(source.get("body_sha256", ""))) is None):
            raise ValueError("shadow-prestart-network-source-clock-required")
        from research_capture import prestart
        if (prestart._source_kind(source.get("url", ""), target) != source["kind"]
                or prestart._source_kind(source.get("request_url", ""), target) != source["kind"]):
            raise ValueError("shadow-source-url-target-conflict")
        observed.append(seen)
    if (max(observed) - min(observed)).total_seconds() > 60:
        raise ValueError("shadow-card-quote-skew-limit")
    # All four market features must be derived from this same full quote vector.
    quoted = features.add_market_features(frame)
    if not np.allclose(frame[columns[:4]].to_numpy(float), quoted[columns[:4]].to_numpy(float), rtol=0., atol=1e-12):
        raise ValueError("shadow-market4-not-derived-from-exact-quotes")
    current = [name for name in columns if name.startswith("safe_") and name in
               ("safe_distance", "safe_num_horses", "safe_horse_number", "safe_age", "safe_burden_weight")]
    if not np.isfinite(frame[current].to_numpy(float)).all():
        raise ValueError("shadow-required-current-feature-missing")
    for safe, meta in (("safe_surface", "surface"), ("safe_venue", "venue"), ("safe_horse_number", "horse_number"),
                       ("safe_num_horses", "expected_runner_count")):
        if not frame[safe].astype(str).equals(frame[meta].astype(str)) and not np.array_equal(frame[safe].to_numpy(), frame[meta].to_numpy()):
            raise ValueError("shadow-current-feature-metadata-conflict")
    return {"status": "retrospective_connection_inputs_verified", "runner_count": len(frame),
        "strict_prior_history": True, "formula_parity": True,
        "history_max_source_date": maximum, "history_lag_days": (datetime.strptime(day, "%Y%m%d") - datetime.strptime(maximum, "%Y%m%d")).days,
        "history_freshness_verified": history.get("freshness_verified") is True,
        "complete_career_coverage_verified": history.get("complete_career_coverage_verified") is True,
        "prospective_validation_pass": False, "eligible_for_ev": False}


def compare_shadow(frame, input_receipt, candidate_bundle, market_calibration, *, expected_input_sha256, mode=MODE):
    """Run the two fixed models on exactly the same input rows; never place bets."""
    import numpy as np
    validation = validate_shadow_input(frame, input_receipt, expected_input_sha256=expected_input_sha256, mode=mode)
    validate_market_calibration(market_calibration)
    worker.validate_bundle(candidate_bundle)
    if candidate_bundle.get("candidate_id") != CANDIDATE or candidate_bundle.get("fold_year") != FOLD:
        raise ValueError("shadow-frozen2026-expanding-race-nll-required")
    components = worker.predict_components(candidate_bundle, frame)
    market = worker.market_values(frame)
    baseline = worker.conditioned_probabilities(frame, market, market, market_calibration["a"], market_calibration["b"])
    rows = frame[["race_id", "race_date", "horse_id", "horse_number", "odds"]].copy()
    for name, values in (("market_only_calibrated", baseline), (CANDIDATE, components["probability"])):
        values = np.asarray(values, float)
        if (values.shape != (len(frame),) or not np.isfinite(values).all() or (values <= 0).any()
                or (values >= 1).any() or not np.isclose(values.sum(), 1., rtol=0., atol=1e-10)):
            raise ValueError("shadow-complete-normalized-probability-required")
        rows[name] = values
    return {"schema": "win-shadow-comparison-v1", "mode": mode, "validation": validation,
        "input_receipt_sha256": expected_input_sha256, "predictions": rows.to_dict("records"),
        "prediction_sha256": worker.canonical_hash(rows.to_dict("records")),
        "same_input_rows_for_both_models": True, "model_fits_started": 0,
        "source_claims_upgraded": False, "model_inference_before_post_verified": False,
        "unknown_future_validation": False, "prospective_validation_pass": False,
        "research_only": True, "shadow_only": True, "reference_only": True,
        "expected_values_display_authorized": False, "eligible_for_ev": False,
        "purchase_approved": False, "actual_purchases": 0, "stakes_assigned": False,
        "model_activated": False, "forward_modified": False}
