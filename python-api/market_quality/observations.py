"""Phase-1 observation, probability and split audits; no model fitting or I/O.

All classifications are evidence-based. A timestamp from reacquiring a historic
result is never promoted to a contemporaneous observation. Callers must bind
evidence references/hashes to inspected source artifacts; declarations alone
are not cryptographic proof of the underlying HTTP capture.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import re
from collections.abc import Mapping

import numpy as np
import pandas as pd

JST = timezone(timedelta(hours=9))
PHASE_RANGES = {"calibration_seed": ("20210101", "20210630"),
                "meta_fit": ("20210701", "20210930"),
                "development": ("20211001", "20211231"),
                "validation": ("20220101", "20221231")}
ODDS_CLASSES = {"A": "timestamp_verified_pre_race", "B": "observed_pre_race_but_source_time_unknown",
                "C": "final_or_historical_odds", "D": "timing_unknown"}
OUTCOME_ONLY = {"finish", "is_win", "tansho_payout", "payout_confirmed_race", "h04_probability_fixed_reference"}
DEFAULT_COLUMNS = ["h04_score", "h04_probability", "odds", "popularity", "training_data", "speed_figures",
                   "horse_weight", "horse_weight_change", "safe_prior_starts", "safe_jockey", "safe_trainer",
                   "surface", "distance"]


def _scope(frame):
    required = {"race_id", "horse_number", "race_date"}
    if frame.empty or not required.issubset(frame) or frame.columns.duplicated().any():
        raise ValueError("nonempty-unique-column-research-frame-required")
    if frame[["race_id", "horse_number"]].isna().any().any() or frame.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("missing-or-duplicate-runner-key")
    dates = frame.race_date.astype(str)
    if not dates.str.fullmatch(r"202[12]\d{4}").all():
        raise ValueError("only-2021-2022-research-inputs-permitted")
    pd.to_datetime(dates, format="%Y%m%d", errors="raise")


def _aware(raw):
    if raw is None or (isinstance(raw, float) and np.isnan(raw)) or raw == "":
        return None
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return value if value.tzinfo is not None and value.utcoffset() is not None else None
    except (ValueError, TypeError):
        return None


def _present(raw):
    return raw is not None and not (isinstance(raw, float) and np.isnan(raw)) and str(raw).strip() != ""


def classify_odds_evidence(race_date, evidence):
    """No guessed source timestamps. C takes priority over a later receipt time."""
    evidence = dict(evidence or {})
    reference = str(evidence.get("evidence_ref") or "")
    historical = (evidence.get("historical_source_verified") is True
                  and evidence.get("source_kind") in {"historical_result", "historical_result_sqlite", "result_page", "historical_research_proxy"}
                  and bool(reference))
    if historical:
        return "C", "verified_historical_source_not_contemporaneous"
    # A/B require independently bound current-card odds, not arbitrary DB dates.
    direct = (evidence.get("evidence_verified") is True and evidence.get("odds_binding_verified") is True
              and evidence.get("capture_kind") == "direct_fetch" and evidence.get("http_status") == 200
              and re.fullmatch(r"[0-9a-fA-F]{64}", str(evidence.get("source_sha256") or ""))
              and bool(reference) and bool(evidence.get("source_url")))
    if not direct:
        return "D", "pre_race_observation_evidence_unproven"
    observed, start = _aware(evidence.get("observed_at")), _aware(evidence.get("race_start_at"))
    if start is None or observed is None or start.astimezone(JST).strftime("%Y%m%d") != str(race_date):
        return "D", "missing_invalid_or_wrong_date_observation_time"
    if observed >= start:
        return "D", "observation_not_before_target_race"
    raw_source = evidence.get("source_time")
    if not _present(raw_source):
        return "B", "bound_direct_observation_pre_race_provider_time_unknown"
    source = _aware(raw_source)
    if source is None or source > observed or source >= start:
        return "D", "source_time_invalid_or_inconsistent"
    return "A", "bound_provider_and_observation_times_before_target_race"


def audit_odds(frame, evidence=None, *, tolerance=1e-12):
    """Return per-runner odds quality and a JSON-safe aggregate, without writes.

    evidence: one-to-one runner-key DataFrame or a verified common provenance
    mapping. A common historic provenance maps all runners to C, never to T-5.
    """
    _scope(frame)
    if not np.isfinite(tolerance) or tolerance < 0:
        raise ValueError("nonnegative-finite-comparison-tolerance-required")
    work = frame.reset_index(drop=True).copy()
    if evidence is None or isinstance(evidence, Mapping):
        evidence_rows = [dict(evidence or {})] * len(work)
    else:
        keys = ["race_id", "horse_number"]
        if not set(keys).issubset(evidence) or evidence.duplicated(keys).any():
            raise ValueError("evidence-needs-unique-runner-keys")
        extra = evidence[keys].merge(work[keys], how="left", on=keys, indicator=True)
        if extra._merge.ne("both").any():
            raise ValueError("out-of-cohort-odds-evidence")
        evidence_rows = work[keys].merge(evidence, how="left", on=keys, validate="one_to_one", sort=False).to_dict("records")
    values = pd.to_numeric(work.get("odds", pd.Series(np.nan, index=work.index)), errors="coerce").astype(float)
    invalid = ~np.isfinite(values) | values.lt(1)
    implied = (1 / values).where(~invalid)
    ids = work.race_id
    group_invalid = invalid.groupby(ids).transform("any")
    if "expected_runner_count" in work:
        incomplete = ~work.expected_runner_count.eq(ids.groupby(ids).transform("size"))
    else:
        incomplete = pd.Series(True, index=work.index)
    date_conflict = ids.map(work.groupby("race_id").race_date.nunique()).ne(1)
    unavailable = (group_invalid | incomplete | date_conflict).groupby(ids).transform("any")
    normalized = (implied / implied.groupby(ids).transform("sum")).where(~unavailable)
    old_implied = pd.to_numeric(work.get("implied_probability", pd.Series(np.nan, index=work.index)), errors="coerce")
    old_normalized = pd.to_numeric(work.get("normalized_market_probability", pd.Series(np.nan, index=work.index)), errors="coerce")
    raw_diff = abs(implied - old_implied)
    norm_diff = abs(normalized - old_normalized)
    raw_mismatch = implied.notna() & (~np.isfinite(old_implied) | raw_diff.gt(tolerance))
    norm_mismatch = normalized.notna() & (~np.isfinite(old_normalized) | norm_diff.gt(tolerance))
    classifications = [classify_odds_evidence(date, item) for date, item in zip(work.race_date, evidence_rows)]
    output = work[["race_id", "race_date", "horse_number"]].copy()
    output["odds"] = values
    output["odds_quality"] = [c[0] for c in classifications]
    output["odds_quality_label"] = output.odds_quality.map(ODDS_CLASSES)
    output["classification_reason"] = [c[1] for c in classifications]
    output["evidence_ref"] = [str(item.get("evidence_ref") or "") for item in evidence_rows]
    for name in ("source_kind", "source_url", "source_sha256", "source_time", "observed_at", "race_start_at"):
        output[name] = [item.get(name) for item in evidence_rows]
    for name, data in {"invalid_odds": invalid, "zero_or_negative_odds": values.le(0),
                       "raw_implied_recomputed": implied, "market_probability_recomputed": normalized,
                       "raw_implied_absolute_difference": raw_diff, "market_probability_absolute_difference": norm_diff,
                       "raw_implied_mismatch": raw_mismatch, "market_probability_mismatch": norm_mismatch,
                       "normalization_unavailable": unavailable}.items():
        output[name] = data
    quality = {c: {"runners": int(output.odds_quality.eq(c).sum()),
                   "races_with_any": int(output.loc[output.odds_quality.eq(c), "race_id"].nunique())} for c in ODDS_CLASSES}
    maximum = lambda series: float(series.max()) if series.notna().any() else None
    return output, {"schema": "market-odds-observation-audit-v1", "runners": len(work), "races": int(ids.nunique()),
                    "classification": quality, "classification_complete": not output.odds_quality.eq("D").any(),
                    "invalid_odds": int(invalid.sum()), "zero_or_negative_odds": int(values.le(0).sum()),
                    "nonfinite_odds": int((~np.isfinite(values)).sum()),
                    "normalization_unavailable_races": int(ids[unavailable].nunique()),
                    "raw_implied_max_absolute_difference": maximum(raw_diff),
                    "normalized_max_absolute_difference": maximum(norm_diff),
                    "raw_implied_mismatched_runners": int(raw_mismatch.sum()),
                    "raw_implied_mismatched_races": int(ids[raw_mismatch].nunique()),
                    "normalized_mismatched_runners": int(norm_mismatch.sum()),
                    "normalized_mismatched_races": int(ids[norm_mismatch].nunique()),
                    "comparison_tolerance": tolerance, "historical_odds_are_T5": False,
                    "quality_classification_is_not_payout_or_lineage_gate": True}


def audit_calibration_split(frame, seed_calibrator, provenance, model_feature_columns, *, tolerance=1e-12):
    """Replay the frozen transform and seed hash; never fit/recalibrate anything."""
    _scope(frame)
    from prospective.calibration import _normalize, _hash_frame, _FIT_COLUMNS, transform_probabilities
    issues, checks, phases = [], {}, {}
    if "phase" not in frame:
        return {"passed": False, "issues": ["phase_column_missing"], "phases": {}}
    work = frame.reset_index(drop=True).copy()
    checks["no_cross_phase_races"] = bool(work.groupby("race_id").phase.nunique(dropna=False).eq(1).all())
    checks["all_four_phases_only"] = set(work.phase) == set(PHASE_RANGES)
    checks["phase_dates_correct"] = True
    for name, (low, high) in PHASE_RANGES.items():
        part = work.loc[work.phase.eq(name)]
        correct = bool(len(part) and part.race_date.astype(str).between(low, high).all())
        checks["phase_dates_correct"] &= correct
        phases[name] = {"runners": len(part), "races": int(part.race_id.nunique()),
                        "date_from": str(part.race_date.min()) if len(part) else None,
                        "date_to": str(part.race_date.max()) if len(part) else None,
                        "allowed_date_from": low, "allowed_date_to": high, "dates_valid": correct}
    groups = model_feature_columns if isinstance(model_feature_columns, Mapping) else {"model": model_feature_columns}
    forbidden = {name: sorted(set(columns) & OUTCOME_ONLY) for name, columns in groups.items()}
    checks["model_feature_manifests_supplied"] = bool(groups) and all(bool(cols) for cols in groups.values())
    checks["reference_and_outcomes_not_model_features"] = not any(forbidden.values())
    checks["provenance_reference_evaluation_only"] = (
        "h04_probability_fixed_reference" not in work
        or ("h04_probability_fixed_reference" in provenance.get("evaluation_only_columns", [])
            and "h04_probability_fixed_reference" not in provenance.get("combined_features", [])
            and "h04_probability_fixed_reference" not in provenance.get("market_features", [])))
    seed = work.loc[work.phase.eq("calibration_seed")].copy()
    later = work.loc[~work.phase.eq("calibration_seed")].copy()
    checks["seed_probability_not_reused_insample"] = bool(len(seed) and "h04_probability" in seed and seed.h04_probability.isna().all())
    checks["seed_family_and_no_market_fit"] = seed_calibrator.get("family") == "global_temperature" and seed_calibrator.get("market_data_used") is False
    checks["seed_fit_period_exact"] = bool(len(seed) and seed_calibrator.get("fit_years") == [2021]
        and seed_calibrator.get("fit_date_from") == seed.race_date.min()
        and seed_calibrator.get("fit_date_to") == seed.race_date.max()
        and "20210101" <= str(seed_calibrator.get("fit_date_from", "")) <= str(seed_calibrator.get("fit_date_to", "")) <= "20210630")
    glob = seed_calibrator.get("global", {})
    checks["seed_fit_cohort_exact"] = glob.get("fit_runners") == len(seed) and glob.get("fit_races") == seed.race_id.nunique()
    replay_difference, actual_hash = None, None
    try:
        seed["prediction"] = seed.h04_score
        normalized = _normalize(seed, require_oof=True)
        actual_hash = _hash_frame(normalized, _FIT_COLUMNS)
        checks["seed_input_sha256_exact"] = actual_hash == seed_calibrator.get("input_sha256")
        # Transform only later rows. Frozen reference is not read as a predictor.
        later["prediction"] = later.h04_score
        scored = transform_probabilities(later, seed_calibrator)
        expected = later.set_index(["race_id", "horse_number"]).h04_probability
        replay = scored.set_index(["race_id", "horse_number"]).win_probability.reindex(expected.index)
        delta = abs(replay - expected)
        replay_difference = float(delta.max()) if len(delta) and np.isfinite(delta).all() else None
        checks["later_probabilities_match_frozen_seed"] = replay_difference is not None and replay_difference <= tolerance
    except (ValueError, KeyError, AttributeError, TypeError) as exc:
        checks["seed_input_sha256_exact"] = False
        checks["later_probabilities_match_frozen_seed"] = False
        issues.append("calibration_replay_unavailable:" + type(exc).__name__ + ":" + str(exc))
    issues.extend(name for name, passed in checks.items() if not passed)
    return {"schema": "market-calibration-split-audit-v1", "passed": all(checks.values()), "checks": checks,
            "issues": issues, "phases": phases, "forbidden_feature_columns_by_model": forbidden,
            "seed_input_sha256_recomputed": actual_hash,
            "seed_input_sha256_declared": seed_calibrator.get("input_sha256"),
            "later_probability_max_absolute_difference": replay_difference, "comparison_tolerance": tolerance,
            "calibrator_fit_performed": False, "reference_used_for_model_or_gate": False,
            "validation_label": "2022 research-known validation, not independent confirmation"}


def audit_missingness(frame, columns=None, feature_specs=None):
    """Counts are nonexclusive; reasons are assigned only with explicit evidence.

    feature_specs[col]: kind=numeric|categorical, min/max/integer, sentinels;
    missing_reason + reason_evidence may justify a source-level absence. Without
    that evidence the reason is reason_unknown, never an assumed parser failure.
    """
    _scope(frame)
    columns = list(DEFAULT_COLUMNS if columns is None else columns)
    if len(set(columns)) != len(columns):
        raise ValueError("unique-missingness-columns-required")
    specs = feature_specs or {}
    records = []
    for name in columns:
        spec = dict(specs.get(name, {}))
        exists = name in frame
        raw = frame[name] if exists else pd.Series(np.nan, index=frame.index)
        text = raw.astype("string").str.strip()
        missing = raw.isna() | text.eq("").fillna(False)
        sentinels = spec.get("sentinels", ["", "-", "--", "N/A", "null", "unknown"])
        sentinel = raw.isin(sentinels) | text.isin([str(x) for x in sentinels])
        numeric = pd.to_numeric(raw, errors="coerce")
        kind = spec.get("kind", "categorical" if name in {"surface", "safe_jockey", "safe_trainer", "jockey_id", "trainer_id"} else "numeric")
        invalid = pd.Series(False, index=frame.index)
        if kind == "numeric":
            invalid |= ~missing & (~np.isfinite(numeric) | sentinel)
            minimum = spec.get("min", 1 if name in {"odds", "popularity", "horse_weight", "distance"} else None)
            if minimum is not None:
                invalid |= ~missing & numeric.lt(minimum)
            if spec.get("max") is not None:
                invalid |= ~missing & numeric.gt(spec["max"])
            if spec.get("integer", name == "popularity"):
                invalid |= ~missing & numeric.notna() & numeric.ne(np.floor(numeric))
            if name == "h04_probability":
                invalid |= ~missing & ~numeric.between(0, 1)
        elif kind == "categorical":
            invalid |= ~missing & sentinel
        else:
            raise ValueError("unknown-feature-kind:" + name)
        reasons = Counter()
        unresolved = missing | invalid | sentinel
        legitimate = pd.Series(False, index=frame.index)
        if name in {"h04_probability", "probability_difference"} and "phase" in frame:
            legitimate = missing & frame.phase.eq("calibration_seed") & frame.race_date.astype(str).between("20210101", "20210630")
            reasons["legitimate_missing"] += int(legitimate.sum())
        remaining = unresolved & ~legitimate
        reason = spec.get("missing_reason")
        if reason in {"legitimate_missing", "source_missing", "parser_failure", "historical_unavailable", "conflict"} and spec.get("reason_evidence"):
            # Source-level missing evidence does not explain malformed nonmissing values.
            explained = remaining & missing
            reasons[reason] += int(explained.sum())
            remaining &= ~explained
        reasons["reason_unknown"] += int(remaining.sum())
        records.append({"feature": name, "present_in_frame": exists, "kind": kind, "rows": len(frame),
                        "missing_count": int(missing.sum()), "missing_rate": float(missing.mean()),
                        "invalid_count": int(invalid.sum()), "zero_count": int(numeric.eq(0).sum()),
                        "sentinel_count": int(sentinel.sum()), "issue_union_count": int(unresolved.sum()),
                        "missing_or_invalid_reason_counts": json.dumps(dict(reasons), sort_keys=True),
                        "reason_evidence": str(spec.get("reason_evidence") or ""),
                        "count_semantics": "missing/invalid/zero/sentinel are nonexclusive; zero alone is not an error"})
    return pd.DataFrame(records)
