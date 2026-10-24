from __future__ import annotations

from copy import deepcopy
import json

import numpy as np
import pandas as pd
import pytest

from feature_provenance.training import NUMERIC, analyze_training, dates, name_key, missingness


def fixture():
    ids = ["201301010101", "202101010101", "202201010101", "202601010101"]
    days = ["20130101", "20210110", "20220110", "20260110"]
    races = pd.DataFrame([{"race_id": rid, "inner_race_id": rid, "data_json_valid": 1, "race_date": day, "num_horses": 2,
        "race_name": "test", "venue": "test", "source_host": "provider", "created_at": "2026-09-01"} for rid, day in zip(ids, days)])
    runners = pd.DataFrame([{"source_row_id": i * 2 + n, "race_id": rid, "inner_race_id": rid, "data_json_valid": 1,
        "horse_id": str(i * 10 + n), "horse_number": n, "horse_name": "ホース" + str(n), "created_at": "2026-09-01"}
        for i, rid in enumerate(ids) for n in (1, 2)])
    entries = [(ids[0], 1, "2012/12/31", "栗坂"), (ids[1], 1, "2021/01/09", "美Ｗ"),
               (ids[1], 2, "2021/01/10", "未知A"), (ids[2], 1, "2022/01/11", "未知B"),
               (ids[2], 2, "2022/02/30", "CW")]
    records = []
    for i, (rid, num, day, course) in enumerate(entries):
        records.append({"id": i + 1, "race_id": rid, "horse_number": num, "horse_name": "ホース" + str(num),
            "training_date": day, "course": course, "track_condition": "良", "rider": "助手",
            **{key: (None if key == "time_6f" else 12.3) for key in NUMERIC}, "position": "", "pace": "馬也", "grade": "B",
            "comment": "text", "is_last_training": 1, "created_at": "2026-05-01 01:02:03"})
    training = pd.DataFrame(records)
    cohort = runners.loc[runners.race_id.isin(ids[1:3]), ["race_id", "horse_number", "horse_id"]].merge(races[["race_id", "race_date"]], on="race_id")
    return training, races, runners, {"original_market_research": cohort, "released_clean": cohort.copy()}


def analyze(values):
    training, races, runners, cohorts = values
    return analyze_training(training, races, runners, cohorts=cohorts)


def test_all_records_four_temporal_classes_and_never_capture_proof():
    output = analyze(fixture())
    point = output["training_point_in_time_audit.csv"]
    assert point.event_date_class.tolist() == ["A_BEFORE_RACE", "A_BEFORE_RACE", "B_SAME_DATE_TIME_UNKNOWN", "C_AFTER_RACE", "D_DATE_OR_RACE_UNKNOWN"]
    assert not point.source_capture_time_verified.any() and not point.safe_point_in_time.any()
    assert not point.registration_is_availability_proof.any()
    inventory = output["training_data_inventory.json"]
    assert inventory["temporal_feature_gate"] == "NO" and inventory["safe_point_in_time_records"] == 0
    assert inventory["date_ranges"]["training_date"]["invalid_or_missing_count"] == 1


def test_join_names_nfkc_only_conflicts_not_fuzzy_repaired():
    values = fixture()
    values[0].loc[0, "horse_name"] = " ﾎｰｽ1 "
    values[0].loc[1, "horse_name"] = "ホース2"
    values[0].loc[2, "horse_name"] = None
    out = analyze(values)["training_runner_join_audit.csv"]
    assert out.iloc[0].identity_verified
    assert out.iloc[1].runner_join_status == "number_name_conflict" and not out.iloc[1].identity_verified
    assert out.iloc[2].runner_join_status == "number_joinable_name_unverified" and out.iloc[2].number_joinable
    assert name_key("A B") != name_key("AB")


def test_unique_name_only_when_number_missing_not_when_contradictory():
    values = fixture()
    values[0].loc[0, "horse_number"] = np.nan
    values[0].loc[1, "horse_number"] = 99
    out = analyze(values)["training_runner_join_audit.csv"]
    assert out.iloc[0].runner_join_status == "unique_race_name_missing_number" and out.iloc[0].identity_verified
    assert out.iloc[1].runner_join_status == "name_candidate_number_conflict" and not out.iloc[1].identity_verified


def test_duplicate_keys_not_silently_collapsed_and_distinct_workouts_not_called_duplicates():
    training, races, runners, cohorts = fixture()
    duplicate = training.iloc[0].copy(); duplicate["id"] = 10
    second_workout = training.iloc[1].copy(); second_workout["id"] = 11; second_workout["training_date"] = "2021/01/08"
    training = pd.concat([training, pd.DataFrame([duplicate, second_workout])], ignore_index=True)
    output = analyze_training(training, races, runners, cohorts=cohorts)
    meta = output["training_data_inventory.json"]
    assert meta["exact_content_duplicate_rows"] == meta["duplicate_workout_key_rows"] == 2
    assert meta["repeated_race_runner_rows"] == 4 and meta["duplicate_records_deleted"] == 0
    assert len(output["training_runner_join_audit.csv"]) == 7


