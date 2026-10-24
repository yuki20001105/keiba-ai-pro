"""Independent, read-only two-batch audit for the explicitly resumed 3F repair.

The interrupted original batch remains interrupted.  Its verified repairs are
combined with a separately sealed continuation, never relabelled successful.
Only new, exclusive receipt files are written; source/collector/model files and
the live SQLite database are not modified.  This is a named-journal audit, not
a claim that every unrelated database row was compared to a baseline snapshot.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "scripts")]

import verify_last3f_gap_artifacts as source_checker
from last3f_evidence_v3 import BASE_PARSER_SHA256, _private_url_race_id
from services.prediction_source_revision import _validated_state

SCHEMA = "last3f-resume-independent-audit-v3"
JOURNAL_SCHEMA = "last3f-combined-two-batch-journal-audit-v3"
COVERAGE_SCHEMA = "last3f-combined-target-coverage-audit-v3"
ORIGINAL_PLAN_SHA256 = "71b36328587ceff9a80272994c1fff479661a51a94b77e9c3811477cb1898675"
ORIGINAL_TARGET_RACES = 24819
HEX = re.compile(r"[a-f0-9]{64}")
GRADES = frozenset({"canonical_url_plus_visible_date_and_all_runners",
    "cached_transport_url_plus_visible_date_and_all_runners",
    "mobile_cached_transport_url_plus_visible_date_and_all_runners",
    "mobile_network_transport_url_plus_visible_date_and_all_runners"})
RECEIPT_NAMES = ("source_integrity_receipt.json", "journal_audit.json",
                 "combined_coverage_audit.json", "audit_receipt.json")


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def sha(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def shared_bytes(path):
    """Close a READ|WRITE|DELETE-sharing Windows handle immediately after read."""
    if os.name != "nt":
        return Path(path).read_bytes()
    import ctypes
    import msvcrt
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    handle = kernel.CreateFileW(str(Path(path).resolve(strict=True)), 0x80000000, 7,
                                None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle(handle)
        raise
    with os.fdopen(descriptor, "rb") as stream:
        return stream.read()


def read(path):
    value = json.loads(shared_bytes(path).decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("audit-json-object-required")
    return value


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def existing(path):
    path = Path(path).absolute()
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("audit-linked-path-forbidden")
    return path.resolve(strict=True)


def ro(path):
    connection = sqlite3.connect(existing(path).as_uri() + "?mode=ro", uri=True,
                                 timeout=5, isolation_level=None)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA cache_size=-4096")
    connection.execute("PRAGMA temp_store=MEMORY")
    return connection


def epoch(path):
    with closing(ro(path)) as connection:
        state = _validated_state(connection)
        return list(state) if state is not None else None


def private_field_auditor():
    """Extend only a private auditor's URL validator; no production monkeypatch."""
    spec = importlib.util.spec_from_file_location("_last3f_v3_private_field_audit",
                                                  ROOT / "scripts/audit_feature_gap_updates.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._url_race_id = _private_url_race_id
    return module


def load_targets(run):
    plan = read(run / "plan.json")
    if sha(run / "targets.jsonl") != plan.get("targets_sha256"):
        raise ValueError("audit-targets-sha-mismatch")
    targets = {}
    with (run / "targets.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            target = json.loads(line)
            rid = target["race_id"]
            if rid in targets or not re.fullmatch(r"\d{12}", str(rid)):
                raise ValueError("audit-duplicate-or-invalid-target")
            source_checker._identities(target["expected_runners"])
            if (target.get("runner_count") != len(target["expected_runners"])
                    or not re.fullmatch(r"\d{8}", str(target.get("race_date")))):
                raise ValueError("audit-invalid-target-date-or-runner-count")
            targets[rid] = target
    if len(targets) != plan.get("counts", {}).get("target_races"):
        raise ValueError("audit-target-count-mismatch")
    return plan, targets


def load_latest(run):
    latest = {}
    for line in shared_bytes(run / "events.jsonl").splitlines():
        event = json.loads(line)
        if event.get("race_id"):
            latest[event["race_id"]] = event
    return latest


def check_old_completion(original_run, original_output):
    receipt = read(original_output / "completion_receipt.json")
    flags = ("safety_verification_pass", "source_integrity_pass", "journal_audit_pass",
        "protected_state_unchanged", "verification_source_epoch_stable",
        "wrapper_and_verification_code_unchanged")
    if (receipt.get("schema") != "last3f-single-network-run-wrapper-v1"
            or receipt.get("batch_id") != original_run.name
            or receipt.get("plan_sha256") != sha(original_run / "plan.json")
            or receipt.get("execution_outcome") != "stopped"
            or receipt.get("child_final_status", {}).get("status") != "stopped"
            or receipt.get("child_final_status", {}).get("stop_reason") != "operator-stop-request"
            or any(receipt.get(key) is not True for key in flags)
            or receipt.get("child", {}).get("confirmed_exited") is not True
            or type(receipt.get("child", {}).get("exit_code")) is not int
            or receipt["child"]["exit_code"] != 0):
        raise ValueError("audit-original-stopped-receipt-not-verified")
    old_status = read(original_run / "status.json")
    if old_status != receipt["child_final_status"]:
        raise ValueError("audit-original-stopped-status-changed")
    sources = existing(Path(receipt.get("source_output_dir", "")))
    source_path, journal_path = sources / "source_integrity_receipt.json", original_output / "journal_audit.json"
    if (sha(source_path) != receipt.get("source_integrity_receipt_sha256")
            or read(source_path).get("artifact_integrity_pass") is not True
            or sha(journal_path) != receipt.get("journal_audit_sha256")
            or read(journal_path).get("audit_pass") is not True):
        raise ValueError("audit-original-independent-receipt-binding-invalid")
    return receipt


def checked_proof(run, event, verified_files):
    path = source_checker.inside(run, event["proof_file"])
    digest = event.get("proof_sha256")
    if (not HEX.fullmatch(str(digest or "")) or path.stem != digest
            or verified_files.get(str(path), {}).get("sha256") != digest):
        raise ValueError("audit-proof-not-independently-verified")
    proof = read(path)
    if proof.get("schema") != "last3f-repair-source-proof-v1":
        raise ValueError("audit-rejected-proof-cannot-complete-target")
    return path, proof


def _verified_source(evidence, target, run, verified_files):
    source = evidence.get("source", {})
    grade = source.get("identity_grade")
    if (evidence.get("race_id") != target["race_id"]
            or evidence.get("race_date") != target["race_date"]
            or evidence.get("runner_count") != target["runner_count"]
            or source_checker._identities(evidence["rows"]) != source_checker._identities(target["expected_runners"])
            or source.get("http_status") != 200 or type(source.get("http_status")) is not int
            or source.get("raw_digest_verification") != "verified_against_bytes"
            or source.get("historical_capture_time_proven") is not False
            or source.get("visible_race_date_verified") is not True
            or source.get("all_runner_identity_verified") is not True or grade not in GRADES
            or _private_url_race_id(source.get("source_url", "")) != target["race_id"]):
        raise ValueError("audit-proof-source-target-or-grade-invalid")
    raw_sha = source.get("raw_body_sha256")
    raw_path = str((run / "sources/raw" / (str(raw_sha) + ".bin")).resolve())
    if not HEX.fullmatch(str(raw_sha or "")) or verified_files.get(raw_path, {}).get("sha256") != raw_sha:
        raise ValueError("audit-proof-raw-bytes-not-independently-verified")
    cache = source.get("cached_transport_receipt")
    if grade in {"cached_transport_url_plus_visible_date_and_all_runners",
                 "mobile_cached_transport_url_plus_visible_date_and_all_runners"}:
        if (not isinstance(cache, dict) or cache.get("normalized_url") != source["source_url"]
                or type(cache.get("status")) is not int or cache.get("status") != 200
                or cache.get("body_sha256") != raw_sha or cache.get("raw_bytes_digest_verified") is not True
                or isinstance(cache.get("fetched_at"), bool) or not isinstance(cache.get("fetched_at"), (int, float))
                or not math.isfinite(cache["fetched_at"]) or cache["fetched_at"] <= 0):
            raise ValueError("audit-cached-source-receipt-invalid")
        stamp = datetime.fromisoformat(source["observed_at_utc"].replace("Z", "+00:00"))
        if stamp.tzinfo is None or abs(stamp.timestamp() - cache["fetched_at"]) > 1e-5:
            raise ValueError("audit-cached-source-observation-time-mismatch")
    if grade == "mobile_cached_transport_url_plus_visible_date_and_all_runners":
        if (evidence.get("schema") != "last3f-source-evidence-v3"
                or not source["source_url"].startswith("https://db.sp.netkeiba.com/race/")
                or source.get("url_policy_version") != "public-mobile-cache-exact-route-v3"
                or source.get("base_parser_sha256") != BASE_PARSER_SHA256):
            raise ValueError("audit-mobile-cache-version-or-source-invalid")
    elif grade == "mobile_network_transport_url_plus_visible_date_and_all_runners":
        receipt = source.get("network_transport_receipt")
        if (evidence.get("schema") != "last3f-source-evidence-v3"
                or not source["source_url"].startswith("https://db.sp.netkeiba.com/race/")
                or source.get("url_policy_version") != "public-mobile-direct-exact-route-v3"
                or source.get("base_parser_sha256") != BASE_PARSER_SHA256
                or cache is not None or not isinstance(receipt, dict)
                or receipt.get("schema") != "last3f-direct-http-receipt-v3"
                or receipt.get("request_url") != source["source_url"]
                or receipt.get("response_url") != source["source_url"]
                or type(receipt.get("response_status")) is not int or receipt["response_status"] != 200
                or receipt.get("body_sha256") != raw_sha or receipt.get("body_complete") is not True
                or type(receipt.get("attempts")) is not int or receipt["attempts"] != 1
                or receipt.get("source") != "network" or receipt.get("raw_bytes_digest_verified") is not True):
            raise ValueError("audit-mobile-direct-transport-invalid")
        observed = datetime.fromisoformat(source["observed_at_utc"].replace("Z", "+00:00"))
        received = datetime.fromisoformat(receipt["received_at_utc"].replace("Z", "+00:00"))
        if observed.tzinfo is None or received.tzinfo is None or observed != received:
            raise ValueError("audit-mobile-direct-observation-time-mismatch")
    elif grade == "canonical_url_plus_visible_date_and_all_runners":
        canonical_urls = source.get("canonical_urls")
        if (source.get("canonical_identity_verified") is not True or not isinstance(canonical_urls, list)
                or not canonical_urls or any(_private_url_race_id(url) != target["race_id"] for url in canonical_urls)):
            raise ValueError("audit-canonical-source-identity-not-proven")
    canonical_urls = source.get("canonical_urls", [])
    if (not isinstance(canonical_urls, list)
            or any(_private_url_race_id(url) != target["race_id"] for url in canonical_urls)
            or source.get("canonical_identity_verified") is not bool(canonical_urls)):
        raise ValueError("audit-source-canonical-flag-or-id-conflict")
    return source


def combined_coverage(original_run, resumed_run, original_targets, resumed_targets, source_receipt):
    old_latest, new_latest = load_latest(original_run), load_latest(resumed_run)
    if set(old_latest) != set(original_targets) or set(new_latest) != set(resumed_targets):
        raise ValueError("audit-latest-events-do-not-cover-exact-plan-targets")
    if set(resumed_targets) - set(original_targets):
        raise ValueError("audit-resumed-target-outside-original-plan")
    for rid, target in resumed_targets.items():
        if canonical(target) != canonical(original_targets[rid]):
            raise ValueError("audit-resumed-target-identity-or-metadata-changed:" + rid)
    records, counts, issues, examples = [], Counter(), Counter(), []
    files = source_receipt.get("file_receipts", {})
    for rid, target in original_targets.items():
        run = resumed_run if rid in resumed_targets else original_run
        event = new_latest[rid] if rid in resumed_targets else old_latest[rid]
        state = event.get("status")
        record = {"race_id": rid, "race_date": target["race_date"], "status": state,
            "source_run": str(run), "event_sha256": event.get("event_sha256"),
            "proof_path": None, "proof_sha256": None, "identity_grade": event.get("identity_grade"),
            "remaining_gap_runners": event.get("remaining_gap_runners", 0),
            "remaining_source_states": event.get("remaining_source_states", {}),
            "source_missing_is_not_numeric_recovery": event.get("source_missing_is_not_numeric_recovery"),
            "remaining_are_legitimate_nonfinishers": event.get("remaining_are_legitimate_nonfinishers")}
        counts[state] += 1
        try:
            if state not in {"completed", "domain_missing", "partial"}:
                raise ValueError("target-not-terminal-or-verified-unavailable")
            if (event.get("http_status") not in (None, 200) or event.get("error")
                    or event.get("source_verified") is False):
                raise ValueError("target-has-transport-or-source-error")
            reason = event.get("reason")
            no_gap_reason = "already-no-repairable-gaps"
            if reason not in (None, "", no_gap_reason if state == "completed" else None):
                raise ValueError("target-has-error-reason")
            if event.get("proof_file"):
                path, proof = checked_proof(run, event, files)
                source = _verified_source(proof["evidence"], target, run, files)
                if event.get("identity_grade") != source.get("identity_grade"):
                    raise ValueError("target-event-proof-grade-mismatch")
                record.update(proof_path=str(path), proof_sha256=event["proof_sha256"])
            elif state != "completed" or reason != no_gap_reason:
                raise ValueError("target-source-proof-required")
            gap = record["remaining_gap_runners"]
            remaining = record["remaining_source_states"]
            if type(gap) is not int or gap < 0:
                raise ValueError("target-invalid-gap-count")
            if state == "completed" and gap != 0:
                raise ValueError("target-completed-but-has-gaps")
            if state in {"domain_missing", "partial"}:
                if (gap < 1 or not isinstance(remaining, dict) or not remaining
                        or any(name not in {"missing", "invalid"} or type(n) is not int or n < 0
                               for name, n in remaining.items()) or sum(remaining.values()) != gap):
                    raise ValueError("target-unavailable-source-not-missing-or-invalid-only")
                if state == "domain_missing" and (set(remaining) != {"missing"}
                        or event.get("remaining_are_legitimate_nonfinishers") is not True
                        or event.get("source_missing_is_not_numeric_recovery") is not True):
                    raise ValueError("target-domain-missing-not-proven-nonfinishers")
                record["missing_values_retained"] = True
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            issues[str(error)] += 1
            if len(examples) < 40:
                examples.append({"race_id": rid, "issue": str(error)})
        records.append(record)
    accounted = len(records) == ORIGINAL_TARGET_RACES and len({x["race_id"] for x in records}) == ORIGINAL_TARGET_RACES
    return {"schema": COVERAGE_SCHEMA, "audited_at_utc": now(), "original_target_races": len(original_targets),
        "original_targets_sha256": sha(original_run / "targets.jsonl"),
        "resumed_targets_sha256": sha(resumed_run / "targets.jsonl"),
        "all_original_targets_accounted": accounted, "race_status_counts": dict(counts),
        "pending": counts.get("pending", 0), "http400": sum(row.get("http_status") == 400 for row in
            [new_latest[rid] if rid in resumed_targets else old_latest[rid] for rid in original_targets]),
        "remaining_nonterminal_races": counts.get("partial", 0),
        "remaining_gap_runners": sum(x["remaining_gap_runners"] for x in records if type(x["remaining_gap_runners"]) is int),
        "issues": dict(issues), "issues_examples": examples,
        "coverage_pass": accounted and not issues, "records": records,
        "partial_status_preserved": True, "old_stopped_status_not_relabelled_completed": True,
        "historical_prestart_capture_proven": False}


def proof_bindings(source_receipt, runs):
    """Compact source/row digest lookup, not full per-row JSON repair chains."""
    result = {}
    for filename, record in source_receipt.get("file_receipts", {}).items():
        path = Path(filename)
        if path.suffix != ".json" or path.parent.name != "evidence":
            continue
        run = next((run for run in runs if path.parent.parent == run), None)
        if run is None:
            continue
        proof = read(path)
        if proof.get("schema") != "last3f-repair-source-proof-v1":
            continue
        evidence = proof["evidence"]
        source = evidence["source"]
        signature = hashlib.sha256(canonical(source)).hexdigest()
        key = (run.name, evidence["race_id"], signature)
        rows = {}
        for runner in evidence["rows"]:
            identity = (runner["horse_number"], str(runner["horse_id"]), str(runner["horse_name"]))
            rows[identity] = (runner.get("last3f_state"), runner.get("last3f_seconds"))
        bound = {"race_date": evidence["race_date"], "raw_sha256": source["raw_body_sha256"], "rows": rows}
        if key in result and result[key] != bound:
            raise ValueError("audit-proof-source-signature-conflict")
        if record.get("sha256") != path.stem:
            raise ValueError("audit-proof-binding-filename-mismatch")
        result[key] = bound
    return result


def combined_journal_audit(db_path, runs, *, source_receipt, targets, yield_every=512, pause_seconds=.003):
    """Stream both batches by journal ID; retain final SHA only for row chains."""
    from scraping.last3f_gap_repair import identity
    field_audit = private_field_auditor()
    batch_runs = {run.name: run for run in runs}
    if len(batch_runs) != 2:
        raise ValueError("audit-two-distinct-batches-required")
    stat_before, outside_before = existing(db_path).stat(), epoch(db_path)
    counts, per_batch, issues, examples, tails = Counter(), Counter(), Counter(), [], {}
    bindings = proof_bindings(source_receipt, runs)
    files = source_receipt.get("file_receipts", {})

    def issue(code, key):
        issues[code] += 1
        if len(examples) < 40:
            examples.append({"issue": code, "journal_id_or_row": key})

    with closing(ro(db_path)) as db:
        db.execute("BEGIN")
        version_before = db.execute("PRAGMA data_version").fetchone()[0]
        state = _validated_state(db)
        transaction_before = list(state) if state is not None else None
        query = ("SELECT id,batch_id,table_name,row_key,race_id,before_data,after_data,before_sha256,after_sha256,evidence_json "
                 "FROM data_quality_repair_journal_v1 WHERE batch_id IN (?,?) ORDER BY id")
        for jid, batch, table, key, rid, before, after, bh, ah, raw_evidence in db.execute(query, tuple(batch_runs)):
            counts["journal_entries"] += 1
            per_batch[batch] += 1
            if not isinstance(before, str) or hashlib.sha256(before.encode()).hexdigest() != bh:
                issue("before_hash_mismatch", jid)
            if not isinstance(after, str) or hashlib.sha256(after.encode()).hexdigest() != ah:
                issue("after_hash_mismatch", jid)
            try:
                evidence = json.loads(raw_evidence)
                entry = {"id": jid, "kind": "last3f", "batch_id": batch, "table": table,
                    "race_id": rid, "before": before, "after": after, "evidence": evidence}
                for failure in field_audit.validate_feature_entry(entry):
                    issue(failure, jid)
                old, new = json.loads(before), json.loads(after)
                if rid not in targets or identity(new) not in {identity(row) for row in targets[rid]["expected_runners"]}:
                    issue("journal_runner_outside_original_target_identity", jid)
                source = evidence.get("source", {})
                raw_sha = source.get("raw_body_sha256")
                raw_path = str((batch_runs[batch] / "sources/raw" / (str(raw_sha) + ".bin")).resolve())
                if files.get(raw_path, {}).get("sha256") != raw_sha or not HEX.fullmatch(str(raw_sha or "")):
                    issue("journal_source_not_in_independently_verified_raw_files", jid)
                signature = hashlib.sha256(canonical(source)).hexdigest()
                proof = bindings.get((batch, rid, signature))
                if proof is None or proof["race_date"] != evidence.get("source_race_date"):
                    issue("journal_source_not_bound_to_exact_verified_proof", jid)
                else:
                    source_row = proof["rows"].get(identity(new))
                    if source_row is None or source_row[0] != "valid":
                        issue("journal_runner_not_numeric_in_verified_proof", jid)
                    for name in field_audit.changed_keys(old, new) & field_audit.CLOCK_FIELDS:
                        counts["filled_alias_slots_" + name] += 1
                        state, number = field_audit.classify_last3f(new[name])
                        if source_row is None or state != "valid" or number != source_row[1]:
                            issue("journal_clock_does_not_equal_verified_source", jid)
            except (ValueError, TypeError, AttributeError, KeyError, OverflowError):
                issue("journal_feature_evidence_invalid", jid)
            if table != "race_results_ultimate" or not str(key).isdigit() or int(key) < 1:
                issue("journal_table_or_key_invalid", jid)
            else:
                row_id = int(key)
                if row_id in tails and (tails[row_id]["after_sha256"] != bh or tails[row_id]["race_id"] != rid):
                    issue("global_cross_batch_journal_discontinuity", jid)
                tails[row_id] = {"race_id": rid, "after_sha256": ah}
            if yield_every and counts["journal_entries"] % yield_every == 0:
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
        state = _validated_state(db)
        transaction_after = list(state) if state is not None else None
        db.rollback()
        version_after = db.execute("PRAGMA data_version").fetchone()[0]
    outside_after, stat_after = epoch(db_path), existing(db_path).stat()
    identity_stable = (stat_before.st_dev, stat_before.st_ino) == (stat_after.st_dev, stat_after.st_ino)
    stable = (outside_before is not None and outside_before == transaction_before == transaction_after == outside_after
              and version_before == version_after and identity_stable)
    counts["unique_repaired_rows"] = len(tails)
    return {"schema": JOURNAL_SCHEMA, "audited_at_utc": now(), "selected_batches": list(batch_runs),
        "entries_by_batch": {batch: per_batch[batch] for batch in batch_runs}, "counts": dict(counts),
        "issues": dict(issues), "issues_examples": examples, "journal_and_current_tail_pass": not issues,
        "stable_and_latest": stable, "audit_pass": not issues and stable,
        "source_epoch": {"outside_before": outside_before, "transaction_before": transaction_before,
            "transaction_after": transaction_after, "outside_after": outside_after,
            "data_version_before": version_before, "data_version_after": version_after,
            "database_file_identity_stable": identity_stable},
        "chain_storage": "final digest and race identity per row; no full JSON chain retained",
        "row_counts_authority": "ordered two-batch DB repair journal, not event progress totals",
        "source_proof_bytes_independently_verified": True, "parser_replay_performed": False,
        "not_full_source_preservation_audit": True, "read_only": True, "network_requests": 0,
        "physical_database_copy_or_hash": False}


def verified_files_still_stable(receipt):
    for filename, record in receipt.get("file_receipts", {}).items():
        try:
            if list(source_checker._fingerprint(Path(filename))) != record["fingerprint"]:
                return False
        except (OSError, KeyError):
            return False
    return True


def audit_resume(original_run, original_output, resumed_run, output_dir, *, repository_root=ROOT):
    started = time.perf_counter()
    root = existing(repository_root)
    original_run, original_output, resumed_run = map(existing, (original_run, original_output, resumed_run))
    output = Path(output_dir).absolute()
    if output.is_symlink() or any(parent.is_symlink() for parent in output.parents):
        raise ValueError("audit-linked-output-forbidden")
    parent = root / "reports/generated/data-quality/feature-gap"
    if (not output.resolve().is_relative_to(parent.resolve(strict=True))
            or output.resolve().is_relative_to(original_run) or output.resolve().is_relative_to(original_output)
            or any((output / name).exists() for name in RECEIPT_NAMES)):
        raise ValueError("audit-new-exclusive-output-required")
    if sha(original_run / "plan.json") != ORIGINAL_PLAN_SHA256:
        raise ValueError("audit-original-plan-not-frozen-plan")
    old = check_old_completion(original_run, original_output)
    old_plan, targets = load_targets(original_run)
    new_plan, new_targets = load_targets(resumed_run)
    if (len(targets) != ORIGINAL_TARGET_RACES or old_plan.get("batch_id") != original_run.name
            or new_plan.get("batch_id") != resumed_run.name or original_run == resumed_run):
        raise ValueError("audit-original-scope-or-distinct-batch-invalid")
    db_path = existing(old_plan["database"])
    if existing(new_plan["database"]) != db_path or db_path != existing(root / "keiba/data/keiba_ultimate.db"):
        raise ValueError("audit-same-fixed-live-database-required")
    before_epoch, before_stat = epoch(db_path), db_path.stat()
    source = source_checker.verify([original_run, resumed_run], repository_root=root)
    source["verifier_code_sha256"] = sha(Path(source_checker.__file__))
    source["resume_auditor_code_sha256"] = sha(Path(__file__))
    coverage = combined_coverage(original_run, resumed_run, targets, new_targets, source)
    coverage.update(original_plan_sha256=sha(original_run / "plan.json"),
        old_completion_sha256=sha(original_output / "completion_receipt.json"),
        new_plan_sha256=sha(resumed_run / "plan.json"))
    journal = combined_journal_audit(db_path, [original_run, resumed_run], source_receipt=source, targets=targets)
    after_epoch, after_stat = epoch(db_path), db_path.stat()
    stable = (before_epoch is not None and before_epoch == journal["source_epoch"]["outside_before"]
        == journal["source_epoch"]["outside_after"] == after_epoch
        and (before_stat.st_dev, before_stat.st_ino) == (after_stat.st_dev, after_stat.st_ino))
    files_stable = verified_files_still_stable(source)
    result = {"schema": SCHEMA, "audited_at_utc": now(), "original_run": str(original_run),
        "resumed_run": str(resumed_run), "original_plan_sha256": sha(original_run / "plan.json"),
        "old_completion_sha256": sha(original_output / "completion_receipt.json"),
        "new_plan_sha256": sha(resumed_run / "plan.json"), "old_status_preserved": old["execution_outcome"],
        "source_integrity_pass": source.get("artifact_integrity_pass") is True,
        "journal_audit_pass": journal["audit_pass"] is True, "coverage_pass": coverage["coverage_pass"] is True,
        "verification_source_epoch_stable": stable, "source_files_stable_after_journal_audit": files_stable,
        "source_epoch": after_epoch, "database_read_only": True, "network_requests": 0,
        "historical_prestart_capture_proven": False, "duration_seconds": time.perf_counter() - started}
    result["safety_verification_pass"] = all(result[key] for key in ("source_integrity_pass", "journal_audit_pass",
        "coverage_pass", "verification_source_epoch_stable", "source_files_stable_after_journal_audit"))
    output.mkdir(parents=True, exist_ok=True)
    for name, value, key in ((RECEIPT_NAMES[0], source, "source_integrity_receipt"),
                              (RECEIPT_NAMES[1], journal, "journal_audit"),
                              (RECEIPT_NAMES[2], coverage, "combined_coverage_audit")):
        path = output / name
        write_new(path, value)
        result[key] = str(path.resolve())
        result[key + "_sha256"] = sha(path)
    write_new(output / "audit_receipt.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-run", type=Path, required=True)
    parser.add_argument("--original-output", type=Path, required=True)
    parser.add_argument("--resumed-run", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    result = audit_resume(args.original_run, args.original_output, args.resumed_run, args.output_dir)
    print(json.dumps({key: result[key] for key in ("safety_verification_pass", "source_integrity_pass",
        "journal_audit_pass", "coverage_pass", "source_epoch", "duration_seconds")}, ensure_ascii=False))
    return 0 if result["safety_verification_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
