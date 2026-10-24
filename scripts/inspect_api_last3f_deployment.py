"""Read-only API-only deployment preflight; never sends a signal or starts apps."""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import struct
import sys
from urllib.request import urlopen

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "scripts")]
from evaluate_db_shared_venues import preservation_receipt
from market_edge.compute import CollectorMonitor

SOURCES = ("python-api/scraping/race.py", "python-api/scraping/mobile_race.py", "python-api/scraping/last3f_evidence.py")
ENV_KEYS = ("APP_ENV", "API_HOST", "MODEL_RUNTIME_STATUS", "SCHEDULER_ENABLED", "SUPABASE_DATA_ENABLED",
            "AUTOMATED_BETTING_ENABLED", "MODEL_TRAINING_LOCAL_ENABLED", "PHASE3N_OPERATIONAL_SAGA_MODE",
            "PHASE3N_SAGA_SQLITE_PATH", "PHASE3N_WORKER_ENABLED", "PHASE3N_REMOTE_EFFECTS_ENABLED",
            "PREDICTION_SOURCE_TRACKING_LOCAL_ENABLED", "LOCAL_CONFIGURATION_ROOT")


def now():
    return datetime.now(timezone.utc).isoformat()


def process_data(process):
    result = {"pid": process.pid}
    for name, callback in (("ppid", process.ppid), ("created_at_epoch", process.create_time),
                           ("cwd", process.cwd), ("command_line", process.cmdline),
                           ("rss_bytes", lambda: process.memory_info().rss)):
        try:
            result[name] = callback()
        except (psutil.AccessDenied, psutil.NoSuchProcess) as exc:
            result[name + "_unverified"] = type(exc).__name__
    if "created_at_epoch" in result:
        result["created_at_utc"] = datetime.fromtimestamp(result["created_at_epoch"], timezone.utc).isoformat()
    return result


def source_data(relative, api_created):
    path = ROOT / relative
    raw, stat = path.read_bytes(), path.stat()
    result = {"path": relative, "sha256": hashlib.sha256(raw).hexdigest(),
              "modified_at_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
              "source_predates_current_api": stat.st_mtime <= api_created}
    pyc = Path(importlib.util.cache_from_source(str(path)))
    result["pyc_path"] = str(pyc)
    result["pyc_exists"] = pyc.exists()
    if pyc.exists():
        header = pyc.read_bytes()[:16]
        if len(header) == 16:
            flags = struct.unpack("<I", header[4:8])[0]
            result["pyc_magic_matches"] = header[:4] == importlib.util.MAGIC_NUMBER
            result["pyc_flags"] = flags
            result["pyc_header_matches_source"] = (header[8:16] == importlib.util.source_hash(raw) if flags & 1
                else struct.unpack("<II", header[8:16]) == (int(stat.st_mtime) & 0xFFFFFFFF, stat.st_size))
    return result


def health(url):
    try:
        with urlopen(url, timeout=8) as response:
            return {"http_status": response.status, "body": json.load(response)}
    except Exception as exc:
        return {"error": type(exc).__name__ + ": " + str(exc)}


def query_counts(path, tables):
    path = Path(path)
    if not path.exists():
        return {"path": str(path), "exists": False, "active_jobs": None}
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3) as db:
        db.execute("PRAGMA query_only=ON")
        existing = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        records = []
        for table, state, predicate in tables:
            if table not in existing:
                records.append({"table": table, "exists": False})
                continue
            counts = db.execute(f"SELECT {state},count(*) FROM {table} GROUP BY {state}").fetchall()
            active = db.execute(f"SELECT count(*) FROM {table} WHERE {predicate}").fetchone()[0]
            records.append({"table": table, "status_counts": [{"state": key, "count": count} for key, count in counts],
                            "active_jobs": active})
    return {"path": str(path), "exists": True, "tables": records,
            "active_jobs": sum(row.get("active_jobs", 0) for row in records)}


