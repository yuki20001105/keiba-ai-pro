from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from market_segments.data import F8, SEGMENT_NAMES, prepare_segment_frame, segment_definitions, build_segment_dataset, _sha

LABELS = {"newcomer": ["新馬"], "maiden": ["未勝利"]}


def frames(n=8):
    probabilities = np.arange(n, 0, -1) / np.arange(n, 0, -1).sum()
    clean = pd.DataFrame({"race_id": "202205010101", "race_date": "20220109", "horse_number": range(1, n+1),
                          "expected_runner_count": n, "normalized_market_probability": probabilities,
                          "h04_score": np.arange(n, dtype=float), "odds": 1 / probabilities,
                          "popularity": np.arange(1, n+1), "market_rank": np.arange(n)+.5,
                          "h04_rank": np.arange(n)+.25, "rank_difference": np.arange(n)+.75,
                          "finish": list(range(1, n+1))})
    source = clean[["race_id", "race_date", "horse_number"]].copy()
    for name in F8:
        source[name] = .5
    source["safe_prior_starts"] = 3
    source["safe_days_since_last"] = 39
    source["safe_age"] = 3
    source["safe_horse_surface_starts"] = 2
    source["safe_surface"] = "turf"
    source["safe_race_class"] = "未勝利"
    source["safe_field_condition"] = "良"
    source["safe_distance_change"] = 0
    return clean, source


def prepare(clean=None, source=None, **kwargs):
    if clean is None:
        clean, source = frames()
    return prepare_segment_frame(clean, source, class_labels=LABELS, **kwargs)


def test_exact18_segments_fixedF8_no_rows_dropped_or_old_rank_changes():
    clean, source = frames()
    original_clean, original_source = clean.copy(deep=True), source.copy(deep=True)
    output, races, runners, manifest = prepare(clean, source)
    assert manifest["segment_count"] == 18 and manifest["F8"] == F8
    assert len(SEGMENT_NAMES) == 18 and len(output) == len(clean)
    assert races.age_3_only.item() and not races.age_2_only.item()
    assert races.maiden.item() and not races.newcomer.item()
    assert races.small_field.item() and not races.large_field.item()
    assert races.any_large_market_H04_rank_divergence.item()
    for name in ["market_rank", "h04_rank", "rank_difference"]:
        pd.testing.assert_series_equal(output[name], clean[name])
    pd.testing.assert_frame_equal(clean, original_clean)
    pd.testing.assert_frame_equal(source, original_source)
    json.dumps(manifest, allow_nan=False)


def test_outcome_mutation_cannot_change_any_segment():
    clean, source = frames()
    first = prepare(clean, source)[1]
    clean["finish"] = [np.nan, -1, "not-a-result", 500, 1, 1, 1, 1]
    clean["tansho_payout"] = 999999999
    clean["is_win"] = True
    second = prepare(clean, source)[1]
    pd.testing.assert_frame_equal(first, second)


def test_mixed_age_not_called_three_year_old_only():
    clean, source = frames()
    source.loc[0, "safe_age"] = 4
    races = prepare(clean, source)[1]
    assert not races.age_2_only.item() and not races.age_3_only.item()


def test_missing_age_is_unknown_not_false_when_remaining_all_three():
    clean, source = frames()
    source.loc[0, "safe_age"] = np.nan
    races = prepare(clean, source)[1]
    assert pd.isna(races.age_3_only.item())
    assert not races.age_3_only__evaluable.item()


def test_history_half_threshold_uses_unknown_count_bounds():
    clean, source = frames()
    source["safe_prior_starts"] = [1, 2, 1, np.nan, 3, 4, 5, 6]
    assert pd.isna(prepare(clean, source)[1].many_one_two_observed_history.item())
    source.loc[3, "safe_prior_starts"] = 2
    assert prepare(clean, source)[1].many_one_two_observed_history.item()
    source.loc[3, "safe_prior_starts"] = 3
    assert not prepare(clean, source)[1].many_one_two_observed_history.item()


def test_exact_distance_layoff_first_surface_and_zero_history_boundaries():
    clean, source = frames()
    source.loc[0, "safe_prior_starts"] = 0
    source.loc[1, "safe_horse_surface_starts"] = 0
    source.loc[2, "safe_distance_change"] = -400
    source.loc[3, "safe_days_since_last"] = 180
    races = prepare(clean, source)[1]
    assert races.any_zero_observed_history.item()
    assert races.any_first_turf.item()
    assert not races.any_first_dirt.item()
    assert races.any_large_distance_change.item() and races.any_long_layoff.item()


def test_no_distance_proxy_for_surface_switch():
    clean, source = frames()
    source["safe_surface"] = "dirt"
    source["safe_distance_change"] = 1000
    races = prepare(clean, source)[1]
    assert pd.isna(races.any_turf_to_dirt.item())
    assert not races.any_turf_to_dirt__evaluable.item()
    assert not races.any_dirt_to_turf.item()  # Current dirt cannot be dirt-to-turf.


