"""Fixed inference fixtures: no model fitting, network or live feature claims."""
import hashlib
import json
from unittest.mock import patch

import joblib
import numpy as np
import pandas as pd
import pytest

from forward_adapters.inference import SAFE_FEATURES, score_fixed_model
from prospective.calibration import SCHEMA, _sha


class Encoder:
    calls = []

    def __init__(self, columns):
        self.feature_columns = self.requested_columns = columns

    def fit(self, *args, **kwargs):
        raise AssertionError("must never fit")

    def fit_transform(self, *args, **kwargs):
        raise AssertionError("must never fit_transform")

    def transform(self, frame):
        type(self).calls.append(list(frame.columns))
        assert list(frame.columns) == self.feature_columns
        return frame.copy()


class Model:
    calls = []

    def __init__(self, columns):
        self.columns = columns

    def feature_name(self):
        return self.columns

    def num_feature(self):
        return len(self.columns)

    def fit(self, *args, **kwargs):
        raise AssertionError("must never fit model")

    def predict(self, frame, *, num_threads):
        assert num_threads == 2
        type(self).calls.append(list(frame.columns))
        return np.arange(len(frame), dtype=float) - 2


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path, *, mutate_contract=None, mutate_calibrator=None):
    columns = sorted(SAFE_FEATURES)[:99]
    assert len(columns) == 99
    contract = {"schema": "isolated-feature-search-trial-v1", "target": "rank_regression", "loss": "huber",
                "evaluation_year": 2026, "score_is_probability": False,
                "validation_used_for_fit_or_early_stopping": False, "market_features": False,
                "train_period": ["20160101", "20251231"], "feature_columns": columns, "requested_features": columns}
    if mutate_contract:
        mutate_contract(contract)
    artifact = {"contract": contract, "encoder": Encoder(columns), "model": Model(columns), "feature_columns": columns}
    model = tmp_path / "fixed.joblib"
    joblib.dump(artifact, model)
    model_hash = file_sha(model)
    calibrator = {"schema": SCHEMA, "family": "global_temperature", "global": {"temperature": .15},
                  "by_surface": {}, "fit_date_to": "20221228"}
    frozen = {"calibrator": calibrator, "calibrator_sha256": _sha(calibrator),
              "model_provenance": {"model_id": "H04", "model_artifact_sha256_by_year": {"2026": model_hash}},
              "frozen_before_later_diagnostics": True}
    if mutate_calibrator:
        mutate_calibrator(frozen)
    calibration = tmp_path / "calibration.json"
    calibration.write_text(json.dumps(frozen), encoding="utf-8")
    frame = pd.DataFrame([{"race_id": "202605010101", "race_date": "20260919", "horse_id": str(number),
                           "horse_number": number, "surface": "turf", "expected_runner_count": 3,
                           **{name: float(number) for name in columns}} for number in (1, 2, 3)])
    Encoder.calls, Model.calls = [], []
    return frame, model, calibration, model_hash, file_sha(calibration), columns


def score(parts):
    return score_fixed_model(*parts[:5])


def test_transform_only_preserves_cohort_and_returns_probabilities_not_scores(tmp_path):
    parts = fixture(tmp_path)
    result = score(parts)
    assert Encoder.calls == [parts[-1]] and Model.calls == [parts[-1]]
    assert result.horse_number.tolist() == [1, 2, 3]
    assert result.prediction.tolist() == [-2, -1, 0]
    assert result.rank_score.equals(result.prediction)
    assert result.win_probability.sum() == pytest.approx(1)
    assert result.win_probability.between(0, 1).all()
    assert not result.policy_ready.any() and not result.raw_asof_features_verified.any()


@pytest.mark.parametrize("which", ["model", "calibrator"])
def test_hash_mismatch_precedes_deserialization(tmp_path, which):
    parts = list(fixture(tmp_path))
    parts[3 if which == "model" else 4] = "0" * 64
    with patch("forward_adapters.inference.joblib.load", side_effect=AssertionError("must not deserialize")):
        with pytest.raises(ValueError, match="SHA-256 mismatch"):
            score(parts)


