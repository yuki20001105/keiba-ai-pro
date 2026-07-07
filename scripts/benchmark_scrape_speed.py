#!/usr/bin/env python3
"""Small-scale scrape benchmark and 10-year runtime estimator.

Safety policy:
- Never runs 10-year live scraping.
- Live scraping is capped by --max-live-days (default: 7 days).
- Uses fetch pipeline safeguards (rate limit, backoff, Retry-After, circuit breaker).
- Dry-run mode performs no HTTP access.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
import sys

import aiohttp

ROOT_DIR = Path(__file__).resolve().parent.parent
PY_API_DIR = ROOT_DIR / "python-api"
if str(PY_API_DIR) not in sys.path:
    sys.path.insert(0, str(PY_API_DIR))

from scraping.constants import get_random_headers  # type: ignore
from scraping.fetch_pipeline import estimate_fetch_plan, fetch_text, get_fetch_metrics  # type: ignore


DEFAULT_OUTPUT = ROOT_DIR / "reports" / "scrape_benchmark_summary.json"
DAYS_PER_YEAR = 365
DAYS_10_YEARS = 3650


@dataclass
class RangeSpec:
    label: str
    start_date: str
    end_date: str
    mode: str  # dry-run | live


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


def _build_urls(start_date: str, end_date: str) -> tuple[list[str], list[str]]:
    dates = _iter_dates(start_date, end_date)
    urls: list[str] = []
    resume_keys: list[str] = []
    for d in dates:
        urls.append(f"https://db.netkeiba.com/race/list/{d}/")
        urls.append(f"https://race.netkeiba.com/top/race_list_sub.html?kaisai_date={d}")
        resume_keys.append(f"benchmark:{start_date}:{end_date}:{d}:list")
        resume_keys.append(f"benchmark:{start_date}:{end_date}:{d}:sub")
    return urls, resume_keys


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


def _load_previous_live_calibration(path: Path) -> float | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    runs = payload.get("runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list):
        return None

    values: list[float] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        if run.get("mode") != "live":
            continue
        spr = _safe_float(run.get("seconds_per_request"))
        if spr is not None and spr > 0:
            values.append(spr)

    if not values:
        return None
    return sum(values) / len(values)


async def _run_dry(start_date: str, end_date: str) -> dict[str, Any]:
    urls, resume_keys = _build_urls(start_date, end_date)
    plan = estimate_fetch_plan(urls, resume_keys=resume_keys)

    cache_hits = _safe_int(plan.get("cache_hits"))
    resume_hits = _safe_int(plan.get("resume_hits"))
    unique_urls = _safe_int(plan.get("unique_urls"))
    est_req = _safe_int(plan.get("estimated_network_requests"))

    return {
        "mode": "dry-run",
        "total_target_count": _safe_int(plan.get("total_input_urls")),
        "unique_url_count": unique_urls,
        "estimated_request_count": est_req,
        "cache_hit_count": cache_hits,
        "cache_miss_count": max(0, unique_urls - cache_hits),
        "resume_hit_count": resume_hits,
        "actual_network_request_count": 0,
        "retry_count": 0,
        "failed_count": 0,
        "elapsed_seconds": 0.0,
        "consecutive_error_break": False,
    }


async def _run_live(start_date: str, end_date: str, args: argparse.Namespace) -> dict[str, Any]:
    urls, _ = _build_urls(start_date, end_date)

    plan = estimate_fetch_plan(urls, resume_keys=None)
    cache_hits = _safe_int(plan.get("cache_hits"))
    unique_urls = _safe_int(plan.get("unique_urls"))

    # Ensure measured live requests are real network calls.
    get_fetch_metrics(reset=True)

    timeout = aiohttp.ClientTimeout(total=25, connect=8)
    connector = aiohttp.TCPConnector(limit=3, limit_per_host=2)

    consecutive_errors = 0
    consecutive_error_break = False

    t0 = time.perf_counter()
    async with aiohttp.ClientSession(
        timeout=timeout,
        connector=connector,
        headers=get_random_headers(),
    ) as session:
        for i, url in enumerate(urls):
            resume_key = f"benchmark-live:{start_date}:{end_date}:{i}"
            result, _ = await fetch_text(
                session,
                url,
                use_cache=False,
                force_refresh=True,
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

            if result.status in (0, 429, 503):
                consecutive_errors += 1
            else:
                consecutive_errors = 0

            metrics_now = get_fetch_metrics(reset=False)
            if _safe_int(metrics_now.get("network_requests")) > int(args.max_actual_requests):
                break

            if consecutive_errors >= int(args.max_consecutive_errors):
                consecutive_error_break = True
                break

    elapsed = max(0.0, time.perf_counter() - t0)
    metrics = get_fetch_metrics(reset=True)

    return {
        "mode": "live",
        "total_target_count": _safe_int(plan.get("total_input_urls")),
        "unique_url_count": unique_urls,
        "estimated_request_count": _safe_int(plan.get("estimated_network_requests")),
        "cache_hit_count": cache_hits,
        "cache_miss_count": max(0, unique_urls - cache_hits),
        "resume_hit_count": 0,
        "actual_network_request_count": _safe_int(metrics.get("network_requests")),
        "retry_count": _safe_int(metrics.get("retry_count")),
        "failed_count": _failed_count_from_metrics(metrics),
        "elapsed_seconds": elapsed,
        "metrics": metrics,
        "consecutive_error_break": consecutive_error_break,
    }


def _build_presets() -> dict[str, list[tuple[int, str]]]:
    return {
        "small": [
            (1, "dry-run"),
            (1, "live"),
            (7, "dry-run"),
            (7, "live"),
            (30, "dry-run"),
        ],
        "estimate-10y": [
            (1, "dry-run"),
            (1, "live"),
            (7, "dry-run"),
            (7, "live"),
            (30, "dry-run"),
            (365, "dry-run"),
            (3650, "dry-run"),
        ],
    }


def _ranges_for_preset(preset: str, anchor: date) -> list[RangeSpec]:
    presets = _build_presets()
    if preset not in presets:
        raise ValueError(f"unsupported preset: {preset}")

    rows: list[RangeSpec] = []
    for days, mode in presets[preset]:
        start = anchor - timedelta(days=days - 1)
        rows.append(RangeSpec(label=f"{days}d", start_date=_fmt_date(start), end_date=_fmt_date(anchor), mode=mode))
    return rows


def _single_range(start_date: str, end_date: str, mode: str) -> list[RangeSpec]:
    days = _inclusive_days(start_date, end_date)
    return [RangeSpec(label=f"{days}d", start_date=start_date, end_date=end_date, mode=mode)]


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    started_at = datetime.now().isoformat()
    anchor = _parse_date(args.anchor_date) if args.anchor_date else (date.today() - timedelta(days=1))

    if args.preset:
        ranges = _ranges_for_preset(args.preset, anchor)
    else:
        if not (args.start_date and args.end_date and args.mode):
            raise ValueError("either --preset or (--start-date --end-date --mode) is required")
        ranges = _single_range(args.start_date, args.end_date, args.mode)

    for r in ranges:
        days = _inclusive_days(r.start_date, r.end_date)
        if r.mode == "live" and days > int(args.max_live_days):
            raise ValueError(f"live range too large ({days} days). max_live_days={args.max_live_days}")

    runs: list[dict[str, Any]] = []
    live_spr_values: list[float] = []
    prior_calibration = _load_previous_live_calibration(Path(args.output))

    benchmark_circuit_breaker = False

    for spec in ranges:
        if benchmark_circuit_breaker and spec.mode == "live":
            runs.append(
                {
                    "date_range": _date_range_label(spec.start_date, spec.end_date),
                    "label": spec.label,
                    "mode": spec.mode,
                    "status": "skipped",
                    "reason": "benchmark-circuit-breaker-open",
                }
            )
            continue

        if spec.mode == "dry-run":
            summary = await _run_dry(spec.start_date, spec.end_date)
        else:
            summary = await _run_live(spec.start_date, spec.end_date, args)

        days = _inclusive_days(spec.start_date, spec.end_date)
        elapsed_seconds = _safe_float(summary.get("elapsed_seconds")) or 0.0
        actual_requests = _safe_int(summary.get("actual_network_request_count"))
        retry_count = _safe_int(summary.get("retry_count"))
        failed_count = _safe_int(summary.get("failed_count"))

        seconds_per_request = None
        if actual_requests > 0 and elapsed_seconds > 0:
            seconds_per_request = elapsed_seconds / actual_requests
            live_spr_values.append(seconds_per_request)

        seconds_per_day = elapsed_seconds / max(1, days) if elapsed_seconds > 0 else None
        estimated_1y = seconds_per_day * DAYS_PER_YEAR if seconds_per_day is not None else None
        estimated_10y = seconds_per_day * DAYS_10_YEARS if seconds_per_day is not None else None

        multiplier = _dynamic_multiplier(
            float(args.conservative_multiplier),
            retry_count=retry_count,
            failed_count=failed_count,
            actual_requests=actual_requests,
        )
        est_10y_cons = estimated_10y * multiplier if estimated_10y is not None else None

        row = {
            "date_range": _date_range_label(spec.start_date, spec.end_date),
            "label": spec.label,
            "mode": spec.mode,
            "status": "completed",
            "total_target_count": _safe_int(summary.get("total_target_count")),
            "unique_url_count": _safe_int(summary.get("unique_url_count")),
            "estimated_request_count": _safe_int(summary.get("estimated_request_count")),
            "cache_hit_count": _safe_int(summary.get("cache_hit_count")),
            "cache_miss_count": _safe_int(summary.get("cache_miss_count")),
            "resume_hit_count": _safe_int(summary.get("resume_hit_count")),
            "actual_network_request_count": actual_requests,
            "retry_count": retry_count,
            "failed_count": failed_count,
            "elapsed_seconds": elapsed_seconds,
            "seconds_per_request": seconds_per_request,
            "seconds_per_day": seconds_per_day,
            "estimated_1_year_seconds": estimated_1y,
            "estimated_10_year_seconds": estimated_10y,
            "conservative_multiplier": multiplier,
            "estimated_10_year_conservative_seconds": est_10y_cons,
        }

        if spec.mode == "live":
            row["fetch_metrics"] = summary.get("metrics", {})
            row["consecutive_error_break"] = bool(summary.get("consecutive_error_break"))

        if spec.mode == "live":
            if row["actual_network_request_count"] > int(args.max_actual_requests):
                benchmark_circuit_breaker = True
            if row.get("consecutive_error_break"):
                benchmark_circuit_breaker = True

        runs.append(row)

    calibration_spr = None
    if live_spr_values:
        calibration_spr = sum(live_spr_values) / len(live_spr_values)
    elif prior_calibration:
        calibration_spr = prior_calibration

    # Fill dry-run estimated runtime using measured seconds/request.
    for row in runs:
        if not isinstance(row, dict) or row.get("mode") != "dry-run":
            continue
        if not calibration_spr:
            row["estimation_source"] = "no-live-calibration"
            continue

        est_req = _safe_int(row.get("estimated_request_count"))
        if est_req <= 0:
            row["estimation_source"] = "live-calibrated"
            continue

        est_seconds = est_req * calibration_spr
        parts = str(row.get("date_range", "")).split("-")
        days = _inclusive_days(parts[0], parts[1]) if len(parts) == 2 else 1
        sec_per_day = est_seconds / max(1, days)

        multiplier = _dynamic_multiplier(
            float(args.conservative_multiplier),
            retry_count=_safe_int(row.get("retry_count")),
            failed_count=_safe_int(row.get("failed_count")),
            actual_requests=_safe_int(row.get("actual_network_request_count")),
        )

        row["elapsed_seconds"] = est_seconds
        row["seconds_per_request"] = calibration_spr
        row["seconds_per_day"] = sec_per_day
        row["estimated_1_year_seconds"] = sec_per_day * DAYS_PER_YEAR
        row["estimated_10_year_seconds"] = sec_per_day * DAYS_10_YEARS
        row["conservative_multiplier"] = multiplier
        row["estimated_10_year_conservative_seconds"] = row["estimated_10_year_seconds"] * multiplier
        row["estimation_source"] = "live-calibrated"

    has_live_10y = any(
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
            "max_live_days": int(args.max_live_days),
            "max_actual_requests": int(args.max_actual_requests),
            "max_consecutive_errors": int(args.max_consecutive_errors),
            "base_conservative_multiplier": float(args.conservative_multiplier),
            "rate_limit_backoff_circuit_breaker": "enabled in fetch_pipeline",
            "dry_run_http_access": False,
            "live_10y_allowed": False,
        },
        "safety": {
            "contains_live_10y": bool(has_live_10y),
            "benchmark_circuit_breaker_triggered": bool(benchmark_circuit_breaker),
        },
        "calibration": {
            "seconds_per_request": calibration_spr,
            "source": "current-live" if live_spr_values else ("previous-output" if prior_calibration else "none"),
            "live_sample_count": len(live_spr_values),
        },
        "runs": runs,
    }

    if payload["safety"]["contains_live_10y"]:
        raise RuntimeError("safety violation: 10-year live scraping is forbidden")

    return payload


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Scrape speed benchmark and 10-year estimator")
    p.add_argument("--preset", choices=["small", "estimate-10y"], default=None)
    p.add_argument("--start-date", default=None)
    p.add_argument("--end-date", default=None)
    p.add_argument("--mode", choices=["dry-run", "live"], default=None)
    p.add_argument("--anchor-date", default=None, help="Anchor date (YYYYMMDD). default: yesterday")
    p.add_argument("--max-live-days", type=int, default=7)
    p.add_argument("--max-actual-requests", type=int, default=200)
    p.add_argument("--max-consecutive-errors", type=int, default=3)
    p.add_argument("--conservative-multiplier", type=float, default=1.5)
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
        print(f"calibrated_seconds_per_request: {payload.get('calibration', {}).get('seconds_per_request')}")
        print(f"contains_live_10y: {payload.get('safety', {}).get('contains_live_10y')}")
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