def private_console_members(api_pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.FreeConsole.argtypes, kernel.FreeConsole.restype = [], ctypes.c_bool
    kernel.AttachConsole.argtypes, kernel.AttachConsole.restype = [ctypes.c_ulong], ctypes.c_bool
    kernel.GetConsoleProcessList.argtypes = [ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong]
    kernel.GetConsoleProcessList.restype = ctypes.c_ulong
    kernel.FreeConsole()
    if not kernel.AttachConsole(api_pid):
        return {"verified": False, "attach_error": ctypes.get_last_error(), "signal_sent": False}
    try:
        ids = (ctypes.c_ulong * 128)()
        count = kernel.GetConsoleProcessList(ids, 128)
        return {"verified": 0 < count <= 128, "members": sorted(ids[:min(count, 128)]),
                "inspector_pid": psutil.Process().pid, "signal_sent": False}
    finally:
        kernel.FreeConsole()


def inspect(api_pid, wrapper_pid):
    api, wrapper = psutil.Process(api_pid), psutil.Process(wrapper_pid)
    descendants = api.children(recursive=True)
    expected_console = {api_pid, wrapper_pid, *(p.pid for p in descendants)}
    environment = api.environ()
    selected_env = {key: environment.get(key) for key in ENV_KEYS}
    child_records = [process_data(child) for child in descendants]
    tracked = [process_data(psutil.Process(pid)) for pid in (19600, 27320, 7536)]
    independent = []
    for process in psutil.process_iter(["pid", "ppid", "cmdline"]):
        try:
            args = process.info["cmdline"] or []
            if any(Path(arg.replace("\\", "/")).name in {"repair_last3f_gaps.py", "repair_last3f_gaps_v2.py", "run_forward_collector.py"} for arg in args):
                ancestors = [parent.pid for parent in process.parents()]
                independent.append({"pid": process.pid, "ppid": process.ppid(),
                                    "api_is_ancestor": api_pid in ancestors, "api_wrapper_is_ancestor": wrapper_pid in ancestors})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    active_predicate = "status IN ('queued','running','recovering','waiting_resources','cancelling')"
    stores = [query_counts(ROOT / "keiba/data/scrape_jobs.db", [("scrape_jobs", "status", active_predicate)]),
              query_counts(ROOT / "python-api/data/local_retrain_jobs.db", [
                  ("local_retrain_jobs", "state", "state NOT IN ('completed','failed','cancelled')")])]
    saga_path = environment.get("PHASE3N_SAGA_SQLITE_PATH")
    if saga_path:
        stores.append(query_counts(saga_path, [("operational_scrape_jobs", "status", active_predicate),
                                               ("operational_scrape_batches", "status", active_predicate)]))
    api_health, web_health = health("http://127.0.0.1:8000/health"), health("http://127.0.0.1:3000/api/health")
    protected = preservation_receipt()
    monitor = CollectorMonitor(ROOT / "reports/generated/forward-runtime/h04-live-20260920-v2").snapshot()
    console = private_console_members(api_pid)
    members = set(console.get("members", [])) - {console.get("inspector_pid")}
    console["expected_api_family"] = sorted(expected_console)
    console["foreign_members"] = sorted(members - expected_console)
    console["api_family_is_exact"] = members == expected_console and console.get("verified", False)
    flags = {"api_identity_matches": api.ppid() == wrapper_pid and Path(api.cwd()) == ROOT / "python-api"
             and api.cmdline()[-1] == "main.py" and wrapper.cmdline()[-1] == "main.py",
             "job_stores_idle": all(row.get("active_jobs") == 0 for row in stores),
             "prediction_idle": api_health.get("body", {}).get("prediction_runtime", {}).get("active_workers") == 0,
             "health_ok": api_health.get("http_status") == 200 and web_health.get("http_status") == 200,
             "private_console_api_only": console["api_family_is_exact"],
             "collector_same_process_healthy": monitor.get("healthy") and monitor.get("pid") == 19600,
             "independent_collectors_and_repair": not any(row["api_is_ancestor"] or row["api_wrapper_is_ancestor"] for row in independent)}
    return {"schema": "api-last3f-readonly-preflight-v1", "recorded_at_utc": now(),
            "api": process_data(api), "wrapper": process_data(wrapper), "descendants": child_records,
            "environment_whitelist": selected_env, "sources": [source_data(name, api.create_time()) for name in SOURCES],
            "health": {"api": api_health, "web": web_health}, "job_stores": stores,
            "console": console, "collector": monitor, "other_processes": tracked,
            "independent_workflows": independent, "protected_before": protected,
            "checks": flags, "preflight_ready": all(flags.values()), "restart_authorized": False,
            "signal_sent": False, "start_dispatched": False, "next_modified_or_restarted": False,
            "credentials_or_auth_values_exported": False, "data_db_writes": False,
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-pid", type=int, default=46404)
    parser.add_argument("--wrapper-pid", type=int, default=37972)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = inspect(args.api_pid, args.wrapper_pid)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({"output": str(args.out.resolve()), "checks": result["checks"], "preflight_ready": result["preflight_ready"],
                      "signal_sent": False}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
