from __future__ import annotations

import argparse
import json
import os
import socket
import statistics
import subprocess
import sys
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from create_scenario_router_test_fixture import FixtureInfo, cleanup_fixture, create_fixture
from scenario_router_audit_triage import build_audit_triage, classify_stderr_for_step, summarize_stderr_classification


ROOT = Path(__file__).resolve().parents[1]
PYTHON_API_DIR = ROOT / "python-api"
REPORTS_DIR = ROOT / "reports"
REPORT_MD = REPORTS_DIR / "scenario_router_audit_report.md"
REPORT_JSON = REPORTS_DIR / "scenario_router_audit_result.json"
HISTORY_JSONL = REPORTS_DIR / "scenario_router_audit_history.jsonl"
TREND_MD = REPORTS_DIR / "scenario_router_audit_trend.md"
BASE_URL = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000")
HEALTH_URL = f"{BASE_URL}/health"
HEALTH_TIMEOUT_SEC = float(os.environ.get("SCENARIO_ROUTER_AUDIT_HEALTH_TIMEOUT_SEC", "180"))
DEFAULT_SANDBOX = str(os.environ.get("SCENARIO_ROUTER_AUDIT_SANDBOX") or "").strip() in {"1", "true", "TRUE"}
DEFAULT_SANDBOX_CLEANUP = str(os.environ.get("SCENARIO_ROUTER_AUDIT_CLEANUP") or "1").strip() in {"1", "true", "TRUE"}


@dataclass
class StepResult:
    name: str
    status: str
    duration_sec: float
    error_message: str
    stdout_tail: str
    stderr_tail: str
    exit_code: int
    stderr_classification: str
    stderr_is_noise: bool


def _git_value(args: list[str]) -> str:
    try:
        cp = subprocess.run(
            ["git", *args],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=10,
        )
        if cp.returncode == 0:
            return str(cp.stdout or "").strip()
    except Exception:
        pass
    return ""


def _history_entry(payload: dict[str, Any]) -> dict[str, Any]:
    steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
    failed_step = ""
    for s in steps:
        if isinstance(s, dict) and str(s.get("status") or "") != "PASS":
            failed_step = str(s.get("name") or "")
            break

    triage = payload.get("triage") if isinstance(payload.get("triage"), dict) else {}
    sandbox = payload.get("sandbox") if isinstance(payload.get("sandbox"), dict) else {}
    stderr_summary = payload.get("stderr_summary") if isinstance(payload.get("stderr_summary"), dict) else {}

    stderr_affected = stderr_summary.get("affected_steps") if isinstance(stderr_summary.get("affected_steps"), list) else []
    stderr_class = str(stderr_summary.get("classification") or "NO_STDERR")
    stderr_evidence = str(stderr_summary.get("evidence") or "")
    if len(stderr_evidence) > 280:
        stderr_evidence = stderr_evidence[:277] + "..."

    step_items: list[dict[str, Any]] = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        step_items.append(
            {
                "step_name": str(s.get("name") or ""),
                "status": str(s.get("status") or ""),
                "duration_sec": float(s.get("duration_sec") or 0.0),
                "error_message": str(s.get("error_message") or ""),
                "failure_type": (str(triage.get("failure_type") or "") if str(s.get("status") or "") != "PASS" else ""),
                "stderr_classification": str(s.get("stderr_classification") or "NO_STDERR"),
                "stderr_is_noise": bool(s.get("stderr_is_noise")),
            }
        )

    return {
        "run_id": f"sraudit_{uuid.uuid4().hex[:12]}",
        "timestamp": str(payload.get("finished_at") or payload.get("started_at") or _now_iso()),
        "started_at": str(payload.get("started_at") or ""),
        "finished_at": str(payload.get("finished_at") or ""),
        "duration_sec": float(payload.get("duration_sec") or 0.0),
        "overall_status": str((payload.get("summary") or {}).get("overall_status") or ""),
        "failure_type": str(triage.get("failure_type") or "NONE"),
        "likely_cause": str(triage.get("likely_cause") or ""),
        "failed_step": failed_step,
        "sandbox_enabled": bool(sandbox.get("enabled")),
        "sandbox_cleanup_status": str(sandbox.get("cleanup_status") or ""),
        "stderr_summary": {
            "has_stderr": bool(stderr_summary.get("has_stderr")),
            "classification": stderr_class,
            "is_noise": bool(stderr_summary.get("is_noise")),
            "affected_steps": [str(x) for x in stderr_affected if str(x)],
            "evidence": stderr_evidence,
        },
        "git_sha": _git_value(["rev-parse", "HEAD"]),
        "branch": _git_value(["rev-parse", "--abbrev-ref", "HEAD"]),
        "steps": step_items,
    }


def _read_history(limit: int = 500) -> list[dict[str, Any]]:
    if not HISTORY_JSONL.exists():
        return []
    rows: list[dict[str, Any]] = []
    for ln in HISTORY_JSONL.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = ln.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
        except Exception:
            continue
    if limit > 0 and len(rows) > limit:
        return rows[-limit:]
    return rows


