"""Synthetic-only V3 receipt binding; never live network/DB copy/fit/service IO."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import types

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("repaired_context_research_v3_test", ROOT / "scripts/run_repaired_context_research_v3.py")
study = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(study)


def fixed_plan():
    return {"schema": "repair-gated-context-research-v2", "registered_at_utc": "old",
        "operation": "old", "repair_output": "old", "repair_processes": [], "runner_path": "old",
        "runner_sha256": "a" * 64, "code_sha256": {}, "workers": 3, "threads_per_worker": 2,
        "period": ["20160101", "20260930"], "groups": dict.fromkeys(["base", "cw", "style", "ped", "full"], []),
        "test_years": list(range(2016, 2027)), "presets": [{"rounds": 120}, {"rounds": 200}],
        "calibration": "CAL-only", "bootstrap_repetitions": 1000, "network": False, "activate_model": False,
        "history_policy": "strict prior", "protected_before": {"model": "same", "forward_processes": []},
        "service_processes": [{"pid": 1, "created_at_epoch": 2.0}]}


def continuation_plan():
    value = fixed_plan()
    value.update(schema=study.SCHEMA, registered_at_utc="new", runner_path="new", runner_sha256="b" * 64,
        original_study_sha256=study.ORIGINAL_STUDY_SHA,
        repair_resume=dict.fromkeys(["original_repair_plan", "old_repair_completion", "new_repair_plan", "continuation_plan"], {"path": "synthetic", "sha256": "a" * 64}))
    return value


@pytest.mark.parametrize("key", ["workers", "threads_per_worker", "period", "groups", "test_years", "presets",
    "calibration", "bootstrap_repetitions", "network", "activate_model", "history_policy", "protected_before", "service_processes"])
def test_every_original_research_setting_remains_fixed(key):
    original, new = fixed_plan(), continuation_plan()
    new[key] = "changed"
    with pytest.raises(ValueError, match="fixed-research-setting-changed"):
        study.assert_fixed_plan(new, original)


def test_new_orchestration_only_can_differ():
    study.assert_fixed_plan(continuation_plan(), fixed_plan())


def test_private_engine_preserves_original_module_and_frozen_formula_functions():
    original_guard = study.FROZEN.guard
    original_scanner = study.FROZEN.no_acquisition_writer_alive
    engine = study.configured_engine(continuation_plan())
    assert engine is not study.FROZEN
    assert study.FROZEN.guard is original_guard and study.FROZEN.no_acquisition_writer_alive is original_scanner
    assert engine.SCHEMA == study.SCHEMA and engine.OUTPUT == study.OUTPUT
    assert engine.guard is study.guard and engine.wait_for_repair is study.wait_for_repair
    # Frozen function bytecode, not a replacement implementation or new formula.
    for name in ("normalized_frame", "prepare", "snapshot", "run_jobs", "report_html", "execute"):
        assert getattr(engine, name).__code__.co_code == getattr(study.FROZEN, name).__code__.co_code


def test_hash_bound_file_rejects_wrong_digest_and_outside_root(tmp_path):
    path = tmp_path / "value.json"
    path.write_text('{"v":1}', encoding="utf-8")
    assert study.checked_file(path, parent=tmp_path, expected=study.sha(path)) == path
    with pytest.raises(ValueError, match="sha-mismatch"):
        study.checked_file(path, parent=tmp_path, expected="0" * 64)
    with pytest.raises(ValueError, match="sha-mismatch"):
        study.checked_file(path, parent=tmp_path, expected=None)
    child = tmp_path / "child"; child.mkdir()
    with pytest.raises(ValueError, match="outside"):
        study.checked_file(path, parent=child)


@pytest.mark.parametrize("binding", [{}, {"path": "x"}, {"path": "x", "sha256": "a" * 64, "allow": True}, None])
def test_receipt_references_need_explicit_path_and_sha_only(binding):
    with pytest.raises(ValueError, match="explicit-artifact-binding"):
        study.bound_json(binding)


def guard_fixtures(monkeypatch):
    original = fixed_plan()
    monkeypatch.setattr(study, "original_plan", lambda: original)
    monkeypatch.setattr(study, "bound_json", lambda *_a, **_k: {"schema": "last3f-network-resume-plan-v3",
        "original_plan_sha256": study.ORIGINAL_REPAIR_SHA, "new_plan_sha256": "a" * 64, "old_completion_sha256": "a" * 64})
    monkeypatch.setattr(study.FROZEN, "protections", lambda: original["protected_before"])
    monkeypatch.setattr(study, "process_alive", lambda _r: True)
    return original


def test_guard_checks_protected_services_by_frozen_pid_epoch(monkeypatch):
    guard_fixtures(monkeypatch)
    study.guard(continuation_plan())
    monkeypatch.setattr(study, "process_alive", lambda _r: False)
    with pytest.raises(ValueError, match="api-or-forward-process"):
        study.guard(continuation_plan())


def test_guard_rejects_active_model_or_h04_drift(monkeypatch):
    guard_fixtures(monkeypatch)
    monkeypatch.setattr(study.FROZEN, "protections", lambda: {"model": "different", "forward_processes": []})
    with pytest.raises(ValueError, match="active-model-or-h04"):
        study.guard(continuation_plan())


def fake_process(name, command, *, denied=False):
    import psutil
    def cmdline():
        if denied:
            raise psutil.AccessDenied(11)
        return command
    return types.SimpleNamespace(info={"pid": 11, "name": name, "cmdline": command}, cmdline=cmdline)


@pytest.mark.parametrize("entrypoint", sorted(study.WRITER_NAMES))
def test_v2_v3_and_wrapper_writers_cannot_be_ignored(monkeypatch, entrypoint):
    import psutil
    monkeypatch.setattr(psutil, "process_iter", lambda *_a: [fake_process("python.exe", ["python", "C:\\repo\\scripts\\" + entrypoint])])
    assert study.no_acquisition_writer_alive() is False


def test_unreadable_python_owner_is_not_cleared(monkeypatch):
    import psutil
    monkeypatch.setattr(psutil, "process_iter", lambda *_a: [fake_process("python.exe", None, denied=True)])
    assert study.no_acquisition_writer_alive() is False


def test_unrelated_process_does_not_block_research(monkeypatch):
    import psutil
    monkeypatch.setattr(psutil, "process_iter", lambda *_a: [fake_process("node.exe", ["node", "next"]), fake_process("python.exe", ["python", "main.py"])])
    assert study.no_acquisition_writer_alive() is True


def test_pinned_repair_pid_epoch_blocks_even_unrecognized_entrypoint(monkeypatch):
    monkeypatch.setattr(study, "process_alive", lambda _r: True)
    assert study.no_acquisition_writer_alive({"repair_processes": [{"pid": 22, "created_at_epoch": 1.0}]}) is False


def test_run_cannot_override_sealed_repair_paths():
    with pytest.raises(SystemExit) as error:
        study.main(["--run-dir", "unused", "--run", "--repair-run", "other"])
    assert error.value.code == 2


def test_registration_requires_both_explicit_repair_paths():
    with pytest.raises(SystemExit) as error:
        study.main(["--run-dir", "unused", "--register"])
    assert error.value.code == 2


def test_stop_before_repair_completion_never_trains(monkeypatch, tmp_path):
    directory = tmp_path / "study"; directory.mkdir()
    (directory / "stop-request.json").write_text("{}")
    plan = {"repair_output": str(tmp_path / "repair"), "max_wait_seconds": 20}
    monkeypatch.setattr(study, "guard", lambda _p: None)
    with pytest.raises(RuntimeError, match="stop-requested"):
        study.wait_for_repair(directory, plan)
    assert not (directory / "repair_gate_receipt.json").exists()


@pytest.fixture
def exhaustive_gate(monkeypatch, tmp_path):
    root = tmp_path / "repo"; root.mkdir()
    feature = root / "feature"; feature.mkdir()
    old_run = feature / "old"; old_run.mkdir()
    new_run = feature / "new"; new_run.mkdir()
    output = feature / "completion"; output.mkdir()
    monkeypatch.setattr(study, "ROOT", root)
    monkeypatch.setattr(study, "FEATURE_PARENT", feature)
    monkeypatch.setattr(study, "ORIGINAL_REPAIR", old_run)
    def write(path, value):
        path.write_text(json.dumps(value), encoding="utf-8")
        return {"path": str(path), "sha256": study.sha(path)}
    targets = [{"race_id": "2020" + f"{number:08d}", "race_date": "20200101"} for number in range(24819)]
    target_path = old_run / "targets.jsonl"
    target_path.write_text("".join(json.dumps(row) + "\n" for row in targets), encoding="utf-8")
    old_plan_ref = write(old_run / "plan.json", {"counts": {"target_races": 24819}, "targets_sha256": study.sha(target_path)})
    monkeypatch.setattr(study, "ORIGINAL_REPAIR_SHA", old_plan_ref["sha256"])
    flags = dict.fromkeys(["safety_verification_pass", "source_integrity_pass", "journal_audit_pass",
        "protected_state_unchanged", "verification_source_epoch_stable", "wrapper_and_verification_code_unchanged"], True)
    old_completion = write(old_run / "old-completion.json", {"schema": "last3f-single-network-run-wrapper-v1",
        "execution_outcome": "stopped", "plan_sha256": old_plan_ref["sha256"], **flags})
    new_plan_ref = write(new_run / "plan.json", {"resumed": True})
    continuation = write(new_run / "continuation_plan.json", {"continued": True})
    plan = {"repair_resume": {"original_repair_plan": old_plan_ref, "old_repair_completion": old_completion,
        "new_repair_plan": new_plan_ref, "continuation_plan": continuation}}
    proof_source = {"identity_grade": "canonical_url_plus_visible_date_and_all_runners"}
    proof_dir = new_run / "evidence"; proof_dir.mkdir()
    proof_ref = write(proof_dir / "proof.json", {"schema": "last3f-repair-source-proof-v1",
        "evidence": {"race_id": targets[-1]["race_id"], "source": proof_source}})
    records = [{**target, "status": "completed", "source_run": str(old_run), "event_sha256": "a" * 64,
        "remaining_gap_runners": 0} for target in targets]
    records[-1].update(status="partial", source_run=str(new_run), remaining_gap_runners=1,
        remaining_source_states={"missing": 1}, identity_grade=proof_source["identity_grade"],
        proof_path=proof_ref["path"], proof_sha256=proof_ref["sha256"])
    coverage = {"schema": "last3f-combined-target-coverage-audit-v3", "coverage_pass": True,
        "all_original_targets_accounted": True, "original_target_races": 24819, "pending": 0, "http400": 0,
        "original_plan_sha256": old_plan_ref["sha256"], "old_completion_sha256": old_completion["sha256"],
        "new_plan_sha256": new_plan_ref["sha256"], "records": records,
        "race_status_counts": {"completed": 24818, "partial": 1}, "remaining_nonterminal_races": 1}
    source = {"schema": "last3f-artifact-independent-verification-v1", "artifact_integrity_pass": True,
        "issues": {}, "file_receipts": {proof_ref["path"]: {"sha256": proof_ref["sha256"]}}}
    journal = {"schema": "last3f-combined-two-batch-journal-audit-v3", "audit_pass": True}
    receipt = {"schema": "last3f-network-resume-completion-v3", **flags,
        "original_plan_sha256": old_plan_ref["sha256"], "old_completion_sha256": old_completion["sha256"],
        "new_plan_sha256": new_plan_ref["sha256"], "continuation_plan_sha256": continuation["sha256"],
        "old_status_preserved": "stopped", "child": {"confirmed_exited": True, "exit_code": 0},
        "automatic_retries": 0, "execution_outcome": "partial_pending_sources"}
    state = {"plan": plan, "receipt": receipt, "coverage": coverage, "source": source, "journal": journal,
        "directory": output, "records": records, "proof_ref": proof_ref, "write": write}
    def refresh():
        for name in ("combined_coverage_audit", "source_integrity_receipt", "journal_audit"):
            value = {"combined_coverage_audit": coverage, "source_integrity_receipt": source, "journal_audit": journal}[name]
            ref = write(output / (name + ".json"), value)
            receipt[name], receipt[name + "_sha256"] = ref["path"], ref["sha256"]
    state["refresh"] = refresh
    refresh()
    return state


def validate(state):
    return study.validate_completion(state["receipt"], state["plan"], state["directory"])


def test_exact_exhaustive_verified_source_missing_pass_is_not_numeric_recovery(exhaustive_gate):
    result = validate(exhaustive_gate)
    assert result["all_targets_attempted_or_completed"] is True
    assert result["all_clocks_complete"] is False
    assert result["partial_outcome_not_renamed_completed"] is True
    assert result["original_target_races"] == 24819
    assert len(result["unavailable_clock_remainders"]) == 1
    assert result["old_status_preserved"] == "stopped"


@pytest.mark.parametrize("field", ["safety_verification_pass", "source_integrity_pass", "journal_audit_pass",
    "protected_state_unchanged", "verification_source_epoch_stable", "wrapper_and_verification_code_unchanged"])
def test_each_independent_completion_flag_required(exhaustive_gate, field):
    exhaustive_gate["receipt"][field] = False
    with pytest.raises(ValueError, match="independent-audit"):
        validate(exhaustive_gate)


@pytest.mark.parametrize("field", ["original_plan_sha256", "old_completion_sha256", "new_plan_sha256", "continuation_plan_sha256"])
def test_old_and_new_plans_and_original_stopped_receipt_are_bound(exhaustive_gate, field):
    exhaustive_gate["receipt"][field] = "0" * 64
    with pytest.raises(ValueError, match="continuation-binding"):
        validate(exhaustive_gate)


@pytest.mark.parametrize("state", ["stopped", "paused", "failed", "completed_selected_batch", "cache_only", "pending"])
def test_unfinished_resume_is_never_training_readiness(exhaustive_gate, state):
    exhaustive_gate["receipt"]["execution_outcome"] = state
    with pytest.raises(ValueError, match="not-exhausted"):
        validate(exhaustive_gate)


@pytest.mark.parametrize("field", ["pending", "http400"])
def test_unresolved_http400_or_pending_prevents_fit(exhaustive_gate, field):
    exhaustive_gate["coverage"][field] = 1
    exhaustive_gate["refresh"]()
    with pytest.raises(ValueError, match="pending-or-http400"):
        validate(exhaustive_gate)


def test_incomplete_target_scope_is_not_silently_dropped(exhaustive_gate):
    exhaustive_gate["coverage"]["records"].pop()
    exhaustive_gate["refresh"]()
    with pytest.raises(ValueError, match="records-count"):
        validate(exhaustive_gate)


@pytest.mark.parametrize("mutation", ["duplicate", "date", "status", "masked", "invalid-grade", "proof", "counter"])
def test_each_race_identity_and_only_safe_unavailable_clocks_required(exhaustive_gate, mutation):
    row = exhaustive_gate["coverage"]["records"][-1]
    if mutation == "duplicate":
        row["race_id"] = exhaustive_gate["records"][0]["race_id"]
    elif mutation == "date":
        row["race_date"] = "20200102"
    elif mutation == "status":
        row["status"] = "pending"
    elif mutation == "masked":
        row["remaining_source_states"] = {"masked": 1}
    elif mutation == "invalid-grade":
        row["identity_grade"] = "unverified"
    elif mutation == "proof":
        row["proof_sha256"] = "0" * 64
    else:
        exhaustive_gate["coverage"]["race_status_counts"]["completed"] += 1
    exhaustive_gate["refresh"]()
    with pytest.raises(ValueError):
        validate(exhaustive_gate)


def test_partial_missing_source_is_not_renamed_completed(exhaustive_gate):
    exhaustive_gate["receipt"]["execution_outcome"] = "completed"
    with pytest.raises(ValueError, match="must-not-be-renamed"):
        validate(exhaustive_gate)


def test_receipt_hash_cannot_be_omitted(exhaustive_gate):
    del exhaustive_gate["receipt"]["journal_audit_sha256"]
    with pytest.raises(ValueError, match="sha-mismatch"):
        validate(exhaustive_gate)


def test_validated_legitimate_nonfinish_missing_is_separate(exhaustive_gate):
    row = exhaustive_gate["records"][-1]
    row.update(status="domain_missing", source_missing_is_not_numeric_recovery=True, remaining_are_legitimate_nonfinishers=True)
    exhaustive_gate["coverage"].update(race_status_counts={"completed": 24818, "domain_missing": 1}, remaining_nonterminal_races=0)
    exhaustive_gate["receipt"]["execution_outcome"] = "completed_with_domain_missing"
    exhaustive_gate["refresh"]()
    result = validate(exhaustive_gate)
    assert result["all_clocks_complete"] is False
    assert result["partial_outcome_not_renamed_completed"] is False


def test_domain_missing_cannot_hide_missing_nonfinish_proof(exhaustive_gate):
    exhaustive_gate["records"][-1]["status"] = "domain_missing"
    exhaustive_gate["refresh"]()
    with pytest.raises(ValueError, match="legitimate-nonfinishers"):
        validate(exhaustive_gate)


def test_cached_identity_needs_real_original_byte_receipt(exhaustive_gate):
    row = exhaustive_gate["records"][-1]
    grade = "mobile_cached_transport_url_plus_visible_date_and_all_runners"
    proof_path = Path(row["proof_path"])
    content = json.loads(proof_path.read_text())
    content["evidence"]["source"]["identity_grade"] = grade
    row["identity_grade"] = grade
    proof_ref = exhaustive_gate["write"](proof_path, content)
    row["proof_sha256"] = proof_ref["sha256"]
    exhaustive_gate["source"]["file_receipts"][str(proof_path)]["sha256"] = proof_ref["sha256"]
    exhaustive_gate["refresh"]()
    with pytest.raises(ValueError, match="original-byte-receipt"):
        validate(exhaustive_gate)
    content["evidence"]["source"]["cached_transport_receipt"] = {"raw_bytes_digest_verified": True}
    proof_ref = exhaustive_gate["write"](proof_path, content)
    row["proof_sha256"] = proof_ref["sha256"]
    exhaustive_gate["source"]["file_receipts"][str(proof_path)]["sha256"] = proof_ref["sha256"]
    exhaustive_gate["refresh"]()
    assert validate(exhaustive_gate)["all_targets_attempted_or_completed"] is True
