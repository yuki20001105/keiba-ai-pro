"""New-version, read-only report for exactly proven legacy rank-key roundtrips.

Never changes the frozen collector, ledger, hashes, model, policy or app DB.
The OLD strict guard remains unchanged and its failure is reported explicitly.
Only parser-native integer official.ranks keys are reconstructed, and only if
ALL original stored digests match. Unknown discrepancies produce no metrics.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "python-api"), str(REPO / "scripts")]

import audit_forward_hash_compatibility as compatibility
from forward_reports.evaluation import evaluate_forward, evaluation_definition
from forward_runtime.schedule import (ForwardError, aware, canonical, require_hash,
                                     schedule_state, sha256, stamp, study_days,
                                     validate_race)
from forward_runtime.settlement import settle_win_decision, validate_capture_and_decision
from prospective.policy import RULE

SCHEMA = "forward-paper-readonly-compatible-report-v1"
EXPECTED_PROTOCOL = "97eeca6207cbf9e8c11816e29c47fc5a71761a63b41e2aea3e4cee4245235307"
PACKAGES = ("forward_runtime", "forward_adapters", "experiments", "prospective")
EXTRA_SOURCES = ("scripts/run_forward_collector.py", "python-api/training/eligibility.py",
                 "python-api/training/local_retrain.py", "python-api/scraping/fetch_pipeline.py")
EVENT_TYPES = {"protocol_initialized", "owner_expired", "owner_acquired", "owner_heartbeat",
               "stop_requested", "owner_released", "race_scheduled", "post_time_change_rejected",
               "post_time_changed", "day_discovered", "capture_missed", "capture_claimed",
               "capture_skipped", "capture_failed", "capture_unknown", "capture_committed",
               "settlement_recorded"}


def file_hash(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_consistent_view(database):
    """One query-only transaction; no backup, writes, migration or network."""
    path = Path(database).resolve(strict=True)
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10,
                         isolation_level=None) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        try:
            return {"meta": dict(db.execute("SELECT key,value FROM runtime_meta")),
                    "days": [dict(r) for r in db.execute("SELECT * FROM schedule_days ORDER BY date")],
                    "races": [dict(r) for r in db.execute("SELECT * FROM races ORDER BY post_time,race_id")],
                    "events": [dict(r) for r in db.execute("SELECT * FROM events ORDER BY seq")],
                    "owner": dict(db.execute("SELECT * FROM owner WHERE singleton=1").fetchone())}
        finally:
            db.rollback()


def verify_fixed_inputs(root, state, *, repo=REPO, expected_protocol=EXPECTED_PROTOCOL):
    """Pin protocol, code, artifacts and the pre-observation metric definition."""
    root, repo = Path(root).resolve(), Path(repo).resolve()
    envelope = read_json(root / "protocol.json")
    protocol = envelope["protocol"]
    if (envelope["protocol_sha256"] != expected_protocol or sha256(protocol) != expected_protocol
            or state["contract"]["protocol_sha256"] != expected_protocol
            or state["contract"]["artifact_hashes"] != protocol["artifact_hashes"]):
        raise ForwardError("pinned-protocol-or-ledger-binding-changed")
    if (protocol["fixed_model_id"] != "H04" or protocol["paper_only"] is not True
            or protocol["automatic_purchase"] is not False
            or (protocol["date_from"], protocol["date_to"]) != ("20260920", "20261218")
            or len(protocol["feature_columns"]) != 99
            or (protocol["cutoff_seconds"], protocol["capture_window_seconds"],
                protocol["max_source_age_seconds"]) != (300, 30, 120)
            or aware(protocol["frozen_at"]) >= aware(protocol["start_at"])):
        raise ForwardError("fixed-H04-paper-period-features-or-cutoff-changed")
    paths = {str(p.relative_to(repo)).replace("\\", "/") for package in PACKAGES
             for p in (repo / "python-api" / package).glob("*.py")} | set(EXTRA_SOURCES)
    if paths != set(protocol["source_manifest"]):
        raise ForwardError("frozen-source-file-set-changed")
    for relative, expected in protocol["source_manifest"].items():
        path = (repo / relative).resolve()
        if not path.is_relative_to(repo) or file_hash(path) != expected:
            raise ForwardError("frozen-source-changed:" + relative)
    for name, digest in protocol["artifact_hashes"].items():
        require_hash(digest)
        if file_hash(root / "objects" / digest) != digest:
            raise ForwardError("frozen-artifact-changed:" + name)
    artifacts = protocol["artifact_hashes"]
    if (read_json(root / "objects" / artifacts["rule"]) != RULE
            or read_json(root / "objects" / artifacts["features"])["feature_columns"] != protocol["feature_columns"]):
        raise ForwardError("fixed-rule-or-feature-artifact-contract-mismatch")
    analysis = read_json(root / "analysis-definition.json")
    if (analysis["definition"] != evaluation_definition()
            or analysis["protocol_sha256"] != expected_protocol
            or analysis["outcomes_observed"] != 0
            or aware(analysis["frozen_at"]) >= aware(protocol["start_at"])):
        raise ForwardError("pre-observation-analysis-definition-changed")
    return {"protocol_sha256": expected_protocol, "source_files_verified": len(paths),
            "artifact_hashes": artifacts, "feature_count": 99,
            "analysis_file_sha256": file_hash(root / "analysis-definition.json"),
            "analysis_source_sha256": analysis["definition"]["evaluation_source_sha256"],
            "analysis_frozen_at": analysis["frozen_at"], "model_loaded": False}


def decode(row):
    result = dict(row)
    for key in ("race_json", "snapshot_json", "decision_json", "settlement_json", "discovery_json"):
        if key in result:
            value = result.pop(key)
            result[key.removesuffix("_json")] = json.loads(value) if value else None
    return result


def audit_consistent_view(raw):
    """Recheck all original digests and chain in the SAME view used for metrics.

    Reuses the existing narrowly scoped compatibility functions. The separate
    baseline audit is not used to authorize rows appended after its snapshot.
    Schedule, eligibility and state projections are also bound to events.
    """
    meta = raw["meta"]
    if meta.get("schema") != "forward-runtime-v1":
        raise ForwardError("unsupported-ledger-schema")
    contract = json.loads(meta["contract"])
    if sha256(contract) != meta["contract_sha256"] or contract.get("paper_only") is not True:
        raise ForwardError("contract-hash-or-paper-only-mismatch")
    for key, expected in {"date_from": "20260920", "date_to": "20261218", "cutoff_seconds": 300,
                          "capture_window_seconds": 30, "max_source_age_seconds": 120}.items():
        if contract.get(key) != expected:
            raise ForwardError("fixed-ledger-contract-changed:" + key)
    captures, settlements, discovered, scheduled, statuses, reasons = {}, {}, {}, {}, {}, {}
    event_modes, settlement_modes = Counter(), Counter()
    head = None
    for sequence, row in enumerate(raw["events"], 1):
        if row["seq"] != sequence or row["previous_sha256"] != head:
            raise ForwardError("event-sequence-or-previous-link-mismatch")
        body = {"recorded_at": row["recorded_at"], "race_id": row["race_id"],
                "event_type": row["event_type"], "payload": json.loads(row["payload_json"]),
                "previous_sha256": row["previous_sha256"]}
        kind, rid, payload = row["event_type"], row["race_id"], body["payload"]
        if kind not in EVENT_TYPES:
            raise ForwardError("unsupported-event-type:" + str(kind))
        strict = sha256(body) == row["event_sha256"]
        mode = "strict"
        if kind == "settlement_recorded":
            if not isinstance(payload, dict) or set(payload) != {"settlement", "settlement_sha256"}:
                raise ForwardError("settlement-event-payload-shape-mismatch")
            restored, variant = compatibility._settlement_variant(payload["settlement"], payload["settlement_sha256"])
            compatibility._check_binding(restored, rid, contract)
            if not strict:
                if variant == "strict":
                    raise ForwardError("event-mismatch-not-caused-by-rank-key-roundtrip")
                body["payload"] = {"settlement": restored, "settlement_sha256": payload["settlement_sha256"]}
                if sha256(body) != row["event_sha256"]:
                    raise ForwardError("unexplained-event-hash-mismatch")
                mode = variant
            settlements[rid] = payload
            statuses[rid] = restored["status"]
        elif not strict:
            raise ForwardError("non-settlement-event-hash-mismatch")
        if kind == "protocol_initialized":
            if sequence != 1 or payload != {"contract": contract, "contract_sha256": meta["contract_sha256"], "planned_dates": 90}:
                raise ForwardError("initial-contract-event-binding-mismatch")
        elif kind == "race_scheduled":
            scheduled[rid] = payload["race"]
            statuses[rid] = payload["status"]
            expected = "missed" if schedule_state(payload["race"]["post_time"], row["recorded_at"]) == "missed" else "queued"
            if payload["status"] != expected:
                raise ForwardError("initial-schedule-status-inconsistent-with-event-time")
            reasons[rid] = "discovered-after-cutoff" if expected == "missed" else None
        elif kind == "post_time_changed":
            scheduled[rid] = payload["race"]
            statuses[rid] = "missed" if schedule_state(payload["race"]["post_time"], row["recorded_at"]) == "missed" else "queued"
            reasons[rid] = "rescheduled-after-cutoff" if statuses[rid] == "missed" else None
        elif kind == "day_discovered":
            discovered[payload["date"]] = payload
        elif kind == "capture_committed":
            captures[rid] = payload
            statuses[rid] = payload["status"]
            reasons[rid] = payload["decision"].get("reason")
            if payload["status"] != ("skipped" if payload["decision"]["decision"] == "skip" else "captured"):
                raise ForwardError("capture-status-decision-mismatch")
        elif kind == "capture_claimed":
            statuses[rid] = "capturing"
        elif kind in {"capture_skipped", "capture_failed", "capture_missed", "capture_unknown"}:
            statuses[rid] = kind.removeprefix("capture_")
            reasons[rid] = payload["reason"]
        event_modes[mode] += 1
        head = row["event_sha256"]
    if not raw["events"] or raw["events"][0]["event_type"] != "protocol_initialized":
        raise ForwardError("initial-contract-event-required")
    days = [decode(row) for row in raw["days"]]
    if [row["date"] for row in days] != study_days():
        raise ForwardError("fixed-90-day-denominator-changed")
    for row in days:
        proof, event = row.get("discovery"), discovered.get(row["date"])
        if proof is None:
            if row["status"] != "unknown" or row["discovery_sha256"] is not None or event is not None:
                raise ForwardError("unknown-day-projection-invalid")
        elif (sha256(proof) != row["discovery_sha256"] or proof["date"] != row["date"]
              or proof["status"] != row["status"] or not event or event["evidence"] != proof
              or event["discovery_sha256"] != row["discovery_sha256"]):
            raise ForwardError("day-discovery-hash-or-event-binding-mismatch")
    races, repaired_ids = [], []
    for raw_row in raw["races"]:
        row = decode(raw_row)
        rid = row["race_id"]
        identity = validate_race(row["race"], row["race_date"])
        if (rid not in scheduled or scheduled[rid] != row["race"] or identity["race_id"] != rid
                or identity["post_time"] != row["post_time"] or identity["venue"] != row["venue"]
                or statuses.get(rid) != row["status"] or reasons.get(rid) != row["reason"]):
            raise ForwardError("race-identity-or-status-projection-event-mismatch")
        if row.get("snapshot"):
            envelope, decision, event = row["snapshot"], row["decision"], captures.get(rid)
            if (envelope["snapshot_sha256"] != row["snapshot_sha256"]
                    or sha256(envelope["snapshot"]) != row["snapshot_sha256"]
                    or sha256(decision) != row["decision_sha256"] or not event
                    or event["snapshot"] != envelope or event["decision"] != decision
                    or event["decision_sha256"] != row["decision_sha256"]
                    or row["captured_at"] != envelope["snapshot"]["stored_at"]
                    or type(row["eligible"]) is not int or row["eligible"] not in (0, 1)
                    or event["eligible"] is not bool(row["eligible"])):
                raise ForwardError("capture-projection-hash-or-event-binding-mismatch")
        elif any(row.get(key) is not None for key in ("snapshot_sha256", "decision", "decision_sha256", "settlement", "settlement_sha256")):
            raise ForwardError("orphan-capture-or-settlement-projection")
        elif row.get("captured_at") is not None or row["eligible"] != 0 or rid in captures:
            raise ForwardError("unproven-capture-or-eligibility-projection")
        if row.get("settlement"):
            original = row["settlement"]
            restored, mode = compatibility._settlement_variant(original, row["settlement_sha256"])
            compatibility._check_binding(restored, rid, contract, row)
            event = settlements.get(rid)
            if (not event or event["settlement"] != original or event["settlement_sha256"] != row["settlement_sha256"]
                    or row["settled_at"] != restored.get("settled_at")):
                raise ForwardError("settlement-projection-event-mismatch")
            row["settlement"] = restored
            settlement_modes[mode] += 1
            if mode != "strict":
                repaired_ids.append(rid)
        elif row.get("settled_at") is not None or row.get("settlement_sha256") is not None or rid in settlements:
            raise ForwardError("unproven-settlement-projection")
        races.append(row)
    ids = {row["race_id"] for row in races}
    if (len(ids) != len(races) or ids != set(scheduled)
            or (set(captures) | set(settlements)) - ids or set(discovered) - set(study_days())):
        raise ForwardError("orphan-or-duplicate-denominator-projection")
    state = {"contract": contract, "contract_sha256": meta["contract_sha256"],
             "days": days, "races": races, "owner": deepcopy(raw["owner"]), "event_count": len(raw["events"])}
    audit = {"status": "PASS", "consistent_transaction": True, "original_stored_hashes_all_exact": True,
             "events_verified": len(raw["events"]), "event_head_sha256": head,
             "event_hash_modes": dict(event_modes), "settlement_hash_modes": dict(settlement_modes),
             "rank_key_compatibility_race_ids": repaired_ids, "races_verified": len(races),
             "days_verified": len(days), "guard_bypassed": False, "ledger_mutated": False}
    return state, audit


def build_report(root, *, as_of=None, repo=REPO, expected_protocol=EXPECTED_PROTOCOL):
    root = Path(root).resolve(strict=True)
    report = {"schema": SCHEMA, "mode": "read_only_prospective_paper_report", "status": "FAIL",
              "root": str(root), "adapter_sha256": file_hash(__file__),
              "compatibility_auditor_sha256": file_hash(compatibility.__file__),
              "metrics_computed": False, "ledger_mutated": False, "frozen_source_mutated": False,
              "model_or_policy_changed": False, "guard_bypassed": False,
              "actual_purchases": 0, "app_performance_db_modified": False,
              "operational_model_release_authorized": False,
              "limitations": [
                  "This is a separate diagnostic report, not an operational guard fix or a release/purchase authorization.",
                  "The frozen status/aggregate commands remain unchanged; strict legacy failures remain visible.",
                  "Raw receipt bytes and executable model inference are not independently replayed by this snapshot-only report.",
                  "Calendar completeness is the frozen evidence-backed attestation; missing days/races are not invented or dropped.",
                  "Pending tickets are excluded from the labelled known-only subset and keep full-campaign ROI undefined.",
                  "Indicative pre-race odds are not executable locked prices; paper payouts use only complete official proof.",
                  "The active collector may append events after the reported transaction head."]}
    try:
        baseline = compatibility.audit_database(root / "ledger.sqlite3")
        report["baseline_compatibility_audit"] = baseline
        if baseline["status"] != "PASS":
            raise ForwardError("required-baseline-compatibility-audit-failed")
        raw = read_consistent_view(root / "ledger.sqlite3")
        state, audit = audit_consistent_view(raw)
        report["consistent_view_audit"] = audit
        report["fixed_inputs_audit"] = verify_fixed_inputs(root, state, repo=repo, expected_protocol=expected_protocol)
        current = aware(as_of if as_of is not None else datetime.now(timezone.utc))
        if any(aware(event["recorded_at"]) > current for event in raw["events"]):
            raise ForwardError("historical-as-of-projection-reconstruction-not-supported")
        report["as_of"] = stamp(current)
        original = deepcopy(state)
        for original_row, raw_row in zip(original["races"], raw["races"]):
            original_row["settlement"] = json.loads(raw_row["settlement_json"]) if raw_row["settlement_json"] else None
        try:
            evaluate_forward(original, as_of=current)
            report["strict_original_report_guard"] = {"status": "PASS"}
        except ForwardError as exc:
            report["strict_original_report_guard"] = {"status": "FAIL", "reason": str(exc), "guard_unchanged": True}
            if not audit["rank_key_compatibility_race_ids"]:
                raise ForwardError("strict-guard-failure-unrelated-to-proven-rank-key-roundtrip") from exc
        # Replay all captures and official settlements, not only metric-eligible
        # winners. This ensures every paper ticket has pre-cutoff proof.
        captures = settlements = 0
        for row in state["races"]:
            if row.get("snapshot"):
                stored = row["snapshot"]["snapshot"]
                _, verified = validate_capture_and_decision(row, row["snapshot"], row["decision"],
                                                           state["contract"], now=stored["stored_at"])
                if verified is not bool(row["eligible"]):
                    raise ForwardError("eligibility-projection-disagrees-with-capture")
                captures += 1
            if row.get("settlement"):
                rebuilt = settle_win_decision(row, row["settlement"]["official"], now=current)
                if rebuilt != row["settlement"]:
                    raise ForwardError("official-settlement-replay-mismatch")
                settlements += 1
        report["semantic_replay_audit"] = {"status": "PASS", "pre_race_captures_verified": captures,
                                            "official_settlements_replayed": settlements,
                                            "all_paper_tickets_pre_cutoff_verified": True}
        report["prediction_and_betting_evaluation"] = evaluate_forward(state, as_of=current)
        report.update(status="PASS", metrics_computed=True,
                      operational_performance_validated=False,
                      result_interpretation="Observed diagnostic paper evidence only; no model/rule promotion or profitability claim.")
    except (ValueError, KeyError, TypeError, OSError, sqlite3.DatabaseError) as exc:
        report["failure"] = {"type": type(exc).__name__, "reason": str(exc)}
        report["prediction_and_betting_evaluation"] = None
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--as-of", help="Timezone-aware report time; historical projection replay is deliberately rejected")
    args = parser.parse_args(argv)
    if args.output:
        output = args.output.resolve()
        protected = [args.root.resolve(), REPO / "keiba/data", REPO / "python-api/data", REPO / "python-api/models"]
        if args.output.exists() or args.output.is_symlink():
            parser.error("output already exists; never overwrite a research artifact")
        if any(output == path or output.is_relative_to(path) for path in protected):
            parser.error("output must be outside the frozen runtime and app data/model directories")
    report = build_report(args.root, as_of=args.as_of)
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(text)
    print(text, end="")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
