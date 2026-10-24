"""Finite feature exploration contracts on synthetic metrics; never actual fits."""
from copy import deepcopy
import importlib.util
import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

from training.db_shared_features import FEATURE_COLUMNS


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/db_shared_feature_optimization_design.py"
spec = importlib.util.spec_from_file_location("fixture_db_shared_feature_optimization_design", SCRIPT)
design = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = design
spec.loader.exec_module(design)


def records_fixture():
    return pd.DataFrame([
        {"split_id": split, "race_id": day + "0001", "race_date": day,
         "runner_count": runners, "rank_correlation": rho, "squared_speed_error_sum": squared}
        for split, dates in [("DEV2020", ["20200101", "20201231"]),
                             ("DEV2021", ["20210101", "20211231"]),
                             ("DEV2022partial", ["20220101", "20220521"])]
        for day, runners, rho, squared in zip(dates, (2, 18), (.5, .7), (8., 0.))
    ])


def summaries_fixture(specs=None, values=None):
    specs = specs or design.initial_candidates(list(FEATURE_COLUMNS))
    values = values or {}
    records = {}
    for candidate in specs:
        frame = records_fixture()
        if candidate["id"] in values:
            rho, rmse = values[candidate["id"]]
            frame["rank_correlation"] = rho
            frame["squared_speed_error_sum"] = rmse ** 2 * frame.runner_count
        records[candidate["id"]] = frame
    return design.summarize_candidates(specs, records, list(design.FOLD_IDS))


def test_initial_family_has_exact_68_unique_subsets_of_actual47_with_closed_groups():
    assert tuple(FEATURE_COLUMNS) == design.CANONICAL_COLUMNS
    rows = design.initial_candidates(list(FEATURE_COLUMNS))
    assert len(rows) == 68
    assert rows[0] == {"id": "all47", "feature_columns": list(FEATURE_COLUMNS), "removed_features": [], "kind": "baseline"}
    assert len({tuple(row["feature_columns"]) for row in rows}) == 68
    assert sum(row["kind"] == "leave_one_out" for row in rows) == 47
    assert sum(row["kind"] == "group_drop" for row in rows) == 8
    assert sum(row["kind"] == "compact" for row in rows) == 12
    assert [len(group) for group in design.FEATURE_GROUPS.values()] == [9, 7, 2, 4, 4, 7, 7, 7]
    assert sorted(name for group in design.FEATURE_GROUPS.values() for name in group) == sorted(FEATURE_COLUMNS)
    assert not any(token in name for name in FEATURE_COLUMNS for token in ("odds", "popularity", "market", "payout"))


@pytest.mark.parametrize("change", [lambda c: c[::-1], lambda c: c[:-1], lambda c: c + [c[0]],
                                    lambda c: ["unknown", *c[1:]], lambda c: "not-a-list"])
def test_canonical_order_or_unknown_columns_cannot_be_silently_changed(change):
    with pytest.raises(ValueError, match="canonical47"):
        design.initial_candidates(change(list(FEATURE_COLUMNS)))


def test_fixed_folds_are_whole_dates_inside_train_and_never_see_eval_year():
    rows = design.selection_folds()
    assert [row["split_id"] for row in rows] == list(design.FOLD_IDS)
    assert [row["train_date_to"] for row in rows] == ["20191231", "20201231", "20211231"]
    assert [row["eval_date_to"] for row in rows] == ["20201231", "20211231", "20220521"]
    assert all(row["train_date_from"] == "20160101" and row["train_date_to"] < row["eval_date_from"] for row in rows)
    assert design.selection_folds("20220430")[-1]["eval_date_to"] == "20220430"


@pytest.mark.parametrize("cap", ["20220522", "20220100", "20211231", "20220230", True, "2022-05-21"])
def test_train_cap_cannot_enter_known_cal_or_use_invalid_dates(cap):
    with pytest.raises(ValueError):
        design.selection_folds(cap)


def test_pooling_is_race_macro_spearman_and_runner_sse_rmse_not_mean_race_rmse():
    row = summaries_fixture()[0]
    assert row["rank_correlation"] == pytest.approx(.6)
    assert row["rmse_speed_mps"] == pytest.approx(math.sqrt(24 / 60))
    assert row["rmse_speed_mps"] != pytest.approx(1.)
    assert row["selection_scope"] == "TRAIN_ONLY"
    assert row["race_count"] == 6 and row["runner_count"] == 60
    assert row["squared_speed_error_sum"] == 24
    assert len(row["by_fold"]) == 3


@pytest.mark.parametrize("reason", ["missing_candidate", "unknown_candidate", "missing_fold", "cal_row", "test_date", "duplicate_race", "nan", "inf",
                                     "partial_runner", "fractional_runner", "string_metric", "negative_sse", "changed_date", "changed_runner", "unknown_feature", "duplicate_feature"])
