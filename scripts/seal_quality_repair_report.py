"""Seal an existing repair and independent after-audit; never repair or train."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python-api"))
sys.path.insert(0, str(ROOT / "scripts"))
from evaluate_db_shared_venues import preservation_receipt
from services.prediction_source_revision import _validated_state


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    # Never physically hash the live database or the original multi-GB backup.
    path = Path(path)
    if path.suffix in {".db", ".sqlite3"} or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("not a small report, source or fixture file: " + str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path, data):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def seal(run_dir, after_path, deployment_dir, recovery_dir, final_health_path):
    run_dir = Path(run_dir).resolve(strict=True)
    expected_root = (ROOT / "reports/generated/data-quality/repair").resolve()
    if not run_dir.is_relative_to(expected_root):
        raise ValueError("unexpected repair directory")
    after_path = Path(after_path).resolve(strict=True)
    if not after_path.is_relative_to(run_dir):
        raise ValueError("after-audit must belong to the same repair")
    plan = read(run_dir / "plan.json")
    receipt = read(run_dir / "plan_receipt.json")
    if receipt["plan_sha256"] != sha(run_dir / "plan.json") or plan["batch_id"] != run_dir.name:
        raise ValueError("plan binding invalid")
    for filename, digest in plan["audit_files"].items():
        if sha(Path(plan["source_audit"]) / filename) != digest:
            raise ValueError("original audit changed")
    after = read(after_path)
    if (after.get("schema") != "quality-repair-after-audit-v2" or after["batch_id"] != run_dir.name
            or not after["normal_records_preserved_and_only_journaled_additions_or_updates"]
            or not after["source_revision"]["stable_and_latest"]):
        raise ValueError("independent source preservation audit has not passed")
    comparison_rows = after["comparison"]
    expected_tables = {"races_ultimate", "race_results_ultimate", "return_tables_ultimate", "training_data", "speed_figures"}
    if (len(comparison_rows) != len(expected_tables)
            or {row["table"] for row in comparison_rows} != expected_tables
            or any(row.get("passed") is not True or row.get("schema_unchanged") is not True
                   or row.get("deleted") != 0 or row.get("unauthorized_changes") != 0
                   for row in comparison_rows)
            or after["journal"].get("issues") or after["journal"].get("unused_chains")
            or after["journal"].get("chain_count") != after["journal"].get("used_chain_count")):
        raise ValueError("every source table and every journal chain must independently pass")

    discoveries = read(run_dir / "discovery.json")
    targets = {row["race_id"] for row in plan["targets"]}
    discovered = {rid for row in discoveries for rid in row["race_ids"]}
    targets.update(discovered)
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    last = {}
    for event in events:
        if event["plan_sha256"] != receipt["plan_sha256"]:
            raise ValueError("event belongs to a different plan")
        if event.get("race_id"):
            last[event["race_id"]] = event
    if set(last) != targets or any(row.get("status") != "saved" for row in last.values()):
        raise ValueError("not all repair candidates are saved")
    target_audit = after["all_repair_targets_including_discovery"]
    independent_status = target_audit["race_status"]
    if (target_audit["target_count"] != len(targets) or set(independent_status) != targets
            or target_audit["counts"] != {"mandatory_complete": len(targets)}
            or any(independent_status.get(rid) != "mandatory_complete" for rid in targets)):
        raise ValueError("independent audit must cover every planned and discovered race")

    source_files = {}
    for index, path in enumerate(sorted((run_dir / "sources").glob("*.html"))):
        digest = sha(path)
        if digest not in path.name:
            raise ValueError("stored source filename/hash mismatch")
        source_files[path.name] = digest
        if index % 16 == 0:
            time.sleep(.003)
    for rid, event in last.items():
        key = rid + "-" + event["source_body_sha256"] + ".html"
        if source_files.get(key) != event["source_body_sha256"]:
            raise ValueError("saved repair lacks its original source body")

    current_protected = preservation_receipt()
    protected = all(current_protected.get(key) == value
                    for key, value in plan["protected_before"].items() if key != "forward_processes")
    if not protected:
        raise ValueError("active model, H04 or a pinned source changed")
    deployment = read(Path(deployment_dir) / "summary.json")
    recovery = read(Path(recovery_dir) / "recovery-proof.json")
    final_health = read(final_health_path)
    # Final health is independently collected and retained as evidence, not an
    # authority to widen the forward protocol or permit betting.
    if deployment["status"] != "DEPLOYED_AND_VERIFIED" or recovery["status"] != "OPERATIONAL_RECOVERY_VERIFIED":
        raise ValueError("deployment or collector recovery not verified")
    if (not all(final_health.get(key) is True for key in
                ("api_health_200", "web_health_200", "collector_lease_alive"))
            or final_health.get("collector_stop_requested") != 0):
        raise ValueError("final API, UI or collector health not verified")
    observed = datetime.fromisoformat(final_health["observed_at_utc"].replace("Z", "+00:00"))
    if observed.tzinfo is None or not 0 <= (datetime.now(timezone.utc) - observed).total_seconds() <= 120:
        raise ValueError("final health receipt not fresh")
    suites = ElementTree.parse(run_dir / "verification.junit.xml").getroot().iter("testsuite")
    test_counts = Counter()
    for suite in suites:
        for name in ("tests", "failures", "errors", "skipped"):
            test_counts[name] += int(suite.attrib.get(name, 0))
    if not test_counts["tests"] or test_counts["failures"] or test_counts["errors"]:
        raise ValueError("passing regression test evidence required")
    code_files = ["python-api/scraping/source_normalization.py", "python-api/scraping/race.py",
                  "python-api/scraping/quality.py", "python-api/scraping/safe_repair.py",
                  "scripts/repair_current_jra_quality.py", "scripts/repair_source_verification.py",
                  "scripts/audit_quality_repair_after.py", "scripts/audit_quality_repair_after_v2.py",
                  "scripts/seal_quality_repair_report.py"]
    code_hashes = {name: sha(ROOT / name) for name in code_files}
    with closing(sqlite3.connect(Path(plan["database"]).resolve(strict=True).as_uri() + "?mode=ro",
                                 uri=True, timeout=3)) as current_db:
        current_db.execute("PRAGMA query_only=ON")
        source_state = _validated_state(current_db)
    source_state = list(source_state) if source_state is not None else None
    if source_state is None or source_state != after["source_revision"]["outside_after"]:
        raise ValueError("core source changed since the independent full audit")

    changes = Counter()
    for row in last.values():
        changes.update(row.get("changes", {}))
    executions = [read(path) for path in sorted(run_dir.glob("execution-*.json"))]
    result_fetch, discovery_fetch = Counter(), Counter()
    for execution in executions:
        result_fetch.update(execution.get("fetch_metrics", {}))
        for metrics in execution.get("discovery_fetch_metrics", []):
            discovery_fetch.update(metrics)
    normalization = read(run_dir / "going_normalization.json")
    flat = after["quality"]["scopes"]["jra_flat_201601_202609"]
    reasons = Counter(reason for row in plan["targets"] for reason in row["reasons"])
    comparison = {item["table"]: item for item in after["comparison"]}
    now = datetime.now(timezone.utc).isoformat()
    result = {
        "schema": "jra-quality-repair-final-summary-v1", "generated_at_utc": now,
        "status": "COMPLETED_AND_INDEPENDENTLY_VERIFIED", "batch_id": run_dir.name,
        "plan_sha256": receipt["plan_sha256"], "independent_after_audit": str(after_path),
        "scope": {"race_scope": "jra", "normalization_period": plan["normalization_period"],
                  "repair_normal_data_overwrite": False, "local_racing_repaired": False},
        "repair": {"initial_unique_candidates": len(plan["targets"]),
                   "initial_reasons": dict(reasons), "discovered_missing_races": len(discovered - {row["race_id"] for row in plan["targets"]}),
                   "discovery_dates": discoveries, "total_unique_candidates": len(targets),
                   "final_status": dict(Counter(row["status"] for row in last.values())),
                   "independently_verified_mandatory_complete": len(targets), "changes": dict(changes),
                   "going_normalized_race_headers": normalization["changed_races"],
                   "raw_values_and_before_images_retained": True,
                   "note": "Going normalization and result header updates overlap; do not sum them as unique changed headers."},
        "before_after": comparison,
        "quality": {"original_186_expected_ids": after["original_186_expected_ids"]["counts"],
                    "original_75_uncollected_results": after["original_75_uncollected_results"]["counts"],
                    "jra_declared_runner_count_mismatches": sum(row["scope"] == "jra" for row in after["quality"]["declared_runner_count_mismatches"]),
                    "runner_identity_or_alias_issues": after["quality"]["runner_issues"],
                    "historical_flat_runner_rows": flat["runner_rows"], "historical_flat_field_availability": flat["fields"]},
        "transport": {"result_requests": result_fetch.get("network_requests", 0),
                      "result_status_200": result_fetch.get("status_200", 0),
                      "listing_requests_separate": discovery_fetch.get("network_requests", 0),
                      "listing_status_200": discovery_fetch.get("status_200", 0),
                      "cached_result_reparses": result_fetch.get("cache_reuse_hits", 0),
                      "provider_pacing_and_backoff_unchanged": True,
                      "note": "Unsupported obstacle notations were fixed after the first pass; saved source HTML was reparsed without new result HTTP requests."},
        "preservation": {"normal_source_records_preserved": True, "source_rows_deleted": 0,
                         "unaccounted_source_changes": 0, "journal": after["journal"],
                         "core_source_epoch_at_seal": source_state,
                         "core_source_epoch_matches_independent_audit": True,
                         "active_model_h04_and_35_pins_unchanged": protected, "protected_after": current_protected,
                         "training_started": False, "model_activated": False, "commit_push": False,
                         "real_purchase_or_ui_purchase_registration": False},
        "runtime": {"deployment_summary": str(Path(deployment_dir) / "summary.json"),
                    "recovery_proof": str(Path(recovery_dir) / "recovery-proof.json"),
                    "final_health_receipt": str(Path(final_health_path)),
                    "final_health": final_health,
                    "expired_capture_windows_replayed": False,
                    "frozen_history_binding_rebuilt_midday": False},
        "tests": {"junit_report": str(run_dir / "verification.junit.xml"), "counts": dict(test_counts)},
        "source_code_sha256": code_hashes,
        "limitations": [
            "Resolved known JRA acquisition candidates, not a guarantee of all possible source fields or all unobserved official race IDs.",
            "Pedigree and final-3F numeric availability remain incomplete; paid/hidden values were not fabricated or zero-filled.",
            "Legitimate withdrawn/DNF/disqualified time omissions are not retrieval failures.",
            "Current H04 forward history remains frozen through 20260927; repair does not silently rebuild its daily binding.",
            "Collector lease loss is confirmed, but the upstream renewal interruption cause is undetermined; permanent external guardian/task hardening is not implemented.",
            "Current capture of historical results is not evidence of original pre-race availability; no accuracy or ROI improvement is claimed.",
            "The first after-audit report is retained with its payout-body race_id comparator failure; the versioned second audit is authoritative only after full independent checks."
        ]
    }
    summary_path = run_dir / "repair_summary.json"
    manifest_path = run_dir / "artifact_manifest.json"
    if summary_path.exists() or manifest_path.exists():
        raise ValueError("final artifacts already exist; never replace a sealed report")
    paths = sorted(path for path in run_dir.rglob("*") if path.is_file() and path.name != "artifact_manifest.json")
    manifest = {"schema": "quality-repair-artifact-manifest-v1", "generated_at_utc": now,
                "files": {str(path.relative_to(ROOT)).replace("\\", "/"): sha(path) for path in paths},
                "source_html_files_rehashed": len(source_files), "live_database_physically_hashed": False,
                "protected_original_audit": plan["audit_files"], "source_code_sha256": code_hashes,
                "external_evidence": {str(path): sha(path) for path in (
                    Path(deployment_dir) / "summary.json", Path(deployment_dir) / "artifact_manifest.json",
                    Path(recovery_dir) / "recovery-proof.json", Path(recovery_dir) / "artifact_manifest.json",
                    Path(final_health_path))}}
    summary_bytes = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    manifest["files"][str(summary_path.relative_to(ROOT)).replace("\\", "/")] = hashlib.sha256(summary_bytes).hexdigest()
    # Complete all input validation and hashing before publishing COMPLETED.
    # Publish the manifest first; summary is the last completion marker.
    write_new(manifest_path, manifest)
    write_new(summary_path, result)
    print(json.dumps({"summary": str(summary_path), "summary_sha256": sha(summary_path),
                      "manifest_sha256": sha(run_dir / "artifact_manifest.json"),
                      "total_candidates": len(targets), "status": result["status"]}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--after-audit", type=Path, required=True)
    parser.add_argument("--deployment-dir", type=Path, required=True)
    parser.add_argument("--recovery-dir", type=Path, required=True)
    parser.add_argument("--final-health", type=Path, required=True)
    args = parser.parse_args()
    seal(args.run_dir, args.after_audit, args.deployment_dir, args.recovery_dir, args.final_health)


if __name__ == "__main__":
    main()
