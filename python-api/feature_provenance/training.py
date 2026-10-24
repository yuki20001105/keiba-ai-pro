"""Whole-table training-data audit, without imputation or outcome analysis.

Event dates, DB insertion dates, identity evidence and publication/capture proof
are separate axes. This module deliberately grants no historical capture proof.
"""
from __future__ import annotations

from collections import defaultdict
import json
import math
import re
import unicodedata

import numpy as np
import pandas as pd


TIMES = ["time_6f", "time_5f", "time_4f", "time_3f", "time_1f"]
LAPS = ["lap_6f_5f", "lap_5f_4f", "lap_4f_3f", "lap_3f_1f", "lap_1f_g"]
NUMERIC = TIMES + LAPS
FIELDS = NUMERIC + ["position", "pace", "grade", "comment", "rider", "track_condition", "course", "is_last_training"]
SENTINELS = {"-", "--", "---", "―", "—", "－", "null", "none", "nan", "n/a"}


def name_key(value):
    if value is None or pd.isna(value): return ""
    return unicodedata.normalize("NFKC", str(value)).strip()


def number(value):
    if isinstance(value, (bool, np.bool_)): return None
    try: x = float(value)
    except (ValueError, TypeError, OverflowError): return None
    return int(x) if math.isfinite(x) and x.is_integer() and 1 <= x <= 99 else None


def dates(values):
    text = values.astype("string").str.strip()
    canonical = text.str.replace("/", "", regex=False).str.replace("-", "", regex=False)
    valid_shape = text.str.fullmatch(r"(?:\d{8}|\d{4}/\d{2}/\d{2}|\d{4}-\d{2}-\d{2})", na=False)
    return pd.to_datetime(canonical.where(valid_shape), format="%Y%m%d", errors="coerce")


def _date_text(value):
    return None if pd.isna(value) else value.strftime("%Y%m%d")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def course_group(value):
    normalized = name_key(value)
    return {"栗坂": "栗坂", "美坂": "美坂", "美W": "美W", "CW": "CW", "栗CW": "CW"}.get(normalized, "OTHER" if normalized else "MISSING")


def _missing_masks(series):
    text = series.astype("string").str.strip()
    null = series.isna()
    blank = text.eq("").fillna(False) & ~null
    sentinel = text.str.lower().isin(SENTINELS) & ~null & ~blank
    return null, blank, sentinel


def missingness(frame, scope="all"):
    rows = []
    for field in FIELDS:
        values = frame[field]
        null, blank, sentinel = _missing_masks(values)
        missing = null | blank | sentinel
        converted = pd.to_numeric(values.where(~missing), errors="coerce")
        if field in NUMERIC:
            invalid = ~missing & (~np.isfinite(converted) | converted.le(0))
            zero = converted.eq(0)
        elif field == "is_last_training":
            invalid = ~missing & ~converted.isin([0, 1])
            zero = converted.eq(0)
        else:
            invalid = ~missing & values.astype("string").str.contains("\ufffd", regex=False, na=False)
            zero = pd.Series(False, index=frame.index)
        rows.append({"scope": scope, "field": field, "rows": len(frame), "null_count": int(null.sum()),
            "blank_count": int(blank.sum()), "sentinel_count": int(sentinel.sum()), "missing_count": int(missing.sum()),
            "missing_percent": float(missing.mean() * 100) if len(frame) else None,
            "invalid_count": int(invalid.sum()), "invalid_percent": float(invalid.mean() * 100) if len(frame) else None,
            "zero_count": int(zero.sum()), "usable_numeric_count": int((~missing & ~invalid).sum()) if field in NUMERIC else None,
            "structural_missing_confirmed_count": 0, "missing_cause_unresolved_count": int(missing.sum()),
            "semantics": "stored slot labels only; course/distance mapping and publication time unverified" if field in NUMERIC else
                "categorical; no imposed ordinal scale" if field in {"pace", "grade"} else
                "historical parser assigns True unconditionally; not proof of latest workout" if field == "is_last_training" else
                "missing source cause unknown; original value not changed"})
    return pd.DataFrame(rows)


