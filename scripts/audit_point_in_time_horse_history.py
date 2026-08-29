#!/usr/bin/env python
"""Read-only coverage audit for leakage-safe horse history windows."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "keiba"))

from keiba_ai.point_in_time_history import (  # noqa: E402
    DEFAULT_HISTORY_WINDOWS,
    add_point_in_time_horse_history_features,
)


def _decode_rows(rows: list[tuple[str, str, str]]) -> pd.DataFrame:
    decoded: list[dict] = []
    for race_id, horse_json, race_json in rows:
        horse = json.loads(horse_json or "{}")
        race = json.loads(race_json or "{}")
        horse["race_id"] = race_id
        horse["race_date"] = race.get("date")
        horse["finish"] = horse.get("finish", horse.get("finish_position"))
        horse["last_3f_time"] = horse.get("last_3f_time", horse.get("last_3f"))
        decoded.append(horse)
    return pd.DataFrame(decoded)


def audit(db_path: Path, target_date: str | None = None) -> dict:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    try:
        if target_date is None:
            target_date = connection.execute(
                "SELECT MAX(json_extract(data, '$.date')) FROM races_ultimate "
                "WHERE length(json_extract(data, '$.date'))=8"
            ).fetchone()[0]
        target_rows = connection.execute(
            """SELECT rr.race_id, rr.data, ru.data
               FROM race_results_ultimate rr
               JOIN races_ultimate ru ON ru.race_id=rr.race_id
               WHERE json_extract(ru.data, '$.date')=?
               ORDER BY rr.race_id, rr.id""",
            (target_date,),
        ).fetchall()
        target = _decode_rows(target_rows)
        if target.empty:
            return {"target_date": target_date, "target_rows": 0, "status": "no_target_rows"}

        horse_ids = sorted(
            {str(value) for value in target.get("horse_id", pd.Series(dtype=str)).dropna() if str(value)}
        )
        placeholders = ",".join("?" for _ in horse_ids)
        history_rows = connection.execute(
            f"""SELECT rr.race_id, rr.data, ru.data
                FROM race_results_ultimate rr
                JOIN races_ultimate ru ON ru.race_id=rr.race_id
                WHERE json_extract(rr.data, '$.horse_id') IN ({placeholders})""",
            horse_ids,
        ).fetchall()
        history = _decode_rows(history_rows)
        features = add_point_in_time_horse_history_features(target, history)
        windows = tuple(DEFAULT_HISTORY_WINDOWS)
        return {
            "status": "ok",
            "target_date": target_date,
            "target_rows": int(len(target)),
            "target_races": int(target["race_id"].nunique()),
            "history_rows_loaded": int(len(history)),
            "audit": features.attrs.get("point_in_time_history_audit", {}),
            "max_prior_starts": int(features["horse_history_total_prior_starts"].max()),
            "windows": {
                str(window): {
                    "full_count": int((features[f"past{window}_is_insufficient"] == 0).sum()),
                    "any_count": int((features[f"past{window}_count"] > 0).sum()),
                    "full_rate": float(
                        (features[f"past{window}_is_insufficient"] == 0).mean()
                    ),
                }
                for window in windows
            },
        }
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        type=Path,
        default=ROOT / "keiba" / "data" / "keiba_ultimate.db",
    )
    parser.add_argument("--date", help="Target date in YYYYMMDD format; defaults to latest")
    args = parser.parse_args()
    if not args.db.exists():
        parser.error(f"database not found: {args.db}")
    print(json.dumps(audit(args.db.resolve(), args.date), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
