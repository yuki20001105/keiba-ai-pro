from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from experiments.dataset import EVALUATION_COLUMNS
from experiments.features import ALLOWED_FEATURES, engineer_features
from experiments.search_features import (
    SEARCH_FEATURES, SEARCH_FEATURE_GROUPS, SOURCE_FEATURES,
    build_search_dataset, derive_search_features,
)


def safe_frame():
    rows = []
    for index, day in enumerate(("20160101", "20160120", "20160210", "20160310"), start=1):
        rid = f"2016050101{index:02d}"
        for number in (1, 2, 3):
            rows.append(dict(race_id=rid, race_date=day, horse_id=f"H{number}", horse_number=number,
                             finish=number, time_seconds=95 + number + index, distance=1600,
                             surface="turf", venue="東京", num_horses=3, odds=number + 1,
                             popularity=number, tansho_payout=220 if number == 1 else np.nan,
                             jockey_id=f"J{number}", trainer_id="T", sire="S", damsire="D",
                             last_3f_time=34 + number))
    frame, features = engineer_features(pd.DataFrame(rows))
    return frame[EVALUATION_COLUMNS + features["feature_sets"]["M03"]], features


def source_dataset(path):
    frame, features = safe_frame()
    path.mkdir()
    frame.to_parquet(path / "dataset.parquet", index=False)
    manifest = {
        "schema_version": 1, "dataset_schema_version": "experiment-jra-flat-dataset-v2",
        "dataset_file": "dataset.parquet", "dataset_path": str(path / "dataset.parquet"),
        "dataset_sha256": hashlib.sha256((path / "dataset.parquet").read_bytes()).hexdigest(),
        "snapshot_sha256": "a" * 64, "eligibility_sha256": "b" * 64,
        "row_count": len(frame), "race_count": int(frame.race_id.nunique()),
        "source_contract": {"immutable": True, "snapshot_sha256": "a" * 64},
        "scope": {"jurisdiction": "JRA", "race_type": "flat"},
        "training_eligibility": {"policy_version": "training-eligibility-v1"},
        "evaluation_only_columns": EVALUATION_COLUMNS.copy(),
        "payout_scope": "winner_only", "cohort_policy": "complete_runner_target_cohort",
        **features,
    }
    save_manifest(path, manifest)
    return frame, manifest


