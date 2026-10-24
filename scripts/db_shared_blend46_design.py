"""Pure contracts for the fixed 46-feature global/venue/50% blend study.

No fitting, database access, file writes, tuning, publication or betting.
Historical folds remain retrospective: the 46 columns were already selected
using previously inspected history, so this is not an unbiased new outer CV.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
from numbers import Real
import re

import numpy as np
import pandas as pd


STRATEGIES = ("global", "venue_only", "blend50")
REMOVED_FEATURE = "safe_past5_top3_rate"
CANONICAL46 = tuple(name for name in (
    "safe_distance", "safe_num_horses", "safe_horse_number", "safe_bracket_number",
    "safe_age", "safe_burden_weight", "safe_horse_weight", "safe_weight_change",
    "safe_month", "safe_race_number", "safe_surface", "safe_venue", "safe_sex",
    "safe_field_condition", "safe_weather", "safe_race_class",
    "safe_prior_starts", "safe_days_since_last",
) + tuple(f"safe_{prefix}_{field}" for prefix in ("prev", "prev2")
          for field in ("finish", "time_seconds", "distance", "speed_mps")) + tuple(
    f"safe_past{window}_{field}" for window in (3, 5, 10)
    for field in ("count", "observed_coverage", "avg_finish", "avg_speed_mps",
                  "avg_last3f_time", "win_rate", "top3_rate")) if name != REMOVED_FEATURE)
PRIMARY_COMPARISON_FAMILY_SIZE = 4
PRIMARY_SIMULTANEOUS_CONFIDENCE = 1.0 - .05 / PRIMARY_COMPARISON_FAMILY_SIZE
_ACCURACY_COLUMNS = {
    "fold_id", "test_year", "strategy", "race_id", "race_date", "venue",
    "runner_count", "rank_correlation", "squared_speed_error_sum",
}
_PROBABILITY_COLUMNS = {
    "probability_eligible", "nll", "brier", "uniform_nll", "uniform_brier",
    "top1_confidence", "top1_correct",
}


def canonical_hash(value):
    """Hash JSON protocol objects; NaN and infinity are forbidden."""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_feature_columns(columns):
    if not isinstance(columns, (list, tuple)) or tuple(columns) != CANONICAL46:
        raise ValueError("blend46-exact-canonical-feature-order-required")
    return tuple(columns)


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        raise ValueError("blend46-valid-date-required")
    try:
        parsed = datetime.strptime(value, "%Y%m%d")
    except ValueError as error:
        raise ValueError("blend46-valid-date-required") from error
    if parsed.strftime("%Y%m%d") != value:
        raise ValueError("blend46-valid-date-required")
    return value


def _finite_number(value):
    return isinstance(value, Real) and not isinstance(value, (bool, np.bool_)) and math.isfinite(float(value))


def build_folds():
    """Fixed expanding TRAIN / previous-year CAL / annual TEST, eleven folds."""
    return [{
        "fold_id": f"Y{year}", "test_year": year,
        "train_date_from": "20130101", "train_date_to": f"{year - 2}1231",
        "cal_date_from": f"{year - 1}0101", "cal_date_to": f"{year - 1}1231",
        "test_date_from": f"{year}0101",
        "test_date_to": "20260913" if year == 2026 else f"{year}1231",
    } for year in range(2016, 2027)]


def validate_fold(fold):
    if not isinstance(fold, dict) or fold not in build_folds():
        raise ValueError("blend46-fixed-annual-fold-required")
    return dict(fold)


def validate_fold_target(target, fold):
    """Return integer row positions, preserving whole races and whole dates."""
    validate_fold(fold)
    required = {"race_id", "race_date", "horse_number"}
    if not isinstance(target, pd.DataFrame) or target.empty or not required.issubset(target.columns):
        raise ValueError("blend46-target-keys-required")
    if target[list(required)].isna().any().any():
        raise ValueError("blend46-target-keys-missing")
    frame = target.reset_index(drop=True)
    if not frame.race_id.map(lambda value: isinstance(value, str) and bool(value.strip())).all():
        raise ValueError("blend46-target-race-identity-invalid")
    for date in frame.race_date.unique():
        _date(date)
    numbers = pd.to_numeric(frame.horse_number, errors="coerce").to_numpy(float)
    if (not np.isfinite(numbers).all() or (numbers < 1).any() or (numbers != np.floor(numbers)).any()
            or frame.duplicated(["race_id", "horse_number"]).any()):
        raise ValueError("blend46-target-runner-identity-invalid-or-duplicate")
    for column in ("race_date", "venue", "surface"):
        if column in frame and frame.groupby("race_id")[column].nunique(dropna=False).ne(1).any():
            raise ValueError("blend46-target-inconsistent-race-metadata")
    if frame.groupby("race_id").size().lt(2).any():
        raise ValueError("blend46-target-whole-race-required")
    if "num_horses" in frame:
        sizes = frame.groupby("race_id").race_id.transform("size")
        supplied = pd.to_numeric(frame.num_horses, errors="coerce")
        if supplied.isna().any() or supplied.ne(sizes).any():
            raise ValueError("blend46-target-incomplete-runner-cohort")
    positions = {}
    for partition in ("TRAIN", "CAL", "TEST"):
        lower, upper = (fold[f"{partition.lower()}_date_{boundary}"] for boundary in ("from", "to"))
        positions[partition] = np.flatnonzero(frame.race_date.ge(lower).to_numpy() & frame.race_date.le(upper).to_numpy())
        if not len(positions[partition]):
            raise ValueError(f"blend46-empty-{partition.lower()}-partition")
    sets = {name: set(frame.iloc[index].race_id) for name, index in positions.items()}
    if (sets["TRAIN"] & sets["CAL"] or sets["TRAIN"] & sets["TEST"] or sets["CAL"] & sets["TEST"]):
        raise ValueError("blend46-partition-race-overlap")
    if not (frame.iloc[positions["TRAIN"]].race_date.max() < frame.iloc[positions["CAL"]].race_date.min()
            <= frame.iloc[positions["CAL"]].race_date.max() < frame.iloc[positions["TEST"]].race_date.min()):
        raise ValueError("blend46-partition-chronology-invalid")
    return positions


def specialist_eligibility(frame, minimum_races=500, minimum_runners=2500, minimum_dates=40):
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1
           for value in (minimum_races, minimum_runners, minimum_dates)):
        raise ValueError("blend46-specialist-minimums-invalid")
    if not isinstance(frame, pd.DataFrame) or not {"race_id", "race_date"}.issubset(frame.columns):
        raise ValueError("blend46-specialist-training-keys-required")
    races, dates = int(frame.race_id.nunique()), int(frame.race_date.nunique())
    reasons = []
    if races < minimum_races:
        reasons.append("insufficient_complete_training_races")
    if len(frame) < minimum_runners:
        reasons.append("insufficient_training_runners")
    if dates < minimum_dates:
        reasons.append("insufficient_training_dates")
    return {"available": not reasons, "reason_codes": reasons,
            "training_race_count": races, "training_runner_count": len(frame), "training_date_count": dates,
            "minimum_races": minimum_races, "minimum_runners": minimum_runners, "minimum_dates": minimum_dates}


def _validated_records(records, split="TEST"):
    if split not in ("CAL", "TEST"):
        raise ValueError("blend46-record-split-invalid")
    if not isinstance(records, pd.DataFrame) or records.empty or not _ACCURACY_COLUMNS.issubset(records.columns):
        raise ValueError("blend46-race-metric-columns-required")
    frame = records.copy().reset_index(drop=True)
    if frame[list(_ACCURACY_COLUMNS)].isna().any().any():
        raise ValueError("blend46-race-metric-values-missing")
    if "split" in frame and not frame["split"].eq(split).all():
        raise ValueError("blend46-mixed-or-leaked-record-split")
    if not frame.strategy.isin(STRATEGIES).all():
        raise ValueError("blend46-record-strategy-invalid")
    for name in ("race_id", "venue", "fold_id"):
        if not frame[name].map(lambda value: isinstance(value, str) and bool(value.strip())).all():
            raise ValueError("blend46-record-identities-invalid")
    folds = {fold["fold_id"]: fold for fold in build_folds()}
    for fold_id, group in frame.groupby("fold_id", sort=True):
        if fold_id not in folds:
            raise ValueError("blend46-record-fold-invalid")
        fold = folds[fold_id]
        if not group.test_year.map(lambda value: _finite_number(value) and float(value).is_integer()
                                   and int(value) == fold["test_year"]).all():
            raise ValueError("blend46-record-fold-year-mismatch")
        for date in group.race_date.unique():
            _date(date)
        if (group.race_date.lt(fold[f"{split.lower()}_date_from"]).any()
                or group.race_date.gt(fold[f"{split.lower()}_date_to"]).any()):
            raise ValueError("blend46-record-date-outside-fold")
    for column in ("runner_count", "rank_correlation", "squared_speed_error_sum"):
        if not frame[column].map(_finite_number).all():
            raise ValueError("blend46-record-numeric-metric-invalid")
    if (frame.runner_count.lt(2).any() or frame.runner_count.mod(1).ne(0).any()
            or frame.rank_correlation.abs().gt(1. + 1e-12).any()
            or frame.squared_speed_error_sum.lt(0).any()):
        raise ValueError("blend46-record-numeric-metric-out-of-range")
    if frame.duplicated(["fold_id", "strategy", "race_id"]).any():
        raise ValueError("blend46-record-duplicate-strategy-race")
    return frame


def validate_metric_cohort(records, split="TEST", require_all_folds=False):
    """Reject missing races/strategies and metadata disagreement before pooling."""
    frame = _validated_records(records, split)
    if require_all_folds and set(frame.fold_id) != {fold["fold_id"] for fold in build_folds()}:
        raise ValueError("blend46-record-all-eleven-folds-required")
    expected = frame.loc[frame.strategy.eq("global")]
    if expected.empty:
        raise ValueError("blend46-record-global-baseline-required")
    keys = ["fold_id", "race_id"]
    metadata = ["test_year", "race_date", "venue", "runner_count"]
    if "surface" in frame:
        metadata.append("surface")
    expected_keys = set(map(tuple, expected[keys].to_numpy()))
    for strategy in STRATEGIES:
        actual = frame.loc[frame.strategy.eq(strategy)]
        if set(map(tuple, actual[keys].to_numpy())) != expected_keys or len(actual) != len(expected):
            raise ValueError("blend46-record-exact-shared-race-cohort-required")
        paired = actual[keys + metadata].merge(expected[keys + metadata], on=keys, validate="one_to_one",
                                              suffixes=("", "_global"))
        for name in metadata:
            if not paired[name].eq(paired[f"{name}_global"]).all():
                raise ValueError("blend46-record-cohort-metadata-mismatch")
    return frame


def cohort_hash(records, split="TEST"):
    frame = _validated_records(records, split)
    # Strategy-free cohort can be compared across independently written files.
    keys = ["fold_id", "test_year", "race_id", "race_date", "venue", "runner_count"]
    if "surface" in frame:
        keys.append("surface")
    values = frame[keys].drop_duplicates().sort_values(["fold_id", "race_id"], kind="stable")
    return canonical_hash(values.to_dict(orient="records"))


def accuracy_summary(records, split="TEST"):
    frame = _validated_records(records, split)
    if frame.strategy.nunique() != 1:
        raise ValueError("blend46-summary-one-strategy-required")
    runners = int(frame.runner_count.sum())
    result = {"strategy": str(frame.strategy.iloc[0]), "race_count": len(frame), "runner_count": runners,
              "date_count": int(frame.race_date.nunique()), "fold_count": int(frame.fold_id.nunique()),
              "rank_correlation": float(frame.rank_correlation.mean()),
              "squared_speed_error_sum": float(frame.squared_speed_error_sum.sum()),
              "rmse_speed_mps": float(np.sqrt(frame.squared_speed_error_sum.sum() / runners)),
              "date_from": str(frame.race_date.min()), "date_to": str(frame.race_date.max()),
              "cohort_sha256": cohort_hash(frame, split),
              "aggregation": "race-macro Spearman; runner-pooled physical speed RMSE m/s"}
    for name, output in (("fallback_to_global", "fallback_race_count"),
                         ("constant_score_or_result", "constant_score_or_result_races")):
        if name in frame:
            if not frame[name].map(lambda value: isinstance(value, (bool, np.bool_))).all():
                raise ValueError("blend46-summary-boolean-flag-required")
            result[output] = int(frame[name].sum())
    if "absolute_error_sum" in frame:
        if not frame.absolute_error_sum.map(_finite_number).all() or frame.absolute_error_sum.lt(0).any():
            raise ValueError("blend46-summary-absolute-error-invalid")
        result["mae_speed_mps"] = float(frame.absolute_error_sum.sum() / runners)
    return result


def probability_summary(records, split="TEST"):
    """Pool race metrics and recompute calibration bins, never average fold ECE."""
    frame = _validated_records(records, split)
    if frame.strategy.nunique() != 1:
        raise ValueError("blend46-summary-one-strategy-required")
    if not _PROBABILITY_COLUMNS.issubset(frame.columns):
        raise ValueError("blend46-probability-columns-required")
    if not frame.probability_eligible.map(lambda value: isinstance(value, (bool, np.bool_))).all():
        raise ValueError("blend46-probability-eligibility-boolean-required")
    eligible = frame.loc[frame.probability_eligible].copy()
    if "probability_top1_correct" in eligible:
        # Probability ties are decided by the probability metric producer,
        # not borrowed from a separate raw-score accuracy ranking.
        eligible["top1_correct"] = eligible.probability_top1_correct
    numeric = sorted(_PROBABILITY_COLUMNS - {"probability_eligible"})
    if not all(eligible[name].map(_finite_number).all() for name in numeric):
        raise ValueError("blend46-eligible-probability-fields-missing-or-invalid")
    if (eligible.nll.lt(0).any() or eligible.brier.lt(0).any() or eligible.brier.gt(2 + 1e-12).any()
            or eligible.top1_confidence.lt(1. / eligible.runner_count - 1e-12).any()
            or eligible.top1_confidence.gt(1.).any() or not eligible.top1_correct.isin((0, 1)).all()
            or not np.allclose(eligible.uniform_nll, np.log(eligible.runner_count), rtol=0., atol=1e-12)
            or not np.allclose(eligible.uniform_brier, 1. - 1. / eligible.runner_count, rtol=0., atol=1e-12)):
        raise ValueError("blend46-probability-field-range-or-uniform-baseline-invalid")
    if "selection_bin" in eligible:
        bins = np.minimum(9, np.floor(eligible.top1_confidence.to_numpy(float) * 10)).astype(int)
        if not eligible.selection_bin.map(_finite_number).all() or not np.array_equal(eligible.selection_bin.to_numpy(), bins):
            raise ValueError("blend46-probability-selection-bin-invalid")
    result = {
        "strategy": str(frame.strategy.iloc[0]), "total_races": len(frame),
        "race_count": len(eligible), "runner_count": int(eligible.runner_count.sum()),
        "date_count": int(eligible.race_date.nunique()), "ineligible_races": len(frame) - len(eligible),
        "eligible_race_coverage": len(eligible) / len(frame),
        "eligible_runner_coverage": float(eligible.runner_count.sum() / frame.runner_count.sum()),
        "eligible_cohort_sha256": cohort_hash(eligible, split) if len(eligible) else None,
        "nll": float(eligible.nll.mean()) if len(eligible) else None,
        "brier": float(eligible.brier.mean()) if len(eligible) else None,
        "uniform_nll": float(eligible.uniform_nll.mean()) if len(eligible) else None,
        "uniform_brier": float(eligible.uniform_brier.mean()) if len(eligible) else None,
        "selection_ece": None, "selection_bias": None, "selection_absolute_bias": None,
        "selection_calibration_bins": [], "eligible_for_ev": False,
        "probability_authority_created": False, "roi_evaluated": False,
        "definitions": {"nll": "macro single-winner race multinomial negative log likelihood",
                        "brier": "macro race sum_i (p_i - winner_i)^2",
                        "selection_ece": "pooled weighted absolute top1 confidence minus hit rate in ten fixed bins",
                        "ineligible": "whole dead-heat races or unavailable calibration; see per-fold receipts"},
    }
    ece = 0.
    for number in range(10):
        lower, upper = number / 10., (number + 1) / 10.
        rows = eligible.loc[eligible.top1_confidence.ge(lower) &
                            (eligible.top1_confidence.le(upper) if number == 9 else eligible.top1_confidence.lt(upper))]
        confidence = float(rows.top1_confidence.mean()) if len(rows) else None
        hit_rate = float(rows.top1_correct.mean()) if len(rows) else None
        if len(rows):
            ece += len(rows) / len(eligible) * abs(confidence - hit_rate)
        result["selection_calibration_bins"].append({"lower": lower, "upper": upper, "count": len(rows),
                                                     "mean_predicted": confidence, "observed_win_rate": hit_rate})
    if len(eligible):
        bias = float((eligible.top1_confidence - eligible.top1_correct).mean())
        result.update(selection_ece=ece, selection_bias=bias, selection_absolute_bias=abs(bias))
    return result


def _bootstrap(paired, repetitions, seed):
    dates = paired.assign(delta_rho=paired.rank_correlation - paired.global_rank_correlation).groupby(
        ["test_year", "race_date"], sort=True).agg(
            delta_sum=("delta_rho", "sum"), race_count=("delta_rho", "size"),
            sse=("squared_speed_error_sum", "sum"), global_sse=("global_squared_speed_error_sum", "sum"),
            runner_count=("runner_count", "sum")).reset_index()
    if len(dates) < 2:
        return {"status": "insufficient_dates", "date_count": len(dates), "repetitions": repetitions,
                "delta_rank_correlation_ci": None, "delta_rmse_speed_mps_ci": None}
    groups = [group[["delta_sum", "race_count", "sse", "global_sse", "runner_count"]].to_numpy(float)
              for _, group in dates.groupby("test_year", sort=True)]
    rng = np.random.default_rng(seed)
    values = np.zeros((repetitions, 5))
    for start in range(0, repetitions, 64):
        count = min(64, repetitions - start)
        for group in groups:
            positions = rng.integers(0, len(group), size=(count, len(group)))
            values[start:start + count] += group[positions].sum(axis=1)
    return {"status": "computed", "date_count": len(dates), "year_strata": len(groups),
            "singleton_year_strata": sum(len(group) == 1 for group in groups), "repetitions": repetitions,
            "seed": seed, "resampling": "paired whole race dates; annual TEST strata",
            "delta_rank_correlation": values[:, 0] / values[:, 1],
            "delta_rmse_speed_mps": np.sqrt(values[:, 2] / values[:, 4]) - np.sqrt(values[:, 3] / values[:, 4])}


def paired_comparisons(records, repetitions=5000, seed=20261012):
    """Two predeclared contrasts, four primary deltas with Bonferroni CIs."""
    if (isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions < 100
            or isinstance(seed, bool) or not isinstance(seed, int) or seed < 0):
        raise ValueError("blend46-bootstrap-options-invalid")
    frame = validate_metric_cohort(records, split="TEST")
    global_rows = frame.loc[frame.strategy.eq("global")]
    keys = ["fold_id", "race_id"]
    reference = global_rows[keys + ["rank_correlation", "squared_speed_error_sum"]].rename(columns={
        "rank_correlation": "global_rank_correlation", "squared_speed_error_sum": "global_squared_speed_error_sum"})
    baseline = accuracy_summary(global_rows)
    result = []
    for strategy in STRATEGIES[1:]:
        candidate = frame.loc[frame.strategy.eq(strategy)].sort_values(["fold_id", "race_id"], kind="stable")
        paired = candidate.merge(reference, on=keys, validate="one_to_one")
        summary = accuracy_summary(candidate)
        stable_seed = seed ^ int(canonical_hash({"strategy": strategy, "comparison": "vs_global"})[:8], 16)
        draws = _bootstrap(paired, repetitions, stable_seed)
        row = {"strategy": strategy, "baseline": "global", "summary": summary,
               "delta_rank_correlation": summary["rank_correlation"] - baseline["rank_correlation"],
               "delta_rmse_speed_mps": summary["rmse_speed_mps"] - baseline["rmse_speed_mps"],
               "positive_rmse_delta_is_worse": True, "primary_comparison_family_size": PRIMARY_COMPARISON_FAMILY_SIZE,
               "adjustment_family": "venue_vs_global and blend_vs_global; Spearman and physical RMSE",
               "fresh_unknown_validation": False, "feature_selection_independent_of_history": False}
        for label, confidence in (("bootstrap95", .95), ("bootstrap98_75", PRIMARY_SIMULTANEOUS_CONFIDENCE)):
            receipt = {key: value for key, value in draws.items() if not isinstance(value, np.ndarray)}
            receipt["confidence"] = confidence
            if draws["status"] == "computed":
                alpha = (1. - confidence) / 2.
                for metric in ("delta_rank_correlation", "delta_rmse_speed_mps"):
                    receipt[f"{metric}_ci"] = [float(np.quantile(draws[metric], alpha)),
                                                float(np.quantile(draws[metric], 1. - alpha))]
                receipt["expected_resamples_in_each_tail"] = repetitions * alpha
            row[label] = receipt
        result.append(row)
    return result


def probability_comparisons(records, repetitions=5000, seed=20261012):
    """Exploratory, paired probability deltas on exactly matching eligible races.

    Both deltas are race-macro means. These unadjusted 95% CIs are separate
    from the four primary accuracy comparisons, not adoption/EV authority.
    A strategy cannot improve its denominator by silently excluding failures.
    """
    if (isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions < 100
            or isinstance(seed, bool) or not isinstance(seed, int) or seed < 0):
        raise ValueError("blend46-bootstrap-options-invalid")
    frame = validate_metric_cohort(records, split="TEST")
    keys = ["fold_id", "race_id"]
    expected, eligible_ids = None, None
    for strategy in STRATEGIES:
        rows = frame.loc[frame.strategy.eq(strategy)]
        probability_summary(rows)  # Strict finite/range checks before pooling.
        eligibility = rows[keys + ["probability_eligible"]].sort_values(keys, kind="stable").reset_index(drop=True)
        if expected is None:
            expected = eligibility
            eligible_ids = set(map(tuple, eligibility.loc[eligibility.probability_eligible, keys].to_numpy()))
        elif not eligibility.equals(expected):
            raise ValueError("blend46-probability-exact-shared-eligible-cohort-required")
    eligible = frame.loc[frame.probability_eligible]
    global_rows = eligible.loc[eligible.strategy.eq("global")]
    reference = global_rows[keys + ["nll", "brier"]].rename(columns={"nll": "global_nll", "brier": "global_brier"})
    result = []
    for strategy in STRATEGIES[1:]:
        candidate = eligible.loc[eligible.strategy.eq(strategy)].sort_values(keys, kind="stable")
        paired = candidate.merge(reference, on=keys, validate="one_to_one")
        if len(paired) != len(eligible_ids):
            raise ValueError("blend46-probability-eligible-pairing-failed")
        row = {"strategy": strategy, "baseline": "global", "race_count": len(paired),
               "total_races": len(frame.loc[frame.strategy.eq("global")]),
               "eligible_race_coverage": len(paired) / len(frame.loc[frame.strategy.eq("global")]),
               "delta_nll": float((paired.nll - paired.global_nll).mean()) if len(paired) else None,
               "delta_brier": float((paired.brier - paired.global_brier).mean()) if len(paired) else None,
               "positive_probability_delta_is_worse": True,
               "secondary_exploratory_unadjusted": True, "in_primary_accuracy_comparison_family": False,
               "probability_authority_created": False, "eligible_for_ev": False,
               "bootstrap95": {"status": "insufficient_dates", "confidence": .95,
                   "repetitions": repetitions, "date_count": int(paired.race_date.nunique()),
                   "delta_nll_ci": None, "delta_brier_ci": None}}
        if not len(paired):
            row["bootstrap95"]["status"] = "no_eligible_races"
            result.append(row)
            continue
        date_sums = paired.assign(delta_nll=paired.nll - paired.global_nll,
                                  delta_brier=paired.brier - paired.global_brier).groupby(
            ["test_year", "race_date"], sort=True).agg(
                nll_sum=("delta_nll", "sum"), brier_sum=("delta_brier", "sum"),
                race_count=("delta_nll", "size")).reset_index()
        if len(date_sums) < 2:
            result.append(row)
            continue
        groups = [group[["nll_sum", "brier_sum", "race_count"]].to_numpy(float)
                  for _, group in date_sums.groupby("test_year", sort=True)]
        stable_seed = seed ^ int(canonical_hash({"strategy": strategy, "comparison": "probability_secondary_vs_global"})[:8], 16)
        rng = np.random.default_rng(stable_seed)
        values = np.zeros((repetitions, 3))
        for start in range(0, repetitions, 64):
            count = min(64, repetitions - start)
            for group in groups:
                positions = rng.integers(0, len(group), size=(count, len(group)))
                values[start:start + count] += group[positions].sum(axis=1)
        intervals = {}
        for column, name in ((0, "nll"), (1, "brier")):
            deltas = values[:, column] / values[:, 2]
            intervals[f"delta_{name}_ci"] = [float(np.quantile(deltas, .025)), float(np.quantile(deltas, .975))]
        row["bootstrap95"].update(status="computed", seed=stable_seed, year_strata=len(groups),
                                 singleton_year_strata=sum(len(group) == 1 for group in groups),
                                 resampling="paired whole race dates; annual TEST strata; race-macro probability deltas",
                                 **intervals)
        result.append(row)
    return result
