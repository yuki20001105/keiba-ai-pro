"""Restart only Next's dev worker through its existing config-file watcher.

No signal, kill, elevation, launcher, build, config-content edit or retry trigger
exists. Preparation is read-only. Execution requires a fresh root GO and an
exclusive one-attempt claim, then changes only next.config.js's last-write time.
The built-in watcher exits immediately; this is NOT graceful request draining.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.request import urlopen

import psutil

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from inspect_api_last3f_deployment import inspect as inspect_api
from inspect_ui_restart import process_data
from prepare_h04_history_revision import protection_receipt

CONFIG_SHA = "17014fb0e7fa9f8347ba7d2a1616d07ac7ee8e4b705ae34cf1629b091c6203ec"
PROTECTED = (52324, 30904, 19600, 27320, 41016, 40136)
OLD_WORKER, PARENT = 7536, 8040
OLD_WORKER_CREATED = 1791612584.0791123
PARENT_CREATED = 1791604228.6039712
CODE_PATHS = (
    "scripts/restart_ui_config_watcher.py", "scripts/inspect_ui_restart.py",
    "python-api/tests/test_ui_config_watcher_restart.py",
    "scripts/inspect_api_last3f_deployment.py", "scripts/prepare_h04_history_revision.py",
    "scripts/evaluate_db_shared_venues.py",
    "node_modules/next/dist/server/lib/start-server.js",
    "node_modules/next/dist/cli/next-dev.js",
    "node_modules/next/dist/server/lib/utils.js",
    "node_modules/next/dist/shared/lib/constants.js",
    "node_modules/next/dist/compiled/watchpack/watchpack.js",
)


def now():
    return datetime.now(timezone.utc).isoformat()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def code_hashes():
    return {name: file_hash(ROOT / name) for name in CODE_PATHS}


def config_state():
    path = ROOT / "next.config.js"
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError("config-symlink-forbidden")
    stat = path.stat()
    return {"path": str(path), "sha256": file_hash(path), "size_bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns, "file_id": stat.st_ino}


def local_health(url):
    try:
        with urlopen(url, timeout=3) as response:
            body = response.read()
            return {"http_status": response.status, "body_sha256": hashlib.sha256(body).hexdigest(),
                    "body": json.loads(body)}
    except Exception as error:
        return {"error": type(error).__name__ + ": " + str(error)}


def identity(record):
    return {key: record.get(key) for key in ("pid", "created_at_epoch", "ppid", "command_line", "username")}


def protected_state():
    return {"processes": [identity(process_data(pid)) for pid in PROTECTED],
            "artifacts": protection_receipt()}


def preflight():
    api = inspect_api(52324, 30904)
    worker, parent = process_data(OLD_WORKER), process_data(PARENT)
    config = config_state()
    parent_expected = str(ROOT / "node_modules/next/dist/bin/next").replace("\\", "/").casefold()
    parent_args = parent.get("command_line", [])
    parent_match = (len(parent_args) >= 5 and parent_args[-3:] == ["dev", "--hostname", "127.0.0.1"]
                    and str(Path(parent_args[-4]).resolve()).replace("\\", "/").casefold() == parent_expected)
    worker_path = str(ROOT / "node_modules/next/dist/server/lib/start-server.js").casefold()
    worker_args = worker.get("command_line", [])
    worker_match = len(worker_args) == 2 and str(Path(worker_args[-1]).resolve()).casefold() == worker_path
    tree = {OLD_WORKER, *(child.pid for child in psutil.Process(OLD_WORKER).children(recursive=True))}
    protected = protected_state()
    flags = {"api_idle_and_health_checks": api["preflight_ready"],
             "exact_repo_worker": worker_match and worker.get("ppid") == PARENT,
             "exact_repo_dev_parent": parent_match,
             "expected_old_process_creation_times": worker.get("created_at_epoch") == OLD_WORKER_CREATED
             and parent.get("created_at_epoch") == PARENT_CREATED,
             "config_bytes_expected": config["sha256"] == CONFIG_SHA,
             "ui_tree_disjoint_from_protected": not bool(tree & set(PROTECTED)),
             "same_user_owner": worker.get("username") == parent.get("username") == "YUKI\\yuki2"}
    if not all(flags.values()):
        raise ValueError("preflight-failed:" + json.dumps(flags, sort_keys=True))
    return {"recorded_at_utc": now(), "checks": flags, "worker": worker, "parent": parent,
            "old_ui_children": [process_data(pid) for pid in sorted(tree - {OLD_WORKER})],
            "config": config, "protected": protected, "api_inspection": api,
            "code_sha256": code_hashes()}


def validate_same(before, fresh):
    if (identity(before["worker"]) != identity(fresh["worker"])
            or identity(before["parent"]) != identity(fresh["parent"])):
        raise ValueError("old-ui-worker-or-parent-instance-changed")
    if before["config"] != fresh["config"]:
        raise ValueError("config-identity-content-or-mtime-changed")
    if before["protected"] != fresh["protected"]:
        raise ValueError("protected-service-or-model-h04-state-changed")
    if before["code_sha256"] != fresh["code_sha256"]:
        raise ValueError("implementation-or-installed-watcher-changed")


def set_last_write_time_once(path):
    """One SetFileTime call: creation/access time pointers are NULL."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong,
                                  ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.SetFileTime.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    kernel.SetFileTime.restype = ctypes.c_bool
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateFileW(str(path), 0x100, 0x7, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        ticks = time.time_ns() // 100 + 116444736000000000
        last_write = ctypes.c_ulonglong(ticks)
        if not kernel.SetFileTime(handle, None, None, ctypes.byref(last_write)):
            raise ctypes.WinError(ctypes.get_last_error())
        return {"set_file_time_calls": 1, "requested_filetime_100ns": ticks,
                "creation_and_access_time_pointers_null": True}
    finally:
        kernel.CloseHandle(handle)


def wait_for_worker(before, *, timeout=120):
    deadline, observations = time.monotonic() + timeout, []
    while time.monotonic() < deadline:
        parent = process_data(PARENT)
        if identity(parent) != identity(before["parent"]):
            raise ValueError("dev-parent-instance-changed-after-trigger")
        found = []
        for child in psutil.Process(PARENT).children():
            args = child.cmdline()
            if (len(args) == 2 and str(Path(args[-1]).resolve()).casefold()
                    == str(ROOT / "node_modules/next/dist/server/lib/start-server.js").casefold()):
                found.append(process_data(child.pid))
        if len(found) > 1:
            raise ValueError("multiple-ui-workers-after-single-trigger")
        if found and found[0]["pid"] != OLD_WORKER and not psutil.pid_exists(OLD_WORKER):
            listeners = {c.pid for c in psutil.net_connections(kind="tcp")
                         if c.status == "LISTEN" and c.laddr.port == 3000}
            if listeners == {found[0]["pid"]}:
                health = {"api": local_health("http://127.0.0.1:8000/health"),
                          "web": local_health("http://127.0.0.1:3000/api/health")}
                observations.append({"recorded_at_utc": now(), "worker_pid": found[0]["pid"], "health": health})
                if all(value.get("http_status") == 200 for value in health.values()):
                    return {"worker": found[0], "parent": parent, "port_3000_owner": sorted(listeners),
                            "health": health, "observations": observations,
                            "old_ui_children": [{"pid": row["pid"], "still_exists": psutil.pid_exists(row["pid"])}
                                                for row in before["old_ui_children"]],
                            "ram_available_bytes": psutil.virtual_memory().available}
        time.sleep(1)
    raise TimeoutError("single-config-trigger-timeout-no-second-touch-or-automatic-fallback")


def prepare(directory, *, probe=preflight):
    directory = Path(directory)
    if directory.exists():
        raise ValueError("new-restart-receipt-directory-required")
    state = probe()
    directory.mkdir(parents=True, exist_ok=False)
    plan = {"schema": "ui-config-watcher-restart-v1", "mode": "prepared_only", "before": state,
            "allowed_effect": "One last-write-time update only; config bytes never change.",
            "config_watcher_not_graceful_request_draining": True,
            "signals_kills_elevation_launcher_build": 0, "mtime_restore_forbidden": True}
    write_new(directory / "plan.json", plan)
    write_new(directory / "plan_receipt.json", {"plan_sha256": file_hash(directory / "plan.json")})
    return plan


def execute(directory, *, root_go=False, probe=preflight, touch=set_last_write_time_once,
            wait=wait_for_worker, protected_probe=protected_state, config_probe=config_state,
            code_probe=code_hashes):
    if not root_go:
        raise ValueError("fresh-root-go-required-no-effect")
    directory = Path(directory)
    plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
    seal = json.loads((directory / "plan_receipt.json").read_text(encoding="utf-8"))
    if seal["plan_sha256"] != file_hash(directory / "plan.json"):
        raise ValueError("restart-plan-seal-mismatch")
    before = plan["before"]
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(before["recorded_at_utc"])).total_seconds()
    if not 0 <= age <= 120:
        raise ValueError("restart-plan-preflight-older-than-two-minutes")
    fresh = probe()
    validate_same(before, fresh)
    write_new(directory / "pretrigger.json", fresh)
    write_new(directory / "trigger.claim.json", {"plan_sha256": seal["plan_sha256"],
              "claimed_at_utc": now(), "maximum_last_write_time_calls": 1, "root_go": True})
    report = {"schema": "ui-config-watcher-restart-result-v1", "started_at_utc": now(),
              "status": "PENDING", "trigger_attempted": False, "signals_kills_uac_launcher_build": 0,
              "config_bytes_written": 0, "external_network_acquisition_requests": 0,
              "mtime_restored": False, "automatic_retry_or_fallback": False}
    try:
        report["trigger_attempted"] = True
        report["metadata_effect"] = touch(Path(before["config"]["path"]))
        after_touch = config_probe()
        report["config_after_trigger"] = after_touch
        if any(after_touch[key] != before["config"][key] for key in ("path", "sha256", "size_bytes", "file_id")):
            raise ValueError("config-content-or-identity-changed-after-metadata-trigger")
        if after_touch["mtime_ns"] <= before["config"]["mtime_ns"]:
            raise ValueError("last-write-time-did-not-advance")
        report["after"] = wait(before, timeout=120)
        report["protected_after"] = protected_probe()
        if report["protected_after"] != before["protected"]:
            raise ValueError("protected-service-or-artifact-changed-after-ui-restart")
        if code_probe() != before["code_sha256"]:
            raise ValueError("implementation-or-installed-watcher-changed-after-trigger")
        report["status"] = "RESTARTED_AND_VERIFIED"
    except Exception as error:
        report["status"] = "FAILED_OR_INCOMPLETE_NO_RETRY"
        report["reason"] = type(error).__name__ + ": " + str(error)
    finally:
        for name, callback in (("final_config", config_probe), ("final_protected", protected_probe)):
            try:
                report[name] = callback()
            except Exception as error:
                report[name + "_unverified"] = type(error).__name__ + ": " + str(error)
        report["finished_at_utc"] = now()
        write_new(directory / "result.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--prepare", action="store_true")
    action.add_argument("--execute", action="store_true")
    parser.add_argument("--root-go", action="store_true")
    args = parser.parse_args()
    result = prepare(args.run_dir) if args.prepare else execute(args.run_dir, root_go=args.root_go)
    print(json.dumps({"directory": str(args.run_dir.resolve()), "status": result.get("status", result.get("mode")),
                      "signals_kills_uac_launcher_build": 0}, ensure_ascii=False), flush=True)
    if args.execute and result["status"] != "RESTARTED_AND_VERIFIED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
