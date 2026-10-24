"""Only temporary synthetic databases; no live migration or acquisitions."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import time

import pytest

from services import prediction_source_revision as revision


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "fixture.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE races_ultimate(race_id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT);
            CREATE TABLE race_results_ultimate(id INTEGER PRIMARY KEY, race_id TEXT, data TEXT, created_at TEXT);
            CREATE TABLE prediction_log(id INTEGER PRIMARY KEY, data TEXT);
            CREATE TABLE unrelated(value TEXT);
            CREATE TRIGGER user_source_audit AFTER INSERT ON prediction_log BEGIN
                INSERT INTO unrelated VALUES ('user audit');
            END;
        """)
        db.execute("INSERT INTO races_ultimate VALUES ('202605010101',?,?)",
                   (json.dumps({"race_id": "202605010101", "date": "20260105"}), "fixed"))
        db.execute("INSERT INTO race_results_ultimate VALUES (1,'202605010101',?,?)",
                   (json.dumps({"horse_number": 1, "time_seconds": 96.0}), "fixed"))
    return path


def provider_state(path):
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        return {name: db.execute(f"SELECT * FROM {name} ORDER BY rowid").fetchall()
                for name in revision.SOURCE_TABLES}


def metadata_state(path):
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        return db.execute(f"SELECT * FROM {revision.TABLE_NAME}").fetchone()


def test_explicit_local_migration_is_idempotent_and_preserves_source(source):
    before = provider_state(source)
    assert revision.read_source_revision(source)["source_tracking"] is False
    assert revision.initialize_source_revision(source, local_enabled=False) is False
    assert revision.initialize_source_revision(source, local_enabled="true") is False
    with sqlite3.connect(source) as db:
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name=?",
                          (revision.TABLE_NAME,)).fetchone()[0] == 0
    assert revision.initialize_source_revision(source, local_enabled=True) is True
    first = revision.read_source_revision(source)
    assert first["source_tracking"] is True
    assert first["revision_kind"] == "sqlite-source-generation"
    assert len(first["data_revision"]) == 64
    state = metadata_state(source)
    assert state[3] == 0
    assert revision.initialize_source_revision(source, local_enabled=True) is True
    assert revision.read_source_revision(source) == first
    assert metadata_state(source) == state
    assert provider_state(source) == before
    with sqlite3.connect(source) as db:
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert names == {*revision.TRIGGER_DEFINITIONS, "user_source_audit"}


@pytest.mark.parametrize("table,operation", [
    (table, operation) for table in revision.SOURCE_TABLES for operation in ("INSERT", "UPDATE", "DELETE")
])
def test_every_committed_source_operation_invalidates(source, table, operation):
    assert revision.initialize_source_revision(source, True)
    before = revision.read_source_revision(source)
    initial = metadata_state(source)
    with sqlite3.connect(source) as db:
        if operation == "INSERT":
            db.execute(f"INSERT INTO {table}(race_id,data,created_at) VALUES ('202605010102','{{}}','fixed')")
        elif operation == "UPDATE":
            db.execute(f"UPDATE {table} SET data='{{\"new\":1}}' WHERE race_id='202605010101'")
        else:
            db.execute(f"DELETE FROM {table} WHERE race_id='202605010101'")
    after = revision.read_source_revision(source)
    assert after["source_tracking"] is True
    assert before["data_revision"] != after["data_revision"]
    current = metadata_state(source)
    assert current[2] == initial[2]
    assert current[3] == initial[3] + 1
    assert current[4] != initial[4]


@pytest.mark.parametrize("field,new_value", [("race_id", "202605010103"), ("created_at", "changed")])
@pytest.mark.parametrize("table", revision.SOURCE_TABLES)
def test_identity_and_acquisition_timestamp_changes_invalidate(source, table, field, new_value):
    assert revision.initialize_source_revision(source, True)
    before = revision.read_source_revision(source)
    with sqlite3.connect(source) as db:
        db.execute(f"UPDATE {table} SET {field}=? WHERE race_id='202605010101'", (new_value,))
    assert revision.read_source_revision(source)["data_revision"] != before["data_revision"]