def test_summary_rejects_incomplete_foreign_or_leaked_cohorts(reason):
    specs = design.initial_candidates(list(FEATURE_COLUMNS))
    frames = {row["id"]: records_fixture() for row in specs}
    key = specs[1]["id"]
    frame = frames[key]
    if reason == "missing_candidate": frames.pop(key)
    elif reason == "unknown_candidate": frames["unknown"] = records_fixture()
    elif reason == "missing_fold": frames[key] = frame.loc[frame.split_id.ne("DEV2021")]
    elif reason == "cal_row": frame.loc[0, "split_id"] = "CAL"
    elif reason == "test_date": frame.loc[5, "race_date"] = "20240727"
    elif reason == "duplicate_race": frames[key] = pd.concat([frame, frame.iloc[[0]]])
    elif reason == "nan": frame.loc[0, "rank_correlation"] = np.nan
    elif reason == "inf": frame.loc[0, "squared_speed_error_sum"] = np.inf
    elif reason == "partial_runner": frame.loc[0, "runner_count"] = 1
    elif reason == "fractional_runner": frame["runner_count"] = frame.runner_count.astype(float); frame.loc[0, "runner_count"] = 2.5
    elif reason == "string_metric": frame["rank_correlation"] = frame.rank_correlation.astype(object); frame.loc[0, "rank_correlation"] = "0.5"
    elif reason == "negative_sse": frame.loc[0, "squared_speed_error_sum"] = -1
    elif reason == "changed_date": frame.loc[0, "race_date"] = "20200102"
    elif reason == "changed_runner": frame.loc[0, "runner_count"] = 3
    elif reason == "unknown_feature": specs[1]["feature_columns"][0] = "unknown"
    elif reason == "duplicate_feature": specs[1]["feature_columns"].append(specs[1]["feature_columns"][0])
    with pytest.raises(ValueError):
        design.summarize_candidates(specs, frames, list(design.FOLD_IDS))


def test_ranking_first_tiny_tie_band_then_physical_rmse_and_smaller_subset():
    specs = design.initial_candidates(list(FEATURE_COLUMNS))[:4]
    values = {"all47": (.6, .7), specs[1]["id"]: (.61, .704), specs[2]["id"]: (.6099, .69), specs[3]["id"]: (.6097, .68)}
    summaries = summaries_fixture(specs, values)
    assert design.select_candidate(summaries)["id"] == specs[2]["id"]
    assert design.select_candidate(summaries, rank_tolerance=0)["id"] == specs[1]["id"]
    assert design.select_candidate(list(reversed(summaries)))["id"] == specs[2]["id"]


def test_rmse_guard_refuses_high_rank_with_over_one_percent_physical_error_deterioration():
    specs = design.initial_candidates(list(FEATURE_COLUMNS))[:3]
    summaries = summaries_fixture(specs, {"all47": (.6, .7), specs[1]["id"]: (.8, .7071), specs[2]["id"]: (.65, .707)})
    assert design.select_candidate(summaries)["id"] == specs[2]["id"]


def test_equal_metrics_prefer_fewer_features_then_stable_id_and_never_read_roi():
    specs = design.initial_candidates(list(FEATURE_COLUMNS))
    rows = [specs[0], specs[1], specs[2], specs[48]]
    summaries = summaries_fixture(rows, {row["id"]: (.6, .7) for row in rows})
    assert design.select_candidate(summaries)["id"] == specs[48]["id"]
    without_group = summaries[:-1]
    expected = min(specs[1]["id"], specs[2]["id"])
    assert design.select_candidate(without_group)["id"] == expected
    for row in without_group: row.update(roi=999999, test_rank_correlation=-1, cal_rmse=0)
    assert design.select_candidate(without_group)["id"] == expected


@pytest.mark.parametrize("change", [lambda rows: rows[0].update(selection_scope="TEST"),
                                    lambda rows: rows[0].update(rank_correlation=np.nan),
                                    lambda rows: rows[0]["by_fold"][2].update(date_to="20220522"),
                                    lambda rows: rows[0].update(runner_count=61),
                                    lambda rows: rows.append(deepcopy(rows[0]))])
def test_selector_rejects_untrusted_or_inconsistent_summary(change):
    summaries = summaries_fixture(design.initial_candidates(list(FEATURE_COLUMNS))[:2])
    change(summaries)
    with pytest.raises(ValueError):
        design.select_candidate(summaries)


def test_stage2_uses_only_top_three_positive_guarded_loo_and_groups_with_at_most_eight_new_subsets():
    specs = design.initial_candidates(list(FEATURE_COLUMNS))
    loo = [row for row in specs if row["kind"] == "leave_one_out"]
    groups = [row for row in specs if row["kind"] == "group_drop"]
    values = {"all47": (.6, .7)}
    values.update({row["id"]: (.63 - .005 * position, .69) for position, row in enumerate(loo[:3])})
    values.update({row["id"]: (.64 - .005 * position, .69) for position, row in enumerate(groups[:3])})
    values[loo[3]["id"]] = (.9, .8)  # Great rank cannot evade the physical guard.
    summaries = summaries_fixture(specs, values)
    before = deepcopy((specs, summaries))
    stage2 = design.stage2_candidates(list(FEATURE_COLUMNS), specs, summaries)
    assert 1 <= len(stage2) <= 8
    allowed = {row["id"] for row in loo[:3] + groups[:3]}
    assert all(set(row["parent_ids"]).issubset(allowed) and len(row["parent_ids"]) in (2, 3) for row in stage2)
    all_subsets = [tuple(row["feature_columns"]) for row in specs + stage2]
    assert len(all_subsets) == len(set(all_subsets))
    assert stage2 == design.stage2_candidates(list(FEATURE_COLUMNS), list(reversed(specs)), list(reversed(summaries)))
    assert design.stage2_candidates(list(FEATURE_COLUMNS), specs, summaries, maximum=1) == stage2[:1]
    assert (specs, summaries) == before


def test_stage2_without_dev_improvement_does_not_invent_more_trials():
    specs = design.initial_candidates(list(FEATURE_COLUMNS))
    assert design.stage2_candidates(list(FEATURE_COLUMNS), specs, summaries_fixture(specs)) == []


@pytest.mark.parametrize("maximum", [True, -1, 9, 1.5])
def test_stage2_budget_is_closed_and_not_an_unbounded_result_driven_search(maximum):
    specs = design.initial_candidates(list(FEATURE_COLUMNS))
    with pytest.raises(ValueError):
        design.stage2_candidates(list(FEATURE_COLUMNS), specs, summaries_fixture(specs), maximum=maximum)
