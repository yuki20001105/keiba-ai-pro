from __future__ import annotations

import csv
import json
import time
import asyncio
import threading
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import nbformat
from nbclient import NotebookClient
from nbclient.exceptions import CellTimeoutError


class TimeoutNotebookError(RuntimeError):
    """Raised when cell-level or notebook-level timeout is exceeded."""


@dataclass
class NotebookExecutionResult:
    notebook: str
    status: str
    started_at: str
    ended_at: str
    elapsed_sec: float
    retry: int
    error: str
    last_cell_number: int
    last_cell_source: str


def _timeout_status_from_error(error_text: str) -> str:
    text = str(error_text or "").lower()
    if "soft-timeout" in text:
        return "soft-timeout"
    if "hard-timeout" in text:
        return "hard-timeout"
    return "timeout"


class ExecutionTraceLogger:
    def __init__(self, trace_path: Path) -> None:
        self.trace_path = trace_path
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, payload: dict[str, Any]) -> None:
        row = dict(payload)
        row["logged_at"] = datetime.now().isoformat(timespec="seconds")
        with self.trace_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


class TracingNotebookClient(NotebookClient):
    def __init__(
        self,
        *args: Any,
        notebook_name: str,
        trace: ExecutionTraceLogger,
        notebook_start_mono: float,
        notebook_timeout_sec: int,
        state: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._notebook_name = notebook_name
        self._trace = trace
        self._notebook_start_mono = notebook_start_mono
        self._notebook_timeout_sec = notebook_timeout_sec
        self._state = state

    def _pre_cell(self, cell: Any, cell_index: int) -> tuple[int, str, float]:
        cell_number = cell_index + 1
        source = "".join(cell.get("source", ""))
        source_preview = source[:800].replace("\n", "\\n")
        self._state["last_cell_number"] = cell_number
        self._state["last_cell_source"] = source_preview

        elapsed_before = time.monotonic() - self._notebook_start_mono
        if elapsed_before > self._notebook_timeout_sec:
            msg = (
                f"notebook_timeout exceeded before cell execution: "
                f"elapsed={elapsed_before:.2f}s > limit={self._notebook_timeout_sec}s"
            )
            raise TimeoutNotebookError(msg)

        cell_start = time.monotonic()
        mem_gb = None
        try:
            import psutil

            mem_gb = round(psutil.Process().memory_info().rss / 1024**3, 3)
        except Exception:
            mem_gb = None

        self._trace.log(
            {
                "event": "cell_start",
                "notebook": self._notebook_name,
                "cell_number": cell_number,
                "cell_source": source_preview,
                "memory_gb": mem_gb,
                "retry_count": int(self._state.get("retry_count", 0)),
            }
        )
        return cell_number, source_preview, cell_start

    def _post_cell_ok(self, cell_number: int, cell_start: float) -> None:
        mem_gb = None
        try:
            import psutil

            mem_gb = round(psutil.Process().memory_info().rss / 1024**3, 3)
        except Exception:
            mem_gb = None
        self._trace.log(
            {
                "event": "cell_end",
                "notebook": self._notebook_name,
                "cell_number": cell_number,
                "status": "ok",
                "elapsed_sec": round(time.monotonic() - cell_start, 3),
                "memory_gb": mem_gb,
                "retry_count": int(self._state.get("retry_count", 0)),
            }
        )

    def _post_cell_error(self, cell_number: int, cell_start: float, status: str, err: str) -> None:
        mem_gb = None
        try:
            import psutil

            mem_gb = round(psutil.Process().memory_info().rss / 1024**3, 3)
        except Exception:
            mem_gb = None
        self._trace.log(
            {
                "event": "cell_end",
                "notebook": self._notebook_name,
                "cell_number": cell_number,
                "status": status,
                "elapsed_sec": round(time.monotonic() - cell_start, 3),
                "error": err,
                "exception": err,
                "memory_gb": mem_gb,
                "retry_count": int(self._state.get("retry_count", 0)),
            }
        )

    def execute_cell(  # type: ignore[override]
        self,
        cell: Any,
        cell_index: int,
        execution_count: int | None = None,
        store_history: bool = True,
    ) -> Any:
        cell_number, _source_preview, cell_start = self._pre_cell(cell, cell_index)
        try:
            out = super().execute_cell(
                cell,
                cell_index,
                execution_count=execution_count,
                store_history=store_history,
            )
            self._post_cell_ok(cell_number, cell_start)
            return out
        except CellTimeoutError as e:
            self._post_cell_error(cell_number, cell_start, "timeout", str(e))
            raise TimeoutNotebookError(
                f"cell_timeout exceeded at cell {cell_number}: {e}"
            ) from e
        except Exception as e:
            self._post_cell_error(cell_number, cell_start, "error", f"{type(e).__name__}: {e}")
            raise

    async def async_execute_cell(  # type: ignore[override]
        self,
        cell: Any,
        cell_index: int,
        execution_count: int | None = None,
        store_history: bool = True,
    ) -> Any:
        cell_number, _source_preview, cell_start = self._pre_cell(cell, cell_index)
        try:
            out = await super().async_execute_cell(
                cell,
                cell_index,
                execution_count=execution_count,
                store_history=store_history,
            )
            self._post_cell_ok(cell_number, cell_start)
            return out
        except CellTimeoutError as e:
            self._post_cell_error(cell_number, cell_start, "timeout", str(e))
            raise TimeoutNotebookError(
                f"cell_timeout exceeded at cell {cell_number}: {e}"
            ) from e
        except Exception as e:
            self._post_cell_error(cell_number, cell_start, "error", f"{type(e).__name__}: {e}")
            raise


def execute_notebook_with_retry(
    notebook_path: Path,
    *,
    cell_timeout: int = 600,
    notebook_timeout: int = 7200,
    max_retry: int = 3,
    trace: ExecutionTraceLogger,
    kernel_env: dict[str, str] | None = None,
) -> NotebookExecutionResult:
    if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    state = {"last_cell_number": 0, "last_cell_source": "", "retry_count": 0}
    notebook_name = notebook_path.name
    attempts = max_retry + 1
    last_error = ""
    final_status = "error"
    started_at = datetime.now().isoformat(timespec="seconds")
    started_mono = time.monotonic()
    used_retry = max_retry

    for attempt in range(1, attempts + 1):
        state["retry_count"] = attempt - 1
        heartbeats: list[dict[str, Any]] = []
        stop_evt = threading.Event()

        def _monitor_resources() -> None:
            try:
                import psutil

                proc = psutil.Process()
                while not stop_evt.is_set():
                    hb = {
                        "event": "heartbeat",
                        "notebook": notebook_name,
                        "attempt": attempt,
                        "cpu_percent": psutil.cpu_percent(interval=1.0),
                        "rss_mb": round(proc.memory_info().rss / 1024**2, 2),
                        "system_mem_percent": psutil.virtual_memory().percent,
                        "last_cell_number": state.get("last_cell_number", 0),
                    }
                    heartbeats.append(hb)
                    trace.log(hb)
                    # 30秒ごと記録（1秒CPU計測 + 29秒待機）
                    if stop_evt.wait(29.0):
                        break
            except Exception as e:
                trace.log(
                    {
                        "event": "heartbeat_error",
                        "notebook": notebook_name,
                        "attempt": attempt,
                        "error": f"{type(e).__name__}: {e}",
                    }
                )

        mon_thread = threading.Thread(target=_monitor_resources, daemon=True)
        mon_thread.start()

        trace.log(
            {
                "event": "notebook_start",
                "notebook": notebook_name,
                "attempt": attempt,
                "cell_timeout": cell_timeout,
                "notebook_timeout": notebook_timeout,
                "audit_mode": (kernel_env or {}).get("AUDIT_MODE") == "1",
            }
        )

        client: TracingNotebookClient | None = None
        attempt_start = time.monotonic()
        try:
            nb = nbformat.read(notebook_path, as_version=4)
            client = TracingNotebookClient(
                nb,
                timeout=cell_timeout,
                kernel_name="python3",
                allow_errors=False,
                resources={"metadata": {"path": str(notebook_path.parent)}},
                notebook_name=notebook_name,
                trace=trace,
                notebook_start_mono=started_mono,
                notebook_timeout_sec=notebook_timeout,
                state=state,
            )
            # Propagate run-scoped environment (e.g. AUDIT_MODE=1) to kernel process.
            old_env: dict[str, str | None] = {}
            if kernel_env:
                for k, v in kernel_env.items():
                    old_env[k] = os.environ.get(k)
                    os.environ[k] = str(v)
            try:
                client.execute()
            finally:
                if kernel_env:
                    for k in kernel_env:
                        prev = old_env.get(k)
                        if prev is None:
                            os.environ.pop(k, None)
                        else:
                            os.environ[k] = prev
            nbformat.write(nb, notebook_path)
            final_status = "success"
            last_error = ""
            used_retry = attempt - 1
            trace.log(
                {
                    "event": "notebook_end",
                    "notebook": notebook_name,
                    "attempt": attempt,
                    "status": "success",
                    "elapsed_sec": round(time.monotonic() - attempt_start, 3),
                }
            )
            break
        except TimeoutNotebookError as e:
            final_status = "timeout"
            last_error = str(e)
            timeout_type = "hard"
            if len(heartbeats) >= 2:
                cpu_activity = any(float(h.get("cpu_percent", 0)) >= 2.0 for h in heartbeats)
                rss_vals = [float(h.get("rss_mb", 0)) for h in heartbeats]
                rss_delta = (max(rss_vals) - min(rss_vals)) if rss_vals else 0.0
                mem_activity = rss_delta >= 64.0
                if cpu_activity or mem_activity:
                    timeout_type = "soft"
            last_error = f"[{timeout_type}-timeout] {last_error}"
            used_retry = attempt - 1
            trace.log(
                {
                    "event": "timeout",
                    "notebook": notebook_name,
                    "attempt": attempt,
                    "status": _timeout_status_from_error(last_error),
                    "error": last_error,
                    "last_cell_number": state["last_cell_number"],
                    "last_cell_source": state["last_cell_source"],
                }
            )
            trace.log(
                {
                    "event": "notebook_end",
                    "notebook": notebook_name,
                    "attempt": attempt,
                    "status": "timeout",
                    "elapsed_sec": round(time.monotonic() - attempt_start, 3),
                    "error": last_error,
                    "timeout_type": timeout_type,
                    "last_cell_number": state["last_cell_number"],
                    "last_cell_source": state["last_cell_source"],
                }
            )
        except Exception as e:
            if isinstance(e, KeyboardInterrupt):
                final_status = "interrupted"
                last_error = "KeyboardInterrupt"
                used_retry = attempt - 1
                trace.log(
                    {
                        "event": "exception",
                        "notebook": notebook_name,
                        "attempt": attempt,
                        "status": final_status,
                        "error": last_error,
                        "last_cell_number": state["last_cell_number"],
                        "last_cell_source": state["last_cell_source"],
                    }
                )
                break
            msg = f"{type(e).__name__}: {e}"
            is_timeout_like = (
                isinstance(e, TimeoutError)
                or "timeout" in msg.lower()
                or "timed out" in msg.lower()
                or "execute reply" in msg.lower()
            )
            if is_timeout_like:
                final_status = "timeout"
                last_error = f"TimeoutNotebookError: {msg}"
                trace.log(
                    {
                        "event": "timeout",
                        "notebook": notebook_name,
                        "attempt": attempt,
                        "status": "timeout",
                        "error": last_error,
                        "last_cell_number": state["last_cell_number"],
                        "last_cell_source": state["last_cell_source"],
                    }
                )
            else:
                final_status = "error"
                last_error = msg
            used_retry = attempt - 1
            trace.log(
                {
                    "event": "exception",
                    "notebook": notebook_name,
                    "attempt": attempt,
                    "status": final_status,
                    "error": last_error,
                    "last_cell_number": state["last_cell_number"],
                    "last_cell_source": state["last_cell_source"],
                }
            )
            trace.log(
                {
                    "event": "notebook_end",
                    "notebook": notebook_name,
                    "attempt": attempt,
                    "status": final_status,
                    "elapsed_sec": round(time.monotonic() - attempt_start, 3),
                    "error": last_error,
                    "last_cell_number": state["last_cell_number"],
                    "last_cell_source": state["last_cell_source"],
                }
            )
        finally:
            stop_evt.set()
            try:
                mon_thread.join(timeout=2.0)
            except Exception:
                pass

        if attempt < attempts:
            trace.log(
                {
                    "event": "retry",
                    "notebook": notebook_name,
                    "attempt": attempt,
                    "next_attempt": attempt + 1,
                    "reason": last_error,
                }
            )
            try:
                if client is not None and getattr(client, "km", None) is not None:
                    client.km.restart_kernel(now=True)
                    trace.log(
                        {
                            "event": "kernel_restart",
                            "notebook": notebook_name,
                            "attempt": attempt,
                            "status": "ok",
                        }
                    )
            except Exception as re:
                trace.log(
                    {
                        "event": "kernel_restart",
                        "notebook": notebook_name,
                        "attempt": attempt,
                        "status": "error",
                        "error": f"{type(re).__name__}: {re}",
                    }
                )

    ended_at = datetime.now().isoformat(timespec="seconds")
    return NotebookExecutionResult(
        notebook=notebook_name,
        status=final_status,
        started_at=started_at,
        ended_at=ended_at,
        elapsed_sec=round(time.monotonic() - started_mono, 3),
        retry=max(0, used_retry),
        error=last_error,
        last_cell_number=int(state["last_cell_number"]),
        last_cell_source=str(state["last_cell_source"]),
    )


def write_detailed_execution_csv(trace_path: Path, out_path: Path) -> None:
    """Write cell-level execution log from JSONL trace.

    Output schema:
      notebook, cell, status, elapsed, retry_count, memory_gb, exception
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    active: dict[tuple[str, int, int], dict[str, Any]] = {}
    rows: list[list[Any]] = []
    nb_retry = defaultdict(int)

    if not trace_path.exists():
        with out_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["notebook", "cell", "status", "elapsed", "retry_count", "memory_gb", "exception"])
        return

    with trace_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue

            event = str(obj.get("event", ""))
            nb = str(obj.get("notebook", ""))
            if not nb:
                continue

            if event == "retry":
                nb_retry[nb] = int(obj.get("next_attempt", nb_retry[nb] + 1)) - 1
                continue

            if event == "cell_start":
                cell = int(obj.get("cell_number", 0))
                retry = int(obj.get("retry_count", nb_retry[nb]))
                active[(nb, cell, retry)] = obj
                continue

            if event == "cell_end":
                cell = int(obj.get("cell_number", 0))
                retry = int(obj.get("retry_count", nb_retry[nb]))
                key = (nb, cell, retry)
                start = active.get(key, {})
                elapsed = obj.get("elapsed_sec", "")
                if elapsed == "":
                    elapsed = ""
                else:
                    elapsed = f"{float(elapsed):.3f}"
                mem = obj.get("memory_gb", start.get("memory_gb", ""))
                if mem in (None, ""):
                    mem_out = ""
                else:
                    mem_out = f"{float(mem):.3f}"
                rows.append(
                    [
                        nb,
                        cell,
                        str(obj.get("status", "")),
                        elapsed,
                        retry,
                        mem_out,
                        str(obj.get("exception", obj.get("error", ""))),
                    ]
                )
                continue

            if event == "notebook_end" and str(obj.get("status", "")) in {"timeout", "error", "interrupted"}:
                status = str(obj.get("status", ""))
                if status == "timeout":
                    status = _timeout_status_from_error(str(obj.get("error", "")))
                rows.append(
                    [
                        nb,
                        int(obj.get("last_cell_number", 0)),
                        status,
                        f"{float(obj.get('elapsed_sec', 0.0)):.3f}",
                        int(obj.get("attempt", 1)) - 1,
                        "",
                        str(obj.get("error", "")),
                    ]
                )

    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["notebook", "cell", "status", "elapsed", "retry_count", "memory_gb", "exception"])
        for row in rows:
            w.writerow(row)


def write_execution_csv(path: Path, rows: list[NotebookExecutionResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["notebook", "status", "elapsed_sec", "retry", "error"])
        for r in rows:
            w.writerow([r.notebook, r.status, f"{r.elapsed_sec:.3f}", r.retry, r.error])


def write_execution_report(path: Path, rows: list[NotebookExecutionResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# notebook_execution_report")
    lines.append("")
    lines.append("| Notebook名 | 実行時間(sec) | 成否 | エラー内容 | リトライ回数 |")
    lines.append("|---|---:|---|---|---:|")
    for r in rows:
        err = r.error.replace("\n", " ")[:200]
        lines.append(
            f"| {r.notebook} | {r.elapsed_sec:.3f} | {r.status} | {err} | {r.retry} |"
        )

    lines.append("")
    lines.append("## 実行中セル最終地点")
    lines.append("")
    for r in rows:
        lines.append(f"- {r.notebook}: cell={r.last_cell_number}, source={r.last_cell_source}")

    path.write_text("\n".join(lines), encoding="utf-8")
