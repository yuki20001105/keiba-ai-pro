"""Separate, read-only artifact verifier with bounded unique-file reuse.

The frozen verifier and its active process are never patched. Every unique file
is read and SHA-256 checked; reuse is local to one invocation and only follows
fresh path-safety and file-identity checks. This is not a stat-only audit.
"""
from __future__ import annotations

import argparse
from collections import Counter, OrderedDict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time

import verify_last3f_gap_artifacts as baseline

ROOT = baseline.ROOT
FEATURE_PARENT = baseline.FEATURE_PARENT
HEX = baseline.HEX
canonical = baseline.canonical
sha_bytes = baseline.sha_bytes
inside = baseline.inside
inventory = baseline.inventory
_fingerprint = baseline._fingerprint
_identities = baseline._identities
MAX_FILE_BYTES = 192 * 1024 * 1024


class FastChecker(baseline.Checker):
    """Digest receipts are unbounded by file count; cached bodies are bounded.

Only page/manifest-sized bytes can enter the LRU. Raw/decoded sources and proof
references that need only a digest stream through verify_blob without entering
it. Evicted JSON bodies may be reread, never reconstructed or trusted by stat.
    """

    def __init__(self, *, body_cache_bytes=4 * 1024 * 1024, body_cache_item_bytes=128 * 1024):
        super().__init__()
        if type(body_cache_bytes) is not int or type(body_cache_item_bytes) is not int:
            raise ValueError("integer-cache-bounds-required")
        if body_cache_bytes < 0 or body_cache_item_bytes < 0:
            raise ValueError("nonnegative-cache-bounds-required")
        self.body_cache_limit = body_cache_bytes
        self.body_item_limit = body_cache_item_bytes
        self._bodies = OrderedDict()
        self._body_bytes = 0
        self.optimization = Counter()

    def _forget_body(self, key):
        body = self._bodies.pop(key, None)
        if body is not None:
            self._body_bytes -= len(body)

    def _remember_body(self, key, body):
        self._forget_body(key)
        if len(body) > min(self.body_cache_limit, self.body_item_limit):
            return
        while self._bodies and self._body_bytes + len(body) > self.body_cache_limit:
            _, old = self._bodies.popitem(last=False)
            self._body_bytes -= len(old)
        self._bodies[key] = body
        self._body_bytes += len(body)
        self.optimization["peak_cached_body_bytes"] = max(
            self.optimization["peak_cached_body_bytes"], self._body_bytes)

    @staticmethod
    def _descriptor_fingerprint(stream):
        stat = os.fstat(stream.fileno())
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns

    def _read_content(self, path, before, *, want_body):
        digest = hashlib.sha256()
        chunks = [] if want_body else None
        total = 0
        with path.open("rb") as stream:
            if self._descriptor_fingerprint(stream) != before:
                self.issue("artifact_changed_while_read", path)
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_FILE_BYTES:
                    raise ValueError("artifact-not-page-or-manifest-sized")
                digest.update(chunk)
                if chunks is not None:
                    chunks.append(chunk)
            if self._descriptor_fingerprint(stream) != before:
                self.issue("artifact_changed_while_read", path)
        after = _fingerprint(path)
        if before != after:
            self.issue("artifact_changed_while_read", path)
        self.counts["file_reads"] += 1
        self.counts["bytes_read"] += total
        return b"".join(chunks) if chunks is not None else None, digest.hexdigest(), after

    def _check(self, root, reference, *, want_body, expected=None, content_addressed=False, absolute=False):
        # Never skip the frozen containment/symlink checks on a cache hit.
        path = inside(root, reference, absolute=absolute)
        before = _fingerprint(path)
        if path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".joblib", ".pkl"} or before[2] > MAX_FILE_BYTES:
            raise ValueError("artifact-not-page-or-manifest-sized")
        key = str(path)
        prior = self.files.get(key)
        cached = self._bodies.get(key)
        same = prior is not None and prior["fingerprint"] == list(before)
        self.optimization["reference_checks"] += 1
        if same and (not want_body or cached is not None):
            body, digest = cached if want_body else None, prior["sha256"]
            # Recheck around the reuse decision, just as a read is bracketed.
            if _fingerprint(path) != before:
                self.issue("artifact_changed_while_read", path)
                raise ValueError("artifact-changed-during-reuse")
            if want_body:
                self._bodies.move_to_end(key)
            self.optimization["duplicate_content_reads_avoided"] += 1
            self.optimization["duplicate_content_bytes_avoided"] += before[2]
        else:
            if prior is not None and not same:
                self.issue("artifact_changed_between_references", path)
                self._forget_body(key)
            body, digest, after = self._read_content(path, before, want_body=want_body)
            current = {"fingerprint": list(after), "sha256": digest}
            if prior is not None and same and prior != current:
                self.issue("artifact_changed_between_references", path)
            self.files[key] = current
            if want_body:
                self._remember_body(key, body)
        # Expected hash and content address apply to EVERY reference, not just
        # the first reference that populated the digest cache.
        if expected is not None and (not isinstance(expected, str) or not HEX.fullmatch(expected) or digest != expected):
            self.issue("artifact_sha256_mismatch", path)
        if content_addressed and (not HEX.fullmatch(path.stem) or path.stem != digest):
            self.issue("content_address_filename_mismatch", path)
        return body, digest, path

    def read(self, root, reference, **kwargs):
        return self._check(root, reference, want_body=True, **kwargs)

    def verify_blob(self, root, reference, **kwargs):
        _, digest, path = self._check(root, reference, want_body=False, **kwargs)
        return digest, path

    def final_stability(self):
        super().final_stability()
        # Files can move through an ancestor link without a different target
        # fingerprint. Recheck path safety even for code/pilot files outside the
        # source inventory. No file is reread or silently removed from receipts.
        for filename in self.files:
            path = Path(filename)
            if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
                self.issue("artifact_linked_before_receipt", filename)


