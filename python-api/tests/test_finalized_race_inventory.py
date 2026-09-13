from __future__ import annotations

import json
import hashlib
import sqlite3
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from scraping import race_inventory as inventory
from scraping.quality import classify_race_quality, record_race_quality, run_date_repair_audit


RACE_DATE = "20200104"
IDS = ["202001010102", "202001010101"]
TODAY = date(2030, 1, 1)
NOW = 1_900_000_000.0


def _race(race_id: str) -> dict:
    return {
        "race_info": {"race_id": race_id, "date": RACE_DATE, "venue": "Tokyo", "distance": 1600},
        "horses": [{
            "race_id": race_id, "horse_id": f"202010000{number}",
            "horse_name": f"Horse {number}", "horse_number": number,
            "finish_position": number, "finish_time": f"1:3{number}.0",
            "odds": 2.0 + number, "popularity": number, "weight_kg": 470 + number,
            "sire": "Sire", "dam": "Dam", "damsire": "Damsire",
        } for number in (1, 2)],
        "return_tables": [{"bet_type": "win", "combinations": "1", "payout": 300, "popularity": 1}],
    }


@pytest.fixture
def complete_day(tmp_path: Path, monkeypatch):
    db = tmp_path / "ultimate.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE races_ultimate (race_id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE race_results_ultimate (id INTEGER PRIMARY KEY, race_id TEXT, data TEXT NOT NULL);
            CREATE TABLE return_tables_ultimate (
                id INTEGER PRIMARY KEY, race_id TEXT, bet_type TEXT, combinations TEXT,
                payout INTEGER, popularity INTEGER
            );
        """)
        for race_id in IDS:
            race = _race(race_id)
            conn.execute("INSERT INTO races_ultimate VALUES (?,?)", (race_id, json.dumps(race["race_info"])))
            conn.executemany("INSERT INTO race_results_ultimate(race_id, data) VALUES (?,?)", [
                (race_id, json.dumps(horse)) for horse in race["horses"]
            ])
            conn.execute(
                "INSERT INTO return_tables_ultimate(race_id, bet_type, combinations, payout, popularity) "
                "VALUES (?, 'win', '1', 300, 1)", (race_id,),
            )
    for race_id in IDS:
        race = _race(race_id)
        record_race_quality(db, race_date=RACE_DATE, race_id=race_id,
                            report=classify_race_quality(race), race_data=race)
    audit = run_date_repair_audit(db, RACE_DATE, IDS)
    source = inventory.RaceListSource("db.netkeiba.com", fetched=SimpleNamespace(
        body=b"<html>original complete day</html>",
        cache_snapshot={"url": f"https://db.netkeiba.com/race/list/{RACE_DATE}/",
                        "fetched_at": NOW - 100, "expires_at": NOW + 100},
    ), parser=inventory._VERSIONS["race_list.py"])
    sources = {stamp["url"]: dict(stamp) for stamp in source.inventory_sources}
    monkeypatch.setattr(inventory, "_read_source_stamp", lambda url: sources.get(url))
    return db, source, audit, sources


def _promote(complete_day, **kwargs):
    db, source, audit, _ = complete_day
    return inventory.record_finalized_race_inventory(
        db, RACE_DATE, IDS, source=source, audit=audit, today=TODAY, now=NOW, **kwargs,
    )


def _get(complete_day, **kwargs):
    return inventory.get_finalized_race_inventory(
        complete_day[0], RACE_DATE, today=TODAY, now=NOW, **kwargs,
    )


def test_complete_historical_inventory_preserves_order_and_original_source(complete_day):
    assert _promote(complete_day)
    assert _get(complete_day) == (IDS, "db.netkeiba.com")
    ids, _ = _get(complete_day)
    ids.clear()
    assert _get(complete_day)[0] == IDS


def test_inventory_hit_is_read_only_and_does_not_extend_source_ttl(complete_day):
    assert _promote(complete_day)
    db = complete_day[0]
    with sqlite3.connect(db) as conn:
        before = conn.execute("SELECT * FROM scrape_finalized_race_inventories").fetchall()
    assert _get(complete_day)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT * FROM scrape_finalized_race_inventories").fetchall() == before
    assert inventory.get_finalized_race_inventory(
        db, RACE_DATE, today=TODAY, now=NOW - 100 + inventory.INVENTORY_TTL_SECONDS,
    ) is None
    # A plain source string returned from a hit cannot renew the manifest.
    assert not inventory.record_finalized_race_inventory(
        db, RACE_DATE, IDS, source="db.netkeiba.com", audit=complete_day[2], today=TODAY, now=NOW,
    )


def test_force_refresh_and_recent_dates_bypass_inventory(complete_day):
    assert _promote(complete_day)
    assert _get(complete_day, force_refresh=True) is None
    assert inventory.get_finalized_race_inventory(
        complete_day[0], RACE_DATE, today=date(2020, 2, 3), now=NOW,
    ) is None
    assert not inventory.record_finalized_race_inventory(
        complete_day[0], RACE_DATE, IDS, source=complete_day[1], audit=complete_day[2],
        today=date(2020, 2, 3), now=NOW,
    )


def test_missing_database_lookup_never_creates_file(tmp_path):
    path = tmp_path / "missing.db"
    assert inventory.get_finalized_race_inventory(path, RACE_DATE, today=TODAY, now=NOW) is None
    assert not path.exists()


@pytest.mark.parametrize("audit_changes", [
    {"status": "partial"}, {"complete_race_count": 1}, {"expected_race_count": 3},
    {"missing_race_ids": [IDS[0]]}, {"quarantined_race_ids": [IDS[0]]},
    {"race_date": "20200105"}, {"no_race": True},
])
def test_incomplete_or_mismatching_audits_cannot_be_promoted(complete_day, audit_changes):
    db, source, audit, _ = complete_day
    assert not inventory.record_finalized_race_inventory(
        db, RACE_DATE, IDS, source=source, audit={**audit, **audit_changes}, today=TODAY, now=NOW,
    )


def test_empty_days_are_never_inventory_entries(complete_day):
    db, source, audit, _ = complete_day
    assert not inventory.record_finalized_race_inventory(
        db, RACE_DATE, [], source=source,
        audit={**audit, "expected_race_count": 0, "complete_race_count": 0}, today=TODAY, now=NOW,
    )


@pytest.mark.parametrize("sql,args", [
    ("UPDATE scrape_date_completeness SET expected_race_ids_json='[]'", ()),
    ("UPDATE scrape_date_completeness SET status='partial'", ()),
    ("UPDATE scrape_race_acquisition_state SET quality_status='stored_partial' WHERE race_id=?", (IDS[0],)),
    ("UPDATE scrape_race_acquisition_state SET excluded_from_standard=1 WHERE race_id=?", (IDS[0],)),
    ("UPDATE scrape_race_acquisition_state SET field_states_json=? WHERE race_id=?",
     (json.dumps({"pedigree": "repair_required"}), IDS[0])),
    ("DELETE FROM race_results_ultimate WHERE id=(SELECT MIN(id) FROM race_results_ultimate)", ()),
    ("UPDATE race_results_ultimate SET data='{}' WHERE race_id=?", (IDS[0],)),
    ("DELETE FROM races_ultimate WHERE race_id=?", (IDS[0],)),
    ("UPDATE return_tables_ultimate SET payout=500 WHERE race_id=?", (IDS[0],)),
    ("ALTER TABLE races_ultimate ADD COLUMN changed_schema TEXT", ()),
    ("UPDATE scrape_finalized_race_inventories SET schema_version=0", ()),
    ("UPDATE scrape_finalized_race_inventories SET payload_json='{}'", ()),
])
def test_quality_data_schema_or_inventory_changes_invalidate(complete_day, sql, args):
    assert _promote(complete_day)
    with sqlite3.connect(complete_day[0]) as conn:
        conn.execute(sql, args)
    assert _get(complete_day) is None


@pytest.mark.parametrize("change", ["deleted", "body", "refreshed", "parser", "quality"])
def test_source_and_parser_changes_invalidate(complete_day, monkeypatch, change):
    assert _promote(complete_day)
    sources = complete_day[3]
    stamp = next(iter(sources.values()))
    if change == "deleted":
        sources.clear()
    elif change == "body":
        stamp["body_sha256"] = "changed"
    elif change == "refreshed":
        stamp["fetched_at"] += 1
    else:
        name = "race_list.py" if change == "parser" else "quality.py"
        monkeypatch.setattr(inventory, "_VERSIONS", {**inventory._VERSIONS, name: "changed"})
    assert _get(complete_day) is None


def test_jra_provenance_is_never_promoted_or_reused_as_all_venues(complete_day):
    db, _, audit, sources = complete_day
    source = inventory.RaceListSource("race.netkeiba.com", fetched=SimpleNamespace(
        body=b"JRA day", cache_snapshot={
            "url": f"https://race.netkeiba.com/top/race_list_sub.html?kaisai_date={RACE_DATE}",
            "fetched_at": NOW - 100,
        },
    ), parser=inventory._VERSIONS["race_list.py"])
    sources.update({stamp["url"]: dict(stamp) for stamp in source.inventory_sources})
    assert not inventory.record_finalized_race_inventory(
        db, RACE_DATE, IDS, source=source, audit=audit, today=TODAY, now=NOW,
    )
    assert inventory.record_finalized_race_inventory(
        db, RACE_DATE, IDS, source=source, audit=audit, scope="jra", today=TODAY, now=NOW,
    )
    assert _get(complete_day) is None
    assert _get(complete_day, scope="jra") == (IDS, "race.netkeiba.com")
    assert not inventory.record_finalized_race_inventory(
        db, RACE_DATE, IDS, source="db.sp.netkeiba.com/month-search", audit=audit, today=TODAY, now=NOW,
    )


def test_pending_pedigree_and_invalid_stored_payload_cannot_be_promoted(complete_day):
    db = complete_day[0]
    with sqlite3.connect(db) as conn:
        # Even if the ledger still says complete, the stored payload must pass
        # the same quality check before becoming durable inventory evidence.
        conn.execute("UPDATE race_results_ultimate SET data='{}' WHERE race_id=?", (IDS[0],))
    assert not _promote(complete_day)


def test_lookup_uses_race_id_indexes_for_large_payload_tables(complete_day):
    assert _promote(complete_day)
    with sqlite3.connect(complete_day[0]) as conn:
        for table, index in (
            ("race_results_ultimate", "idx_finalized_inventory_horses"),
            ("return_tables_ultimate", "idx_finalized_inventory_returns"),
        ):
            plan = conn.execute(
                f"EXPLAIN QUERY PLAN SELECT * FROM {table} INDEXED BY {index} WHERE race_id=?", (IDS[0],),
            ).fetchall()
            assert any("SEARCH" in row[3] and index in row[3] for row in plan)


def test_original_response_cannot_be_relabelled_with_a_concurrent_refresh(complete_day):
    stamp = next(iter(complete_day[3].values()))
    stamp["fetched_at"] += 1
    stamp["body_sha256"] = "newer body"
    assert not _promote(complete_day)


def test_source_stamp_survives_short_http_ttl_but_detects_body_change(tmp_path, monkeypatch):
    from scraping import fetch_pipeline

    cache = tmp_path / "http.db"
    url = f"https://db.netkeiba.com/race/list/{RACE_DATE}/"
    monkeypatch.setattr(fetch_pipeline, "_CACHE_DB_PATH", cache)
    with sqlite3.connect(cache) as conn:
        conn.execute("CREATE TABLE http_cache (normalized_url TEXT PRIMARY KEY, status INTEGER, "
                     "fetched_at REAL, expires_at REAL, body BLOB)")
        conn.execute("INSERT INTO http_cache VALUES (?,200,?,?,?)", (url, NOW - 100, 0, b"original"))
    stamp = inventory._read_source_stamp(url)
    assert stamp == {"url": url, "fetched_at": NOW - 100,
                     "body_sha256": hashlib.sha256(b"original").hexdigest()}
    with sqlite3.connect(cache) as conn:
        conn.execute("UPDATE http_cache SET body=? WHERE normalized_url=?", (b"changed", url))
    assert inventory._read_source_stamp(url) != stamp
    with sqlite3.connect(cache) as conn:
        conn.execute("DELETE FROM http_cache WHERE normalized_url=?", (url,))
    assert inventory._read_source_stamp(url) is None


def test_incomplete_existing_quality_does_not_get_durable_inventory(complete_day):
    db = complete_day[0]
    race = _race(IDS[0])
    race["horses"][0]["sire"] = None
    report = classify_race_quality(race)
    assert report.valid_for_date_completion
    assert report.field_states["pedigree"] == "repair_required"
    record_race_quality(db, race_date=RACE_DATE, race_id=IDS[0], report=report, race_data=race)
    assert not _promote(complete_day)


def test_existing_race_id_indexes_are_reused_without_building_duplicates(complete_day):
    db = complete_day[0]
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE INDEX "existing horse ""index" ON race_results_ultimate(race_id)')
        conn.execute("CREATE INDEX existing_returns ON return_tables_ultimate(race_id, id)")
        before = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name IN (?,?) ORDER BY name",
            ("race_results_ultimate", "return_tables_ultimate"),
        ).fetchall()
    assert _promote(complete_day)
    assert _get(complete_day) == (IDS, "db.netkeiba.com")
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name IN (?,?) ORDER BY name",
            ("race_results_ultimate", "return_tables_ultimate"),
        ).fetchall() == before


def test_partial_or_nonleading_race_id_indexes_do_not_cover_full_inventory(complete_day):
    db = complete_day[0]
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE INDEX partial_horses ON race_results_ultimate(race_id) WHERE id > 100")
        conn.execute("CREATE INDEX nonleading_returns ON return_tables_ultimate(payout, race_id)")
    assert _promote(complete_day)
    assert _get(complete_day) == (IDS, "db.netkeiba.com")
    with sqlite3.connect(db) as conn:
        assert inventory._race_id_index(conn, "race_results_ultimate") == "idx_finalized_inventory_horses"
        assert inventory._race_id_index(conn, "return_tables_ultimate") == "idx_finalized_inventory_returns"
