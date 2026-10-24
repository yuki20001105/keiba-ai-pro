"""Outcome-blind artifact replay checks using synthetic, isolated artifacts."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/verify_market_edge_outputs.py"
SPEC = importlib.util.spec_from_file_location("market_edge_output_verifier", SCRIPT)
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def features():
    rows = []
    for date, phase in [("20210301", "calibration_seed"), ("20210801", "meta_fit"),
                        ("20211101", "development"), ("20220201", "validation")]:
        for number in (1, 2):
            row = {name: .25 for name in verifier.FEATURES}
            row.update(race_id=date + "0001", race_date=date, horse_number=number,
                       phase=phase, expected_runner_count=2, field_size=2,
                       normalized_market_probability=.6 if number == 1 else .4)
            if phase == "calibration_seed":
                row.update(h04_probability=np.nan, probability_difference=np.nan)
            rows.append(row)
    return pd.DataFrame(rows)[verifier.SAFE_COLUMNS]


@pytest.fixture
def artifacts(tmp_path):
    root = tmp_path.resolve()
    folder = root / "jobs/logistic-dev"
    folder.mkdir(parents=True)
    result_path = folder / "result.json"
    frame = features()
    model = {"model": "logistic", "device": "cpu", "features": verifier.FEATURES,
             "train_rows": 2, "train_races": 1, "train_date_from": "20210801",
             "train_date_to": "20210801", "rounds": None,
             "mean": [0.] * 9, "scale": [1.] * 9, "beta": [0.] * 10}
    result = {"model": "logistic", "device": "cpu", "fold": "development", "feature_count": 9,
              "rounds": 150, "train_rows": 2, "evaluation_rows": 2}
    result_path.write_text(json.dumps(result), encoding="utf-8")
    (folder / "model.json").write_text(json.dumps(model), encoding="utf-8")
    test = frame.loc[frame.phase.eq("development")]
    predictions = test[["race_id", "horse_number"]].copy()
    predictions["probability"] = [.6, .4]
    predictions.to_parquet(folder / "predictions.parquet", index=False)
    spec = {"job_id": "logistic-dev", "model": "logistic", "device": "cpu",
            "fold": "development", "result_path": str(result_path), "command": ["synthetic-no-execution"]}
    row = {**spec, "status": "completed", "exit_code": 0, "finished_at": "2026-09-19T15:00:00+00:00",
           "spec_json": json.dumps(spec), "command_json": json.dumps(spec["command"]),
           "result_sha256": verifier.sha(result_path)}
    return root, frame, spec, row


def verify(artifacts, predictor=None):
    from market_edge.modeling import predict_model
    return verifier.verify_job(*artifacts, predictor=predictor or predict_model)


def test_seed_missing_probabilities_are_intentional_but_not_inference_inputs():
    frame = features()
    verifier.validate_frame(frame)
    frame.loc[frame.phase.eq("development"), "h04_probability"] = np.nan
    with pytest.raises(ValueError, match="nonfinite"):
        verifier.validate_frame(frame)


def test_exact_real_predictor_replay_without_fit_or_labels(artifacts):
    result = verify(artifacts)
    assert result["status"] == "verified"
    assert result["prediction_max_absolute_difference"] < 1e-15
    assert result["rows"] == 2
    assert len(result["artifact_sha256"]) == 3
    assert not {"finish", "tansho_payout", "is_win"}.intersection(artifacts[1].columns)


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate"])
def test_prediction_cohort_must_match_every_runner(artifacts, mutation):
    root, _, _, _ = artifacts
    path = root / "jobs/logistic-dev/predictions.parquet"
    predictions = pd.read_parquet(path)
    if mutation == "missing":
        predictions = predictions.iloc[:1]
    elif mutation == "extra":
        extra = predictions.iloc[:1].copy()
        extra["horse_number"] = 3
        predictions = pd.concat([predictions, extra], ignore_index=True)
    else:
        predictions = pd.concat([predictions, predictions.iloc[:1]], ignore_index=True)
    predictions.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="prediction"):
        verify(artifacts)


def test_probabilities_with_same_simplex_but_wrong_model_fail(artifacts):
    path = artifacts[0] / "jobs/logistic-dev/predictions.parquet"
    predictions = pd.read_parquet(path)
    predictions["probability"] = [.4, .6]
    predictions.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="stored-model-prediction-mismatch"):
        verify(artifacts)


def test_prediction_row_order_does_not_matter(artifacts):
    path = artifacts[0] / "jobs/logistic-dev/predictions.parquet"
    pd.read_parquet(path).iloc[::-1].to_parquet(path, index=False)
    assert verify(artifacts)["status"] == "verified"


def test_result_hash_tampering_fails_before_inference(artifacts):
    path = artifacts[0] / "jobs/logistic-dev/result.json"
    path.write_text(path.read_text() + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="result-sha"):
        verify(artifacts, predictor=lambda *_: pytest.fail("must not infer"))


def test_incomplete_job_fails_before_inference(artifacts):
    artifacts[3]["status"] = "running"
    with pytest.raises(ValueError, match="completed"):
        verify(artifacts, predictor=lambda *_: pytest.fail("must not infer"))


def test_model_mutation_during_replay_rejected(artifacts):
    from market_edge.modeling import predict_model

    def changing_predictor(frame, folder):
        result = predict_model(frame, folder)
        path = folder / "model.json"
        path.write_text(path.read_text() + " ", encoding="utf-8")
        return result

    with pytest.raises(ValueError, match="changed-during"):
        verify(artifacts, predictor=changing_predictor)


@pytest.mark.parametrize("mutation", ["later_year", "partial_cohort", "duplicate", "phase_overlap", "labels"])
def test_feature_boundary_rejects_bad_scope(mutation):
    frame = features()
    if mutation == "later_year":
        frame.loc[frame.phase.eq("validation"), "race_date"] = "20230101"
    elif mutation == "partial_cohort":
        frame = frame.iloc[1:]
    elif mutation == "duplicate":
        frame = pd.concat([frame, frame.iloc[:1]], ignore_index=True)
    elif mutation == "phase_overlap":
        frame.loc[frame.phase.eq("meta_fit"), "race_date"] = "20211101"
    else:
        frame["finish"] = 1
    with pytest.raises(ValueError):
        verifier.validate_frame(frame)


def test_artifact_path_cannot_escape_root(tmp_path):
    with pytest.raises(ValueError, match="outside"):
        verifier._within(tmp_path.parent / "outside.json", tmp_path)
