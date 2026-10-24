"""Read-only guard fixtures. Never launches a process or registers a task."""
from copy import deepcopy
from datetime import timedelta
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/forward_startup_guard.py"
spec = importlib.util.spec_from_file_location("forward_startup_guard", SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
NOW = guard.timestamp("2026-09-20T00:00:00+09:00")


def owner_state():
    return {"owner": {"owner_id": None, "fence": 3, "expires_at": None, "stop_requested": 0},
            "days": [{"date": "20260920", "status": "complete"}], "races": [], "last_event_seq": 1}


def test_alive_collector_never_restarted():
    state = owner_state(); state["owner"].update(owner_id="live", expires_at=guard.stamp(NOW+timedelta(seconds=60)))
    assert guard.decide_resume(state, processes=[], now=NOW) == "already_running"


def test_explicit_stop_persists_even_after_expiry_and_reboot():
    state = owner_state(); state["owner"].update(stop_requested=1, expires_at=guard.stamp(NOW-timedelta(seconds=1)))
    assert guard.decide_resume(state, processes=[], now=NOW) == "explicit_stop_preserved"


def test_expired_lease_with_surviving_process_does_not_spawn_or_kill():
    state = owner_state(); state["owner"].update(owner_id="stale", expires_at=guard.stamp(NOW-timedelta(seconds=1)))
    assert guard.decide_resume(state, processes=[{"pid": 123}], now=NOW) == "process_exists_without_live_lease_manual_review"


def test_reboot_with_no_process_and_no_stop_is_resume_eligible():
    state = owner_state(); state["owner"].update(owner_id="old", expires_at=guard.stamp(NOW-timedelta(seconds=1)))
    assert guard.decide_resume(state, processes=[], now=NOW) == "eligible_for_guarded_resume"
    assert guard.decide_resume(state, processes=[], now=guard.timestamp("2026-12-19T00:00:00+09:00")) == "fixed_study_ended"


def test_declared_capture_and_research_windows_do_not_change_contract():
    state = owner_state()
    state["races"] = [{"race_id": "202606040601", "race_date": "20260920", "post_time": "2026-09-20T10:00:00+09:00", "status": "queued", "has_snapshot": 0}]
    result = guard.operational_summary(state, now=NOW)
    assert result["capture_windows"][0]["capture_start_jst"] == "2026-09-20T09:54:30+09:00"
    assert result["capture_windows"][0]["deadline_jst"] == "2026-09-20T09:55:00+09:00"
    assert result["capture_windows"][0]["research_reserve_start_jst"] == "2026-09-20T09:52:30+09:00"


def test_legacy_launcher_gets_relative_not_broken_absolute_path(tmp_path):
    root = tmp_path / "reports/generated/forward-runtime/fixture with spaces"
    cmd = guard.launcher_command(tmp_path, root)
    assert cmd[-1] == str(root.relative_to(tmp_path))
    assert cmd[-2] == "-RuntimeDirectory" and "Hidden" in cmd
    with pytest.raises(ValueError): guard.launcher_command(tmp_path, tmp_path / "keiba/data")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def inputs(tmp_path):
    repo, root = tmp_path, tmp_path / "reports/generated/forward-runtime/fixture"
    for package in guard.PACKAGES:
        p = repo / "python-api" / package / "fixture.py"
        p.parent.mkdir(parents=True); p.write_text("# fixture\n", encoding="utf-8")
    for name in guard.EXTRA_SOURCES + ("python-api/forward_reports/evaluation.py", "scripts/start-forward-collector.ps1"):
        p = repo / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text("# fixed\n", encoding="utf-8")
    sources = {str(p.relative_to(repo)).replace("\\", "/"): guard.file_hash(p) for package in guard.PACKAGES for p in (repo / "python-api" / package).glob("*.py")}
    sources.update({name: guard.file_hash(repo/name) for name in guard.EXTRA_SOURCES})
    artifacts = {}
    for name in ("model", "calibrator", "features", "rule"):
        raw = name.encode(); digest = guard.hashlib.sha256(raw).hexdigest(); path = root / "objects" / digest
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw); artifacts[name] = digest
    protocol = {"source_manifest": sources, "artifact_hashes": artifacts, "fixed_model_id": "H04", "paper_only": True,
                "automatic_purchase": False, "date_from": "20260920", "date_to": "20261218", "feature_columns": [f"safe_{i}" for i in range(99)],
                "cutoff_seconds": 300, "capture_window_seconds": 30, "max_source_age_seconds": 120,
                "start_at": "2026-09-19T15:00:00Z", "end_at": "2026-12-18T15:00:00Z"}
    digest = guard.object_hash(protocol)
    write_json(root / "protocol.json", {"protocol": protocol, "protocol_sha256": digest})
    write_json(root / "analysis-definition.json", {"protocol_sha256": digest, "outcomes_observed": 0,
               "frozen_at": "2026-09-19T14:55:00Z", "definition": {"evaluation_source_sha256": guard.file_hash(repo/"python-api/forward_reports/evaluation.py")}})
    state = owner_state(); state["contract"] = {"protocol_sha256": digest, "artifact_hashes": artifacts}
    kwargs = {"expected_protocol": digest, "expected_analysis": guard.file_hash(root/"analysis-definition.json"),
              "expected_launcher": guard.file_hash(repo/"scripts/start-forward-collector.ps1"), "now": NOW}
    return repo, root, state, kwargs


