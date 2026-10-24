"""Isolated fixtures only: never scan the live DB or its capture ledger."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pandas as pd
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/prepare_h04_history_revision.py"
spec = importlib.util.spec_from_file_location("prepare_h04_history_revision_under_test", SCRIPT)
revision = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = revision
spec.loader.exec_module(revision)

NOW = datetime(2026, 10, 17, 8, 0, tzinfo=timezone.utc)


def source_fixture(path):
    db = sqlite3.connect(path)
    db.executescript("CREATE TABLE races_ultimate(race_id TEXT,data TEXT,created_at TEXT); CREATE TABLE race_results_ultimate(race_id TEXT,data TEXT,created_at TEXT)")
    for day, suffix in (("20261016", "01"), ("20261017", "02"), ("20261018", "03"), ("20261019", "04")):
        rid = "2026050406" + suffix
        race = {"race_id": rid, "date": day, "surface": "turf", "venue": "東京",
                "distance": 1600, "num_horses": 4}
        db.execute("INSERT INTO races_ultimate VALUES (?,?, 'fixture')", (rid, json.dumps(race)))
        for number in range(1, 5):
            row = {"race_id": rid, "horse_id": f"horse{number}", "horse_number": number,
                "finish": number, "time_seconds": 90 + number, "odds": 2 * number,
                "popularity": number, "sire": "S", "damsire": "D", "last_3f_time": 35.2}
            db.execute("INSERT INTO race_results_ultimate VALUES (?,?,'fixture')", (rid, json.dumps(row)))
    db.commit()
    db.close()
    from services.prediction_source_revision import initialize_source_revision
    assert initialize_source_revision(path.resolve(), True)
    return path


def ledger_fixture(root, *, status="complete", rows=None):
    root.mkdir()
    db = sqlite3.connect(root / "ledger.sqlite3")
    db.executescript("CREATE TABLE schedule_days(date TEXT,status TEXT); CREATE TABLE races(race_id TEXT,race_date TEXT,post_time TEXT,status TEXT)")
    if status is not None:
        db.execute("INSERT INTO schedule_days VALUES ('20261017',?)", (status,))
    for rid, post, state in rows or []:
        db.execute("INSERT INTO races VALUES (?,'20261017',?,?)", (rid, post, state))
    db.commit()
    db.close()
    return root


@pytest.mark.parametrize("target", ["20261016", "20261017"])
def test_past_and_same_day_targets_are_rejected(target):
    with pytest.raises(ValueError, match="same-day-or-past"):
        revision.validate_target(target, now=NOW)


@pytest.mark.parametrize("target", ["20261019", "20261025"])
def test_not_arbitrary_future_revision(target):
    with pytest.raises(ValueError, match="next-calendar-day"):
        revision.validate_target(target, now=NOW)


def test_next_day_and_jst_boundary():
    assert revision.validate_target("20261018", now=NOW) == "20261018"
    # 16:00 UTC on the 17th is already 01:00 JST on the 18th.
    with pytest.raises(ValueError, match="same-day-or-past"):
        revision.validate_target("20261018", now=datetime(2026, 10, 17, 16, tzinfo=timezone.utc))


@pytest.mark.parametrize("target", ["2026-10-18", "20261301", "2026102", None])
def test_invalid_dates_rejected(target):
    with pytest.raises(ValueError):
        revision.validate_target(target, now=NOW)


def test_naive_clock_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        revision.validate_target("20261018", now=datetime(2026, 10, 17))


def test_output_is_new_isolated_path(tmp_path):
    base = tmp_path / "revisions"
    assert revision.validate_output(base / "v1", revision_root=base) == (base / "v1").resolve()
    with pytest.raises(ValueError, match="isolated"):
        revision.validate_output(tmp_path / "runtime/daily/20261018", revision_root=base)
    with pytest.raises(ValueError, match="isolated"):
        revision.validate_output(base, revision_root=base)
    (base / "v1").mkdir(parents=True)
    with pytest.raises(ValueError, match="must-not-exist"):
        revision.validate_output(base / "v1", revision_root=base)


@pytest.mark.parametrize("status", [None, "unknown", "partial"])
def test_incomplete_today_schedule_prevents_heavy_work(tmp_path, status):
    root = ledger_fixture(tmp_path / "runtime", status=status)
    with pytest.raises(ValueError, match="complete-schedule"):
        revision.capture_idle_receipt(root, now=NOW)


@pytest.mark.parametrize("state", ["queued", "capturing"])
def test_any_remaining_capture_prevents_heavy_work(tmp_path, state):
    root = ledger_fixture(tmp_path / "runtime", rows=[("x", "2026-10-17T07:30:00Z", state)])
    with pytest.raises(ValueError, match="capture-work-remains"):
        revision.capture_idle_receipt(root, now=NOW)


def test_waits_until_one_minute_after_final_post(tmp_path):
    root = ledger_fixture(tmp_path / "runtime", rows=[("x", "2026-10-17T08:00:00Z", "captured")])
    with pytest.raises(ValueError, match="final-registered-race"):
        revision.capture_idle_receipt(root, now=NOW)


def test_capture_idle_check_is_read_only(tmp_path):
    root = ledger_fixture(tmp_path / "runtime", rows=[("x", "2026-10-17T07:30:00Z", "captured")])
    before = revision.file_hash(root / "ledger.sqlite3")
    receipt = revision.capture_idle_receipt(root, now=NOW)
    assert receipt["queued_or_capturing"] == [] and receipt["ledger_read_only"] is True
    assert revision.file_hash(root / "ledger.sqlite3") == before


def test_standard_build_uses_physical_snapshot_and_is_identical_to_frozen_gate(tmp_path):
    from forward_runtime.data_quality import audit_clean_history, prepare_daily_history
    from experiments.common import read_json
    source = source_fixture(tmp_path / "source.db")
    original_hash = revision.file_hash(source)
    run_dir = tmp_path / "revision"
    run_dir.mkdir()
    result = revision.build_standard_artifacts(source, run_dir, target_date="20261018")
    snapshot = Path(result["snapshot_path"])
    assert revision.file_hash(snapshot) == result["snapshot_sha256"]
    assert revision.file_hash(source) == original_hash
    assert result["daily"]["snapshot_sha256"] == result["snapshot_sha256"]
    assert result["daily"]["history_max_source_date"] == "20261017"
    assert result["daily"]["history_rows"] == 8  # Target day and the future excluded.
    assert result["daily"]["same_day_or_future_history_used"] is False
    assert result["source_revision_before"] == result["source_revision_after"]
    assert result["snapshot_content_epoch"] == result["source_revision_before"]["content_epoch"]
    direct = tmp_path / "direct-clean"
    audit_clean_history(snapshot, direct, before_date="20261018")
    direct_daily = prepare_daily_history(direct, tmp_path / "direct-state", target_date="20261018")
    pd.testing.assert_frame_equal(pd.read_parquet(run_dir / "clean/clean_history.parquet"),
        pd.read_parquet(direct / "clean_history.parquet"))
    assert read_json(result["daily"]["state_path"]) == read_json(direct_daily["state_path"])
    assert read_json(result["daily"]["bloodline_path"]) == read_json(direct_daily["bloodline_path"])


def test_dry_run_does_not_create_or_build(monkeypatch, tmp_path):
    path = tmp_path / "new-output"
    monkeypatch.setattr(revision, "validate_output", lambda output: path)
    monkeypatch.setattr(revision, "protection_receipt", lambda **kw: {"unchanged": True})
    monkeypatch.setattr(revision, "reserve_receipt", lambda *args: {"sufficient": True})
    def forbidden(*args, **kwargs):
        raise AssertionError("dry-run touched expensive state")
    monkeypatch.setattr(revision, "build_standard_artifacts", forbidden)
    monkeypatch.setattr(revision, "capture_idle_receipt", forbidden)
    result = revision.prepare_revision(target_date="20261018", output_dir=path,
        now=NOW, source_db=tmp_path / "source.db", runtime_root=tmp_path / "runtime")
    assert result["operation"] == "dry_run" and result["physical_database_copy_or_hash_performed"] is False
    assert result["runtime_publication"] is False and not path.exists()


def test_changed_protected_state_prevents_completed_receipt(monkeypatch, tmp_path):
    output = tmp_path / "revision"
    monkeypatch.setattr(revision, "validate_output", lambda value: output)
    calls = iter([{"model": "before"}, {"model": "after"}])
    monkeypatch.setattr(revision, "protection_receipt", lambda **kw: next(calls))
    monkeypatch.setattr(revision, "reserve_receipt", lambda *args: {"sufficient": True})
    monkeypatch.setattr(revision, "capture_idle_receipt", lambda *args, **kw: {})
    monkeypatch.setattr(revision, "build_standard_artifacts", lambda *args, **kw: {})
    with pytest.raises(ValueError, match="protected-h04"):
        revision.prepare_revision(target_date="20261018", output_dir=output,
            now=NOW, build=True, source_db=tmp_path / "source.db", runtime_root=tmp_path / "runtime")
    assert not (output / "revision_receipt.json").exists()
    assert revision.read_json(output / "preparation_failure.json")["operation"] == "failed_isolated_revision_not_published"


def test_no_runtime_publication_or_network_cli():
    # Guard against later accidental scope expansion in this repair helper.
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'add_argument("--publish' not in source
    assert 'add_argument("--allow-network' not in source


def test_lightweight_source_epoch_tracks_raw_and_quality_only(tmp_path):
    source = source_fixture(tmp_path / "source.db")
    before = revision.source_epoch_receipt(source)
    db = sqlite3.connect(source)
    db.execute("CREATE TABLE unrelated_log(id INTEGER)")
    db.execute("INSERT INTO unrelated_log VALUES (1)")
    db.commit()
    assert revision.source_epoch_receipt(source) == before
    db.execute("UPDATE race_results_ultimate SET data=data || ' ' WHERE rowid=1")
    db.commit()
    raw_changed = revision.source_epoch_receipt(source)
    assert raw_changed != before
    db.execute("CREATE TABLE scrape_race_acquisition_state(race_id TEXT,lifecycle TEXT,quality_status TEXT,excluded_from_standard INTEGER)")
    db.execute("INSERT INTO scrape_race_acquisition_state VALUES ('202605040601','result','complete',0)")
    db.commit()
    assert revision.source_epoch_receipt(source) != raw_changed
    db.close()


def test_missing_source_tracking_is_rejected_not_repaired(tmp_path):
    source = tmp_path / "source.db"
    db = sqlite3.connect(source)
    db.executescript("CREATE TABLE races_ultimate(race_id TEXT,data TEXT); CREATE TABLE race_results_ultimate(race_id TEXT,data TEXT)")
    db.close()
    before = revision.file_hash(source)
    with pytest.raises(ValueError, match="validated-source-generation"):
        revision.source_epoch_receipt(source)
    assert revision.file_hash(source) == before

