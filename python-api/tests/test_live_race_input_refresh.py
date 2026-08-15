from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[2]
PYTHON_API = ROOT / "python-api"
if str(PYTHON_API) not in sys.path:
    sys.path.insert(0, str(PYTHON_API))

from scraping import fetch_pipeline, race  # type: ignore  # noqa: E402
from scraping.odds import parse_tansho_odds_payload  # type: ignore  # noqa: E402
from services.race_snapshot import (  # type: ignore  # noqa: E402
    is_trustworthy_race_date,
    save_valid_race_snapshot,
    snapshot_validation_errors,
    stored_rows_need_refresh,
)


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200, payload: Any = None) -> None:
        self.status = status
        self._body = body
        self._payload = payload
        self.headers: dict[str, str] = {}

    async def read(self) -> bytes:
        return self._body

    async def json(self, content_type: Any = None) -> Any:
        del content_type
        if self._payload is None:
            return json.loads(self._body.decode("utf-8"))
        return self._payload


class _FakeContext:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response

    async def __aenter__(self) -> _FakeResponse:
        return self.response

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def get(self, url: str, **kwargs: Any) -> _FakeContext:
        del url, kwargs
        self.calls += 1
        return _FakeContext(self.responses.pop(0))


def test_resume_metadata_never_returns_an_empty_body_after_cache_expiry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cache_db = tmp_path / "fetch-cache.db"
    monkeypatch.setattr(fetch_pipeline, "_CACHE_DB_PATH", cache_db)
    session = _FakeSession([
        _FakeResponse(b'<meta charset="utf-8">\xe5\x88\x9d\xe5\x9b\x9e'),
        _FakeResponse(b'<meta charset="utf-8">\xe6\x9c\x80\xe6\x96\xb0'),
    ])
    url = "https://example.test/live"

    first, first_text = asyncio.run(fetch_pipeline.fetch_text(
        session,
        url,
        cache_ttl_sec=60,
        resume_key="live:race",
        min_interval_sec=0,
    ))
    assert first.status == 200
    assert "初回" in first_text

    with sqlite3.connect(cache_db) as connection:
        connection.execute("UPDATE http_cache SET expires_at = 0")
        connection.commit()

    second, second_text = asyncio.run(fetch_pipeline.fetch_text(
        session,
        url,
        cache_ttl_sec=60,
        resume_key="live:race",
        min_interval_sec=0,
    ))
    assert second.status == 200
    assert second.source != "resume"
    assert "最新" in second_text
    assert session.calls == 2


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('<meta charset="utf-8"><div>芝2000m</div>'.encode(), "芝2000m"),
        ('<meta charset="euc-jp"><div>芝2000m</div>'.encode("euc-jp"), "芝2000m"),
    ],
)
def test_fetch_text_detects_utf8_and_euc_jp(body: bytes, expected: str) -> None:
    assert expected in fetch_pipeline._decode_text_body(body)


def test_shutuba_parser_keeps_distance_and_uses_actual_dynamic_odds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = """
    <html><meta charset="utf-8"><body>
      <h1 class="RaceName">検証レース</h1>
      <div class="RaceData01">18:20発走 / 芝2000m (左)</div>
      <div class="RaceData02">2回 新潟 8日目</div>
      <title>検証レース | 2026年8月16日</title>
      <table class="Shutuba_Table"><tr class="HorseList">
        <td>1</td><td>1</td><td></td>
        <td class="HorseInfo"><span class="HorseName"><a href="/horse/2023100001">テスト馬</a></span></td>
        <td>牡3</td><td>57.0</td>
        <td><a href="/jockey/result/recent/01234/">騎手</a></td>
        <td><a href="/trainer/result/recent/05678/">調教師</a></td>
        <td>480(+2)</td><td><span id="odds-1_01">---.-</span></td><td>**</td>
      </tr></table>
    </body></html>
    """
    captured: dict[str, Any] = {}

    async def _fake_fetch_text(session: Any, url: str, **kwargs: Any):
        del session, url
        captured.update(kwargs)
        result = fetch_pipeline.FetchResult("u", "u", 200, html.encode(), "network", 1)
        return result, html

    async def _fake_odds(session: Any, race_id: str):
        del session, race_id
        return {1: 4.2}, {1: 3}, "middle"

    monkeypatch.setattr(race, "fetch_text", _fake_fetch_text)
    monkeypatch.setattr(race, "fetch_tansho_odds_api", _fake_odds)
    snapshot = asyncio.run(race._scrape_shutuba_fallback(
        object(),
        "202604020812",
        force_refresh=True,
    ))

    assert snapshot is not None
    assert snapshot["race_info"]["distance"] == 2000
    assert snapshot["race_info"]["date"] == "20260816"
    assert snapshot["horses"][0]["odds"] == 4.2
    assert snapshot["horses"][0]["popularity"] == 3
    assert captured["force_refresh"] is True


def test_forecast_odds_are_not_accepted_as_point_in_time_market_odds() -> None:
    payload = {
        "status": "yoso",
        "data": {"odds": {"1": {"01": ["2.5", "", "1"]}}},
    }
    odds, popularity, status = parse_tansho_odds_payload(payload)
    assert status == "yoso"
    assert odds == {}
    assert popularity == {}

    allowed, ranks, _ = parse_tansho_odds_payload(payload, allow_predicted=True)
    assert allowed == {1: 2.5}
    assert ranks == {1: 1}


def _snapshot(distance: int = 2000) -> dict[str, Any]:
    race_id = "202604020812"
    return {
        "race_info": {
            "race_id": race_id,
            "date": "20260816",
            "distance": distance,
            "venue": "新潟",
            "num_horses": 1,
        },
        "horses": [{
            "race_id": race_id,
            "horse_number": 1,
            "horse_name": "テスト馬",
            "distance": distance,
            "odds": 4.2,
        }],
    }


def test_legacy_race_id_slice_is_not_a_calendar_date() -> None:
    assert is_trustworthy_race_date("202604020812", "20260402") is False
    assert is_trustworthy_race_date("202604020812", "20260816") is True


def test_snapshot_rejects_legacy_race_id_slice_even_with_valid_distance() -> None:
    snapshot = _snapshot()
    snapshot["race_info"]["date"] = "20260402"
    assert "race date is missing or derived from race_id" in snapshot_validation_errors(
        snapshot,
        snapshot["race_info"]["race_id"],
    )


def test_incomplete_stored_snapshot_requires_refresh() -> None:
    rows = [(json.dumps({"horse_number": 1, "odds": None}),)]
    assert stored_rows_need_refresh(
        "202604020812",
        {"date": "20260402", "distance": 0},
        rows,
    ) is True


def test_invalid_snapshot_cannot_replace_an_existing_atomic_snapshot(tmp_path: Path) -> None:
    db_path = tmp_path / "race.db"
    valid = _snapshot()
    save_valid_race_snapshot(valid, db_path, valid["race_info"]["race_id"])

    invalid = _snapshot(distance=0)
    assert snapshot_validation_errors(invalid, invalid["race_info"]["race_id"])
    with pytest.raises(ValueError, match="distance is missing"):
        save_valid_race_snapshot(invalid, db_path, invalid["race_info"]["race_id"])

    with sqlite3.connect(db_path) as connection:
        stored = json.loads(connection.execute(
            "SELECT data FROM races_ultimate WHERE race_id = ?",
            (valid["race_info"]["race_id"],),
        ).fetchone()[0])
    assert stored["distance"] == 2000