def test_pins_verified_without_executing_any_source(inputs):
    repo, root, state, kwargs = inputs
    result = guard.verify_fixed_inputs(repo, root, state, **kwargs)
    assert result["all_frozen_hashes_match"] and result["feature_count"] == 99
    assert result["history"]["daily_binding_present"] is False


@pytest.mark.parametrize("relative,error", [
    ("python-api/experiments/fixture.py", "source-changed"),
    ("python-api/forward_runtime/fixture.py", "source-changed"),
    ("python-api/forward_reports/evaluation.py", "analysis-source-changed"),
    ("scripts/start-forward-collector.ps1", "launcher-changed"),
])
def test_source_or_launcher_change_blocks_resume(inputs, relative, error):
    repo, root, state, kwargs = inputs
    (repo/relative).write_text("# changed", encoding="utf-8")
    with pytest.raises(ValueError, match=error): guard.verify_fixed_inputs(repo, root, state, **kwargs)


def test_new_file_inside_frozen_glob_blocks_resume(inputs):
    repo, root, state, kwargs = inputs
    (repo/"python-api/experiments/extra.py").write_text("# unexpected", encoding="utf-8")
    with pytest.raises(ValueError, match="file-set-changed"): guard.verify_fixed_inputs(repo, root, state, **kwargs)


def test_protocol_analysis_and_artifact_tampering_blocked(inputs):
    repo, root, state, kwargs = inputs
    with pytest.raises(ValueError, match="protocol-changed"):
        guard.verify_fixed_inputs(repo, root, state, **{**kwargs, "expected_protocol": "0"*64})
    with pytest.raises(ValueError, match="analysis-file-changed"):
        guard.verify_fixed_inputs(repo, root, state, **{**kwargs, "expected_analysis": "0"*64})
    (root/"objects"/state["contract"]["artifact_hashes"]["model"]).write_bytes(b"wrong")
    with pytest.raises(ValueError, match="artifact-changed"): guard.verify_fixed_inputs(repo, root, state, **kwargs)


def test_missing_daytime_history_blocks_launch_instead_of_starting_heavy_preparation(inputs):
    repo, root, state, kwargs = inputs
    with pytest.raises(ValueError, match="after-0800"):
        guard.verify_fixed_inputs(repo, root, state, **{**kwargs, "now": guard.timestamp("2026-09-20T08:00:00+09:00")})


def test_ledger_readonly_and_no_full_capture_payload(tmp_path):
    from forward_runtime.ledger import RuntimeLedger
    root = tmp_path / "runtime"
    c = {"protocol_sha256": "a"*64, "artifact_hashes": {k: "b"*64 for k in ("model", "features", "calibrator", "rule")}}
    ledger = RuntimeLedger(root/"ledger.sqlite3"); ledger.initialize(c)
    before = guard.file_hash(ledger.path)
    result = guard.ledger_status(root)
    assert len(result["days"]) == 90
    assert guard.file_hash(ledger.path) == before
    assert result["races"] == []
