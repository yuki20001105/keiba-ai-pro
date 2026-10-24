"""Read-only Windows UI-server restart preflight; never sends a signal."""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

import psutil

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from inspect_api_last3f_deployment import health
from evaluate_db_shared_venues import preservation_receipt


def process_data(pid):
    process = psutil.Process(pid)
    result = {"pid": pid}
    for name, function in (
        ("ppid", process.ppid), ("name", process.name), ("exe", process.exe),
        ("command_line", process.cmdline), ("cwd", process.cwd),
        ("username", process.username), ("created_at_epoch", process.create_time),
        ("rss_bytes", lambda: process.memory_info().rss),
        ("child_pids", lambda: [p.pid for p in process.children()]),
        ("ancestor_pids", lambda: [p.pid for p in process.parents()]),
    ):
        try:
            result[name] = function()
        except (psutil.AccessDenied, psutil.NoSuchProcess) as error:
            result[name + "_unverified"] = type(error).__name__
    return result


def token_data(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    advapi.OpenProcessToken.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p)]
    advapi.OpenProcessToken.restype = ctypes.c_bool
    advapi.GetTokenInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                                           ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
    advapi.GetTokenInformation.restype = ctypes.c_bool
    process = kernel.OpenProcess(0x1000, False, pid)
    if not process:
        return {"verified": False, "open_process_error": ctypes.get_last_error()}
    token = ctypes.c_void_p()
    try:
        if not advapi.OpenProcessToken(process, 0x8, ctypes.byref(token)):
            return {"verified": False, "open_token_error": ctypes.get_last_error()}
        result = {"verified": True}
        for name, information_class in (("elevated", 20), ("session_id", 12), ("elevation_type", 18)):
            data, length = ctypes.c_ulong(), ctypes.c_ulong()
            if advapi.GetTokenInformation(token, information_class, ctypes.byref(data),
                                         ctypes.sizeof(data), ctypes.byref(length)):
                result[name] = data.value
            else:
                result[name + "_error"] = ctypes.get_last_error()
        return result
    finally:
        if token:
            kernel.CloseHandle(token)
        kernel.CloseHandle(process)


def console_data(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.FreeConsole.argtypes, kernel.FreeConsole.restype = [], ctypes.c_bool
    kernel.AttachConsole.argtypes, kernel.AttachConsole.restype = [ctypes.c_ulong], ctypes.c_bool
    kernel.GetConsoleProcessList.argtypes = [ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong]
    kernel.GetConsoleProcessList.restype = ctypes.c_ulong
    kernel.FreeConsole()
    if not kernel.AttachConsole(pid):
        return {"verified": False, "attach_error": ctypes.get_last_error(), "signal_sent": False}
    try:
        buffer = (ctypes.c_ulong * 128)()
        count = kernel.GetConsoleProcessList(buffer, 128)
        ids = sorted(pid for pid in buffer[:min(count, 128)] if pid != os.getpid())
        return {"verified": 0 < count <= 128, "members": ids,
                "processes": [process_data(member) for member in ids], "signal_sent": False}
    finally:
        kernel.FreeConsole()


def inspect():
    ui = psutil.Process(7536)
    family = [process_data(p.pid) for p in [ui, *ui.children(recursive=True)]]
    ancestors = [process_data(p.pid) for p in ui.parents()]
    protected_pids = (52324, 30904, 19600, 27320, 41016, 40136)
    protected = [process_data(pid) for pid in protected_pids]
    console = console_data(7536)
    console_pids = set(console.get("members", []))
    protected_tree = set(protected_pids)
    for pid in protected_pids:
        protected_tree.update(p.pid for p in psutil.Process(pid).children(recursive=True))
    ui_tree = {row["pid"] for row in family}
    return {"schema": "ui-only-restart-readonly-preflight-v1",
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "ui": family, "ui_ancestors": ancestors, "ui_console": console,
            "ui_token": token_data(7536), "inspector_token": token_data(os.getpid()),
            "protected_processes": protected,
            "protected_model_h04_and_pins": preservation_receipt(),
            "protected_tree_pids": sorted(protected_tree),
            "ui_tree_disjoint_from_protected": not bool(ui_tree & protected_tree),
            "ui_console_disjoint_from_protected": console.get("verified") is True
            and not bool(console_pids & protected_tree),
            "health": {"api": health("http://127.0.0.1:8000/health"),
                       "web": health("http://127.0.0.1:3000/api/health")},
            "ram_available_bytes": psutil.virtual_memory().available,
            "launcher": {"path": "scripts/start-local-app.ps1",
                         "sha256": hashlib.sha256((ROOT / "scripts/start-local-app.ps1").read_bytes()).hexdigest(),
                         "current_ui_mode": "development",
                         "current_ui_command": "npm run dev -- --hostname 127.0.0.1",
                         "api_reuse_supported": True,
                         "next_production_default_can_build_if_missing": True},
            "restart_authorized_for_this_inspector": False, "signal_sent": False,
            "services_started": False, "network_acquisition_requests": 0,
            "credentials_exported": False, "database_writes": False,
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = inspect()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({"path": str(args.out.resolve()), "ui_token": result["ui_token"],
                      "inspector_token": result["inspector_token"],
                      "console": result["ui_console"],
                      "ui_tree_disjoint_from_protected": result["ui_tree_disjoint_from_protected"],
                      "signal_sent": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
