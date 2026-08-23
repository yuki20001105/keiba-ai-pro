from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "collect_limited_production_observations.py"
SPEC = importlib.util.spec_from_file_location("limited_production_collector", SCRIPT)
assert SPEC and SPEC.loader
collector = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = collector
SPEC.loader.exec_module(collector)
JST = ZoneInfo("Asia/Tokyo")


class Response:
    def __init__(self, status: int, payload: bytes) -> None:
        self.status = status
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.payload


def json_response(payload: object, status: int = 200) -> Response:
    return Response(status, json.dumps(payload).encode())


def fake_opener(request, timeout=0):  # noqa: ARG001
    url = request.full_url
    if url.endswith("/health"):
        return json_response(
            {
                "status": "ok",
                "app_env": "production",
                "model_runtime_status": "observation",
                "observation_enabled": True,
                "observation_release_mode": "limited-observation",
                "automated_betting_enabled": False,
            }
        )
    if "phase3n_prediction_observations" in url:
        return json_response([])
    if "race_list_sub" in url:
        return Response(
            200,
            (
                '<li class="RaceList_DataItem"><a href="?race_id=202601020211">'
                '<span class="RaceList_Itemtime">15:45 </span>'
                '<span class="RaceList_ItemLong Turf">芝1200m</span>'
                "</a></li>"
            ).encode(),
        )
    if "shutuba.html" in url:
        return Response(
            200,
            '<div class="RaceData01">15:45発走 / <span>芝1200m</span></div>'.encode(),
        )
    if "api_get_jra_odds" in url:
        return json_response(
            {
                "status": "middle",
                "data": {
                    "official_datetime": "2026-08-23T15:30:00+09:00",
                    "odds": {"1": {"01": "2.0", "02": "3.0"}},
                },
            }
        )
    if "/auth/v1/token" in url:
        return json_response({"access_token": "ephemeral-user-token"})
    if url.endswith("/api/analyze_race"):
        return json_response({"success": True, "predictions": [{}, {}]})
    raise AssertionError(url)


def base_env() -> dict[str, str]:
    return {
        "PRODUCTION_BACKEND_HEALTH_URL": "https://api.example.test/health",
        "PRODUCTION_SUPABASE_URL": "https://abcdefghijklmnopqrst.supabase.co",
        "PRODUCTION_SUPABASE_SERVICE_KEY": "server-secret",
    }


def test_dry_run_finds_only_future_middle_race() -> None:
    report = collector.run(
        base_env(),
        now=datetime(2026, 8, 23, 15, 30, tzinfo=JST),
        execute=False,
        opener=fake_opener,
    )
    assert report["success"] is True
    assert report["candidate_count"] == 1
    assert report["executed_count"] == 0
    assert report["automatic_betting_enabled"] is False


def test_execute_requires_explicit_enable_and_credentials() -> None:
    with pytest.raises(RuntimeError, match="missing-config"):
        collector.run(
            base_env(),
            now=datetime(2026, 8, 23, 15, 30, tzinfo=JST),
            execute=True,
            opener=fake_opener,
        )


def test_execute_records_sanitized_summary_only() -> None:
    env = {
        **base_env(),
        "PRODUCTION_E2E_EMAIL": "observer@example.test",
        "PRODUCTION_E2E_PASSWORD": "not-recorded",
        "LIMITED_PRODUCTION_COLLECTION_ENABLED": "true",
    }
    report = collector.run(
        env,
        now=datetime(2026, 8, 23, 15, 30, tzinfo=JST),
        execute=True,
        opener=fake_opener,
    )
    assert report["executed_count"] == 1
    assert report["executed"] == [
        {"race_id": "202601020211", "status": 200, "prediction_count": 2}
    ]
    serialized = json.dumps(report)
    assert "ephemeral-user-token" not in serialized
    assert env["PRODUCTION_E2E_PASSWORD"] not in serialized


def test_http_error_body_is_handled_without_leaking_request_headers() -> None:
    def denied(request, timeout=0):  # noqa: ARG001
        raise HTTPError(request.full_url, 401, "denied", {}, None)

    status, payload = collector._request("https://example.test", opener=denied)
    assert status == 401
    assert payload == b""
