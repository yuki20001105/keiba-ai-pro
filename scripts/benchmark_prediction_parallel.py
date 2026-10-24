"""Read-only paired CPU benchmark; never calls an API or records predictions.

Compare HEAD's original feature builder against the optimized builder, then
measure the same races with the bounded local worker pool. All inputs/outputs
are checked; HTTP refresh and personal/observation writes are deliberately
outside this benchmark and must not be claimed as measured end-to-end speed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import subprocess
import sys
import time
import types
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python-api"))
sys.path.insert(0, str(ROOT / "keiba"))


def input_frame(db: Path, race_id: str):
    import pandas as pd
    from keiba_ai.constants import FUTURE_FIELDS
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        row = conn.execute("SELECT data FROM races_ultimate WHERE race_id=?", (race_id,)).fetchone()
        if row is None:
            raise ValueError(f"missing race: {race_id}")
        race = json.loads(row[0])
        horses = conn.execute("SELECT data FROM race_results_ultimate WHERE race_id=?", (race_id,)).fetchall()
    records = []
    for row in horses:
        horse = json.loads(row[0])
        for key, value in race.items():
            if horse.get(key) is None:
                horse[key] = value
        horse["race_id"] = race_id
        records.append({key: value for key, value in horse.items() if key not in FUTURE_FIELDS})
    frame = pd.DataFrame(records)
    for old, new in {
        "track_type": "surface", "weight_kg": "horse_weight", "weight_change": "horse_weight_change",
    }.items():
        if old in frame:
            frame[new] = frame[new].fillna(frame[old]) if new in frame else frame[old]
    for url, id_, name in [
        ("jockey_url", "jockey_id", "jockey_name"),
        ("trainer_url", "trainer_id", "trainer_name"),
        ("horse_url", "horse_id", "horse_name"),
    ]:
        if id_ not in frame:
            if url in frame:
                frame[id_] = frame[url].str.extract(r"/([^/]+)/?$")[0]
            elif name in frame:
                frame[id_] = frame[name]
    for column in [
        "bracket_number", "horse_number", "jockey_weight", "odds", "popularity", "horse_weight",
        "age", "distance", "num_horses", "kai", "day", "corner_1", "corner_2", "corner_3", "corner_4",
        "horse_total_runs", "horse_total_wins", "horse_total_prize_money",
        "prev_race_distance", "prev_race_finish", "prev_race_weight",
    ]:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["race_date"] = frame["race_date"].fillna(race.get("date")) if "race_date" in frame else race.get("date")
    if "sex_age" in frame:
        if "sex" not in frame or frame["sex"].isna().all():
            frame["sex"] = frame["sex_age"].str.extract(r"^([牡牝セ])")[0]
        if "age" not in frame or frame["age"].isna().all():
            frame["age"] = pd.to_numeric(frame["sex_age"].str.extract(r"(\d+)$")[0], errors="coerce")
    return frame


@contextmanager
def original_features():
    import keiba_ai.feature_engineering as optimized
    old_pit = types.ModuleType("keiba_ai.old_point_in_time_history")
    old_fe = types.ModuleType("keiba_ai.feature_engineering")
    old_fe.__package__ = "keiba_ai"
    old_fe.__file__ = optimized.__file__
    for module, file in [(old_pit, "point_in_time_history.py"), (old_fe, "feature_engineering.py")]:
        source = subprocess.check_output(["git", "show", f"HEAD:keiba/keiba_ai/{file}"], cwd=ROOT).decode("utf-8")
        exec(compile(source, f"HEAD:{file}", "exec"), module.__dict__)
    old_fe.add_point_in_time_horse_history_features = old_pit.add_point_in_time_horse_history_features
    sys.modules["keiba_ai.feature_engineering"] = old_fe
    try:
        yield
    finally:
        sys.modules["keiba_ai.feature_engineering"] = optimized


def assert_same(original: dict, candidate: dict) -> None:
    import numpy as np
    import pandas as pd
    for key in ("error",):
        if key in original or key in candidate:
            raise AssertionError({"original": original.get(key), "candidate": candidate.get(key)})
    pd.testing.assert_frame_equal(original["features"], candidate["features"], check_exact=True)
    for key in ("raw", "probabilities"):
        np.testing.assert_array_equal(original[key], candidate[key])
    assert original["calibrator_applied"] == candidate["calibrator_applied"]
    assert original["explanations"] == candidate["explanations"]
    assert original["place3"] is None and candidate["place3"] is None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--race-ids", nargs="+", required=True)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--db", type=Path, default=ROOT / "keiba/data/keiba_ultimate.db")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.output.exists():
        parser.error("output exists; use a fresh report path")
    from routers.predict import _resolve_model_path
    from services.prediction_compute import compute_prediction, run_prediction_compute, shutdown_prediction_compute, worker_count
    model = (args.model or _resolve_model_path(None)).resolve()
    stat = model.stat()
    payloads = [{
        "frame": input_frame(args.db, race_id), "db_path": str(args.db.resolve()),
        "model_path": str(model), "expected_model_identity": (stat.st_mtime_ns, stat.st_size),
        "excluded_race_ids": [race_id], "native_threads": 2, "include_explanation": True,
    } for race_id in args.race_ids]
    workers = worker_count()
    print(f"benchmark starts: {len(payloads)} races, workers={workers}; no network/writes", flush=True)
    start = time.perf_counter()
    with original_features():
        originals = []
        for race_id, payload in zip(args.race_ids, payloads):
            result = compute_prediction(payload)
            if "error" in result:
                raise RuntimeError(result["error"])
            originals.append(result)
            print(f"old {race_id}: {result['timings']}", flush=True)
    old_seconds = time.perf_counter() - start
    start = time.perf_counter()
    optimized = []
    for race_id, payload, original in zip(args.race_ids, payloads, originals):
        result = compute_prediction(payload)
        assert_same(original, result)
        optimized.append(result)
        print(f"new {race_id}: {result['timings']}; EXACT_MATCH", flush=True)
    serial_seconds = time.perf_counter() - start
    async def parallel():
        return await asyncio.gather(*(run_prediction_compute(**payload) for payload in payloads))
    start = time.perf_counter()
    try:
        concurrent = asyncio.run(parallel())
    finally:
        shutdown_prediction_compute()
    parallel_seconds = time.perf_counter() - start
    for original, candidate in zip(originals, concurrent):
        assert_same(original, candidate)
    report = {
        "scope": "read-only CPU path: history SQL + feature generation + inference + TreeSHAP; NOT full API or HTTP acquisition",
        "race_ids": args.race_ids, "model": model.name, "model_identity": [stat.st_mtime_ns, stat.st_size],
        "workers": concurrent[0]["timings"]["workers"], "native_threads": 2, "exact_match": True,
        "original_serial_seconds": old_seconds, "optimized_serial_seconds": serial_seconds,
        "optimized_parallel_seconds_including_spawn_shutdown": parallel_seconds,
        "serial_speedup": old_seconds / serial_seconds,
        "combined_speedup": old_seconds / parallel_seconds,
        "additional_parallel_speedup": serial_seconds / parallel_seconds,
        "timings": {"original": [r["timings"] for r in originals], "optimized": [r["timings"] for r in optimized],
                    "parallel": [r["timings"] for r in concurrent]},
    }
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
