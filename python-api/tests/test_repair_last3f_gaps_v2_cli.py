"""Reuse V1 contracts unchanged, plus the limited V2 operational amendments."""
from __future__ import annotations

import asyncio
import errno
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
V2_SPEC = importlib.util.spec_from_file_location("last3f_gap_v2_cli_fixture", ROOT / "scripts/repair_last3f_gaps_v2.py")
cli = importlib.util.module_from_spec(V2_SPEC)
V2_SPEC.loader.exec_module(cli)

LEGACY_SPEC = importlib.util.spec_from_file_location("last3f_gap_v1_contracts_reused", Path(__file__).with_name("test_repair_last3f_gaps_cli.py"))
legacy = importlib.util.module_from_spec(LEGACY_SPEC)
LEGACY_SPEC.loader.exec_module(legacy)
legacy.cli = cli  # Runtime injection only; no V1 file or frozen artifact is edited.

stores = legacy.stores
for name, function in vars(legacy).items():
    if name.startswith("test_") and name != "test_network_stop_request_finishes_current_transaction_then_stops":
        globals()["test_v1_contract_" + name[5:]] = function


def sharing_error(code=32):
    error = PermissionError(errno.EACCES, "injected status handle busy")
    error.winerror = code
    return error


@pytest.mark.parametrize("code", [5, 32, 33])
def test_short_status_handle_contention_retries_then_succeeds(tmp_path, monkeypatch, code):
    target = tmp_path / "status.json"
    target.write_text('{"status":"old"}', encoding="utf-8")
    original = cli.os.replace
    calls, sleeps = [], []

    def replace(source, dest):
        calls.append((source, dest))
        if len(calls) < 3:
            raise sharing_error(code)
        original(source, dest)

    monkeypatch.setattr(cli.os, "replace", replace)
    monkeypatch.setattr(cli.time, "sleep", sleeps.append)
    cli.atomic_status(target, {"status": "new"})
    assert cli.read_json(target) == {"status": "new"}
    assert len(calls) == 3 and sleeps == [.025, .05]
    assert not list(tmp_path.glob("failure-*.json"))


def test_permanent_status_handle_contention_is_bounded_and_retains_failure_receipt(tmp_path, monkeypatch):
    target = tmp_path / "status.json"
    target.write_text('{"status":"old"}', encoding="utf-8")
    calls, sleeps = [], []

    def replace(*args):
        calls.append(args)
        raise sharing_error(32)

    monkeypatch.setattr(cli.os, "replace", replace)
    monkeypatch.setattr(cli.time, "sleep", sleeps.append)
    with pytest.raises(PermissionError):
        cli.atomic_status(target, {"status": "new"})
    assert len(calls) == 7 and len(sleeps) == 6 and sum(sleeps) <= 1.1
    assert cli.read_json(target)["status"] == "old"
    receipt = cli.read_json(next(tmp_path.glob("failure-*.json")))
    assert receipt["replace_attempts"] == 7
    assert receipt["intended_status"]["status"] == "new"
    assert Path(receipt["pending_path"]).is_file()


@pytest.mark.parametrize("error", [PermissionError(errno.EACCES, "ACL denied"), OSError(errno.ENOSPC, "disk full")])
def test_nonsharing_permission_and_disk_errors_are_not_retried(tmp_path, monkeypatch, error):
    target = tmp_path / "status.json"
    target.write_text("{}", encoding="utf-8")
    calls, sleeps = [], []

    def replace(*args):
        calls.append(args)
        raise error

    monkeypatch.setattr(cli.os, "replace", replace)
    monkeypatch.setattr(cli.time, "sleep", sleeps.append)
    with pytest.raises(OSError):
        cli.atomic_status(target, {"status": "new"})
    assert len(calls) == 1 and sleeps == []
    assert len(list(tmp_path.glob("failure-*.json"))) == 1


def test_failure_handler_preserves_original_error_even_if_status_remains_locked(tmp_path, monkeypatch):
    target = tmp_path / "status.json"
    target.write_text("{}", encoding="utf-8")

    def replace(*args):
        raise sharing_error(32)

    monkeypatch.setattr(cli.os, "replace", replace)
    monkeypatch.setattr(cli.time, "sleep", lambda *_: None)
    with pytest.raises(ValueError, match="originating error"):
        with cli.failure_status(tmp_path):
            raise ValueError("originating error")
    receipts = [cli.read_json(path) for path in tmp_path.glob("failure-*.json")]
    assert any(row["schema"] == "last3f-v2-execution-failure-v1" and "originating error" in row["failure"] for row in receipts)
    assert any(row["schema"] == "last3f-v2-status-write-failure-v1" for row in receipts)


