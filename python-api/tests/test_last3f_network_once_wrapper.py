"""Finite process handoff and independent journal checks; no real HTTP/app."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import run_last3f_gap_network_once as wrapper
from scraping.last3f_gap_repair import apply_evidence
from scraping.safe_repair import initialize_journal
from services.prediction_source_revision import initialize_source_revision

RID = "202605010101"
PROTECTED = {"active_model_id": "model_untouched", "forward_protocol_sha256": "a" * 64,
             "forward_processes": [{"pid": 123}]}


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def fixture(tmp_path):
    root = tmp_path / "repository"
    parent = root / "reports/generated/data-quality/feature-gap"
    run = parent / "frozen-v2"
    run.mkdir(parents=True)
    for filename in (wrapper.CLI, wrapper.VERIFIER, "python-api/.venv/Scripts/python.exe"):
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"# isolated executable/code fixture\n")
    path = root / "keiba/data/keiba_ultimate.db"
    path.parent.mkdir(parents=True)
    runners = [{"race_id": RID, "horse_number": i, "horse_id": f"202000000{i}", "horse_name": f"テスト{i}",
        "finish_position": i, "finish_time": "1:34.5", "odds": i + 1, "popularity": i,
        "last_3f": "**", "last_3f_rank": None} for i in (1, 2)]
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE races_ultimate(race_id TEXT PRIMARY KEY,data TEXT,created_at TEXT DEFAULT 'original')")
        db.execute("CREATE TABLE race_results_ultimate(id INTEGER PRIMARY KEY,race_id TEXT,data TEXT,created_at TEXT DEFAULT 'original')")
        dump_meta = {"race_id": RID, "date": "20260104", "venue": "東京", "distance": 1600,
                     "track_type": "芝", "num_horses": 2, "field_condition": "良"}
        db.execute("INSERT INTO races_ultimate(race_id,data) VALUES(?,?)", (RID, json.dumps(dump_meta)))
        for i, runner in enumerate(runners, 1):
            db.execute("INSERT INTO race_results_ultimate(id,race_id,data) VALUES(?,?,?)", (i, RID, json.dumps(runner)))
    assert initialize_source_revision(path.resolve(), True)
    initialize_journal(path)
    dump(run / "targets.jsonl", {"race_id": RID})
    plan = {"schema": "last3f-gap-repair-plan-v2", "batch_id": run.name, "database": str(path),
        "targets_sha256": wrapper.sha(run / "targets.jsonl"), "protected_before": PROTECTED,
        "code_sha256": {wrapper.CLI: wrapper.sha(root / wrapper.CLI)}}
    dump(run / "plan.json", plan)
    dump(run / "plan_receipt.json", {"batch_id": run.name, "plan_sha256": wrapper.sha(run / "plan.json")})
    dump(run / "status.json", {"status": "partial_pending_sources"})
    body_hash = hashlib.sha256(b"synthetic public result body").hexdigest()
    body = run / "sources/raw" / (body_hash + ".bin")
    body.parent.mkdir(parents=True)
    body.write_bytes(b"synthetic public result body")
    evidence = {"race_id": RID, "race_date": "20260104", "runner_count": 2,
        "rows": [{"horse_number": row["horse_number"], "horse_id": row["horse_id"], "horse_name": row["horse_name"],
                  "last3f_state": "valid", "last3f_seconds": 35.0 + row["horse_number"] / 10} for row in runners],
        "source": {"http_status": 200, "raw_body_sha256": body_hash,
            "source_url": f"https://race.netkeiba.com/race/result.html?race_id={RID}",
            "all_runner_identity_verified": True, "visible_race_date_verified": True,
            "historical_capture_time_proven": False, "raw_digest_verification": "verified_against_bytes",
            "observed_at_utc": "2026-10-17T07:00:00+00:00"}}
    return {"root": root, "run": run, "db": path, "parent": parent, "evidence": evidence, "body": body}


def protected():
    return deepcopy(PROTECTED)


def fake_runner(fixture, *, child_state="partial_pending_sources", child_exit=0, confirm=True,
                repair=True, source_pass=True, after_source=None):
    calls = []
    def runner(command, *, cwd, stdout_path, stderr_path):
        calls.append(command)
        Path(stdout_path).write_bytes(b"fixture process log\n")
        Path(stderr_path).write_bytes(b"")
        if "--apply-network" in command:
            if repair:
                apply_evidence(fixture["db"], fixture["evidence"], batch_id=fixture["run"].name)
            dump(fixture["run"] / "status.json", {"status": child_state, "updated_runners_total": 99999,
                "network_metrics": {"network_requests": 1}})
            return {"pid": 1234, "exit_code": child_exit, "confirmed_exited": confirm}
        assert len(calls) == 2 and "verify_last3f_gap_artifacts.py" in command[3]
        output = Path(command[command.index("--output-dir") + 1]); output.mkdir()
        dump(output / "source_integrity_receipt.json", {"artifact_integrity_pass": source_pass,
             "file_receipts": {str(fixture["body"].resolve()): {"sha256": wrapper.sha(fixture["body"])}}})
        if after_source:
            after_source()
        return {"pid": 2345, "exit_code": 0 if source_pass else 1, "confirmed_exited": True}
    return runner, calls


def execute(fixture, runner, suffix="once", **kwargs):
    return wrapper.execute_once(fixture["run"], fixture["parent"] / suffix, limit=20,
        repository_root=fixture["root"], runner=runner, protection_fn=protected, **kwargs)


def test_child_is_invoked_once_then_verifier_and_counts_are_journal_authoritative(fixture):
    runner, calls = fake_runner(fixture)
    result = execute(fixture, runner)
    assert len(calls) == 2
    assert calls[0] == [str((fixture["root"] / "python-api/.venv/Scripts/python.exe").resolve()),
        "-X", "utf8", str(fixture["root"] / wrapper.CLI), "--run-dir", str(fixture["run"]),
        "--apply-network", "--allow-network", "--limit", "20"]
    assert calls[0].count("--apply-network") == 1 and calls[0][calls[0].index("--limit") + 1] == "20"
    assert result["network_cli_invocations"] == 1 and result["automatic_retries"] == 0
    assert result["safety_verification_pass"] is True and result["wrapper_exit_code"] == 0
    assert result["journal_counts"]["unique_repaired_rows"] == 2
    assert result["new_unique_repaired_rows_this_child"] == 2
    assert result["child_final_status"]["updated_runners_total"] == 99999
    assert result["full_source_preservation_claimed"] is False
    assert (fixture["parent"] / "once/completion_receipt.json").exists()


@pytest.mark.parametrize("state,code,expected", [("blocked", 0, 2), ("paused", 0, 2), ("stopped", 0, 2),
    ("failed", 1, 1), ("interrupted", 130, 1), ("unknown", 0, 2)])
def test_every_confirmed_child_exit_is_sealed_and_then_verified(fixture, state, code, expected):
    runner, calls = fake_runner(fixture, child_state=state, child_exit=code, repair=False)
    result = execute(fixture, runner)
    assert len(calls) == 2 and result["child"]["exit_code"] == code
    assert result["safety_verification_pass"] is True
    assert result["execution_outcome"] == state and result["wrapper_exit_code"] == expected
    assert result["journal_counts"]["unique_repaired_rows"] == 0


def test_unconfirmed_child_exit_never_starts_readonly_verifier(fixture):
    runner, calls = fake_runner(fixture, confirm=False)
    result = execute(fixture, runner)
    assert len(calls) == 1
    assert result["safety_verification_pass"] is False
    assert "exit-not-confirmed" in result["failure"]


def test_source_verification_failure_is_not_hidden_by_successful_child(fixture):
    runner, calls = fake_runner(fixture, source_pass=False)
    result = execute(fixture, runner)
    assert len(calls) == 2 and result["wrapper_exit_code"] == 1
    assert result["source_integrity_pass"] is False


def test_protection_change_is_recorded_but_readonly_checks_still_run(fixture):
    runner, calls = fake_runner(fixture)
    snapshots = []
    def changed_protection():
        snapshots.append(1)
        return protected() if len(snapshots) == 1 else {**protected(), "active_model_id": "model_changed"}
    result = wrapper.execute_once(fixture["run"], fixture["parent"] / "changed-protection", limit=20,
        repository_root=fixture["root"], runner=runner, protection_fn=changed_protection)
    assert len(calls) == 2
    assert result["protected_state_unchanged"] is False and result["safety_verification_pass"] is False


def test_external_raw_write_during_verification_prevents_latest_pass(fixture):
    def unrelated_update():
        with sqlite3.connect(fixture["db"]) as db:
            raw = db.execute("SELECT data FROM races_ultimate").fetchone()[0]
            value = json.loads(raw); value["weather"] = "雨"
            db.execute("UPDATE races_ultimate SET data=?", (json.dumps(value),))
    runner, calls = fake_runner(fixture, after_source=unrelated_update)
    result = execute(fixture, runner)
    assert result["verification_source_epoch_stable"] is False
    assert result["safety_verification_pass"] is False
    assert result["journal_audit_pass"] is True  # Named-batch tails still match; full-period latest does not.


def test_after_child_launcher_exception_gets_own_failure_receipt_without_retry(fixture):
    calls = []
    def fail(*args, **kwargs):
        calls.append(args)
        raise OSError("fixture process launch failure")
    result = execute(fixture, fail)
    assert len(calls) == 1 and result["wrapper_exit_code"] == 1
    assert "launch failure" in result["failure"]
    assert (fixture["parent"] / "once/completion_receipt.json").exists()


def test_code_change_during_child_is_not_accepted(fixture):
    def mutate():
        (fixture["root"] / wrapper.CLI).write_bytes(b"changed frozen CLI")
    runner, calls = fake_runner(fixture, after_source=mutate)
    result = execute(fixture, runner)
    assert result["wrapper_and_verification_code_unchanged"] is False
    assert result["safety_verification_pass"] is False


def test_existing_output_is_never_overwritten(fixture):
    runner, calls = fake_runner(fixture)
    execute(fixture, runner)
    before = (fixture["parent"] / "once/completion_receipt.json").read_bytes()
    with pytest.raises(ValueError, match="new-feature-gap"):
        execute(fixture, runner)
    assert (fixture["parent"] / "once/completion_receipt.json").read_bytes() == before
    assert len(calls) == 2


def test_explicit_positive_finite_limit_is_required(fixture):
    runner, calls = fake_runner(fixture)
    for limit in (None, 0, -1, True):
        with pytest.raises(ValueError, match="explicit-positive-limit"):
            wrapper.execute_once(fixture["run"], fixture["parent"] / "no-run", limit=limit,
                repository_root=fixture["root"], runner=runner, protection_fn=protected)
    assert calls == []


def test_journal_hash_and_current_tail_are_verified_not_just_row_counts(fixture):
    apply_evidence(fixture["db"], fixture["evidence"], batch_id=fixture["run"].name)
    result = wrapper.journal_audit(fixture["db"], fixture["run"].name)
    assert result["audit_pass"] is True and result["counts"]["unique_repaired_rows"] == 2
    with sqlite3.connect(fixture["db"]) as db:
        db.execute("UPDATE data_quality_repair_journal_v1 SET after_sha256='wrong' WHERE id=1")
    result = wrapper.journal_audit(fixture["db"], fixture["run"].name)
    assert result["audit_pass"] is False and result["issues"]["after_hash_mismatch"] == 1
    assert result["issues"]["journal_tail_differs_from_current_row_external_change_possible"] == 1


def test_journal_source_must_be_among_independently_verified_raw_files(fixture):
    apply_evidence(fixture["db"], fixture["evidence"], batch_id=fixture["run"].name)
    result = wrapper.journal_audit(fixture["db"], fixture["run"].name,
        source_receipt={"file_receipts": {}}, run_dir=fixture["run"])
    assert result["audit_pass"] is False
    assert result["issues"]["journal_source_not_in_independently_verified_raw_files"] == 2


def test_existing_batch_counts_are_not_misreported_as_new_child_repairs(fixture):
    apply_evidence(fixture["db"], fixture["evidence"], batch_id=fixture["run"].name)
    runner, calls = fake_runner(fixture)
    result = execute(fixture, runner)
    assert result["journal_counts_before_child"]["unique_repaired_rows"] == 2
    assert result["journal_counts"]["unique_repaired_rows"] == 2
    assert result["new_unique_repaired_rows_this_child"] == result["new_journal_entries_this_child"] == 0


def test_disallowed_result_mutation_fails_independent_field_validation(fixture):
    apply_evidence(fixture["db"], fixture["evidence"], batch_id=fixture["run"].name)
    with sqlite3.connect(fixture["db"]) as db:
        after = json.loads(db.execute("SELECT after_data FROM data_quality_repair_journal_v1 WHERE id=1").fetchone()[0])
        after["finish_position"] = 2
        encoded = json.dumps(after)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        db.execute("UPDATE data_quality_repair_journal_v1 SET after_data=?,after_sha256=? WHERE id=1", (encoded, digest))
        db.execute("UPDATE race_results_ultimate SET data=? WHERE id=1", (encoded,))
    result = wrapper.journal_audit(fixture["db"], fixture["run"].name)
    assert result["audit_pass"] is False
    assert result["issues"]["last3f_unrelated_or_no_clock_change"] == 1


def test_running_process_is_spawned_once_without_shell_and_start_is_sealed(tmp_path, monkeypatch):
    calls = []
    class Child:
        pid = 4321
        code = None
        def wait(self):
            self.code = 0
            return self.code
        def poll(self):
            return self.code
    def launch(command, **kwargs):
        calls.append((command, kwargs))
        return Child()
    monkeypatch.setattr(wrapper.subprocess, "Popen", launch)
    command = ["fixture-python", "script.py", "--limit", "20"]
    result = wrapper.run_process(command, cwd=tmp_path, stdout_path=tmp_path / "child.stdout.log", stderr_path=tmp_path / "child.stderr.log")
    assert len(calls) == 1 and calls[0][0] == command
    assert calls[0][1]["shell"] is False and calls[0][1]["stdin"] == wrapper.subprocess.DEVNULL
    assert result["confirmed_exited"] is True and result["exit_code"] == 0
    assert json.loads((tmp_path / "child.stdout.log.process-start.json").read_text())["pid"] == 4321


def test_wrapper_interruption_never_kills_or_verifies_running_child(tmp_path, monkeypatch):
    class Child:
        pid = 4321
        def wait(self):
            raise KeyboardInterrupt()
        def poll(self):
            return None
    monkeypatch.setattr(wrapper.subprocess, "Popen", lambda *args, **kwargs: Child())
    with pytest.raises(KeyboardInterrupt):
        wrapper.run_process(["fixture-python"], cwd=tmp_path, stdout_path=tmp_path / "child.stdout.log", stderr_path=tmp_path / "child.stderr.log")
    receipt = json.loads((tmp_path / "child-still-running-4321.json").read_text())
    assert receipt["poll"] is None and receipt["verifier_started"] is False
