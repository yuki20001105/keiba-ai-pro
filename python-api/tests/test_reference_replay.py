import json
import sqlite3

import numpy as np
import pandas as pd
import pytest

from test_db_shared_optimized import fixture_data, pure_fit
from training import db_shared_features as shared, db_shared_optimized as optimized


def test_manifest_excludes_training_end_inclusively_and_uses_actual_dates(tmp_path):
    from training.reference_replay import read_manifest
    path = tmp_path / "source.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE races_ultimate (race_id TEXT PRIMARY KEY,data TEXT)")
        for rid, info in [
            ("training", {"date": "20220604"}),
            ("first-validation", {"date": "2022-06-05"}),
            ("test", {"race_date": "20240811"}),
            ("209901010101", {"date": "20261010"}),
            ("unknown", {}),
            ("conflicting", {"date": "20220604", "race_date": "20240811"}),
        ]:
            db.execute("INSERT INTO races_ultimate VALUES (?,?)", (rid, json.dumps(info)))
    result = read_manifest(path, "2022-06-04", "2026-11-05")
    assert [row["race_id"] for row in result["races"]] == ["first-validation", "test", "209901010101"]
    assert result["training_excluded"] == 1
    assert result["unverified_dates"] == ["conflicting", "unknown"]


def replay_inputs():
    target, raw = fixture_data()
    bundle, _ = optimized.fit_shared_model(target, raw)
    heldout = target.race_date.gt(bundle["training_date_to"])
    raw.attrs.update(current_outcomes_consumed=False, same_day_or_future_history_used=False,
                     source_hashes=shared.source_hashes())
    return bundle, target.loc[heldout].copy(), raw.loc[heldout].copy(), target, raw


def test_batch_scores_use_saved_profile_and_normalize_each_race_independently(pure_fit):
    from training.reference_replay import score_batch
    bundle, target, raw, _, _ = replay_inputs()
    scores = score_batch(bundle, target, raw)
    assert len(scores) == len(target)
    assert scores.groupby("race_id").p_norm.sum().to_numpy() == pytest.approx(1.0)
    assert scores.groupby("race_id", sort=False).horse_number.first().eq(1).all()
    first = scores.iloc[:3]
    assert first.p_norm.iloc[0] > first.p_norm.iloc[1] > first.p_norm.iloc[2]
    changed = target.copy()
    changed[["finish", "time_seconds"]] = 999999
    pd.testing.assert_frame_equal(score_batch(bundle, changed, raw), scores)


@pytest.mark.parametrize("change", ["train", "outcomes", "same_day", "source", "columns"])
def test_batch_replay_refuses_training_overlap_and_unverified_features(pure_fit, change):
    from training.reference_replay import score_batch
    bundle, target, raw, complete_target, complete_raw = replay_inputs()
    if change == "train":
        target, raw = complete_target, complete_raw
    elif change == "outcomes":
        raw.attrs["current_outcomes_consumed"] = True
    elif change == "same_day":
        raw.attrs["same_day_or_future_history_used"] = True
    elif change == "source":
        raw.attrs["source_hashes"] = {}
    else:
        raw["finish"] = np.ones(len(raw))
    with pytest.raises(ValueError):
        score_batch(bundle, target, raw)
