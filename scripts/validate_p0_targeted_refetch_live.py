#!/usr/bin/env python3
"""Small live validation for P0 targeted refetch candidates.

This script performs limited live fetch validation (max 10 URLs) and parse checks
without any DB upsert/repair execution. It only writes a JSON report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

ROOT_DIR = Path(__file__).resolve().parent.parent
PYTHON_API_DIR = ROOT_DIR / "python-api"
if str(PYTHON_API_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_API_DIR))

from scraping.fetch_pipeline import fetch_text, get_fetch_metrics  # type: ignore

try:
    from scraping.constants import VENUE_MAP, is_cloudflare_block  # type: ignore
except Exception:  # pragma: no cover
    VENUE_MAP = {}

    def is_cloudflare_block(_body: bytes) -> bool:
        return False


DEFAULT_REFETCH_PLAN_INPUT = ROOT_DIR / "reports" / "p0_targeted_refetch_plan.json"
DEFAULT_OUTPUT = ROOT_DIR / "reports" / "p0_targeted_refetch_live_validation.json"
DEFAULT_CACHE_DB = ROOT_DIR / "keiba" / "data" / "fetch_cache.db"

URL_TYPE_MAP = {
    "race-result": "result_page",
    "race-detail": "race_detail",
    "horse-detail": "horse_detail",
    "pedigree": "pedigree",
}

TARGET_COLUMNS: dict[str, set[str]] = {
    "all": {
        "race_id",
        "race_date",
        "venue",
        "race_number",
        "horse_id",
        "horse_name",
        "frame_number",
        "horse_number",
        "finish_position",
        "result_time",
        "margin",
        "odds",
        "popularity",
        "sire",
        "dam",
        "broodmare_sire",
        "(check)",
    },
    "race": {"race_id", "race_date", "venue", "race_number", "(check)"},
    "horse": {"horse_id", "horse_name", "frame_number", "horse_number", "(check)"},
    "result": {"finish_position", "result_time", "margin", "(check)"},
    "pedigree": {"sire", "dam", "broodmare_sire", "(check)"},
    "odds": {"odds", "popularity", "(check)"},
}


@dataclass
class ValidationTarget:
    url: str
    url_type: str
    race_id: str | None
    horse_id: str | None
    reason: str
    column: str
    priority: str
    source: str
    recommended_next_action: str


@dataclass
class FetchObserved:
    url: str
    url_type: str
    race_id: str | None
    horse_id: str | None
    reason: str
    column: str
    priority: str
    source: str
    recommended_next_action: str
    http_status: int
    parse_status: str
    action: str
    elapsed_seconds: float
    retry_count: int
    cache_hit: bool
    backoff_observed: bool
    circuit_open_observed: bool
    missing_fields_before: list[str]
    fields_found_after: list[str]
    would_fix_columns: list[str]


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"error: {label} not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"error: failed to parse {label}: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"error: invalid {label} JSON object: {path}")
    return data


def _open_ro_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"error: database not found: {path}")
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path or "/"
    query = parts.query or ""
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}{('?' + query) if query else ''}"


def _target_allowed(column: str, target: str) -> bool:
    return column in TARGET_COLUMNS.get(target, TARGET_COLUMNS["all"])


def _cache_has_url(conn: sqlite3.Connection, url: str) -> bool:
    normalized = _normalize_url(url)
    row = conn.execute("SELECT 1 FROM http_cache WHERE normalized_url = ?", (normalized,)).fetchone()
    if row:
        return True

    path = urlsplit(url).path or ""
    if "/race/" in path:
        race_id = path.split("/race/", 1)[1].strip("/")
        if race_id:
            row = conn.execute(
                "SELECT 1 FROM http_cache WHERE normalized_url LIKE ? OR final_url LIKE ? LIMIT 1",
                (f"%/race/{race_id}/%", f"%/race/{race_id}/%"),
            ).fetchone()
            return bool(row)
    if "/horse/result/" in path:
        horse_id = path.split("/horse/result/", 1)[1].strip("/")
        if horse_id:
            row = conn.execute(
                "SELECT 1 FROM http_cache WHERE normalized_url LIKE ? OR final_url LIKE ? LIMIT 1",
                (f"%/horse/result/{horse_id}/%", f"%/horse/result/{horse_id}/%"),
            ).fetchone()
            return bool(row)
    if "/horse/ped/" in path:
        horse_id = path.split("/horse/ped/", 1)[1].strip("/")
        if horse_id:
            row = conn.execute(
                "SELECT 1 FROM http_cache WHERE normalized_url LIKE ? OR final_url LIKE ? LIMIT 1",
                (f"%/horse/ped/{horse_id}/%", f"%/horse/ped/{horse_id}/%"),
            ).fetchone()
            return bool(row)
    return False


def _canonical_url_type(raw: str) -> str:
    if raw in {"result_page", "race-result"}:
        return "result_page"
    if raw in {"race_detail", "race-detail"}:
        return "race_detail"
    if raw in {"horse_detail", "horse-detail"}:
        return "horse_detail"
    if raw in {"pedigree", "horse-pedigree"}:
        return "pedigree"
    return raw


def _read_targets_from_plan(plan: dict[str, Any]) -> list[ValidationTarget]:
    out: list[ValidationTarget] = []

    sample_urls = plan.get("sample_urls") if isinstance(plan.get("sample_urls"), dict) else {}
    for _slot, rows in sample_urls.items():
        if not isinstance(rows, list):
            continue
        for item in rows:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            out.append(
                ValidationTarget(
                    url=url,
                    url_type=_canonical_url_type(str(item.get("url_type") or "")),
                    race_id=str(item.get("race_id") or "").strip() or None,
                    horse_id=str(item.get("horse_id") or "").strip() or None,
                    reason=str(item.get("reason") or ""),
                    column=str(item.get("column") or ""),
                    priority=str(item.get("priority") or "P1"),
                    source=str(item.get("source") or "plan"),
                    recommended_next_action=str(item.get("recommended_next_action") or "targeted refetch live validation"),
                )
            )

    fallback_rows = plan.get("url_candidates") if isinstance(plan.get("url_candidates"), list) else []
    for item in fallback_rows:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        out.append(
            ValidationTarget(
                url=url,
                url_type=_canonical_url_type(str(item.get("url_type") or "")),
                race_id=str(item.get("race_id") or "").strip() or None,
                horse_id=str(item.get("horse_id") or "").strip() or None,
                reason=str(item.get("reason") or ""),
                column=str(item.get("column") or ""),
                priority=str(item.get("priority") or "P1"),
                source=str(item.get("source") or "plan"),
                recommended_next_action=str(item.get("recommended_next_action") or "targeted refetch live validation"),
            )
        )

    dedup: dict[str, ValidationTarget] = {}
    for target in out:
        if target.url not in dedup:
            dedup[target.url] = target
    return list(dedup.values())


def _expected_type_for_url_type(url_type: str) -> str:
    if url_type in {"result_page", "race_detail"}:
        return "race-detail"
    if url_type in {"horse_detail", "pedigree"}:
        return "horse-detail"
    return "unknown"


def _detect_page_type(url: str, html: str) -> str:
    lower_html = html.lower()
    if re.search(r"/race/\d{12}/", url):
        return "race-detail"
    if "/horse/result/" in url or "/horse/ped/" in url:
        return "horse-detail"
    if "race_table_01" in lower_html:
        return "race-detail"
    if "blood_table" in lower_html:
        return "horse-detail"
    return "unknown"


def _is_error_page(html: str) -> bool:
    lowered = html.lower()
    if "access denied" in lowered or "forbidden" in lowered or "error" in lowered[:500]:
        return True
    try:
        if is_cloudflare_block(html.encode("utf-8", errors="ignore")):
            return True
    except Exception:
        return False
    return False


def _extract_race_fields(html: str, race_id: str | None, horse_id: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="race_table_01")
    if table is None:
        return out

    header_rows = table.find_all("tr")
    if not header_rows:
        return out
    headers = [c.get_text(strip=True) for c in header_rows[0].find_all(["th", "td"])]

    def idx(names: list[str], default: int = -1) -> int:
        for name in names:
            for i, h in enumerate(headers):
                if name in h:
                    return i
        return default

    idx_finish = idx(["着順"], 0)
    idx_bracket = idx(["枠番"], 1)
    idx_horse_num = idx(["馬番"], 2)
    idx_horse = idx(["馬名"], 3)
    idx_time = idx(["タイム"], 7)
    idx_margin = idx(["着差"], 8)

    entries: list[dict[str, Any]] = []
    for row in table.find_all("tr")[1:]:
        cols = row.find_all("td")
        if len(cols) < 4:
            continue

        def text(i: int) -> str:
            return cols[i].get_text(strip=True) if i < len(cols) else ""

        def href(i: int) -> str:
            a = cols[i].find("a") if i < len(cols) else None
            raw = str(a.get("href") or "") if a else ""
            return f"https://db.netkeiba.com{raw}" if raw.startswith("/") else raw

        horse_url = href(idx_horse)
        parsed_hid = ""
        if horse_url:
            m = re.search(r"/horse/(?:result/)?([A-Za-z0-9]+)(?:/|$)", horse_url)
            parsed_hid = m.group(1) if m else ""

        entries.append(
            {
                "race_id": race_id,
                "horse_id": parsed_hid,
                "horse_name": text(idx_horse) or None,
                "frame_number": text(idx_bracket) or None,
                "horse_number": text(idx_horse_num) or None,
                "finish_position": text(idx_finish) or None,
                "result_time": text(idx_time) or None,
                "margin": text(idx_margin) or None,
            }
        )

    target_entry = None
    if horse_id:
        for entry in entries:
            if str(entry.get("horse_id") or "") == horse_id:
                target_entry = entry
                break
    if target_entry is None and entries:
        target_entry = entries[0]
    if isinstance(target_entry, dict):
        out.update(target_entry)

    smalltxt = soup.find("p", class_="smalltxt")
    info_text = smalltxt.get_text(" ", strip=True) if smalltxt else html[:2000]
    date_match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", info_text)
    if date_match:
        out["race_date"] = f"{date_match.group(1)}{int(date_match.group(2)):02d}{int(date_match.group(3)):02d}"
    if not out.get("race_date"):
        date_match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", html)
        if date_match:
            out["race_date"] = f"{date_match.group(1)}{int(date_match.group(2)):02d}{int(date_match.group(3)):02d}"

    venue = None
    if race_id and len(race_id) >= 6:
        venue = VENUE_MAP.get(race_id[4:6], None)
    if not venue:
        venue_match = re.search(r"([\u4e00-\u9fff]{2,3})\s*\d{4}年", info_text)
        if venue_match:
            venue = venue_match.group(1)
    out["venue"] = venue
    out["entries"] = entries
    return out


def _extract_horse_fields(html: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    soup = BeautifulSoup(html, "html.parser")
    title = soup.find("h1")
    if title:
        out["horse_name"] = title.get_text(strip=True) or None

    blood_table = soup.find("table", class_="blood_table")
    if blood_table:
        rows = blood_table.find_all("tr")
        if rows:
            first = rows[0].find_all("td")
            if first and first[0].find("a"):
                out["sire"] = first[0].find("a").get_text(strip=True)
        if rows:
            half = len(rows) // 2
            if half and len(rows) > half:
                mid = rows[half].find_all("td")
                if mid and mid[0].find("a"):
                    out["dam"] = mid[0].find("a").get_text(strip=True)
                if len(mid) >= 2 and mid[1].find("a"):
                    out["broodmare_sire"] = mid[1].find("a").get_text(strip=True)

    horse_link = soup.find("a", href=re.compile(r"/horse/(?:result/)?[A-Za-z0-9]+/?"))
    if horse_link:
        href = str(horse_link.get("href") or "")
        m = re.search(r"/horse/(?:result/)?([A-Za-z0-9]+)(?:/|$)", href)
        if m:
            out["horse_id"] = m.group(1)
    return out


def _missing_fields_before(target: ValidationTarget) -> list[str]:
    if target.reason == "consistency:race_without_horse_data":
        return ["horse_id", "horse_name", "frame_number", "horse_number"]
    if target.column in {"race_number", "margin"}:
        return []
    if target.column:
        return [target.column]
    return []


def _parse_for_target(target: ValidationTarget, html: str) -> tuple[str, dict[str, Any], str | None]:
    if not html or not html.strip():
        return "parse_failed", {}, "empty-body"
    if len(html.strip()) < 80:
        return "parse_failed", {}, "too-short-html"
    if _is_error_page(html):
        return "parse_failed", {}, "error-page"

    detected = _detect_page_type(target.url, html)
    expected = _expected_type_for_url_type(target.url_type)
    if expected != "unknown" and detected != expected:
        return "parse_failed", {}, f"page-type-mismatch:{detected}"

    if target.url_type in {"result_page", "race_detail"}:
        fields = _extract_race_fields(html, target.race_id, target.horse_id)
    else:
        fields = _extract_horse_fields(html)

    if not fields:
        return "parse_failed", {}, "no-fields"
    return "parse_success", fields, None


def _would_fix_columns(target: ValidationTarget, missing_before: list[str], fields_after: dict[str, Any]) -> list[str]:
    if target.reason == "consistency:race_without_horse_data":
        entries = fields_after.get("entries") if isinstance(fields_after.get("entries"), list) else []
        if entries:
            return ["(check)"]
        return []
    out = []
    for col in missing_before:
        val = fields_after.get(col)
        if val not in (None, "", []):
            out.append(col)
    return out


def _field_names_found(fields: dict[str, Any]) -> list[str]:
    out = []
    for k, v in fields.items():
        if k == "entries":
            continue
        if v not in (None, "", []):
            out.append(k)
    return sorted(out)


def _required_missing_count(url_type: str, fields: dict[str, Any]) -> int:
    if url_type in {"result_page", "race_detail"}:
        required = ["race_id", "horse_id", "horse_name", "finish_position", "result_time", "margin", "frame_number", "horse_number", "race_date", "venue"]
    else:
        required = ["horse_id", "horse_name", "sire", "dam", "broodmare_sire"]
    cnt = 0
    for key in required:
        if fields.get(key) in (None, "", []):
            cnt += 1
    return cnt


def _select_targets(
    all_targets: list[ValidationTarget],
    *,
    target: str,
    url_type: str,
    max_urls: int,
    cache_conn: sqlite3.Connection,
) -> tuple[list[ValidationTarget], dict[str, int]]:
    excluded = {
        "schema_review": 0,
        "domain_allowed": 0,
        "metadata_repair": 0,
        "cache_available": 0,
    }

    chosen: list[ValidationTarget] = []
    seen_urls: set[str] = set()
    seen_race_ids: set[str] = set()
    seen_horse_ids: set[str] = set()

    wanted_type = URL_TYPE_MAP.get(url_type, "all")

    for item in all_targets:
        if not _target_allowed(item.column, target):
            continue
        if item.reason in {"derived-field-candidate", "alias-candidate"} or item.column == "race_number":
            excluded["schema_review"] += 1
            continue
        if item.reason == "domain-allowed-missing" or item.column == "margin":
            excluded["domain_allowed"] += 1
            continue
        if item.reason == "true-missing" and item.column in {"race_date", "venue"}:
            excluded["metadata_repair"] += 1
            continue

        canonical = _canonical_url_type(item.url_type)
        if wanted_type != "all" and canonical != wanted_type:
            continue

        if _cache_has_url(cache_conn, item.url):
            excluded["cache_available"] += 1
            continue

        if item.url in seen_urls:
            continue
        if canonical in {"result_page", "race_detail"} and item.race_id:
            if item.race_id in seen_race_ids:
                continue
            seen_race_ids.add(item.race_id)
        if canonical in {"horse_detail", "pedigree"} and item.horse_id:
            if item.horse_id in seen_horse_ids:
                continue
            seen_horse_ids.add(item.horse_id)

        seen_urls.add(item.url)
        chosen.append(item)
        if len(chosen) >= max_urls:
            break

    return chosen, excluded


async def _fetch_live(
    target: ValidationTarget,
    *,
    fixture_map: dict[str, dict[str, Any]] | None,
    metric_before: dict[str, int],
) -> tuple[int, str, str, int, bool, bool, bool, float]:
    started = time.perf_counter()
    if fixture_map is not None:
        data = fixture_map.get(target.url, fixture_map.get("*", {}))
        status = int(data.get("status", 0))
        body = str(data.get("body", ""))
        source = str(data.get("source", "fixture"))
        attempts = int(data.get("attempts", 1))
        elapsed = float(data.get("elapsed_seconds", round(time.perf_counter() - started, 3)))
        backoff_observed = bool(data.get("backoff_observed", attempts > 1))
        circuit_observed = bool(data.get("circuit_open_observed", False))
        cache_hit = source == "cache"
        return status, body, source, attempts, cache_hit, backoff_observed, circuit_observed, elapsed

    import aiohttp  # type: ignore

    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        result, text = await fetch_text(
            session,
            target.url,
            use_cache=False,
            force_refresh=True,
            dry_run=False,
            min_interval_sec=1.0,
            max_retries=3,
            retry_base_sec=2.0,
            retry_jitter_sec=0.7,
            circuit_threshold=3,
            circuit_cooldown_sec=120.0,
        )

    metric_after = get_fetch_metrics(reset=False)
    backoff_observed = int(metric_after.get("backoff_count", 0)) > int(metric_before.get("backoff_count", 0))
    circuit_observed = int(metric_after.get("circuit_open_count", 0)) > int(metric_before.get("circuit_open_count", 0))
    elapsed = round(time.perf_counter() - started, 3)
    return (
        int(result.status),
        text,
        str(result.source),
        int(result.attempts or 0),
        str(result.source) == "cache",
        backoff_observed,
        circuit_observed,
        elapsed,
    )


def _load_fixture_map(path: Path | None) -> dict[str, dict[str, Any]] | None:
    if path is None:
        return None
    data = _load_json(path, "fixture-json")
    rows = data.get("responses") if isinstance(data.get("responses"), dict) else {}
    out: dict[str, dict[str, Any]] = {}
    for key, val in rows.items():
        if isinstance(val, dict):
            out[str(key)] = val
    return out


async def _run_validation(args: argparse.Namespace) -> dict[str, Any]:
    plan = _load_json(Path(args.input_refetch_plan), "input-refetch-plan")
    all_targets = _read_targets_from_plan(plan)
    requested_max_urls = int(args.max_urls)
    if requested_max_urls <= 0:
        raise SystemExit("error: --max-urls must be >= 1")
    max_urls_applied = min(requested_max_urls, 10)

    cache_conn = _open_ro_db(Path(args.cache_db))
    fixture_map = _load_fixture_map(Path(args.fixture_json) if args.fixture_json else None)
    try:
        selected, excluded_counts = _select_targets(
            all_targets,
            target=args.target,
            url_type=args.url_type,
            max_urls=max_urls_applied,
            cache_conn=cache_conn,
        )

        started_all = time.perf_counter()
        get_fetch_metrics(reset=True)

        observed: list[FetchObserved] = []
        for target in selected:
            metric_before = get_fetch_metrics(reset=False)
            http_status, html, _source, retry_count, cache_hit, backoff_observed, circuit_observed, elapsed = await _fetch_live(
                target,
                fixture_map=fixture_map,
                metric_before=metric_before,
            )

            missing_before = _missing_fields_before(target)
            if http_status < 200 or http_status >= 300:
                parse_status = "http_error"
                fields_after: dict[str, Any] = {}
                would_fix = []
                action = "http_error"
            else:
                parse_status, fields_after, parse_reason = _parse_for_target(target, html)
                if parse_status == "parse_success":
                    would_fix = _would_fix_columns(target, missing_before, fields_after)
                    if would_fix:
                        action = "would-fix"
                    else:
                        action = "no-downgrade-skip"
                else:
                    would_fix = []
                    action = f"parse_failed:{parse_reason or 'unknown'}"

            observed.append(
                FetchObserved(
                    url=target.url,
                    url_type=target.url_type,
                    race_id=target.race_id,
                    horse_id=target.horse_id,
                    reason=target.reason,
                    column=target.column,
                    priority=target.priority,
                    source=target.source,
                    recommended_next_action=target.recommended_next_action,
                    http_status=http_status,
                    parse_status=parse_status,
                    action=action,
                    elapsed_seconds=elapsed,
                    retry_count=retry_count,
                    cache_hit=cache_hit,
                    backoff_observed=backoff_observed,
                    circuit_open_observed=circuit_observed,
                    missing_fields_before=missing_before,
                    fields_found_after=_field_names_found(fields_after),
                    would_fix_columns=would_fix,
                )
            )

        elapsed_seconds = round(time.perf_counter() - started_all, 3)
        metrics = get_fetch_metrics(reset=False)

        attempted_url_count = len(observed)
        http_success_count = sum(1 for x in observed if 200 <= x.http_status < 300)
        http_error_count = attempted_url_count - http_success_count
        parse_success_count = sum(1 for x in observed if x.parse_status == "parse_success")
        parse_failed_count = attempted_url_count - parse_success_count
        would_fix_count = sum(1 for x in observed if len(x.would_fix_columns) > 0)
        would_not_fix_count = attempted_url_count - would_fix_count
        no_downgrade_skip_count = sum(1 for x in observed if x.action == "no-downgrade-skip")
        required_field_missing_count = sum(
            _required_missing_count(x.url_type, {name: True for name in x.fields_found_after}) for x in observed if x.parse_status == "parse_success"
        )
        repairable_from_live_count = would_fix_count

        unique_url_count = int(plan.get("unique_url_count") or 0)
        avg_elapsed_per_url = (elapsed_seconds / attempted_url_count) if attempted_url_count else 0.0
        estimated_full_refetch_runtime_seconds = round(unique_url_count * avg_elapsed_per_url, 2)

        sample_results = [
            {
                "url": x.url,
                "url_type": x.url_type,
                "race_id": x.race_id,
                "horse_id": x.horse_id,
                "http_status": x.http_status,
                "parse_status": x.parse_status,
                "missing_fields_before": x.missing_fields_before,
                "fields_found_after": x.fields_found_after,
                "would_fix_columns": x.would_fix_columns,
                "action": x.action,
                "reason": x.reason,
                "recommended_next_action": x.recommended_next_action,
            }
            for x in observed
        ]

        recommended_next_actions = []
        if attempted_url_count == 0:
            recommended_next_actions.append("対象URLがないため live validation の対象条件を見直す")
        elif would_fix_count == 0:
            recommended_next_actions.append("would_fix_count が 0 のため parser/selector か対象URL層を再確認")
        else:
            recommended_next_actions.append("would_fix_count が確認できたため小規模 targeted refetch 実行設計を次に検討")

        if unique_url_count > 100:
            recommended_next_actions.append("full refetch は date/race_id 分割で段階実行")
        if any(x.url_type == "result_page" for x in observed):
            recommended_next_actions.append("result page 優先で finish_position 系の修復見込みを先に検証")
        if any(x.url_type == "race_detail" for x in observed):
            recommended_next_actions.append("race_without_horse_data は race detail/result の有効性を比較")
        if any(x.url_type in {"horse_detail", "pedigree"} for x in observed):
            recommended_next_actions.append("horse/pedigree は別 tier で上限を分離して検証")

        verdict = "pass" if attempted_url_count > 0 and parse_success_count > 0 else "warn"
        return {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "input_refetch_plan": str(args.input_refetch_plan),
            "target": args.target,
            "url_type": args.url_type,
            "max_urls": int(args.max_urls),
            "max_urls_applied": max_urls_applied,
            "attempted_url_count": attempted_url_count,
            "http_success_count": http_success_count,
            "http_error_count": http_error_count,
            "parse_success_count": parse_success_count,
            "parse_failed_count": parse_failed_count,
            "would_fix_count": would_fix_count,
            "would_not_fix_count": would_not_fix_count,
            "required_field_missing_count": required_field_missing_count,
            "no_downgrade_skip_count": no_downgrade_skip_count,
            "repairable_from_live_count": repairable_from_live_count,
            "elapsed_seconds": elapsed_seconds,
            "estimated_full_refetch_runtime_seconds": estimated_full_refetch_runtime_seconds,
            "fetch_metrics": metrics,
            "excluded_schema_review_count": excluded_counts["schema_review"],
            "excluded_domain_allowed_count": excluded_counts["domain_allowed"],
            "excluded_metadata_repair_count": excluded_counts["metadata_repair"],
            "excluded_cache_available_count": excluded_counts["cache_available"],
            "sample_results": sample_results,
            "recommended_next_actions": recommended_next_actions,
            "rate_limit_policy": {
                "max_urls": max_urls_applied,
                "max_supported_urls": 10,
                "min_interval_sec": 1.0,
                "max_retries": 3,
                "retry_base_sec": 2.0,
                "retry_jitter_sec": 0.7,
                "retry_after_enabled": True,
                "circuit_breaker": {"threshold": 3, "cooldown_sec": 120.0},
                "parallelism": 1,
                "fetch_pipeline_used": True,
            },
            "safety_flags": {
                "small_live_validation_only": True,
                "max_urls_limited": True,
                "no_db_write": True,
                "no_upsert": True,
                "no_repair_execute": True,
                "no_production_table_write": True,
                "no_force_refresh_execute": True,
                "no_bulk_refetch": True,
            },
            "verdict": verdict,
            "verdict_reason": "small-live-validation",
        }
    finally:
        cache_conn.close()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Small live validation for P0 targeted refetch URLs")
    p.add_argument("--input-refetch-plan", default=str(DEFAULT_REFETCH_PLAN_INPUT), help="Path to p0_targeted_refetch_plan.json")
    p.add_argument("--target", choices=["all", "race", "horse", "result", "pedigree", "odds"], default="all")
    p.add_argument("--max-urls", type=int, default=5, help="Maximum URLs to validate live (capped at 10)")
    p.add_argument("--url-type", choices=["race-result", "race-detail", "horse-detail", "pedigree", "all"], default="all")
    p.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON path")
    p.add_argument("--cache-db", default=str(DEFAULT_CACHE_DB), help="Read-only fetch cache DB path")
    p.add_argument("--fixture-json", default="", help=argparse.SUPPRESS)
    return p


def main() -> int:
    args = _build_parser().parse_args()
    if int(args.max_urls) > 10:
        args.max_urls = 10

    payload = asyncio.run(_run_validation(args))

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "output": str(out),
                "verdict": payload.get("verdict"),
                "max_urls": payload.get("max_urls"),
                "max_urls_applied": payload.get("max_urls_applied"),
                "attempted_url_count": payload.get("attempted_url_count"),
                "http_success_count": payload.get("http_success_count"),
                "http_error_count": payload.get("http_error_count"),
                "parse_success_count": payload.get("parse_success_count"),
                "parse_failed_count": payload.get("parse_failed_count"),
                "would_fix_count": payload.get("would_fix_count"),
                "would_not_fix_count": payload.get("would_not_fix_count"),
                "estimated_full_refetch_runtime_seconds": payload.get("estimated_full_refetch_runtime_seconds"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())