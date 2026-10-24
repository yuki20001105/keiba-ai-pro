"""One finite frozen-CLI network run, followed by independent read-only checks.

This is not a scheduler or an automatic retry loop. The existing V2 CLI owns
all provider pacing, stop requests, source validation and acquisition writes.
The wrapper writes only its new receipts/logs; it never changes the plan,
collector, active model, H04, source parser or any automation registration.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "scripts")]

import audit_feature_gap_updates as field_audit
from evaluate_db_shared_venues import preservation_receipt
from services.prediction_source_revision import _validated_state

PARENT = ROOT / "reports/generated/data-quality/feature-gap"
CLI = "scripts/repair_last3f_gaps_v2.py"
VERIFIER = "scripts/verify_last3f_gap_artifacts.py"
SCHEMA = "last3f-single-network-run-wrapper-v1"


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def read(path):
    with Path(path).open(encoding="utf-8-sig") as stream:
        return json.load(stream)


def _protect(receipt):
    return {key: value for key, value in receipt.items() if key != "forward_processes"}


def ro(path):
    db = sqlite3.connect(Path(path).resolve(strict=True).as_uri() + "?mode=ro", uri=True, timeout=3, isolation_level=None)
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA cache_size=-4096")
    return db


def epoch(path):
    with closing(ro(path)) as db:
        value = _validated_state(db)
        return list(value) if value is not None else None


def snapshot(path, protection_fn):
    return {"recorded_at_utc": now(), "protected": protection_fn(), "source_epoch": epoch(path)}


def safe_snapshot(path, protection_fn):
    value = {"recorded_at_utc": now(), "protected": None, "source_epoch": None}
    try:
        value["protected"] = protection_fn()
    except Exception as exc:
        value["protection_error"] = type(exc).__name__ + ": " + str(exc)[:300]
    try:
        value["source_epoch"] = epoch(path)
    except Exception as exc:
        value["epoch_error"] = type(exc).__name__ + ": " + str(exc)[:300]
    return value


def batch_counts(path, batch):
    with closing(ro(path)) as db:
        row = db.execute("SELECT COUNT(*),COUNT(DISTINCT row_key) FROM data_quality_repair_journal_v1 WHERE batch_id=? AND table_name='race_results_ultimate'", (batch,)).fetchone()
        return {"journal_entries": row[0], "unique_repaired_rows": row[1]}


def _new_child_directory(root, path):
    path = Path(path)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("wrapper-output-link-forbidden")
    resolved = path.resolve()
    if (resolved.parent != Path(root).resolve(strict=True) or resolved.exists()
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", resolved.name)):
        raise ValueError("wrapper-new-feature-gap-directory-required")
    return resolved


def run_process(command, *, cwd, stdout_path, stderr_path):
    """Wait for this exact child once; never restart or kill another process."""
    start = now()
    with Path(stdout_path).open("xb") as stdout, Path(stderr_path).open("xb") as stderr:
        process = subprocess.Popen(command, cwd=str(cwd), stdin=subprocess.DEVNULL,
            stdout=stdout, stderr=stderr, shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            write_new(Path(stdout_path).parent / (Path(stdout_path).name + ".process-start.json"),
                {"pid": process.pid, "started_at_utc": start, "command": command})
            code = process.wait()
        except BaseException:
            # Do not run a read-only verifier while this acquisition child is
            # still alive. The operator can use its existing stop-request UI.
            try:
                write_new(Path(stdout_path).parent / ("child-still-running-" + str(process.pid) + ".json"),
                    {"pid": process.pid, "started_at_utc": start, "observed_at_utc": now(),
                     "poll": process.poll(), "verifier_started": False,
                     "action": "Use the existing CLI stop request; no orphan process was killed."})
            except OSError:
                pass  # Preserve the original failure, including disk exhaustion.
            raise
    return {"pid": process.pid, "exit_code": code, "started_at_utc": start, "exited_at_utc": now(),
            "confirmed_exited": process.poll() is not None,
            "stdout_path": str(stdout_path), "stderr_path": str(stderr_path)}


def journal_audit(db_path, batch_id, *, source_receipt=None, run_dir=None, yield_every=512, pause_seconds=.003):
    """Stream one batch, not a baseline-to-current all-source preservation audit."""
    identity_before = Path(db_path).stat()
    source_before = epoch(db_path)
    counts, issues, examples, tails = Counter(), Counter(), [], {}
    source_records = (source_receipt or {}).get("file_receipts", {})
    def issue(code, identifier):
        issues[code] += 1
        if len(examples) < 30:
            examples.append({"issue": code, "journal_id_or_row": identifier})
    with closing(ro(db_path)) as db:
        db.execute("BEGIN")
        version_before = db.execute("PRAGMA data_version").fetchone()[0]
        transaction_before = _validated_state(db)
        transaction_before = list(transaction_before) if transaction_before is not None else None
        query = ("SELECT id,table_name,row_key,race_id,before_data,after_data,before_sha256,after_sha256,evidence_json "
                 "FROM data_quality_repair_journal_v1 WHERE batch_id=? ORDER BY id")
        for jid, table, key, rid, before, after, bh, ah, raw_evidence in db.execute(query, (batch_id,)):
            counts["journal_entries"] += 1
            if (not isinstance(before, str) or hashlib.sha256(before.encode()).hexdigest() != bh):
                issue("before_hash_mismatch", jid)
            if not isinstance(after, str) or hashlib.sha256(after.encode()).hexdigest() != ah:
                issue("after_hash_mismatch", jid)
            try:
                evidence = json.loads(raw_evidence)
                entry = {"id": jid, "kind": "last3f", "batch_id": batch_id, "table": table,
                         "race_id": rid, "before": before, "after": after, "evidence": evidence}
                failures = field_audit.validate_feature_entry(entry)
                for failure in failures:
                    issue(failure, jid)
                old, new = json.loads(before), json.loads(after)
                for name in field_audit.changed_keys(old, new) & field_audit.CLOCK_FIELDS:
                    counts["filled_alias_slots_" + name] += 1
                source = evidence.get("source", {})
                raw_digest = source.get("raw_body_sha256")
                if source_receipt is not None:
                    expected_path = str((Path(run_dir) / "sources/raw" / (str(raw_digest) + ".bin")).resolve())
                    if source_records.get(expected_path, {}).get("sha256") != raw_digest:
                        issue("journal_source_not_in_independently_verified_raw_files", jid)
            except (ValueError, TypeError, AttributeError, KeyError):
                issue("journal_feature_evidence_invalid", jid)
            if table != "race_results_ultimate" or not str(key).isdigit() or int(key) < 1:
                issue("journal_table_or_key_invalid", jid)
            else:
                row_id = int(key)
                if row_id in tails and tails[row_id]["after_sha256"] != bh:
                    issue("within_batch_chain_not_contiguous_external_intermediate_possible", jid)
                tails[row_id] = {"race_id": rid, "after_sha256": ah}
            if counts["journal_entries"] % yield_every == 0:
                time.sleep(pause_seconds)
        keys = list(tails)
        for offset in range(0, len(keys), 100):
            selected = keys[offset:offset + 100]
            marks = ",".join("?" for _ in selected)
            current = {row[0]: row[1:] for row in db.execute(
                f"SELECT id,race_id,data FROM race_results_ultimate WHERE id IN ({marks})", selected)}
            for key in selected:
                row = current.get(key)
                if (row is None or row[0] != tails[key]["race_id"]
                        or hashlib.sha256(row[1].encode()).hexdigest() != tails[key]["after_sha256"]):
                    issue("journal_tail_differs_from_current_row_external_change_possible", key)
                else:
                    counts["current_tails_matched"] += 1
            time.sleep(pause_seconds)
        transaction_after = _validated_state(db)
        transaction_after = list(transaction_after) if transaction_after is not None else None
        db.rollback()
        version_after = db.execute("PRAGMA data_version").fetchone()[0]
    source_after = epoch(db_path)
    identity_after = Path(db_path).stat()
    file_stable = ((identity_before.st_dev, identity_before.st_ino)
                   == (identity_after.st_dev, identity_after.st_ino))
    stable = (source_before is not None and source_before == transaction_before == transaction_after == source_after
              and version_before == version_after and file_stable)
    counts["unique_repaired_rows"] = len(tails)
    return {"schema": "last3f-one-batch-journal-audit-v1", "batch_id": batch_id, "counts": dict(counts),
        "issues": dict(issues), "issues_examples": examples, "journal_and_current_tail_pass": not issues,
        "stable_and_latest": stable, "audit_pass": not issues and stable,
        "source_epoch": {"outside_before": source_before, "transaction_before": transaction_before,
            "transaction_after": transaction_after, "outside_after": source_after,
            "data_version_before": version_before, "data_version_after": version_after,
            "database_file_identity_stable": file_stable},
        "row_counts_authority": "DB repair journal, NOT CLI event totals",
        "scope": "This named batch only; other approved result-refresh batches and non-JSON acquisition timestamps are not baseline-audited here.",
        "not_full_source_preservation_audit": True, "read_only": True, "physical_database_copy_or_hash": False}


def execute_once(run_dir, output_dir, *, limit, python_executable=None, source_output=None,
                 repository_root=ROOT, runner=run_process, protection_fn=preservation_receipt):
    root = Path(repository_root).resolve(strict=True)
    parent = root / "reports/generated/data-quality/feature-gap"
    run = Path(run_dir).resolve(strict=True)
    if run.parent != parent.resolve(strict=True) or not run.is_dir():
        raise ValueError("wrapper-fixed-feature-gap-run-required")
    if type(limit) is not int or limit < 1:
        raise ValueError("wrapper-explicit-positive-limit-required")
    output = _new_child_directory(parent, output_dir)
    source_output = _new_child_directory(parent, source_output or output.with_name(output.name + "-sources"))
    if output == source_output:
        raise ValueError("wrapper-distinct-source-output-required")
    plan = read(run / "plan.json")
    plan_sha = sha(run / "plan.json")
    receipt = read(run / "plan_receipt.json")
    if (plan.get("schema") != "last3f-gap-repair-plan-v2" or plan.get("batch_id") != run.name
            or receipt.get("plan_sha256") != plan_sha or receipt.get("batch_id") != run.name
            or plan.get("targets_sha256") != sha(run / "targets.jsonl")
            or Path(plan["database"]).resolve(strict=True) != (root / "keiba/data/keiba_ultimate.db").resolve(strict=True)):
        raise ValueError("wrapper-frozen-plan-or-database-binding-invalid")
    cli = root / CLI
    if plan.get("code_sha256", {}).get(CLI) != sha(cli):
        raise ValueError("wrapper-frozen-cli-code-binding-invalid")
    python = Path(python_executable or root / "python-api/.venv/Scripts/python.exe").resolve(strict=True)
    db_path = Path(plan["database"])
    output.mkdir(exist_ok=False)
    result = {"schema": SCHEMA, "started_at_utc": now(), "batch_id": plan["batch_id"],
        "run_dir": str(run), "output_dir": str(output), "source_output_dir": str(source_output),
        "limit": limit, "plan_sha256": plan_sha, "network_cli_invocations": 0,
        "automatic_retries": 0, "scheduler_or_automation_created": False, "full_source_preservation_claimed": False}
    child, source = None, None
    try:
        pinned_code = {str(path): sha(path) for path in (Path(__file__).resolve(), root / CLI, root / VERIFIER,
            Path(field_audit.__file__).resolve())}
        result["wrapper_and_verification_code_sha256_before"] = pinned_code
        pre = safe_snapshot(db_path, protection_fn)
        result["before"] = pre
        if (not isinstance(pre["protected"], dict) or _protect(pre["protected"]) != _protect(plan["protected_before"])
                or pre["source_epoch"] is None):
            raise ValueError("wrapper-protected-state-or-source-revision-invalid")
        result["journal_counts_before_child"] = batch_counts(db_path, plan["batch_id"])
        command = [str(python), "-X", "utf8", str(cli), "--run-dir", str(run),
                   "--apply-network", "--allow-network", "--limit", str(limit)]
        result["child_command"] = command
        write_new(output / "start_receipt.json", {**result, "progress_authority": str(run / "status.json")})
        result["network_cli_invocations"] = 1
        child = runner(command, cwd=root, stdout_path=output / "network-child.stdout.log", stderr_path=output / "network-child.stderr.log")
        result["child"] = child
        write_new(output / "child_exit_receipt.json", child)
        if child.get("confirmed_exited") is not True or type(child.get("exit_code")) is not int:
            raise ValueError("wrapper-child-exit-not-confirmed-no-verifier")
        result["after_child"] = safe_snapshot(db_path, protection_fn)
        try:
            result["child_final_status"] = read(run / "status.json")
        except (OSError, ValueError):
            result["child_final_status"] = {"status": "unreadable"}
        source_command = [str(python), "-X", "utf8", str(root / VERIFIER), "--run-dir", str(run), "--output-dir", str(source_output)]
        result["source_verifier_command"] = source_command
        source = runner(source_command, cwd=root, stdout_path=output / "source-verifier.stdout.log", stderr_path=output / "source-verifier.stderr.log")
        result["source_verifier"] = source
        if source.get("confirmed_exited") is not True:
            raise ValueError("wrapper-source-verifier-exit-not-confirmed")
        source_path = source_output / "source_integrity_receipt.json"
        source_receipt = read(source_path) if source_path.exists() else None
        result["source_integrity_receipt_sha256"] = sha(source_path) if source_path.exists() else None
        result["source_integrity_pass"] = bool(source.get("exit_code") == 0 and source_receipt and source_receipt.get("artifact_integrity_pass") is True)
        audited = journal_audit(db_path, plan["batch_id"], source_receipt=source_receipt, run_dir=run)
        write_new(output / "journal_audit.json", audited)
        result["journal_audit_sha256"] = sha(output / "journal_audit.json")
        result["journal_counts"] = audited["counts"]
        result["journal_audit_pass"] = audited["audit_pass"]
        result["after_verification"] = safe_snapshot(db_path, protection_fn)
        result["protected_state_unchanged"] = (all(isinstance(item["protected"], dict) for item in
            (pre, result["after_child"], result["after_verification"])) and _protect(pre["protected"])
            == _protect(result["after_child"]["protected"]) == _protect(result["after_verification"]["protected"]))
        result["verification_source_epoch_stable"] = (result["after_child"]["source_epoch"] == result["after_verification"]["source_epoch"])
        result["wrapper_and_verification_code_unchanged"] = all(sha(path) == expected for path, expected in pinned_code.items())
        result["new_unique_repaired_rows_this_child"] = audited["counts"]["unique_repaired_rows"] - result["journal_counts_before_child"]["unique_repaired_rows"]
        result["new_journal_entries_this_child"] = audited["counts"].get("journal_entries", 0) - result["journal_counts_before_child"]["journal_entries"]
        result["safety_verification_pass"] = (result["source_integrity_pass"] and audited["audit_pass"]
            and result["protected_state_unchanged"] and result["verification_source_epoch_stable"]
            and result["wrapper_and_verification_code_unchanged"])
    except BaseException as exc:
        result.update(safety_verification_pass=False, failure=type(exc).__name__ + ": " + str(exc)[:600],
            verifier_not_started_before_confirmed_child_exit=source is None)
    result["finished_at_utc"] = now()
    state = result.get("child_final_status", {}).get("status", "not_confirmed")
    result["execution_outcome"] = state
    result["data_repair_counts_authority"] = "journal_audit.json; events are progress only"
    result["log_sha256"] = {path.name: sha(path) for path in output.glob("*.log") if path.is_file()}
    result["owned_receipts_sha256"] = {path.name: sha(path) for path in output.glob("*.json") if path.is_file()}
    result["wrapper_exit_code"] = (0 if result.get("safety_verification_pass") and child and child.get("exit_code") == 0
        and state in {"completed", "completed_with_domain_missing", "completed_selected_batch", "partial_pending_sources"}
        else 2 if result.get("safety_verification_pass") and child and child.get("exit_code") == 0 else 1)
    write_new(output / "completion_receipt.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-output-dir", type=Path)
    parser.add_argument("--limit", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 10)
    except (ImportError, OSError, AttributeError):
        pass
    result = execute_once(args.run_dir, args.output_dir, limit=args.limit, source_output=args.source_output_dir)
    print(json.dumps({"completion_receipt": str(Path(args.output_dir) / "completion_receipt.json"),
        "execution_outcome": result["execution_outcome"], "safety_verification_pass": result.get("safety_verification_pass"),
        "journal_counts": result.get("journal_counts"), "exit_code": result["wrapper_exit_code"]}, ensure_ascii=False))
    return result["wrapper_exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
