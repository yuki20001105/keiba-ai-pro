from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .contracts import ObservationContractError, canonical_sha256


def canonical_cache_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized = [dict(row) for row in rows]
    normalized.sort(
        key=lambda row: (
            str(row.get("race_id") or ""),
            str(row.get("horse_id") or ""),
            str(row.get("observation_id") or ""),
        )
    )
    return normalized


def cache_digest(rows: Iterable[Mapping[str, Any]]) -> str:
    return canonical_sha256(canonical_cache_rows(rows))


def write_cache_atomic(path: Path, rows: Iterable[Mapping[str, Any]]) -> str:
    resolved = path.resolve(strict=False)
    payload = canonical_cache_rows(rows)
    digest = canonical_sha256(payload)
    document = {
        "schema": "phase3n-rebuildable-cache",
        "schema_version": 1,
        "authoritative": False,
        "source": "shared-persistent-store",
        "row_count": len(payload),
        "rows_sha256": digest,
        "rows": payload,
    }
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(f".{resolved.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(document, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temporary, resolved)
    finally:
        temporary.unlink(missing_ok=True)
    return digest


def verify_cache(path: Path, source_rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    start = time.perf_counter()
    expected_rows = canonical_cache_rows(source_rows)
    expected_digest = canonical_sha256(expected_rows)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ObservationContractError("cache-unavailable-or-invalid") from exc
    cached_rows = document.get("rows")
    if not isinstance(cached_rows, list):
        raise ObservationContractError("cache-rows-invalid")
    cached_digest = canonical_sha256(cached_rows)
    expected_ids = [str(row.get("observation_id") or "") for row in expected_rows]
    cached_ids = [str(row.get("observation_id") or "") for row in cached_rows]
    missing_count = len(set(expected_ids) - set(cached_ids))
    duplicate_count = len(cached_ids) - len(set(cached_ids))
    stale_count = len(set(cached_ids) - set(expected_ids))
    return {
        "source_row_count": len(expected_rows),
        "cache_row_count": len(cached_rows),
        "source_digest_sha256": expected_digest,
        "cache_digest_sha256": cached_digest,
        "digest_match": cached_digest == expected_digest,
        "missing_count": missing_count,
        "duplicate_count": duplicate_count,
        "stale_count": stale_count,
        "verification_ms": round((time.perf_counter() - start) * 1000, 3),
        "success": (
            cached_digest == expected_digest
            and missing_count == 0
            and duplicate_count == 0
            and stale_count == 0
        ),
    }


def exercise_cache_rebuild(
    path: Path,
    load_source_rows: Callable[[], list[dict[str, Any]]],
) -> dict[str, Any]:
    started = time.perf_counter()
    source_before = load_source_rows()
    if not source_before:
        raise ObservationContractError("cache-integrity-source-empty")
    database_digest_before = cache_digest(source_before)
    cache_digest_before = write_cache_atomic(path, source_before)
    try:
        path.unlink()
    except OSError as exc:
        raise ObservationContractError("cache-delete-failed") from exc
    rebuild_started = time.perf_counter()
    rebuild_source = load_source_rows()
    rebuilt_digest = write_cache_atomic(path, rebuild_source)
    rebuild_ms = round((time.perf_counter() - rebuild_started) * 1000, 3)
    verification = verify_cache(path, rebuild_source)
    source_after = load_source_rows()
    database_digest_after = cache_digest(source_after)
    database_unchanged = database_digest_before == database_digest_after
    success = bool(
        verification["success"]
        and cache_digest_before == rebuilt_digest
        and database_unchanged
    )
    return {
        "schema": "phase3n-cache-integrity-exercise",
        "schema_version": 1,
        "authoritative_store": "supabase-postgresql",
        "cache_authoritative": False,
        "source_row_count": len(source_before),
        "cache_digest_before_sha256": cache_digest_before,
        "cache_digest_after_sha256": rebuilt_digest,
        "database_digest_before_sha256": database_digest_before,
        "database_digest_after_sha256": database_digest_after,
        "database_unchanged": database_unchanged,
        "cache_deleted_before_rebuild": True,
        "rebuild_ms": rebuild_ms,
        "total_exercise_ms": round((time.perf_counter() - started) * 1000, 3),
        **verification,
        "success": success,
    }
