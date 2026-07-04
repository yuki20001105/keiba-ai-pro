from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class AuditTriage:
    failure_type: str
    likely_cause: str
    evidence: str
    suggested_fix: list[str]
    rerun_command: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _contains_any(text: str, needles: list[str]) -> bool:
    t = text.lower()
    return any(n.lower() in t for n in needles)


STDERR_CLASSIFICATIONS = {
    "NO_STDERR",
    "BENIGN_SERVER_LOG",
    "POWERSHELL_NATIVE_COMMAND_ERROR_NOISE",
    "WINDOWS_ENCODING_NOISE",
    "DEPRECATION_WARNING",
    "REAL_TRACEBACK",
    "REAL_IMPORT_ERROR",
    "REAL_SYNTAX_ERROR",
    "REAL_RUNTIME_ERROR",
    "UNKNOWN_STDERR",
}


def _short_evidence(text: str, max_chars: int = 280) -> str:
    s = str(text or "").strip().replace("\r\n", "\n")
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 3] + "..."


def classify_stderr_for_step(
    *,
    exit_code: int,
    stderr_text: str,
    stdout_text: str = "",
) -> dict[str, Any]:
    err = str(stderr_text or "")
    out = str(stdout_text or "")
    err_low = err.lower()
    out_low = out.lower()
    merged_low = f"{err_low}\n{out_low}"

    has_stderr = bool(err.strip())
    if not has_stderr:
        return {
            "has_stderr": False,
            "classification": "NO_STDERR",
            "is_noise": True,
            "evidence": "",
        }

    if exit_code == 0 and "nativecommanderror" in merged_low:
        return {
            "has_stderr": True,
            "classification": "POWERSHELL_NATIVE_COMMAND_ERROR_NOISE",
            "is_noise": True,
            "evidence": _short_evidence(err or out),
        }

    if "modulenotfounderror" in merged_low or "no module named" in merged_low:
        return {
            "has_stderr": True,
            "classification": "REAL_IMPORT_ERROR",
            "is_noise": False,
            "evidence": _short_evidence(err or out),
        }

    if "syntaxerror" in merged_low:
        return {
            "has_stderr": True,
            "classification": "REAL_SYNTAX_ERROR",
            "is_noise": False,
            "evidence": _short_evidence(err or out),
        }

    if "traceback" in merged_low and exit_code != 0:
        return {
            "has_stderr": True,
            "classification": "REAL_TRACEBACK",
            "is_noise": False,
            "evidence": _short_evidence(err or out),
        }

    if "unicodedecodeerror" in merged_low or "charmap" in merged_low:
        if exit_code == 0:
            return {
                "has_stderr": True,
                "classification": "WINDOWS_ENCODING_NOISE",
                "is_noise": True,
                "evidence": _short_evidence(err or out),
            }
        return {
            "has_stderr": True,
            "classification": "REAL_RUNTIME_ERROR",
            "is_noise": False,
            "evidence": _short_evidence(err or out),
        }

    if exit_code == 0 and (
        "deprecationwarning" in merged_low
        or "futurewarning" in merged_low
        or "pendingdeprecationwarning" in merged_low
        or "deprecated" in merged_low
    ):
        return {
            "has_stderr": True,
            "classification": "DEPRECATION_WARNING",
            "is_noise": True,
            "evidence": _short_evidence(err or out),
        }

    server_tokens = [
        "uvicorn",
        "application startup complete",
        "started server process",
        "waiting for application startup",
        "127.0.0.1",
        "get /health",
        "info:",
    ]
    if exit_code == 0 and any(x in merged_low for x in server_tokens):
        if not any(x in merged_low for x in ["traceback", "exception", "error:", "fatal"]):
            return {
                "has_stderr": True,
                "classification": "BENIGN_SERVER_LOG",
                "is_noise": True,
                "evidence": _short_evidence(err or out),
            }

    runtime_tokens = [
        "runtimeerror",
        "valueerror",
        "typeerror",
        "keyerror",
        "attributeerror",
        "nameerror",
        "assertionerror",
        "exception",
    ]
    if exit_code != 0 and any(x in merged_low for x in runtime_tokens):
        return {
            "has_stderr": True,
            "classification": "REAL_RUNTIME_ERROR",
            "is_noise": False,
            "evidence": _short_evidence(err or out),
        }

    if exit_code == 0 and "warning" in merged_low:
        return {
            "has_stderr": True,
            "classification": "DEPRECATION_WARNING",
            "is_noise": True,
            "evidence": _short_evidence(err or out),
        }

    return {
        "has_stderr": True,
        "classification": "UNKNOWN_STDERR",
        "is_noise": bool(exit_code == 0),
        "evidence": _short_evidence(err or out),
    }


