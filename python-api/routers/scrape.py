"""
スクレイピングエンドポイント
POST /api/scrape/start  → 非同期ジョブ開始
GET  /api/scrape/status/{job_id}
POST /api/scrape         → レガシー同期スクレイプ
POST /api/rescrape_incomplete
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse

from app_config import (  # type: ignore
    ULTIMATE_DB,
    NETKEIBA_RACE_WRITE_ENABLED,
    ALLOW_STAGING_WRITE,
    APP_ENV,
    logger,
)
from deps.auth import require_admin  # type: ignore
from models import ScrapeRequest, ScrapeResponse, RescrapeResponse  # type: ignore
from scraping.constants import SCRAPE_HEADERS  # type: ignore
from scraping.fetch_pipeline import fetch_text  # type: ignore
from scraping.jobs import _scrape_jobs, _JOBS_LOCK, _purge_old_jobs, _run_scrape_job, get_job  # type: ignore
from scraping.race import scrape_race_full  # type: ignore
from scraping.storage import _save_race_to_ultimate_db  # type: ignore

router = APIRouter()
_SCRAPE_SERVICE_URL = os.environ.get("SCRAPE_SERVICE_URL", "http://localhost:8001")
_WRITE_TARGET_TABLE_WHITELIST = {"races", "race_results", "race_payouts"}
_WRITE_ROW_LIMITS = {
    "races": 1,
    "race_results": 30,
    "race_payouts": 100,
}
_SANDBOX_TABLE_MAP = {
    "races": "sandbox_netkeiba_races",
    "race_results": "sandbox_netkeiba_race_results",
    "race_payouts": "sandbox_netkeiba_race_payouts",
}


def _is_text_type_compatible(type_decl: str) -> bool:
    t = (type_decl or "").strip().upper()
    if not t:
        return True
    return any(x in t for x in ("TEXT", "CHAR", "CLOB", "JSON", "VARCHAR", "NCHAR", "NVARCHAR", "STRING"))


def _is_timestamp_type_compatible(type_decl: str) -> bool:
    t = (type_decl or "").strip().upper()
    if not t:
        return True
    return any(x in t for x in ("TEXT", "DATETIME", "TIMESTAMP", "DATE", "NUMERIC", "INTEGER", "REAL"))


def _get_table_column_types(conn: sqlite3.Connection, table_name: str) -> dict[str, str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    out: dict[str, str] = {}
    for row in rows:
        try:
            col = str(row[1])
            decl = str(row[2] or "")
            out[col] = decl
        except Exception:
            continue
    return out


def _detect_base_table_references(conn: sqlite3.Connection, table_name: str) -> list[str]:
    rows = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE tbl_name=? AND sql IS NOT NULL",
        (table_name,),
    ).fetchall()
    found: list[str] = []
    for row in rows:
        if not isinstance(row, tuple) or len(row) < 3:
            continue
        sql = str(row[2] or "").lower()
        for base_table in _WRITE_TARGET_TABLE_WHITELIST:
            if re.search(rf"\b{re.escape(base_table.lower())}\b", sql):
                found.append(base_table)
    return sorted(set(found))


def _run_sandbox_precheck() -> dict[str, Any]:
    expected_tables = [
        "sandbox_netkeiba_races",
        "sandbox_netkeiba_race_results",
        "sandbox_netkeiba_race_payouts",
    ]
    required_columns_common = [
        "race_id",
        "created_at",
        "idempotency_key",
        "payload_hash",
        "audit_payload",
    ]
    base = {
        "success": False,
        "status": "unavailable",
        "service": "netkeiba-race-sandbox-precheck",
        "target_mode": "sandbox",
        "write_performed": False,
        "tables": {},
        "expected_tables": expected_tables,
        "required_columns": {
            "common": required_columns_common,
            "payload": "data|payload",
        },
        "row_limits": dict(_WRITE_ROW_LIMITS),
        "reason": None,
    }

    try:
        conn = sqlite3.connect(str(ULTIMATE_DB))
        conn.row_factory = sqlite3.Row
    except Exception as e:
        return {
            **base,
            "status": "unavailable",
            "reason": f"failed to open db safely: {e}",
        }

    table_reports: dict[str, Any] = {}
    missing_any = False
    incompatible_any = False
    try:
        for base_table, sandbox_table in _SANDBOX_TABLE_MAP.items():
            exists = _table_exists(conn, sandbox_table)
            row_limit = int(_WRITE_ROW_LIMITS.get(base_table, 0))
            report = {
                "exists": exists,
                "schema_compatible": False,
                "missing_columns": [],
                "type_mismatches": [],
                "row_limit": row_limit,
                "row_limit_supported": False,
                "references_base_tables": [],
                "payload_column": None,
            }

            if not exists:
                report["missing_columns"] = [*required_columns_common, "data|payload"]
                missing_any = True
                table_reports[sandbox_table] = report
                continue

            col_types = _get_table_column_types(conn, sandbox_table)
            cols = set(col_types.keys())

            missing_cols: list[str] = []
            for col in required_columns_common:
                if col not in cols:
                    missing_cols.append(col)

            payload_col = "data" if "data" in cols else ("payload" if "payload" in cols else "")
            if not payload_col:
                missing_cols.append("data|payload")

            type_mismatches: list[str] = []
            if "race_id" in col_types and not _is_text_type_compatible(col_types.get("race_id") or ""):
                type_mismatches.append(f"race_id:{col_types.get('race_id')}")
            if "idempotency_key" in col_types and not _is_text_type_compatible(col_types.get("idempotency_key") or ""):
                type_mismatches.append(f"idempotency_key:{col_types.get('idempotency_key')}")
            if "payload_hash" in col_types and not _is_text_type_compatible(col_types.get("payload_hash") or ""):
                type_mismatches.append(f"payload_hash:{col_types.get('payload_hash')}")
            if "audit_payload" in col_types and not _is_text_type_compatible(col_types.get("audit_payload") or ""):
                type_mismatches.append(f"audit_payload:{col_types.get('audit_payload')}")
            if "created_at" in col_types and not _is_timestamp_type_compatible(col_types.get("created_at") or ""):
                type_mismatches.append(f"created_at:{col_types.get('created_at')}")
            if payload_col and payload_col in col_types and not _is_text_type_compatible(col_types.get(payload_col) or ""):
                type_mismatches.append(f"{payload_col}:{col_types.get(payload_col)}")

            refs = _detect_base_table_references(conn, sandbox_table)
            schema_ok = len(missing_cols) == 0 and len(type_mismatches) == 0 and len(refs) == 0
            if not schema_ok:
                incompatible_any = True

            report.update(
                {
                    "schema_compatible": schema_ok,
                    "missing_columns": missing_cols,
                    "type_mismatches": type_mismatches,
                    "row_limit_supported": schema_ok,
                    "references_base_tables": refs,
                    "payload_column": payload_col or None,
                }
            )
            table_reports[sandbox_table] = report

        if missing_any:
            status = "stopped"
            reason = "sandbox tables are missing"
        elif incompatible_any:
            status = "warn"
            reason = "sandbox table schema is not fully compatible"
        else:
            status = "ready"
            reason = None

        return {
            **base,
            "success": True,
            "status": status,
            "tables": table_reports,
            "reason": reason,
        }
    except Exception as e:
        return {
            **base,
            "status": "unavailable",
            "tables": table_reports,
            "reason": f"sandbox precheck failed safely: {e}",
        }
    finally:
        conn.close()


@router.get("/api/netkeiba/race/sandbox/precheck")
async def netkeiba_race_sandbox_precheck() -> dict[str, Any]:
    """Read-only precheck for sandbox write readiness. No write/readback is executed."""
    return _run_sandbox_precheck()


@router.post("/api/scrape/start")
async def scrape_start(request: ScrapeRequest, _: dict = Depends(require_admin)):
    """スクレイピングをバックグラウンドで開始し、即座に job_id を返す（Admin専用）"""
    job_id = str(uuid.uuid4())[:8]
    with _JOBS_LOCK:
        _purge_old_jobs(_scrape_jobs)
        _scrape_jobs[job_id] = {
            "status": "queued",
            "progress": {"done": 0, "total": 0, "message": "開始待ち"},
            "result": None,
            "error": None,
        }
    try:
        import threading
        def _bg() -> None:
            # Windows の ProactorEventLoop(IOCP) が main loop と干渉しないよう
            # スレッド内では SelectorEventLoop を明示的に使用する
            import asyncio
            import sys
            if sys.platform == "win32":
                loop = asyncio.SelectorEventLoop()
            else:
                loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    _run_scrape_job(
                        job_id,
                        request.start_date,
                        request.end_date,
                        request.force_rescrape,
                        request.dry_run,
                    )
                )
            finally:
                loop.close()
        threading.Thread(target=_bg, daemon=True, name=f"scrape-{job_id}").start()
        logger.info(f"ジョブ {job_id} をスレッドでスケジュール済み")
    except Exception as e:
        logger.error(f"スレッド起動失敗: {e}")
        _scrape_jobs[job_id]["status"] = "error"
        _scrape_jobs[job_id]["error"] = f"タスク起動失敗: {e}"
    return {
        "job_id": job_id,
        "status": _scrape_jobs[job_id]["status"],
        "mode": "dry-run" if request.dry_run else "execute",
    }


@router.get("/api/scrape/status/{job_id}")
async def scrape_status(job_id: str):
    """スクレイピングジョブの進捗・結果を返す（メモリ → SQLite の順で検索）"""
    job = get_job(job_id)
    if not job:
        return {
            "job_id": job_id,
            "status": "not_found",
            "progress": {},
            "result": None,
            "error": f"ジョブ {job_id} が見つかりません（サーバー再起動の可能性）",
        }
    return {
        "job_id": job_id,
        "status": job["status"],
        "progress": job["progress"],
        "result": job.get("result"),
        "error": job.get("error"),
    }


@router.get("/api/netkeiba/race-list")
async def netkeiba_race_list(date: str):
    """Scrape Service の race_list を FastAPI 経由で read-only プロキシする。"""
    date_str = (date or "").strip().replace("-", "")
    if not re.fullmatch(r"\d{8}", date_str):
        raise HTTPException(status_code=400, detail="date は YYYY-MM-DD または YYYYMMDD 形式で指定してください")

    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{_SCRAPE_SERVICE_URL}/scrape/race_list",
                headers={"Content-Type": "application/json"},
                json={"kaisai_date": date_str},
            ) as resp:
                body_text = await resp.text()
                if resp.status >= 400:
                    return JSONResponse(
                        status_code=502,
                        content={
                            "success": False,
                            "error": "scrape service returned error",
                            "status_code": resp.status,
                            "detail": body_text[:500],
                        },
                    )
        data = json.loads(body_text) if body_text else {}
        races = data.get("races") if isinstance(data, dict) else []
        if not isinstance(races, list):
            races = []
        return {
            "success": True,
            "date": date_str,
            "raceIds": races,
            "count": len(races),
            "source": "fastapi_proxy",
        }
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "error": "failed to fetch race list from scrape service",
                "detail": str(e),
            },
        )


@router.get("/api/netkeiba/race/preflight")
async def netkeiba_race_preflight(race_id: str | None = None, date: str | None = None) -> dict:
    """Write path preflight check for /api/netkeiba/race without performing any write."""
    race_id_str = (race_id or "").strip()
    date_str = (date or "").strip().replace("-", "")

    base = {
        "success": False,
        "status": "unavailable",
        "service": "netkeiba-race",
        "race_id": race_id_str,
        "can_scrape": False,
        "can_write": False,
        "write_performed": False,
        "required_params": ["race_id"],
        "provided_params": {
            "race_id": bool(race_id_str),
            "date": bool(date),
        },
        "reason": None,
    }

    if not race_id_str:
        base["reason"] = "race_id is required"
        raise HTTPException(status_code=400, detail=base)

    if not re.fullmatch(r"\d{12}", race_id_str):
        base["reason"] = "race_id must be 12 digits"
        raise HTTPException(status_code=400, detail=base)

    if date and not re.fullmatch(r"\d{8}", date_str):
        base["reason"] = "date must be YYYYMMDD or YYYY-MM-DD format"
        raise HTTPException(status_code=400, detail=base)

    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{_SCRAPE_SERVICE_URL}/scrape/ultimate",
                headers={"Content-Type": "application/json"},
                json={"race_id": race_id_str, "include_details": False},
            ) as resp:
                body_text = await resp.text()
                parsed: dict | None = None
                try:
                    parsed = json.loads(body_text) if body_text else {}
                except Exception:
                    parsed = None

                if resp.status >= 500:
                    return {
                        **base,
                        "status": "unavailable",
                        "reason": f"scrape service unavailable: HTTP {resp.status}",
                    }

                if resp.status >= 400:
                    return {
                        **base,
                        "status": "degraded",
                        "can_scrape": False,
                        "reason": f"scrape service rejected request: HTTP {resp.status}",
                    }

                if isinstance(parsed, dict) and parsed.get("success") is True:
                    return {
                        **base,
                        "success": True,
                        "status": "ready",
                        "can_scrape": True,
                        "reason": None,
                    }

                return {
                    **base,
                    "status": "degraded",
                    "can_scrape": False,
                    "reason": "scrape service reachable but race data is not ready",
                }
    except HTTPException:
        raise
    except Exception as e:
        return {
            **base,
            "status": "unavailable",
            "reason": f"scrape service not reachable: {e}",
        }


def _build_race_record_preview(race_id: str, user_id: str, scrape_data: dict[str, Any]) -> dict[str, Any]:
    race_info = scrape_data.get("race_info") if isinstance(scrape_data, dict) else {}
    if not isinstance(race_info, dict):
        race_info = {}

    return {
        "race_id": race_id,
        "race_name": race_info.get("race_name") or "",
        "venue": race_info.get("venue") or "",
        "distance": race_info.get("distance") or 0,
        "track_type": race_info.get("track_type") or "",
        "weather": race_info.get("weather") or "",
        "field_condition": race_info.get("field_condition") or "",
        "user_id": user_id,
    }


def _build_results_preview(race_id: str, user_id: str, scrape_data: dict[str, Any]) -> list[dict[str, Any]]:
    raw_results = scrape_data.get("results") if isinstance(scrape_data, dict) else []
    if not isinstance(raw_results, list):
        return []

    results: list[dict[str, Any]] = []

    def _to_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _to_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    for result in raw_results:
        if not isinstance(result, dict):
            continue
        sex_age = str(result.get("sex_age") or "")
        sex = sex_age[:1] if sex_age else ""
        age_text = sex_age[1:] if len(sex_age) > 1 else ""
        try:
            age = int(age_text) if age_text else 0
        except ValueError:
            age = 0

        results.append(
            {
                "race_id": race_id,
                "finish_position": _to_int(result.get("finish_position") or 0),
                "bracket_number": _to_int(result.get("bracket_number") or 0),
                "horse_number": _to_int(result.get("horse_number") or 0),
                "horse_name": str(result.get("horse_name") or ""),
                "sex": sex,
                "age": age,
                "jockey_weight": _to_float(result.get("jockey_weight") or 0),
                "jockey_name": str(result.get("jockey_name") or ""),
                "finish_time": str(result.get("finish_time") or ""),
                "odds": _to_float(result.get("odds") or 0),
                "popularity": _to_int(result.get("popularity") or 0),
                "user_id": user_id,
            }
        )

    return results


def _build_payouts_preview(race_id: str, user_id: str, scrape_data: dict[str, Any]) -> list[dict[str, Any]]:
    raw_payouts = scrape_data.get("payouts") if isinstance(scrape_data, dict) else []
    if not isinstance(raw_payouts, list):
        return []

    payouts: list[dict[str, Any]] = []
    for payout in raw_payouts:
        if not isinstance(payout, dict):
            continue
        amount_str = str(payout.get("amount") or payout.get("payout") or "0")
        amount = int(re.sub(r"[^0-9]", "", amount_str) or "0")
        payouts.append(
            {
                "race_id": race_id,
                "bet_type": str(payout.get("type") or payout.get("bet_type") or ""),
                "combination": str(payout.get("numbers") or payout.get("combination") or ""),
                "payout": amount,
                "user_id": user_id,
            }
        )

    return payouts


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return bool(value)


def _validate_writer_preview(preview: Any) -> dict[str, Any]:
    issues: list[str] = []
    summary = {
        "tables_count": 0,
        "total_records": 0,
        "target_tables": [],
        "row_limits": dict(_WRITE_ROW_LIMITS),
    }

    tables = preview.get("tables") if isinstance(preview, dict) else None
    if not isinstance(tables, list) or not tables:
        issues.append("preview.tables is required and must be a non-empty list")
        return {"ok": False, "issues": issues, "summary": summary}

    target_tables: list[str] = []
    total_records = 0

    for idx, table_info in enumerate(tables):
        if not isinstance(table_info, dict):
            issues.append(f"preview.tables[{idx}] must be an object")
            continue

        target_table = str(table_info.get("target_table") or "").strip()
        if not target_table:
            issues.append(f"preview.tables[{idx}].target_table is required")
            continue

        target_tables.append(target_table)

        if target_table not in _WRITE_TARGET_TABLE_WHITELIST:
            issues.append(f"target_table is not allowed: {target_table}")

        try:
            records_count = int(table_info.get("records_count") or 0)
        except (TypeError, ValueError):
            records_count = -1
        if records_count < 0:
            issues.append(f"records_count must be >= 0 for target_table={target_table}")
            continue
        row_limit = _WRITE_ROW_LIMITS.get(target_table)
        if row_limit is None:
            issues.append(f"row_limit is undefined for target_table={target_table}")
        elif records_count > row_limit:
            issues.append(
                f"records_count exceeds limit for target_table={target_table}: "
                f"{records_count}>{row_limit}"
            )
        total_records += records_count

    summary["tables_count"] = len(target_tables)
    summary["total_records"] = total_records
    summary["target_tables"] = target_tables

    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "summary": summary,
    }


def _build_payload_hash(payload: dict[str, Any], preview_summary: dict[str, Any]) -> str:
    hash_seed = {
        "race_id": str(payload.get("race_id") or payload.get("raceId") or ""),
        "date": str(payload.get("date") or ""),
        "confirm_write": _to_bool(payload.get("confirm_write")),
        "dry_run": _to_bool(payload.get("dry_run"), default=True),
        "payload_contract_approved": _to_bool(payload.get("payload_contract_approved") or payload.get("payload_contract_ok")),
        "user_id": str(payload.get("user_id") or payload.get("userId") or ""),
        "target_tables": preview_summary.get("target_tables") if isinstance(preview_summary, dict) else [],
        "total_records": preview_summary.get("total_records") if isinstance(preview_summary, dict) else 0,
    }
    raw = json.dumps(hash_seed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _build_idempotency_key(race_id: str, payload_hash: str) -> str:
    return f"netkeiba_race:{race_id}:{payload_hash[:16]}"


def _build_audit_payload_preview(
    *,
    race_id: str,
    requested_at: str,
    app_env: str,
    dry_run: bool,
    confirm_write: bool,
    target_tables: list[str],
    records_count: int,
    payload_hash: str,
    write_performed: bool,
    reason: str,
) -> dict[str, Any]:
    return {
        "race_id": race_id,
        "requested_at": requested_at,
        "app_env": app_env,
        "dry_run": dry_run,
        "confirm_write": confirm_write,
        "target_tables": target_tables,
        "records_count": records_count,
        "payload_hash": payload_hash,
        "write_performed": write_performed,
        "reason": reason,
    }


def _extract_idempotency_key(payload: dict[str, Any]) -> str:
    raw = payload.get("idempotency_key")
    if raw is None:
        raw = payload.get("idempotencyKey")
    return str(raw or "").strip()


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _get_table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    cols: set[str] = set()
    for r in rows:
        try:
            cols.add(str(r[1]))
        except Exception:
            continue
    return cols


def _insert_sandbox_record(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    record: dict[str, Any],
    race_id: str,
    app_env: str,
    idempotency_key: str,
    payload_hash: str,
    requested_at: str,
    audit_payload_json: str,
) -> tuple[bool, str | None]:
    cols = _get_table_columns(conn, table_name)
    if "race_id" not in cols:
        return False, f"sandbox table missing race_id column: {table_name}"

    payload_col = "data" if "data" in cols else ("payload" if "payload" in cols else None)
    if payload_col is None:
        return False, f"sandbox table missing data/payload column: {table_name}"

    insert_cols: list[str] = ["race_id", payload_col]
    params: list[Any] = [race_id, json.dumps(record, ensure_ascii=False)]

    if "app_env" in cols:
        insert_cols.append("app_env")
        params.append(app_env)
    if "target_mode" in cols:
        insert_cols.append("target_mode")
        params.append("sandbox")
    if "idempotency_key" in cols:
        insert_cols.append("idempotency_key")
        params.append(idempotency_key)
    if "payload_hash" in cols:
        insert_cols.append("payload_hash")
        params.append(payload_hash)
    if "audit_payload" in cols:
        insert_cols.append("audit_payload")
        params.append(audit_payload_json)
    if "requested_at" in cols:
        insert_cols.append("requested_at")
        params.append(requested_at)
    if "created_at" in cols:
        insert_cols.append("created_at")
        params.append(requested_at)

    placeholders = ", ".join(["?"] * len(insert_cols))
    sql = f"INSERT INTO {table_name} ({', '.join(insert_cols)}) VALUES ({placeholders})"
    conn.execute(sql, params)
    return True, None


def _write_preview_to_sandbox(
    *,
    race_id: str,
    preview_tables: list[dict[str, Any]],
    idempotency_key: str,
    payload_hash: str,
    requested_at: str,
    audit_payload: dict[str, Any],
) -> dict[str, Any]:
    conn = sqlite3.connect(str(ULTIMATE_DB))
    conn.row_factory = sqlite3.Row
    audit_payload_json = json.dumps(audit_payload, ensure_ascii=False, separators=(",", ":"))

    missing_tables: list[str] = []
    target_tables: list[str] = []
    for table in preview_tables:
        target = str(table.get("target_table") or "")
        sandbox_table = _SANDBOX_TABLE_MAP.get(target)
        if sandbox_table is None:
            continue
        target_tables.append(sandbox_table)
        if not _table_exists(conn, sandbox_table):
            missing_tables.append(sandbox_table)

    if missing_tables:
        conn.close()
        return {
            "ok": False,
            "status": "stopped",
            "reason": "sandbox tables are missing",
            "missing_tables": sorted(set(missing_tables)),
            "target_tables": sorted(set(target_tables)),
            "records_written": {},
            "records_written_total": 0,
        }

    records_written: dict[str, int] = {}
    try:
        for table in preview_tables:
            target = str(table.get("target_table") or "")
            sandbox_table = _SANDBOX_TABLE_MAP.get(target)
            if sandbox_table is None:
                continue

            records = table.get("records")
            if not isinstance(records, list):
                records = []

            count = 0
            for record in records:
                if not isinstance(record, dict):
                    continue
                ok, err = _insert_sandbox_record(
                    conn,
                    table_name=sandbox_table,
                    record=record,
                    race_id=race_id,
                    app_env=APP_ENV,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    requested_at=requested_at,
                    audit_payload_json=audit_payload_json,
                )
                if not ok:
                    conn.rollback()
                    return {
                        "ok": False,
                        "status": "stopped",
                        "reason": err or "sandbox table schema is not writable",
                        "missing_tables": [],
                        "target_tables": sorted(set(target_tables)),
                        "records_written": records_written,
                        "records_written_total": int(sum(records_written.values())),
                    }
                count += 1
            records_written[sandbox_table] = count

        conn.commit()
    except Exception as e:
        conn.rollback()
        return {
            "ok": False,
            "status": "stopped",
            "reason": f"sandbox write failed safely: {e}",
            "missing_tables": [],
            "target_tables": sorted(set(target_tables)),
            "records_written": records_written,
            "records_written_total": int(sum(records_written.values())),
        }
    finally:
        conn.close()

    return {
        "ok": True,
        "status": "sandbox-written",
        "reason": "sandbox write completed",
        "missing_tables": [],
        "target_tables": sorted(set(target_tables)),
        "records_written": records_written,
        "records_written_total": int(sum(records_written.values())),
    }


def _verify_sandbox_write_readback(
    *,
    race_id: str,
    idempotency_key: str,
    payload_hash: str,
    records_written: dict[str, int],
    target_tables: list[str],
) -> dict[str, Any]:
    sandbox_tables = set(_SANDBOX_TABLE_MAP.values())
    normalized_targets = sorted(set(str(x or "") for x in target_tables if str(x or "")))
    target_tables_sandbox_only = all(t in sandbox_tables for t in normalized_targets)

    if not target_tables_sandbox_only:
        return {
            "ok": False,
            "status": "sandbox-readback-mismatch",
            "reason": "readback target tables must be sandbox tables only",
            "target_tables": normalized_targets,
            "target_tables_sandbox_only": False,
            "records_readback": {},
            "records_readback_total": 0,
            "records_count_match": False,
            "idempotency_key_match": False,
            "payload_hash_match": False,
            "audit_payload_present": False,
            "table_reports": {},
        }

    conn = sqlite3.connect(str(ULTIMATE_DB))
    conn.row_factory = sqlite3.Row

    records_readback: dict[str, int] = {}
    table_reports: dict[str, Any] = {}
    records_count_match = True
    idempotency_key_match = True
    payload_hash_match = True
    audit_payload_present = True
    mismatch_tables: list[str] = []

    try:
        for table_name in normalized_targets:
            if table_name not in sandbox_tables:
                mismatch_tables.append(table_name)
                records_count_match = False
                idempotency_key_match = False
                payload_hash_match = False
                audit_payload_present = False
                table_reports[table_name] = {
                    "exists": False,
                    "reason": "non-sandbox table is not allowed",
                }
                continue

            exists = _table_exists(conn, table_name)
            if not exists:
                mismatch_tables.append(table_name)
                records_count_match = False
                idempotency_key_match = False
                payload_hash_match = False
                audit_payload_present = False
                table_reports[table_name] = {
                    "exists": False,
                    "reason": "sandbox table is missing during readback",
                }
                continue

            cols = _get_table_columns(conn, table_name)
            required_cols = {"race_id", "idempotency_key", "payload_hash", "audit_payload"}
            missing_cols = sorted(list(required_cols - cols))
            if missing_cols:
                mismatch_tables.append(table_name)
                records_count_match = False
                idempotency_key_match = False
                payload_hash_match = False
                audit_payload_present = False
                table_reports[table_name] = {
                    "exists": True,
                    "missing_columns": missing_cols,
                    "reason": "sandbox table schema is missing readback columns",
                }
                continue

            expected_count = int(records_written.get(table_name, 0) or 0)
            count_row = conn.execute(
                f"SELECT COUNT(*) FROM {table_name} WHERE race_id=? AND idempotency_key=?",
                (race_id, idempotency_key),
            ).fetchone()
            actual_count = int(count_row[0]) if count_row is not None else 0
            records_readback[table_name] = actual_count

            count_ok = actual_count == expected_count
            records_count_match = records_count_match and count_ok

            sample_limit = max(actual_count, 1)
            rows = conn.execute(
                f"SELECT idempotency_key, payload_hash, audit_payload FROM {table_name} WHERE race_id=? AND idempotency_key=? LIMIT ?",
                (race_id, idempotency_key, sample_limit),
            ).fetchall()

            idem_ok = len(rows) == actual_count and all(str(r[0] or "") == idempotency_key for r in rows)
            hash_ok = len(rows) == actual_count and all(str(r[1] or "") == payload_hash for r in rows)
            audit_ok = len(rows) == actual_count and all(str(r[2] or "").strip() != "" for r in rows)

            idempotency_key_match = idempotency_key_match and idem_ok
            payload_hash_match = payload_hash_match and hash_ok
            audit_payload_present = audit_payload_present and audit_ok

            if not (count_ok and idem_ok and hash_ok and audit_ok):
                mismatch_tables.append(table_name)

            table_reports[table_name] = {
                "exists": True,
                "expected_count": expected_count,
                "actual_count": actual_count,
                "count_match": count_ok,
                "idempotency_key_match": idem_ok,
                "payload_hash_match": hash_ok,
                "audit_payload_present": audit_ok,
                "missing_columns": [],
            }

        ok = (
            target_tables_sandbox_only
            and records_count_match
            and idempotency_key_match
            and payload_hash_match
            and audit_payload_present
            and len(mismatch_tables) == 0
        )

        return {
            "ok": ok,
            "status": "sandbox-readback-ok" if ok else "sandbox-readback-mismatch",
            "reason": None if ok else "sandbox readback verification mismatch",
            "target_tables": normalized_targets,
            "target_tables_sandbox_only": target_tables_sandbox_only,
            "records_readback": records_readback,
            "records_readback_total": int(sum(records_readback.values())),
            "records_count_match": records_count_match,
            "idempotency_key_match": idempotency_key_match,
            "payload_hash_match": payload_hash_match,
            "audit_payload_present": audit_payload_present,
            "mismatch_tables": sorted(set(mismatch_tables)),
            "table_reports": table_reports,
        }
    except Exception as e:
        return {
            "ok": False,
            "status": "sandbox-readback-mismatch",
            "reason": f"sandbox readback failed safely: {e}",
            "target_tables": normalized_targets,
            "target_tables_sandbox_only": target_tables_sandbox_only,
            "records_readback": records_readback,
            "records_readback_total": int(sum(records_readback.values())) if records_readback else 0,
            "records_count_match": False,
            "idempotency_key_match": False,
            "payload_hash_match": False,
            "audit_payload_present": False,
            "table_reports": table_reports,
        }
    finally:
        conn.close()


def _build_guarded_writer_stub(
    race_id: str,
    preview_summary: dict[str, Any],
    payload_contract_approved: bool,
    idempotency_key: str,
    payload_hash: str,
    requested_at: str,
    confirm_write: bool,
    dry_run: bool,
) -> dict[str, Any]:
    audit_payload_preview = _build_audit_payload_preview(
        race_id=race_id,
        requested_at=requested_at,
        app_env=APP_ENV,
        dry_run=dry_run,
        confirm_write=confirm_write,
        target_tables=list(preview_summary.get("target_tables") or []),
        records_count=int(preview_summary.get("total_records") or 0),
        payload_hash=payload_hash,
        write_performed=False,
        reason="staging writer implementation is intentionally disabled in this phase",
    )
    return {
        "status": "guarded-stub",
        "write_performed": False,
        "reason": "staging writer implementation is intentionally disabled in this phase",
        "writer": {
            "name": "staging_netkeiba_writer_stub",
            "phase": "P1-12",
            "target_tables_whitelist": sorted(_WRITE_TARGET_TABLE_WHITELIST),
            "row_limits": dict(_WRITE_ROW_LIMITS),
            "preview_summary": preview_summary,
            "payload_contract_approved": payload_contract_approved,
            "idempotency_key": idempotency_key,
            "payload_hash": payload_hash,
            "requested_at": requested_at,
            "audit_payload_preview": audit_payload_preview,
            "implementation": "no-op",
            "todo": [
                "snapshot backup before write",
                "idempotency key persistence and duplicate guard",
                "audit log persistence",
                "duplicate prevention",
                "rollback execution plan",
            ],
        },
        "write_safety": {
            "snapshot_required": True,
            "audit_log_required": True,
            "idempotency_key_required": True,
            "duplicate_prevention_required": True,
            "rollback_plan_required": True,
            "table_whitelist_required": True,
            "row_count_limit_required": True,
            "snapshot": {
                "snapshot_id": f"prewrite-{race_id}-{int(datetime.utcnow().timestamp())}",
                "captured": False,
                "phase": "design-only-no-persist",
                "captured_at": None,
            },
            "audit": {
                "event_type": "netkeiba_race_write_guarded_stub",
                "event_time": requested_at,
                "phase": "P1-12",
                "persisted": False,
            },
        },
    }


@router.post("/api/netkeiba/race/dry-run")
async def netkeiba_race_dry_run(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Simulate netkeiba race write orchestration and return write payload preview without performing any write."""
    race_id_raw = payload.get("race_id") if isinstance(payload, dict) else None
    if race_id_raw is None and isinstance(payload, dict):
        race_id_raw = payload.get("raceId")

    race_id = str(race_id_raw or "").strip()
    date_raw = str(payload.get("date") or "").strip() if isinstance(payload, dict) else ""
    date_str = date_raw.replace("-", "")

    user_id_raw = payload.get("user_id") if isinstance(payload, dict) else None
    if user_id_raw is None and isinstance(payload, dict):
        user_id_raw = payload.get("userId")
    user_id = str(user_id_raw or "dry-run-user")

    base = {
        "success": False,
        "status": "unavailable",
        "service": "netkeiba-race",
        "race_id": race_id,
        "can_scrape": False,
        "can_write": False,
        "write_performed": False,
        "dry_run": True,
        "required_params": ["race_id"],
        "provided_params": {
            "race_id": bool(race_id),
            "date": bool(date_raw),
            "user_id": bool(user_id_raw),
        },
        "reason": None,
    }

    if not race_id:
        return {**base, "status": "invalid", "reason": "race_id is required"}

    if not re.fullmatch(r"\d{12}", race_id):
        return {**base, "status": "invalid", "reason": "race_id must be 12 digits"}

    if date_raw and not re.fullmatch(r"\d{8}", date_str):
        return {**base, "status": "invalid", "reason": "date must be YYYYMMDD or YYYY-MM-DD format"}

    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{_SCRAPE_SERVICE_URL}/scrape/ultimate",
                headers={"Content-Type": "application/json"},
                json={"race_id": race_id, "include_details": False},
            ) as resp:
                body_text = await resp.text()
                parsed: dict[str, Any] | None = None
                try:
                    parsed_any = json.loads(body_text) if body_text else {}
                    parsed = parsed_any if isinstance(parsed_any, dict) else None
                except Exception:
                    parsed = None

                if resp.status >= 500:
                    return {
                        **base,
                        "status": "unavailable",
                        "reason": f"scrape service unavailable: HTTP {resp.status}",
                    }

                if resp.status >= 400:
                    return {
                        **base,
                        "status": "degraded",
                        "reason": f"scrape service rejected request: HTTP {resp.status}",
                    }

                if not isinstance(parsed, dict):
                    return {
                        **base,
                        "status": "degraded",
                        "reason": "scrape service returned invalid JSON payload",
                    }

                if parsed.get("success") is not True:
                    return {
                        **base,
                        "status": "degraded",
                        "reason": str(parsed.get("error") or "scrape service did not return success"),
                    }

                race_record = _build_race_record_preview(race_id, user_id, parsed)
                results = _build_results_preview(race_id, user_id, parsed)
                payouts = _build_payouts_preview(race_id, user_id, parsed)

                preview = {
                    "tables": [
                        {
                            "target_table": "races",
                            "records_count": 1,
                            "records": [race_record],
                            "sample_records": [race_record],
                        },
                        {
                            "target_table": "race_results",
                            "records_count": len(results),
                            "records": results,
                            "sample_records": results[:3],
                        },
                        {
                            "target_table": "race_payouts",
                            "records_count": len(payouts),
                            "records": payouts,
                            "sample_records": payouts[:3],
                        },
                    ],
                    "source": "scrape_service",
                }

                return {
                    **base,
                    "success": True,
                    "status": "ready",
                    "can_scrape": True,
                    "preview": preview,
                    "reason": None,
                }
    except Exception as e:
        return {
            **base,
            "status": "unavailable",
            "reason": f"scrape service not reachable: {e}",
        }


