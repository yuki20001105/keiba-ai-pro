from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[2]
PYTHON_API = ROOT / "python-api"
VALIDATOR_PATH = ROOT / "scripts" / "validate_p0_targeted_refetch_live.py"
PLANNER_PATH = ROOT / "scripts" / "plan_p0_targeted_refetch.py"

if str(PYTHON_API) not in sys.path:
    sys.path.insert(0, str(PYTHON_API))

from scraping import fetch_pipeline  # type: ignore  # noqa: E402


def _load_validator():
    spec = importlib.util.spec_from_file_location("p0_live_validator_safety_test", VALIDATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validator = _load_validator()


def _load_planner():
    spec = importlib.util.spec_from_file_location("p0_refetch_planner_safety_test", PLANNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = _load_planner()


def _target(
    url: str,
    *,
    url_type: str = "result_page",
    race_id: str | None = "202601010101",
    horse_id: str | None = "2021100001",
):
    return validator.ValidationTarget(
        url=url,
        url_type=url_type,
        race_id=race_id,
        horse_id=horse_id,
        reason="true-missing",
        column="finish_position",
        priority="P0",
        source="plan",
        recommended_next_action="targeted refetch live validation",
    )


def _create_cache(path: Path, rows: list[tuple[str, float]] | None = None) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE http_cache (
            normalized_url TEXT PRIMARY KEY,
            final_url TEXT NOT NULL,
            status INTEGER NOT NULL,
            headers_json TEXT NOT NULL,
            body BLOB NOT NULL,
            fetched_at REAL NOT NULL,
            expires_at REAL NOT NULL
        )
        """
    )
    for url, expires_at in rows or []:
        conn.execute(
            "INSERT INTO http_cache VALUES (?, ?, 200, '{}', X'', ?, ?)",
            (url, url, time.time(), expires_at),
        )
    conn.commit()
    conn.close()


def test_fetch_pipeline_import_does_not_open_or_create_cache_db() -> None:
    code = f"""
import sqlite3, sys
sys.path.insert(0, {str(PYTHON_API)!r})
def forbidden(*args, **kwargs):
    raise AssertionError('sqlite opened during import')
sqlite3.connect = forbidden
import scraping.fetch_pipeline
"""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_cache_read_is_non_creating_and_first_write_initializes_lazily(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_path = tmp_path / "nested" / "fetch_cache.db"
    monkeypatch.setattr(fetch_pipeline, "_CACHE_DB_PATH", cache_path)

    assert fetch_pipeline._read_cache("https://example.test/") is None
    assert not cache_path.exists()
    assert not cache_path.parent.exists()

    fetch_pipeline._write_cache(
        "https://example.test/",
        "https://example.test/",
        200,
        {},
        b"ok",
        60,
    )
    assert cache_path.is_file()
    assert fetch_pipeline._read_cache("https://example.test/") is not None


@pytest.mark.parametrize(
    ("url", "url_type", "race_id", "horse_id"),
    [
        ("http://db.netkeiba.com/race/202601010101/", "result_page", "202601010101", None),
        ("https://db.netkeiba.com.evil/race/202601010101/", "result_page", "202601010101", None),
        ("https://db.netkeiba.com@127.0.0.1/race/202601010101/", "result_page", "202601010101", None),
        ("https://user@db.netkeiba.com/race/202601010101/", "result_page", "202601010101", None),
        ("https://db.netkeiba.com:443/race/202601010101/", "result_page", "202601010101", None),
        ("https://db.netkeiba.com/race/202601010101/?next=http://127.0.0.1", "result_page", "202601010101", None),
        ("https://db.netkeiba.com/race/202601010101/#x", "result_page", "202601010101", None),
        ("https://db.netkeiba.com/race/202601010101/%2f..", "result_page", "202601010101", None),
        ("https://db.netkeiba.com/race/list/20260101/", "result_page", "202601010101", None),
        ("https://db.netkeiba.com/horse/result/2021100001/", "pedigree", None, "2021100001"),
        ("https://db.netkeiba.com/race/202601010102/", "result_page", "202601010101", None),
        ("https://db.netkeiba.com/horse/ped/2021100002/", "pedigree", None, "2021100001"),
    ],
)
def test_target_url_validation_rejects_ssrf_and_contract_mismatches(
    url: str, url_type: str, race_id: str | None, horse_id: str | None
) -> None:
    checked, error = validator._validate_target_url(
        _target(url, url_type=url_type, race_id=race_id, horse_id=horse_id)
    )
    assert checked is None
    assert error


def test_target_url_validation_accepts_exact_paths_and_fills_requested_horse_id() -> None:
    race, race_error = validator._validate_target_url(
        _target(
            "https://db.netkeiba.com/race/202601010101/",
            url_type="race_detail",
            race_id=None,
            horse_id=None,
        )
    )
    horse, horse_error = validator._validate_target_url(
        _target(
            "https://db.netkeiba.com/horse/result/2021100001/",
            url_type="horse_detail",
            race_id=None,
            horse_id=None,
        )
    )
    pedigree, pedigree_error = validator._validate_target_url(
        _target(
            "https://db.netkeiba.com/horse/ped/2021100001/",
            url_type="pedigree",
            race_id=None,
            horse_id=None,
        )
    )

    assert race_error is None and race.race_id == "202601010101"
    assert horse_error is None and horse.horse_id == "2021100001"
    assert pedigree_error is None and pedigree.horse_id == "2021100001"


def test_expired_cache_entry_does_not_exclude_live_target(tmp_path: Path) -> None:
    expired = "https://db.netkeiba.com/race/202601010101/"
    fresh = "https://db.netkeiba.com/race/202601010102/"
    db = tmp_path / "cache.db"
    _create_cache(db, [(expired, time.time() - 1), (fresh, time.time() + 60)])
    before = db.stat().st_mtime_ns

    conn = validator._open_ro_db(db)
    try:
        assert validator._cache_has_url(conn, expired) is False
        assert validator._cache_has_url(conn, fresh) is True
    finally:
        conn.close()

    assert db.stat().st_mtime_ns == before


def test_read_only_cache_lookup_sees_committed_wal_entries(tmp_path: Path) -> None:
    url = "https://db.netkeiba.com/race/202601010101/"
    db = tmp_path / "wal-cache.db"
    writer = sqlite3.connect(db)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute(
            """
            CREATE TABLE http_cache (
                normalized_url TEXT PRIMARY KEY,
                final_url TEXT NOT NULL,
                status INTEGER NOT NULL,
                headers_json TEXT NOT NULL,
                body BLOB NOT NULL,
                fetched_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
            """
        )
        writer.execute(
            "INSERT INTO http_cache VALUES (?, ?, 200, '{}', X'', ?, ?)",
            (url, url, time.time(), time.time() + 60),
        )
        writer.commit()

        reader = validator._open_ro_db(db)
        try:
            assert validator._cache_has_url(reader, url) is True
            with pytest.raises(sqlite3.OperationalError):
                reader.execute("DELETE FROM http_cache")
        finally:
            reader.close()
    finally:
        writer.close()


def test_requested_horse_never_falls_back_to_first_race_row() -> None:
    html = """
    <html><body><table class="race_table_01">
      <tr><th>finish</th><th>frame</th><th>number</th><th>horse</th><th>x</th><th>x</th><th>x</th><th>time</th><th>margin</th></tr>
      <tr><td>1</td><td>2</td><td>3</td><td><a href="/horse/result/2021100002/">Wrong Horse</a></td><td>x</td><td>x</td><td>x</td><td>1:34.5</td><td>0.0</td></tr>
    </table><p class="smalltxt">enough content for parsing this representative race document</p></body></html>
    """
    fields = validator._extract_race_fields(html, "202601010101", "2021100001")
    assert fields.get("horse_id") is None
    assert fields.get("horse_name") is None
    assert fields["entries"][0]["horse_id"] == "2021100002"


def test_planner_excludes_only_nonexpired_http_cache_rows() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE http_cache (normalized_url TEXT, final_url TEXT, expires_at REAL)"
    )
    now = time.time()
    race_url = "https://db.netkeiba.com/race/202601010101/"
    horse_url = "https://db.netkeiba.com/horse/ped/2021100001/"

    conn.execute(
        "INSERT INTO http_cache VALUES (?, ?, ?)",
        (race_url, race_url, now - 60),
    )
    conn.execute(
        "INSERT INTO http_cache VALUES (?, ?, ?)",
        ("https://cache.invalid/race-alias", race_url, now + 60),
    )
    assert planner._cache_lookup_http(conn, race_url) is True

    conn.execute("DELETE FROM http_cache WHERE expires_at > ?", (now,))
    assert planner._cache_lookup_http(conn, race_url) is False

    conn.execute(
        "INSERT INTO http_cache VALUES (?, ?, ?)",
        (horse_url, horse_url, now - 60),
    )
    assert planner._cache_lookup_http(conn, horse_url) is False
    conn.execute(
        "INSERT INTO http_cache VALUES (?, ?, ?)",
        ("https://cache.invalid/horse-alias", horse_url, now + 60),
    )
    assert planner._cache_lookup_http(conn, horse_url) is True
    conn.close()


def test_horse_parser_uses_response_identity_not_first_related_horse_link() -> None:
    html = """
    <html><head>
      <title>Requested Horse (Requested Horse)の競走成績 | 競走馬データ - netkeiba</title>
      <meta property="og:type" content="article">
      <meta property="og:title" content="Requested Horse (Requested Horse)の競走成績 | 競走馬データ - netkeiba">
      <meta property="og:url" content="https://db.netkeiba.com//horse/2021100001/">
    </head><body><h1>Requested Horse</h1>
      <a href="/horse/result/1999100001/">Sire link, not a self link</a>
      <p>enough content for parsing this representative horse document</p>
    </body></html>
    """
    fields = validator._extract_horse_fields(html, "2021100001")
    assert fields["horse_id"] == "2021100001"
    assert fields["horse_name"] == "Requested Horse"


@pytest.mark.parametrize(
    "html",
    [
        "<html><head><title>Welcome - netkeiba</title></head><body>generic content " + ("x" * 120) + "</body></html>",
        "<html><head><title>メンテナンス | netkeiba</title></head><body>メンテナンス中です "
        + ("x" * 120)
        + "</body></html>",
        "<html><body><h1>Requested Horse</h1><p>unverified horse page " + ("x" * 120) + "</p></body></html>",
    ],
)
def test_horse_detail_rejects_generic_maintenance_and_h1_only_html(html: str) -> None:
    status, fields, error = validator._parse_for_target(
        _target(
            "https://db.netkeiba.com/horse/result/2021100001/",
            url_type="horse_detail",
            race_id=None,
            horse_id="2021100001",
        ),
        html,
    )
    assert status == "parse_failed"
    assert fields == {}
    assert error


def test_horse_detail_accepts_verified_real_metadata_shape() -> None:
    html = """
    <html><head>
      <title>プラウドウィッチ (Proud Witch)の競走成績 | 競走馬データ - netkeiba</title>
      <meta property="og:type" content="article">
      <meta property="og:title" content="プラウドウィッチ (Proud Witch)の競走成績 | 競走馬データ - netkeiba">
      <meta property="og:url" content="https://db.netkeiba.com//horse/2020103445/">
    </head><body><p>representative response metadata with enough content for validation</p></body></html>
    """
    status, fields, error = validator._parse_for_target(
        _target(
            "https://db.netkeiba.com/horse/result/2020103445/",
            url_type="horse_detail",
            race_id=None,
            horse_id="2020103445",
        ),
        html,
    )
    assert status == "parse_success"
    assert error is None
    assert fields["horse_id"] == "2020103445"
    assert fields["horse_name"] == "プラウドウィッチ"


def test_horse_detail_rejects_response_identity_mismatch() -> None:
    html = """
    <html><head>
      <title>Wrong Horseの競走成績 | 競走馬データ - netkeiba</title>
      <meta property="og:type" content="article">
      <meta property="og:title" content="Wrong Horseの競走成績 | 競走馬データ - netkeiba">
      <meta property="og:url" content="https://db.netkeiba.com/horse/2021100002/">
    </head><body><p>representative response metadata with enough content for validation</p></body></html>
    """
    status, fields, error = validator._parse_for_target(
        _target(
            "https://db.netkeiba.com/horse/result/2021100001/",
            url_type="horse_detail",
            race_id=None,
            horse_id="2021100001",
        ),
        html,
    )
    assert status == "parse_failed"
    assert fields == {}
    assert error == "missing-horse-identity"


def test_pedigree_requires_verified_identity_and_real_blood_fields() -> None:
    html = """
    <html><head>
      <title>Requested Horse | 競走馬データ - netkeiba</title>
      <meta property="og:type" content="article">
      <meta property="og:title" content="Requested Horse | 競走馬データ - netkeiba">
      <meta property="og:url" content="https://db.netkeiba.com/horse/2021100001/">
    </head><body><table class="blood_table">
      <tr><td><a href="/horse/1999100001/">Sire Horse</a></td><td>line</td></tr>
      <tr><td><a href="/horse/1999100002/">Dam Horse</a></td><td><a href="/horse/1999100003/">Broodmare Sire</a></td></tr>
    </table></body></html>
    """
    status, fields, error = validator._parse_for_target(
        _target(
            "https://db.netkeiba.com/horse/ped/2021100001/",
            url_type="pedigree",
            race_id=None,
            horse_id="2021100001",
        ),
        html,
    )
    assert status == "parse_success"
    assert error is None
    assert fields["horse_id"] == "2021100001"
    assert fields["sire"] == "Sire Horse"


def test_pedigree_rejects_verified_title_without_blood_table() -> None:
    html = """
    <html><head>
      <title>Requested Horse | 競走馬データ - netkeiba</title>
      <meta property="og:type" content="article">
      <meta property="og:title" content="Requested Horse | 競走馬データ - netkeiba">
      <meta property="og:url" content="https://db.netkeiba.com/horse/2021100001/">
    </head><body><p>metadata only response with enough content but no pedigree evidence</p></body></html>
    """
    status, fields, error = validator._parse_for_target(
        _target(
            "https://db.netkeiba.com/horse/ped/2021100001/",
            url_type="pedigree",
            race_id=None,
            horse_id="2021100001",
        ),
        html,
    )
    assert status == "parse_failed"
    assert fields == {}
    assert error


class _FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, _size: int):
        for chunk in self._chunks:
            yield chunk


class _FakeResponse:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.headers = headers or {}
        self._body = body
        self.content = _FakeContent([body])

    async def read(self) -> bytes:
        return self._body


class _FakeRequest:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response

    async def __aenter__(self) -> _FakeResponse:
        return self.response

    async def __aexit__(self, *_args: Any) -> bool:
        return False


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> _FakeRequest:
        self.calls.append((url, kwargs))
        return _FakeRequest(self.response)


def _run_fetch(session: _FakeSession, **kwargs: Any):
    fetch_pipeline._LAST_REQUEST_TS.clear()
    fetch_pipeline._CIRCUIT_UNTIL.clear()
    fetch_pipeline._FAILURE_COUNTS.clear()
    return asyncio.run(
        fetch_pipeline.fetch_bytes(
            session,
            "https://db.netkeiba.com/race/202601010101/",
            use_cache=False,
            max_retries=1,
            min_interval_sec=0,
            total_timeout_sec=1,
            **kwargs,
        )
    )


def test_redirects_are_not_followed_even_when_location_is_unsafe() -> None:
    session = _FakeSession(_FakeResponse(302, b"redirect", {"Location": "http://169.254.169.254/latest/meta-data"}))
    result = _run_fetch(session, allow_redirects=False, max_body_bytes=1024)

    assert result.status == 302
    assert len(session.calls) == 1
    assert session.calls[0][1] == {"allow_redirects": False}


def test_response_body_is_rejected_as_soon_as_bound_is_exceeded() -> None:
    session = _FakeSession(_FakeResponse(200, b"12345"))
    result = _run_fetch(session, allow_redirects=False, max_body_bytes=4)
    assert result.status == 0
    assert result.source == "network-rejected"
    assert result.body == b""
    assert result.error == "response-body-too-large:4"


def test_retry_after_is_capped() -> None:
    assert fetch_pipeline._parse_retry_after({"Retry-After": "999999"}, 2.0) == 2.0


def test_live_fetch_passes_all_network_safety_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _ClientTimeout:
        def __init__(self, *, total: float) -> None:
            captured["client_timeout"] = total

    class _ClientSession:
        def __init__(self, *, timeout: Any) -> None:
            captured["session_timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: Any) -> bool:
            return False

    async def _fake_fetch_text(_session: Any, url: str, **kwargs: Any):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return SimpleNamespace(status=200, source="network", attempts=1), "ok"

    monkeypatch.setitem(
        sys.modules,
        "aiohttp",
        SimpleNamespace(ClientTimeout=_ClientTimeout, ClientSession=_ClientSession),
    )
    monkeypatch.setattr(validator, "fetch_text", _fake_fetch_text)
    monkeypatch.setattr(validator, "get_fetch_metrics", lambda reset=False: {})

    result = asyncio.run(
        validator._fetch_live(
            _target("https://db.netkeiba.com/race/202601010101/", horse_id=None),
            fixture_map=None,
            metric_before={},
            total_timeout_sec=7.0,
        )
    )

    assert result[0] == 200
    assert captured["client_timeout"] == 7.0
    assert captured["kwargs"]["allow_redirects"] is False
    assert captured["kwargs"]["force_refresh"] is False
    assert captured["kwargs"]["use_cache"] is False
    assert captured["kwargs"]["max_body_bytes"] == validator.MAX_BODY_BYTES
    assert captured["kwargs"]["max_retries"] == 1
    assert captured["kwargs"]["retry_base_sec"] == 0
    assert captured["kwargs"]["retry_jitter_sec"] == 0
    assert captured["kwargs"]["max_retry_after_sec"] == validator.MAX_RETRY_AFTER_SEC
    assert captured["kwargs"]["total_timeout_sec"] == 7.0


def test_three_retryable_urls_never_exceed_three_outbound_attempts() -> None:
    session = _FakeSession(_FakeResponse(503, b"retryable service unavailable"))
    fetch_pipeline._LAST_REQUEST_TS.clear()
    fetch_pipeline._CIRCUIT_UNTIL.clear()
    fetch_pipeline._FAILURE_COUNTS.clear()

    async def _run_all() -> None:
        for suffix in ("101", "102", "103"):
            await fetch_pipeline.fetch_bytes(
                session,
                f"https://db.netkeiba.com/race/202601010{suffix}/",
                use_cache=False,
                max_retries=validator.MAX_RETRIES,
                min_interval_sec=0,
                retry_base_sec=0,
                retry_jitter_sec=0,
                max_retry_after_sec=0,
                circuit_threshold=10,
                total_timeout_sec=5,
                allow_redirects=False,
                max_body_bytes=1024,
            )

    asyncio.run(_run_all())
    assert len(session.calls) == 3


def test_validation_enforces_total_runtime_and_reports_safety_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path = tmp_path / "plan.json"
    cache_path = tmp_path / "cache.db"
    plan_path.write_text(
        json.dumps(
            {
                "unique_url_count": 1,
                "sample_urls": {
                    "result_page": [
                        {
                            "url": "https://db.netkeiba.com/race/202601010101/",
                            "url_type": "result_page",
                            "race_id": "202601010101",
                            "horse_id": "2021100001",
                            "reason": "true-missing",
                            "column": "finish_position",
                            "priority": "P0",
                            "source": "plan",
                            "recommended_next_action": "targeted refetch live validation",
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    _create_cache(cache_path)

    async def _slow_fetch(*_args: Any, **_kwargs: Any):
        await asyncio.sleep(1)
        raise AssertionError("runtime limit did not cancel fetch")

    monkeypatch.setattr(validator, "TOTAL_TIMEOUT_SEC", 0.03)
    monkeypatch.setattr(validator, "_fetch_live", _slow_fetch)
    args = argparse.Namespace(
        input_refetch_plan=str(plan_path),
        target="all",
        max_urls=1,
        url_type="all",
        output=str(tmp_path / "out.json"),
        cache_db=str(cache_path),
        fixture_json="",
    )

    started = time.perf_counter()
    payload = asyncio.run(validator._run_validation(args))
    elapsed = time.perf_counter() - started

    assert elapsed < 0.3
    assert payload["attempted_url_count"] == 1
    assert payload["http_error_count"] == 1
    assert payload["rate_limit_policy"]["total_timeout_sec"] == 0.03
    assert payload["rate_limit_policy"]["per_request_timeout_sec"] == validator.PER_REQUEST_TIMEOUT_SEC
    assert payload["rate_limit_policy"]["max_body_bytes"] == validator.MAX_BODY_BYTES
    assert payload["rate_limit_policy"]["max_retry_after_sec"] == validator.MAX_RETRY_AFTER_SEC
    flags = payload["safety_flags"]
    assert flags["redirects_disabled"] is True
    assert flags["bounded_response_body"] is True
    assert flags["bounded_total_runtime"] is True
    assert flags["no_force_refresh_execute"] is True
