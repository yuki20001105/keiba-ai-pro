"""Pure, finite TRAIN-only feature search design; no fitting or file writes.

The fixed47 production formula and all frozen research modules are untouched.
All candidate choices consume only the three historical TRAIN-internal folds.
CAL/TEST, probability calibration, betting ROI and publication are not inputs.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
from itertools import combinations
import math
from numbers import Real
import re

import numpy as np
import pandas as pd


TRAIN_DATE_TO = "20220521"
FOLD_IDS = ("DEV2020", "DEV2021", "DEV2022partial")
CANONICAL_COLUMNS = (
    "safe_distance", "safe_num_horses", "safe_horse_number", "safe_bracket_number",
    "safe_age", "safe_burden_weight", "safe_horse_weight", "safe_weight_change",
    "safe_month", "safe_race_number", "safe_surface", "safe_venue", "safe_sex",
    "safe_field_condition", "safe_weather", "safe_race_class",
    "safe_prior_starts", "safe_days_since_last",
) + tuple(f"safe_{prefix}_{field}" for prefix in ("prev", "prev2")
          for field in ("finish", "time_seconds", "distance", "speed_mps")) + tuple(
    f"safe_past{window}_{field}" for window in (3, 5, 10)
    for field in ("count", "observed_coverage", "avg_finish", "avg_speed_mps",
                  "avg_last3f_time", "win_rate", "top3_rate"))
FEATURE_GROUPS = {
    "race_context": ("safe_distance", "safe_num_horses", "safe_month", "safe_race_number",
                     "safe_surface", "safe_venue", "safe_field_condition", "safe_weather", "safe_race_class"),
    "horse_physical": ("safe_horse_number", "safe_bracket_number", "safe_age", "safe_burden_weight",
                       "safe_horse_weight", "safe_weight_change", "safe_sex"),
    "history_recency": ("safe_prior_starts", "safe_days_since_last"),
    "previous1": tuple(f"safe_prev_{field}" for field in ("finish", "time_seconds", "distance", "speed_mps")),
    "previous2": tuple(f"safe_prev2_{field}" for field in ("finish", "time_seconds", "distance", "speed_mps")),
    **{f"past{window}": tuple(f"safe_past{window}_{field}" for field in (
        "count", "observed_coverage", "avg_finish", "avg_speed_mps", "avg_last3f_time", "win_rate", "top3_rate"))
       for window in (3, 5, 10)},
}
COMPACT_HISTORY_GROUPS = (
    (), ("history_recency", "previous1"), ("history_recency", "previous1", "previous2"),
    ("history_recency", "past3"), ("history_recency", "past5"), ("history_recency", "past10"),
    ("history_recency", "previous1", "past3"), ("history_recency", "previous1", "past5"),
    ("history_recency", "previous1", "past10"), ("history_recency", "past3", "past5"),
    ("history_recency", "past3", "past10"), ("history_recency", "past5", "past10"),
)
_RECORD_COLUMNS = {"split_id", "race_id", "race_date", "runner_count", "rank_correlation", "squared_speed_error_sum"}
_KINDS = {"baseline", "leave_one_out", "group_drop", "compact", "interaction"}


def _canonical(columns):
    if not isinstance(columns, (list, tuple)) or tuple(columns) != CANONICAL_COLUMNS:
        raise ValueError("feature-search-exact-canonical47-required")
    return tuple(columns)


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        raise ValueError("feature-search-valid-date-required")
    try:
        parsed = datetime.strptime(value, "%Y%m%d")
    except ValueError as error:
        raise ValueError("feature-search-valid-date-required") from error
    if parsed.strftime("%Y%m%d") != value:
        raise ValueError("feature-search-valid-date-required")
    return value


def _number(value):
    return not isinstance(value, (bool, np.bool_)) and isinstance(value, Real) and math.isfinite(float(value))


def _spec(identifier, removed, kind, removed_groups=None):
    excluded = set(removed)
    row = {"id": identifier, "feature_columns": [name for name in CANONICAL_COLUMNS if name not in excluded],
           "removed_features": [name for name in CANONICAL_COLUMNS if name in excluded], "kind": kind}
    if removed_groups is not None:
        row["removed_groups"] = list(removed_groups)
    return row


def _validate_specs(specs):
    if not isinstance(specs, (list, tuple)) or not specs:
        raise ValueError("feature-search-candidate-specs-required")
    ids, subsets = set(), set()
    for spec in specs:
        if (not isinstance(spec, dict) or not isinstance(spec.get("id"), str)
                or not re.fullmatch(r"[a-zA-Z0-9_]{1,180}", spec["id"]) or spec["id"] in ids
                or spec.get("kind") not in _KINDS):
            raise ValueError("feature-search-candidate-identity-invalid-or-duplicate")
        kept, removed = spec.get("feature_columns"), spec.get("removed_features")
        if (not isinstance(kept, list) or not kept or not isinstance(removed, list)
                or not all(isinstance(name, str) for name in kept + removed)
                or len(set(kept + removed)) != len(CANONICAL_COLUMNS)
                or set(kept + removed) != set(CANONICAL_COLUMNS)
                or kept != [name for name in CANONICAL_COLUMNS if name in kept]
                or removed != [name for name in CANONICAL_COLUMNS if name in removed]
                or tuple(kept) in subsets):
            raise ValueError("feature-search-unique-canonical-subset-required")
        groups = spec.get("removed_groups", [])
        if (not isinstance(groups, list) or not all(isinstance(name, str) for name in groups) or len(set(groups)) != len(groups)
                or any(name not in FEATURE_GROUPS for name in groups)
                or any(not set(FEATURE_GROUPS[name]).issubset(removed) for name in groups)):
            raise ValueError("feature-search-removed-groups-invalid")
        ids.add(spec["id"])
        subsets.add(tuple(kept))
    return ids


def initial_candidates(canonical_columns):
    _canonical(canonical_columns)
    rows = [_spec("all47", (), "baseline")]
    rows.extend(_spec(f"loo__{name}", (name,), "leave_one_out") for name in CANONICAL_COLUMNS)
    rows.extend(_spec(f"drop_group__{name}", columns, "group_drop", (name,))
                for name, columns in FEATURE_GROUPS.items())
    for groups in COMPACT_HISTORY_GROUPS:
        selected = {name for group in ("race_context", "horse_physical", *groups) for name in FEATURE_GROUPS[group]}
        identifier = "compact__base" + ("__" + "__".join(groups) if groups else "")
        rows.append(_spec(identifier, set(CANONICAL_COLUMNS) - selected, "compact"))
    _validate_specs(rows)
    return rows


def selection_folds(train_date_to=TRAIN_DATE_TO):
    cap = _date(train_date_to)
    if not "20220101" <= cap <= TRAIN_DATE_TO:
        raise ValueError("feature-search-fold-cap-outside-fixed-TRAIN")
    return [{"split_id": split, "train_date_from": "20160101", "train_date_to": f"{year - 1}1231",
             "eval_date_from": f"{year}0101", "eval_date_to": f"{year}1231" if year < 2022 else cap}
            for split, year in zip(FOLD_IDS, (2020, 2021, 2022))]


def _aggregate(frame):
    return {"rank_correlation": float(frame.rank_correlation.mean()),
            "rmse_speed_mps": float(math.sqrt(frame.squared_speed_error_sum.sum() / frame.runner_count.sum())),
            "race_count": len(frame), "runner_count": int(frame.runner_count.sum()),
            "squared_speed_error_sum": float(frame.squared_speed_error_sum.sum()),
            "date_from": str(frame.race_date.min()), "date_to": str(frame.race_date.max())}


def summarize_candidates(candidate_specs, records_by_candidate, expected_folds, *, train_date_to=TRAIN_DATE_TO):
    ids = _validate_specs(candidate_specs)
    if (not isinstance(expected_folds, (list, tuple)) or len(expected_folds) != 3
            or set(expected_folds) != set(FOLD_IDS) or not isinstance(records_by_candidate, dict)
            or set(records_by_candidate) != ids):
        raise ValueError("feature-search-complete-TRAIN-fold-candidate-family-required")
    folds = {row["split_id"]: row for row in selection_folds(train_date_to)}
    reference_cohort, results = None, []
    for spec in candidate_specs:
        frame = records_by_candidate[spec["id"]]
        if (not isinstance(frame, pd.DataFrame) or frame.empty or not _RECORD_COLUMNS.issubset(frame.columns)
                or frame.columns.duplicated().any() or set(frame.split_id) != set(FOLD_IDS)
                or frame.duplicated(["split_id", "race_id"]).any()
                or frame.race_id.duplicated().any()):
            raise ValueError("feature-search-complete-unique-development-records-required")
        frame = frame.loc[:, sorted(_RECORD_COLUMNS)].copy()
        for row in frame.itertuples(index=False):
            fold = folds[row.split_id]
            if (not isinstance(row.race_id, str) or not row.race_id.strip()
                    or not fold["eval_date_from"] <= _date(row.race_date) <= fold["eval_date_to"]
                    or not _number(row.runner_count) or not 2 <= row.runner_count <= 18
                    or int(row.runner_count) != row.runner_count
                    or not _number(row.rank_correlation) or not -1 <= row.rank_correlation <= 1
                    or not _number(row.squared_speed_error_sum) or row.squared_speed_error_sum < 0):
                raise ValueError("feature-search-TRAIN-only-finite-complete-race-metrics-required")
        frame = frame.sort_values(["split_id", "race_id"], kind="stable").reset_index(drop=True)
        cohort = frame[["split_id", "race_id", "race_date", "runner_count"]]
        if reference_cohort is None:
            reference_cohort = cohort
        elif not cohort.equals(reference_cohort):
            raise ValueError("feature-search-all-candidates-identical-cohort-required")
        results.append({"id": spec["id"], "kind": spec["kind"], "feature_count": len(spec["feature_columns"]),
                        "selection_scope": "TRAIN_ONLY", "fold_ids": list(FOLD_IDS), **_aggregate(frame),
                        "by_fold": [{"split_id": name, **_aggregate(frame.loc[frame.split_id.eq(name)])} for name in FOLD_IDS]})
    return results


def _validate_summaries(summaries):
    if not isinstance(summaries, (list, tuple)) or not summaries:
        raise ValueError("feature-search-summaries-required")
    ids = set()
    for row in summaries:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]
                or row["id"] in ids or row.get("selection_scope") != "TRAIN_ONLY"
                or row.get("fold_ids") != list(FOLD_IDS)
                or not _number(row.get("rank_correlation")) or not -1 <= row["rank_correlation"] <= 1
                or not _number(row.get("rmse_speed_mps")) or row["rmse_speed_mps"] < 0
                or not isinstance(row.get("feature_count"), int) or isinstance(row["feature_count"], bool)
                or not 1 <= row["feature_count"] <= 47):
            raise ValueError("feature-search-valid-TRAIN-only-summary-required")
        folds = row.get("by_fold")
        if (not isinstance(folds, list) or len(folds) != 3
                or any(not isinstance(fold, dict) for fold in folds)
                or [fold.get("split_id") for fold in folds] != list(FOLD_IDS)):
            raise ValueError("feature-search-complete-TRAIN-summary-folds-required")
        for fold, bounds in zip(folds, selection_folds()):
            count, runners = fold.get("race_count"), fold.get("runner_count")
            if (isinstance(count, bool) or not isinstance(count, int) or count < 1
                    or isinstance(runners, bool) or not isinstance(runners, int) or not 2 * count <= runners <= 18 * count
                    or not bounds["eval_date_from"] <= _date(fold.get("date_from")) <= _date(fold.get("date_to")) <= bounds["eval_date_to"]
                    or not _number(fold.get("rank_correlation")) or not -1 <= fold["rank_correlation"] <= 1
                    or not _number(fold.get("squared_speed_error_sum")) or fold["squared_speed_error_sum"] < 0
                    or not _number(fold.get("rmse_speed_mps"))
                    or not math.isclose(fold["rmse_speed_mps"], math.sqrt(fold["squared_speed_error_sum"] / runners), rel_tol=1e-12, abs_tol=1e-12)):
                raise ValueError("feature-search-valid-TRAIN-summary-fold-metrics-required")
        count = sum(fold["race_count"] for fold in folds)
        runners = sum(fold["runner_count"] for fold in folds)
        squared = sum(fold["squared_speed_error_sum"] for fold in folds)
        rho = sum(fold["rank_correlation"] * fold["race_count"] for fold in folds) / count
        if (row.get("race_count") != count or row.get("runner_count") != runners
                or row.get("date_from") != min(fold["date_from"] for fold in folds)
                or row.get("date_to") != max(fold["date_to"] for fold in folds)
                or not _number(row.get("squared_speed_error_sum"))
                or not math.isclose(row["squared_speed_error_sum"], squared, rel_tol=1e-12, abs_tol=1e-12)
                or not math.isclose(row["rank_correlation"], rho, rel_tol=1e-12, abs_tol=1e-12)
                or not math.isclose(row["rmse_speed_mps"], math.sqrt(squared / runners), rel_tol=1e-12, abs_tol=1e-12)):
            raise ValueError("feature-search-pooling-must-match-folds")
        ids.add(row["id"])
    return {row["id"]: row for row in summaries}


def select_candidate(summaries, baseline_id="all47", rank_tolerance=0.0002, max_rmse_relative_deterioration=0.01):
    indexed = _validate_summaries(summaries)
    if (baseline_id not in indexed or not _number(rank_tolerance) or not 0 <= rank_tolerance <= 0.002
            or not _number(max_rmse_relative_deterioration) or not 0 <= max_rmse_relative_deterioration <= 0.01):
        raise ValueError("feature-search-selection-parameters-or-baseline-invalid")
    maximum_rmse = indexed[baseline_id]["rmse_speed_mps"] * (1 + max_rmse_relative_deterioration)
    eligible = [row for row in summaries if row["rmse_speed_mps"] <= maximum_rmse]
    best_rho = max(row["rank_correlation"] for row in eligible)
    tied = [row for row in eligible if best_rho - row["rank_correlation"] <= rank_tolerance]
    return dict(min(tied, key=lambda row: (row["rmse_speed_mps"], row["feature_count"], row["id"])))


def stage2_candidates(canonical_columns, initial_specs, stage1_summaries, maximum=8):
    _canonical(canonical_columns)
    ids = _validate_specs(initial_specs)
    summaries = _validate_summaries(stage1_summaries)
    locked = {spec["id"]: spec for spec in initial_candidates(canonical_columns)}
    if (set(summaries) != ids or {spec["id"]: spec for spec in initial_specs} != locked
            or "all47" not in summaries or isinstance(maximum, bool)
            or not isinstance(maximum, int) or not 0 <= maximum <= 8
            or any(summaries[spec["id"]]["feature_count"] != len(spec["feature_columns"]) for spec in initial_specs)):
        raise ValueError("feature-search-stage2-locked-stage1-family-required")
    baseline = summaries["all47"]
    eligible = [spec for spec in initial_specs
                if summaries[spec["id"]]["rank_correlation"] > baseline["rank_correlation"]
                and summaries[spec["id"]]["rmse_speed_mps"] <= baseline["rmse_speed_mps"] * 1.01]
    existing = {tuple(spec["feature_columns"]) for spec in initial_specs}
    rows = []
    for kind in ("leave_one_out", "group_drop"):
        top = sorted((spec for spec in eligible if spec["kind"] == kind), key=lambda spec: (
            -summaries[spec["id"]]["rank_correlation"], summaries[spec["id"]]["rmse_speed_mps"], spec["id"]))[:3]
        for size in (2, 3):
            for parents in combinations(top, size):
                removed = {name for spec in parents for name in spec["removed_features"]}
                groups = sorted({name for spec in parents for name in spec.get("removed_groups", [])})
                identifier = "interaction__" + ("features__" if kind == "leave_one_out" else "groups__")
                identifier += hashlib.sha256("\n".join(name for name in CANONICAL_COLUMNS if name in removed).encode()).hexdigest()[:16]
                row = _spec(identifier, removed, "interaction", groups or None)
                signature = tuple(row["feature_columns"])
                if signature not in existing and row["feature_columns"]:
                    row["parent_ids"] = [spec["id"] for spec in parents]
                    rows.append(row)
                    existing.add(signature)
    result = rows[:maximum]
    if result:
        _validate_specs(result)
    return result
