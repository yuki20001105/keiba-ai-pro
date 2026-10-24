"""Small generated fixtures only: no network, publication or active pointer."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from training import db_context_features as features
from training import db_context_fit as fit


def sample():
    records = []
    for index, day in enumerate(pd.date_range("2020-01-01", periods=30, freq="3D"), start=1):
        for number in range(1, 5):
            records.append({"race_id": day.strftime("%Y")+f"0501{index:02d}01", "race_date": day.strftime("%Y%m%d"),
                "horse_id": "horse"+str(number), "horse_number": number, "distance": 1600, "surface": "turf",
                "venue": "東京", "num_horses": 4, "finish": number, "time_seconds": 94+number+(day.day%4),
                "last_3f_time": 35+number, "odds": 2+number, "popularity": number, "bracket_number": number,
                "horse_weight": 470+number, "horse_weight_change": 1, "age": 4, "sex": "牡", "burden_weight": 57,
                "field_condition": "良", "weather": "晴", "race_class": "未勝利", "sire": "父", "damsire": "母父",
                features._CORNER_COLUMN: (number, number)})
    target = pd.DataFrame(records)
    raw = features.build_training_features(target, target)
    return fit.prepare(target, raw)


class FixedModel:
    def predict(self, frame, **_kwargs):
        return -frame.safe_horse_number.to_numpy(dtype=float)*.01


@pytest.fixture
def fake_fit(monkeypatch):
    monkeypatch.setattr(fit, "_train_booster", lambda *_args, **_kwargs: FixedModel())


def test_whole_dates_and_raw_column_alignment_stay_fixed(fake_fit):
    target, raw = sample()
    before = fit.legacy._frame_digest(raw)
    bundle, result = fit.fit_shared_model(target, raw, n_threads=2)
    assert len(bundle["feature_columns"]) == 80
    assert result["test_used_for_selection_or_fit"] is False
    assert result["cal_used_for_preset_selection"] is False
    assert result["roi_evaluated"] is False
    parts = result["partitions"]
    assert parts["TRAIN"]["date_to"] < parts["CAL"]["date_from"] < parts["TEST"]["date_from"]
    assert fit.legacy._frame_digest(raw) == before
    fit._validate_prediction_bundle(bundle)
    scores, probabilities = fit.predict_encoded_shared_scores(bundle, bundle["encoder"].transform(raw), target.race_id)
    assert np.isfinite(scores).all()
    if probabilities is not None:
        assert np.allclose(pd.Series(probabilities).groupby(target.race_id).sum(), 1)


def test_subset_research_is_not_a_published_80_column_model(fake_fit):
    target, raw = sample()
    bundle, result, output = fit.fit_on_partitions(target, raw, fit.legacy.date_partitions(target),
        columns=features.FEATURE_GROUPS["baseline46"], n_threads=2, preset_parallel=False)
    assert result["feature_count"] == 46
    assert result["training_parallel_workers"] == 1
    assert output["target"].race_date.min() > result["partitions"]["CAL"]["date_to"]
    with pytest.raises(ValueError, match="input-contract"):
        fit._validate_prediction_bundle(bundle)


def test_overlapping_partitions_rejected_before_training(fake_fit):
    target, raw = sample()
    partition = fit.legacy.date_partitions(target)
    partition["CAL"] = partition["TRAIN"]
    with pytest.raises(ValueError, match="overlap"):
        fit.fit_on_partitions(target, raw, partition)


@pytest.mark.parametrize("kind", ["partial_day", "duplicates", "out_of_range", "float_index"])
def test_partition_positions_must_be_unique_whole_dates(fake_fit, kind):
    target, raw = sample()
    partition = fit.legacy.date_partitions(target)
    if kind == "partial_day":
        partition["TEST"] = partition["TEST"][1:]
    elif kind == "duplicates":
        partition["TEST"] = np.append(partition["TEST"], partition["TEST"][0])
    elif kind == "out_of_range":
        partition["TEST"] = np.append(partition["TEST"], len(target))
    else:
        partition["TEST"] = partition["TEST"].astype(float)
    with pytest.raises(ValueError, match="partition"):
        fit.fit_on_partitions(target, raw, partition)


def test_columns_and_encoder_cannot_be_reordered_after_fit(fake_fit):
    target, raw = sample()
    with pytest.raises(ValueError, match="aligned"):
        fit.prepare(target, raw[list(reversed(raw.columns))])
    bundle, _ = fit.fit_shared_model(target, raw, n_threads=2)
    bundle["encoder"].feature_columns.reverse()
    with pytest.raises(ValueError, match="input-contract"):
        fit._validate_prediction_bundle(bundle)


def test_gpu_request_without_verified_worker_is_rejected():
    target, raw = sample()
    with pytest.raises(ValueError, match="bound-worker"):
        fit.accelerated_fit_shared_model(target, raw, compute_device="gpu")