def test_same_row_count_and_preserved_file_timestamp_still_invalidates(source):
    assert revision.initialize_source_revision(source, True)
    before = revision.read_source_revision(source)
    stat = source.stat()
    original = provider_state(source)
    with sqlite3.connect(source) as db:
        db.execute("UPDATE race_results_ultimate SET data=? WHERE id=1",
                   (json.dumps({"horse_number": 1, "time_seconds": 97.0}),))
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert source.stat().st_size == stat.st_size
    assert source.stat().st_mtime_ns == stat.st_mtime_ns
    assert len(provider_state(source)["race_results_ultimate"]) == len(original["race_results_ultimate"])
    assert revision.read_source_revision(source)["data_revision"] != before["data_revision"]


def test_noop_source_updates_logs_and_other_tables_do_not_invalidate(source):
    assert revision.initialize_source_revision(source, True)
    before = revision.read_source_revision(source)
    with sqlite3.connect(source) as db:
        for name in revision.SOURCE_TABLES:
            db.execute(f"UPDATE {name} SET race_id=race_id,data=data,created_at=created_at")
        db.execute("UPDATE race_results_ultimate SET id=10 WHERE id=1")
        db.execute("INSERT INTO prediction_log VALUES (1,'prediction')")
        db.execute("UPDATE prediction_log SET data='updated prediction'")
        db.execute("DELETE FROM prediction_log")
        db.execute("INSERT INTO unrelated VALUES ('ordinary operational write')")
    assert revision.read_source_revision(source) == before
    assert metadata_state(source)[3] == 0


def test_rollback_does_not_publish_source_revision(source):
    assert revision.initialize_source_revision(source, True)
    before = revision.read_source_revision(source)
    with sqlite3.connect(source) as db:
        db.execute("DELETE FROM race_results_ultimate")
        db.execute("INSERT INTO race_results_ultimate VALUES (2,'202605010101','{}','fixed')")
        db.rollback()
    assert revision.read_source_revision(source) == before


def test_wal_source_commits_invalidate_but_log_commits_and_checkpoint_do_not(source):
    with sqlite3.connect(source) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA wal_autocheckpoint=0")
        assert revision.initialize_source_revision(source, True)
        before = revision.read_source_revision(source)
        stat = source.stat()
        db.execute("UPDATE race_results_ultimate SET data='new result' WHERE id=1")
        db.commit()
        after = revision.read_source_revision(source)
        assert after["data_revision"] != before["data_revision"]
        assert source.stat().st_mtime_ns == stat.st_mtime_ns
        db.execute("INSERT INTO prediction_log VALUES (1,'prediction')")
        db.commit()
        assert revision.read_source_revision(source) == after
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        assert revision.read_source_revision(source) == after


def test_reader_does_not_create_or_repair_missing_schema(source, tmp_path):
    before = provider_state(source)
    fallback = revision.read_source_revision(source)
    assert fallback["revision_kind"] == "sqlite-file-version"
    assert fallback["source_tracking"] is False
    assert revision.read_source_revision(source) == fallback
    assert provider_state(source) == before
    with sqlite3.connect(source) as db:
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name=?",
                          (revision.TABLE_NAME,)).fetchone()[0] == 0
    missing = tmp_path / "missing.db"
    assert not revision.initialize_source_revision(missing, True)
    assert revision.read_source_revision(missing)["source_tracking"] is False
    assert not missing.exists()


def test_missing_or_incomplete_source_tables_are_not_migrated(tmp_path):
    path = tmp_path / "incomplete.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE races_ultimate(race_id TEXT,data TEXT,created_at TEXT)")
        db.execute("CREATE TABLE race_results_ultimate(race_id TEXT,data TEXT)")
    assert not revision.initialize_source_revision(path, True)
    assert revision.read_source_revision(path)["source_tracking"] is False
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name=?",
                          (revision.TABLE_NAME,)).fetchall() == []


