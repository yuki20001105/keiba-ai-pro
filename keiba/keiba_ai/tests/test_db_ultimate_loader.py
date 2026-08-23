from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from keiba_ai.db_ultimate_loader import load_ultimate_training_frame


def test_mixed_scraper_generations_coalesce_finish_position(tmp_path: Path) -> None:
    database = tmp_path / "ultimate.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE races_ultimate (race_id TEXT PRIMARY KEY, data TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE race_results_ultimate "
        "(id INTEGER PRIMARY KEY, race_id TEXT NOT NULL, data TEXT NOT NULL)"
    )

    races = [
        ("202601010101", "20260101"),
        ("202602010101", "20260201"),
    ]
    for race_id, race_date in races:
        connection.execute(
            "INSERT INTO races_ultimate (race_id, data) VALUES (?, ?)",
            (
                race_id,
                json.dumps(
                    {
                        "race_id": race_id,
                        "date": race_date,
                        "distance": 1600,
                        "track_type": "turf",
                        "num_horses": 1,
                    }
                ),
            ),
        )

    connection.execute(
        "INSERT INTO race_results_ultimate (race_id, data) VALUES (?, ?)",
        (
            races[0][0],
            json.dumps(
                {
                    "race_id": races[0][0],
                    "horse_id": "old-horse",
                    "horse_name": "old",
                    "horse_number": 1,
                    "finish": 2,
                }
            ),
        ),
    )
    connection.execute(
        "INSERT INTO race_results_ultimate (race_id, data) VALUES (?, ?)",
        (
            races[1][0],
            json.dumps(
                {
                    "race_id": races[1][0],
                    "horse_id": "new-horse",
                    "horse_name": "new",
                    "horse_number": 1,
                    "finish_position": 1,
                }
            ),
        ),
    )
    connection.commit()
    connection.close()

    frame = load_ultimate_training_frame(database).set_index("race_id")

    assert frame.loc[races[0][0], "finish"] == 2
    assert frame.loc[races[1][0], "finish"] == 1
