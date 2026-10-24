"""One metadata-only restart handoff, using no actual process or filesystem effect."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import restart_ui_config_watcher as restart


@pytest.fixture
def state(tmp_path):
    before = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "worker": {"pid": 7536, "ppid": 8040, "created_at_epoch": 1, "command_line": ["node", "old"], "username": "owner"},
        "parent": {"pid": 8040, "ppid": 20, "created_at_epoch": 0, "command_line": ["node", "next", "dev"], "username": "owner"},
        "config": {"path": str(tmp_path / "next.config.js"), "sha256": "a" * 64, "size_bytes": 50, "file_id": 77, "mtime_ns": 1},
        "protected": {"processes": [{"pid": 123, "created_at_epoch": 3}], "artifacts": {"H04": "fixed"}},
        "code_sha256": {"fixed-code": "b" * 64}, "old_ui_children": [],
    }
    after_config = {**before["config"], "mtime_ns": 2}
    calls = []
    directory = tmp_path / "new-run"
    restart.prepare(directory, probe=lambda: deepcopy(before))
    def touch(path):
        calls.append(str(path))
        return {"set_file_time_calls": 1}
    options = dict(root_go=True, probe=lambda: deepcopy(before), touch=touch,
        wait=lambda before, timeout: {"worker": {"pid": 888}, "port_3000_owner": [888]},
        protected_probe=lambda: deepcopy(before["protected"]),
        config_probe=lambda: deepcopy(after_config), code_probe=lambda: dict(before["code_sha256"]))
    return directory, before, after_config, calls, options


def test_success_exactly_one_metadata_call(state):
    directory, before, after_config, calls, options = state
    result = restart.execute(directory, **options)
    assert result["status"] == "RESTARTED_AND_VERIFIED"
    assert calls == [before["config"]["path"]]
    assert result["config_bytes_written"] == result["signals_kills_uac_launcher_build"] == 0
    assert result["mtime_restored"] is False
    assert (directory / "trigger.claim.json").is_file()


def test_no_go_no_effect(state):
    directory, _, _, calls, options = state
    options["root_go"] = False
    with pytest.raises(ValueError, match="root-go"):
        restart.execute(directory, **options)
    assert not calls and not (directory / "trigger.claim.json").exists()


def test_changed_plan_seal_refuses_without_effect(state):
    directory, _, _, calls, options = state
    with (directory / "plan.json").open("a") as file:
        file.write(" ")
    with pytest.raises(ValueError, match="seal-mismatch"):
        restart.execute(directory, **options)
    assert not calls


def test_old_plan_refuses_without_effect(state):
    directory, before, _, calls, options = state
    before["recorded_at_utc"] = (datetime.now(timezone.utc) - timedelta(seconds=121)).isoformat()
    plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
    plan["before"] = before
    (directory / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (directory / "plan_receipt.json").write_text(json.dumps({"plan_sha256": restart.file_hash(directory / "plan.json")}), encoding="utf-8")
    with pytest.raises(ValueError, match="two-minutes"):
        restart.execute(directory, **options)
    assert not calls


@pytest.mark.parametrize("group,key,value", [
    ("worker", "created_at_epoch", 99), ("parent", "created_at_epoch", 99),
    ("config", "sha256", "changed"), ("config", "mtime_ns", 99),
    ("protected", "artifacts", {"H04": "changed"}), ("code_sha256", "fixed-code", "changed"),
])
def test_changed_instance_or_protection_refuses(state, group, key, value):
    directory, before, _, calls, options = state
    fresh = deepcopy(before); fresh[group][key] = value
    options["probe"] = lambda: fresh
    with pytest.raises(ValueError):
        restart.execute(directory, **options)
    assert not calls


def test_timeout_never_retries_or_restores(state):
    directory, _, _, calls, options = state
    options["wait"] = lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("not-restarted"))
    result = restart.execute(directory, **options)
    assert result["status"] == "FAILED_OR_INCOMPLETE_NO_RETRY" and len(calls) == 1
    with pytest.raises(FileExistsError):
        restart.execute(directory, **options)
    assert len(calls) == 1 and not result["mtime_restored"]


def test_metadata_denied_attempt_is_not_retried(state):
    directory, _, _, calls, options = state
    def denied(path):
        calls.append(str(path)); raise PermissionError("denied")
    options["touch"] = denied
    result = restart.execute(directory, **options)
    assert result["status"] == "FAILED_OR_INCOMPLETE_NO_RETRY" and len(calls) == 1
    assert result["final_protected"] and result["final_config"]


@pytest.mark.parametrize("key,value", [("sha256", "changed"), ("mtime_ns", 1), ("file_id", 55)])
def test_unexpected_config_state_fails_without_second_touch(state, key, value):
    directory, _, config, calls, options = state
    config[key] = value
    result = restart.execute(directory, **options)
    assert result["status"] == "FAILED_OR_INCOMPLETE_NO_RETRY" and len(calls) == 1


def test_after_protection_change_reports_failure(state):
    directory, _, _, calls, options = state
    options["protected_probe"] = lambda: {"artifacts": "changed"}
    result = restart.execute(directory, **options)
    assert result["status"] == "FAILED_OR_INCOMPLETE_NO_RETRY" and len(calls) == 1


def test_windows_effect_sets_lastwrite_only_one_call(monkeypatch, tmp_path):
    calls = []
    class Function:
        def __init__(self, name, result): self.name, self.result = name, result
        def __call__(self, *args): calls.append((self.name, args)); return self.result
    class Kernel:
        CreateFileW = Function("CreateFileW", 99)
        SetFileTime = Function("SetFileTime", True)
        CloseHandle = Function("CloseHandle", True)
    monkeypatch.setattr(restart.ctypes, "WinDLL", lambda *args, **kwargs: Kernel())
    result = restart.set_last_write_time_once(tmp_path / "next.config.js")
    assert [item[0] for item in calls] == ["CreateFileW", "SetFileTime", "CloseHandle"]
    assert calls[0][1][1] == 0x100  # FILE_WRITE_ATTRIBUTES, never content-write access.
    assert calls[1][1][1:3] == (None, None)  # creation and access times untouched.
    assert result["set_file_time_calls"] == 1