def summarize_stderr_classification(steps: list[dict[str, Any]]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or s.get("step_name") or "")
        st = str(s.get("status") or "")
        exit_code = int(s.get("exit_code") if s.get("exit_code") is not None else (0 if st == "PASS" else 1))
        stderr_tail = str(s.get("stderr_tail") or "")
        stdout_tail = str(s.get("stdout_tail") or "")
        c = classify_stderr_for_step(exit_code=exit_code, stderr_text=stderr_tail, stdout_text=stdout_tail)
        items.append(
            {
                "step_name": name,
                "exit_code": exit_code,
                "classification": str(c.get("classification") or "UNKNOWN_STDERR"),
                "is_noise": bool(c.get("is_noise")),
                "evidence": str(c.get("evidence") or ""),
                "has_stderr": bool(c.get("has_stderr")),
            }
        )

    if not items:
        return {
            "has_stderr": False,
            "classification": "NO_STDERR",
            "is_noise": True,
            "evidence": "",
            "affected_steps": [],
            "classifications_by_step": [],
        }

    affected = [x for x in items if bool(x.get("has_stderr"))]
    if not affected:
        return {
            "has_stderr": False,
            "classification": "NO_STDERR",
            "is_noise": True,
            "evidence": "",
            "affected_steps": [],
            "classifications_by_step": items,
        }

    priority = {
        "REAL_IMPORT_ERROR": 100,
        "REAL_SYNTAX_ERROR": 95,
        "REAL_TRACEBACK": 90,
        "REAL_RUNTIME_ERROR": 80,
        "UNKNOWN_STDERR": 70,
        "WINDOWS_ENCODING_NOISE": 40,
        "POWERSHELL_NATIVE_COMMAND_ERROR_NOISE": 35,
        "BENIGN_SERVER_LOG": 30,
        "DEPRECATION_WARNING": 20,
        "NO_STDERR": 0,
    }
    affected_sorted = sorted(
        affected,
        key=lambda x: (
            int(priority.get(str(x.get("classification") or "UNKNOWN_STDERR"), 10)),
            0 if bool(x.get("is_noise")) else 1,
        ),
        reverse=True,
    )
    top = affected_sorted[0]
    return {
        "has_stderr": True,
        "classification": str(top.get("classification") or "UNKNOWN_STDERR"),
        "is_noise": bool(top.get("is_noise")),
        "evidence": _short_evidence(str(top.get("evidence") or "")),
        "affected_steps": [str(x.get("step_name") or "") for x in affected],
        "classifications_by_step": items,
    }