Checker = FastChecker


def verify_run(run_dir, checker, *, repository_root=ROOT):
    """Frozen validation rules, with digest-only reads explicitly identified."""
    run = Path(run_dir).resolve(strict=True)
    initial = inventory(run)
    result = {"run_dir": str(run), "source_integrity_verified": False}
    referenced, proofs, used_raw, used_decoded = set(), {}, set(), set()
    plan, plan_sha, _ = checker.json(run, "plan.json")
    receipt, _, _ = checker.json(run, "plan_receipt.json")
    if (plan.get("schema") not in {"last3f-gap-repair-plan-v1", "last3f-gap-repair-plan-v2"}
            or plan.get("batch_id") != run.name or receipt.get("batch_id") != run.name
            or receipt.get("plan_sha256") != plan_sha or plan.get("targets_file") != "targets.jsonl"):
        checker.issue("plan_receipt_binding_invalid", run)
    result.update(batch_id=plan.get("batch_id"), plan_sha256=plan_sha)
    targets_body, targets_sha, _ = checker.read(run, "targets.jsonl", expected=plan.get("targets_sha256"))
    targets = {}
    for line in targets_body.splitlines():
        target = json.loads(line)
        rid = target["race_id"]
        if rid in targets:
            checker.issue("duplicate_plan_target", rid)
        targets[rid] = {"date": target["race_date"], "runners": target["runner_count"],
                        "identity_sha256": _identities(target["expected_runners"])}
    if len(targets) != plan.get("counts", {}).get("target_races"):
        checker.issue("plan_target_count_mismatch", run)
    result.update(targets_sha256=targets_sha, target_races=len(targets))
    del targets_body
    code_hashes = plan.get("code_sha256", {})
    if not isinstance(code_hashes, dict) or not code_hashes:
        checker.issue("declared_code_hashes_missing", run)
        code_hashes = {}
    for filename, expected in code_hashes.items():
        if Path(filename).suffix != ".py":
            checker.issue("non_python_code_reference", filename)
            continue
        try:
            checker.verify_blob(repository_root, filename, expected=expected)
        except (OSError, ValueError) as exc:
            checker.issue("code_reference_invalid", f"{filename}: {exc}")
    events_path = run / "events.jsonl"
    tail, event_count = None, 0
    if events_path.exists():
        body, event_sha, _ = checker.read(run, "events.jsonl")
        for line in body.splitlines():
            item = json.loads(line)
            declared = item.pop("event_sha256", None)
            if (item.get("plan_sha256") != plan_sha or item.get("previous_event_sha256") != tail
                    or sha_bytes(canonical(item)) != declared):
                checker.issue("event_sha_chain_invalid", event_count + 1)
            tail, event_count = declared, event_count + 1
            if item.get("race_id") not in targets:
                checker.issue("event_target_outside_plan", item.get("race_id"))
            refs = ([item] if item.get("proof_file") else []) + item.get("source_proofs", [])
            for ref in refs:
                try:
                    _, path = checker.verify_blob(run, ref["proof_file"], expected=ref["proof_sha256"], content_addressed=True)
                    relative = path.relative_to(run).as_posix()
                    if not relative.startswith("evidence/") or path.suffix != ".json":
                        checker.issue("event_proof_path_not_evidence", path)
                    referenced.add(relative)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    checker.issue("event_proof_reference_invalid", f"{event_count}: {exc}")
        result.update(events_sha256=event_sha, event_count=event_count, last_event_sha256=tail)
        del body
    else:
        result.update(event_count=0, events_sha256=None, last_event_sha256=None)
        checker.issue("event_ledger_missing", run)
    try:
        status, status_sha, _ = checker.json(run, "status.json")
        if (status.get("plan_sha256") != plan_sha or status.get("event_count") != event_count
                or status.get("last_event_sha256") != tail):
            checker.issue("event_tail_status_anchor_mismatch", run)
        result["status_sha256"] = status_sha
    except (OSError, ValueError, KeyError, TypeError) as exc:
        checker.issue("event_tail_status_anchor_missing_or_invalid", f"{run}: {exc}")
    schemas = Counter()
    for relative, _fingerprint_value in initial:
        if not relative.startswith("evidence/"):
            continue
        try:
            proof, proof_sha, path = checker.json(run, relative, content_addressed=True)
            schema = proof.get("schema")
            schemas[schema] += 1
            if schema == "last3f-repair-source-proof-v1":
                evidence = proof["evidence"]
                source = evidence["source"]
                rid, day = evidence["race_id"], evidence["race_date"]
                if (source.get("raw_digest_verification") != "verified_against_bytes"
                        or source.get("historical_capture_time_proven") is not False):
                    checker.issue("verified_source_receipt_invalid", relative)
                if (rid not in targets or targets[rid]["date"] != day
                        or evidence.get("runner_count") != targets[rid]["runners"]
                        or _identities(evidence["rows"]) != targets[rid]["identity_sha256"]):
                    checker.issue("proof_target_identity_or_date_mismatch", relative)
                raw_sha, decoded_sha = source.get("raw_body_sha256"), source.get("decoded_html_sha256")
            elif schema == "last3f-rejected-source-proof-v1":
                rid = proof["race_id"]
                if rid not in targets or proof.get("source_verified") is not False or proof.get("database_write_authority") is not False:
                    checker.issue("rejected_source_receipt_invalid", relative)
                raw_sha, decoded_sha = proof.get("raw_body_sha256"), proof.get("decoded_html_sha256")
            else:
                raise ValueError("unrecognized-source-proof-schema")
            for key, expected, prefix, used in (("raw_body_path", raw_sha, "sources/raw/", used_raw),
                                                ("decoded_html_path", decoded_sha, "sources/decoded/", used_decoded)):
                if not isinstance(expected, str) or not HEX.fullmatch(expected):
                    raise ValueError("source-body-digest-required")
                _, source_path = checker.verify_blob(run, proof[key], expected=expected, content_addressed=True)
                source_relative = source_path.relative_to(run).as_posix()
                if not source_relative.startswith(prefix):
                    checker.issue("proof_source_path_wrong_kind", source_relative)
                used.add(source_relative)
            proofs[relative] = proof_sha
        except (OSError, ValueError, KeyError, TypeError) as exc:
            checker.issue("source_proof_or_body_invalid", f"{relative}: {exc}")
        if len(proofs) % 32 == 0:
            time.sleep(.003)
    for relative, _fingerprint_value in initial:
        if relative.startswith("sources/"):
            try:
                checker.verify_blob(run, relative, content_addressed=True)
                if relative not in used_raw | used_decoded:
                    checker.issue("source_body_without_proof", relative)
            except (OSError, ValueError) as exc:
                checker.issue("source_body_invalid", f"{relative}: {exc}")
    if referenced - set(proofs):
        checker.issue("event_refers_to_unverified_proof", sorted(referenced - set(proofs)))
    if inventory(run) != initial:
        checker.issue("run_source_inventory_changed_during_verification", run)
    result.update(proof_count=len(proofs), proof_schema_counts=dict(schemas),
        referenced_proof_count=len(referenced), unreferenced_proof_count=len(set(proofs) - referenced),
        unreferenced_proof_note="Unreferenced proofs are still checked; a crash can occur after source sealing and before event publication.",
        unique_raw_bodies=len(used_raw), unique_decoded_html=len(used_decoded))
    return result


