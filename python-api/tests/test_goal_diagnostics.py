from __future__ import annotations

import copy
import json

import numpy as np
import pandas as pd
import pytest

from experiments.common import file_sha256
from experiments.goal_diagnostics import diagnose_dataset, propose_hypotheses


def _write(dataset, frame, manifest):
    frame.to_parquet(dataset / "dataset.parquet", index=False)
    manifest["dataset_sha256"] = file_sha256(dataset / "dataset.parquet")
    (dataset / "dataset_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


@pytest.fixture
def dataset(tmp_path):
    directory = tmp_path / "diagnostics"
    directory.mkdir()
    frame = pd.DataFrame({
        "race_date": ["20150101", "20160101", "20230101", "20230201", "20230201", "20260101"],
        "race_id": ["old", "a", "b", "c", "c", "future"],
        "horse_id": ["h", "h", "h", None, "h", "h"],
        "venue": ["old", "Tokyo", "Tokyo", "Kyoto", "Kyoto", "future"],
        "safe_distance": [9, 1, 2, 3, 4, 99],
        "safe_copy": [9, 2, 4, 6, 8, -99],
        "safe_prior_starts": [0, 0, 1, 0, np.nan, 0],
        "safe_days_since_last": [1, np.nan, np.nan, np.nan, np.nan, 1],
        "safe_prev_finish": [1, np.nan, np.nan, np.nan, np.nan, 1],
        "safe_past3_avg_finish": [1, np.nan, np.nan, np.nan, np.nan, 1],
        "safe_past3_count": [1, 0, np.nan, 0, 1, 1],
        "safe_past3_win_rate": [1, np.nan, np.nan, np.nan, np.nan, 1],
        "safe_sex": ["M", "M", None, " ", "M", "future"],
        "finish": [1, 1, 1, 1, 2, 1],
        "odds": [1, 1, 1, 1, 2, 1],
    })
    base = ["safe_distance", "safe_copy", "safe_sex"]
    history = ["safe_prior_starts", "safe_days_since_last", "safe_prev_finish",
               "safe_past3_avg_finish", "safe_past3_count", "safe_past3_win_rate"]
    manifest = {
        "dataset_schema_version": "experiment-jra-flat-dataset-v2",
        "feature_sets": {"M01": base, "M03": base + history, "M05": base + history},
        "feature_groups": {"base": base, "history": history},
        "categorical_features": ["safe_sex"],
        "training_eligibility": {
            "counts": {"input": {"races": 9, "rows": 18, "race_ids": ["secret"]}},
            "exclusion_reason_counts": {"partial": {"races": 1, "rows": 2}},
            "eligible_identifiers": {"race_ids": ["secret"]},
            "arbitrary_metadata": {"horse_ids": ["secret"]},
        },
    }
    _write(directory, frame, manifest)
    return directory, frame, manifest


def test_missingness_requires_verified_history_semantics_and_identity(dataset):
    directory, _, _ = dataset
    report = diagnose_dataset(directory, [2023])
    assert report["schema"] == "goal-loop-diagnostics-v1"
    assert (report["row_count"], report["race_count"]) == (4, 3)
    for name in ("safe_days_since_last", "safe_prev_finish", "safe_past3_avg_finish",
                 "safe_past3_win_rate"):
        stats = report["features"][name]
        assert stats["missing_reason_counts"]["structural_missing"] == 1
        assert stats["missing_reason_counts"]["unknown"] == 3
        assert sum(stats["missing_reason_counts"].values()) == 4
        assert stats["by_month"] == {"2016-01": 1.0, "2023-01": 1.0, "2023-02": 1.0}
    for name in ("safe_prior_starts", "safe_past3_count", "safe_sex"):
        stats = report["features"][name]
        assert stats["missing_reason_counts"]["structural_missing"] == 0
        assert stats["missing_reason_counts"]["unknown"] == stats["missing_count"]
        assert all(stats["missing_reason_counts"][cause] == 0
                   for cause in ("fetch_failed", "parse_failed", "storage_failed", "transform_failed"))
    assert report["features"]["safe_sex"]["zero_fraction"] is None
    assert report["features"]["safe_sex"]["missing_fraction"] == 0.5
    assert report["features"]["safe_prior_starts"]["zero_fraction"] == 0.5
    assert report["features"]["safe_days_since_last"]["constant"] is True
    assert report["features"]["safe_sex"]["by_venue"] == {"Kyoto": 0.5, "Tokyo": 0.5}
    assert report["training_eligibility"] == {
        "counts": {"input": {"races": 9, "rows": 18}},
        "exclusion_reason_counts": {"partial": {"races": 1, "rows": 2}},
    }
    assert "secret" not in json.dumps(report)
    assert report["no_future_rows_read"] is True
    assert report["acquisition_requests_performed"] == 0
    assert "selected cohort" in report["provenance"]["caveat"]
    json.dumps(report, allow_nan=False)
    assert any(pair["left"] == "safe_copy" and pair["right"] == "safe_distance"
               and pair["correlation"] == pytest.approx(1) for pair in report["high_correlation"])
    assert all(pair["left"] < pair["right"] for pair in report["high_correlation"])


def test_temporal_projection_filters_before_inspection_and_ignores_outcomes(dataset, monkeypatch):
    directory, frame, manifest = dataset
    original_read = pd.read_parquet
    calls = []

    def read(path, **kwargs):
        calls.append(kwargs)
        selected = original_read(path, **kwargs)
        assert selected.race_date.between("20160101", "20231231").all()
        assert not {"finish", "odds"}.intersection(selected.columns)
        return selected

    monkeypatch.setattr(pd, "read_parquet", read)
    before = diagnose_dataset(directory, [2023])
    frame.loc[frame.race_date >= "20260101", "safe_distance"] = -999999
    frame.loc[frame.race_date >= "20260101", "safe_days_since_last"] = np.nan
    frame["finish"] = -888
    _write(directory, frame, manifest)
    after = diagnose_dataset(directory, [2023])
    assert before.pop("dataset_sha256") != after.pop("dataset_sha256")
    assert before == after
    assert calls[0]["filters"] == [
        ("race_date", ">=", "20160101"), ("race_date", "<=", "20231231")]


@pytest.mark.parametrize("missing", ["horse_id", "safe_prior_starts", "venue"])
def test_optional_context_does_not_fabricate_structural_evidence(dataset, missing):
    directory, frame, manifest = dataset
    frame = frame.drop(columns=missing)
    if missing == "safe_prior_starts":
        for features in manifest["feature_sets"].values():
            if missing in features:
                features.remove(missing)
    _write(directory, frame, manifest)
    report = diagnose_dataset(directory, [2023])
    if missing == "venue":
        assert report["features"]["safe_days_since_last"]["by_venue"] == {}
    else:
        assert report["features"]["safe_days_since_last"]["missing_reason_counts"]["unknown"] == 4


def test_hash_and_unsafe_features_fail_closed(dataset):
    directory, frame, manifest = dataset
    manifest["dataset_sha256"] = "wrong"
    (directory / "dataset_manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="hash mismatch"):
        diagnose_dataset(directory, [2023])
    manifest["feature_sets"]["M03"].append("finish")
    _write(directory, frame, manifest)
    with pytest.raises(ValueError, match="unsafe"):
        diagnose_dataset(directory, [2023])


def test_empty_window_and_invalid_boundaries_fail_explicitly(dataset):
    directory, _, _ = dataset
    with pytest.raises(ValueError, match="empty development"):
        diagnose_dataset(directory, [2020], train_start_year=2019)
    for years in ([], [True], ["2023"]):
        with pytest.raises(ValueError, match="development_years"):
            diagnose_dataset(directory, years)
    with pytest.raises(ValueError, match="train_start_year"):
        diagnose_dataset(directory, [2023], train_start_year=2024)


def test_proposals_are_bounded_deterministic_and_never_acquire(dataset, monkeypatch):
    directory, _, manifest = dataset
    report = diagnose_dataset(directory, [2023])
    original = copy.deepcopy((report, manifest))

    def forbidden(*args, **kwargs):
        pytest.fail("proposal generation must not read data or perform acquisition")

    monkeypatch.setattr(pd, "read_parquet", forbidden)
    proposals = propose_hypotheses(report, manifest)
    assert proposals == propose_hypotheses(report, manifest)
    assert len(proposals) == len({item["id"] for item in proposals}) == 3
    assert [item["kind"] for item in proposals] == [
        "regularization", "acquisition_review", "feature_ablation"]
    assert proposals[0]["requires_approval"] is False
    assert proposals[0]["params"] == {"num_leaves": 15, "min_data_in_leaf": 80, "lambda_l2": 1}
    assert proposals[1]["requires_approval"] is True
    assert proposals[2]["feature_columns"] == manifest["feature_sets"]["M01"]
    assert proposals[:1] == propose_hypotheses(report, manifest, 1)
    assert proposals[:2] == propose_hypotheses(report, manifest, 2)
    assert all(set(item["feature_columns"]).issubset(report["feature_columns"]) for item in proposals)
    assert all(set(item) == {"id", "kind", "target", "feature_columns", "params",
                             "reason", "evidence", "requires_approval"} for item in proposals)
    assert (report, manifest) == original
    for limit in (0, 4, True, 1.5):
        with pytest.raises(ValueError, match="max_proposals"):
            propose_hypotheses(report, manifest, limit)
    json.dumps(proposals, allow_nan=False)


def test_target_comparison_uses_existing_m03_without_new_features(dataset):
    directory, _, manifest = dataset
    report = diagnose_dataset(directory, [2023])
    manifest["feature_groups"] = {}
    for stats in report["features"].values():
        stats["missing_reason_counts"]["unknown"] = 0
    proposals = propose_hypotheses(report, manifest)
    assert [item["kind"] for item in proposals] == ["regularization", "target_comparison"]
    assert proposals[1]["target"] == "rank"
    assert proposals[1]["feature_columns"] == manifest["feature_sets"]["M03"]
    report["feature_columns"] = ["odds"]
    with pytest.raises(ValueError, match="features"):
        propose_hypotheses(report, manifest)


def test_missing_second_start_and_course_statistics_use_their_own_history_counts(dataset):
    directory, frame, manifest = dataset
    frame["horse_id"] = "known"
    frame["safe_prior_starts"] = [0, 0, 1, 2, np.nan, 4]
    frame["safe_prev2_finish"] = np.nan
    frame["safe_horse_course_starts"] = [0, 0, 0, 1, np.nan, 2]
    frame["safe_horse_course_win_rate"] = np.nan
    for name in ("safe_prev2_finish", "safe_horse_course_starts", "safe_horse_course_win_rate"):
        for key in ("M03", "M05"):
            manifest["feature_sets"][key].append(name)
    _write(directory, frame, manifest)
    report = diagnose_dataset(directory, [2023])
    for name in ("safe_prev2_finish", "safe_horse_course_win_rate"):
        reasons = report["features"][name]["missing_reason_counts"]
        assert reasons["structural_missing"] == 2
        assert reasons["unknown"] == 2
    assert report["features"]["safe_horse_course_starts"]["missing_reason_counts"]["structural_missing"] == 0


def test_entity_zero_history_is_natural_but_missing_entity_context_stays_unknown(dataset):
    directory, frame, manifest = dataset
    frame["horse_id"] = None
    frame["safe_sire_starts"] = [0, 0, np.nan, 3, -1, 3]
    frame["safe_sire_win_rate"] = np.nan
    for name in ("safe_sire_starts", "safe_sire_win_rate"):
        for key in ("M03", "M05"):
            manifest["feature_sets"][key].append(name)
    _write(directory, frame, manifest)
    stats = diagnose_dataset(directory, [2023])["features"]
    assert stats["safe_sire_win_rate"]["missing_reason_counts"]["structural_missing"] == 1
    assert stats["safe_sire_win_rate"]["missing_reason_counts"]["unknown"] == 3
    assert stats["safe_sire_starts"]["missing_reason_counts"]["structural_missing"] == 0


def test_nonzero_prior_starts_do_not_explain_missing_past_last3f_values(dataset):
    directory, frame, manifest = dataset
    frame["horse_id"] = "known"
    frame["safe_past3_count"] = [0, 0, 2, 0, np.nan, 3]
    frame["safe_past3_avg_last3f_time"] = np.nan
    frame["safe_past3_win_rate"] = np.nan
    for key in ("M03", "M05"):
        manifest["feature_sets"][key].append("safe_past3_avg_last3f_time")
    _write(directory, frame, manifest)
    stats = diagnose_dataset(directory, [2023])["features"]
    for name in ("safe_past3_avg_last3f_time", "safe_past3_win_rate"):
        assert stats[name]["missing_reason_counts"]["structural_missing"] == 2
        assert stats[name]["missing_reason_counts"]["unknown"] == 2


def test_invalid_observation_counts_do_not_explain_missing_second_start(dataset):
    directory, frame, manifest = dataset
    frame["horse_id"] = "known"
    frame["safe_prior_starts"] = [0, 0.5, -1, np.nan, np.inf, 0]
    frame["safe_prev2_finish"] = np.nan
    for key in ("M03", "M05"):
        manifest["feature_sets"][key].append("safe_prev2_finish")
    _write(directory, frame, manifest)
    reasons = diagnose_dataset(directory, [2023])["features"]["safe_prev2_finish"]["missing_reason_counts"]
    assert reasons["structural_missing"] == 0
    assert reasons["unknown"] == 4
