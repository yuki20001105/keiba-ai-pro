"""Verify actual saved-snapshot serving parity without publishing or DB writes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]

import joblib
import numpy as np
import pandas as pd

from training import db_context_features as context
from training import db_context_fit as base
from training import pace_fit


def run(output):
    result = json.loads((output / "pace-stack.json").read_text(encoding="utf-8"))
    path = output / "pace-stack.research.joblib"
    if base.file_hash(path) != result["artifact_sha256"]:
        raise ValueError("pace-verification-artifact-hash-mismatch")
    bundle = joblib.load(path)
    protocol = json.loads((output / "training-protocol.json").read_text(encoding="utf-8"))
    if protocol["sources"] != pace_fit.source_hashes():
        raise ValueError("pace-verification-training-source-mismatch")
    target, features, _, receipt = base.prepare_inputs(
        output / "snapshot.sqlite", protocol["date_from"], protocol["date_to"], output / "inputs")
    test = target.loc[target.race_date.ge("20260101")]
    dates = sorted(test.race_date.unique())
    selected = [dates[0], dates[len(dates) // 2], dates[-1]]
    samples = []
    for date in selected:
        race = str(test.loc[test.race_date.eq(date)].race_id.iloc[0])
        positions = np.flatnonzero(target.race_id.eq(race))
        card = target.iloc[positions].reset_index(drop=True).copy()
        expected = features.iloc[positions].reset_index(drop=True).copy()
        clean = card.drop(columns=[name for name in ("finish", "time_seconds", "last_3f_time",
                                                    "_ctx_history_corners", "corner_positions_list") if name in card])
        # Retrospective observations deliberately injected: the serving
        # builder must ignore them before either supervised head is invoked.
        injected = clean.copy()
        injected["finish"] = 99
        injected["time_seconds"] = 1.
        injected["corner_positions_list"] = [[1, 1]] * len(card)
        info = clean.iloc[0].to_dict()
        actual, audit = context.build_serving_features(output / "snapshot.sqlite", info, injected)
        pd.testing.assert_frame_equal(actual, expected, check_exact=True)
        raw, _ = pace_fit.predict_scores(bundle, clean, expected)
        served, forecast = pace_fit.predict_scores(bundle, clean, actual)
        np.testing.assert_allclose(raw, served, rtol=0, atol=0)
        if (audit.get("current_outcomes_consumed") is not False
                or audit.get("same_day_or_future_history_used") is not False
                or audit["history_max_source_date"] >= date):
            raise ValueError("pace-serving-history-contract-failed")
        samples.append({"race_id": race, "race_date": date, "runners": len(card),
                        "features_exact": True, "scores_exact": True,
                        "current_outcome_injection_ignored": True,
                        "history_max_source_date": audit["history_max_source_date"],
                        "pace_model_available": bool(forecast.pace_model_available.eq(1).all()),
                        "position_model_available": bool(forecast.position_model_available.eq(1).all())})
    proof = {"schema": "pace-stack-serving-parity-v1", "artifact_sha256": base.file_hash(path),
             "snapshot_sha256": receipt["snapshot_sha256"], "sources": pace_fit.source_hashes(),
             "samples": samples, "actual_snapshot_parity_verified": True,
             "public_endpoint_verified": False, "live_card_capture_verified": False,
             "active_model_changed": False, "network_requests": 0}
    with (output / "serving-parity.json").open("x", encoding="utf-8") as stream:
        json.dump(proof, stream, indent=2, ensure_ascii=False, allow_nan=False)
    print(json.dumps(proof, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output.resolve())
