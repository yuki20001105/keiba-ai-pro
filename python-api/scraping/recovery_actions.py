from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scraping.cache import html_cache_path
from scraping.task_models import ScrapeTask
from scraping.task_queue import TaskQueueProtocol


@dataclass
class ActionContext:
    queue: TaskQueueProtocol
    task: ScrapeTask
    error: str
    event_publisher: Any | None = None
    retry_backoff_seconds: tuple[float, ...] = (3.0, 10.0, 30.0)
    retry_limit: int | None = None


@dataclass
class ActionResult:
    aborted: bool = False


class RecoveryAction:
    name = "BASE"

    async def execute(self, ctx: ActionContext) -> ActionResult:
        return ActionResult()


class RetryAction(RecoveryAction):
    name = "RETRY"

    async def execute(self, ctx: ActionContext) -> ActionResult:
        retry_limit = ctx.retry_limit if ctx.retry_limit is not None else ctx.task.max_attempts
        if ctx.task.attempts <= retry_limit:
            ctx.queue.mark_retry(ctx.task, ctx.error)
            wait_idx = min(ctx.task.attempts - 1, max(0, len(ctx.retry_backoff_seconds) - 1))
            await asyncio.sleep(ctx.retry_backoff_seconds[wait_idx])
            ctx.queue.requeue(ctx.task)
            if ctx.event_publisher:
                ctx.event_publisher("task.retried", {"task_id": ctx.task.task_id, "attempts": ctx.task.attempts})
        else:
            ctx.queue.mark_failed(ctx.task, ctx.error)
            if ctx.event_publisher:
                ctx.event_publisher("task.retry_exhausted", {"task_id": ctx.task.task_id, "attempts": ctx.task.attempts})
        return ActionResult()


class SkipAction(RecoveryAction):
    name = "SKIP"

    async def execute(self, ctx: ActionContext) -> ActionResult:
        ctx.queue.mark_skip(ctx.task)
        if ctx.event_publisher:
            ctx.event_publisher("task.skipped", {"task_id": ctx.task.task_id})
        return ActionResult()


class AbortAction(RecoveryAction):
    name = "ABORT"

    async def execute(self, ctx: ActionContext) -> ActionResult:
        ctx.queue.mark_failed(ctx.task, ctx.error)
        if ctx.event_publisher:
            ctx.event_publisher("task.aborted", {"task_id": ctx.task.task_id, "error": ctx.error})
        return ActionResult(aborted=True)


class ContinueAction(RecoveryAction):
    name = "CONTINUE"

    async def execute(self, ctx: ActionContext) -> ActionResult:
        ctx.queue.mark_failed(ctx.task, ctx.error)
        if ctx.event_publisher:
            ctx.event_publisher("task.continued", {"task_id": ctx.task.task_id})
        return ActionResult()


class InvalidateCacheAction(RecoveryAction):
    name = "INVALIDATE_CACHE"

    async def execute(self, ctx: ActionContext) -> ActionResult:
        payload = ctx.task.payload or {}
        date = str(payload.get("date") or "")
        race_id = str(payload.get("race_id") or "")
        candidates: list[Path] = []
        if date:
            candidates.append(html_cache_path("db_race_list", date))
            candidates.append(html_cache_path("race_list_sub", date))
        if race_id:
            candidates.append(html_cache_path("race", race_id))
        for p in candidates:
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass
        ctx.task.meta["invalidate_cache"] = True
        if ctx.event_publisher:
            ctx.event_publisher("recovery.cache_invalidated", {"task_id": ctx.task.task_id, "date": date})
        return ActionResult()


class ReconnectAction(RecoveryAction):
    name = "RECONNECT"

    async def execute(self, ctx: ActionContext) -> ActionResult:
        ctx.task.meta["reconnect_requested"] = True
        if ctx.event_publisher:
            ctx.event_publisher("recovery.reconnect_requested", {"task_id": ctx.task.task_id})
        return ActionResult()


class ReplayAction(RecoveryAction):
    name = "REPLAY"

    async def execute(self, ctx: ActionContext) -> ActionResult:
        ctx.task.meta["replay_requested"] = True
        if ctx.event_publisher:
            ctx.event_publisher("recovery.replay_requested", {"task_id": ctx.task.task_id})
        return ActionResult()


_ACTIONS: dict[str, RecoveryAction] = {
    RetryAction.name: RetryAction(),
    SkipAction.name: SkipAction(),
    AbortAction.name: AbortAction(),
    ContinueAction.name: ContinueAction(),
    InvalidateCacheAction.name: InvalidateCacheAction(),
    ReconnectAction.name: ReconnectAction(),
    ReplayAction.name: ReplayAction(),
}


def get_recovery_action(name: str) -> RecoveryAction:
    key = str(name or "").upper()
    return _ACTIONS.get(key, _ACTIONS[ContinueAction.name])