def build_audit_triage(
    *,
    steps: list[dict[str, Any]],
    sandbox: dict[str, Any],
    rerun_command: str,
    stderr_summary: dict[str, Any] | None = None,
) -> AuditTriage:
    stderr_info = stderr_summary if isinstance(stderr_summary, dict) else summarize_stderr_classification(steps)
    failed = [s for s in steps if str(s.get("status") or "").upper() != "PASS"]
    cleanup_status = str(sandbox.get("cleanup_status") or "")

    if not failed and not cleanup_status.startswith("cleanup_failed"):
        return AuditTriage(
            failure_type="NONE",
            likely_cause="",
            evidence="",
            suggested_fix=[],
            rerun_command=rerun_command,
        )

    if cleanup_status.startswith("cleanup_failed"):
        return AuditTriage(
            failure_type="SANDBOX_CLEANUP_FAILED",
            likely_cause="Sandbox cleanup failed after audit run.",
            evidence=cleanup_status,
            suggested_fix=[
                "Check file lock holders on sandbox DB path.",
                "Remove sandbox directory manually and rerun audit.",
            ],
            rerun_command=rerun_command,
        )

    first = failed[0] if failed else {}
    name = str(first.get("name") or "")
    err = str(first.get("error_message") or "")
    tail = str(first.get("stdout_tail") or "")
    blob = f"{err}\n{tail}"

    default = AuditTriage(
        failure_type="UNKNOWN",
        likely_cause="Unknown failure in audit step.",
        evidence=(blob[:500] if blob else str(stderr_info.get("evidence") or name)),
        suggested_fix=[
            "Inspect scenario_router_audit_report.md Output Tail section for the failed step.",
            "Rerun with --sandbox --keep-sandbox-on-failure to preserve evidence.",
        ],
        rerun_command=rerun_command,
    )

    if _contains_any(blob, ["address already in use", "port 8000", "already in use"]):
        return AuditTriage(
            failure_type="PORT_CONFLICT",
            likely_cause="Port 8000 is occupied by another process.",
            evidence=(blob[:500] if blob else "port 8000 conflict"),
            suggested_fix=[
                "Stop the process on port 8000.",
                "Restart FastAPI and rerun audit.",
            ],
            rerun_command=rerun_command,
        )

    if _contains_any(blob, ["modulenotfounderror", "no module named"]):
        return AuditTriage(
            failure_type="DEPENDENCY_MISSING",
            likely_cause="Required Python dependency is missing in the current environment.",
            evidence=(blob[:500] if blob else "ModuleNotFoundError"),
            suggested_fix=[
                "Install project dependencies in the active environment.",
                "Re-run py_compile/import smoke before full audit.",
            ],
            rerun_command=rerun_command,
        )

    if str(stderr_info.get("classification") or "") == "REAL_IMPORT_ERROR":
        return AuditTriage(
            failure_type="DEPENDENCY_MISSING",
            likely_cause="Import error detected from stderr classification.",
            evidence=str(stderr_info.get("evidence") or ""),
            suggested_fix=[
                "Install missing module in active environment.",
                "Re-run py_compile/import smoke before full audit.",
            ],
            rerun_command=rerun_command,
        )

    if str(stderr_info.get("classification") or "") == "REAL_SYNTAX_ERROR":
        return AuditTriage(
            failure_type="PY_COMPILE_FAILED",
            likely_cause="Syntax error detected from stderr classification.",
            evidence=str(stderr_info.get("evidence") or ""),
            suggested_fix=[
                "Fix syntax errors shown in stderr evidence.",
                "Re-run py_compile then full audit.",
            ],
            rerun_command=rerun_command,
        )

    if _contains_any(blob, ["no such table", "unable to open database file", "no such column"]):
        return AuditTriage(
            failure_type="DB_PATH_MISMATCH",
            likely_cause="Audit is pointing to an unexpected or incompatible database path.",
            evidence=(blob[:500] if blob else "DB path mismatch"),
            suggested_fix=[
                "Verify sandbox_db_path and race_db_path in report.",
                "Recreate sandbox fixture and rerun audit.",
            ],
            rerun_command=rerun_command,
        )

    if _contains_any(blob, ["database is locked"]):
        return AuditTriage(
            failure_type="DB_PATH_MISMATCH",
            likely_cause="SQLite DB is locked by another process during audit.",
            evidence=(blob[:500] if blob else "database is locked"),
            suggested_fix=[
                "Close processes holding the sandbox DB.",
                "Rerun with --keep-sandbox-on-failure to inspect lock context if needed.",
            ],
            rerun_command=rerun_command,
        )

    if _contains_any(blob, ["unicodedecodeerror"]):
        return AuditTriage(
            failure_type="UNKNOWN",
            likely_cause="Windows encoding mismatch while collecting subprocess output.",
            evidence=(blob[:500] if blob else "UnicodeDecodeError"),
            suggested_fix=[
                "Use UTF-8 output settings in subprocess execution.",
                "Rerun audit and inspect triage evidence section.",
            ],
            rerun_command=rerun_command,
        )

    if _contains_any(blob, ["404", "auto-recovery", "/api/mlops/research/scenario-router/auto-recovery"]):
        return AuditTriage(
            failure_type="OLD_SERVER_PROCESS",
            likely_cause="FastAPI process is likely running old code, or route registration is stale.",
            evidence=(blob[:500] if blob else "404 on auto-recovery route"),
            suggested_fix=[
                "Stop existing process on port 8000.",
                "Start FastAPI with current source and verify /health.",
                "Re-run audit with sandbox mode.",
            ],
            rerun_command=rerun_command,
        )

    if name == "sandbox_fixture_create":
        return AuditTriage(
            failure_type="SANDBOX_CREATE_FAILED",
            likely_cause="Failed to create or copy sandbox fixture databases.",
            evidence=(blob[:500] if blob else name),
            suggested_fix=[
                "Check source DB existence and file permissions.",
                "Retry with --fixture-minimal for faster setup.",
            ],
            rerun_command=rerun_command,
        )

    if name == "fastapi_start_or_reuse":
        return AuditTriage(
            failure_type="FASTAPI_START_FAILED",
            likely_cause="FastAPI failed to start or become healthy in time.",
            evidence=(blob[:500] if blob else name),
            suggested_fix=[
                "Inspect FastAPI startup logs and dependency imports.",
                "Confirm /health endpoint availability.",
            ],
            rerun_command=rerun_command,
        )

    if name == "health_check":
        return AuditTriage(
            failure_type="HEALTH_CHECK_FAILED",
            likely_cause="FastAPI health endpoint is unavailable.",
            evidence=(blob[:500] if blob else name),
            suggested_fix=[
                "Check API_BASE_URL and server process status.",
                "Restart FastAPI and retry audit.",
            ],
            rerun_command=rerun_command,
        )

    if name == "scenario_router_e2e":
        return AuditTriage(
            failure_type="SCENARIO_ROUTER_E2E_FAILED",
            likely_cause="Scenario Router E2E checks failed.",
            evidence=(blob[:500] if blob else name),
            suggested_fix=[
                "Inspect Scenario Router E2E output tail for failed assertion.",
                "Validate model_id and race fixture availability.",
            ],
            rerun_command=rerun_command,
        )

    if name == "scenario_router_auto_recovery_e2e":
        return AuditTriage(
            failure_type="AUTO_RECOVERY_E2E_FAILED",
            likely_cause="Auto Recovery E2E checks failed.",
            evidence=(blob[:500] if blob else name),
            suggested_fix=[
                "Inspect auto-recovery endpoint responses in output tail.",
                "Ensure routes are loaded in current FastAPI process.",
            ],
            rerun_command=rerun_command,
        )

    if name == "py_compile":
        return AuditTriage(
            failure_type="PY_COMPILE_FAILED",
            likely_cause="Syntax validation failed in one or more Python files.",
            evidence=(blob[:500] if blob else name),
            suggested_fix=[
                "Fix syntax errors reported in py_compile output.",
                "Re-run py_compile step before full audit.",
            ],
            rerun_command=rerun_command,
        )

    if name == "import_smoke":
        return AuditTriage(
            failure_type="IMPORT_SMOKE_FAILED",
            likely_cause="Import smoke test failed due to runtime import error.",
            evidence=(blob[:500] if blob else name),
            suggested_fix=[
                "Inspect import stack trace and missing dependencies.",
                "Confirm PYTHONPATH and environment package set.",
            ],
            rerun_command=rerun_command,
        )

    return default
