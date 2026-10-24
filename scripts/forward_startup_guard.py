"""External, fail-closed Windows restart guard for the FROZEN paper collector.

Default mode is read-only. --launch is explicit authority to call the existing
launcher only when no owner/process survives. Never clears stop, restarts a
process, changes protocol, or imports/retrains the frozen model. Periodic
checks of a healthy collector deliberately avoid model/history hashing.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
JST = timezone(timedelta(hours=9))
UTC = timezone.utc
PACKAGES = ("forward_runtime", "forward_adapters", "experiments", "prospective")
EXTRA_SOURCES = ("scripts/run_forward_collector.py", "python-api/training/eligibility.py",
                 "python-api/training/local_retrain.py", "python-api/scraping/fetch_pipeline.py")


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timezone-aware-clock-required")
    return parsed.astimezone(UTC)


def stamp(value):
    return timestamp(value).isoformat().replace("+00:00", "Z")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def object_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_hash(path):
    with Path(path).open("rb") as handle:
        digest = hashlib.sha256()
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ledger_status(root):
    """Read only a small projection; never loads features, outcomes or JSON blobs."""
    path = Path(root) / "ledger.sqlite3"
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON"); db.execute("BEGIN")
        meta = dict(db.execute("SELECT key,value FROM runtime_meta"))
        if meta.get("schema") != "forward-runtime-v1":
            raise ValueError("unexpected-ledger-schema")
        contract = json.loads(meta["contract"])
        if object_hash(contract) != meta["contract_sha256"]:
            raise ValueError("ledger-contract-hash-mismatch")
        return {"contract": contract, "contract_sha256": meta["contract_sha256"],
                "owner": dict(db.execute("SELECT * FROM owner WHERE singleton=1").fetchone()),
                "days": [dict(r) for r in db.execute("SELECT date,status FROM schedule_days ORDER BY date")],
                "races": [dict(r) for r in db.execute("SELECT race_id,race_date,post_time,status,snapshot_json IS NOT NULL AS has_snapshot FROM races ORDER BY post_time,race_id")],
                "last_event_seq": db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]}
    finally:
        db.rollback(); db.close()


def collector_processes(root):
    if os.name != "nt":
        raise ValueError("windows-process-verification-required")
    command = ("Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | "
               "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress")
    done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                          check=True, capture_output=True, encoding="utf-8", timeout=20,
                          creationflags=subprocess.CREATE_NO_WINDOW)
    rows = json.loads(done.stdout) if done.stdout.strip() else []
    rows = [rows] if isinstance(rows, dict) else rows
    needle = str(Path(root).resolve()).replace("/", "\\").lower()
    return [{"pid": r["ProcessId"], "parent_pid": r["ParentProcessId"]} for r in rows
            if "run_forward_collector.py" in (r.get("CommandLine") or "").lower()
            and needle in (r.get("CommandLine") or "").replace("/", "\\").lower()
            and " run " in (r.get("CommandLine") or "").lower()]


def operational_summary(state, *, now):
    owner = state["owner"]
    current = timestamp(now)
    alive = bool(owner["owner_id"] and owner["expires_at"] and timestamp(owner["expires_at"]) > current)
    windows = []
    for race in state["races"]:
        if race["status"] != "queued":
            continue
        cutoff = timestamp(race["post_time"]) - timedelta(minutes=5)
        windows.append({"race_id": race["race_id"], "race_date": race["race_date"],
                        "capture_start_jst": (cutoff-timedelta(seconds=30)).astimezone(JST).isoformat(),
                        "deadline_jst": cutoff.astimezone(JST).isoformat(),
                        "research_reserve_start_jst": (cutoff-timedelta(minutes=2, seconds=30)).astimezone(JST).isoformat(),
                        "research_reserve_end_jst": (cutoff+timedelta(minutes=2)).astimezone(JST).isoformat()})
    return {"observed_at": stamp(current), "collector_lease_alive": alive, "owner": owner,
            "planned_days": len(state["days"]), "day_status_counts": dict(Counter(d["status"] for d in state["days"])),
            "scheduled_races": len(state["races"]), "race_status_counts": dict(Counter(r["status"] for r in state["races"])),
            "captured_races": sum(bool(r["has_snapshot"]) for r in state["races"]),
            "last_event_seq": state["last_event_seq"], "capture_windows": windows,
            "research_reservation_is_operational_only": True}


def verify_fixed_inputs(repo, root, state, *, expected_protocol, expected_analysis, expected_launcher, now):
    """Deep audit only on request or before a needed launch, not every heartbeat."""
    repo, root = Path(repo).resolve(), Path(root).resolve()
    envelope = read_json(root / "protocol.json"); protocol = envelope["protocol"]
    if object_hash(protocol) != expected_protocol or envelope["protocol_sha256"] != expected_protocol:
        raise ValueError("pinned-protocol-changed")
    if state["contract"]["protocol_sha256"] != expected_protocol or state["contract"]["artifact_hashes"] != protocol["artifact_hashes"]:
        raise ValueError("ledger-protocol-binding-changed")
    if protocol["fixed_model_id"] != "H04" or protocol["paper_only"] is not True or protocol["automatic_purchase"] is not False:
        raise ValueError("not-fixed-paper-H04")
    if protocol["date_from"] != "20260920" or protocol["date_to"] != "20261218" or len(protocol["feature_columns"]) != 99:
        raise ValueError("fixed-period-or-feature-count-changed")
    if (protocol["cutoff_seconds"], protocol["capture_window_seconds"], protocol["max_source_age_seconds"]) != (300, 30, 120):
        raise ValueError("fixed-capture-policy-changed")
    actual_paths = {str(p.relative_to(repo)).replace("\\", "/") for package in PACKAGES
                    for p in (repo / "python-api" / package).glob("*.py")} | set(EXTRA_SOURCES)
    if actual_paths != set(protocol["source_manifest"]):
        raise ValueError("frozen-source-file-set-changed")
    for relative, expected in protocol["source_manifest"].items():
        path = (repo / relative).resolve()
        if not path.is_relative_to(repo) or file_hash(path) != expected:
            raise ValueError("frozen-source-changed:" + relative)
    for name, digest in protocol["artifact_hashes"].items():
        if file_hash(root / "objects" / digest) != digest:
            raise ValueError("frozen-artifact-changed:" + name)
    if file_hash(root / "analysis-definition.json") != expected_analysis:
        raise ValueError("frozen-analysis-file-changed")
    analysis = read_json(root / "analysis-definition.json")
    if analysis["protocol_sha256"] != expected_protocol or analysis["outcomes_observed"] != 0 or timestamp(analysis["frozen_at"]) >= timestamp(protocol["start_at"]):
        raise ValueError("analysis-precommit-invalid")
    if file_hash(repo / "python-api/forward_reports/evaluation.py") != analysis["definition"]["evaluation_source_sha256"]:
        raise ValueError("frozen-analysis-source-changed")
    if file_hash(repo / "scripts/start-forward-collector.ps1") != expected_launcher:
        raise ValueError("existing-launcher-changed")
    target = max(timestamp(now).astimezone(JST).strftime("%Y%m%d"), protocol["date_from"])
    daily_path = root / "daily-bindings" / (target + ".json")
    daily = read_json(daily_path) if daily_path.exists() else None
    history = {"target_date": target, "daily_binding_present": daily is not None}
    if daily:
        if daily["target_date"] != target or daily["status"] != "completed" or not daily["history_max_source_date"] < target or daily["same_day_or_future_history_used"] is not False:
            raise ValueError("daily-history-time-boundary-invalid")
        for kind in ("state", "bloodline"):
            if file_hash(Path(daily[kind + "_path"])) != daily[kind + "_sha256"]:
                raise ValueError("daily-history-object-changed:" + kind)
        history.update({k: daily[k] for k in ("history_max_source_date", "history_rows", "history_races", "state_sha256", "bloodline_sha256")})
        history["lag_calendar_days"] = (datetime.strptime(target, "%Y%m%d") - datetime.strptime(daily["history_max_source_date"], "%Y%m%d")).days
        history["complete_career_history_proven"] = False
    elif timestamp(now).astimezone(JST).hour >= 8:
        raise ValueError("daily-history-missing-after-0800-do-not-prepare-during-races")
    return {"protocol_sha256": expected_protocol, "all_frozen_hashes_match": True,
            "source_files_verified": len(actual_paths), "artifacts_verified": protocol["artifact_hashes"],
            "analysis_file_sha256": expected_analysis, "launcher_sha256": expected_launcher,
            "feature_count": 99, "history": history, "start_at": protocol["start_at"], "end_at": protocol["end_at"]}


def decide_resume(state, *, processes, now):
    current = timestamp(now)
    owner = state["owner"]
    if owner["stop_requested"]:
        return "explicit_stop_preserved"
    if current.astimezone(JST).strftime("%Y%m%d") > "20261218":
        return "fixed_study_ended"
    if owner["owner_id"] and owner["expires_at"] and timestamp(owner["expires_at"]) > current:
        return "already_running"
    if processes:
        return "process_exists_without_live_lease_manual_review"
    return "eligible_for_guarded_resume"


def launcher_command(repo, root):
    repo, root = Path(repo).resolve(), Path(root).resolve()
    relative = root.relative_to(repo)  # Legacy launcher joins repo + relative.
    if not root.is_relative_to(repo / "reports/generated/forward-runtime") or root == repo / "reports/generated/forward-runtime":
        raise ValueError("isolated-forward-runtime-directory-required")
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
            "-File", str(repo / "scripts/start-forward-collector.ps1"), "-RuntimeDirectory", str(relative)]


@contextmanager
def launch_mutex(root):
    if os.name != "nt":
        raise ValueError("launch-supported-only-on-windows")
    import ctypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    # Session-local mutex plus TaskScheduler IgnoreNew plus the collector's
    # SQLite lease/fence. The lease is the cross-session authoritative guard.
    name = "Local\\KeibaForwardGuard-" + hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:24]
    handle = kernel.CreateMutexW(None, False, name)
    if not handle:
        raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
    exists = ctypes.get_last_error() == 183
    try:
        if exists:
            raise ValueError("another-startup-guard-running")
        yield
    finally:
        kernel.CloseHandle(handle)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--expected-protocol", required=True)
    parser.add_argument("--expected-analysis", required=True)
    parser.add_argument("--expected-launcher", required=True)
    parser.add_argument("--audit", action="store_true", help="Hash all pinned inputs even if the collector is alive")
    parser.add_argument("--launch", action="store_true", help="Explicitly permit only a guarded start; never restart/clear-stop")
    args = parser.parse_args()
    def check():
        current = datetime.now(UTC); state = ledger_status(args.root)
        # Healthy periodic checks read only the tiny ledger projection; no
        # process census, raw data, model/history hash or network call.
        alive = operational_summary(state, now=current)["collector_lease_alive"]
        processes = [] if alive and not args.audit else collector_processes(args.root)
        action = decide_resume(state, processes=processes, now=current)
        report = {"schema": "forward-external-restart-guard-v1", "operation": action,
                  "operational": operational_summary(state, now=current), "matching_processes": processes,
                  "full_preflight_performed": False, "launched": False, "stop_latch_cleared": False}
        if args.audit or action == "eligible_for_guarded_resume":
            report["preflight"] = verify_fixed_inputs(REPO, args.root, state, expected_protocol=args.expected_protocol,
                expected_analysis=args.expected_analysis, expected_launcher=args.expected_launcher, now=current)
            report["full_preflight_performed"] = True
        if args.launch and action == "eligible_for_guarded_resume":
            # Recheck after hashes in case another process started meanwhile.
            latest = ledger_status(args.root)
            action = decide_resume(latest, processes=collector_processes(args.root), now=datetime.now(UTC))
            if action == "eligible_for_guarded_resume":
                launched = subprocess.run(launcher_command(REPO, args.root), capture_output=True, text=True, timeout=30,
                    creationflags=subprocess.CREATE_NO_WINDOW, check=True)
                report.update(launched=True, operation="existing_launcher_dispatched", launcher_output=launched.stdout.strip(),
                              startup_success_confirmed=False)
            else:
                report["operation"] = action
        print(json.dumps(report, ensure_ascii=True, indent=2))
    try:
        if args.launch:
            with launch_mutex(args.root):
                check()
        else:
            check()
    except Exception as exc:
        print(json.dumps({"schema": "forward-external-restart-guard-v1", "operation": "blocked",
                          "error": f"{type(exc).__name__}: {exc}", "launched": False, "stop_latch_cleared": False}))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
