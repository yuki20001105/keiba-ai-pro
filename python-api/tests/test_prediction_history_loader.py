from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

from keiba_ai.db_ultimate_loader import load_prediction_history_frame


def _insert_race(
    conn: sqlite3.Connection,
    race_id: str,
    *,
    horse_id: str,
    jockey_id: str,
    trainer_id: str,
    sire: str,
    damsire: str,
    venue: str,
    surface: str,
    distance: int,
) -> None:
    race = {
        "date": "20240101",
        "venue": venue,
        "track_type": surface,
        "distance": distance,
    }
    result = {
        "horse_id": horse_id,
        "jockey_id": jockey_id,
        "trainer_id": trainer_id,
        "sire": sire,
        "damsire": damsire,
        "bracket_number": 1,
        "finish_position": 1,
        "last_3f": "34.5",
    }
    conn.execute(
        "INSERT INTO races_ultimate (race_id, data) VALUES (?, ?)",
        (race_id, json.dumps(race)),
    )
    conn.execute(
        "INSERT INTO race_results_ultimate (race_id, data) VALUES (?, ?)",
        (race_id, json.dumps(result)),
    )


def test_prediction_history_loader_selects_only_relevant_entities_and_gate_group(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "ultimate.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("CREATE TABLE races_ultimate (race_id TEXT PRIMARY KEY, data TEXT)")
        conn.execute("CREATE TABLE race_results_ultimate (race_id TEXT, data TEXT)")
        conn.execute(
            "CREATE TABLE return_tables_ultimate "
            "(race_id TEXT, bet_type TEXT, payout INTEGER)"
        )
        _insert_race(
            conn,
            "202401010101",
            horse_id="target-horse",
            jockey_id="other-jockey",
            trainer_id="other-trainer",
            sire="other-sire",
            damsire="other-damsire",
            venue="Kyoto",
            surface="turf",
            distance=2000,
        )
        _insert_race(
            conn,
            "202401010102",
            horse_id="other-horse",
            jockey_id="target-jockey",
            trainer_id="other-trainer",
            sire="other-sire",
            damsire="other-damsire",
            venue="Kyoto",
            surface="dirt",
            distance=1200,
        )
        _insert_race(
            conn,
            "202401010103",
            horse_id="gate-horse",
            jockey_id="gate-jockey",
            trainer_id="gate-trainer",
            sire="gate-sire",
            damsire="gate-damsire",
            venue="Tokyo",
            surface="turf",
            distance=1600,
        )
        _insert_race(
            conn,
            "202401010104",
            horse_id="irrelevant-horse",
            jockey_id="irrelevant-jockey",
            trainer_id="irrelevant-trainer",
            sire="irrelevant-sire",
            damsire="irrelevant-damsire",
            venue="Tokyo",
            surface="dirt",
            distance=1600,
        )
        _insert_race(
            conn,
            "202501010101",
            horse_id="target-horse",
            jockey_id="target-jockey",
            trainer_id="target-trainer",
            sire="target-sire",
            damsire="target-damsire",
            venue="Tokyo",
            surface="turf",
            distance=1600,
        )
        conn.execute(
            "INSERT INTO return_tables_ultimate VALUES (?, ?, ?)",
            ("202401010101", "単勝", 450),
        )

    current = pd.DataFrame(
        [
            {
                "race_id": "202501010101",
                "horse_id": "target-horse",
                "jockey_id": "target-jockey",
                "trainer_id": "target-trainer",
                "sire": "target-sire",
                "damsire": "target-damsire",
                "venue": "Tokyo",
                "surface": "turf",
                "distance": 1600,
            }
        ]
    )

    history = load_prediction_history_frame(
        db_path,
        current,
        excluded_race_ids={"202501010101"},
    )

    assert set(history["race_id"]) == {
        "202401010101",
        "202401010102",
        "202401010103",
    }
    horse_row = history.loc[history["race_id"] == "202401010101"].iloc[0]
    assert horse_row["finish"] == 1
    assert horse_row["last_3f_time"] == 34.5
    assert horse_row["tansho_payout"] == 450
    assert horse_row["race_date"] == "20240101"
