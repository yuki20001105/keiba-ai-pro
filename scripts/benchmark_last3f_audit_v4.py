"""Bounded real-byte audit comparison, never a production completion receipt.

Copy 16--32 existing proofs and their exact source bytes into a NEW derived
fixture. Build separate sample-only manifests/events, then compare frozen V2
and candidate V4 in alternating order. No HTTP, database, live process control,
original-file edit, or all-production-files verification is performed.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import gc
import hashlib
import json
import os
from pathlib import Path
import statistics
import time
import uuid

import verify_last3f_gap_artifacts as old
import verify_last3f_gap_artifacts_v4 as new

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "reports/generated/data-quality/feature-gap/20261017-last3f-v2"
OUTPUT_PARENT = ROOT / "outputs/feature-research-20261017-v4"
SEMANTIC_FIELDS = (
    "schema", "artifact_integrity_pass", "issues", "issues_examples", "runs", "pilot",
    "unique_files_verified", "file_receipts", "historical_prestart_availability_proven",
    "database_opened", "network_requests", "source_files_modified", "interpretation",
)


@contextmanager
def shared_read(path):
    """Close promptly; on Windows do not deny writer/rename/delete sharing."""
    if os.name != "nt":
        with Path(path).open("rb") as stream:
            yield stream
        return
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    handle = create(str(path), 0x80000000, 0x1 | 0x2 | 0x4, None, 3, 0x08000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close(handle)
        raise
    with os.fdopen(descriptor, "rb") as stream:
        yield stream


def sha_bytes(body):
    return hashlib.sha256(body).hexdigest()


def read_bytes(path, *, maximum=192 * 1024 * 1024):
    before = old._fingerprint(path)
    if before[2] > maximum:
        raise ValueError("bounded-source-read-exceeded")
    with shared_read(path) as stream:
        body = stream.read(maximum + 1)
    if len(body) > maximum or before != old._fingerprint(path):
        raise ValueError("source-changed-during-bounded-read")
    return body


def sha_file(path):
    before = old._fingerprint(path)
    digest = hashlib.sha256()
    with shared_read(path) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if before != old._fingerprint(path):
        raise ValueError("source-changed-during-hash")
    return digest.hexdigest()


def write_bytes_new(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(body)


def write_json_new(path, value):
    write_bytes_new(path, old.canonical(value) + b"\n")


def derive_fixture(directory, count):
    run = directory / "derived-real-source-fixture"
    run.mkdir(exist_ok=False)
    source = SOURCE.resolve(strict=True)
    plan_body = read_bytes(source / "plan.json")
    plan = json.loads(plan_body)
    receipt = json.loads(read_bytes(source / "plan_receipt.json"))
    if receipt.get("plan_sha256") != sha_bytes(plan_body) or plan.get("targets_file") != "targets.jsonl":
        raise ValueError("original-plan-binding-invalid")
    selected, race_ids, source_receipts = [], set(), {}
    # Names only are enumerated; stop reading proofs once the bounded sample is
    # selected. Do not parse or re-audit all existing source bodies.
    candidates = sorted((source / "evidence").glob("*.json"), key=lambda path: path.name)
    for candidate in candidates:
        relative = candidate.relative_to(source).as_posix()
        candidate = old.inside(source, relative)
        body = read_bytes(candidate, maximum=1024 * 1024)
        digest = sha_bytes(body)
        if candidate.stem != digest:
            raise ValueError("original-proof-content-address-invalid")
        proof = json.loads(body)
        if proof.get("schema") != "last3f-repair-source-proof-v1":
            continue
        evidence = proof["evidence"]
        rid = evidence["race_id"]
        if rid in race_ids:
            continue
        race_ids.add(rid)
        selected.append({"race_id": rid, "proof_file": relative, "proof_sha256": digest})
        source_receipts[relative] = {"sha256": digest, "bytes": len(body)}
        write_bytes_new(run / relative, body)
        for key, digest_key in (("raw_body_path", "raw_body_sha256"), ("decoded_html_path", "decoded_html_sha256")):
            source_relative = proof[key]
            path = old.inside(source, source_relative)
            body = read_bytes(path)
            body_sha = sha_bytes(body)
            if body_sha != evidence["source"][digest_key] or path.stem != body_sha:
                raise ValueError("original-body-content-address-or-proof-mismatch")
            known = source_receipts.get(source_relative)
            current = {"sha256": body_sha, "bytes": len(body)}
            if known and known != current:
                raise ValueError("original-source-changed-between-proofs")
            if not known:
                source_receipts[source_relative] = current
                write_bytes_new(run / source_relative, body)
        if len(selected) == count:
            break
    if len(selected) != count:
        raise ValueError("not-enough-bounded-real-proofs")
    targets, target_hash = {}, hashlib.sha256()
    target_path = old.inside(source, "targets.jsonl")
    before = old._fingerprint(target_path)
    # One streamed metadata-manifest scan to retain the original all-runner
    # target contract. This does not open a DB or parse every source page.
    with shared_read(target_path) as stream:
        for line in stream:
            target_hash.update(line)
            target = json.loads(line)
            rid = target["race_id"]
            if rid in race_ids:
                if rid in targets:
                    raise ValueError("duplicate-original-sample-target")
                targets[rid] = target
    if (before != old._fingerprint(target_path) or target_hash.hexdigest() != plan.get("targets_sha256")
            or set(targets) != race_ids):
        raise ValueError("original-target-contract-invalid-or-changed")
    target_body = b"".join(old.canonical(targets[item["race_id"]]) + b"\n" for item in selected)
    write_bytes_new(run / "targets.jsonl", target_body)
    code = {name: sha_file(ROOT / name) for name in (
        "scripts/verify_last3f_gap_artifacts.py", "scripts/verify_last3f_gap_artifacts_v4.py",
        "scripts/benchmark_last3f_audit_v4.py")}
    derived = {"schema": "last3f-gap-repair-plan-v2", "batch_id": run.name,
        "targets_file": "targets.jsonl", "targets_sha256": sha_bytes(target_body),
        "counts": {"target_races": count}, "code_sha256": code,
        "benchmark_only": True, "production_completion_authority": False,
        "source_plan_sha256": sha_bytes(plan_body), "selection_policy": "first-lexical-distinct-verified-proof-races"}
    write_json_new(run / "plan.json", derived)
    plan_sha = sha_file(run / "plan.json")
    write_json_new(run / "plan_receipt.json", {"batch_id": run.name, "plan_sha256": plan_sha,
        "benchmark_only": True, "production_completion_authority": False})
    events, tail = [], None
    for item in selected:
        event = {"plan_sha256": plan_sha, "previous_event_sha256": tail,
                 "status": "sample-only", "benchmark_only": True, **item}
        tail = sha_bytes(old.canonical(event))
        events.append(old.canonical({**event, "event_sha256": tail}) + b"\n")
    write_bytes_new(run / "events.jsonl", b"".join(events))
    write_json_new(run / "status.json", {"plan_sha256": plan_sha, "event_count": count,
        "last_event_sha256": tail, "status": "derived-benchmark-fixture-only", "production_completion_authority": False})
    provenance = {"original_run": str(source), "original_plan_sha256": sha_bytes(plan_body),
        "original_targets_sha256": target_hash.hexdigest(), "copied_source_files": source_receipts,
        "selected_race_ids": sorted(race_ids), "source_bytes_copied": sum(item["bytes"] for item in source_receipts.values()),
        "derived_manifests_and_events_not_original_execution_receipts": True, "code_sha256": code}
    write_json_new(directory / "sample-provenance.json", provenance)
    return run, provenance


def benchmark(*, count=24, rounds=3):
    if not 16 <= count <= 32 or rounds not in (2, 3):
        raise ValueError("bounded-benchmark-requires-16-to-32-proofs-and-2-or-3-rounds")
    if OUTPUT_PARENT.is_symlink() or any(parent.is_symlink() for parent in OUTPUT_PARENT.parents):
        raise ValueError("benchmark-output-linked-path-forbidden")
    parent = OUTPUT_PARENT.resolve()
    name = datetime.now(timezone.utc).strftime("audit-benchmark-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    directory = parent / name
    if directory.parent != parent or directory.exists():
        raise ValueError("new-isolated-benchmark-directory-required")
    directory.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    result = {"schema": "last3f-audit-v4-real-bounded-comparison-v1", "benchmark_only": True,
        "production_completion_authority": False, "full_production_audit_performed": False,
        "network_requests": 0, "database_opened": False, "live_processes_modified": False,
        "source_files_modified": False, "requested_sample_proofs": count, "rounds_per_version": rounds,
        "output": str(directory), "status": "failed", "measurements": []}
    try:
        run, provenance = derive_fixture(directory, count)
        result.update(fixture=str(run), source_bytes_copied=provenance["source_bytes_copied"],
                      unique_source_files=len(provenance["copied_source_files"]))
        semantic = None
        for round_index in range(rounds):
            order = (("v2", old), ("v4", new)) if round_index % 2 == 0 else (("v4", new), ("v2", old))
            for version, module in order:
                gc.collect()
                clock, cpu = time.perf_counter(), time.process_time()
                receipt = module.verify([run], repository_root=ROOT)
                elapsed, cpu_elapsed = time.perf_counter() - clock, time.process_time() - cpu
                current = {key: receipt[key] for key in SEMANTIC_FIELDS}
                if receipt.get("artifact_integrity_pass") is not True:
                    raise ValueError(f"{version}-bounded-fixture-integrity-failed:{receipt.get('issues')}")
                if semantic is not None and current != semantic:
                    unequal = [key for key in SEMANTIC_FIELDS if current[key] != semantic[key]]
                    raise ValueError("bounded-semantic-parity-failed:" + ",".join(unequal))
                semantic = current
                write_json_new(directory / f"{version}-round-{round_index + 1}-receipt.json", receipt)
                measurement = {"version": version, "round": round_index + 1, "wall_seconds": elapsed,
                    "process_cpu_seconds": cpu_elapsed, "file_reads": receipt["counts"]["file_reads"],
                    "bytes_read": receipt["counts"]["bytes_read"], "unique_files_verified": receipt["unique_files_verified"],
                    "semantic_sha256": sha_bytes(old.canonical(current)), "optimization": receipt.get("optimization")}
                result["measurements"].append(measurement)
        # Verify the copied sample source files remain identical. This is still
        # bounded to the selected sample, not a replacement for the old audit.
        for relative, expected in provenance["copied_source_files"].items():
            if sha_file(old.inside(SOURCE, relative)) != expected["sha256"]:
                raise ValueError("original-sample-source-changed-during-benchmark")
        for name, digest in provenance["code_sha256"].items():
            if sha_file(ROOT / name) != digest:
                raise ValueError("benchmark-code-changed-during-measurement")
        medians = {version: statistics.median(row["wall_seconds"] for row in result["measurements"]
                                            if row["version"] == version) for version in ("v2", "v4")}
        first = {version: next(row for row in result["measurements"] if row["version"] == version)
                 for version in ("v2", "v4")}
        result.update(status="passed", semantic_parity=True, all_sample_hashes_equal=True,
            original_sample_files_unchanged=True, median_wall_seconds=medians,
            audit_component_median_speedup=medians["v2"] / medians["v4"],
            file_reads_ratio_v2_over_v4=first["v2"]["file_reads"] / first["v4"]["file_reads"],
            read_bytes_ratio_v2_over_v4=first["v2"]["bytes_read"] / first["v4"]["bytes_read"],
            interpretation="Warm/local bounded audit component only; not HTTP acquisition or whole-pipeline speedup. No production completion authority.")
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["total_wall_seconds"] = time.perf_counter() - started
    result["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    write_json_new(directory / "benchmark-result.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proofs", type=int, default=24)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args(argv)
    result = benchmark(count=args.proofs, rounds=args.rounds)
    print(json.dumps({key: result[key] for key in ("output", "status", "total_wall_seconds")}
        | {key: result.get(key) for key in ("audit_component_median_speedup", "median_wall_seconds",
            "read_bytes_ratio_v2_over_v4", "file_reads_ratio_v2_over_v4", "error")}, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