def _join(training, races, runners, legacy):
    target_ids = set(training.race_id.astype(str))
    race_index = {str(k): p for k, p in races.loc[races.race_id.astype(str).isin(target_ids)].groupby("race_id", sort=False)}
    legacy_index = {} if legacy.empty else {str(k): p for k, p in legacy.loc[legacy.race_id.astype(str).isin(target_ids)].groupby("race_id", sort=False)}
    training_groups = training.groupby("race_id", sort=False).indices
    selected = runners.loc[runners.race_id.astype(str).isin(target_ids)].copy()
    selected["number_key"] = selected.horse_number.map(number)
    selected["name_key"] = selected.horse_name.map(name_key)
    selected["horse_id_key"] = selected.horse_id.map(name_key)
    number_index, name_index = defaultdict(list), defaultdict(list)
    for row in selected.itertuples(index=False):
        item = {"horse_id": row.horse_id_key, "horse_number": row.number_key, "name": row.name_key,
                "inner_race_id": str(row.inner_race_id), "json_valid": row.data_json_valid}
        if pd.notna(row.number_key): number_index[(str(row.race_id), int(row.number_key))].append(item)
        if row.name_key: name_index[(str(row.race_id), row.name_key)].append(item)
    links, race_rows = [], []
    race_dates = {}
    for rid in sorted(target_ids):
        part = training.iloc[training_groups[rid]]
        header = race_index.get(rid)
        legacy_part = legacy_index.get(rid)
        reason, date, source = "matched_unique_canonical", pd.NaT, "canonical"
        if header is None:
            reason, source = "canonical_race_missing", "missing"
            if legacy_part is not None and len(legacy_part) == 1:
                date = dates(legacy_part.kaisai_date).iloc[0]
                source = "legacy_only_not_canonical"
        elif len(header) != 1:
            reason = "canonical_race_id_duplicate"
        else:
            value = header.iloc[0]
            date = dates(pd.Series([value.race_date])).iloc[0]
            if value.data_json_valid != 1 or str(value.inner_race_id) != rid: reason = "canonical_identity_or_json_conflict"
            elif pd.isna(date): reason = "canonical_race_date_invalid"
        race_dates[rid] = (date, reason, source)
        race_rows.append({"race_id": rid, "race_id_year": rid[:4], "race_date": _date_text(date), "date_source": source,
            "race_join_status": reason, "canonical_race_rows": 0 if header is None else len(header),
            "legacy_race_rows": 0 if legacy_part is None else len(legacy_part), "training_rows": len(part),
            "stored_training_runner_numbers": int(part.horse_number.dropna().nunique())})
    for row in training.itertuples(index=False):
        rid, num, name = str(row.race_id), number(row.horse_number), name_key(row.horse_name)
        by_num, by_name = number_index.get((rid, num), []), name_index.get((rid, name), []) if name else []
        date, race_reason, date_source = race_dates[rid]
        resolved, candidate, method = None, None, "unmatched"
        if race_reason != "matched_unique_canonical": method = race_reason
        elif len(by_num) > 1: method = "ambiguous_race_number"
        elif len(by_num) == 1:
            candidate = by_num[0]
            if candidate["json_valid"] != 1 or candidate["inner_race_id"] != rid or not candidate["horse_id"]:
                method = "candidate_source_identity_invalid"
            elif name and name == candidate["name"]:
                resolved, method = candidate, "unique_number_and_normalized_name"
            elif not name or not candidate["name"]:
                method = "number_joinable_name_unverified"
            else:
                method = "number_name_conflict"
        elif len(by_name) > 1: method = "ambiguous_race_name"
        elif len(by_name) == 1:
            candidate = by_name[0]
            if num is not None: method = "name_candidate_number_conflict"
            elif candidate["json_valid"] == 1 and candidate["inner_race_id"] == rid and candidate["horse_id"]:
                resolved, method = candidate, "unique_race_name_missing_number"
            else: method = "candidate_source_identity_invalid"
        else: method = "runner_key_not_found"
        links.append({"training_id": row.id, "race_id": rid, "race_id_year": rid[:4], "horse_number": row.horse_number,
            "normalized_training_name": name, "race_date": _date_text(date), "race_date_source": date_source,
            "race_join_status": race_reason, "runner_join_status": method, "number_match_rows": len(by_num), "name_match_rows": len(by_name),
            "number_joinable": len(by_num) == 1, "identity_verified": resolved is not None,
            "candidate_horse_id": candidate["horse_id"] if candidate else None,
            "resolved_horse_id": resolved["horse_id"] if resolved else None,
            "resolved_horse_number": resolved["horse_number"] if resolved else None})
    return pd.DataFrame(links), pd.DataFrame(race_rows), race_index