def _success_rate(rows: list[dict[str, Any]]) -> float:
    if not rows:
        return 0.0
    ok = sum(1 for r in rows if str(r.get("overall_status") or "") == "PASS")
    return ok / float(len(rows))


def _intermittent_failure(rows: list[dict[str, Any]]) -> bool:
    # Same failure_type appearing at least twice with at least one different run between them.
    idx_map: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        ft = str(r.get("failure_type") or "")
        if ft and ft != "NONE":
            idx_map[ft].append(i)
    for _, idxs in idx_map.items():
        if len(idxs) < 2:
            continue
        for a, b in zip(idxs, idxs[1:]):
            if b - a > 1:
                return True
    return False


def _duration_spike(rows: list[dict[str, Any]]) -> bool:
    if len(rows) < 2:
        return False
    prev = rows[-2]
    curr = rows[-1]

    def _step_map(row: dict[str, Any]) -> dict[str, float]:
        out: dict[str, float] = {}
        for s in (row.get("steps") or []):
            if not isinstance(s, dict):
                continue
            out[str(s.get("step_name") or "")] = float(s.get("duration_sec") or 0.0)
        return out

    p = _step_map(prev)
    c = _step_map(curr)
    for k, cv in c.items():
        pv = float(p.get(k) or 0.0)
        if pv > 0 and cv >= (pv * 2.0):
            return True
    return False


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    q = max(0.0, min(100.0, float(p))) / 100.0
    s = sorted(float(x) for x in values)
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    if lo == hi:
        return float(s[lo])
    frac = pos - lo
    return float(s[lo] * (1.0 - frac) + s[hi] * frac)


