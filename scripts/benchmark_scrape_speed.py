#!/usr/bin/env python3
"""Detailed scrape benchmark tiers with safe runtime estimation.

Safety policy:
- Never runs 10-year live scraping.
- Live scraping is strictly capped by day/race/horse guards.
- Uses fetch_pipeline safeguards (rate limit, backoff, Retry-After, circuit breaker).
- Dry-run mode performs no HTTP access.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
import sys

import aiohttp
from bs4 import BeautifulSoup

ROOT_DIR = Path(__file__).resolve().parent.parent
PY_API_DIR = ROOT_DIR / "python-api"
if str(PY_API_DIR) not in sys.path:
    sys.path.insert(0, str(PY_API_DIR))

from scraping.constants import get_random_headers  # type: ignore
from scraping.fetch_pipeline import estimate_fetch_plan, fetch_text, get_fetch_metrics  # type: ignore


DEFAULT_OUTPUT = ROOT_DIR / "reports" / "scrape_benchmark_summary.json"
DAYS_PER_YEAR = 365
DAYS_10_YEARS = 3650
TIERS = ("list", "race-detail", "horse-detail", "full-1day")
MIN_HTML_CHARS = 200
VENUE_PATTERN = re.compile(r"(札幌|函館|福島|新潟|東京|中山|中京|京都|阪神|小倉|門別|盛岡|水沢|浦和|船橋|大井|川崎|金沢|笠松|名古屋|園田|姫路|高知|佐賀|帯広)")


@dataclass
class BenchmarkSpec:
    tier: str
    label: str
    start_date: str
    end_date: str
    mode: str


def _parse_date(s: str) -> date:
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"invalid date format: {s}")


def _fmt_date(d: date) -> str:
    return d.strftime("%Y%m%d")


def _inclusive_days(start_date: str, end_date: str) -> int:
    s = _parse_date(start_date)
    e = _parse_date(end_date)
    if e < s:
        raise ValueError("end_date must be >= start_date")
    return (e - s).days + 1


def _date_range_label(start_date: str, end_date: str) -> str:
    return f"{start_date}-{end_date}"


def _iter_dates(start_date: str, end_date: str) -> list[str]:
    s = _parse_date(start_date)
    e = _parse_date(end_date)
    out: list[str] = []
    cur = s
    while cur <= e:
        out.append(_fmt_date(cur))
        cur += timedelta(days=1)
    return out


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _safe_int(v: Any) -> int:
    try:
        if v is None:
            return 0
        return int(v)
    except Exception:
        return 0


def _failed_count_from_metrics(metrics: dict[str, Any]) -> int:
    return (
        _safe_int(metrics.get("status_429"))
        + _safe_int(metrics.get("status_403"))
        + _safe_int(metrics.get("status_500"))
        + _safe_int(metrics.get("status_503"))
        + _safe_int(metrics.get("timeout_count"))
    )


def _dynamic_multiplier(base_multiplier: float, retry_count: int, failed_count: int, actual_requests: int) -> float:
    noisy = failed_count > 0 or retry_count >= 3
    high_ratio = actual_requests > 0 and (retry_count / actual_requests) >= 0.1
    if noisy or high_ratio:
        return max(base_multiplier, 2.0)
    return base_multiplier


def _parse_race_ids(html: str) -> list[str]:
    ids = re.findall(r"/race/(\d{12})/", html)
    ids += re.findall(r"race_id=(\d{12})", html)
    return list(dict.fromkeys(ids))


def _parse_horse_ids(html: str) -> list[str]:
    ids = re.findall(r"/horse/(\d{10})/", html)
    return list(dict.fromkeys(ids))


def _list_urls_for_dates(dates: list[str]) -> list[str]:
    urls: list[str] = []
    for d in dates:
        urls.append(f"https://db.netkeiba.com/race/list/{d}/")
        urls.append(f"https://race.netkeiba.com/top/race_list_sub.html?kaisai_date={d}")
    return urls


def _race_urls_from_ids(race_ids: list[str]) -> list[str]:
    return [f"https://db.netkeiba.com/race/{rid}/" for rid in race_ids]


def _horse_urls_from_ids(horse_ids: list[str]) -> list[str]:
    urls: list[str] = []
    for hid in horse_ids:
        urls.append(f"https://db.netkeiba.com/horse/result/{hid}/")
        urls.append(f"https://db.netkeiba.com/horse/ped/{hid}/")
    return urls


def _synthetic_race_ids(dates: list[str], max_races: int) -> list[str]:
    out: list[str] = []
    for d in dates:
        y = d[:4]
        for i in range(max_races):
            out.append(f"{y}010101{i+1:02d}")
    return out


def _synthetic_horse_ids(dates: list[str], max_horses: int) -> list[str]:
    out: list[str] = []
    for d in dates:
        y = d[:4]
        for i in range(max_horses):
            out.append(f"{y}{(i+1):06d}")
    return out


def _build_resume_keys(prefix: str, count: int) -> list[str]:
    return [f"benchmark:{prefix}:{i}" for i in range(count)]


def _new_quality_stats(enabled: bool) -> dict[str, Any]:
    return {
        "enabled": bool(enabled),
        "http_success_count": 0,
        "http_error_count": 0,
        "empty_body_count": 0,
        "too_short_html_count": 0,
        "page_type_mismatch_count": 0,
        "title_empty_count": 0,
        "body_empty_count": 0,
        "parse_success_count": 0,
        "parse_failed_count": 0,
        "required_field_missing_count": 0,
        "duplicate_count": 0,
        "valid_record_count": 0,
        "invalid_record_count": 0,
        "status_403_count": 0,
        "status_404_count": 0,
        "status_429_count": 0,
        "status_500_count": 0,
        "status_503_count": 0,
        "timeout_count": 0,
    }


def _finalize_quality_stats(stats: dict[str, Any]) -> dict[str, Any]:
    parse_total = _safe_int(stats.get("parse_success_count")) + _safe_int(stats.get("parse_failed_count"))
    valid_total = _safe_int(stats.get("valid_record_count")) + _safe_int(stats.get("invalid_record_count"))
    missing = _safe_int(stats.get("required_field_missing_count"))
    dup = _safe_int(stats.get("duplicate_count"))
    http_error = _safe_int(stats.get("http_error_count"))
    mismatch = _safe_int(stats.get("page_type_mismatch_count"))

    parse_failed_rate = (_safe_int(stats.get("parse_failed_count")) / parse_total) if parse_total > 0 else 0.0
    invalid_record_rate = (_safe_int(stats.get("invalid_record_count")) / valid_total) if valid_total > 0 else 0.0
    required_missing_rate = (missing / valid_total) if valid_total > 0 else 0.0

    penalty = parse_failed_rate + invalid_record_rate + min(1.0, required_missing_rate)
    penalty += min(1.0, (http_error + mismatch + dup) / max(1, _safe_int(stats.get("http_success_count")) + http_error))
    data_quality_score = max(0.0, min(100.0, 100.0 * (1.0 - penalty / 4.0)))

    if data_quality_score >= 95.0:
        risk = "low"
    elif data_quality_score >= 85.0:
        risk = "medium"
    else:
        risk = "high"

    stats["parse_failed_rate"] = parse_failed_rate
    stats["required_field_missing_rate"] = required_missing_rate
    stats["invalid_record_rate"] = invalid_record_rate
    stats["data_quality_score"] = data_quality_score
    stats["quality_risk_level"] = risk
    return stats


def _merge_quality_stats(parts: list[dict[str, Any]]) -> dict[str, Any]:
    merged = _new_quality_stats(enabled=any(bool(p.get("enabled")) for p in parts if isinstance(p, dict)))
    for part in parts:
        if not isinstance(part, dict):
            continue
        for key in merged.keys():
            if key == "enabled":
                continue
            merged[key] = _safe_int(merged.get(key)) + _safe_int(part.get(key))
    return _finalize_quality_stats(merged)


def _detect_page_type(url: str, html: str) -> str:
    lower_html = html.lower()
    if "/race/list/" in url or "race_list_sub" in url:
        return "list"
    if re.search(r"/race/\d{12}/", url):
        if "/horse/" in lower_html:
            return "race-detail"
        return "race-detail"
    if "/horse/result/" in url:
        return "horse-detail"
    if "/horse/ped/" in url:
        return "horse-detail"
    return "unknown"


def _expected_type_for_url(url: str) -> str:
    if "/race/list/" in url or "race_list_sub" in url:
        return "list"
    if re.search(r"/race/\d{12}/", url):
        return "race-detail"
    if "/horse/result/" in url or "/horse/ped/" in url:
        return "horse-detail"
    return "unknown"


def _parse_race_page_quality(url: str, html: str) -> dict[str, Any]:
    try:
        soup = BeautifulSoup(html, "html.parser")
        race_id_match = re.search(r"/race/(\d{12})/", url)
        race_id = race_id_match.group(1) if race_id_match else ""

        race_name = ""
        for sel in ("h1", ".RaceName", ".RaceName01"):
            node = soup.select_one(sel)
            if node:
                race_name = node.get_text(strip=True)
                if race_name:
                    break

        page_text = soup.get_text(" ", strip=True)
        race_date = ""
        m_date = re.search(r"(\d{4}年\d{1,2}月\d{1,2}日)", page_text)
        if m_date:
            race_date = m_date.group(1)

        venue = ""
        m_venue = VENUE_PATTERN.search(page_text)
        if m_venue:
            venue = m_venue.group(1)

        race_number = ""
        m_num = re.search(r"\b(\d{1,2})R\b", page_text)
        if m_num:
            race_number = m_num.group(1)

        horse_pairs: list[tuple[str, str]] = []
        for a in soup.select("a[href*='/horse/']"):
            href = str(a.get("href") or "")
            m_h = re.search(r"/horse/(\d{10})/?", href)
            if not m_h:
                continue
            horse_pairs.append((m_h.group(1), a.get_text(strip=True)))

        horse_ids = [hid for hid, _ in horse_pairs]
        horse_names = [name for _, name in horse_pairs if name]
        unique_horse_ids = list(dict.fromkeys(horse_ids))
        duplicate_count = max(0, len(horse_ids) - len(set(horse_ids)))

        missing = 0
        if not race_id:
            missing += 1
        if not race_name:
            missing += 1
        if not race_date:
            missing += 1
        if not venue:
            missing += 1
        if not race_number:
            missing += 1
        if not unique_horse_ids:
            missing += 1
        if not horse_names:
            missing += 1

        horse_count = len(unique_horse_ids)
        horse_count_valid = 1 <= horse_count <= 18
        if not horse_count_valid:
            missing += 1

        parse_success = bool(race_id and horse_count > 0)
        valid_record = bool(parse_success and missing == 0 and duplicate_count == 0)
        return {
            "attempted": True,
            "parse_success": parse_success,
            "valid_record": valid_record,
            "missing": missing,
            "duplicate_count": duplicate_count,
        }
    except Exception:
        return {
            "attempted": True,
            "parse_success": False,
            "valid_record": False,
            "missing": 1,
            "duplicate_count": 0,
        }


def _extract_horse_name_from_soup(soup: BeautifulSoup) -> str:
    for sel in ("h1", ".horse_title", ".HorseName", ".HorseName01"):
        node = soup.select_one(sel)
        if node:
            t = node.get_text(strip=True)
            if t:
                return t
    title = soup.title.get_text(strip=True) if soup.title else ""
    return title


def _parse_horse_page_quality(url: str, html: str) -> dict[str, Any]:
    try:
        soup = BeautifulSoup(html, "html.parser")
        horse_id_match = re.search(r"/horse/(?:result|ped)/(\d{10})/", url)
        horse_id = horse_id_match.group(1) if horse_id_match else ""
        horse_name = _extract_horse_name_from_soup(soup)

        missing = 0
        if not horse_id:
            missing += 1
        if not horse_name:
            missing += 1

        if "/horse/ped/" in url:
            ped_nodes = soup.select("table.pedigree_table td, table.pedigree_table a, td.b_ml")
            if not ped_nodes:
                missing += 1

            sire = ""
            dam = ""
            broodmare_sire = ""
            ped_anchor_texts = [a.get_text(strip=True) for a in soup.select("table.pedigree_table a") if a.get_text(strip=True)]
            if ped_anchor_texts:
                sire = ped_anchor_texts[0] if len(ped_anchor_texts) >= 1 else ""
                dam = ped_anchor_texts[1] if len(ped_anchor_texts) >= 2 else ""
                broodmare_sire = ped_anchor_texts[2] if len(ped_anchor_texts) >= 3 else ""

            if sire is not None and sire == "":
                missing += 1
            if dam is not None and dam == "":
                missing += 1
            if broodmare_sire is not None and broodmare_sire == "":
                missing += 1

        parse_success = bool(horse_id and horse_name)
        valid_record = bool(parse_success and missing == 0)
        return {
            "attempted": True,
            "parse_success": parse_success,
            "valid_record": valid_record,
            "missing": missing,
            "duplicate_count": 0,
        }
    except Exception:
        return {
            "attempted": True,
            "parse_success": False,
            "valid_record": False,
            "missing": 1,
            "duplicate_count": 0,
        }


def _apply_quality_check(
    stats: dict[str, Any],
    *,
    tier: str,
    url: str,
    result: Any,
    text: str,
    min_html_chars: int,
) -> None:
    if not bool(stats.get("enabled")):
        return

    status = _safe_int(getattr(result, "status", 0))
    source = str(getattr(result, "source", ""))

    if 200 <= status < 300:
        stats["http_success_count"] = _safe_int(stats.get("http_success_count")) + 1
    else:
        stats["http_error_count"] = _safe_int(stats.get("http_error_count")) + 1
        if status == 403:
            stats["status_403_count"] = _safe_int(stats.get("status_403_count")) + 1
        elif status == 404:
            stats["status_404_count"] = _safe_int(stats.get("status_404_count")) + 1
        elif status == 429:
            stats["status_429_count"] = _safe_int(stats.get("status_429_count")) + 1
        elif status == 500:
            stats["status_500_count"] = _safe_int(stats.get("status_500_count")) + 1
        elif status == 503:
            stats["status_503_count"] = _safe_int(stats.get("status_503_count")) + 1
        elif status == 0 and ("timeout" in str(getattr(result, "error", "")).lower() or source == "network-error"):
            stats["timeout_count"] = _safe_int(stats.get("timeout_count")) + 1

    if not text.strip():
        stats["empty_body_count"] = _safe_int(stats.get("empty_body_count")) + 1
        return

    if len(text.strip()) < max(1, min_html_chars):
        stats["too_short_html_count"] = _safe_int(stats.get("too_short_html_count")) + 1

    page_type = _detect_page_type(url, text)
    expected = _expected_type_for_url(url)
    if expected != "unknown" and page_type != expected:
        stats["page_type_mismatch_count"] = _safe_int(stats.get("page_type_mismatch_count")) + 1

    soup = BeautifulSoup(text, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    body = soup.body.get_text(" ", strip=True) if soup.body else ""
    if not title:
        stats["title_empty_count"] = _safe_int(stats.get("title_empty_count")) + 1
    if not body:
        stats["body_empty_count"] = _safe_int(stats.get("body_empty_count")) + 1

    parse_result: dict[str, Any] | None = None
    if tier in ("race-detail", "full-1day") and expected == "race-detail":
        parse_result = _parse_race_page_quality(url, text)
    elif tier in ("horse-detail", "full-1day") and expected == "horse-detail":
        parse_result = _parse_horse_page_quality(url, text)

    if not parse_result or not bool(parse_result.get("attempted")):
        return

    if bool(parse_result.get("parse_success")):
        stats["parse_success_count"] = _safe_int(stats.get("parse_success_count")) + 1
    else:
        stats["parse_failed_count"] = _safe_int(stats.get("parse_failed_count")) + 1

    stats["required_field_missing_count"] = _safe_int(stats.get("required_field_missing_count")) + _safe_int(parse_result.get("missing"))
    stats["duplicate_count"] = _safe_int(stats.get("duplicate_count")) + _safe_int(parse_result.get("duplicate_count"))
    if bool(parse_result.get("valid_record")):
        stats["valid_record_count"] = _safe_int(stats.get("valid_record_count")) + 1
    else:
        stats["invalid_record_count"] = _safe_int(stats.get("invalid_record_count")) + 1


def _plan_metrics(urls: list[str], resume_prefix: str) -> dict[str, int]:
    resume_keys = _build_resume_keys(resume_prefix, len(urls))
    plan = estimate_fetch_plan(urls, resume_keys=resume_keys)
    unique_urls = _safe_int(plan.get("unique_urls"))
    cache_hits = _safe_int(plan.get("cache_hits"))
    resume_hits = _safe_int(plan.get("resume_hits"))
    est_req = _safe_int(plan.get("estimated_network_requests"))
    return {
        "target_count": _safe_int(plan.get("total_input_urls")),
        "unique_url_count": unique_urls,
        "estimated_request_count": est_req,
        "cache_hit_count": cache_hits,
        "cache_miss_count": max(0, unique_urls - cache_hits),
        "resume_hit_count": resume_hits,
    }


async def _fetch_urls_live(
    session: aiohttp.ClientSession,
    urls: list[str],
    resume_prefix: str,
    args: argparse.Namespace,
    tier: str,
    parse_race: bool = False,
    parse_horse: bool = False,
) -> dict[str, Any]:
    discovered_races: list[str] = []
    discovered_horses: list[str] = []
    consecutive_errors = 0
    consecutive_error_break = False
    quality = _new_quality_stats(bool(args.quality_check))

    get_fetch_metrics(reset=True)
    t0 = time.perf_counter()

    for idx, url in enumerate(urls):
        resume_key = None if bool(args.quality_check) else f"{resume_prefix}:{idx}"
        result, text = await fetch_text(
            session,
            url,
            use_cache=True,
            force_refresh=False,
            cache_ttl_sec=60,
            resume_key=resume_key,
            min_interval_sec=1.0,
            max_retries=3,
            retry_statuses={429, 500, 503},
            retry_base_sec=2.0,
            retry_jitter_sec=0.6,
            circuit_threshold=3,
            circuit_cooldown_sec=120.0,
        )

        _apply_quality_check(
            quality,
            tier=tier,
            url=url,
            result=result,
            text=text,
            min_html_chars=int(args.min_html_chars),
        )

        if result.status in (0, 429, 503):
            consecutive_errors += 1
        else:
            consecutive_errors = 0

        if parse_race and text:
            discovered_races.extend(_parse_race_ids(text))
        if parse_horse and text:
            discovered_horses.extend(_parse_horse_ids(text))

        metrics_now = get_fetch_metrics(reset=False)
        if _safe_int(metrics_now.get("network_requests")) >= int(args.max_actual_requests):
            break
        if consecutive_errors >= int(args.max_consecutive_errors):
            consecutive_error_break = True
            break

    elapsed = max(0.0, time.perf_counter() - t0)
    metrics = get_fetch_metrics(reset=True)
    return {
        "elapsed_seconds": elapsed,
        "metrics": metrics,
        "consecutive_error_break": consecutive_error_break,
        "race_ids": list(dict.fromkeys(discovered_races)),
        "horse_ids": list(dict.fromkeys(discovered_horses)),
        "quality": _finalize_quality_stats(quality),
    }


async def _run_tier_once(spec: BenchmarkSpec, args: argparse.Namespace) -> dict[str, Any]:
    dates = _iter_dates(spec.start_date, spec.end_date)
    days = len(dates)

    if spec.mode == "live" and days > int(args.max_live_days):
        raise ValueError(f"live range too large ({days} days). max_live_days={args.max_live_days}")
    if spec.mode == "live" and days >= DAYS_10_YEARS:
        raise ValueError("10-year live scraping is forbidden")

    if spec.mode == "live":
        if int(args.max_races) <= 0 or int(args.max_horses) <= 0:
            raise ValueError("live mode requires positive max-races and max-horses")
        if int(args.max_races) > 30:
            raise ValueError("max-races must be <= 30 for safety")
        if int(args.max_horses) > 50:
            raise ValueError("max-horses must be <= 50 for safety")

    if spec.tier == "full-1day" and days != 1:
        raise ValueError("full-1day tier requires exactly 1 day range")

    target_races = 0
    target_horses = 0
    all_urls: list[str] = []

    list_urls = _list_urls_for_dates(dates)

    if spec.mode == "dry-run":
        if spec.tier == "list":
            all_urls = list_urls
        elif spec.tier == "race-detail":
            race_ids = _synthetic_race_ids(dates, int(args.max_races))
            target_races = len(race_ids)
            all_urls = _race_urls_from_ids(race_ids)
        elif spec.tier == "horse-detail":
            horse_ids = _synthetic_horse_ids(dates, int(args.max_horses))
            target_horses = len(horse_ids)
            all_urls = _horse_urls_from_ids(horse_ids)
        elif spec.tier == "full-1day":
            race_ids = _synthetic_race_ids(dates, int(args.max_races))
            horse_ids = _synthetic_horse_ids(dates, int(args.max_horses))
            target_races = len(race_ids)
            target_horses = len(horse_ids)
            all_urls = list_urls + _race_urls_from_ids(race_ids) + _horse_urls_from_ids(horse_ids)
        else:
            raise ValueError(f"unsupported tier: {spec.tier}")

        plan = _plan_metrics(all_urls, resume_prefix=f"{spec.tier}:{spec.start_date}:{spec.end_date}:{spec.mode}")
        quality = _finalize_quality_stats(_new_quality_stats(bool(args.quality_check)))
        return {
            "tier": spec.tier,
            "date_range": _date_range_label(spec.start_date, spec.end_date),
            "mode": spec.mode,
            "max_races": int(args.max_races),
            "max_horses": int(args.max_horses),
            "target_count": plan["target_count"],
            "target_race_count": target_races,
            "target_horse_count": target_horses,
            "unique_url_count": plan["unique_url_count"],
            "estimated_request_count": plan["estimated_request_count"],
            "actual_network_request_count": 0,
            "cache_hit_count": plan["cache_hit_count"],
            "cache_miss_count": plan["cache_miss_count"],
            "resume_hit_count": plan["resume_hit_count"],
            "retry_count": 0,
            "failed_count": 0,
            "elapsed_seconds": 0.0,
            "consecutive_error_break": False,
            **quality,
        }

    timeout = aiohttp.ClientTimeout(total=25, connect=8)
    connector = aiohttp.TCPConnector(limit=3, limit_per_host=2)

    async with aiohttp.ClientSession(timeout=timeout, connector=connector, headers=get_random_headers()) as session:
        if spec.tier == "list":
            all_urls = list_urls
            plan = _plan_metrics(all_urls, resume_prefix=f"{spec.tier}:{spec.start_date}:{spec.end_date}:{spec.mode}")
            measured = await _fetch_urls_live(
                session,
                all_urls,
                resume_prefix=f"live:{spec.tier}:{spec.start_date}:{spec.end_date}",
                args=args,
                tier=spec.tier,
            )

        elif spec.tier == "race-detail":
            discover = await _fetch_urls_live(
                session,
                list_urls,
                resume_prefix=f"live:list-discovery:{spec.start_date}:{spec.end_date}",
                args=args,
                tier=spec.tier,
                parse_race=True,
            )
            race_ids = discover["race_ids"][: int(args.max_races)]
            target_races = len(race_ids)
            race_urls = _race_urls_from_ids(race_ids)
            all_urls = list_urls + race_urls
            plan = _plan_metrics(all_urls, resume_prefix=f"{spec.tier}:{spec.start_date}:{spec.end_date}:{spec.mode}")
            detail = await _fetch_urls_live(
                session,
                race_urls,
                resume_prefix=f"live:race-detail:{spec.start_date}:{spec.end_date}",
                args=args,
                tier=spec.tier,
            )
            measured = {
                "elapsed_seconds": _safe_float(discover.get("elapsed_seconds")) + _safe_float(detail.get("elapsed_seconds")),
                "consecutive_error_break": bool(discover.get("consecutive_error_break") or detail.get("consecutive_error_break")),
                "metrics": {
                    k: _safe_int(discover.get("metrics", {}).get(k)) + _safe_int(detail.get("metrics", {}).get(k))
                    for k in set(list(discover.get("metrics", {}).keys()) + list(detail.get("metrics", {}).keys()))
                },
                "quality": _merge_quality_stats([
                    discover.get("quality", {}),
                    detail.get("quality", {}),
                ]),
            }

        elif spec.tier == "horse-detail":
            discover_list = await _fetch_urls_live(
                session,
                list_urls,
                resume_prefix=f"live:list-for-horse:{spec.start_date}:{spec.end_date}",
                args=args,
                tier=spec.tier,
                parse_race=True,
            )
            race_ids = discover_list["race_ids"][: int(args.max_races)]
            race_urls = _race_urls_from_ids(race_ids)
            discover_horse = await _fetch_urls_live(
                session,
                race_urls,
                resume_prefix=f"live:race-for-horse:{spec.start_date}:{spec.end_date}",
                args=args,
                tier=spec.tier,
                parse_horse=True,
            )
            horse_ids = discover_horse["horse_ids"][: int(args.max_horses)]
            target_horses = len(horse_ids)
            horse_urls = _horse_urls_from_ids(horse_ids)
            all_urls = list_urls + race_urls + horse_urls
            plan = _plan_metrics(all_urls, resume_prefix=f"{spec.tier}:{spec.start_date}:{spec.end_date}:{spec.mode}")
            detail = await _fetch_urls_live(
                session,
                horse_urls,
                resume_prefix=f"live:horse-detail:{spec.start_date}:{spec.end_date}",
                args=args,
                tier=spec.tier,
            )
            measured = {
                "elapsed_seconds": (
                    _safe_float(discover_list.get("elapsed_seconds"))
                    + _safe_float(discover_horse.get("elapsed_seconds"))
                    + _safe_float(detail.get("elapsed_seconds"))
                ),
                "consecutive_error_break": bool(
                    discover_list.get("consecutive_error_break")
                    or discover_horse.get("consecutive_error_break")
                    or detail.get("consecutive_error_break")
                ),
                "metrics": {
                    k: _safe_int(discover_list.get("metrics", {}).get(k))
                    + _safe_int(discover_horse.get("metrics", {}).get(k))
                    + _safe_int(detail.get("metrics", {}).get(k))
                    for k in set(
                        list(discover_list.get("metrics", {}).keys())
                        + list(discover_horse.get("metrics", {}).keys())
                        + list(detail.get("metrics", {}).keys())
                    )
                },
                "quality": _merge_quality_stats([
                    discover_list.get("quality", {}),
                    discover_horse.get("quality", {}),
                    detail.get("quality", {}),
                ]),
            }

        elif spec.tier == "full-1day":
            discover_list = await _fetch_urls_live(
                session,
                list_urls,
                resume_prefix=f"live:full-list:{spec.start_date}:{spec.end_date}",
                args=args,
                tier=spec.tier,
                parse_race=True,
            )
            race_ids = discover_list["race_ids"][: int(args.max_races)]
            target_races = len(race_ids)
            race_urls = _race_urls_from_ids(race_ids)
            race_stage = await _fetch_urls_live(
                session,
                race_urls,
                resume_prefix=f"live:full-race:{spec.start_date}:{spec.end_date}",
                args=args,
                tier=spec.tier,
                parse_horse=True,
            )
            horse_ids = race_stage["horse_ids"][: int(args.max_horses)]
            target_horses = len(horse_ids)
            horse_urls = _horse_urls_from_ids(horse_ids)
            horse_stage = await _fetch_urls_live(
                session,
                horse_urls,
                resume_prefix=f"live:full-horse:{spec.start_date}:{spec.end_date}",
                args=args,
                tier=spec.tier,
            )
            all_urls = list_urls + race_urls + horse_urls
            plan = _plan_metrics(all_urls, resume_prefix=f"{spec.tier}:{spec.start_date}:{spec.end_date}:{spec.mode}")
            measured = {
                "elapsed_seconds": (
                    _safe_float(discover_list.get("elapsed_seconds"))
                    + _safe_float(race_stage.get("elapsed_seconds"))
                    + _safe_float(horse_stage.get("elapsed_seconds"))
                ),
                "consecutive_error_break": bool(
                    discover_list.get("consecutive_error_break")
                    or race_stage.get("consecutive_error_break")
                    or horse_stage.get("consecutive_error_break")
                ),
                "metrics": {
                    k: _safe_int(discover_list.get("metrics", {}).get(k))
                    + _safe_int(race_stage.get("metrics", {}).get(k))
                    + _safe_int(horse_stage.get("metrics", {}).get(k))
                    for k in set(
                        list(discover_list.get("metrics", {}).keys())
                        + list(race_stage.get("metrics", {}).keys())
                        + list(horse_stage.get("metrics", {}).keys())
                    )
                },
                "quality": _merge_quality_stats([
                    discover_list.get("quality", {}),
                    race_stage.get("quality", {}),
                    horse_stage.get("quality", {}),
                ]),
            }

        else:
            raise ValueError(f"unsupported tier: {spec.tier}")

    metrics = measured["metrics"]
    quality = measured.get("quality", _finalize_quality_stats(_new_quality_stats(bool(args.quality_check))))
    return {
        "tier": spec.tier,
        "date_range": _date_range_label(spec.start_date, spec.end_date),
        "mode": spec.mode,
        "max_races": int(args.max_races),
        "max_horses": int(args.max_horses),
        "target_count": _safe_int(plan.get("target_count")),
        "target_race_count": target_races,
        "target_horse_count": target_horses,
        "unique_url_count": _safe_int(plan.get("unique_url_count")),
        "estimated_request_count": _safe_int(plan.get("estimated_request_count")),
        "actual_network_request_count": _safe_int(metrics.get("network_requests")),
        "cache_hit_count": _safe_int(plan.get("cache_hit_count")),
        "cache_miss_count": _safe_int(plan.get("cache_miss_count")),
        "resume_hit_count": _safe_int(plan.get("resume_hit_count")),
        "retry_count": _safe_int(metrics.get("retry_count")),
        "failed_count": _failed_count_from_metrics(metrics),
        "elapsed_seconds": float(_safe_float(measured.get("elapsed_seconds")) or 0.0),
        "consecutive_error_break": bool(measured.get("consecutive_error_break")),
        **quality,
    }


def _build_presets() -> dict[str, list[tuple[str, int, str]]]:
    return {
        "small": [
            ("list", 1, "dry-run"),
            ("list", 1, "live"),
            ("list", 7, "dry-run"),
            ("list", 7, "live"),
            ("list", 30, "dry-run"),
        ],
        "estimate-10y": [
            ("list", 1, "dry-run"),
            ("list", 1, "live"),
            ("list", 7, "dry-run"),
            ("list", 7, "live"),
            ("list", 3650, "dry-run"),
        ],
        "race-detail-small": [
            ("race-detail", 1, "dry-run"),
            ("race-detail", 1, "live"),
        ],
        "horse-detail-small": [
            ("horse-detail", 1, "dry-run"),
            ("horse-detail", 1, "live"),
        ],
        "full-1day-small": [
            ("full-1day", 1, "dry-run"),
            ("full-1day", 1, "live"),
        ],
        "detailed-estimate-10y": [
            ("list", 7, "live"),
            ("race-detail", 1, "live"),
            ("horse-detail", 1, "live"),
            ("list", 3650, "dry-run"),
            ("race-detail", 3650, "dry-run"),
            ("horse-detail", 3650, "dry-run"),
            ("full-1day", 1, "live"),
        ],
    }


def _build_specs(args: argparse.Namespace, anchor: date) -> list[BenchmarkSpec]:
    if args.preset:
        presets = _build_presets()
        if args.preset not in presets:
            raise ValueError(f"unsupported preset: {args.preset}")

        specs: list[BenchmarkSpec] = []
        for tier, days, mode in presets[args.preset]:
            start = anchor - timedelta(days=days - 1)
            specs.append(
                BenchmarkSpec(
                    tier=tier,
                    label=f"{tier}:{days}d",
                    start_date=_fmt_date(start),
                    end_date=_fmt_date(anchor),
                    mode=mode,
                )
            )
        return specs

    if not (args.start_date and args.end_date and args.mode):
        raise ValueError("either --preset or (--start-date --end-date --mode) is required")

    return [
        BenchmarkSpec(
            tier=args.tier,
            label=f"{args.tier}:{_inclusive_days(args.start_date, args.end_date)}d",
            start_date=args.start_date,
            end_date=args.end_date,
            mode=args.mode,
        )
    ]


def _load_previous_tier_calibration(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    cal = payload.get("tier_calibration") if isinstance(payload, dict) else None
    out: dict[str, float] = {}
    if isinstance(cal, dict):
        for k, v in cal.items():
            fv = _safe_float(v)
            if fv and fv > 0:
                out[str(k)] = fv
    return out


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    started_at = datetime.now().isoformat()
    anchor = _parse_date(args.anchor_date) if args.anchor_date else (date.today() - timedelta(days=1))
    specs = _build_specs(args, anchor)

    prior_cal = _load_previous_tier_calibration(Path(args.output))
    tier_live_spr: dict[str, list[float]] = {t: [] for t in TIERS}
    runs: list[dict[str, Any]] = []

    benchmark_circuit_breaker = False

    for spec in specs:
        if benchmark_circuit_breaker and spec.mode == "live":
            runs.append(
                {
                    "tier": spec.tier,
                    "date_range": _date_range_label(spec.start_date, spec.end_date),
                    "mode": spec.mode,
                    "status": "skipped",
                    "reason": "benchmark-circuit-breaker-open",
                }
            )
            continue

        row = await _run_tier_once(spec, args)
        row["status"] = "completed"

        elapsed = _safe_float(row.get("elapsed_seconds")) or 0.0
        actual_req = _safe_int(row.get("actual_network_request_count"))
        retry_count = _safe_int(row.get("retry_count"))
        failed_count = _safe_int(row.get("failed_count"))
        target_races = _safe_int(row.get("target_race_count"))
        target_horses = _safe_int(row.get("target_horse_count"))

        spr = elapsed / actual_req if actual_req > 0 and elapsed > 0 else None
        seconds_per_race = elapsed / target_races if target_races > 0 and elapsed > 0 else None
        seconds_per_horse = elapsed / target_horses if target_horses > 0 and elapsed > 0 else None

        row["seconds_per_request"] = spr
        row["seconds_per_race"] = seconds_per_race
        row["seconds_per_horse"] = seconds_per_horse

        if spr and spr > 0:
            tier_live_spr[spec.tier].append(spr)

        multiplier = _dynamic_multiplier(
            float(args.conservative_multiplier),
            retry_count=retry_count,
            failed_count=failed_count,
            actual_requests=actual_req,
        )
        row["conservative_multiplier"] = multiplier

        if spec.mode == "live" and (
            _safe_int(row.get("actual_network_request_count")) >= int(args.max_actual_requests)
            or bool(row.get("consecutive_error_break"))
        ):
            benchmark_circuit_breaker = True

        runs.append(row)

    tier_calibration: dict[str, float | None] = {}
    for tier in TIERS:
        if tier_live_spr[tier]:
            tier_calibration[tier] = sum(tier_live_spr[tier]) / len(tier_live_spr[tier])
        elif tier in prior_cal:
            tier_calibration[tier] = prior_cal[tier]
        else:
            tier_calibration[tier] = None

    for row in runs:
        if not isinstance(row, dict) or row.get("status") != "completed":
            continue

        tier = str(row.get("tier") or "list")
        est_req = _safe_int(row.get("estimated_request_count"))
        spr = _safe_float(row.get("seconds_per_request"))
        if spr is None:
            spr = _safe_float(tier_calibration.get(tier))

        if spr and est_req > 0:
            est_seconds = est_req * spr
            date_range = str(row.get("date_range") or "")
            row_days = 0
            if len(date_range.split("-")) == 2:
                row_days = _inclusive_days(*date_range.split("-"))
            row["estimated_10_year_seconds"] = est_seconds if row_days >= DAYS_10_YEARS else None
            if row.get("estimated_10_year_seconds") is None:
                row["estimated_10_year_seconds"] = est_seconds * (DAYS_10_YEARS / max(1, row_days))
            row["estimated_10_year_conservative_seconds"] = row["estimated_10_year_seconds"] * _safe_float(row.get("conservative_multiplier"))
            row["estimation_source"] = "tier-live-calibrated"
        else:
            row["estimated_10_year_seconds"] = None
            row["estimated_10_year_conservative_seconds"] = None
            row["estimation_source"] = "no-tier-calibration"

        parse_failed_rate = _safe_float(row.get("parse_failed_rate")) or 0.0
        required_missing_rate = _safe_float(row.get("required_field_missing_rate")) or 0.0
        invalid_record_rate = _safe_float(row.get("invalid_record_rate")) or 0.0
        est_quality_base = _safe_float(row.get("estimated_10_year_seconds"))
        if est_quality_base is not None and est_req > 0:
            expected_records_10y = float(est_req)
            row["estimated_10_year_parse_failures"] = expected_records_10y * parse_failed_rate
            row["estimated_10_year_required_field_missing"] = expected_records_10y * required_missing_rate
            row["estimated_10_year_invalid_records"] = expected_records_10y * invalid_record_rate
        else:
            row["estimated_10_year_parse_failures"] = None
            row["estimated_10_year_required_field_missing"] = None
            row["estimated_10_year_invalid_records"] = None

    tier_estimates: dict[str, dict[str, float | None]] = {}
    for tier in ("list", "race-detail", "horse-detail"):
        tier_rows = [r for r in runs if isinstance(r, dict) and r.get("tier") == tier and r.get("status") == "completed"]
        dry_10y = next(
            (
                r
                for r in tier_rows
                if r.get("mode") == "dry-run"
                and isinstance(r.get("date_range"), str)
                and len(str(r.get("date_range")).split("-")) == 2
                and _inclusive_days(*str(r.get("date_range")).split("-")) >= DAYS_10_YEARS
            ),
            None,
        )
        if isinstance(dry_10y, dict):
            tier_estimates[tier] = {
                "seconds_per_request": _safe_float(tier_calibration.get(tier)),
                "estimated_10_year_seconds": _safe_float(dry_10y.get("estimated_10_year_seconds")),
                "estimated_10_year_conservative_seconds": _safe_float(dry_10y.get("estimated_10_year_conservative_seconds")),
                "estimated_10_year_parse_failures": _safe_float(dry_10y.get("estimated_10_year_parse_failures")),
                "estimated_10_year_invalid_records": _safe_float(dry_10y.get("estimated_10_year_invalid_records")),
                "quality_risk_level": dry_10y.get("quality_risk_level"),
                "data_quality_score": _safe_float(dry_10y.get("data_quality_score")),
            }
        else:
            tier_estimates[tier] = {
                "seconds_per_request": _safe_float(tier_calibration.get(tier)),
                "estimated_10_year_seconds": None,
                "estimated_10_year_conservative_seconds": None,
                "estimated_10_year_parse_failures": None,
                "estimated_10_year_invalid_records": None,
                "quality_risk_level": None,
                "data_quality_score": None,
            }

    contains_live_10y = any(
        isinstance(r, dict)
        and r.get("mode") == "live"
        and isinstance(r.get("date_range"), str)
        and len(str(r.get("date_range")).split("-")) == 2
        and _inclusive_days(*str(r.get("date_range")).split("-")) >= DAYS_10_YEARS
        for r in runs
    )

    payload = {
        "started_at": started_at,
        "finished_at": datetime.now().isoformat(),
        "preset": args.preset,
        "anchor_date": _fmt_date(anchor),
        "policy": {
            "tier": args.tier,
            "quality_check": bool(args.quality_check),
            "min_html_chars": int(args.min_html_chars),
            "max_live_days": int(args.max_live_days),
            "max_races": int(args.max_races),
            "max_horses": int(args.max_horses),
            "max_actual_requests": int(args.max_actual_requests),
            "max_consecutive_errors": int(args.max_consecutive_errors),
            "base_conservative_multiplier": float(args.conservative_multiplier),
            "rate_limit_backoff_circuit_breaker": "enabled in fetch_pipeline",
            "dry_run_http_access": False,
            "live_10y_allowed": False,
        },
        "safety": {
            "contains_live_10y": bool(contains_live_10y),
            "benchmark_circuit_breaker_triggered": bool(benchmark_circuit_breaker),
        },
        "tier_calibration": tier_calibration,
        "tier_estimates_10y": tier_estimates,
        "runs": runs,
    }

    if payload["safety"]["contains_live_10y"]:
        raise RuntimeError("safety violation: 10-year live scraping is forbidden")

    return payload


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Detailed scrape speed benchmark tiers")
    p.add_argument(
        "--preset",
        choices=[
            "small",
            "estimate-10y",
            "race-detail-small",
            "horse-detail-small",
            "full-1day-small",
            "detailed-estimate-10y",
        ],
        default=None,
    )
    p.add_argument("--tier", choices=list(TIERS), default="list")
    p.add_argument("--start-date", default=None)
    p.add_argument("--end-date", default=None)
    p.add_argument("--mode", choices=["dry-run", "live"], default=None)
    p.add_argument("--anchor-date", default=None, help="Anchor date (YYYYMMDD). default: yesterday")
    p.add_argument("--max-live-days", type=int, default=7)
    p.add_argument("--max-races", type=int, default=10)
    p.add_argument("--max-horses", type=int, default=20)
    p.add_argument("--max-actual-requests", type=int, default=200)
    p.add_argument("--max-consecutive-errors", type=int, default=3)
    p.add_argument("--conservative-multiplier", type=float, default=1.5)
    p.add_argument("--quality-check", action="store_true", help="Enable HTTP/HTML/parse quality checks")
    p.add_argument("--min-html-chars", type=int, default=MIN_HTML_CHARS, help="Minimum HTML length threshold")
    p.add_argument("--output", default=str(DEFAULT_OUTPUT))
    return p


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        payload = asyncio.run(_run(args))
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        live_rows = [r for r in payload.get("runs", []) if isinstance(r, dict) and r.get("mode") == "live"]
        dry_rows = [r for r in payload.get("runs", []) if isinstance(r, dict) and r.get("mode") == "dry-run"]

        print(f"output: {out_path}")
        print(f"runs: total={len(payload.get('runs', []))} live={len(live_rows)} dry={len(dry_rows)}")
        print(f"contains_live_10y: {payload.get('safety', {}).get('contains_live_10y')}")
        print(f"tier_calibration: {payload.get('tier_calibration')}")
        return 0
    except Exception as e:  # noqa: BLE001
        fail = {
            "error": f"{type(e).__name__}: {e}",
            "finished_at": datetime.now().isoformat(),
        }
        out_path.write_text(json.dumps(fail, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"error: {fail['error']}")
        print(f"output: {out_path}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
