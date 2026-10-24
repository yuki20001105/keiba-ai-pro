"""A separately sealed continuation of the fixed V2 55-job feature study.

Only repair transport/receipt binding is versioned.  Formulae, feature groups,
chronological partitions, presets, calibration, bootstrap and CPU allocation
are inherited byte-for-byte from the previously registered study.  No network,
acquisition write, model activation, service restart, or automatic retry exists
in this runner.  The old runner and all old artifacts remain untouched.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import importlib.util
import json
from pathlib import Path, PureWindowsPath
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / name) for name in ("scripts", "python-api", "keiba")]
OUTPUT = ROOT / "reports/generated/context-repaired-v3"
FEATURE_PARENT = ROOT / "reports/generated/data-quality/feature-gap"
ORIGINAL_STUDY = ROOT / "reports/generated/context-repaired-v2/20261017-after-last3f-v2"
ORIGINAL_STUDY_SHA = "96cd84e7813ef64f3ede3a1b2dff7b5abf38c1faa82ea2be0c43c6f3360799f3"
ORIGINAL_REPAIR = ROOT / "reports/generated/data-quality/feature-gap/20261017-last3f-v2"
ORIGINAL_REPAIR_SHA = "71b36328587ceff9a80272994c1fff479661a51a94b77e9c3811477cb1898675"
ORIGINAL_REPAIR_OUTPUT = FEATURE_PARENT / "20261017-last3f-network-full-v1"
SCHEMA = "repair-gated-context-research-v3"
HEX = re.compile(r"[a-f0-9]{64}")
_NO_HASH_ARGUMENT = object()
IDENTITY_GRADES = frozenset({"canonical_url_plus_visible_date_and_all_runners",
    "cached_transport_url_plus_visible_date_and_all_runners",
    "mobile_cached_transport_url_plus_visible_date_and_all_runners",
    "mobile_network_transport_url_plus_visible_date_and_all_runners"})
OPERATIONAL_KEYS = frozenset({"schema", "registered_at_utc", "operation", "repair_output",
    "repair_processes", "code_sha256", "runner_path", "runner_sha256"})
WRITER_NAMES = frozenset({"repair_last3f_gaps_v2.py", "repair_last3f_gaps_v3.py",
    "run_last3f_gap_network_once.py", "run_last3f_gap_network_once_v3.py",
    "run_last3f_network_resume_v3.py", "repair_last3f_resume_v3.py"})


def load_frozen_v2():
    """Private module namespace; no file or shared imported-module mutation."""
    spec = importlib.util.spec_from_file_location("_context_research_v2_private_v3", ROOT / "scripts/run_repaired_context_research.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FROZEN = load_frozen_v2()
sha, read, exclusive, now = FROZEN.sha, FROZEN.read, FROZEN.exclusive, FROZEN.now
process_alive = FROZEN.process_alive


def checked_file(path, *, parent=None, expected=_NO_HASH_ARGUMENT):
    candidate = Path(path).absolute()
    if candidate.is_symlink() or any(part.is_symlink() for part in candidate.parents):
        raise ValueError("v3-linked-artifact-forbidden")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(Path(ROOT if parent is None else parent).resolve(strict=True)) or not resolved.is_file():
        raise ValueError("v3-artifact-outside-allowed-root")
    if expected is not _NO_HASH_ARGUMENT and (not isinstance(expected, str) or not HEX.fullmatch(expected) or sha(resolved) != expected):
        raise ValueError("v3-artifact-sha-mismatch:" + str(resolved))
    return resolved


def reference(path):
    path = checked_file(path)
    return {"path": str(path), "sha256": sha(path)}


def bound_json(binding, *, parent=None):
    if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}:
        raise ValueError("v3-explicit-artifact-binding-required")
    return read(checked_file(binding["path"], parent=parent, expected=binding["sha256"]))


def original_plan():
    plan = read(checked_file(ORIGINAL_STUDY / "registered_plan.json", expected=ORIGINAL_STUDY_SHA))
    receipt = read(ORIGINAL_STUDY / "registration_receipt.json")
    if receipt.get("registered_plan_sha256") != ORIGINAL_STUDY_SHA or plan.get("schema") != "repair-gated-context-research-v2":
        raise ValueError("v3-original-study-registration-invalid")
    return plan


def assert_fixed_plan(plan, original):
    """No metric, period, feature, budget or safety-policy drift is permitted."""
    for key, value in original.items():
        if key not in OPERATIONAL_KEYS and plan.get(key) != value:
            raise ValueError("v3-fixed-research-setting-changed:" + key)
    if (plan.get("workers") != 3 or plan.get("threads_per_worker") != 2
            or plan.get("period") != ["20160101", "20260930"]
            or len(plan.get("groups", {})) * len(plan.get("test_years", [])) != 55):
        raise ValueError("v3-fixed-55-job-budget-required")


def safe_directory(path, *, new=False):
    candidate = Path(path).absolute()
    if candidate.is_symlink() or any(part.is_symlink() for part in candidate.parents):
        raise ValueError("v3-research-linked-path-forbidden")
    result = candidate.resolve()
    if result.parent != OUTPUT.resolve() or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", result.name):
        raise ValueError("v3-new-separate-research-output-required")
    if new and result.exists():
        raise FileExistsError(result)
    return result


def basename(value):
    return PureWindowsPath(str(value)).name.casefold()


def repair_references(repair_root, extra_names=()):
    import psutil
    names = WRITER_NAMES | {basename(value) for value in extra_names}
    references = []
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            command = process.info.get("cmdline") or []
            if (any(basename(token) in names for token in command)
                    and any(str(repair_root).casefold() == str(token).replace("/", "\\").casefold()
                            or str(repair_root).replace("\\", "/").casefold() == str(token).replace("\\", "/").casefold()
                            for token in command)):
                references.append({"pid": process.pid, "created_at_epoch": process.create_time(), "command": command})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return references


def no_acquisition_writer_alive(plan=None):
    import psutil
    names = WRITER_NAMES | {"run_repaired_context_research.py"} | {basename(value) for value in (plan or {}).get("repair_writer_entrypoints", [])}
    for reference in (plan or {}).get("repair_processes", []):
        if process_alive(reference):
            return False
    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            command = process.info.get("cmdline")
            if command and any(basename(token) in names for token in command):
                return False
            if not command and str(process.info.get("name", "")).casefold() in {"python", "python.exe", "pythonw.exe"}:
                # An unreadable Python owner cannot be silently cleared.
                command = process.cmdline()
                if not command:
                    return False
                if any(basename(token) in names for token in command):
                    return False
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            return False
    return True


def guard(plan):
    original = original_plan()
    assert_fixed_plan(plan, original)
    if plan.get("original_study_sha256") != ORIGINAL_STUDY_SHA or plan.get("schema") != SCHEMA:
        raise ValueError("v3-original-study-or-schema-binding-invalid")
    bindings = {name: bound_json(plan["repair_resume"][name], parent=FEATURE_PARENT)
        for name in ("original_repair_plan", "old_repair_completion", "new_repair_plan", "continuation_plan")}
    envelope = bindings["continuation_plan"]
    if (envelope.get("schema") != "last3f-network-resume-plan-v3"
            or envelope.get("original_plan_sha256") != ORIGINAL_REPAIR_SHA
            or envelope.get("new_plan_sha256") != plan["repair_resume"]["new_repair_plan"]["sha256"]
            or envelope.get("old_completion_sha256") != plan["repair_resume"]["old_repair_completion"]["sha256"]):
        raise ValueError("v3-continuation-envelope-binding-changed")
    for name, item in envelope.get("original_artifacts", {}).items():
        path = checked_file(item["path"], parent=FEATURE_PARENT)
        current_stat = path.stat()
        if envelope.get("original_fingerprints", {}).get(name) != [current_stat.st_size, current_stat.st_mtime_ns]:
            raise ValueError("v3-original-artifact-fingerprint-changed:" + name)
    current = FROZEN.protections()
    if FROZEN.core(current) != FROZEN.core(original["protected_before"]):
        raise ValueError("v3-active-model-or-h04-protected-state-changed")
    for item in original.get("service_processes", []):
        if not process_alive(item):
            raise ValueError("v3-protected-api-or-forward-process-exited-or-replaced")
    for relative, expected in plan["code_sha256"].items():
        checked_file(ROOT / relative, expected=expected)
    return current


def configured_engine(plan=None):
    """Only the new child-local orchestration names differ from frozen V2."""
    engine = load_frozen_v2()
    engine.OUTPUT = OUTPUT
    engine.SCHEMA = SCHEMA
    engine.safe_directory = safe_directory
    engine.guard = guard
    engine.no_acquisition_writer_alive = lambda: no_acquisition_writer_alive(plan)
    engine.wait_for_repair = wait_for_repair
    return engine


def register(directory, *, repair_run, repair_output, writer_entrypoints=()):
    """Register only; never start acquisition, copy a DB or dispatch a fit."""
    directory = safe_directory(directory, new=True)
    original = original_plan()
    old_receipt = checked_file(ORIGINAL_REPAIR_OUTPUT / "completion_receipt.json", parent=FEATURE_PARENT)
    checked_file(ORIGINAL_REPAIR / "plan.json", expected=ORIGINAL_REPAIR_SHA)
    repair_run = Path(repair_run).resolve(strict=True)
    new_plan_path = checked_file(repair_run / "plan.json", parent=FEATURE_PARENT)
    new_plan = read(new_plan_path)
    continuation_path = checked_file(repair_run / "continuation_plan.json", parent=FEATURE_PARENT)
    continuation = read(continuation_path)
    output = Path(repair_output).resolve()
    if output.parent != FEATURE_PARENT.resolve(strict=True) or output.is_symlink():
        raise ValueError("v3-new-repair-output-outside-feature-parent")
    # The continuation plan must explicitly refer back to the frozen scope.
    if (continuation.get("schema") != "last3f-network-resume-plan-v3"
            or continuation.get("original_plan_sha256") != ORIGINAL_REPAIR_SHA
            or continuation.get("new_plan_sha256") != sha(new_plan_path)
            or continuation.get("old_completion_sha256") != sha(old_receipt)):
        raise ValueError("v3-repair-continuation-does-not-bind-original-plan")
    if set(continuation.get("original_artifacts", {})) != {"plan", "targets", "events", "completion", "source", "journal"}:
        raise ValueError("v3-original-artifact-preservation-set-required")
    for item in continuation["original_artifacts"].values():
        checked_file(item["path"], parent=FEATURE_PARENT, expected=item["sha256"])
    for relative, expected in original["code_sha256"].items():
        checked_file(ROOT / relative, expected=expected)
    code = {**original["code_sha256"], "scripts/run_repaired_context_research_v3.py": sha(__file__)}
    for relative, expected in {**new_plan.get("code_sha256", {}), **continuation.get("code_sha256", {})}.items():
        path = Path(relative)
        path = path if path.is_absolute() else ROOT / path
        path = checked_file(path, expected=expected)
        code[path.relative_to(ROOT).as_posix()] = expected
    entries = list(writer_entrypoints)
    for entry in entries:
        if Path(entry).as_posix() not in code or basename(entry) not in WRITER_NAMES:
            raise ValueError("v3-writer-entrypoint-not-explicitly-pinned")
    processes = repair_references(new_plan_path.parent, entries)
    if not processes and not (output / "completion_receipt.json").exists():
        raise RuntimeError("v3-no-approved-repair-process-or-completion-receipt")
    plan = deepcopy(original)
    plan.update(schema=SCHEMA, registered_at_utc=now(), operation="versioned_repair_receipt_continuation_then_same_fixed_study",
        repair_output=str(output), repair_processes=processes, code_sha256=code,
        runner_path="scripts/run_repaired_context_research_v3.py", runner_sha256=sha(__file__),
        original_study_sha256=ORIGINAL_STUDY_SHA, original_study=str(ORIGINAL_STUDY),
        repair_writer_entrypoints=entries,
        repair_resume={"original_repair_plan": reference(ORIGINAL_REPAIR / "plan.json"),
            "old_repair_completion": reference(old_receipt), "new_repair_plan": reference(new_plan_path),
            "continuation_plan": reference(continuation_path)})
    assert_fixed_plan(plan, original)
    guard(plan)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    directory.mkdir(exist_ok=False)
    exclusive(directory / "registered_plan.json", plan)
    exclusive(directory / "registration_receipt.json", {"registered_plan_sha256": sha(directory / "registered_plan.json"),
        "original_registered_plan_sha256": ORIGINAL_STUDY_SHA, "recorded_at_utc": now()})
    configured_engine(plan).status(directory, "waiting_for_verified_repair", new_training_started=False,
        jobs=55, completed=0, repair_output=str(output))
    return plan


def validate_completion(receipt, plan, directory):
    """Independently bind the exhaustive combined audit, not CLI exit alone.

    An interrupted V2 pass is historical evidence only, never renamed PASS.
    V3 must account for every original target and prove every retained empty
    clock came from a verified, unmasked source; HTTP failures cannot pass.
    """
    required = ("safety_verification_pass", "source_integrity_pass", "journal_audit_pass",
        "protected_state_unchanged", "verification_source_epoch_stable", "wrapper_and_verification_code_unchanged")
    if not isinstance(receipt, dict) or any(receipt.get(name) is not True for name in required):
        raise ValueError("v3-repair-safety-or-independent-audit-not-pass")
    resume = plan["repair_resume"]
    expected = {"original_plan_sha256": ORIGINAL_REPAIR_SHA,
        "old_completion_sha256": resume["old_repair_completion"]["sha256"],
        "new_plan_sha256": resume["new_repair_plan"]["sha256"],
        "continuation_plan_sha256": resume["continuation_plan"]["sha256"]}
    if receipt.get("schema") != "last3f-network-resume-completion-v3" or any(receipt.get(key) != value for key, value in expected.items()):
        raise ValueError("v3-completion-continuation-binding-invalid")
    child = receipt.get("child", {})
    if (child.get("confirmed_exited") is not True or type(child.get("exit_code")) is not int
            or child["exit_code"] != 0 or type(receipt.get("automatic_retries")) is not int
            or receipt["automatic_retries"] != 0):
        raise ValueError("v3-repair-child-or-no-retry-binding-invalid")
    if receipt.get("execution_outcome") not in {"completed", "completed_with_domain_missing", "partial_pending_sources"}:
        raise ValueError("v3-repair-not-exhausted-or-interrupted")
    old = bound_json(resume["old_repair_completion"], parent=FEATURE_PARENT)
    if (old.get("schema") != "last3f-single-network-run-wrapper-v1" or old.get("execution_outcome") != "stopped"
            or old.get("plan_sha256") != ORIGINAL_REPAIR_SHA or receipt.get("old_status_preserved") != "stopped"
            or any(old.get(name) is not True for name in required)):
        raise ValueError("v3-old-stopped-receipt-not-preserved-or-not-audited")
    artifacts = {}
    for name in ("source_integrity_receipt", "journal_audit", "combined_coverage_audit"):
        path = checked_file(receipt.get(name, ""), parent=FEATURE_PARENT, expected=receipt.get(name + "_sha256"))
        if not path.is_relative_to(Path(directory).resolve(strict=True)):
            raise ValueError("v3-audit-outside-new-completion-directory:" + name)
        artifacts[name] = read(path)
    source, journal, coverage = (artifacts[key] for key in ("source_integrity_receipt", "journal_audit", "combined_coverage_audit"))
    if (source.get("schema") != "last3f-artifact-independent-verification-v1"
            or source.get("artifact_integrity_pass") is not True or source.get("issues") != {}):
        raise ValueError("v3-source-artifact-independent-check-not-pass")
    if journal.get("schema") != "last3f-combined-two-batch-journal-audit-v3" or journal.get("audit_pass") is not True:
        raise ValueError("v3-combined-journal-check-not-pass")
    if (coverage.get("schema") != "last3f-combined-target-coverage-audit-v3"
            or coverage.get("coverage_pass") is not True or coverage.get("all_original_targets_accounted") is not True
            or type(coverage.get("original_target_races")) is not int or coverage["original_target_races"] != 24819
            or type(coverage.get("pending")) is not int or coverage["pending"] != 0
            or type(coverage.get("http400")) is not int or coverage["http400"] != 0):
        raise ValueError("v3-combined-coverage-has-pending-or-http400")
    for key in ("original_plan_sha256", "old_completion_sha256", "new_plan_sha256"):
        if coverage.get(key) != expected[key]:
            raise ValueError("v3-combined-coverage-plan-binding-invalid:" + key)
    original = bound_json(resume["original_repair_plan"], parent=FEATURE_PARENT)
    targets_path = checked_file(ORIGINAL_REPAIR / "targets.jsonl", expected=original.get("targets_sha256"))
    targets = {}
    with targets_path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row["race_id"] in targets:
                raise ValueError("v3-original-target-duplicate")
            targets[row["race_id"]] = row["race_date"]
    if len(targets) != 24819 or original.get("counts", {}).get("target_races") != len(targets):
        raise ValueError("v3-original-target-scope-count-invalid")
    records = coverage.get("records")
    if not isinstance(records, list) or len(records) != len(targets):
        raise ValueError("v3-combined-target-records-count-invalid")
    allowed_roots = {ORIGINAL_REPAIR.resolve(strict=True), Path(resume["new_repair_plan"]["path"]).parent.resolve(strict=True)}
    root_names = {str(path): path for path in allowed_roots}
    seen, counts, remainders = set(), Counter(), []
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("v3-combined-target-record-invalid")
        rid, state = row.get("race_id"), row.get("status")
        if rid in seen or targets.get(rid) != row.get("race_date") or rid not in targets:
            raise ValueError("v3-combined-target-identity-date-or-duplicate")
        seen.add(rid)
        if state not in {"completed", "domain_missing", "partial"}:
            raise ValueError("v3-combined-target-has-unresolved-state:" + str(state))
        counts[state] += 1
        source_root = root_names.get(row.get("source_run"))
        if source_root is None or not HEX.fullmatch(str(row.get("event_sha256", ""))):
            raise ValueError("v3-combined-target-source-or-event-binding-invalid")
        gaps = row.get("remaining_gap_runners")
        if type(gaps) is not int or gaps < 0 or (state == "completed" and gaps != 0) or (state != "completed" and gaps == 0):
            raise ValueError("v3-combined-gap-count-invalid")
        if state == "completed":
            continue
        states = row.get("remaining_source_states")
        if (not isinstance(states, dict) or not states or any(key not in {"missing", "invalid"}
                or type(value) is not int or value < 0 for key, value in states.items()) or sum(states.values()) != gaps
                or row.get("identity_grade") not in IDENTITY_GRADES):
            raise ValueError("v3-retained-clock-is-not-verified-source-missing")
        if state == "domain_missing" and (set(states) != {"missing"}
                or row.get("source_missing_is_not_numeric_recovery") is not True
                or row.get("remaining_are_legitimate_nonfinishers") is not True):
            raise ValueError("v3-domain-missing-not-proven-legitimate-nonfinishers")
        proof = checked_file(row.get("proof_path", ""), parent=source_root, expected=row.get("proof_sha256"))
        declared = source.get("file_receipts", {}).get(str(proof), {})
        if declared.get("sha256") != row["proof_sha256"]:
            raise ValueError("v3-retained-proof-not-in-independent-source-audit")
        content = read(proof)
        evidence = content.get("evidence", {})
        if (content.get("schema") != "last3f-repair-source-proof-v1" or evidence.get("race_id") != rid
                or evidence.get("source", {}).get("identity_grade") != row["identity_grade"]):
            raise ValueError("v3-retained-proof-not-validated-result")
        proof_source = evidence["source"]
        if row["identity_grade"] in {"cached_transport_url_plus_visible_date_and_all_runners", "mobile_cached_transport_url_plus_visible_date_and_all_runners"}:
            if not isinstance(proof_source.get("cached_transport_receipt"), dict) or proof_source["cached_transport_receipt"].get("raw_bytes_digest_verified") is not True:
                raise ValueError("v3-retained-cached-source-has-no-original-byte-receipt")
        if row["identity_grade"] == "mobile_network_transport_url_plus_visible_date_and_all_runners":
            transport = proof_source.get("network_transport_receipt", {})
            if (transport.get("schema") != "last3f-direct-http-receipt-v3" or transport.get("raw_bytes_digest_verified") is not True
                    or transport.get("response_status") != 200 or transport.get("body_complete") is not True
                    or transport.get("body_sha256") != proof_source.get("raw_body_sha256")):
                raise ValueError("v3-retained-direct-source-has-no-original-byte-receipt")
        remainders.append({"race_id": rid, "status": state, "remaining_gap_runners": gaps,
            "remaining_source_states": states, "proof_path": str(proof), "proof_sha256": row["proof_sha256"],
            "missing_values_retained": True})
    declared_counts = coverage.get("race_status_counts")
    if (seen != set(targets) or not isinstance(declared_counts, dict)
            or any(type(n) is not int or n < 0 or name not in {"completed", "domain_missing", "partial"}
                   for name, n in declared_counts.items()) or Counter({name: n for name, n in declared_counts.items() if n}) != counts
            or type(coverage.get("remaining_nonterminal_races")) is not int
            or coverage["remaining_nonterminal_races"] != counts.get("partial", 0)):
        raise ValueError("v3-combined-status-counts-not-exhaustive")
    if ((receipt["execution_outcome"] == "partial_pending_sources") != bool(counts.get("partial", 0))
            or receipt["execution_outcome"] == "completed" and bool(counts.get("domain_missing", 0))):
        raise ValueError("v3-source-missing-partial-must-not-be-renamed-completed")
    return {"all_targets_attempted_or_completed": True, "original_target_races": len(targets),
        "race_status_counts": dict(counts), "all_clocks_complete": not remainders,
        "unavailable_clock_remainders": remainders, "old_status_preserved": receipt.get("old_status_preserved"),
        "partial_outcome_not_renamed_completed": bool(counts.get("partial", 0)),
        "source_sha256": receipt["source_integrity_receipt_sha256"], "journal_sha256": receipt["journal_audit_sha256"],
        "combined_coverage_sha256": receipt["combined_coverage_audit_sha256"]}


def wait_for_repair(directory, plan):
    completion = Path(plan["repair_output"]) / "completion_receipt.json"
    deadline = time.monotonic() + plan["max_wait_seconds"]
    while not completion.exists():
        if (directory / "stop-request.json").exists():
            raise RuntimeError("v3-research-stop-requested-before-data-freeze")
        if time.monotonic() > deadline:
            raise TimeoutError("v3-repair-verification-not-completed-within-one-day-no-auto-retry")
        guard(plan)
        if not any(process_alive(item) for item in plan["repair_processes"]):
            raise RuntimeError("v3-repair-exited-without-completion-receipt-no-retry")
        time.sleep(30)
    receipt = read(completion)
    validation = validate_completion(receipt, plan, completion.parent)
    exit_deadline = time.monotonic() + 90
    while any(process_alive(item) for item in plan["repair_processes"]):
        if time.monotonic() > exit_deadline:
            raise RuntimeError("v3-repair-wrapper-still-alive-after-completion-receipt")
        time.sleep(1)
    if not no_acquisition_writer_alive(plan):
        raise RuntimeError("v3-acquisition-writer-still-alive-no-snapshot")
    guard(plan)
    exclusive(directory / "repair_gate_receipt.json", {"schema": SCHEMA, "passed": True,
        "recorded_at_utc": now(), "receipt_path": str(completion), "completion_sha256": sha(completion),
        "original_study_sha256": ORIGINAL_STUDY_SHA, **validation,
        "remaining_missing_are_not_invented": True, "domain_missing_is_separately_audited": True})
    return receipt


def execute(directory):
    directory = safe_directory(directory)
    plan = read(directory / "registered_plan.json")
    assert_fixed_plan(plan, original_plan())
    return configured_engine(plan).execute(directory)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--register", action="store_true")
    choice.add_argument("--run", action="store_true")
    parser.add_argument("--repair-run", type=Path)
    parser.add_argument("--repair-output", type=Path)
    parser.add_argument("--repair-writer-entrypoint", action="append", default=[])
    args = parser.parse_args(argv)
    if args.register:
        if args.repair_run is None or args.repair_output is None:
            parser.error("--register requires explicit --repair-run and --repair-output")
        register(args.run_dir, repair_run=args.repair_run, repair_output=args.repair_output,
            writer_entrypoints=args.repair_writer_entrypoint)
    else:
        if args.repair_run is not None or args.repair_output is not None or args.repair_writer_entrypoint:
            parser.error("--run uses only sealed registration; runtime repair overrides are forbidden")
        execute(args.run_dir)


if __name__ == "__main__":
    main()