@pytest.mark.parametrize("kind", ["table", "trigger"])
def test_reserved_name_conflicts_are_kept_and_fail_closed(source, kind):
    with sqlite3.connect(source) as db:
        if kind == "table":
            db.execute(f"CREATE TABLE {revision.TABLE_NAME}(value TEXT)")
            db.execute(f"INSERT INTO {revision.TABLE_NAME} VALUES ('user-owned')")
        else:
            name = next(iter(revision.TRIGGER_DEFINITIONS))
            db.execute(f"CREATE TRIGGER {name} AFTER INSERT ON unrelated BEGIN SELECT 1; END")
        before = db.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
    assert not revision.initialize_source_revision(source, True)
    assert revision.read_source_revision(source)["source_tracking"] is False
    with sqlite3.connect(source) as db:
        assert db.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall() == before
        if kind == "table":
            assert db.execute(f"SELECT * FROM {revision.TABLE_NAME}").fetchall() == [("user-owned",)]


def test_partial_or_tampered_tracking_is_never_silently_repaired(source):
    assert revision.initialize_source_revision(source, True)
    name = next(iter(revision.TRIGGER_DEFINITIONS))
    with sqlite3.connect(source) as db:
        db.execute(f"DROP TRIGGER {name}")
    assert revision.read_source_revision(source)["source_tracking"] is False
    assert not revision.initialize_source_revision(source, True)
    with sqlite3.connect(source) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name=?", (name,)).fetchall() == []


def test_uncommitted_source_change_is_not_visible_to_reader(source):
    with sqlite3.connect(source) as db:
        db.execute("PRAGMA journal_mode=WAL")
        assert revision.initialize_source_revision(source, True)
        before = revision.read_source_revision(source)
        db.execute("UPDATE race_results_ultimate SET data='pending result'")
        assert revision.read_source_revision(source) == before
        db.commit()
        assert revision.read_source_revision(source)["data_revision"] != before["data_revision"]


def test_concurrent_readers_and_writer_only_see_valid_committed_states(source):
    assert revision.initialize_source_revision(source, True)
    with sqlite3.connect(source) as db:
        db.execute("PRAGMA journal_mode=WAL")
    initial = revision.read_source_revision(source)

    def write():
        with sqlite3.connect(source, timeout=5) as db:
            for number in range(30):
                db.execute("UPDATE race_results_ultimate SET data=? WHERE id=1", (str(number),))
                db.commit()

    def read():
        return [revision.read_source_revision(source) for _ in range(45)]

    with ThreadPoolExecutor(max_workers=3) as pool:
        writer = pool.submit(write)
        readers = [pool.submit(read) for _ in range(2)]
        writer.result()
        rows = [value for reader in readers for value in reader.result()]
    assert all(row["source_tracking"] and len(row["data_revision"]) == 64 for row in rows)
    assert metadata_state(source)[3] == 30
    assert revision.read_source_revision(source)["data_revision"] != initial["data_revision"]


def test_small_write_workload_uses_constant_metadata_not_source_scans(source):
    assert revision.initialize_source_revision(source, True)
    started = time.perf_counter()
    with sqlite3.connect(source) as db:
        for number in range(100):
            db.execute("UPDATE race_results_ultimate SET data=? WHERE id=1", (str(number),))
    assert metadata_state(source)[3] == 100
    assert time.perf_counter() - started < 5.0


def test_readonly_migration_failure_does_not_create_partial_schema(source, monkeypatch):
    original = sqlite3.connect

    def readonly_connect(uri, *args, **kwargs):
        return original(uri.replace("?mode=rw", "?mode=ro"), *args, **kwargs)

    monkeypatch.setattr(revision.sqlite3, "connect", readonly_connect)
    assert revision.initialize_source_revision(source, True) is False
    assert revision.read_source_revision(source)["source_tracking"] is False
    with original(source) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name=?",
                          (revision.TABLE_NAME,)).fetchall() == []
