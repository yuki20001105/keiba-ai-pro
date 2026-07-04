from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path


def write_scraping_report(
    *,
    start_date: str,
    end_date: str,
    processed_dates: int,
    saved_races: int,
    saved_horses: int,
    elapsed_min: float,
    queue_counts: dict[str, int] | None,
    errors: int,
    task_totals: dict[str, int] | None = None,
    quality_counts: dict[str, int] | None = None,
    severity_totals: dict[str, int] | None = None,
    policy_totals: dict[str, int] | None = None,
    lineage_records: list[dict] | None = None,
) -> Path:
    reports_dir = Path(__file__).parent.parent.parent / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / "scraping_report.md"

    lines = [
        "# Scraping Summary",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Range: {start_date} - {end_date}",
        "",
        "## Metrics",
        f"- Processed dates: {processed_dates}",
        f"- Saved races: {saved_races}",
        f"- Saved horses: {saved_horses}",
        f"- Errors: {errors}",
        f"- Elapsed(min): {elapsed_min:.1f}",
    ]

    if queue_counts:
        lines.append("")
        lines.append("## Queue")
        for k in ("PENDING", "RUNNING", "SUCCESS", "FAILED", "SKIP"):
            lines.append(f"- {k}: {int(queue_counts.get(k, 0))}")

    if task_totals:
        lines.append("")
        lines.append("## Task Totals")
        for k in ("TOTAL", "SUCCESS", "FAILED", "SKIP"):
            lines.append(f"- {k}: {int(task_totals.get(k, 0))}")

    if quality_counts:
        lines.append("")
        lines.append("## Data Quality (Error Codes)")
        for code in sorted(quality_counts.keys()):
            lines.append(f"- {code}: {int(quality_counts.get(code, 0))}")

    if severity_totals:
        lines.append("")
        lines.append("## Severity Totals")
        for k in ("INFO", "WARNING", "ERROR", "FATAL"):
            lines.append(f"- {k}: {int(severity_totals.get(k, 0))}")

    if policy_totals:
        lines.append("")
        lines.append("## Recovery Policy Totals")
        for k in ("RETRY", "SKIP", "ABORT", "CONTINUE"):
            lines.append(f"- {k}: {int(policy_totals.get(k, 0))}")

    if lineage_records:
        lines.append("")
        lines.append("## Data Lineage")
        lines.append("| date | downloader_ids | parser_ids | validator_in | validator_out | tasks_total | tasks_success | tasks_failed | tasks_skip | repository_saved | download_time_sec | parse_time_sec | validate_time_sec | insert_time_sec | cache_hit | parser_version | rule_version |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|")
        for r in lineage_records:
            lines.append(
                f"| {r.get('date','')} | {int(r.get('downloader_ids',0))} | {int(r.get('parser_ids',0))} | "
                f"{int(r.get('validator_in',0))} | {int(r.get('validator_out',0))} | {int(r.get('tasks_total',0))} | "
                f"{int(r.get('tasks_success',0))} | {int(r.get('tasks_failed',0))} | {int(r.get('tasks_skip',0))} | "
                f"{int(r.get('repository_saved',0))} | {float(r.get('download_time_sec',0.0)):.3f} | "
                f"{float(r.get('parse_time_sec',0.0)):.3f} | {float(r.get('validate_time_sec',0.0)):.3f} | "
                f"{float(r.get('insert_time_sec',0.0)):.3f} | {int(r.get('cache_hit',0))} | "
                f"{r.get('parser_version','')} | {r.get('rule_version','')} |"
            )

    out.write_text("\n".join(lines), encoding="utf-8")

    # JSON artifact for CI/automation
    out_json = reports_dir / "scraping_report.json"
    out_json.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "range": {"start": start_date, "end": end_date},
                "metrics": {
                    "processed_dates": int(processed_dates),
                    "saved_races": int(saved_races),
                    "saved_horses": int(saved_horses),
                    "errors": int(errors),
                    "elapsed_min": float(elapsed_min),
                },
                "queue_counts": queue_counts or {},
                "task_totals": task_totals or {},
                "quality_counts": quality_counts or {},
                "severity_totals": severity_totals or {},
                "policy_totals": policy_totals or {},
                "lineage_records": lineage_records or [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # CSV artifact for analysis/BI
    out_csv = reports_dir / "scraping_metrics.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "date",
                "downloader_ids",
                "parser_ids",
                "validator_in",
                "validator_out",
                "tasks_total",
                "tasks_success",
                "tasks_failed",
                "tasks_skip",
                "repository_saved",
                "download_time_sec",
                "parse_time_sec",
                "validate_time_sec",
                "insert_time_sec",
                "cache_hit",
                "parser_version",
                "rule_version",
            ],
        )
        writer.writeheader()
        for r in lineage_records or []:
            writer.writerow(
                {
                    "date": r.get("date", ""),
                    "downloader_ids": int(r.get("downloader_ids", 0)),
                    "parser_ids": int(r.get("parser_ids", 0)),
                    "validator_in": int(r.get("validator_in", 0)),
                    "validator_out": int(r.get("validator_out", 0)),
                    "tasks_total": int(r.get("tasks_total", 0)),
                    "tasks_success": int(r.get("tasks_success", 0)),
                    "tasks_failed": int(r.get("tasks_failed", 0)),
                    "tasks_skip": int(r.get("tasks_skip", 0)),
                    "repository_saved": int(r.get("repository_saved", 0)),
                    "download_time_sec": float(r.get("download_time_sec", 0.0)),
                    "parse_time_sec": float(r.get("parse_time_sec", 0.0)),
                    "validate_time_sec": float(r.get("validate_time_sec", 0.0)),
                    "insert_time_sec": float(r.get("insert_time_sec", 0.0)),
                    "cache_hit": int(r.get("cache_hit", 0)),
                    "parser_version": r.get("parser_version", ""),
                    "rule_version": r.get("rule_version", ""),
                }
            )

    return out