@router.post("/api/netkeiba/race/write")
async def netkeiba_race_write(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Guarded write endpoint. P1-13 enables explicit sandbox-only writes under strict staging locks."""
    race_id_raw = payload.get("race_id") if isinstance(payload, dict) else None
    if race_id_raw is None and isinstance(payload, dict):
        race_id_raw = payload.get("raceId")
    race_id = str(race_id_raw or "").strip()

    base = {
        "success": False,
        "status": "disabled",
        "service": "netkeiba-race-write",
        "race_id": race_id,
        "app_env": APP_ENV,
        "can_write": False,
        "write_performed": False,
        "reason": "NETKEIBA_RACE_WRITE_ENABLED is false",
        "guard_locks": {
            "netkeiba_race_write_enabled": NETKEIBA_RACE_WRITE_ENABLED,
            "allow_staging_write": ALLOW_STAGING_WRITE,
            "app_env": APP_ENV,
        },
    }

    if not NETKEIBA_RACE_WRITE_ENABLED:
        return base

    if APP_ENV == "production":
        return {
            **base,
            "status": "blocked",
            "reason": "production write is forbidden in this phase",
        }

    if not ALLOW_STAGING_WRITE:
        return {
            **base,
            "status": "blocked",
            "reason": "ALLOW_STAGING_WRITE=true is required",
        }

    if APP_ENV != "staging":
        return {
            **base,
            "status": "blocked",
            "reason": "APP_ENV=staging is required",
        }

    confirm_write = _to_bool(payload.get("confirm_write") if isinstance(payload, dict) else None)
    dry_run_flag = payload.get("dry_run") if isinstance(payload, dict) else None
    if dry_run_flag is None and isinstance(payload, dict):
        dry_run_flag = payload.get("dryRun")
    dry_run = True if dry_run_flag is None else _to_bool(dry_run_flag)

    payload_contract_flag = payload.get("payload_contract_approved") if isinstance(payload, dict) else None
    if payload_contract_flag is None and isinstance(payload, dict):
        payload_contract_flag = payload.get("payload_contract_ok")
    payload_contract_approved = _to_bool(payload_contract_flag, default=False)

    sandbox_write_flag = payload.get("sandbox_write") if isinstance(payload, dict) else None
    if sandbox_write_flag is None and isinstance(payload, dict):
        sandbox_write_flag = payload.get("sandboxWrite")
    sandbox_write = _to_bool(sandbox_write_flag, default=False)

    target_mode_raw = payload.get("target_mode") if isinstance(payload, dict) else None
    if target_mode_raw is None and isinstance(payload, dict):
        target_mode_raw = payload.get("targetMode")
    target_mode = str(target_mode_raw or "").strip().lower()

    if not confirm_write:
        return {
            **base,
            "status": "blocked",
            "reason": "confirm_write=true is required",
        }

    if dry_run:
        return {
            **base,
            "status": "blocked",
            "reason": "dry_run=false is required for guarded write path",
        }

    if not payload_contract_approved:
        return {
            **base,
            "status": "blocked",
            "reason": "payload_contract_approved=true is required",
        }

    if target_mode and target_mode != "sandbox":
        return {
            **base,
            "status": "blocked",
            "reason": "target_mode must be sandbox",
        }

    if target_mode == "sandbox" and not sandbox_write:
        return {
            **base,
            "status": "blocked",
            "reason": "sandbox_write=true is required when target_mode=sandbox",
        }

    if not race_id or not re.fullmatch(r"\d{12}", race_id):
        return {
            **base,
            "status": "invalid",
            "reason": "race_id must be 12 digits",
        }

    dry_run_result = await netkeiba_race_dry_run(payload)
    if not isinstance(dry_run_result, dict):
        return {
            **base,
            "status": "invalid",
            "reason": "dry-run response is invalid",
        }

    if dry_run_result.get("status") != "ready":
        return {
            **base,
            "status": "blocked",
            "reason": f"preconditions not ready: dry-run status={dry_run_result.get('status')}",
            "dry_run_status": dry_run_result.get("status"),
        }

    preview = dry_run_result.get("preview")
    preview_validation = _validate_writer_preview(preview)
    if not preview_validation.get("ok"):
        return {
            **base,
            "status": "blocked",
            "reason": "payload preview is invalid for staging writer",
            "preview_validation": preview_validation,
        }

    preview_summary = preview_validation.get("summary") if isinstance(preview_validation, dict) else {}
    payload_hash = _build_payload_hash(payload if isinstance(payload, dict) else {}, preview_summary if isinstance(preview_summary, dict) else {})
    idempotency_key = _build_idempotency_key(race_id, payload_hash)
    requested_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    client_idempotency_key = _extract_idempotency_key(payload if isinstance(payload, dict) else {})

    if sandbox_write and not client_idempotency_key:
        return {
            **base,
            "status": "blocked",
            "reason": "idempotency_key is required for sandbox write",
            "target_mode": "sandbox",
            "sandbox_write": True,
        }

    writer_stub = _build_guarded_writer_stub(
        race_id=race_id,
        preview_summary=preview_summary if isinstance(preview_summary, dict) else {},
        payload_contract_approved=payload_contract_approved,
        idempotency_key=idempotency_key,
        payload_hash=payload_hash,
        requested_at=requested_at,
        confirm_write=confirm_write,
        dry_run=dry_run,
    )

    audit_payload_preview = writer_stub.get("writer", {}).get("audit_payload_preview") if isinstance(writer_stub, dict) else None

    if sandbox_write and target_mode == "sandbox":
        sandbox_precheck = _run_sandbox_precheck()
        if str(sandbox_precheck.get("status") or "") != "ready":
            precheck_status = str(sandbox_precheck.get("status") or "stopped")
            return {
                **base,
                "status": precheck_status if precheck_status in {"stopped", "warn", "unavailable"} else "stopped",
                "reason": str(sandbox_precheck.get("reason") or "sandbox precheck is not ready"),
                "target_mode": "sandbox",
                "sandbox_write": True,
                "idempotency_key": client_idempotency_key,
                "payload_hash": payload_hash,
                "audit_payload_preview": audit_payload_preview,
                "dry_run_preview": {
                    "tables_count": preview_summary.get("tables_count", 0),
                    "target_tables": preview_summary.get("target_tables", []),
                    "total_records": preview_summary.get("total_records", 0),
                    "row_limits": preview_summary.get("row_limits", {}),
                },
                "sandbox_precheck": sandbox_precheck,
                "writer_stub": writer_stub,
            }

        preview = dry_run_result.get("preview")
        tables = preview.get("tables") if isinstance(preview, dict) else None
        if not isinstance(tables, list):
            tables = []

        sandbox_result = _write_preview_to_sandbox(
            race_id=race_id,
            preview_tables=[t for t in tables if isinstance(t, dict)],
            idempotency_key=client_idempotency_key,
            payload_hash=payload_hash,
            requested_at=requested_at,
            audit_payload=audit_payload_preview if isinstance(audit_payload_preview, dict) else {},
        )

        if sandbox_result.get("ok") is not True:
            return {
                **base,
                "status": str(sandbox_result.get("status") or "stopped"),
                "reason": str(sandbox_result.get("reason") or "sandbox write not executed"),
                "target_mode": "sandbox",
                "sandbox_write": True,
                "idempotency_key": client_idempotency_key,
                "payload_hash": payload_hash,
                "audit_payload_preview": audit_payload_preview,
                "dry_run_preview": {
                    "tables_count": preview_summary.get("tables_count", 0),
                    "target_tables": preview_summary.get("target_tables", []),
                    "total_records": preview_summary.get("total_records", 0),
                    "row_limits": preview_summary.get("row_limits", {}),
                },
                "sandbox_result": sandbox_result,
                "writer_stub": writer_stub,
            }

        readback_result = _verify_sandbox_write_readback(
            race_id=race_id,
            idempotency_key=client_idempotency_key,
            payload_hash=payload_hash,
            records_written=sandbox_result.get("records_written", {}) if isinstance(sandbox_result.get("records_written"), dict) else {},
            target_tables=sandbox_result.get("target_tables", []) if isinstance(sandbox_result.get("target_tables"), list) else [],
        )

        if readback_result.get("ok") is not True:
            return {
                **base,
                "status": "sandbox-readback-mismatch",
                "reason": str(readback_result.get("reason") or "sandbox readback verification mismatch"),
                "can_write": True,
                "write_performed": True,
                "target_mode": "sandbox",
                "sandbox_write": True,
                "payload_contract_approved": payload_contract_approved,
                "idempotency_key": client_idempotency_key,
                "payload_hash": payload_hash,
                "audit_payload": audit_payload_preview,
                "target_tables": sandbox_result.get("target_tables", []),
                "records_written": sandbox_result.get("records_written", {}),
                "records_written_total": sandbox_result.get("records_written_total", 0),
                "readback_verification": readback_result,
                "sandbox_result": sandbox_result,
            }

        return {
            **base,
            "success": True,
            "status": "sandbox-written",
            "reason": "sandbox write completed under staging guard",
            "can_write": True,
            "write_performed": True,
            "target_mode": "sandbox",
            "sandbox_write": True,
            "payload_contract_approved": payload_contract_approved,
            "idempotency_key": client_idempotency_key,
            "payload_hash": payload_hash,
            "audit_payload": audit_payload_preview,
            "target_tables": sandbox_result.get("target_tables", []),
            "records_written": sandbox_result.get("records_written", {}),
            "records_written_total": sandbox_result.get("records_written_total", 0),
            "readback_verification": readback_result,
            "dry_run_preview": {
                "tables_count": preview_summary.get("tables_count", 0),
                "target_tables": preview_summary.get("target_tables", []),
                "total_records": preview_summary.get("total_records", 0),
                "row_limits": preview_summary.get("row_limits", {}),
            },
            "sandbox_result": sandbox_result,
        }

    # P1-11: staging guard design is active, but actual write remains intentionally disabled.
    return {
        **base,
        "success": True,
        "status": "guarded-stub",
        "reason": "staging-only write guard passed; actual writer remains disabled in this phase",
        "can_write": True,
        "write_performed": False,
        "target_mode": "stub",
        "sandbox_write": False,
        "payload_contract_approved": payload_contract_approved,
        "idempotency_key": idempotency_key,
        "payload_hash": payload_hash,
        "audit_payload_preview": audit_payload_preview,
        "dry_run_preview": {
            "tables_count": preview_summary.get("tables_count", 0),
            "target_tables": preview_summary.get("target_tables", []),
            "total_records": preview_summary.get("total_records", 0),
            "row_limits": preview_summary.get("row_limits", {}),
        },
        "writer_stub": writer_stub,
    }


@router.get("/api/scrape/health")
async def scrape_health() -> dict:
    """スクレイプ系サービスのヘルスチェック（read-only, 契約固定）。"""
    timestamp = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    try:
        with _JOBS_LOCK:
            _purge_old_jobs(_scrape_jobs)
            statuses = [str(j.get("status", "unknown")) for j in _scrape_jobs.values()]

        active_jobs = sum(1 for s in statuses if s in {"queued", "running"})
        error_jobs = sum(1 for s in statuses if s == "error")

        if error_jobs > 0:
            return {
                "success": True,
                "status": "degraded",
                "service": "scrape",
                "timestamp": timestamp,
                "reason": "recent scrape job errors detected",
                "metrics": {
                    "active_jobs": active_jobs,
                    "error_jobs": error_jobs,
                    "total_jobs": len(statuses),
                },
            }

        return {
            "success": True,
            "status": "healthy",
            "service": "scrape",
            "timestamp": timestamp,
            "metrics": {
                "active_jobs": active_jobs,
                "error_jobs": error_jobs,
                "total_jobs": len(statuses),
            },
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "status": "unhealthy",
                "service": "scrape",
                "timestamp": timestamp,
                "reason": f"scrape service not reachable: {e}",
            },
        )


@router.post("/api/scrape", response_model=ScrapeResponse)
async def scrape_data(request: ScrapeRequest):
    """
    期間指定でnetkeiba.comから完全データを自動収集しkeiba_ultimate.dbに保存。
    NOTE: 土日のみ対象。全日程は /api/scrape/start を使用すること。
    """
    logger.info(f"完全スクレイピング開始: {request.start_date} ～ {request.end_date}")
    start_time = time.time()

    def _parse_date(s: str) -> datetime:
        for fmt in ("%Y%m%d", "%Y/%m/%d", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        raise ValueError(f"日付フォーマット不正: {s}")

    start = _parse_date(request.start_date)
    end = _parse_date(request.end_date)
    dates = []
    cur = start
    while cur <= end:
        # 土日 + 月曜祝日（成人の日等）を対象とする
        # NOTE: /api/scrape/start を使えば全日処理可能（こちらは短期間向け）
        if cur.weekday() in [5, 6]:
            dates.append(cur.strftime("%Y%m%d"))
        elif cur.weekday() == 0:  # 月曜日 — 祝日の可能性があるので含める
            dates.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)

    logger.info(f"対象日数: {len(dates)}日")

    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(limit=5, limit_per_host=3)

    saved_races = 0
    saved_horses = 0
    error_dates: list[str] = []

    async with aiohttp.ClientSession(headers=SCRAPE_HEADERS, timeout=timeout, connector=connector) as session:
        for date in dates:
            list_url = f"https://db.netkeiba.com/race/list/{date}/"
            logger.info(f"レース一覧取得: {date}")
            try:
                await asyncio.sleep(1.0)
                _list_result, html = await fetch_text(
                    session,
                    list_url,
                    cache_ttl_sec=12 * 60 * 60,
                    resume_key=f"legacy-scrape:{date}:list",
                    min_interval_sec=1.0,
                    max_retries=3,
                    retry_statuses={429, 500, 503},
                    retry_base_sec=2.0,
                    retry_jitter_sec=0.6,
                    circuit_threshold=3,
                    circuit_cooldown_sec=120.0,
                )
                if _list_result.status != 200:
                    logger.warning(f"{date}: レース一覧 HTTP {_list_result.status} → スキップ")
                    error_dates.append(date)
                    continue

                race_ids = list(dict.fromkeys(re.findall(r"/race/(\d{12})/", html)))
                logger.info(f"  {len(race_ids)}レース発見")

                for race_id in race_ids:
                    race_data = await scrape_race_full(session, race_id, date_hint=date)
                    if race_data and race_data["horses"]:
                        _save_race_to_ultimate_db(race_data, ULTIMATE_DB, overwrite=True)
                        saved_races += 1
                        saved_horses += len(race_data["horses"])
                        logger.info(f"  保存: {race_id} {race_data['race_info']['race_name']} ({len(race_data['horses'])}頭)")
            except Exception as e:
                logger.error(f"{date} エラー: {e}")
                error_dates.append(date)

    elapsed = time.time() - start_time
    logger.info(f"完全スクレイピング完了: {saved_races}レース/{saved_horses}頭, {elapsed:.1f}秒")

    msg = f"{saved_races}レース・{saved_horses}頭のデータを収集しました（完全版）"
    if error_dates:
        msg += f" ※{len(error_dates)}日取得失敗: {', '.join(error_dates[:3])}"

    return ScrapeResponse(
        success=len(error_dates) == 0,
        message=msg,
        races_collected=saved_races,
        db_path=str(ULTIMATE_DB),
        elapsed_time=elapsed,
    )


@router.post("/api/rescrape_incomplete")
async def rescrape_incomplete(limit: int = 50) -> RescrapeResponse:
    """
    keiba_ultimate.db 内の不完全レコード（trainer_name=NULL / distance=0 等）を再スクレイプして上書き保存する。
    """
    import sqlite3 as _sq3

    start_time = time.time()

    conn = _sq3.connect(str(ULTIMATE_DB))
    rows = conn.execute("SELECT DISTINCT race_id FROM race_results_ultimate").fetchall()
    all_race_ids = [r[0] for r in rows]

    # races_ultimate から既存の date を取得（date_hint として再利用）
    date_map: dict[str, str] = {}
    # distance=0 または _invalid_distance=True のレースを収集
    invalid_dist_ids: set[str] = set()
    for row in conn.execute("SELECT race_id, data FROM races_ultimate").fetchall():
        try:
            import json as _json
            _d = _json.loads(row[1] or "{}")
            if _d.get("date"):
                date_map[row[0]] = _d["date"]
            if _d.get("_invalid_distance") or _d.get("distance", -1) == 0:
                invalid_dist_ids.add(row[0])
        except Exception:
            pass

    incomplete_ids = []
    for rid in all_race_ids:
        if rid in invalid_dist_ids:
            incomplete_ids.append(rid)
            continue
        sample = conn.execute(
            "SELECT data FROM race_results_ultimate WHERE race_id = ? LIMIT 1", (rid,)
        ).fetchone()
        if sample:
            d = json.loads(sample[0])
            if d.get("trainer_name") is None and d.get("horse_weight") is None:
                incomplete_ids.append(rid)
    conn.close()

    to_process = incomplete_ids[:limit]
    logger.info(f"不完全レース: {len(incomplete_ids)}件中 {len(to_process)}件を再取得")

    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(limit=5, limit_per_host=3)

    updated_races = 0
    updated_horses = 0

    async with aiohttp.ClientSession(headers=SCRAPE_HEADERS, timeout=timeout, connector=connector) as session:
        for race_id in to_process:
            _date_hint = date_map.get(race_id, "")
            race_data = await scrape_race_full(session, race_id, date_hint=_date_hint)
            if race_data and race_data["horses"]:
                _save_race_to_ultimate_db(race_data, ULTIMATE_DB, overwrite=True)
                updated_races += 1
                updated_horses += len(race_data["horses"])
                logger.info(f"  更新: {race_id} ({len(race_data['horses'])}頭)")
            else:
                logger.warning(f"  スキップ: {race_id} (取得失敗)")

    elapsed = time.time() - start_time
    remaining = len(incomplete_ids) - updated_races

    return RescrapeResponse(
        success=True,
        message=f"{updated_races}レース/{updated_horses}頭を更新。残り不完全: {remaining}件",
        updated_races=updated_races,
        updated_horses=updated_horses,
        elapsed_time=elapsed,
    )


@router.post("/api/scrape/repair/{race_id}")
async def repair_race(race_id: str, _: dict = Depends(require_admin)) -> dict:
    """
    指定レース ID を再スクレイプして DB を上書き修復する（distance=0 等の修正用）。
    """
    import sqlite3 as _sq3

    if not re.fullmatch(r"\d{12}", race_id):
        raise HTTPException(status_code=400, detail="race_id は12桁の数字である必要があります")

    # 既存の date_hint を取得
    date_hint = ""
    try:
        conn = _sq3.connect(str(ULTIMATE_DB))
        row = conn.execute("SELECT data FROM races_ultimate WHERE race_id = ?", (race_id,)).fetchone()
        conn.close()
        if row:
            date_hint = json.loads(row[0] or "{}").get("date", "")
    except Exception:
        pass

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(headers=SCRAPE_HEADERS, timeout=timeout) as session:
        race_data = await scrape_race_full(session, race_id, date_hint=date_hint)

    if not race_data or not race_data.get("horses"):
        raise HTTPException(status_code=502, detail=f"レース {race_id} の再スクレイプに失敗しました")

    _save_race_to_ultimate_db(race_data, ULTIMATE_DB, overwrite=True)
    ri = race_data["race_info"]
    return {
        "success": True,
        "race_id": race_id,
        "race_name": ri.get("race_name", ""),
        "distance": ri.get("distance", 0),
        "track_type": ri.get("track_type", ""),
        "horses": len(race_data["horses"]),
    }
