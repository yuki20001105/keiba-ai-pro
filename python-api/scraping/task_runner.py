from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from scraping.recovery_actions import ActionContext, get_recovery_action
from scraping.recovery_engine import (
    resolve_recovery,
)
from scraping.task_models import ScrapeTask
from scraping.task_queue import TaskQueueProtocol
from scraping.quality_codes import E202_TASK_EXEC_EXCEPTION


class TaskRunner:
    def __init__(self, retry_backoff_seconds: tuple[float, ...] = (3.0, 10.0, 30.0)):
        self.retry_backoff_seconds = retry_backoff_seconds

    async def run(
        self,
        queue: TaskQueueProtocol,
        handler: Callable[[ScrapeTask], Awaitable[dict[str, Any]]],
        event_publisher: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, int]:
        severity_totals = {"INFO": 0, "WARNING": 0, "ERROR": 0, "FATAL": 0}
        policy_totals = {"RETRY": 0, "SKIP": 0, "ABORT": 0, "CONTINUE": 0}
        aborted = False
        while queue.has_items():
            task = queue.dequeue()
            if task is None:
                break

            queue.mark_running(task)
            try:
                result = await handler(task)
                if result.get("skip"):
                    queue.mark_skip(task)
                    if event_publisher:
                        event_publisher("task.skipped", {"task_id": task.task_id, "reason": "executor_skip"})
                    continue
                if result.get("ok", False):
                    queue.mark_success(task)
                    if event_publisher:
                        event_publisher("task.succeeded", {"task_id": task.task_id})
                    continue

                err = str(result.get("error") or "task failed")
                code = str(result.get("error_code") or "")
                decision = resolve_recovery(code)
                severity_totals[decision.severity] = int(severity_totals.get(decision.severity, 0)) + 1
                policy_totals[decision.policy] = int(policy_totals.get(decision.policy, 0)) + 1

                for plugin_name in decision.plugins:
                    plugin = get_recovery_action(plugin_name)
                    await plugin.execute(
                        ActionContext(
                            queue=queue,
                            task=task,
                            error=err,
                            event_publisher=event_publisher,
                            retry_backoff_seconds=self.retry_backoff_seconds,
                            retry_limit=decision.retry_limit,
                        )
                    )

                action = get_recovery_action(decision.action)
                action_result = await action.execute(
                    ActionContext(
                        queue=queue,
                        task=task,
                        error=err,
                        event_publisher=event_publisher,
                        retry_backoff_seconds=self.retry_backoff_seconds,
                        retry_limit=decision.retry_limit,
                    )
                )
                if action_result.aborted:
                    aborted = True
                    break
            except Exception as exc:
                err = str(exc)
                decision = resolve_recovery(E202_TASK_EXEC_EXCEPTION)
                severity_totals[decision.severity] = int(severity_totals.get(decision.severity, 0)) + 1
                policy_totals[decision.policy] = int(policy_totals.get(decision.policy, 0)) + 1

                for plugin_name in decision.plugins:
                    plugin = get_recovery_action(plugin_name)
                    await plugin.execute(
                        ActionContext(
                            queue=queue,
                            task=task,
                            error=err,
                            event_publisher=event_publisher,
                            retry_backoff_seconds=self.retry_backoff_seconds,
                            retry_limit=decision.retry_limit,
                        )
                    )

                action = get_recovery_action(decision.action)
                action_result = await action.execute(
                    ActionContext(
                        queue=queue,
                        task=task,
                        error=err,
                        event_publisher=event_publisher,
                        retry_backoff_seconds=self.retry_backoff_seconds,
                        retry_limit=decision.retry_limit,
                    )
                )
                if action_result.aborted:
                    aborted = True
                    break

        out = queue.stats()
        out["ABORTED"] = 1 if aborted else 0
        out["SEVERITY_INFO"] = int(severity_totals.get("INFO", 0))
        out["SEVERITY_WARNING"] = int(severity_totals.get("WARNING", 0))
        out["SEVERITY_ERROR"] = int(severity_totals.get("ERROR", 0))
        out["SEVERITY_FATAL"] = int(severity_totals.get("FATAL", 0))
        out["POLICY_RETRY"] = int(policy_totals.get("RETRY", 0))
        out["POLICY_SKIP"] = int(policy_totals.get("SKIP", 0))
        out["POLICY_ABORT"] = int(policy_totals.get("ABORT", 0))
        out["POLICY_CONTINUE"] = int(policy_totals.get("CONTINUE", 0))
        return out
