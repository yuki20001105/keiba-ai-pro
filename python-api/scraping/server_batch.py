"""Local monthly acquisition owned by one durable operational saga claim.

There is no independent scheduler here: the parent's existing worker owns
the child task, heartbeat, cancellation, and owner lock for the entire range.
Monthly receipts and parent progress live in the same fenced SQLite ledger.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from scraping.operational_saga_runtime import (
    CANCELLED_BY_OWNER,
    EffectResult,
    OperationalClaim,
    OperationalEffectCancelled,
    OperationalSagaConflict,
    OperationalSagaError,
    OperationalSagaUnavailable,
    SQLiteOperationalSagaStore,
)
from scraping.scrape_request_contract import build_scrape_months


def month_job_id(parent_job_id: str, start: str, end: str) -> str:
    return str(uuid.uuid5(uuid.UUID(parent_job_id), f"scrape-month-v1:{start}:{end}"))


class BatchCheckpointStore:
    def __init__(self, store: SQLiteOperationalSagaStore, claim: OperationalClaim) -> None:
        self.store, self.claim = store, claim

    def _check(self, connection: sqlite3.Connection) -> bool:
        row = connection.execute(
            "SELECT o.*,j.owner_user_id,j.request_hash FROM operational_scrape_outbox o "
            "JOIN operational_scrape_jobs j USING(job_id) WHERE o.job_id=?",
            (self.claim.job_id,),
        ).fetchone()
        if (
            row is None
            or row["state"] != "claimed"
            or row["worker_owner"] != self.claim.worker_owner
            or row["fencing_token"] != self.claim.fencing_token
            or int(row["lease_expires_at_epoch"] or 0) <= int(time.time())
            or row["owner_user_id"] != self.claim.owner_user_id
            or row["request_hash"] != self.claim.request_hash
        ):
            raise OperationalSagaConflict("batch-worker-lease-lost")
        return row["cancel_requested_at_epoch"] is not None

    def cancel_requested(self) -> bool:
        connection = self.store._connect()
        try:
            return self._check(connection)
        finally:
            connection.close()

    def completed(self) -> dict[int, dict[str, Any]]:
        connection = self.store._connect()
        try:
            self._check(connection)
            rows = connection.execute(
                "SELECT month_index,result FROM operational_scrape_batch_months "
                "WHERE parent_job_id=? ORDER BY month_index", (self.claim.job_id,),
            ).fetchall()
            result = {int(row["month_index"]): json.loads(row["result"]) for row in rows}
            if list(result) != list(range(len(result))):
                raise OperationalSagaConflict("batch-month-checkpoint-gap")
            return result
        finally:
            connection.close()

    def save(
        self,
        progress: dict[str, Any],
        *,
        month_index: int | None = None,
        child_job_id: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        connection = self.store._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._check(connection)
            now = int(time.time())
            previous_row = connection.execute(
                "SELECT progress FROM operational_scrape_jobs WHERE job_id=?",
                (self.claim.job_id,),
            ).fetchone()
            previous_progress = json.loads(previous_row["progress"] or "{}")
            started_at = previous_progress.get("batch_started_at_epoch")
            timing_complete = previous_progress.get("batch_wall_time_complete") is True
            if not isinstance(started_at, (int, float)) or isinstance(started_at, bool):
                started_at = now
                timing_complete = self.claim.attempt_count == 1 and not progress.get("completed_months")
            saved_progress = {
                **progress,
                "batch_started_at_epoch": started_at,
                "batch_wall_time_complete": timing_complete,
                "observed_batch_wall_time_sec": max(0.0, now - started_at),
                "batch_wall_time_sec": max(0.0, now - started_at) if timing_complete else None,
            }
            if month_index is not None:
                serialized = json.dumps(result, ensure_ascii=False, sort_keys=True)
                existing = connection.execute(
                    "SELECT child_job_id,result FROM operational_scrape_batch_months "
                    "WHERE parent_job_id=? AND month_index=?",
                    (self.claim.job_id, month_index),
                ).fetchone()
                if existing is not None:
                    if existing["child_job_id"] != child_job_id or existing["result"] != serialized:
                        raise OperationalSagaConflict("batch-month-receipt-conflict")
                else:
                    connection.execute(
                        "INSERT INTO operational_scrape_batch_months "
                        "(parent_job_id,month_index,child_job_id,result,completed_at_epoch) VALUES(?,?,?,?,?)",
                        (self.claim.job_id, month_index, child_job_id, serialized, now),
                    )
            connection.execute(
                "UPDATE operational_scrape_jobs SET progress=?,updated_at_epoch=? WHERE job_id=?",
                (json.dumps(saved_progress, ensure_ascii=False, sort_keys=True), now, self.claim.job_id),
            )
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise OperationalSagaUnavailable("batch-checkpoint-unavailable") from exc
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def timings(self) -> dict[str, Any]:
        """Elapsed batch wall time survives lease recovery and month gaps."""
        connection = self.store._connect()
        try:
            self._check(connection)
            row = connection.execute(
                "SELECT progress FROM operational_scrape_jobs WHERE job_id=?",
                (self.claim.job_id,),
            ).fetchone()
            progress = json.loads(row["progress"] or "{}")
            started_at = progress.get("batch_started_at_epoch")
            if not isinstance(started_at, (int, float)) or isinstance(started_at, bool):
                return {"batch_wall_time_sec": None, "batch_started_at_epoch": None, "batch_wall_time_complete": False}
            elapsed = max(0.0, time.time() - started_at)
            complete = progress.get("batch_wall_time_complete") is True
            return {
                "batch_started_at_epoch": started_at,
                "observed_batch_wall_time_sec": elapsed,
                "batch_wall_time_sec": elapsed if complete else None,
                "batch_wall_time_complete": complete,
                "batch_wall_time_includes_recovery_and_month_gaps": True,
                "batch_wall_time_boundary": "first_batch_checkpoint_to_final_month_receipt",
            }
        finally:
            connection.close()


@asynccontextmanager
async def _local_effect_lock(checkpoint: BatchCheckpointStore):
    """Drain the old local effect before a newly fenced worker can resume.

    SQLite destinations predate fencing. The existing saga still owns all
    authorization; this OS-released lock additionally prevents overlap while
    an expired worker finishes an already-started thread write. It is released
    automatically on process death, not on an arbitrary timeout.
    """
    # Bind to the owner rather than only the parent ID: an expired cancelled
    # parent can be settled by another worker while its final write drains.
    # A newly accepted parent for that owner must wait for the same lock.
    owner_key = hashlib.sha256(checkpoint.claim.owner_user_id.encode()).hexdigest()
    path = checkpoint.store._path.with_name(
        f".{checkpoint.store._path.name}.batch-owner-{owner_key}.lock"
    )
    handle = path.open("a+b")
    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            if handle.seek(0, 2) == 0:
                handle.write(b"0")
                handle.flush()
        while not locked:
            if checkpoint.cancel_requested():
                raise OperationalEffectCancelled(CANCELLED_BY_OWNER)
            try:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError:
                await asyncio.sleep(0.1)
        yield
    finally:
        if locked:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _totals(results: dict[int, dict[str, Any]]) -> dict[str, int]:
    return {
        progress_key: sum(int(_number(item.get(result_key))) for item in results.values())
        for progress_key, result_key in (
            ("saved_races", "races_collected"), ("saved_horses", "saved_horses"),
            ("existing_races_skipped", "existing_races_skipped"),
            ("no_race_dates", "verified_no_race_dates"),
        )
    }


def batch_progress(
    months: list[tuple[str, str]], results: dict[int, dict[str, Any]],
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    completed_count, count = len(results), len(months)
    current = current or {}
    child = current.get("progress") or {}
    fraction = min(1.0, max(0.0, _number(child.get("done")) / max(1.0, _number(child.get("total")))))
    current_month = months[min(completed_count, count - 1)][0][:6]
    counters = _totals(results)
    for key in counters:
        counters[key] += int(_number(child.get(key)))
    return {
        **counters,
        "server_batch": True,
        "done": min(count * 1000, completed_count * 1000 + int(fraction * 1000)),
        "total": count * 1000,
        "current_month": f"{current_month[:4]}-{current_month[4:]}",
        "completed_months": completed_count,
        "total_months": count,
        "current_status": current.get("status", "running"),
        "message": ("全期間の処理が完了しました" if completed_count == count else
                    f"{current_month[:4]}年{current_month[4:]}月（{completed_count + 1}/{count}） "
                    + str(child.get("message") or "取得中")),
        "fetch_control": child.get("fetch_control"),
    }


def _aggregate(
    claim: OperationalClaim, results: dict[int, dict[str, Any]],
    *, batch_timings: dict[str, Any] | None = None,
) -> EffectResult:
    totals = _totals(results)
    success = all(item.get("success") is True for item in results.values())
    dry_run = claim.request_payload["dry_run"] is True
    elapsed = sum(_number(item.get("elapsed_time")) for item in results.values())
    metrics: dict[str, int] = {}
    for item in results.values():
        for key, value in (item.get("fetch_summary", {}).get("metrics") or {}).items():
            if isinstance(value, int) and not isinstance(value, bool):
                metrics[key] = metrics.get(key, 0) + value
    summary: dict[str, Any] = {
        "job_id": claim.job_id,
        "mode": "dry-run" if dry_run else "execute",
        "start_date": claim.request_payload["start_date"],
        "end_date": claim.request_payload["end_date"],
        "server_batch": True,
        "completed_months": len(results),
        "total_months": len(results),
        "elapsed_time_sec": elapsed,
        "metrics": metrics,
        "timings": {
            **(batch_timings or {}),
            "completed_attempt_work_wall_time_sec": sum(
                _number((item.get("fetch_summary", {}).get("timings") or {}).get("attempt_work_wall_time_sec"))
                for item in results.values()
            ),
            "completed_months_with_attempt_timings": sum(
                isinstance((item.get("fetch_summary", {}).get("timings") or {}).get("attempt_work_wall_time_sec"), (int, float))
                for item in results.values()
            ),
            "metrics_scope": "sum_of_completed_month_worker_attempts",
            "metrics_include_interrupted_attempts": False,
            "legacy_processing_time_sec": elapsed,
        },
        "verified_no_race_dates": totals["no_race_dates"],
        "execution_mode": "repair_missing_or_incomplete" if claim.request_payload["force_rescrape"] else "incremental",
        **totals,
    }
    if dry_run:
        dry_totals: dict[str, float] = {}
        for item in results.values():
            for key, value in (item.get("fetch_summary", {}).get("dry_run") or {}).items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    dry_totals[key] = dry_totals.get(key, 0) + value
        summary["dry_run"] = dry_totals
    result = {
        "success": success,
        "dry_run": dry_run,
        "server_batch": True,
        "races_collected": totals["saved_races"],
        "saved_horses": totals["saved_horses"],
        "existing_races_skipped": totals["existing_races_skipped"],
        "verified_no_race_dates": totals["no_race_dates"],
        "completed_months": len(results),
        "total_months": len(results),
        "elapsed_time": elapsed,
        "message": "全期間の処理が完了しました" if success else "処理完了。一部データは修復が必要です。",
        "fetch_summary": summary,
    }
    receipt = hashlib.sha256((claim.idempotency_key + "|" + json.dumps(
        result, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    )).encode()).hexdigest()
    return EffectResult(result=result, receipt_hash=receipt)


async def execute_server_batch(
    claim: OperationalClaim, store: SQLiteOperationalSagaStore, cancel_event: threading.Event,
) -> EffectResult:
    # ScrapeJobEffectExecutor holds the shared owner effect lock around both
    # this path and the old single-month path. Never take a second lock here.
    checkpoint = BatchCheckpointStore(store, claim)
    return await _execute_months(claim, checkpoint, cancel_event)


async def _execute_months(
    claim: OperationalClaim, checkpoint: BatchCheckpointStore, cancel_event: threading.Event,
) -> EffectResult:
    from scraping import jobs

    payload = claim.request_payload
    months = build_scrape_months(payload["start_date"], payload["end_date"])
    results = await asyncio.to_thread(checkpoint.completed)
    if len(results) > len(months):
        raise OperationalSagaConflict("batch-month-checkpoint-overflow")

    drain_event = threading.Event()

    def cancelled() -> bool:
        # Each existing scraper checkpoint checks the durable owner marker
        # AND the current fence. An expired claim never launches a new month.
        return drain_event.is_set() or checkpoint.cancel_requested() or cancel_event.is_set()

    for index, (start, end) in enumerate(months):
        if index in results:
            continue
        if cancelled():
            raise OperationalEffectCancelled(CANCELLED_BY_OWNER)
        child_id = month_job_id(claim.job_id, start, end)
        child_hash = hashlib.sha256(f"{claim.request_hash}|{start}|{end}".encode()).hexdigest()
        child = await asyncio.to_thread(jobs.get_job, child_id, owner_user_id=claim.owner_user_id)
        if child is not None and child.get("request_hash") != child_hash:
            raise OperationalSagaConflict("batch-child-binding-conflict")
        if child is None or child.get("status") != "completed":
            previous_progress = dict((child or {}).get("progress") or {})
            child = {
                "status": "recovering" if child else "queued",
                "progress": previous_progress,
                "result": None,
                "error": None,
                "owner_user_id": claim.owner_user_id,
                "request_hash": child_hash,
                "fencing_token": claim.fencing_token,
                "request": {
                    "start_date": start, "end_date": end,
                    "force_rescrape": payload["force_rescrape"], "dry_run": payload["dry_run"],
                    "operational_parent_job_id": claim.job_id,
                },
                "resume_count": int((child or {}).get("resume_count", 0)) + (1 if child else 0),
            }
            with jobs._JOBS_LOCK:
                jobs._scrape_jobs[child_id] = child
            await asyncio.to_thread(checkpoint.save, batch_progress(months, results, child))
            if cancelled():
                raise OperationalEffectCancelled(CANCELLED_BY_OWNER)
            child_task = asyncio.create_task(jobs._run_scrape_job(
                child_id, start, end, payload["force_rescrape"], payload["dry_run"],
                cancel_requested=cancelled,
            ))
            try:
                while not child_task.done():
                    await asyncio.wait({child_task}, timeout=1.0)
                    await asyncio.to_thread(checkpoint.save, batch_progress(months, results, child))
                await child_task
            finally:
                if not child_task.done():
                    # Do not cancel an in-flight asyncio.to_thread DB write:
                    # let it finish, then stop at the scraper's next safe
                    # checkpoint while retaining the OS effect lock.
                    drain_event.set()
                while not child_task.done():
                    try:
                        await asyncio.shield(child_task)
                    except asyncio.CancelledError:
                        # Repeated runtime/lease shutdown signals must not
                        # release the owner effect lock before the child has
                        # drained its already-started thread writes.
                        continue
                    except Exception:
                        break
        # A crash between the child receipt and parent checkpoint reuses the
        # persisted terminal child; it must not fetch the month again.
        if child.get("status") == "cancelled":
            await asyncio.to_thread(checkpoint.save, batch_progress(months, results, child))
            raise OperationalEffectCancelled(CANCELLED_BY_OWNER)
        if child.get("status") != "completed" or not isinstance(child.get("result"), dict):
            raise OperationalSagaError(str(child.get("error") or "batch-child-failed"))
        results[index] = dict(child["result"])
        await asyncio.to_thread(
            checkpoint.save, batch_progress(months, results), month_index=index,
            child_job_id=child_id, result=results[index],
        )
        # Cancellation racing the last completed child may settle completed;
        # otherwise the next loop sees the marker and never starts next month.
    return _aggregate(claim, results, batch_timings=await asyncio.to_thread(checkpoint.timings))