def _coverage(races, runners, training, joined, scopes):
    canonical = races.copy()
    canonical["parsed_date"] = dates(canonical.race_date)
    canonical["year"] = canonical.parsed_date.dt.year
    unique_header = ~canonical.race_id.duplicated(keep=False)
    canonical = canonical.loc[unique_header & canonical.data_json_valid.eq(1) & canonical.inner_race_id.eq(canonical.race_id) & canonical.parsed_date.notna()]
    base = runners[["race_id", "horse_number", "horse_id"]].copy()
    base["horse_number"] = base.horse_number.map(number)
    valid = base.horse_number.notna() & ~base.duplicated(["race_id", "horse_number"], keep=False)
    base = base.loc[valid].merge(canonical[["race_id", "year"]], on="race_id", validate="many_to_one")
    presence = set(zip(training.race_id.astype(str), training.horse_number.map(number)))
    verified = set(zip(joined.loc[joined.identity_verified, "race_id"], joined.loc[joined.identity_verified, "resolved_horse_number"]))
    prior = joined.loc[joined.identity_verified & joined.event_date_class.eq("A_BEFORE_RACE")]
    prior_keys = set(zip(prior.race_id, prior.resolved_horse_number))
    tables, summaries = [], []
    for name, frame in [("all_canonical", base), *scopes.items()]:
        part = frame[["race_id", "horse_number"]].copy()
        part["race_id"] = part.race_id.astype(str)
        part["horse_number"] = part.horse_number.map(number)
        if "race_date" in frame: years = dates(frame.race_date).dt.year
        elif "year" in frame: years = frame.year
        else: years = pd.to_numeric(part.race_id.str[:4], errors="coerce")
        part["year"] = years.to_numpy()
        keys = list(zip(part.race_id, part.horse_number))
        part["has_training_key"] = [k in presence for k in keys]
        part["identity_verified_training"] = [k in verified for k in keys]
        part["prior_event_training"] = [k in prior_keys for k in keys]
        part["capture_time_verified_training"] = False
        by_race = part.groupby("race_id", sort=True).agg(year=("year", "first"), known_runner_keys=("horse_number", "size"),
            training_runners=("has_training_key", "sum"), identity_verified_runners=("identity_verified_training", "sum"),
            prior_event_runners=("prior_event_training", "sum"))
        if name == "all_canonical":
            headers = canonical.set_index("race_id")
            by_race = by_race.reindex(headers.index)
            by_race["year"] = headers.year
            by_race[["known_runner_keys", "training_runners", "identity_verified_runners", "prior_event_runners"]] = by_race[
                ["known_runner_keys", "training_runners", "identity_verified_runners", "prior_event_runners"]].fillna(0)
            declared = headers.num_horses.map(number)
            by_race["runners"] = declared.where(declared.ge(by_race.known_runner_keys))
        else:
            by_race["runners"] = by_race.known_runner_keys
        by_race["scope"] = name
        tables.append(by_race.reset_index())
        # Include zero-data years explicitly; all-table denominators include races
        # without any runner rows, while threshold coverage never assumes them complete.
        for year in range(2013, 2027):
            r = by_race.loc[by_race.year.eq(year)]
            total_races = int(canonical.year.eq(year).sum()) if name == "all_canonical" else len(r)
            total_runners = int(r.runners.sum())
            ratio = r.training_runners / r.runners
            known = r.loc[r.runners.notna()]
            summaries.append({"scope": name, "year": year, "total_races": total_races, "races_with_unique_runner_keys": int(r.known_runner_keys.gt(0).sum()),
                "races_with_complete_runner_keys": int(r.known_runner_keys.eq(r.runners).sum()),
                "races_with_unknown_or_conflicting_runner_denominator": int(r.runners.isna().sum()),
                "known_runner_key_rows": int(r.known_runner_keys.sum()), "total_runners": total_runners,
                "any_training_races": int(r.training_runners.gt(0).sum()),
                "at_least_50pct_training_races": int(ratio.ge(.5).sum()), "at_least_80pct_training_races": int(ratio.ge(.8).sum()),
                "all_runner_training_races": int(ratio.eq(1).sum()), "training_runner_count": int(r.training_runners.sum()),
                "training_runner_coverage_percent": float(known.training_runners.sum() / total_runners * 100) if total_runners else None,
                "identity_verified_runner_count": int(r.identity_verified_runners.sum()), "source_date_prior_runner_count": int(r.prior_event_runners.sum()),
                "pre_race_capture_verified_runner_count": 0, "pre_race_capture_verified_coverage_percent": 0. if total_runners else None,
                "counts_are_source_presence_not_feature_safety": True})
    return pd.DataFrame(summaries), pd.concat(tables, ignore_index=True)


