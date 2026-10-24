"""One explicit CTRL_BREAK for the verified private API console only."""
import argparse
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inspect_api_last3f_deployment import inspect


def write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prestop", type=Path, required=True)
    parser.add_argument("--root-go", action="store_true")
    args = parser.parse_args()
    if not args.root_go or datetime.now(timezone.utc) < datetime(2026, 10, 17, 7, 31, tzinfo=timezone.utc):
        raise ValueError("explicit-root-go-after-1631-JST-required")
    old = json.loads(args.prestop.read_text(encoding="utf-8"))
    api_pid, wrapper_pid = old["api"]["pid"], old["wrapper"]["pid"]
    if (api_pid, wrapper_pid) != (46404, 37972) or not old["preflight_ready"]:
        raise ValueError("unexpected-old-api-identity")
    current = inspect(api_pid, wrapper_pid)
    if (not current["preflight_ready"] or current["api"]["created_at_epoch"] != old["api"]["created_at_epoch"]
            or current["wrapper"]["created_at_epoch"] != old["wrapper"]["created_at_epoch"]):
        raise ValueError("api-not-idle-or-instance-changed")
    projection = lambda state: {key: value for key, value in state.items() if key != "forward_processes"}
    if projection(current["protected_before"]) != projection(old["protected_before"]):
        raise ValueError("protected-model-h04-or-pins-changed")
    if [(s["path"], s["sha256"]) for s in current["sources"]] != [(s["path"], s["sha256"]) for s in old["sources"]]:
        raise ValueError("approved-parser-source-changed")
    out = args.prestop.parent
    write(out / "prestop-final.json", current)
    expected = {api_pid, wrapper_pid, *(row["pid"] for row in current["descendants"])}
    if expected & {19600, 27320, 7536}:
        raise ValueError("protected-process-in-api-family")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.FreeConsole.argtypes, kernel.FreeConsole.restype = [], ctypes.c_bool
    kernel.AttachConsole.argtypes, kernel.AttachConsole.restype = [ctypes.c_ulong], ctypes.c_bool
    kernel.GetConsoleProcessList.argtypes = [ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong]
    kernel.GetConsoleProcessList.restype = ctypes.c_ulong
    kernel.GenerateConsoleCtrlEvent.argtypes, kernel.GenerateConsoleCtrlEvent.restype = [ctypes.c_ulong, ctypes.c_ulong], ctypes.c_bool
    kernel.FreeConsole()
    if not kernel.AttachConsole(api_pid):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        ids = (ctypes.c_ulong * 128)()
        count = kernel.GetConsoleProcessList(ids, 128)
        if not 0 < count <= 128 or set(ids[:count]) != expected | {os.getpid()}:
            raise ValueError("private-console-members-changed-abort-without-signal")
        handler_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong)
        handler = handler_type(lambda event: True)
        kernel.SetConsoleCtrlHandler.argtypes, kernel.SetConsoleCtrlHandler.restype = [handler_type, ctypes.c_bool], ctypes.c_bool
        if not kernel.SetConsoleCtrlHandler(handler, True):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.GenerateConsoleCtrlEvent(1, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        receipt = {"schema": "api-last3f-stop-receipt-v1", "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
                   "signal": "CTRL_BREAK_EVENT", "signal_sent": True, "targets": sorted(expected),
                   "root_go_received_after_1631_JST": True, "foreign_console_members": [],
                   "force_kill": False, "next_signal_sent": False, "collector_signal_sent": False}
        write(out / "stop-signal.json", receipt)
        print(json.dumps(receipt), flush=True)
    finally:
        kernel.FreeConsole()


if __name__ == "__main__":
    main()
