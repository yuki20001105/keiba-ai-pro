#!/usr/bin/env python3
"""Fail-closed Production observation ledger operations.

This script never enables betting, training, or activation.  It accepts only a
Production limited-observation runtime boundary and keeps cache rebuilds local.
Result reconciliation is append-only and requires an explicit approval
reference on every invocation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
PYTHON_API = ROOT / "python-api"
if str(PYTHON_API) not in sys.path:
    sys.path.insert(0, str(PYTHON_API))

from observation.cache_integrity import exercise_cache_rebuild  # noqa: E402
from observation.contracts import ObservationContractError, canonical_sha256  # noqa: E402
from observation.reconcile import (  # noqa: E402
    reconcile_available_results,
    reconcile_result_snapshot,
)
from observation.service import (  # noqa: E402
    ObservationConfig,
    ObservationGateway,
    join_progress_rows,
)


REPORTS = (ROOT / "reports").resolve()
APPROVAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{15,199}$")


class ProductionRestGateway:
    """Minimal service-key PostgREST adapter used by Production operations."""

    def __init__(self, base_url: str, service_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "User-Agent": "keiba-ai-pro-production-observation-ops/1.0",
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
        }

    def _json(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> Any:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self._base_url}{path}",
            data=body,
            headers=self._headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ObservationContractError("observation-rest-request-failed") from exc

    def select(
        self,
        table: str,
        columns: str = "*",
        *,
        max_rows: int = 100_000,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page_size = 1_000
        while len(rows) < max_rows:
            query = urlencode(
                {
                    "select": columns,
                    "limit": page_size,
                    "offset": len(rows),
                },
                safe=",*",
            )
            payload = self._json(f"/rest/v1/{table}?{query}")
            if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
                raise ObservationContractError("observation-read-invalid")
            rows.extend(dict(row) for row in payload)
            if len(payload) < page_size:
                break
        if len(rows) >= max_rows:
            raise ObservationContractError("observation-read-limit-reached")
        return rows

    def select_in(
        self,
        table: str,
        columns: str,
        key: str,
        values: Iterable[str],
    ) -> list[dict[str, Any]]:
        items = list(dict.fromkeys(str(value) for value in values))
        rows: list[dict[str, Any]] = []
        for offset in range(0, len(items), 100):
            selected = items[offset : offset + 100]
            if not all(re.fullmatch(r"[A-Za-z0-9._:-]+", item) for item in selected):
                raise ObservationContractError("observation-filter-invalid")
            query = urlencode(
                {"select": columns, key: f"in.({','.join(selected)})"},
                safe=",*().:-",
            )
            payload = self._json(f"/rest/v1/{table}?{query}")
            if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
                raise ObservationContractError("observation-read-invalid")
            rows.extend(dict(row) for row in payload)
        return rows

    def record_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._json(
            "/rest/v1/rpc/record_phase3n_result_observation",
            method="POST",
            payload={"p_result": payload},
        )
        if not isinstance(response, list) or len(response) != 1 or not isinstance(response[0], dict):
            raise ObservationContractError("result-observation-write-failed")
        row = dict(response[0])
        if row.get("mutation_code") not in {"inserted", "duplicate"}:
            raise ObservationContractError("result-observation-rejected")
        return row


def _safe_report_path(value: Path) -> Path:
    resolved = (value if value.is_absolute() else ROOT / value).resolve(strict=False)
    try:
        resolved.relative_to(REPORTS)
    except ValueError as exc:
        raise ObservationContractError("output-must-be-under-reports") from exc
    if resolved.suffix.lower() != ".json" or resolved.is_symlink():
        raise ObservationContractError("output-path-invalid")
    return resolved


def _write_json_atomic(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2)
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _production_gateway() -> tuple[ProductionRestGateway, ObservationConfig]:
    config = ObservationConfig.from_env()
    if config.app_env != "production":
        raise ObservationContractError("production-observation-boundary-required")
    config.require_environment_boundary()
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not service_key:
        raise ObservationContractError("production-service-key-required")
    return ProductionRestGateway(config.supabase_url, service_key), config


def _production_rows(gateway: ObservationGateway) -> list[dict]:
    predictions, _results, _attempts = join_progress_rows(gateway)
    rows = [row for row in predictions if row.get("source_environment") == "production"]
    if len(rows) != len(predictions):
        raise ObservationContractError("production-observation-mixed-environment")
    return rows


def command_cache_integrity(args: argparse.Namespace) -> dict:
    gateway, config = _production_gateway()
    cache_path = _safe_report_path(args.cache_output)
    evidence_path = _safe_report_path(args.evidence_output)

    evidence = exercise_cache_rebuild(cache_path, lambda: _production_rows(gateway))
    report = {
        **evidence,
        "candidate_commit_sha": config.candidate_commit_sha,
        "source_environment": "production",
        "production_database_changed": False,
        "cache": cache_path.relative_to(ROOT).as_posix(),
    }
    _write_json_atomic(evidence_path, report)
    return {
        "success": report["success"],
        "evidence": evidence_path.relative_to(ROOT).as_posix(),
        "candidate_commit_sha": config.candidate_commit_sha,
        "source_row_count": report["source_row_count"],
        "cache_digest_sha256": report["cache_digest_after_sha256"],
        "database_unchanged": report["database_unchanged"],
        "rebuild_ms": report["rebuild_ms"],
    }


def command_reconcile(args: argparse.Namespace) -> dict:
    if APPROVAL_RE.fullmatch(args.approval_reference or "") is None:
        raise ObservationContractError("production-settlement-approval-required")
    gateway, config = _production_gateway()
    outcome = reconcile_available_results(gateway, source_environment="production")
    report = {
        "schema": "phase3n-production-result-reconciliation",
        "schema_version": 1,
        "source_environment": "production",
        "candidate_commit_sha": config.candidate_commit_sha,
        "approval_reference": args.approval_reference,
        "append_only": True,
        "automatic_betting_enabled": False,
        **outcome,
        "success": True,
    }
    evidence_path = _safe_report_path(args.evidence_output)
    _write_json_atomic(evidence_path, report)
    return {
        "success": True,
        "evidence": evidence_path.relative_to(ROOT).as_posix(),
        **outcome,
    }


async def _fetch_result_snapshot(race_id: str, race_date: str) -> dict[str, Any]:
    try:
        import aiohttp
        from scraping.race import scrape_current_race_result, scrape_race_full  # type: ignore
    except ImportError as exc:
        raise ObservationContractError("result-source-client-unavailable") from exc
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(
        headers={"User-Agent": "keiba-ai-pro-production-settlement/1.0"},
        timeout=timeout,
    ) as session:
        snapshot = await scrape_current_race_result(
            session, race_id, force_refresh=True
        )
        if not snapshot:
            snapshot = await scrape_race_full(
                session,
                race_id,
                date_hint=race_date,
                force_refresh=True,
            )
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("horses"), list):
        raise ObservationContractError("settlement-result-source-unavailable")
    return snapshot


def command_settle_race(args: argparse.Namespace) -> dict:
    if APPROVAL_RE.fullmatch(args.approval_reference or "") is None:
        raise ObservationContractError("production-settlement-approval-required")
    if re.fullmatch(r"\d{12}", args.race_id or "") is None:
        raise ObservationContractError("settlement-race-id-invalid")
    if re.fullmatch(r"\d{8}", args.race_date or "") is None:
        raise ObservationContractError("settlement-race-date-invalid")
    gateway, config = _production_gateway()
    snapshot = asyncio.run(_fetch_result_snapshot(args.race_id, args.race_date))
    race_info = snapshot.get("race_info") if isinstance(snapshot.get("race_info"), dict) else {}
    horses = [dict(row) for row in snapshot.get("horses", []) if isinstance(row, dict)]
    payouts = []
    for row in snapshot.get("payouts", []):
        if not isinstance(row, dict):
            continue
        payouts.append(
            {
                "bet_type": row.get("bet_type"),
                "combination": row.get("combination") or row.get("combinations"),
                "payout": row.get("payout"),
            }
        )
    observed_at = datetime.now(timezone.utc)
    outcome = reconcile_result_snapshot(
        gateway,
        race_id=args.race_id,
        horse_rows=horses,
        payout_rows=payouts,
        settled_at=observed_at,
        source_environment="production",
    )
    source_digest = canonical_sha256(
        {
            "race_id": args.race_id,
            "race_date": args.race_date,
            "race_info": race_info,
            "horses": horses,
            "payouts": payouts,
        }
    )
    report = {
        "schema": "phase3n-production-result-settlement",
        "schema_version": 1,
        "source_environment": "production",
        "source_url": f"https://race.netkeiba.com/race/result.html?race_id={args.race_id}",
        "source_observed_at": observed_at.isoformat(),
        "source_payload_sha256": source_digest,
        "candidate_commit_sha": config.candidate_commit_sha,
        "race_id": args.race_id,
        "race_date": args.race_date,
        "approval_reference": args.approval_reference,
        "append_only": True,
        "automatic_betting_enabled": False,
        **outcome,
        "success": True,
    }
    evidence_path = _safe_report_path(args.evidence_output)
    _write_json_atomic(evidence_path, report)
    return {
        "success": True,
        "evidence": evidence_path.relative_to(ROOT).as_posix(),
        "source_payload_sha256": source_digest,
        **outcome,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Operate the fail-closed Production Phase3N observation ledger."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    integrity = subparsers.add_parser("cache-integrity")
    integrity.add_argument(
        "--cache-output",
        type=Path,
        default=Path("reports/phase3n_production_observation_cache.json"),
    )
    integrity.add_argument(
        "--evidence-output",
        type=Path,
        default=Path("reports/phase3n_production_cache_integrity_evidence.json"),
    )
    integrity.set_defaults(func=command_cache_integrity)
    reconcile = subparsers.add_parser("reconcile-results")
    reconcile.add_argument("--approval-reference", required=True)
    reconcile.add_argument(
        "--evidence-output",
        type=Path,
        default=Path("reports/phase3n_production_result_reconciliation.json"),
    )
    reconcile.set_defaults(func=command_reconcile)
    settle = subparsers.add_parser("settle-race")
    settle.add_argument("--race-id", required=True)
    settle.add_argument("--race-date", required=True)
    settle.add_argument("--approval-reference", required=True)
    settle.add_argument(
        "--evidence-output",
        type=Path,
        default=Path("reports/phase3n_production_result_settlement.json"),
    )
    settle.set_defaults(func=command_settle_race)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = args.func(args)
    except ObservationContractError as exc:
        print(json.dumps({"success": False, "failure_code": exc.code}, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=True, allow_nan=False, sort_keys=True))
    return 0 if result.get("success") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
