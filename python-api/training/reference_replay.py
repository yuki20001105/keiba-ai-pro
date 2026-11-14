"""Published-model replay primitives; never fit, acquire data or authorize bets."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd


def read_manifest(db_path, training_end, until):
    from betting.performance import _race_date

    training_end, until = _race_date(training_end), _race_date(until)
    races, unverified, excluded = [], [], 0
    with sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True) as db:
        for rid, encoded in db.execute("SELECT race_id,data FROM races_ultimate ORDER BY race_id"):
            try:
                info = json.loads(encoded)
                dates = {_race_date(info[key]) for key in ("date", "race_date") if info.get(key) not in (None, "")}
                if len(dates) != 1:
                    raise ValueError("unverified_race_date")
                day = dates.pop()
            except (ValueError, TypeError, KeyError):
                unverified.append(str(rid))
                continue
            if day <= training_end:
                excluded += 1
            elif day <= until:
                races.append({"race_id": str(rid), "date": day, "race_info": {**info, "race_id": str(rid)}})
    races.sort(key=lambda row: (row["date"], row["race_id"]))
    return {"races": races, "training_excluded": excluded, "unverified_dates": unverified}


def score_batch(bundle, target, raw_features, *, n_threads=1):
    from betting.performance import _race_date
    from training import db_normalized_registry, db_shared_features
    from training.db_shared_fit import race_softmax

    if (target.empty or not target.index.equals(raw_features.index)
            or raw_features.attrs.get("current_outcomes_consumed") is not False
            or raw_features.attrs.get("same_day_or_future_history_used") is not False
            or raw_features.attrs.get("source_hashes") != db_shared_features.source_hashes()
            or target.race_date.map(_race_date).le(_race_date(bundle["training_date_to"])).any()):
        raise ValueError("historical-replay-training-overlap-or-unverified-input")
    profile = db_normalized_registry.prediction_module(bundle)
    features = profile.select_features(raw_features)
    raw, probabilities = profile.predict_shared_scores(bundle, features, target.race_id, n_threads=n_threads)
    if probabilities is None:
        probabilities = race_softmax(raw, target.race_id, 1.0)
    result = target[["race_id", "race_date", "horse_number", "horse_id"]].copy()
    result["raw_score"], result["p_norm"] = raw, probabilities
    if (not np.isfinite(result[["raw_score", "p_norm"]]).all().all()
            or not result.p_norm.between(0, 1).all()
            or not np.allclose(result.groupby("race_id").p_norm.sum(), 1, atol=1e-8)):
        raise ValueError("historical-replay-probability-contract")
    return result