def _statistics(frame):
    rows = []
    for fields, scope in [(["course_key"], "course"), (["course_key", "condition_group"], "course_condition"),
                          (["course_key", "canonical_race_year"], "course_year"),
                          (["course_key", "condition_group", "canonical_race_year"], "course_condition_year")]:
        for keys, part in frame.groupby(fields, sort=True, dropna=False):
            if not isinstance(keys, tuple): keys = (keys,)
            labels = dict(zip(fields, keys))
            labels["course_group"] = part.course_group.iloc[0]
            for field in NUMERIC:
                values = pd.to_numeric(part[field], errors="coerce")
                finite = values.loc[np.isfinite(values) & values.gt(0)]
                rows.append({"scope": scope, **labels, "field": field, "source_rows": len(part), "valid_numeric_count": len(finite),
                    "mean": float(finite.mean()) if len(finite) else None, "median": float(finite.median()) if len(finite) else None,
                    "std_sample": float(finite.std(ddof=1)) if len(finite) > 1 else None,
                    "min": float(finite.min()) if len(finite) else None, "max": float(finite.max()) if len(finite) else None,
                    "stored_distance_label_verified": False, "descriptive_only_not_feature_normalization": True})
    return pd.DataFrame(rows)


def analyze_training(training, races, runners, legacy=None, cohorts=None, schema=None):
    required = {"id", "race_id", "horse_number", "horse_name", "training_date", "created_at", *FIELDS}
    if not required <= set(training) or training.empty or training.id.duplicated().any():
        raise ValueError("complete training schema and unique record IDs required")
    legacy = pd.DataFrame() if legacy is None else legacy
    cohorts = {} if cohorts is None else cohorts
    data = training.reset_index(drop=True).copy()
    data["race_id"] = data.race_id.astype(str)
    data["race_id_year"] = data.race_id.str[:4]
    data["course_group"] = data.course.map(course_group)
    data["course_key"] = data.course.map(name_key).replace("", "MISSING")
    data["condition_group"] = data.track_condition.map(name_key).replace("", "MISSING")
    join, race_join, _ = _join(data, races, runners, legacy)
    workout, race_date = dates(data.training_date), dates(join.race_date)
    data["canonical_race_year"] = race_date.dt.year.astype("Int64").astype("string").fillna("UNKNOWN")
    # Input order is retained by _join, so per-record alignment remains explicit.
    if not join.training_id.equals(data.id.rename("training_id")):
        raise ValueError("record alignment changed")
    join["training_date_raw"] = data.training_date.to_numpy()
    join["training_date_validated"] = workout.map(_date_text).to_numpy()
    join["created_at"] = data.created_at.to_numpy()
    both = workout.notna() & race_date.notna()
    join["event_date_class"] = np.select([both & workout.lt(race_date), both & workout.eq(race_date), both & workout.gt(race_date)],
        ["A_BEFORE_RACE", "B_SAME_DATE_TIME_UNKNOWN", "C_AFTER_RACE"], default="D_DATE_OR_RACE_UNKNOWN")
    join["days_before_race"] = (race_date - workout).dt.days
    join["source_capture_time_verified"] = False
    join["safe_point_in_time"] = False
    join["registration_is_availability_proof"] = False
    registration_day = pd.to_datetime(data.created_at, format="mixed", errors="coerce").dt.normalize()
    join["registration_day_relation"] = np.select([registration_day.lt(race_date), registration_day.eq(race_date), registration_day.gt(race_date)],
        ["DB_DAY_BEFORE_RACE_NOT_CAPTURE_PROOF", "DB_DAY_SAME_RACE_NOT_CAPTURE_PROOF", "DB_DAY_AFTER_RACE"], default="UNKNOWN")
    join["record_pit_class"] = np.where(~join.identity_verified, "D_NOT_AVAILABLE",
        np.where(join.event_date_class.eq("A_BEFORE_RACE"), "B_SOURCE_DATE_VALID_BUT_NOT_CAPTURE_TIME_VERIFIED", "C_JOINABLE_BUT_TIME_UNVERIFIED"))
    key_columns = ["race_id", "horse_number", "training_date", "course"]
    content_columns = [c for c in training if c not in {"id", "created_at"}]
    data["exact_content_duplicate"] = data.duplicated(content_columns, keep=False)
    data["duplicate_workout_key"] = data.duplicated(key_columns, keep=False)
    data["repeated_runner_context"] = data.duplicated(["race_id", "horse_number"], keep=False)
    for field in ("exact_content_duplicate", "duplicate_workout_key", "repeated_runner_context"):
        join[field] = data[field].to_numpy()
    per_race = join.groupby("race_id", sort=True).agg(identity_verified_records=("identity_verified", "sum"),
        number_joinable_records=("number_joinable", "sum"), source_date_prior_records=("event_date_class", lambda s: int(s.eq("A_BEFORE_RACE").sum())),
        safe_point_in_time_records=("safe_point_in_time", "sum"))
    def race_pit(part):
        valid = part.loc[part.identity_verified]
        if valid.empty: return "D_NOT_AVAILABLE"
        return "B_SOURCE_DATE_VALID_BUT_NOT_CAPTURE_TIME_VERIFIED" if valid.event_date_class.eq("A_BEFORE_RACE").any() else "C_JOINABLE_BUT_TIME_UNVERIFIED"
    per_race["race_pit_class"] = {rid: race_pit(part) for rid, part in join.groupby("race_id", sort=False)}
    race_join = race_join.merge(per_race.reset_index(), on="race_id", validate="one_to_one")
    all_race_pit = races[["race_id", "race_date"]].drop_duplicates("race_id").copy()
    duplicate_headers = set(races.loc[races.race_id.duplicated(keep=False), "race_id"])
    all_race_pit.loc[all_race_pit.race_id.isin(duplicate_headers), "race_date"] = None
    all_race_pit["race_date"] = dates(all_race_pit.race_date).map(_date_text)
    all_race_pit = all_race_pit.merge(per_race.reset_index(), on="race_id", how="left", validate="one_to_one")
    all_race_pit["race_pit_class"] = all_race_pit.race_pit_class.fillna("D_NOT_AVAILABLE")
    for field in ("identity_verified_records", "number_joinable_records", "source_date_prior_records", "safe_point_in_time_records"):
        all_race_pit[field] = all_race_pit[field].fillna(0).astype(int)
    all_race_pit["no_training_records"] = ~all_race_pit.race_id.isin(set(data.race_id))
    all_race_pit["same_class_not_all_runner_coverage"] = True
    yearly, race_coverage = _coverage(races, runners, data, join, cohorts)
    course_missing = pd.concat([missingness(part, str(course)).assign(course_key=course, course_group=part.course_group.iloc[0])
                               for course, part in data.groupby("course_key", sort=True)], ignore_index=True)
    time_columns = {"training_date": workout, "race_date": race_date,
                    "created_at": pd.to_datetime(data.created_at, format="mixed", errors="coerce")}
    ranges = {key: {"valid_count": int(value.notna().sum()), "invalid_or_missing_count": int(value.isna().sum()),
                   "min": str(value.min()) if value.notna().any() else None,
                   "max": str(value.max()) if value.notna().any() else None} for key, value in time_columns.items()}
    axes = {"race_id_prefix_not_date": pd.to_numeric(data.race_id_year.where(data.race_id_year.str.fullmatch(r"\d{4}")), errors="coerce"),
            "training_date_year": workout.dt.year, "canonical_race_date_year": race_date.dt.year,
            "created_at_registration_year": time_columns["created_at"].dt.year}
    year_axes = []
    for axis, years in axes.items():
        for year in sorted(set(range(2013, 2027)) | set(int(x) for x in years.dropna())):
            year_axes.append({"year_axis": axis, "year": str(year), "rows": int(years.eq(year).sum()),
                              "outside_2013_2026": not 2013 <= year <= 2026})
        year_axes.append({"year_axis": axis, "year": "UNKNOWN", "rows": int(years.isna().sum()), "outside_2013_2026": None})
    year_inventory = []
    for year in range(2013, 2027):
        part = data.loc[data.race_id_year.eq(str(year))]
        resolved = join.loc[join.race_id_year.eq(str(year))]
        year_inventory.append({"year": year, "rows": len(part), "distinct_races": int(part.race_id.nunique()),
            "distinct_race_runner_keys": int(part[["race_id", "horse_number"]].drop_duplicates().shape[0]),
            "distinct_stored_names": int(part.horse_name.map(name_key).replace("", pd.NA).nunique()),
            "distinct_verified_horse_ids": int(resolved.resolved_horse_id.nunique()),
            "event_date_classes": {str(k): int(v) for k, v in resolved.event_date_class.value_counts().items()}})
    cohort_comparison = {}
    for label, frame in cohorts.items():
        keys = set(zip(frame.race_id.astype(str), frame.horse_number.map(number)))
        inside = data.loc[[key in keys for key in zip(data.race_id, data.horse_number.map(number))]]
        scoped_join = join.loc[join.training_id.isin(inside.id)]
        cohort_comparison[label] = {"cohort_races": int(frame.race_id.nunique()), "cohort_runners": len(frame),
            "training_records": len(inside), "races_with_training_records": int(inside.race_id.nunique()),
            "runners_with_training_records": int(inside[["race_id", "horse_number"]].drop_duplicates().shape[0]),
            "identity_verified_records": int(scoped_join.identity_verified.sum()), "event_date_classes": scoped_join.event_date_class.value_counts().to_dict(),
            "safe_point_in_time_records": 0}
    metadata = {"schema": "whole-training-data-provenance-v1", "rows": len(training), "columns": list(training.columns),
        "source_schema": schema, "date_ranges": ranges, "year_inventory": year_inventory,
        "race_join_status": race_join.race_join_status.value_counts().to_dict(), "runner_join_status": join.runner_join_status.value_counts().to_dict(),
        "event_date_classes": join.event_date_class.value_counts().to_dict(), "training_race_pit_classes": race_join.race_pit_class.value_counts().to_dict(),
        "all_canonical_race_pit_classes": all_race_pit.race_pit_class.value_counts().to_dict(),
        "registration_day_relation_not_availability": join.registration_day_relation.value_counts().to_dict(),
        "exact_content_duplicate_rows": int(data.exact_content_duplicate.sum()), "duplicate_workout_key_rows": int(data.duplicate_workout_key.sum()),
        "repeated_race_runner_rows": int(data.repeated_runner_context.sum()), "duplicate_records_deleted": 0,
        "duplicate_policy": "repeat runner rows with different date/course may be legitimate workouts; same workout key or content is flagged, never silently collapsed",
        "source_race_id_duplicate_rows": int(races.race_id.duplicated(keep=False).sum()),
        "year_inventory_year_basis": "race_id prefix, explicitly not a reconstructed race_date; coverage uses validated canonical race_date",
        "source_header_invalid_or_missing_date_rows": int(dates(races.race_date).isna().sum()),
        "source_runner_key_duplicate_rows": int(runners.duplicated(["race_id", "horse_number"], keep=False).sum()),
        "source_runner_rows": len(runners), "source_runner_invalid_number_rows": int(runners.horse_number.map(number).isna().sum()),
        "coverage_denominator_policy": "validated unique canonical headers; declared runner count when valid and >=unique stored keys; missing/conflicting denominator reported, never assumed complete; duplicate/invalid runner keys excluded and disclosed",
        "training_last_flag_counts": {str(k): int(v) for k, v in data.is_last_training.value_counts(dropna=False).items()},
        "cohort_reproduction": cohort_comparison, "temporal_feature_gate": "NO",
        "safe_point_in_time_records": 0, "source_capture_verified_records": 0,
        "blockers": ["no contemporaneous source publication/capture evidence", "course/distance slot meanings not verified from original HTML",
                     "structural vs extraction missingness unresolved", "latest-workout flag not validated"],
        "source_date_prior_is_capture_proof": False, "created_at_is_publication_time": False,
        "imputation_performed": False, "models_fitted": 0, "race_outcome_values_loaded": False,
        "statistics_warning": "all descriptive means/std include entire audit period; forbidden as model normalization or future-aware features"}
    raw = {"training_time_5f": "time_5f", "training_time_4f": "time_4f", "training_time_3f": "time_3f",
           "training_time_1f": "time_1f", "final_lap": "lap_1f_g", "pace": "pace", "grade": "grade", "is_last_training": "is_last_training"}
    derived = ["last_1f_zscore", "4f_zscore", "5f_zscore", "acceleration_score", "final_lap_rank", "course_relative_time",
               "condition_relative_time", "training_days_before_race", "current_vs_previous_training", "current_vs_horse_average", "best_recent_training", "training_trend"]
    candidates = [{"feature": name, "source": field, "kind": "raw", "status": "HOLD", "permitted_for_current_model": False,
                   "reason": "capture/PIT and source semantics unverified", "encoding": "categorical_no_ordinal" if name in {"pace", "grade"} else "not_transformed"} for name, field in raw.items()]
    candidates += [{"feature": name, "kind": "derived_candidate_only", "status": "HOLD", "permitted_for_current_model": False,
                    "fit_rule": "verified pre-race source only; normalization fitted inside train fold on strictly older events; horse comparisons strictly earlier workouts; no audit-global means/std"} for name in derived]
    candidates.append({"feature": "comment", "status": "EXCLUDED", "permitted_for_current_model": False,
                       "reason": "publication/edit version and contemporaneous availability unverified"})
    point = join[["training_id", "race_id", "race_id_year", "horse_number", "race_date", "race_date_source", "training_date_raw", "training_date_validated",
                  "created_at", "days_before_race", "identity_verified", "event_date_class", "record_pit_class", "source_capture_time_verified", "safe_point_in_time", "registration_is_availability_proof"]]
    categories = []
    for field in ("course", "track_condition", "pace", "grade", "rider", "position"):
        for value, count in data[field].map(lambda v: "<SQL_NULL>" if pd.isna(v) else str(v)).value_counts(dropna=False).items():
            categories.append({"field": field, "stored_value": value, "rows": int(count), "normalized_value_for_audit_only": name_key(value)})
    return {"training_data_inventory.json": metadata, "training_yearly_inventory.csv": pd.DataFrame(year_inventory).drop(columns="event_date_classes"),
        "training_year_axes.csv": pd.DataFrame(year_axes), "training_categorical_values.csv": pd.DataFrame(categories),
        "training_yearly_coverage.csv": yearly, "training_race_coverage.csv": race_coverage,
        "training_race_join_audit.csv": race_join, "training_runner_join_audit.csv": join,
        "training_race_point_in_time_audit.csv": all_race_pit.sort_values("race_id"),
        "training_point_in_time_audit.csv": point, "training_missingness.csv": missingness(data),
        "training_course_missingness.csv": course_missing, "training_course_statistics.csv": _statistics(data),
        "training_feature_candidates.json": {"schema": "training-feature-candidates-provenance-v1", "gate": "NO", "candidates": candidates,
            "no_feature_values_generated": True, "no_probability_or_model_evaluation": True}}