def _build_trend(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    last_10 = rows[-10:]
    last_30 = rows[-30:]
    latest = rows[-1] if rows else {}

    fail_types = [
        str(r.get("failure_type") or "")
        for r in last_30
        if str(r.get("overall_status") or "") != "PASS" and str(r.get("failure_type") or "")
    ]
    cnt = Counter(fail_types)
    common_fail = [{"failure_type": k, "count": int(v)} for k, v in cnt.most_common(5)]

    step_durations: dict[str, list[float]] = defaultdict(list)
    for r in last_30:
        for s in (r.get("steps") or []):
            if not isinstance(s, dict):
                continue
            nm = str(s.get("step_name") or "")
            if not nm:
                continue
            step_durations[nm].append(float(s.get("duration_sec") or 0.0))

    slowest = []
    for nm, vals in step_durations.items():
        if not vals:
            continue
        avg = sum(vals) / float(len(vals))
        slowest.append({"step_name": nm, "avg_duration_sec": avg, "samples": len(vals)})
    slowest.sort(key=lambda x: float(x.get("avg_duration_sec") or 0.0), reverse=True)
    slowest = slowest[:5]

    baseline_window = 10
    baseline_rows = rows[-baseline_window:] if baseline_window > 0 else list(rows)
    latest_steps: dict[str, float] = {}
    if latest:
        for s in (latest.get("steps") or []):
            if not isinstance(s, dict):
                continue
            nm = str(s.get("step_name") or "")
            if not nm:
                continue
            latest_steps[nm] = float(s.get("duration_sec") or 0.0)

    baseline_by_step: list[dict[str, Any]] = []
    for nm in sorted(step_durations.keys()):
        vals: list[float] = []
        for r in baseline_rows:
            for s in (r.get("steps") or []):
                if not isinstance(s, dict):
                    continue
                if str(s.get("step_name") or "") != nm:
                    continue
                vals.append(float(s.get("duration_sec") or 0.0))
        if not vals:
            continue
        baseline_by_step.append(
            {
                "step_name": nm,
                "samples": len(vals),
                "latest_duration_sec": float(latest_steps.get(nm) or 0.0),
                "median_duration_sec": float(statistics.median(vals)),
                "mean_duration_sec": float(statistics.mean(vals)),
                "p95_duration_sec": _percentile(vals, 95.0),
            }
        )

    mixed_last5 = False
    last5 = rows[-5:]
    if last5:
        sts = {str(r.get("overall_status") or "") for r in last5}
        mixed_last5 = ("PASS" in sts) and ("FAIL" in sts)

    flaky = bool(mixed_last5 or _intermittent_failure(last_30) or _duration_spike(last_30))

    def _stderr_info(row: dict[str, Any]) -> dict[str, Any]:
        ss = row.get("stderr_summary") if isinstance(row.get("stderr_summary"), dict) else {}
        classification = str(ss.get("classification") or "")
        has_stderr = bool(ss.get("has_stderr"))
        if not classification:
            classification = "NO_STDERR"
            has_stderr = False
        affected_steps = ss.get("affected_steps") if isinstance(ss.get("affected_steps"), list) else []
        return {
            "has_stderr": bool(has_stderr),
            "classification": str(classification),
            "is_noise": bool(ss.get("is_noise", not has_stderr)),
            "affected_steps": [str(x) for x in affected_steps if str(x)],
        }

    stderr_last10 = rows[-10:]
    stderr_infos = [_stderr_info(r) for r in stderr_last10]
    stderr_n = len(stderr_infos)
    noise_count = sum(1 for x in stderr_infos if bool(x.get("has_stderr")) and bool(x.get("is_noise")))
    real_count = sum(1 for x in stderr_infos if bool(x.get("has_stderr")) and not bool(x.get("is_noise")))
    class_counter = Counter(str(x.get("classification") or "NO_STDERR") for x in stderr_infos)

    noisy_steps_counter: Counter[str] = Counter()
    real_steps_counter: Counter[str] = Counter()
    for x in stderr_infos:
        steps = x.get("affected_steps") if isinstance(x.get("affected_steps"), list) else []
        if not steps:
            continue
        if bool(x.get("is_noise")):
            noisy_steps_counter.update([str(s) for s in steps if str(s)])
        else:
            real_steps_counter.update([str(s) for s in steps if str(s)])

    latest_stderr = _stderr_info(latest) if latest else {
        "classification": "NO_STDERR",
        "has_stderr": False,
        "is_noise": True,
        "affected_steps": [],
    }

    common_stderr_classifications = [
        {"classification": k, "count": int(v)}
        for k, v in class_counter.most_common(8)
    ]
    noisy_steps = [{"step_name": k, "count": int(v)} for k, v in noisy_steps_counter.most_common(8)]
    real_error_steps = [{"step_name": k, "count": int(v)} for k, v in real_steps_counter.most_common(8)]

    prev10 = rows[-20:-10] if len(rows) >= 20 else []
    prev_real_rate = 0.0
    if prev10:
        prev_infos = [_stderr_info(r) for r in prev10]
        prev_real = sum(1 for x in prev_infos if bool(x.get("has_stderr")) and not bool(x.get("is_noise")))
        prev_real_rate = prev_real / float(len(prev_infos))

    last_10_stderr_noise_rate = (noise_count / float(stderr_n)) if stderr_n else 0.0
    last_10_real_stderr_rate = (real_count / float(stderr_n)) if stderr_n else 0.0

    stderr_suggested = "stderr trend stable. continue monitoring."
    if last_10_real_stderr_rate > max(0.0, prev_real_rate + 0.15):
        stderr_suggested = "real-error stderr increased in recent runs; inspect import/traceback signals and failing steps."
    elif last_10_stderr_noise_rate >= 0.4:
        stderr_suggested = "noise stderr ratio is high; keep classifying as noise and check shell/log output verbosity."

    stderr_trend = {
        "last_10_stderr_noise_rate": last_10_stderr_noise_rate,
        "last_10_real_stderr_rate": last_10_real_stderr_rate,
        "common_stderr_classifications": common_stderr_classifications,
        "noisy_steps": noisy_steps,
        "real_error_steps": real_error_steps,
        "latest_stderr_classification": str(latest_stderr.get("classification") or "NO_STDERR"),
        "suggested_next_action": stderr_suggested,
    }

    latest_status = str(latest.get("overall_status") or "")
    latest_failure = str(latest.get("failure_type") or "NONE")
    if latest_status == "FAIL":
        suggested = f"Triage indicates {latest_failure}. Apply suggested_fix from latest report and rerun sandbox audit."
    elif flaky:
        suggested = "Audit appears flaky. Preserve sandbox on next failure and compare step durations/evidence."
    else:
        suggested = "No immediate reliability risk detected. Continue scheduled audits."

    trend = {
        "latest_status": latest_status,
        "last_10_success_rate": _success_rate(last_10),
        "last_30_success_rate": _success_rate(last_30),
        "common_failure_types": common_fail,
        "slowest_steps": slowest,
        "baseline_window": baseline_window,
        "step_baselines": baseline_by_step,
        "flaky_warning": bool(flaky),
        "suggested_next_action": suggested,
        "stderr_trend": stderr_trend,
    }

    lines: list[str] = []
    lines.append("# Scenario Router Audit Trend")
    lines.append("")
    lines.append(f"- latest_status: {trend['latest_status']}")
    lines.append(f"- last_10_success_rate: {trend['last_10_success_rate']:.2%}")
    lines.append(f"- last_30_success_rate: {trend['last_30_success_rate']:.2%}")
    lines.append(f"- flaky_warning: {trend['flaky_warning']}")
    lines.append(f"- suggested_next_action: {trend['suggested_next_action']}")
    lines.append("")
    lines.append("## Common Failure Types")
    lines.append("")
    if common_fail:
        for x in common_fail:
            lines.append(f"- {x['failure_type']}: {x['count']}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Slowest Steps (Avg)")
    lines.append("")
    if slowest:
        for x in slowest:
            lines.append(f"- {x['step_name']}: {x['avg_duration_sec']:.2f}s (n={x['samples']})")
    else:
        lines.append("- none")

    lines.append("")
    lines.append(f"## Step Baselines (Last {baseline_window})")
    lines.append("")
    if baseline_by_step:
        lines.append("| step_name | latest_sec | median_sec | mean_sec | p95_sec | samples |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for x in baseline_by_step:
            lines.append(
                f"| {x['step_name']} | {float(x['latest_duration_sec']):.2f} | {float(x['median_duration_sec']):.2f} | {float(x['mean_duration_sec']):.2f} | {float(x['p95_duration_sec']):.2f} | {int(x['samples'])} |"
            )
    else:
        lines.append("- none")

    lines.append("")
    lines.append("## stderr Noise Trend")
    lines.append("")
    lines.append(f"- Latest classification: {stderr_trend['latest_stderr_classification']}")
    lines.append(f"- Last 10 noise rate: {stderr_trend['last_10_stderr_noise_rate']:.2%}")
    lines.append(f"- Last 10 real error rate: {stderr_trend['last_10_real_stderr_rate']:.2%}")
    lines.append(f"- Suggested next action: {stderr_trend['suggested_next_action']}")
    lines.append("")
    lines.append("### Common classifications")
    if common_stderr_classifications:
        for x in common_stderr_classifications:
            lines.append(f"- {x['classification']}: {x['count']}")
    else:
        lines.append("- none")

    lines.append("")
    lines.append("### Noisy steps")
    if noisy_steps:
        for x in noisy_steps:
            lines.append(f"- {x['step_name']}: {x['count']}")
    else:
        lines.append("- none")

    lines.append("")
    lines.append("### Real error steps")
    if real_error_steps:
        for x in real_error_steps:
            lines.append(f"- {x['step_name']}: {x['count']}")
    else:
        lines.append("- none")

    return trend, "\n".join(lines) + "\n"


def _update_history_and_trend(payload: dict[str, Any]) -> dict[str, Any]:
    info: dict[str, Any] = {
        "history_path": str(HISTORY_JSONL),
        "trend_path": str(TREND_MD),
        "append_ok": False,
        "trend_ok": False,
        "error": "",
    }
    try:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        entry = _history_entry(payload)
        with HISTORY_JSONL.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        info["append_ok"] = True

        rows = _read_history(limit=1000)
        trend, md = _build_trend(rows)
        TREND_MD.write_text(md, encoding="utf-8")
        info["trend_ok"] = True
        info["history_count"] = len(rows)
        info["trend"] = trend
    except Exception as e:
        info["error"] = str(e)
    return info


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_tail(text: str, max_chars: int = 4000) -> str:
    s = str(text or "")
    if len(s) <= max_chars:
        return s
    return s[-max_chars:]


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _health_ok(timeout: float = 5.0) -> bool:
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(HEALTH_URL)
            return r.status_code == 200
    except Exception:
        return False


def _wait_health(timeout_sec: float) -> bool:
    deadline = time.time() + max(1.0, timeout_sec)
    while time.time() < deadline:
        if _health_ok(timeout=3.0):
            return True
        time.sleep(1.0)
    return False


def _resolve_python_executable() -> str:
    # Prefer workspace venv, then Python API venv, then current interpreter.
    candidates = [
        ROOT / ".venv" / "Scripts" / "python.exe",
        PYTHON_API_DIR / ".venv" / "Scripts" / "python.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return sys.executable


def _run_step(
    *,
    name: str,
    command: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout_sec: float | None = None,
) -> StepResult:
    t0 = time.time()
    try:
        cp = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
        dt = time.time() - t0
        ok = cp.returncode == 0
        stderr_cls = classify_stderr_for_step(
            exit_code=int(cp.returncode),
            stderr_text=str(cp.stderr or ""),
            stdout_text=str(cp.stdout or ""),
        )
        return StepResult(
            name=name,
            status="PASS" if ok else "FAIL",
            duration_sec=dt,
            error_message=("" if ok else f"exit_code={cp.returncode}"),
            stdout_tail=_safe_tail(str(cp.stdout or "")),
            stderr_tail=_safe_tail(str(cp.stderr or "")),
            exit_code=int(cp.returncode),
            stderr_classification=str(stderr_cls.get("classification") or "UNKNOWN_STDERR"),
            stderr_is_noise=bool(stderr_cls.get("is_noise")),
        )
    except Exception as e:
        dt = time.time() - t0
        return StepResult(
            name=name,
            status="FAIL",
            duration_sec=dt,
            error_message=str(e),
            stdout_tail="",
            stderr_tail="",
            exit_code=1,
            stderr_classification="UNKNOWN_STDERR",
            stderr_is_noise=False,
        )


def _enrich_step_stderr_meta(results: list[StepResult]) -> list[StepResult]:
    out: list[StepResult] = []
    for r in results:
        cls = classify_stderr_for_step(
            exit_code=int(r.exit_code),
            stderr_text=str(r.stderr_tail or ""),
            stdout_text=str(r.stdout_tail or ""),
        )
        out.append(
            StepResult(
                name=r.name,
                status=r.status,
                duration_sec=r.duration_sec,
                error_message=r.error_message,
                stdout_tail=r.stdout_tail,
                stderr_tail=r.stderr_tail,
                exit_code=r.exit_code,
                stderr_classification=str(cls.get("classification") or "UNKNOWN_STDERR"),
                stderr_is_noise=bool(cls.get("is_noise")),
            )
        )
    return out


def _start_fastapi(python_exe: str, extra_env: dict[str, str] | None = None) -> tuple[subprocess.Popen[str] | None, dict[str, Any]]:
    # If server is already healthy, reuse it and do not manage lifecycle.
    if _health_ok(timeout=2.5):
        return None, {"mode": "reused", "message": "existing FastAPI is healthy"}

    if _port_open("127.0.0.1", 8000, timeout=1.0):
        raise RuntimeError("port 8000 is already in use, but /health is not responding; resolve port conflict first")

    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("FASTAPI_DEV", "false")
    if extra_env:
        env.update({k: str(v) for k, v in extra_env.items()})

    proc = subprocess.Popen(
        [python_exe, "main.py"],
        cwd=str(PYTHON_API_DIR),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )

    if not _wait_health(HEALTH_TIMEOUT_SEC):
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass
        raise RuntimeError("FastAPI failed to become healthy within timeout")

    return proc, {"mode": "started", "pid": proc.pid}


def _stop_fastapi(proc: subprocess.Popen[str] | None) -> None:
    if not proc:
        return
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    except Exception:
        pass


def _write_reports(
    *,
    started_at: str,
    finished_at: str,
    results: list[StepResult],
    fastapi: dict[str, Any],
    sandbox: dict[str, Any],
    triage: dict[str, Any],
) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    total = sum(float(x.duration_sec) for x in results)
    passed = sum(1 for x in results if x.status == "PASS")
    failed = sum(1 for x in results if x.status != "PASS")
    step_dicts = [asdict(x) for x in _enrich_step_stderr_meta(results)]
    stderr_summary = summarize_stderr_classification(step_dicts)
    payload = {
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_sec": total,
        "summary": {
            "passed": passed,
            "failed": failed,
            "total": len(results),
            "overall_status": ("PASS" if failed == 0 else "FAIL"),
        },
        "fastapi": fastapi,
        "sandbox": sandbox,
        "triage": triage,
        "stderr_summary": stderr_summary,
        "steps": step_dicts,
    }
    history_tracker = _update_history_and_trend(payload)
    payload["history_tracker"] = history_tracker
    if isinstance(history_tracker.get("trend"), dict):
        payload["trend"] = history_tracker.get("trend")
    REPORT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines: list[str] = []
    lines.append("# Scenario Router Audit Report")
    lines.append("")
    lines.append(f"- started_at: {started_at}")
    lines.append(f"- finished_at: {finished_at}")
    lines.append(f"- overall_status: {payload['summary']['overall_status']}")
    lines.append(f"- passed: {passed}")
    lines.append(f"- failed: {failed}")
    lines.append(f"- duration_sec: {total:.2f}")
    lines.append(f"- fastapi_mode: {fastapi.get('mode', '')}")
    lines.append(f"- sandbox_enabled: {bool(sandbox.get('enabled'))}")
    lines.append(f"- sandbox_db_path: {str(sandbox.get('sandbox_db_path') or '')}")
    lines.append(f"- sandbox_race_db_path: {str(sandbox.get('sandbox_race_db_path') or '')}")
    lines.append(f"- cleanup_status: {str(sandbox.get('cleanup_status') or '')}")
    lines.append("")
    lines.append("## stderr Classification")
    lines.append("")
    lines.append(f"- classification: {str(stderr_summary.get('classification') or '')}")
    lines.append(f"- is_noise: {bool(stderr_summary.get('is_noise'))}")
    lines.append(f"- affected_steps: {', '.join([str(x) for x in (stderr_summary.get('affected_steps') or [])])}")
    lines.append(f"- evidence: {str(stderr_summary.get('evidence') or '')}")
    lines.append(f"- action_needed: {('NO' if bool(stderr_summary.get('is_noise')) else 'YES')}")
    lines.append("")
    lines.append("## Failure Triage")
    lines.append("")
    lines.append(f"- failure_type: {str(triage.get('failure_type') or 'NONE')}")
    lines.append(f"- likely_cause: {str(triage.get('likely_cause') or '')}")
    lines.append(f"- evidence: {str(triage.get('evidence') or '')}")
    fixes = triage.get("suggested_fix") if isinstance(triage.get("suggested_fix"), list) else []
    if fixes:
        lines.append("- suggested_fix:")
        for i, fx in enumerate(fixes, start=1):
            lines.append(f"  {i}. {str(fx)}")
    else:
        lines.append("- suggested_fix:")
        lines.append("  1. (none)")
    lines.append(f"- rerun_command: {str(triage.get('rerun_command') or '')}")
    lines.append("")
    lines.append("## Steps")
    lines.append("")
    lines.append("| step | status | duration_sec | error_message |")
    lines.append("|---|---|---:|---|")
    for r in results:
        err = (r.error_message or "").replace("|", "\\|")
        lines.append(f"| {r.name} | {r.status} | {r.duration_sec:.2f} | {err} |")

    lines.append("")
    lines.append("## stderr By Step")
    lines.append("")
    lines.append("| step | exit_code | classification | is_noise |")
    lines.append("|---|---:|---|---|")
    for r in _enrich_step_stderr_meta(results):
        lines.append(f"| {r.name} | {int(r.exit_code)} | {r.stderr_classification} | {bool(r.stderr_is_noise)} |")

    lines.append("")
    lines.append("## Output Tail")
    lines.append("")
    for r in results:
        lines.append(f"### {r.name}")
        lines.append("")
        lines.append("stdout:")
        lines.append("```")
        lines.append(r.stdout_tail or "(no output)")
        lines.append("```")
        lines.append("")
        lines.append("stderr:")
        lines.append("```")
        lines.append(r.stderr_tail or "(no stderr)")
        lines.append("```")
        lines.append("")

    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Scenario Router audit pipeline")
    parser.add_argument("--sandbox", action="store_true", default=DEFAULT_SANDBOX)
    parser.add_argument("--keep-sandbox-on-failure", action="store_true", default=False)
    parser.add_argument("--sandbox-db-path", default="")
    parser.add_argument("--fixture-minimal", action="store_true", default=False)
    parser.add_argument("--enable-gate", action="store_true", default=False)
    parser.add_argument("--gate-preset", default="")
    parser.add_argument("--gate-strict", dest="gate_strict", action="store_true", default=None)
    parser.add_argument("--no-gate-strict", dest="gate_strict", action="store_false")
    parser.add_argument("--min-last-10-success-rate", type=float, default=None)
    parser.add_argument("--fail-on-flaky", dest="fail_on_flaky", action="store_true", default=None)
    parser.add_argument("--no-fail-on-flaky", dest="fail_on_flaky", action="store_false")
    parser.add_argument("--baseline-window", type=int, default=None)
    parser.add_argument("--duration-warn-multiplier", type=float, default=None)
    parser.add_argument("--duration-fail-multiplier", type=float, default=None)
    parser.add_argument("--min-baseline-samples", type=int, default=None)
    parser.add_argument("--fail-on-duration-spike", dest="fail_on_duration_spike", action="store_true", default=None)
    parser.add_argument("--no-fail-on-duration-spike", dest="fail_on_duration_spike", action="store_false")
    return parser


def _build_rerun_command(args: argparse.Namespace) -> str:
    parts = ["python scripts/run_scenario_router_audit.py"]
    if bool(args.sandbox):
        parts.append("--sandbox")
    if bool(args.keep_sandbox_on_failure):
        parts.append("--keep-sandbox-on-failure")
    if str(args.sandbox_db_path or "").strip():
        parts.append(f"--sandbox-db-path {str(args.sandbox_db_path).strip()}")
    if bool(args.fixture_minimal):
        parts.append("--fixture-minimal")
    if bool(args.enable_gate):
        parts.append("--enable-gate")
    if str(args.gate_preset or "").strip():
        parts.append(f"--gate-preset {str(args.gate_preset).strip()}")
    if args.gate_strict is True:
        parts.append("--gate-strict")
    if args.gate_strict is False:
        parts.append("--no-gate-strict")
    if args.fail_on_flaky is True:
        parts.append("--fail-on-flaky")
    if args.fail_on_flaky is False:
        parts.append("--no-fail-on-flaky")
    if args.baseline_window is not None:
        parts.append(f"--baseline-window {int(args.baseline_window)}")
    if args.duration_warn_multiplier is not None:
        parts.append(f"--duration-warn-multiplier {float(args.duration_warn_multiplier)}")
    if args.duration_fail_multiplier is not None:
        parts.append(f"--duration-fail-multiplier {float(args.duration_fail_multiplier)}")
    if args.min_baseline_samples is not None:
        parts.append(f"--min-baseline-samples {int(args.min_baseline_samples)}")
    if args.fail_on_duration_spike is True:
        parts.append("--fail-on-duration-spike")
    if args.fail_on_duration_spike is False:
        parts.append("--no-fail-on-duration-spike")
    if args.min_last_10_success_rate is not None:
        parts.append(f"--min-last-10-success-rate {float(args.min_last_10_success_rate)}")
    return " ".join(parts)


def _maybe_run_gate(
    *,
    args: argparse.Namespace,
    python_exe: str,
    env: dict[str, str],
) -> int:
    if not bool(args.enable_gate):
        return 0
    cmd = [
        python_exe,
        "scripts/scenario_router_audit_gate.py",
        "--result-json",
        str(REPORT_JSON),
        "--history-jsonl",
        str(HISTORY_JSONL),
        "--output-json",
        str(REPORTS_DIR / "scenario_router_audit_gate.json"),
        "--output-md",
        str(REPORTS_DIR / "scenario_router_audit_gate.md"),
    ]
    if str(args.gate_preset or "").strip():
        cmd.extend(["--preset", str(args.gate_preset).strip()])
    if args.gate_strict is True:
        cmd.append("--strict")
    if args.gate_strict is False:
        cmd.append("--no-strict")
    if args.min_last_10_success_rate is not None:
        cmd.extend(["--min-last-10-success-rate", str(float(args.min_last_10_success_rate))])
    if args.fail_on_flaky is True:
        cmd.append("--fail-on-flaky")
    if args.fail_on_flaky is False:
        cmd.append("--no-fail-on-flaky")
    if args.baseline_window is not None:
        cmd.extend(["--baseline-window", str(max(1, int(args.baseline_window)))])
    if args.duration_warn_multiplier is not None:
        cmd.extend(["--duration-warn-multiplier", str(max(1.0, float(args.duration_warn_multiplier)))])
    if args.duration_fail_multiplier is not None:
        cmd.extend(["--duration-fail-multiplier", str(max(1.0, float(args.duration_fail_multiplier)))])
    if args.min_baseline_samples is not None:
        cmd.extend(["--min-baseline-samples", str(max(1, int(args.min_baseline_samples)))])
    if args.fail_on_duration_spike is True:
        cmd.append("--fail-on-duration-spike")
    if args.fail_on_duration_spike is False:
        cmd.append("--no-fail-on-duration-spike")
    cp = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=120,
        check=False,
    )
    return int(cp.returncode)


def _resolve_exit(
    *,
    audit_exit: int,
    args: argparse.Namespace,
    python_exe: str,
    env: dict[str, str],
) -> int:
    gate_exit = _maybe_run_gate(args=args, python_exe=python_exe, env=env)
    if int(audit_exit) != 0:
        return 1
    return 0 if int(gate_exit) == 0 else 1


def main() -> int:
    args = _build_parser().parse_args()
    started_at = _now_iso()
    results: list[StepResult] = []
    python_exe = _resolve_python_executable()
    fastapi_proc: subprocess.Popen[str] | None = None
    fastapi_info: dict[str, Any] = {"mode": "unknown"}
    fixture_info: FixtureInfo | None = None
    cleanup_status = "not_required"

    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    # Keep local/CI behavior consistent for admin-protected endpoints.
    env.setdefault("E2E_USE_FAKE_ADMIN_TOKEN", "1")
    env.setdefault("SCENARIO_ROUTER_AUDIT_CLEANUP", ("1" if DEFAULT_SANDBOX_CLEANUP else "0"))

    sandbox_meta: dict[str, Any] = {
        "enabled": bool(args.sandbox),
        "fixture_minimal": bool(args.fixture_minimal),
        "sandbox_db_path": "",
        "sandbox_race_db_path": "",
        "cleanup_status": cleanup_status,
        "keep_sandbox_on_failure": bool(args.keep_sandbox_on_failure),
    }

    if args.sandbox:
        t_fix = time.time()
        try:
            fixture_info = create_fixture(
                sandbox_db_path=str(args.sandbox_db_path or ""),
                fixture_minimal=bool(args.fixture_minimal),
            )
            env["SCENARIO_ROUTER_AUDIT_SANDBOX"] = "1"
            env["SCENARIO_ROUTER_AUDIT_DB_PATH"] = str(fixture_info.mlops_db_path)
            env["SCENARIO_ROUTER_AUDIT_RACE_DB_PATH"] = str(fixture_info.race_db_path)
            sandbox_meta["sandbox_db_path"] = str(fixture_info.mlops_db_path)
            sandbox_meta["sandbox_race_db_path"] = str(fixture_info.race_db_path)
            results.append(
                StepResult(
                    name="sandbox_fixture_create",
                    status="PASS",
                    duration_sec=time.time() - t_fix,
                    error_message="",
                    stdout_tail=json.dumps(asdict(fixture_info), ensure_ascii=False),
                    stderr_tail="",
                    exit_code=0,
                    stderr_classification="NO_STDERR",
                    stderr_is_noise=True,
                )
            )
        except Exception as e:
            results.append(
                StepResult(
                    name="sandbox_fixture_create",
                    status="FAIL",
                    duration_sec=time.time() - t_fix,
                    error_message=str(e),
                    stdout_tail="",
                    stderr_tail="",
                    exit_code=1,
                    stderr_classification="UNKNOWN_STDERR",
                    stderr_is_noise=False,
                )
            )
            sandbox_meta["cleanup_status"] = "not_started"
            finished_at = _now_iso()
            _write_reports(
                started_at=started_at,
                finished_at=finished_at,
                results=results,
                fastapi=fastapi_info,
                sandbox=sandbox_meta,
                triage=build_audit_triage(
                    steps=[asdict(x) for x in results],
                    sandbox=sandbox_meta,
                    rerun_command=_build_rerun_command(args),
                    stderr_summary=summarize_stderr_classification([asdict(x) for x in results]),
                ).to_dict(),
            )
            return _resolve_exit(audit_exit=1, args=args, python_exe=python_exe, env=env)

    try:
        t0 = time.time()
        try:
            fastapi_proc, fastapi_info = _start_fastapi(python_exe, extra_env=env)
            results.append(
                StepResult(
                    name="fastapi_start_or_reuse",
                    status="PASS",
                    duration_sec=time.time() - t0,
                    error_message="",
                    stdout_tail=json.dumps(fastapi_info, ensure_ascii=False),
                    stderr_tail="",
                    exit_code=0,
                    stderr_classification="NO_STDERR",
                    stderr_is_noise=True,
                )
            )
        except Exception as e:
            results.append(
                StepResult(
                    name="fastapi_start_or_reuse",
                    status="FAIL",
                    duration_sec=time.time() - t0,
                    error_message=str(e),
                    stdout_tail="",
                    stderr_tail="",
                    exit_code=1,
                    stderr_classification="UNKNOWN_STDERR",
                    stderr_is_noise=False,
                )
            )
            finished_at = _now_iso()
            _write_reports(
                started_at=started_at,
                finished_at=finished_at,
                results=results,
                fastapi=fastapi_info,
                sandbox=sandbox_meta,
                triage=build_audit_triage(
                    steps=[asdict(x) for x in results],
                    sandbox=sandbox_meta,
                    rerun_command=_build_rerun_command(args),
                    stderr_summary=summarize_stderr_classification([asdict(x) for x in results]),
                ).to_dict(),
            )
            return _resolve_exit(audit_exit=1, args=args, python_exe=python_exe, env=env)

        # explicit health step
        t1 = time.time()
        ok = _health_ok(timeout=5.0)
        results.append(
            StepResult(
                name="health_check",
                status=("PASS" if ok else "FAIL"),
                duration_sec=time.time() - t1,
                error_message=("" if ok else "GET /health failed"),
                stdout_tail=f"url={HEALTH_URL}",
                stderr_tail="",
                exit_code=(0 if ok else 1),
                stderr_classification="NO_STDERR",
                stderr_is_noise=True,
            )
        )
        if not ok:
            finished_at = _now_iso()
            _write_reports(
                started_at=started_at,
                finished_at=finished_at,
                results=results,
                fastapi=fastapi_info,
                sandbox=sandbox_meta,
                triage=build_audit_triage(
                    steps=[asdict(x) for x in results],
                    sandbox=sandbox_meta,
                    rerun_command=_build_rerun_command(args),
                    stderr_summary=summarize_stderr_classification([asdict(x) for x in results]),
                ).to_dict(),
            )
            return _resolve_exit(audit_exit=1, args=args, python_exe=python_exe, env=env)

        results.append(
            _run_step(
                name="scenario_router_e2e",
                command=[python_exe, "scripts/scenario_router_e2e_validation.py"],
                cwd=ROOT,
                env=env,
                timeout_sec=1200,
            )
        )

        results.append(
            _run_step(
                name="scenario_router_auto_recovery_e2e",
                command=[python_exe, "scripts/scenario_router_auto_recovery_e2e.py"],
                cwd=ROOT,
                env=env,
                timeout_sec=1200,
            )
        )

        py_compile_files = [
            "python-api/models.py",
            "python-api/mlops/store.py",
            "python-api/routers/mlops.py",
            "python-api/research/__init__.py",
            "python-api/research/scenario_router_auto_recovery.py",
            "python-api/research/scenario_router_incident_actions.py",
            "python-api/research/scenario_router_incident_response.py",
            "python-api/research/scenario_router_notifications.py",
            "python-api/research/scenario_router_runbooks.py",
            "scripts/scenario_router_e2e_validation.py",
            "scripts/scenario_router_auto_recovery_e2e.py",
            "scripts/scenario_router_audit_triage.py",
            "scripts/scenario_router_audit_gate.py",
            "scripts/create_scenario_router_test_fixture.py",
            "scripts/run_scenario_router_audit.py",
        ]
        results.append(
            _run_step(
                name="py_compile",
                command=[python_exe, "-m", "py_compile", *py_compile_files],
                cwd=ROOT,
                env=env,
                timeout_sec=300,
            )
        )

        import_smoke_code = (
            "import sys; "
            "sys.path.insert(0, 'python-api'); "
            "import routers.mlops; "
            "import research.scenario_router_auto_recovery; "
            "import research.scenario_router_incident_actions; "
            "import research.scenario_router_incident_response; "
            "import research.scenario_router_notifications; "
            "import research.scenario_router_runbooks; "
            "print('import_smoke_ok')"
        )
        results.append(
            _run_step(
                name="import_smoke",
                command=[python_exe, "-c", import_smoke_code],
                cwd=ROOT,
                env=env,
                timeout_sec=180,
            )
        )

        finished_at = _now_iso()
        any_fail = any(x.status != "PASS" for x in results)
        if args.sandbox and fixture_info:
            should_cleanup = DEFAULT_SANDBOX_CLEANUP and not (any_fail and args.keep_sandbox_on_failure)
            if should_cleanup:
                try:
                    cleanup_fixture(fixture_info)
                    cleanup_status = "deleted"
                except Exception as e:
                    cleanup_status = f"cleanup_failed: {e}"
            else:
                cleanup_status = "kept_on_failure" if any_fail and args.keep_sandbox_on_failure else "kept"
            sandbox_meta["cleanup_status"] = cleanup_status

        _write_reports(
            started_at=started_at,
            finished_at=finished_at,
            results=results,
            fastapi=fastapi_info,
            sandbox=sandbox_meta,
            triage=build_audit_triage(
                steps=[asdict(x) for x in results],
                sandbox=sandbox_meta,
                rerun_command=_build_rerun_command(args),
                stderr_summary=summarize_stderr_classification([asdict(x) for x in results]),
            ).to_dict(),
        )
        audit_exit = 0 if all(x.status == "PASS" for x in results) else 1
        return _resolve_exit(audit_exit=audit_exit, args=args, python_exe=python_exe, env=env)
    finally:
        _stop_fastapi(fastapi_proc)


if __name__ == "__main__":
    raise SystemExit(main())