def verify_pilot(source_dir, receipt_dir, checker):
    # The baseline pilot driver requests raw bytes once and is bounded to its
    # explicit input. Reuse it unchanged; FastChecker still enforces all checks.
    return baseline.verify_pilot(source_dir, receipt_dir, checker)


def verify(run_dirs, *, pilot_source_dir=None, pilot_receipt_dir=None, repository_root=ROOT):
    started = time.perf_counter()
    checker, runs, pilot = FastChecker(), [], None
    for run in run_dirs:
        before = sum(checker.issues.values())
        try:
            value = verify_run(run, checker, repository_root=repository_root)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            checker.issue("run_verification_failed", f"{run}: {exc}")
            value = {"run_dir": str(run)}
        value["source_integrity_verified"] = sum(checker.issues.values()) == before
        runs.append(value)
    if pilot_source_dir or pilot_receipt_dir:
        try:
            if not pilot_source_dir or not pilot_receipt_dir:
                raise ValueError("both-pilot-directories-required")
            pilot = verify_pilot(pilot_source_dir, pilot_receipt_dir, checker)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            checker.issue("pilot_verification_failed", str(exc))
    checker.final_stability()
    return {"schema": "last3f-artifact-independent-verification-v1", "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "artifact_integrity_pass": not checker.issues, "issues": dict(checker.issues), "issues_examples": checker.examples,
        "runs": runs, "pilot": pilot, "counts": dict(checker.counts), "unique_files_verified": len(checker.files),
        "file_receipts": checker.files, "historical_prestart_availability_proven": False,
        "database_opened": False, "network_requests": 0, "source_files_modified": False,
        "interpretation": "Internal byte/hash/plan/event integrity only, not publisher authenticity, historical publication or model performance.",
        "optimization": {"version": "bounded-unique-artifact-v4", "duration_seconds": time.perf_counter() - started,
            "body_cache_limit_bytes": checker.body_cache_limit, "digest_cache_persistent": False,
            **dict(checker.optimization)}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pilot-source-dir", type=Path)
    parser.add_argument("--pilot-receipt-dir", type=Path)
    args = parser.parse_args(argv)
    runs = [baseline._under_existing(FEATURE_PARENT, path) for path in args.run_dir]
    pilot_source = (baseline._under_existing(ROOT / "reports/generated/data-quality/public-last3f-probe", args.pilot_source_dir)
                    if args.pilot_source_dir else None)
    pilot_receipt = baseline._under_existing(FEATURE_PARENT, args.pilot_receipt_dir) if args.pilot_receipt_dir else None
    output = args.output_dir.resolve()
    if (output.parent != FEATURE_PARENT.resolve(strict=True) or output.exists()
            or not baseline.re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", output.name)):
        raise ValueError("new-feature-gap-output-directory-required")
    result = verify(runs, pilot_source_dir=pilot_source, pilot_receipt_dir=pilot_receipt)
    result["verifier_code_sha256"] = sha_bytes(Path(__file__).read_bytes())
    result["baseline_verifier_code_sha256"] = sha_bytes(Path(baseline.__file__).read_bytes())
    output.mkdir(exist_ok=False)
    path = output / "source_integrity_receipt.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": str(path), "artifact_integrity_pass": result["artifact_integrity_pass"],
        "issues": result["issues"], "unique_files_verified": result["unique_files_verified"],
        "optimization": result["optimization"]}, ensure_ascii=False))
    return 0 if result["artifact_integrity_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
