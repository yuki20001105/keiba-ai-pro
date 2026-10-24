"""Synthetic metrics only; never a learner, live DB, network or publication."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("repaired_context_evaluation", ROOT / "scripts/repaired_context_evaluation.py")
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def predictions(*, year=2025, score_reverse=False, probabilities=True):
    rows = []
    for day_number in (1, 2):
        for number in (1, 2, 3):
            rows.append({"race_id": f"{year}050101{day_number:02}", "race_date": f"{year}010{day_number}",
                "horse_number": number, "horse_id": f"h{number}", "finish": number, "num_horses": 3,
                "venue": "東京", "surface": "turf", "field_condition": "良", "odds": [2., 5., np.nan][number-1],
                "score": number if score_reverse else -number, "squared_error": .04,
                "probability": [.6, .3, .1][number-1] if probabilities else np.nan})
    return pd.DataFrame(rows)


def test_fixed_five_feature_groups():
    assert list(evaluation.groups()) == list(evaluation.GROUP_NAMES)
    assert [len(v) for v in evaluation.groups().values()] == [46, 60, 73, 53, 80]
    assert not any("odds" in name or "market" in name for columns in evaluation.groups().values() for name in columns)


def test_annual_partitions_are_chronological_and_capped():
    frame = pd.DataFrame({"race_date": ["20130101", "20241231", "20250101", "20251231", "20260101", "20260930", "20261001"]})
    split = evaluation.partition_positions(frame, 2026)
    assert split["TRAIN"].tolist() == [0, 1]
    assert split["CAL"].tolist() == [2, 3]
    assert split["TEST"].tolist() == [4, 5]
    with pytest.raises(ValueError, match="fixed-annual"):
        evaluation.partition_positions(frame, 2026, "20261031")


def test_accuracy_probability_and_odds_diagnostics_are_separate():
    races, valid = evaluation.race_metrics(predictions())
    accuracy = evaluation.accuracy(races)
    assert accuracy["rank_correlation"] == 1.
    assert accuracy["rmse_speed_mps"] == pytest.approx(.2)
    probability = evaluation.probability_summary(races, valid)
    assert probability["nll"] == pytest.approx(-np.log(.6))
    assert probability["brier"] == pytest.approx(.26)
    assert probability["selection_ece"] == pytest.approx(.4)
    assert probability["all_runner_ece"] == pytest.approx((.4 + .3 + .1) / 3)
    assert sum(row["count"] for row in probability["all_runner_probability_calibration"]) == 6
    assert sum(row["count"] for row in probability["top1_probability_calibration"]) == 2
    unknown = probability["all_runner_odds_calibration"][-1]
    assert unknown["band"] == "missing_or_invalid" and unknown["count"] == 2
    assert probability["top1_odds_calibration"][0]["count"] == 2
    assert probability["calibration_retrained_in_aggregation"] is False
    json.dumps(probability, allow_nan=False)


def test_dead_heat_is_excluded_only_from_probability():
    frame = predictions()
    frame.loc[frame.race_date.eq("20250101") & frame.horse_number.eq(2), "finish"] = 1
    races, valid = evaluation.race_metrics(frame)
    assert len(races) == 2 and len(valid) == 3
    assert evaluation.probability_summary(races, valid)["race_count"] == 1


def test_unavailable_calibration_is_null_not_zero_and_serializes():
    races, valid = evaluation.race_metrics(predictions(probabilities=False))
    result = evaluation.probability_summary(races, valid)
    assert result["status"] == "unavailable" and result["nll"] is None
    assert result["all_runner_ece"] is None and result["selection_ece"] is None
    assert result["top1_probability_calibration"][0]["mean_predicted"] is None
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("corruption", ["partial_probability", "probability_sum", "runner_count", "negative_error", "duplicate"])
def test_incomplete_or_inconsistent_predictions_fail_closed(corruption):
    frame = predictions()
    if corruption == "partial_probability":
        frame.loc[0, "probability"] = np.nan
    elif corruption == "probability_sum":
        frame.loc[0, "probability"] = .8
    elif corruption == "runner_count":
        frame.loc[0, "num_horses"] = 4
    elif corruption == "negative_error":
        frame.loc[0, "squared_error"] = -.1
    else:
        frame = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError):
        evaluation.race_metrics(frame)


def test_date_cluster_paired_comparison_has_exact_zero_for_identical_inputs():
    races, _ = evaluation.race_metrics(predictions())
    result = evaluation.paired_comparison(races, races.copy(), repetitions=30)
    assert result["rank_delta_ci95"] == [0., 0.]
    assert result["rmse_delta_ci95"] == [0., 0.]
    assert result["probability"]["nll_delta_ci95"] == [0., 0.]
    assert result["probability"]["common_probability_races"] == 2
    json.dumps(result, allow_nan=False)


def test_paired_cohort_mismatch_rejected():
    races, _ = evaluation.race_metrics(predictions())
    with pytest.raises(ValueError, match="identical-whole"):
        evaluation.paired_comparison(races, races.iloc[[0]], repetitions=10)


def test_wilson_empty_is_unknown_and_valid_intervals_bound_rate():
    assert evaluation.wilson_interval(0, 0) == [None, None]
    lower, upper = evaluation.wilson_interval(3, 10)
    assert 0 <= lower < .3 < upper <= 1
    with pytest.raises(ValueError):
        evaluation.wilson_interval(11, 10)


def test_source_guard_requires_self_pin_and_rejects_wrong_fit_source():
    fit, _ = evaluation.modules()
    sources = {**fit.source_hashes(), "scripts/repaired_context_evaluation.py": evaluation.sha(evaluation.__file__)}
    evaluation.validate_sources(sources)
    with pytest.raises(ValueError, match="evaluation-pin"):
        evaluation.validate_sources(fit.source_hashes())
    sources["training.db_context_fit"] = "0" * 64
    with pytest.raises(ValueError, match="fit-source"):
        evaluation.validate_sources(sources)


def worker_fixture(tmp_path, monkeypatch):
    fit, features = evaluation.modules()
    target = pd.concat([predictions(year=year) for year in (2014, 2015, 2016)], ignore_index=True)
    target["distance"] = 1600
    target["time_seconds"] = 100.
    matrix = pd.DataFrame({name: pd.Series(["fixture"] * len(target), dtype="string")
                           if name in features.CATEGORICAL_COLUMNS else np.ones(len(target), dtype=np.float32)
                           for name in features.FEATURE_COLUMNS})
    target_path, feature_path = tmp_path / "target.parquet", tmp_path / "features.parquet"
    target.to_parquet(target_path, index=False)
    matrix.to_parquet(feature_path, index=False)
    runner = ROOT / "scripts/repaired_context_evaluation.py"
    sources = {**fit.source_hashes(), "scripts/repaired_context_evaluation.py": evaluation.sha(runner)}
    protocol = {"runner_path": str(runner), "runner_sha256": evaluation.sha(runner),
                "source_hashes": sources, "groups": evaluation.groups()}
    evaluation.save(tmp_path / "protocol.json", protocol)
    payload = {"output": str(tmp_path), "target": str(target_path), "features": str(feature_path),
               "target_sha": evaluation.sha(target_path), "features_sha": evaluation.sha(feature_path),
               "sources": sources, "year": 2016, "group": "baseline46", "period_end": "20260930",
               "protocol_sha": evaluation.sha(tmp_path / "protocol.json"), "runner_sha": evaluation.sha(runner)}
    monkeypatch.setattr(fit, "prepare", lambda target, raw: (target, raw))
    def fake_fit(target, raw, positions, *, columns, n_threads, preset_parallel):
        assert len(columns) == 46 and "odds" not in columns
        assert n_threads == 2 and preset_parallel is False
        assert len(positions["TRAIN"]) == len(positions["CAL"]) == len(positions["TEST"]) == 6
        test = target.iloc[positions["TEST"]].reset_index(drop=True)
        return {"fixture": "not-a-learner"}, {"elapsed_seconds": 0.}, {
            "target": test, "scores": test.score.to_numpy(float),
            "physical_speed": np.full(len(test), 16.), "probabilities": test.probability.to_numpy(float)}
    monkeypatch.setattr(fit, "fit_on_partitions", fake_fit)
    return payload


def test_worker_records_frozen_lineage_and_diagnostic_odds_without_training(tmp_path, monkeypatch):
    payload = worker_fixture(tmp_path, monkeypatch)
    result = evaluation.worker(payload)
    assert result["group"] == "baseline46" and result["test_year"] == 2016
    saved = json.loads((tmp_path / "2016-baseline46-result.json").read_text(encoding="utf-8"))
    assert saved["protocol_sha256"] == payload["protocol_sha"]
    frame = pd.read_parquet(tmp_path / saved["prediction_file"])
    assert frame.odds.isna().sum() == 2 and saved["odds_diagnostic_only"]
    with pytest.raises(ValueError, match="not-overwritten"):
        evaluation.worker(payload)


def test_worker_refuses_modified_matrix_before_any_fit(tmp_path, monkeypatch):
    payload = worker_fixture(tmp_path, monkeypatch)
    payload["features_sha"] = "0" * 64
    with pytest.raises(ValueError, match="matrix-drift"):
        evaluation.worker(payload)


def test_aggregate_reads_only_hash_bound_55_saved_results(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluation, "validate_sources", lambda _sources: None)
    protocol = {"groups": evaluation.groups(), "test_years": list(range(2016, 2027)),
                "source_hashes": {"fixture": "not_real_sources_no_training"}, "runner_sha256": "fixture",
                "bootstrap_repetitions": 10, "bootstrap_seed": 42}
    evaluation.save(tmp_path / "protocol.json", protocol)
    digest = evaluation.sha(tmp_path / "protocol.json")
    for name in evaluation.GROUP_NAMES:
        for year in protocol["test_years"]:
            filename = f"{year}-{name}-predictions.parquet"
            predictions(year=year).to_parquet(tmp_path / filename, index=False)
            bundle_name = f"{year}-{name}-research.joblib"
            (tmp_path / bundle_name).write_bytes(b"fixture-not-a-real-learner")
            evaluation.save(tmp_path / f"{year}-{name}-result.json", {"group": name, "test_year": year,
                "prediction_file": filename, "prediction_sha256": evaluation.sha(tmp_path / filename),
                "protocol_sha256": digest, "research_source_hashes": protocol["source_hashes"], "runner_sha256": "fixture",
                "research_bundle": bundle_name, "research_bundle_sha256": evaluation.sha(tmp_path / bundle_name),
                "evaluation": {"primary": {"rank_correlation": 1., "rmse": .2}}, "probability_metrics": {},
                "probability_validation": {}, "selected_preset": "fixture", "elapsed_seconds": 0.})
    result = evaluation.aggregate(tmp_path, protocol)
    assert result["summaries"]["baseline46"]["accuracy"]["race_count"] == 22
    assert result["pedigree_increment_full80_vs_context73"]["rank_delta"] == 0.
    assert result["roi_evaluated"] is False and result["model_activated"] is False
    json.dumps(result, allow_nan=False)
    path = tmp_path / "2026-pedigree53-result.json"
    item = json.loads(path.read_text(encoding="utf-8"))
    item["prediction_sha256"] = "0" * 64
    path.write_text(json.dumps(item), encoding="utf-8")
    with pytest.raises(ValueError, match="lineage"):
        evaluation.aggregate(tmp_path, protocol)
