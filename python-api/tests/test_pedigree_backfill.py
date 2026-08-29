import json
import sqlite3
from pathlib import Path

from scraping.pedigree_backfill import sync_pedigree_cache


def _make_databases(tmp_path: Path) -> tuple[Path, Path]:
    ultimate = tmp_path / "ultimate.db"
    pedigree = tmp_path / "pedigree.db"
    with sqlite3.connect(str(ultimate)) as conn:
        conn.executescript(
            """
            CREATE TABLE race_results_ultimate (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_id TEXT,
                data TEXT NOT NULL
            );
            CREATE TABLE horse_details (
                horse_id TEXT PRIMARY KEY,
                horse_name TEXT,
                sire TEXT,
                dam TEXT,
                damsire TEXT,
                updated_at TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO race_results_ultimate (race_id, data) VALUES (?, ?)",
            (
                "202001010101",
                json.dumps({"horse_id": "horse-1", "horse_name": "Horse 1"}),
            ),
        )
    with sqlite3.connect(str(pedigree)) as conn:
        conn.execute(
            "CREATE TABLE pedigree_cache (horse_id TEXT PRIMARY KEY, sire TEXT, dam TEXT, damsire TEXT)"
        )
        conn.execute(
            "INSERT INTO pedigree_cache VALUES ('horse-1', 'Sire', 'Dam', 'Damsire')"
        )
    return ultimate, pedigree


def test_pedigree_sync_dry_run_does_not_write(tmp_path: Path) -> None:
    ultimate, pedigree = _make_databases(tmp_path)
    report = sync_pedigree_cache(ultimate, pedigree, apply=False)

    assert report["writes_performed"] is False
    assert report["cache_rows_available"] == 1
    assert report["embedded_rows_missing_before"] == 1
    with sqlite3.connect(str(ultimate)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM horse_details").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='scrape_repair_queue'"
        ).fetchone()[0] == 0


def test_pedigree_sync_updates_normalized_and_embedded_data(tmp_path: Path) -> None:
    ultimate, pedigree = _make_databases(tmp_path)
    report = sync_pedigree_cache(ultimate, pedigree, apply=True)

    assert report["writes_performed"] is True
    assert report["embedded_rows_repaired"] == 1
    assert report["embedded_rows_missing_after"] == 0
    assert report["quick_check"] == "ok"
    with sqlite3.connect(str(ultimate)) as conn:
        payload = json.loads(
            conn.execute("SELECT data FROM race_results_ultimate").fetchone()[0]
        )
        normalized = conn.execute(
            "SELECT sire, dam, damsire FROM horse_details WHERE horse_id='horse-1'"
        ).fetchone()
    assert payload["sire"] == "Sire"
    assert payload["dam"] == "Dam"
    assert payload["damsire"] == "Damsire"
    assert normalized == ("Sire", "Dam", "Damsire")
