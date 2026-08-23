#!/usr/bin/env python3
"""Collect prospective, pre-result middle-odds Production observations.

The collector is intentionally fail-closed: it only considers races listed for
the current JST date, requires a future advertised start time and netkeiba's
``middle`` odds state, skips races already recorded in Production, verifies the
limited-observation health boundary, and never enables or performs betting.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")
RACE_ID_RE = re.compile(r"race_id=(\d{12})")
RACE_ITEM_RE = re.compile(
    r'<li[^>]+class="[^"]*RaceList_DataItem[^"]*"[^>]*>(.*?)</li>', re.DOTALL
)
RACE_DATA_RE = re.compile(
    r'<div[^>]+class="[^"]*RaceData01[^"]*"[^>]*>(.*?)</div>', re.DOTALL
)
START_RE = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")
DISTANCE_RE = re.compile(r"(?:芝|ダ|障)\s*(\d{3,4})m")
TRUE_VALUES = {"1", "true", "yes", "on"}
USER_AGENT = "keiba-ai-pro-limited-observation-collector/1.0"


@dataclass(frozen=True)
class Candidate:
    race_id: str
    start_at: datetime
    distance: int
    odds_count: int
    odds_observed_at: str | None


def _request(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
    opener: Callable[..., Any] = urlopen,
) -> tuple[int, bytes]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers=headers or {}, method=method)
    try:
        with opener(request, timeout=timeout) as response:
            return int(response.status), response.read()
    except HTTPError as exc:
        return int(exc.code), exc.read()
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(type(exc).__name__) from exc


def _json_request(*args: Any, **kwargs: Any) -> tuple[int, Any]:
    status, raw = _request(*args, **kwargs)
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("response-json-invalid") from exc


def _server_headers(service_key: str) -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
    }


def _discover_races(
    race_date: str,
    *,
    opener: Callable[..., Any],
) -> list[tuple[str, datetime, int]]:
    status, raw = _request(
        f"https://race.netkeiba.com/top/race_list_sub.html?kaisai_date={race_date}",
        headers={"User-Agent": USER_AGENT},
        opener=opener,
    )
    if status != 200:
        raise RuntimeError("race-list-unavailable")
    page = raw.decode("utf-8", "replace")
    races: dict[str, tuple[str, datetime, int]] = {}
    for match in RACE_ITEM_RE.finditer(page):
        item = match.group(1)
        race_match = RACE_ID_RE.search(item)
        time_match = re.search(
            r'class="[^"]*RaceList_Itemtime[^"]*"[^>]*>\s*(\d{1,2}):(\d{2})',
            item,
        )
        distance_match = re.search(
            r'class="[^"]*RaceList_ItemLong[^"]*"[^>]*>\s*(?:芝|ダ|障)\s*(\d{3,4})m',
            item,
        )
        if race_match is None or time_match is None or distance_match is None:
            continue
        hour, minute = int(time_match.group(1)), int(time_match.group(2))
        if hour > 23 or minute > 59:
            continue
        start_at = datetime.strptime(race_date, "%Y%m%d").replace(
            hour=hour, minute=minute, tzinfo=JST
        )
        race_id = race_match.group(1)
        races[race_id] = (race_id, start_at, int(distance_match.group(1)))
    return [races[key] for key in sorted(races)]


def _race_start_and_distance(
    race_id: str,
    race_date: str,
    *,
    opener: Callable[..., Any],
) -> tuple[datetime, int] | None:
    status, raw = _request(
        f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}",
        headers={"User-Agent": USER_AGENT},
        opener=opener,
    )
    if status != 200:
        return None
    page = raw.decode("utf-8", "replace")
    block = RACE_DATA_RE.search(page)
    if block is None:
        return None
    text = html.unescape(re.sub(r"<[^>]+>", " ", block.group(1)))
    start_match = START_RE.search(text)
    distance_match = DISTANCE_RE.search(text)
    if start_match is None or distance_match is None:
        return None
    hour, minute = int(start_match.group(1)), int(start_match.group(2))
    if hour > 23 or minute > 59:
        return None
    start_at = datetime.strptime(race_date, "%Y%m%d").replace(
        hour=hour, minute=minute, tzinfo=JST
    )
    return start_at, int(distance_match.group(1))


def _middle_odds(
    race_id: str,
    *,
    opener: Callable[..., Any],
) -> tuple[int, str | None] | None:
    status, payload = _json_request(
        "https://race.netkeiba.com/api/api_get_jra_odds.html"
        f"?race_id={race_id}&type=1&action=init",
        headers={"User-Agent": USER_AGENT},
        opener=opener,
    )
    if status != 200 or not isinstance(payload, dict) or payload.get("status") != "middle":
        return None
    data = payload.get("data")
    odds = data.get("odds") if isinstance(data, dict) else None
    win_odds = odds.get("1") if isinstance(odds, dict) else None
    if not isinstance(win_odds, dict) or not win_odds:
        return None
    observed_at = data.get("official_datetime") if isinstance(data, dict) else None
    return len(win_odds), str(observed_at) if observed_at else None


def _observed_race_ids(
    supabase_url: str,
    service_key: str,
    race_date: str,
    *,
    opener: Callable[..., Any],
) -> set[str]:
    query = (
        "select=race_id&source_environment=eq.production"
        f"&race_date=eq.{quote(race_date)}"
    )
    status, payload = _json_request(
        f"{supabase_url.rstrip('/')}/rest/v1/phase3n_prediction_observations?{query}",
        headers=_server_headers(service_key),
        opener=opener,
    )
    if status != 200 or not isinstance(payload, list):
        raise RuntimeError("production-observation-query-failed")
    return {str(row.get("race_id")) for row in payload if isinstance(row, dict)}


def _health_check(backend_health_url: str, *, opener: Callable[..., Any]) -> None:
    status, payload = _json_request(backend_health_url, opener=opener)
    expected = {
        "status": "ok",
        "app_env": "production",
        "model_runtime_status": "observation",
        "observation_enabled": True,
        "observation_release_mode": "limited-observation",
        "automated_betting_enabled": False,
    }
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError("production-health-invalid")
    if any(payload.get(key) != value for key, value in expected.items()):
        raise RuntimeError("production-safety-boundary-invalid")


def _access_token(
    supabase_url: str,
    service_key: str,
    email: str,
    password: str,
    *,
    opener: Callable[..., Any],
) -> str:
    status, payload = _json_request(
        f"{supabase_url.rstrip('/')}/auth/v1/token?grant_type=password",
        method="POST",
        payload={"email": email, "password": password},
        headers={
            "User-Agent": USER_AGENT,
            "apikey": service_key,
            "Content-Type": "application/json",
        },
        opener=opener,
    )
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if status != 200 or not isinstance(token, str) or not token:
        raise RuntimeError("production-observer-auth-failed")
    return token


def _analyze(
    backend_health_url: str,
    token: str,
    race_id: str,
    *,
    opener: Callable[..., Any],
) -> tuple[int, int]:
    base = backend_health_url.removesuffix("/health").rstrip("/")
    status, payload = _json_request(
        f"{base}/api/analyze_race",
        method="POST",
        payload={
            "race_id": race_id,
            "bankroll": 100000,
            "risk_mode": "balanced",
            "use_kelly": True,
            "dynamic_unit": True,
            "min_ev": 1.2,
            "ultimate_mode": True,
        },
        headers={
            "User-Agent": USER_AGENT,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        timeout=180.0,
        opener=opener,
    )
    predictions = payload.get("predictions") if isinstance(payload, dict) else None
    if (
        status != 200
        or not isinstance(payload, dict)
        or payload.get("success") is not True
        or not isinstance(predictions, list)
    ):
        raise RuntimeError(f"production-analysis-failed-{status}")
    return status, len(predictions)


def run(
    env: dict[str, str],
    *,
    now: datetime | None = None,
    execute: bool,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    required = (
        "PRODUCTION_BACKEND_HEALTH_URL",
        "PRODUCTION_SUPABASE_URL",
        "PRODUCTION_SUPABASE_SERVICE_KEY",
    )
    if execute:
        required += ("PRODUCTION_E2E_EMAIL", "PRODUCTION_E2E_PASSWORD")
    missing = sorted(name for name in required if not env.get(name, "").strip())
    if missing:
        raise RuntimeError("missing-config:" + ",".join(missing))
    if execute and env.get("LIMITED_PRODUCTION_COLLECTION_ENABLED", "").strip().lower() not in TRUE_VALUES:
        raise RuntimeError("limited-production-collection-disabled")

    observed_at = (now or datetime.now(timezone.utc)).astimezone(JST)
    race_date = observed_at.strftime("%Y%m%d")
    iso_date = observed_at.strftime("%Y-%m-%d")
    supabase_url = env["PRODUCTION_SUPABASE_URL"].strip()
    service_key = env["PRODUCTION_SUPABASE_SERVICE_KEY"].strip()
    backend_health_url = env["PRODUCTION_BACKEND_HEALTH_URL"].strip()
    _health_check(backend_health_url, opener=opener)
    already_observed = _observed_race_ids(
        supabase_url, service_key, iso_date, opener=opener
    )

    candidates: list[Candidate] = []
    rejected = {"already_observed": 0, "not_middle": 0, "not_future": 0, "metadata_invalid": 0}
    for race_id, start_at, distance in _discover_races(race_date, opener=opener):
        if race_id in already_observed:
            rejected["already_observed"] += 1
            continue
        if start_at <= observed_at:
            rejected["not_future"] += 1
            continue
        odds = _middle_odds(race_id, opener=opener)
        if odds is None:
            rejected["not_middle"] += 1
            continue
        odds_count, odds_observed_at = odds
        candidates.append(Candidate(race_id, start_at, distance, odds_count, odds_observed_at))

    max_races = max(1, min(int(env.get("LIMITED_PRODUCTION_MAX_RACES_PER_RUN", "6")), 12))
    candidates.sort(key=lambda item: (item.start_at, item.race_id))
    selected = candidates[:max_races]
    executed: list[dict[str, Any]] = []
    if execute and selected:
        token = _access_token(
            supabase_url,
            service_key,
            env["PRODUCTION_E2E_EMAIL"],
            env["PRODUCTION_E2E_PASSWORD"],
            opener=opener,
        )
        try:
            for candidate in selected:
                current = datetime.now(timezone.utc).astimezone(JST) if now is None else observed_at
                if current >= candidate.start_at:
                    continue
                status, prediction_count = _analyze(
                    backend_health_url, token, candidate.race_id, opener=opener
                )
                executed.append(
                    {
                        "race_id": candidate.race_id,
                        "status": status,
                        "prediction_count": prediction_count,
                    }
                )
        finally:
            token = ""

    return {
        "schema": "limited-production-middle-observation-collection",
        "schema_version": 1,
        "observed_at": observed_at.isoformat(),
        "race_date": iso_date,
        "execute": execute,
        "success": True,
        "automatic_betting_enabled": False,
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "executed_count": len(executed),
        "executed": executed,
        "rejected_counts": rejected,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/limited_production_middle_collection.json"),
    )
    args = parser.parse_args()
    try:
        report = run(dict(os.environ), execute=args.execute)
    except RuntimeError as exc:
        report = {
            "schema": "limited-production-middle-observation-collection",
            "schema_version": 1,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "execute": args.execute,
            "success": False,
            "failure_code": str(exc),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "success": report["success"],
                "candidate_count": report.get("candidate_count", 0),
                "executed_count": report.get("executed_count", 0),
                "failure_code": report.get("failure_code"),
            },
            sort_keys=True,
        )
    )
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