def test_cache_absence_skips_current_full_runner_load(stores, monkeypatch):
    db, _, run = stores
    legacy.plan(stores)

    def forbidden(*args, **kwargs):
        raise AssertionError("cache miss must not load current full JSON")

    monkeypatch.setattr(cli, "_current", forbidden)
    result = asyncio.run(cli.apply_run(run, mode="cache", db_path=db))
    assert result["race_status_counts"] == {"pending": 1}
    event = json.loads((run / "events.jsonl").read_text(encoding="utf-8"))
    assert event["current_revalidated"] is False and event["cache_sources_found"] == 0


def test_present_cache_still_revalidates_current_before_any_apply(stores, monkeypatch):
    db, cache, run = stores
    legacy.cached(cache, legacy.source_html())
    legacy.plan(stores)
    before = legacy.contents(db)
    calls = []

    def reject(*args, **kwargs):
        calls.append(args)
        raise ValueError("current identity drift")

    monkeypatch.setattr(cli, "_current", reject)
    result = asyncio.run(cli.apply_run(run, mode="cache", db_path=db))
    assert len(calls) == 1 and result["race_status_counts"] == {"pending": 1}
    assert legacy.contents(db) == before


def test_network_priority_is_explicit_in_v2_plan_and_latest_first(stores, monkeypatch):
    db, _, run = stores
    with sqlite3.connect(db) as connection:
        legacy.insert(connection, legacy.race(rid="202101010102", day="20210613"))
        legacy.insert(connection, legacy.race(rid="202101010103", day="20210613"))
    plan = legacy.plan(stores)
    assert plan["schema"] == "last3f-gap-repair-plan-v2"
    assert plan["execution"]["network_priority"] == "race_date_desc_race_id_desc"
    assert "scripts/repair_last3f_gaps_v2.py" in plan["code_sha256"]
    assert "scripts/repair_last3f_gaps.py" not in plan["code_sha256"]
    asyncio.run(cli.apply_run(run, mode="cache", db_path=db))
    legacy.fake_monitor(monkeypatch)
    from scraping import fetch_pipeline
    calls = []

    async def request(session, url, **kwargs):
        calls.append(url.split("race_id=")[1])
        return SimpleNamespace(status=500, body=b"", source="injected-fixture", error=None, attempts=1)

    monkeypatch.setattr(fetch_pipeline, "fetch_bytes", request)
    asyncio.run(cli.apply_run(run, mode="network", limit=3, allow_network=True, db_path=db))
    assert calls == ["202101010103", "202101010102", legacy.RID]


def test_network_stop_finishes_current_latest_race_then_leaves_older_untouched(stores, monkeypatch):
    db, _, run = stores
    latest = "202101010102"
    with sqlite3.connect(db) as connection:
        legacy.insert(connection, legacy.race(rid=latest))
    legacy.plan(stores)
    asyncio.run(cli.apply_run(run, mode="cache", db_path=db))
    legacy.fake_monitor(monkeypatch)
    from scraping import fetch_pipeline
    calls = []

    async def request(session, url, **kwargs):
        calls.append(url)
        cli.exclusive_json(run / "stop-request.json", {"stop": True})
        return SimpleNamespace(status=200, body=legacy.source_html(rid=latest).encode(), source="injected-fixture", error=None, attempts=1)

    monkeypatch.setattr(fetch_pipeline, "fetch_bytes", request)
    result = asyncio.run(cli.apply_run(run, mode="network", limit=2, allow_network=True, db_path=db))
    assert result["status"] == "stopped" and result["updated_runners_this_run"] == 2 and len(calls) == 1
    assert json.loads(legacy.contents(db)[0][2])["last_3f"] == "**"


def test_frozen_v1_source_has_not_been_edited():
    assert cli.digest_file(ROOT / "scripts/repair_last3f_gaps.py") == "215a61a2af4eca53732d7a63b7c904b3913cce4780176b4c2e4720568d8b2897"
