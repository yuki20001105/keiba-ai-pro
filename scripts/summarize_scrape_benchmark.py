#!/usr/bin/env python3
"""Summarize scrape benchmark results for quick CLI review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_PATHS = [
    ROOT_DIR / "reports" / "scrape_benchmark_summary.json",
    ROOT_DIR / "scrape_benchmark_summary.json",
]
DISPLAY_TIERS = ["list", "race-detail", "horse-detail"]
FULL_ESTIMATE_TIER = "full-estimate"


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _safe_str(value: Any) -> str:
    if value is None:
        return "-"
    s = str(value).strip()
    return s if s else "-"


def _fmt_num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def _humanize_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    total = int(round(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)

    parts: list[str] = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0 or days > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or hours > 0 or days > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def _risk_level_severity(level: str) -> int:
    mapping = {"low": 0, "medium": 1, "high": 2}
    return mapping.get(level.lower(), -1)


def _max_risk_level(levels: list[str]) -> str:
    if not levels:
        return "-"
    ordered = sorted(levels, key=_risk_level_severity, reverse=True)
    return ordered[0]


def _resolve_input_path(input_path: str | None) -> Path | None:
    if input_path:
        p = Path(input_path)
        return p if p.exists() else None

    for p in DEFAULT_REPORT_PATHS:
        if p.exists():
            return p
    return None


def _build_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    tier_estimates = payload.get("tier_estimates_10y")
    if not isinstance(tier_estimates, dict):
        return []

    rows: list[dict[str, Any]] = []
    for tier in DISPLAY_TIERS:
        src = tier_estimates.get(tier)
        if not isinstance(src, dict):
            rows.append(
                {
                    "tier": tier,
                    "estimated_10_year_seconds": None,
                    "estimated_10_year_conservative_seconds": None,
                    "human_time": "-",
                    "data_quality_score": None,
                    "quality_risk_level": "-",
                    "estimated_10_year_parse_failures": None,
                    "estimated_10_year_invalid_records": None,
                }
            )
            continue

        est = _safe_float(src.get("estimated_10_year_seconds"))
        row = {
            "tier": tier,
            "estimated_10_year_seconds": est,
            "estimated_10_year_conservative_seconds": _safe_float(src.get("estimated_10_year_conservative_seconds")),
            "human_time": _humanize_seconds(est),
            "data_quality_score": _safe_float(src.get("data_quality_score")),
            "quality_risk_level": _safe_str(src.get("quality_risk_level")),
            "estimated_10_year_parse_failures": _safe_float(src.get("estimated_10_year_parse_failures")),
            "estimated_10_year_invalid_records": _safe_float(src.get("estimated_10_year_invalid_records")),
        }
        rows.append(row)

    available = [r for r in rows if r["estimated_10_year_seconds"] is not None]
    if available:
        full_est = sum(_safe_float(r.get("estimated_10_year_seconds")) or 0.0 for r in available)
        full_est_cons = sum(_safe_float(r.get("estimated_10_year_conservative_seconds")) or 0.0 for r in available)
        parse_fail = sum(_safe_float(r.get("estimated_10_year_parse_failures")) or 0.0 for r in available)
        invalid = sum(_safe_float(r.get("estimated_10_year_invalid_records")) or 0.0 for r in available)
        quality_scores = [_safe_float(r.get("data_quality_score")) for r in available]
        quality_scores = [x for x in quality_scores if x is not None]
        risks = [_safe_str(r.get("quality_risk_level")) for r in available if _safe_str(r.get("quality_risk_level")) != "-"]

        rows.append(
            {
                "tier": FULL_ESTIMATE_TIER,
                "estimated_10_year_seconds": full_est,
                "estimated_10_year_conservative_seconds": full_est_cons,
                "human_time": _humanize_seconds(full_est),
                "data_quality_score": (sum(quality_scores) / len(quality_scores)) if quality_scores else None,
                "quality_risk_level": _max_risk_level(risks),
                "estimated_10_year_parse_failures": parse_fail,
                "estimated_10_year_invalid_records": invalid,
            }
        )

    return rows


def _find_bottleneck(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [r for r in rows if r.get("tier") != FULL_ESTIMATE_TIER and _safe_float(r.get("estimated_10_year_seconds")) is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda r: _safe_float(r.get("estimated_10_year_seconds")) or -1.0)


def _find_quality_risks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    for row in rows:
        if row.get("tier") == FULL_ESTIMATE_TIER:
            continue
        level = _safe_str(row.get("quality_risk_level")).lower()
        score = _safe_float(row.get("data_quality_score"))
        if level in {"medium", "high"}:
            risks.append(row)
            continue
        if score is not None and score < 95.0:
            risks.append(row)
    return risks


def _print_table(rows: list[dict[str, Any]]) -> None:
    headers = [
        "tier",
        "est_10y_sec",
        "est_10y_cons_sec",
        "human_time",
        "quality_score",
        "quality_risk",
        "est_parse_fail",
        "est_invalid",
    ]
    table_rows: list[list[str]] = []
    for row in rows:
        table_rows.append(
            [
                _safe_str(row.get("tier")),
                _fmt_num(_safe_float(row.get("estimated_10_year_seconds"))),
                _fmt_num(_safe_float(row.get("estimated_10_year_conservative_seconds"))),
                _safe_str(row.get("human_time")),
                _fmt_num(_safe_float(row.get("data_quality_score"))),
                _safe_str(row.get("quality_risk_level")),
                _fmt_num(_safe_float(row.get("estimated_10_year_parse_failures"))),
                _fmt_num(_safe_float(row.get("estimated_10_year_invalid_records"))),
            ]
        )

    widths = [len(h) for h in headers]
    for row in table_rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def format_row(cells: list[str]) -> str:
        return " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells))

    print(format_row(headers))
    print("-+-".join("-" * w for w in widths))
    for row in table_rows:
        print(format_row(row))


def _build_output(payload: dict[str, Any], input_path: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    bottleneck = _find_bottleneck(rows)
    quality_risks = _find_quality_risks(rows)
    return {
        "input": str(input_path),
        "started_at": payload.get("started_at"),
        "finished_at": payload.get("finished_at"),
        "preset": payload.get("preset"),
        "rows": rows,
        "bottleneck_tier": bottleneck,
        "quality_risk_tiers": quality_risks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize scrape benchmark summary JSON")
    parser.add_argument("--input", default=None, help="Path to scrape benchmark summary JSON")
    parser.add_argument("--json", action="store_true", help="Print summary as JSON")
    args = parser.parse_args()

    input_path = _resolve_input_path(args.input)
    if input_path is None:
        print("WARN: summary JSON not found. Tried reports/scrape_benchmark_summary.json and scrape_benchmark_summary.json")
        return 0

    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: failed to read summary JSON: {type(exc).__name__}: {exc}")
        return 1

    if not isinstance(payload, dict):
        print("WARN: summary JSON root must be an object")
        return 1

    rows = _build_rows(payload)
    if not rows:
        print("WARN: tier_estimates_10y was not found or empty")
        return 0

    output = _build_output(payload, input_path, rows)

    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0

    print(f"input: {input_path}")
    _print_table(rows)

    bottleneck = output.get("bottleneck_tier")
    if isinstance(bottleneck, dict):
        print(
            "bottleneck_tier: "
            f"{_safe_str(bottleneck.get('tier'))} "
            f"({_fmt_num(_safe_float(bottleneck.get('estimated_10_year_seconds')))} sec)"
        )
    else:
        print("bottleneck_tier: -")

    risk_tiers = output.get("quality_risk_tiers")
    if isinstance(risk_tiers, list) and risk_tiers:
        names = ", ".join(_safe_str(r.get("tier")) for r in risk_tiers if isinstance(r, dict))
        print(f"quality_risk_tiers: {names}")
    else:
        print("quality_risk_tiers: none")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
