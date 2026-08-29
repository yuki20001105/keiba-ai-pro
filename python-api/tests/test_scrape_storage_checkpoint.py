import json
import sqlite3
from pathlib import Path

from scraping.storage import (
    _get_scraped_dates_sqlite,
    _init_sqlite_db,
    _save_scraped_date_sqlite,
    _save_verified_no_race_dates_sqlite,
    reconcile_scraped_dates_from_races,
)


def test_empty_fetch_is_not_treated_as_verified_no_race(tmp_path: Path) -> None:
    db_path = tmp_path / "ultimate.db"
    _save_scraped_date_sqlite(db_path, "20200101", 0)

    with sqlite3.connect(str(db_path)) as conn:
        race_count, no_race = conn.execute(
            "SELECT race_count, no_race FROM scraped_dates WHERE date = ?",
            ("20200101",),
        ).fetchone()

    assert race_count == 0
    assert no_race == 0
    assert "20200101" not in _get_scraped_dates_sqlite(db_path)


def test_only_real_database_coverage_is_skipped(tmp_path: Path) -> None:
    db_path = tmp_path / "ultimate.db"
    _save_scraped_date_sqlite(db_path, "20200104", 12)
    _save_scraped_date_sqlite(db_path, "20200105", 0, verified_no_race=True)

    covered = _get_scraped_dates_sqlite(db_path, min_races=6)
    assert "20200104" in covered
    assert "20200105" not in covered


def test_verified_no_race_dates_are_batch_persisted(tmp_path: Path) -> None:
    db_path = tmp_path / "ultimate.db"
    assert _save_verified_no_race_dates_sqlite(
        db_path, ["20200101", "20200102", "20200101"]
    ) == 2
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT date, race_count, no_race FROM scraped_dates ORDER BY date"
        ).fetchall()
    assert rows == [("20200101", 0, 1), ("20200102", 0, 1)]


def test_reconcile_coverage_ledger_is_audited_and_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "ultimate.db"
    _init_sqlite_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        for date, race_count in (("20200104", 6), ("20200105", 2)):
            for index in range(race_count):
                race_id = f"{date}{index:04d}"
                conn.execute(
                    "INSERT INTO races_ultimate (race_id, data) VALUES (?, ?)",
                    (race_id, json.dumps({"date": date})),
                )
        conn.execute(
            "INSERT INTO scraped_dates (date, race_count, no_race) VALUES (?, 0, 1)",
            ("20200104",),
        )

    preview = reconcile_scraped_dates_from_races(db_path, min_races=6, apply=False)
    assert preview["race_rows_scanned"] == 8
    assert preview["observed_dates"] == 2
    assert preview["complete_observed_dates"] == 1
    assert preview["changes_required"] == 2
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute(
            "SELECT race_count, no_race FROM scraped_dates WHERE date = '20200104'"
        ).fetchone() == (0, 1)

    applied = reconcile_scraped_dates_from_races(db_path, min_races=6, apply=True)
    assert applied["quick_check"] == "ok"
    assert applied["audit_rows_written"] == 2
    assert applied["protected_counts_before"] == applied["protected_counts_after"]
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute(
            "SELECT race_count, no_race FROM scraped_dates WHERE date = '20200104'"
        ).fetchone() == (6, 0)
        assert conn.execute(
            "SELECT COUNT(*) FROM scraped_dates_reconcile_audit WHERE run_id = ?",
            (applied["run_id"],),
        ).fetchone()[0] == 2

    second = reconcile_scraped_dates_from_races(db_path, min_races=6, apply=True)
    assert second["changes_required"] == 0
    assert second["audit_rows_written"] == 0
