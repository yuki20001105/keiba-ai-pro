from __future__ import annotations

import asyncio
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from scraping.fetch_pipeline import FetchAccessBlocked, FetchResult
from test_pedigree_identity import HORSE, page


@pytest.fixture
def runner():
    script = Path(__file__).resolve().parents[2] / "scripts" / "reacquire_pedigree.py"
    spec = importlib.util.spec_from_file_location("reacquire_fixture", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def queue(tmp_path):
    with sqlite3.connect(tmp_path / "source-archive.db") as db:
        db.executescript("""
            CREATE TABLE sources(url TEXT PRIMARY KEY,final_url TEXT,status INTEGER,body BLOB,fetched_at REAL);
            CREATE TABLE queue(horse_id TEXT PRIMARY KEY,affected_rows INTEGER,status TEXT,
                proof_json TEXT,error TEXT,attempted_at TEXT,fetch_source TEXT,http_status INTEGER,
                network_requests INTEGER DEFAULT 0);
        """)
        db.executemany("INSERT INTO queue(horse_id,affected_rows,status) VALUES(?,?,'pending')",
                       [(str(int(HORSE) + index), 10 - index) for index in range(4)])
    return tmp_path


def install_fetch(monkeypatch, runner, calls, outcomes=None):
    monkeypatch.setattr(runner, "get_fetch_control_status", lambda: {})

    async def fetch(session, url, **kwargs):
        calls.append((url, kwargs))
        horse = url.rstrip("/").split("/")[-1]
        outcome = outcomes[len(calls) - 1] if outcomes else "accepted"
        if outcome == "blocked":
            raise FetchAccessBlocked("http-403")
        body = page().replace(HORSE, horse).encode() if outcome == "accepted" else b"<html>missing</html>"
        return FetchResult(url, url, 200, body, "network", 1,
                           cache_snapshot={"fetched_at": 1000}), body.decode()

    monkeypatch.setattr(runner, "fetch_text", fetch)


def test_checkpointed_resume_skips_accepted_horses_and_retains_source_bytes(runner, queue, monkeypatch):
    calls = []
    install_fetch(monkeypatch, runner, calls)
    first = asyncio.run(runner.collect(queue, 1))
    second = asyncio.run(runner.collect(queue, 1))
    assert first["counts"] == {"accepted": 1, "pending": 3}
    assert second["counts"] == {"accepted": 2, "pending": 2}
    assert calls[0][0] != calls[1][0]
    assert calls[0][1]["min_interval_sec"] == 2.0
    assert calls[0][1]["allow_redirects"] is False
    with closing(sqlite3.connect(queue / "source-archive.db")) as db:
        assert db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 2
        raw = db.execute("SELECT proof_json FROM queue WHERE horse_id=?", (HORSE,)).fetchone()[0]
        assert json.loads(raw)["parents"]["damsire"]["horse_id"] == "000a010a17"


def test_access_block_stops_before_next_horse_and_keeps_success(runner, queue, monkeypatch):
    calls = []
    install_fetch(monkeypatch, runner, calls, ["accepted", "blocked"])
    result = asyncio.run(runner.collect(queue, None))
    assert len(calls) == 2
    assert result["stop_reason"] == "provider_blocked"
    assert result["counts"] == {"accepted": 1, "pending": 2, "provider_blocked": 1}


def test_existing_provider_stop_does_not_issue_a_request(runner, queue, monkeypatch):
    calls = []
    install_fetch(monkeypatch, runner, calls)
    monkeypatch.setattr(runner, "get_fetch_control_status", lambda: {"blocked_reason": "restricted"})
    result = asyncio.run(runner.collect(queue, None))
    assert calls == []
    assert result["stop_reason"] == "provider_blocked"
    assert result["remaining"] == 4


def test_invalid_sources_keep_explicit_reasons_and_stop_after_three(runner, queue, monkeypatch):
    calls = []
    install_fetch(monkeypatch, runner, calls, ["invalid"] * 3)
    result = asyncio.run(runner.collect(queue, None))
    assert len(calls) == 3
    assert result["counts"] == {"pending": 1, "source_rejected": 3}
    assert result["stop_reason"] == "three_consecutive_unaccepted_sources"
    with closing(sqlite3.connect(queue / "source-archive.db")) as db:
        reasons = [raw for raw, in db.execute("SELECT error FROM queue WHERE status='source_rejected'")]
    assert all("child-identity-conflict" in reason for reason in reasons)
