"""Independent read-only integrity checks of frozen final-3F repair artifacts.

No SQLite connection, HTTP request, repair, model operation, or database-file
hash is performed. Only the separately named new receipt directory is written.
This proves internal artifact integrity, not original historical publication.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
FEATURE_PARENT = ROOT / "reports/generated/data-quality/feature-gap"
HEX = re.compile(r"[a-f0-9]{64}")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _fingerprint(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def inside(root, reference, *, absolute=False):
    root = Path(root).resolve(strict=True)
    if not isinstance(reference, (str, Path)) or not str(reference):
        raise ValueError("artifact-path-required")
    candidate = Path(reference)
    if candidate.is_absolute() and not absolute:
        raise ValueError("artifact-absolute-reference-forbidden")
    if ".." in candidate.parts:
        raise ValueError("artifact-parent-traversal-forbidden")
    candidate = candidate if candidate.is_absolute() else root / candidate
    if candidate.is_symlink() or any(parent.is_symlink() for parent in candidate.parents):
        raise ValueError("artifact-linked-reference-forbidden")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError("artifact-reference-outside-root-or-not-file")
    return resolved


class Checker:
    def __init__(self):
        self.issues, self.counts, self.examples = Counter(), Counter(), []
        self.files = {}

    def issue(self, code, location):
        self.issues[code] += 1
        if len(self.examples) < 60:
            self.examples.append({"issue": code, "location": str(location)})

    def read(self, root, reference, *, expected=None, content_addressed=False, absolute=False):
        path = inside(root, reference, absolute=absolute)
        before = _fingerprint(path)
        # Source artifacts are page-sized. Refuse accidental DB/model/broad-file
        # references even when a malformed proof points at an in-root file.
        if path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".joblib", ".pkl"} or before[2] > 192 * 1024 * 1024:
            raise ValueError("artifact-not-page-or-manifest-sized")
        body = path.read_bytes()
        digest = sha_bytes(body)
        after = _fingerprint(path)
        if before != after:
            self.issue("artifact_changed_while_read", path)
        prior = self.files.get(str(path))
        if prior is not None and prior != {"fingerprint": list(after), "sha256": digest}:
            self.issue("artifact_changed_between_references", path)
        self.files[str(path)] = {"fingerprint": list(after), "sha256": digest}
        self.counts["file_reads"] += 1
        self.counts["bytes_read"] += len(body)
        if expected is not None and (not isinstance(expected, str) or not HEX.fullmatch(expected) or digest != expected):
            self.issue("artifact_sha256_mismatch", path)
        if content_addressed and (not HEX.fullmatch(path.stem) or path.stem != digest):
            self.issue("content_address_filename_mismatch", path)
        return body, digest, path

    def json(self, root, reference, **kwargs):
        body, digest, path = self.read(root, reference, **kwargs)
        value = json.loads(body.decode("utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("artifact-json-object-required")
        return value, digest, path

    def final_stability(self):
        for filename, recorded in self.files.items():
            try:
                if list(_fingerprint(Path(filename))) != recorded["fingerprint"]:
                    self.issue("artifact_changed_before_receipt", filename)
            except OSError:
                self.issue("artifact_missing_before_receipt", filename)


def inventory(run):
    """List evidence/body files without following directory links."""
    records = []
    for name in ("evidence", "sources/raw", "sources/decoded"):
        base = run / name
        if not base.exists():
            continue
        if base.is_symlink():
            raise ValueError("artifact-linked-source-directory")
        for directory, dirs, files in os.walk(base, followlinks=False):
            for child in dirs:
                if (Path(directory) / child).is_symlink():
                    raise ValueError("artifact-linked-descendant-directory")
            for filename in files:
                path = inside(run, (Path(directory) / filename).relative_to(run))
                records.append((path.relative_to(run).as_posix(), list(_fingerprint(path))))
    return sorted(records)


def _identities(rows):
    def text(value):
        return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))
    identities = [[row["horse_number"], text(row["horse_id"]), text(row["horse_name"])] for row in rows]
    if (any(type(row[0]) is not int or not 1 <= row[0] <= 18 or not row[1] or not row[2] for row in identities)
            or len({row[0] for row in identities}) != len(identities)
            or len({row[1] for row in identities}) != len(identities)):
        raise ValueError("artifact-runner-identity-invalid")
    return sha_bytes(canonical(sorted(identities)))


def verify_run(run_dir, checker, *, repository_root=ROOT):
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
    code_hashes = plan.get("code_sha256", {})
    if not isinstance(code_hashes, dict) or not code_hashes:
        checker.issue("declared_code_hashes_missing", run)
        code_hashes = {}
    for filename, expected in code_hashes.items():
        if Path(filename).suffix != ".py":
            checker.issue("non_python_code_reference", filename)
            continue
        try:
            checker.read(repository_root, filename, expected=expected)
        except (OSError, ValueError) as exc:
            checker.issue("code_reference_invalid", f"{filename}: {exc}")
    # Stream the event ledger: no need to retain all 24,000 event objects.
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
                    _, _, path = checker.read(run, ref["proof_file"], expected=ref["proof_sha256"], content_addressed=True)
                    relative = path.relative_to(run).as_posix()
                    if not relative.startswith("evidence/") or path.suffix != ".json":
                        checker.issue("event_proof_path_not_evidence", path)
                    referenced.add(relative)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    checker.issue("event_proof_reference_invalid", f"{event_count}: {exc}")
        result.update(events_sha256=event_sha, event_count=event_count, last_event_sha256=tail)
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
                _, _, source_path = checker.read(run, proof[key], expected=expected, content_addressed=True)
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
                checker.read(run, relative, content_addressed=True)
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
    source_root, receipt_root = Path(source_dir).resolve(strict=True), Path(receipt_dir).resolve(strict=True)
    probe, probe_sha, _ = checker.json(source_root, "probe.json")
    receipt, receipt_sha, _ = checker.json(receipt_root, "receipt.json")
    if probe.get("schema") != "public-last3f-three-probes-v1" or receipt.get("schema") != "last3f-public-probe-replay-v1":
        raise ValueError("pilot-source-or-replay-receipt-schema-invalid")
    records = {}
    for row in probe["records"]:
        rid = row["race_id"]
        if rid in records:
            checker.issue("pilot_duplicate_source_race", rid)
        _, digest, _ = checker.read(source_root, row["body_path"], expected=row["body_sha256"], absolute=True)
        records[rid] = digest
    replay_ids = set()
    for row in receipt["results"]:
        rid = row["race_id"]
        if rid in replay_ids or records.get(rid) != row["source_body_sha256"]:
            checker.issue("pilot_replay_source_binding_invalid", rid)
        replay_ids.add(rid)
    return {"source_dir": str(source_root), "receipt_dir": str(receipt_root),
        "probe_sha256": probe_sha, "replay_receipt_sha256": receipt_sha,
        "source_races": len(records), "replay_races": len(replay_ids),
        "decoded_html_not_separately_preserved": True,
        "scope": "Verify original raw response bytes and replay receipt binding only; no invented decoded-HTML receipt."}


def verify(run_dirs, *, pilot_source_dir=None, pilot_receipt_dir=None, repository_root=ROOT):
    checker, runs, pilot = Checker(), [], None
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
        "interpretation": "Internal byte/hash/plan/event integrity only, not publisher authenticity, historical publication or model performance."}


def _under_existing(root, path):
    path = Path(path)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("verification-directory-link-forbidden")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(Path(root).resolve(strict=True)) or not resolved.is_dir():
        raise ValueError("verification-directory-outside-allowed-root")
    return resolved


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pilot-source-dir", type=Path)
    parser.add_argument("--pilot-receipt-dir", type=Path)
    args = parser.parse_args(argv)
    runs = [_under_existing(FEATURE_PARENT, path) for path in args.run_dir]
    pilot_source = (_under_existing(ROOT / "reports/generated/data-quality/public-last3f-probe", args.pilot_source_dir)
                    if args.pilot_source_dir else None)
    pilot_receipt = _under_existing(FEATURE_PARENT, args.pilot_receipt_dir) if args.pilot_receipt_dir else None
    output = args.output_dir.resolve()
    if (output.parent != FEATURE_PARENT.resolve(strict=True) or output.exists()
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", output.name)):
        raise ValueError("new-feature-gap-output-directory-required")
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 10)
    except (ImportError, OSError, AttributeError):
        pass
    result = verify(runs, pilot_source_dir=pilot_source, pilot_receipt_dir=pilot_receipt)
    result["verifier_code_sha256"] = sha_bytes(Path(__file__).read_bytes())
    output.mkdir(exist_ok=False)
    path = output / "source_integrity_receipt.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": str(path), "artifact_integrity_pass": result["artifact_integrity_pass"],
        "issues": result["issues"], "unique_files_verified": result["unique_files_verified"]}, ensure_ascii=False))
    return 0 if result["artifact_integrity_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