def test_exact_verified_bytes_are_deserialized_even_if_path_changes(tmp_path):
    parts = fixture(tmp_path)
    original = joblib.load
    def changed_path(source):
        parts[1].write_bytes(b"replaced after verification")
        assert hasattr(source, "read")
        return original(source)
    with patch("forward_adapters.inference.joblib.load", side_effect=changed_path):
        assert len(score(parts)) == 3


@pytest.mark.parametrize("date,race", [("20160105", "201605010101"), ("20251231", "202505010101")])
def test_historical_dates_at_or_before_training_end_rejected(tmp_path, date, race):
    parts = fixture(tmp_path)
    parts[0]["race_date"], parts[0]["race_id"] = date, race
    with pytest.raises(ValueError, match="after model"):
        score(parts)
    assert not Encoder.calls and not Model.calls


@pytest.mark.parametrize("column", ["race_id", "horse_id", "expected_runner_count", "surface"])
def test_required_metadata(tmp_path, column):
    parts = list(fixture(tmp_path))
    parts[0] = parts[0].drop(columns=column)
    with pytest.raises(ValueError, match="metadata"):
        score(parts)


def test_missing_feature_is_not_filled_or_refitted(tmp_path):
    parts = list(fixture(tmp_path))
    parts[0] = parts[0].drop(columns=parts[-1][0])
    with pytest.raises(ValueError, match="feature columns are missing"):
        score(parts)
    assert not Encoder.calls


def test_market_and_result_columns_never_reach_model_or_output(tmp_path):
    parts = fixture(tmp_path)
    baseline = score(parts)
    parts[0]["odds"], parts[0]["finish"], parts[0]["tansho_payout"] = 999, 1, 999999
    changed = score(parts)
    np.testing.assert_array_equal(baseline.win_probability, changed.win_probability)
    assert not {"odds", "finish", "tansho_payout"}.intersection(changed.columns)
    assert all(columns == parts[-1] for columns in Encoder.calls + Model.calls)


def test_calibrator_provenance_mismatch_before_model_load(tmp_path):
    parts = fixture(tmp_path, mutate_calibrator=lambda c: c["model_provenance"]["model_artifact_sha256_by_year"].update({"2026": "f" * 64}))
    with patch("forward_adapters.inference.joblib.load", side_effect=AssertionError("must not load")):
        with pytest.raises(ValueError, match="not bound"):
            score(parts)


@pytest.mark.parametrize("key,value", [("schema", "other"), ("target", "regression"), ("loss", "mse"),
                                      ("evaluation_year", 2025), ("score_is_probability", True),
                                      ("market_features", True), ("validation_used_for_fit_or_early_stopping", True)])
def test_model_contract_gate(tmp_path, key, value):
    parts = fixture(tmp_path, mutate_contract=lambda c: c.update({key: value}))
    with pytest.raises(ValueError, match="safe H04"):
        score(parts)
    assert not Encoder.calls


def test_current_cohort_must_be_complete(tmp_path):
    parts = list(fixture(tmp_path))
    parts[0] = parts[0].iloc[:2]
    with pytest.raises(ValueError, match="runner count"):
        score(parts)


def test_unsafe_feature_contract_rejected(tmp_path):
    def unsafe(contract):
        contract["feature_columns"][0] = "safe_current_finish"
    parts = fixture(tmp_path, mutate_contract=unsafe)
    with pytest.raises(ValueError, match="99-feature"):
        score(parts)


def test_duplicate_input_columns_rejected(tmp_path):
    parts = list(fixture(tmp_path))
    parts[0] = pd.concat([parts[0], parts[0][[parts[-1][0]]]], axis=1)
    with pytest.raises(ValueError, match="unique columns"):
        score(parts)