def save_manifest(path, manifest):
    (path / "dataset_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def rewrite_frame(path, frame, manifest):
    frame.to_parquet(path / "dataset.parquet", index=False)
    manifest["dataset_sha256"] = hashlib.sha256((path / "dataset.parquet").read_bytes()).hexdigest()
    save_manifest(path, manifest)


def first_race():
    frame, _ = safe_frame()
    return frame.iloc[:3].copy()


def test_29_candidates_have_explicit_sources_and_groups():
    assert len(SEARCH_FEATURES) == len(set(SEARCH_FEATURES)) == 29
    assert SOURCE_FEATURES <= ALLOWED_FEATURES
    assert all(name.startswith("safe_") for name in SEARCH_FEATURES)
    assert not set(SEARCH_FEATURES) & ALLOWED_FEATURES
    assert not SOURCE_FEATURES & {"finish", "time_seconds", "odds", "popularity", "tansho_payout"}
    assert [len(group) for group in SEARCH_FEATURE_GROUPS.values()] == [16, 4, 3, 2, 4]


def test_build_preserves_parent_bytes_values_cohort_and_provenance(tmp_path):
    source, target = tmp_path / "source", tmp_path / "search"
    original, parent = source_dataset(source)
    original_bytes = {file.name: file.read_bytes() for file in source.iterdir()}
    manifest = build_search_dataset(source, target)
    result = pd.read_parquet(target / "dataset.parquet")
    pd.testing.assert_frame_equal(result[original.columns], original)
    assert {file.name: file.read_bytes() for file in source.iterdir()} == original_bytes
    assert {file.name for file in target.iterdir()} == {"dataset.parquet", "dataset_manifest.json"}
    assert manifest == json.loads((target / "dataset_manifest.json").read_text(encoding="utf-8"))
    assert manifest["row_count"] == parent["row_count"]
    assert manifest["race_count"] == parent["race_count"]
    assert len(manifest["feature_allowlist"]) == 101
    assert manifest["parent_feature_sets"] == parent["feature_sets"]
    assert len(manifest["parent_feature_sets"]["M03"]) == 72
    assert not set(manifest["parent_feature_sets"]["M03"]) & set(SEARCH_FEATURES)
    assert manifest["feature_sets"]["M03"] == manifest["feature_sets"]["M05"] == manifest["feature_allowlist"]
    assert manifest["feature_sets"]["M01"] == parent["feature_sets"]["M01"]
    assert manifest["feature_sets"]["M02"] == parent["feature_sets"]["M02"]
    for key in ("source_contract", "point_in_time_audit", "snapshot_sha256", "eligibility_sha256",
                "training_eligibility", "scope", "payout_scope", "cohort_policy", "categorical_features"):
        assert manifest[key] == parent[key]
    contract = manifest["search_feature_contract"]
    assert contract["source_dataset_sha256"] == parent["dataset_sha256"]
    assert contract["source_manifest_sha256"] == hashlib.sha256(original_bytes["dataset_manifest.json"]).hexdigest()
    assert contract["uses_current_outcomes_or_market"] is False
    assert contract["label_based_selection"] is False
    assert manifest["dataset_sha256"] == hashlib.sha256((target / "dataset.parquet").read_bytes()).hexdigest()


def test_relative_speed_and_finish_direction_use_known_current_field_only():
    frame = first_race()
    frame["safe_prev_speed_mps"] = [10, 20, np.nan]
    frame["safe_past3_avg_finish"] = [5, 2, np.nan]
    result = derive_search_features(frame)
    assert result.safe_search_rel_prev_speed_rank.tolist()[:2] == [0, 1]
    assert result.safe_search_rel_prev_speed_delta.tolist()[:2] == [-5, 5]
    assert result.safe_search_rel_past3_finish_rank.tolist()[:2] == [0, 1]
    assert result.safe_search_rel_past3_finish_delta.tolist()[:2] == [-1.5, 1.5]
    assert np.isnan(result.safe_search_rel_prev_speed_rank.iloc[2])
    assert np.isnan(result.safe_search_rel_prev_speed_delta.iloc[2])


def test_tied_ranks_are_neutral_and_do_not_use_outcome_tiebreaker():
    frame = first_race()
    frame["safe_prev_speed_mps"] = [16, 16, 16]
    result = derive_search_features(frame)
    assert result.safe_search_rel_prev_speed_rank.eq(0.5).all()
    assert result.safe_search_rel_prev_speed_delta.eq(0).all()


@pytest.mark.parametrize("known", [0, 1])
def test_insufficient_comparators_remain_unknown(known):
    frame = first_race()
    frame["safe_jockey_win_rate"] = [0.2] * known + [np.nan] * (3 - known)
    result = derive_search_features(frame)
    assert result.safe_search_rel_jockey_win_rate_rank.isna().all()
    assert result.safe_search_rel_jockey_win_rate_delta.isna().all()
    assert result.safe_search_missing_jockey_history.tolist() == [0] * known + [1] * (3 - known)


def test_trends_rest_distance_and_support_have_expected_semantics():
    frame = first_race()
    for key, value in {"safe_past3_avg_speed_mps": 17, "safe_past5_avg_speed_mps": 16,
                       "safe_past10_avg_speed_mps": 15, "safe_past3_avg_finish": 4,
                       "safe_past10_avg_finish": 6, "safe_past3_win_rate": 0.5,
                       "safe_past10_win_rate": 0.2, "safe_days_since_last": 20,
                       "safe_distance": 1800, "safe_prev_distance": 1200,
                       "safe_prior_starts": 10}.items():
        frame[key] = value
    result = derive_search_features(frame)
    assert result.safe_search_speed_trend_3_10.eq(2).all()
    assert result.safe_search_speed_trend_5_10.eq(1).all()
    assert result.safe_search_finish_improvement_3_10.eq(2).all()
    assert result.safe_search_win_rate_trend_3_10.iloc[0] == pytest.approx(0.3)
    assert result.safe_search_log_days_since_last.iloc[0] == pytest.approx(np.log1p(20))
    assert result.safe_search_distance_change_ratio.eq(0.5).all()
    assert result.safe_search_log_prior_starts.iloc[0] == pytest.approx(np.log1p(10))


def test_invalid_or_missing_values_never_become_fake_zero_win_rates():
    frame = first_race()
    frame["safe_prev_speed_mps"] = [np.nan, np.inf, -np.inf]
    frame["safe_jockey_win_rate"] = [np.nan, np.nan, np.nan]
    frame["safe_days_since_last"] = [np.nan, 0, -1]
    frame["safe_prev_distance"] = [np.nan, 0, -1200]
    frame["safe_prior_starts"] = [np.nan, -1, 0]
    result = derive_search_features(frame)
    assert result.safe_search_rel_jockey_win_rate_rank.isna().all()
    assert result.safe_search_rel_jockey_win_rate_delta.isna().all()
    assert result.safe_search_log_days_since_last.isna().all()
    assert result.safe_search_distance_change_ratio.isna().all()
    assert result.safe_search_log_prior_starts.iloc[:2].isna().all()
    assert result.safe_search_log_prior_starts.iloc[2] == 0  # observed zero starts, not a zero win rate
    assert result.safe_search_missing_prev.eq(1).all()
    assert not np.isinf(result.to_numpy()).any()


def test_current_outcomes_markets_raw_prev_and_profile_totals_have_no_effect():
    frame, _ = safe_frame()
    expected = derive_search_features(frame)
    altered = frame.copy()
    for name in ("finish", "time_seconds", "odds", "popularity", "tansho_payout", "distance",
                 "prev_race_finish", "horse_total_runs", "last_3f_time", "speed_deviation"):
        altered[name] = np.arange(len(altered)) * 1000 + 999
    observed = derive_search_features(altered)
    pd.testing.assert_frame_equal(expected, observed)


def test_changing_another_race_cannot_change_target_race_candidates():
    frame, _ = safe_frame()
    expected = derive_search_features(frame)
    unrelated = frame.race_id.ne(frame.race_id.iloc[-1])
    frame.loc[unrelated, list(SOURCE_FEATURES)] = 999
    observed = derive_search_features(frame)
    pd.testing.assert_frame_equal(expected.loc[~unrelated], observed.loc[~unrelated])


def test_shuffled_rows_keep_identity_alignment_without_order_dependent_rank():
    frame, _ = safe_frame()
    expected = derive_search_features(frame)
    observed = derive_search_features(frame.sample(frac=1, random_state=42)).sort_index()
    pd.testing.assert_frame_equal(expected, observed)


@pytest.mark.parametrize("issue", ["schema", "hash", "leak", "allowlist", "groups", "provenance"])
def test_untrusted_parent_contract_fails_closed(tmp_path, issue):
    source = tmp_path / "source"
    _, manifest = source_dataset(source)
    if issue == "schema":
        manifest["dataset_schema_version"] = "experiment-jra-flat-dataset-v1"
    elif issue == "hash":
        manifest["dataset_sha256"] = "f" * 64
    elif issue == "leak":
        manifest["point_in_time_audit"]["same_day_future_references"] = 1
    elif issue == "allowlist":
        manifest["feature_sets"]["M03"].append("odds")
    elif issue == "groups":
        manifest["feature_groups"]["rogue"] = ["time_seconds"]
    else:
        manifest.pop("source_contract")
    save_manifest(source, manifest)
    with pytest.raises(ValueError, match="search-"):
        build_search_dataset(source, tmp_path / "search")


@pytest.mark.parametrize("issue", ["rows", "headcount", "duplicate", "missing_feature", "unknown_column", "date"])
def test_parent_cohort_corruption_is_not_silently_changed(tmp_path, issue):
    source = tmp_path / "source"
    frame, manifest = source_dataset(source)
    if issue == "rows":
        frame = frame.iloc[:-1]
    elif issue == "headcount":
        frame.loc[0, "num_horses"] = 5
    elif issue == "duplicate":
        frame.loc[1, "horse_number"] = 1
    elif issue == "missing_feature":
        frame = frame.drop(columns="safe_prev_speed_mps")
    elif issue == "unknown_column":
        frame["safe_injected_result"] = frame.finish
    else:
        frame.loc[0, "race_date"] = "20160102"
    rewrite_frame(source, frame, manifest)
    with pytest.raises(ValueError, match="search-parent-"):
        build_search_dataset(source, tmp_path / "search")


def test_output_never_overwrites_source_or_existing_search(tmp_path):
    source = tmp_path / "source"
    source_dataset(source)
    with pytest.raises(ValueError, match="separate"):
        build_search_dataset(source, source)
    with pytest.raises(ValueError, match="separate"):
        build_search_dataset(source, source / "nested")
    first = build_search_dataset(source, tmp_path / "search")
    second = build_search_dataset(source, tmp_path / "second")
    assert first["dataset_sha256"] == second["dataset_sha256"]
    with pytest.raises(FileExistsError, match="empty"):
        build_search_dataset(source, tmp_path / "search")


def test_failed_manifest_publication_rolls_back_only_new_output(tmp_path, monkeypatch):
    source = tmp_path / "source"
    _, manifest = source_dataset(source)
    import experiments.search_features as module
    original_link = module.os.link

    def fail_manifest_link(src, dst):
        if dst.name == "dataset_manifest.json":
            raise OSError("fixture publication failure")
        return original_link(src, dst)

    monkeypatch.setattr(module.os, "link", fail_manifest_link)
    with pytest.raises(OSError, match="fixture publication failure"):
        build_search_dataset(source, tmp_path / "search")
    assert not list((tmp_path / "search").iterdir())
    assert hashlib.sha256((source / "dataset.parquet").read_bytes()).hexdigest() == manifest["dataset_sha256"]


@pytest.mark.parametrize("issue", ["missing", "empty", "race_id"])
def test_incomplete_derivation_input_is_rejected(issue):
    frame = first_race()
    if issue == "missing":
        frame = frame.drop(columns="safe_prev_speed_mps")
    elif issue == "empty":
        frame = frame.iloc[:0]
    else:
        frame.loc[0, "race_id"] = "unknown"
    with pytest.raises(ValueError, match="search-"):
        derive_search_features(frame)