def prior_fixture():
    clean, source = frames()
    source["safe_surface"] = "dirt"
    mapping = clean.loc[:0, ["race_id", "horse_number"]].copy()
    mapping["previous_surface"] = "turf"
    mapping["source_race_date"] = "20211201"
    mapping["source_race_id"] = "202105050101"
    provenance = {"snapshot_sha256": "a"*64, "history_boundary": "source_race_date < target_race_date"}
    return clean, source, mapping, provenance


def test_dated_prior_surface_mapping_proves_switch_for_one_runner():
    clean, source, mapping, provenance = prior_fixture()
    _, races, runners, manifest = prepare(clean, source, prior_surface=mapping, prior_surface_provenance=provenance, source_snapshot_sha256="a"*64)
    assert races.any_turf_to_dirt.item()  # Proven TRUE despite 7 unknown runners.
    assert runners.any_turf_to_dirt.isna().sum() == 7
    assert manifest["prior_surface"]["verified_rows"] == 1


@pytest.mark.parametrize("mutation", ["same_day", "future", "not_latest", "snapshot", "extra_key"])
def test_prior_surface_unproven_mapping_rejected(mutation):
    clean, source, mapping, provenance = prior_fixture()
    if mutation == "same_day":
        mapping["source_race_date"] = "20220109"
        mapping["source_race_id"] = "202205010101"
    elif mutation == "future":
        mapping["source_race_date"] = "20220110"
        mapping["source_race_id"] = "202205010201"
    elif mutation == "not_latest":
        mapping["source_race_date"] = "20211130"
    elif mutation == "snapshot":
        provenance["snapshot_sha256"] = "b"*64
    else:
        mapping["race_id"] = "202205990101"
    with pytest.raises(ValueError):
        prepare(clean, source, prior_surface=mapping, prior_surface_provenance=provenance, source_snapshot_sha256="a"*64)


def test_exact_class_and_going_not_substring_matching():
    clean, source = frames()
    source["safe_race_class"] = "元未勝利"
    source["safe_field_condition"] = "稍重"
    races = prepare(clean, source)[1]
    assert not races.maiden.item()
    assert not races.heavy_going.item() and not races.bad_going.item()
    source["safe_field_condition"] = "重"
    assert prepare(clean, source)[1].heavy_going.item()
    source.loc[0, "safe_field_condition"] = "良"
    assert pd.isna(prepare(clean, source)[1].heavy_going.item())


@pytest.mark.parametrize("label", ["不", "不良"])
def test_fixed_bad_going_aliases_are_explicit(label):
    clean, source = frames()
    source["safe_field_condition"] = label
    assert prepare(clean, source)[1].bad_going.item()
    assert segment_definitions(LABELS)["going_labels"] == {"heavy": ["重"], "bad": ["不", "不良"]}


def test_longshot_is_any_runner_broad_whole_race_condition():
    clean, source = frames(16)
    clean.loc[9, ["popularity", "odds"]] = [10, 30]
    races = prepare(clean, source)[1]
    assert races.any_longshot.item() and races.large_field.item()


@pytest.mark.parametrize("mutation", ["source_missing", "source_date", "source_duplicate", "future", "partial", "feature_conflict"])
def test_strict_key_and_cohort_boundary(mutation):
    clean, source = frames()
    if mutation == "source_missing":
        source = source.iloc[1:]
    elif mutation == "source_date":
        source["race_date"] = "20220110"
    elif mutation == "source_duplicate":
        source = pd.concat([source, source.iloc[:1]], ignore_index=True)
    elif mutation == "future":
        source["race_date"] = "20230101"
    elif mutation == "partial":
        clean = clean.iloc[1:]
    else:
        clean["safe_weight_change"] = 999
    with pytest.raises(ValueError):
        prepare(clean, source)


def test_gate_is_checked_before_reading_dataset_or_creating_output(tmp_path):
    quality = tmp_path / "quality.json"
    quality.write_text(json.dumps({"MODEL_RESEARCH_READY": False}), encoding="utf-8")
    with pytest.raises(ValueError, match="gate-not-PASS"):
        build_segment_dataset(tmp_path/"does-not-exist.parquet", tmp_path/"source", tmp_path/"output",
            quality_summary_path=quality, expected_quality_sha256=_sha(quality), class_labels=LABELS)
    assert not (tmp_path/"output").exists()


def test_pass_summary_alone_cannot_bypass_independent_release(tmp_path):
    quality = tmp_path / "quality.json"
    quality.write_text(json.dumps({"MODEL_RESEARCH_READY": True}), encoding="utf-8")
    with pytest.raises(ValueError, match="release-binding"):
        build_segment_dataset(tmp_path/"does-not-exist.parquet", tmp_path/"source", tmp_path/"output",
            quality_summary_path=quality, expected_quality_sha256=_sha(quality), class_labels=LABELS)