def test_duplicate_source_number_and_header_are_ambiguous():
    training, races, runners, cohorts = fixture()
    runners = pd.concat([runners, runners.iloc[:1]], ignore_index=True)
    races = pd.concat([races, races.iloc[[1]]], ignore_index=True)
    out = analyze_training(training, races, runners, cohorts=cohorts)
    statuses = out["training_runner_join_audit.csv"].runner_join_status
    assert statuses.iloc[0] == "ambiguous_race_number"
    assert statuses.iloc[1] == statuses.iloc[2] == "canonical_race_id_duplicate"
    assert out["training_data_inventory.json"]["source_runner_key_duplicate_rows"] == 2


def test_missing_structural_unknown_and_no_cross_other_course_pooling():
    values = fixture()
    values[0].loc[2, "time_4f"] = 10.
    values[0].loc[3, "time_4f"] = 90.
    out = analyze(values)
    missing = out["training_course_missingness.csv"]
    assert missing.structural_missing_confirmed_count.eq(0).all()
    stats = out["training_course_statistics.csv"]
    other = stats.loc[stats.scope.eq("course") & stats.course_group.eq("OTHER") & stats.field.eq("time_4f")]
    assert dict(zip(other.course_key, other["mean"])) == {"未知A": 10., "未知B": 90.}
    assert stats.stored_distance_label_verified.eq(False).all()


def test_numeric_missing_invalid_zero_and_categorical_grades_separate():
    values = fixture()
    values[0]["time_4f"] = pd.Series([None, 0, -1, "--", float("inf")], dtype=object)
    frame = missingness(values[0]).set_index("field")
    assert frame.loc["time_4f", "missing_count"] == 2
    assert frame.loc["time_4f", "invalid_count"] == 3 and frame.loc["time_4f", "zero_count"] == 1
    assert frame.loc["grade", "invalid_count"] == 0
    assert frame.loc["position", "blank_count"] == 5


def test_four_year_axes_include_out_of_range_and_do_not_infer_date_from_id():
    values = fixture()
    values[1].loc[0, "race_date"] = "20140101"
    out = analyze(values)
    axes = out["training_year_axes.csv"]
    assert axes.loc[axes.year_axis.eq("training_date_year") & axes.year.eq("2012"), "rows"].iloc[0] == 1
    assert axes.loc[axes.year_axis.eq("canonical_race_date_year") & axes.year.eq("2014"), "rows"].iloc[0] == 1
    assert axes.loc[axes.year_axis.eq("race_id_prefix_not_date") & axes.year.eq("2013"), "rows"].iloc[0] == 1
    assert all(axes.groupby("year_axis").rows.sum() == len(values[0]))


def test_all_canonical_races_pit_includes_no_training_and_no_runner_headers():
    values = fixture()
    values = (values[0], values[1], values[2].loc[~values[2].race_id.eq("202601010101")], values[3])
    out = analyze(values)
    pit = out["training_race_point_in_time_audit.csv"].set_index("race_id")
    assert len(pit) == 4 and pit.loc["202601010101", "race_pit_class"] == "D_NOT_AVAILABLE"
    coverage = out["training_yearly_coverage.csv"]
    row = coverage.loc[coverage.scope.eq("all_canonical") & coverage.year.eq(2026)].iloc[0]
    assert row.total_races == 1 and row.total_runners == 2 and row.known_runner_key_rows == 0
    assert row.training_runner_coverage_percent == 0 and row.all_runner_training_races == 0


def test_input_values_not_imputed_or_mutated():
    values = fixture()
    originals = [v.copy(deep=True) for v in values[:3]]
    output = analyze(values)
    for before, after in zip(originals, values): pd.testing.assert_frame_equal(before, after)
    candidates = output["training_feature_candidates.json"]
    assert candidates["gate"] == "NO" and all(not c["permitted_for_current_model"] for c in candidates["candidates"])
    json.dumps(output["training_data_inventory.json"], allow_nan=False)


@pytest.mark.parametrize("value,valid", [("2020/02/29", True), ("2021/02/29", False), ("2021/02/30", False),
    ("2021/01/01suffix", False), ("01/01", False), (None, False), ("2012/12/31", True)])
def test_full_calendar_date_validation_without_year_guess(value, valid):
    assert bool(dates(pd.Series([value])).notna().iloc[0]) == valid
