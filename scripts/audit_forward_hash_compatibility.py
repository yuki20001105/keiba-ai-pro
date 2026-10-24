"""Read-only diagnosis of the frozen collector's JSON integer-key roundtrip.

This is NOT a repair, a new hashing rule, a guard bypass, or evidence of
operational model performance. It never changes the frozen source, ledger,
hashes, decisions, or results. Only ``official.ranks`` keys may be restored
to their parser-native integer type, and every original stored digest must
then match EXACTLY. Anything unexplained fails closed.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

SCHEMA = "forward-json-rank-key-compatibility-audit-v1"
RANK_KEY = re.compile(r"(?:[1-9]|1[0-8])\Z")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def restore_official_rank_keys(official):
    """Return a copy; never convert any other mapping or value.

    The exact key range/types come from forward_runtime.provider.parse_result.
    Missing ranks (e.g. a pending result) need no compatibility conversion.
    Mixed keys, leading zeroes, floats, booleans and impossible ranks are not
    interpreted as this known serialization issue.
    """
    if not isinstance(official, dict):
        raise ValueError("official-not-object")
    result = deepcopy(official)
    ranks = result.get("ranks")
    if ranks is None:
        return result
    if not isinstance(ranks, dict):
        raise ValueError("rank-map-not-object")
    if not ranks:
        return result
    if all(type(key) is int and 1 <= key <= 18 for key in ranks):
        normalized = ranks
    elif all(type(key) is str and RANK_KEY.fullmatch(key) for key in ranks):
        normalized = {int(key): value for key, value in ranks.items()}
    else:
        raise ValueError("rank-key-type-or-range-unsupported")
    if any(type(value) is not int or not 1 <= value <= 18 for value in normalized.values()):
        raise ValueError("rank-value-type-or-range-unsupported")
    numbers = result.get("runner_numbers")
    if (not isinstance(numbers, list) or any(type(n) is not int or not 1 <= n <= 18 for n in numbers)
            or len(numbers) != len(set(numbers)) or not set(normalized) <= set(numbers)):
        raise ValueError("rank-runner-identity-mismatch")
    result["ranks"] = normalized
    return result


def _settlement_variant(settlement, stored_hash):
    if not isinstance(settlement, dict) or not isinstance(stored_hash, str):
        raise ValueError("settlement-envelope-invalid")
    official = settlement.get("official")
    strict = (digest(settlement) == stored_hash
              and digest(official) == settlement.get("official_sha256"))
    if strict:
        # Shape validation remains necessary even when someone generated a
        # self-consistent digest for a malformed rank map.
        restore_official_rank_keys(official)
        return deepcopy(settlement), "strict"
    restored = deepcopy(settlement)
    restored["official"] = restore_official_rank_keys(official)
    if (digest(restored["official"]) != restored.get("official_sha256")
            or digest(restored) != stored_hash):
        raise ValueError("unexplained-settlement-hash-mismatch")
    return restored, "exact_original_integer_rank_keys"


def _check_binding(settlement, race_id, contract, projection=None):
    if (settlement.get("race_id") != race_id
            or settlement.get("official", {}).get("race_id") != race_id
            or settlement.get("protocol_sha256") != contract.get("protocol_sha256")
            or settlement.get("artifact_hashes") != contract.get("artifact_hashes")
            or settlement.get("mode") != "prospective_paper"
            or settlement.get("actual_purchase") is not False):
        raise ValueError("settlement-identity-or-contract-binding-mismatch")
    if projection is not None and any(
        settlement.get(key) != projection.get(key)
        for key in ("snapshot_sha256", "decision_sha256")
    ):
        raise ValueError("settlement-projection-binding-mismatch")


def audit_database(database):
    path = Path(database).resolve(strict=True)
    failures = []
    race_counts, settlement_modes, event_modes = Counter(), Counter(), Counter()
    latest_settlement_events = {}
    latest_capture_events = {}
    repaired_ids = []
    event_count = snapshot_count = settlement_count = 0
    contract_hash = None
    head = None
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10,
                         isolation_level=None) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        try:
            meta = dict(db.execute("SELECT key,value FROM runtime_meta"))
            if meta.get("schema") != "forward-runtime-v1":
                raise ValueError("unsupported-ledger-schema")
            contract = json.loads(meta["contract"])
            contract_hash = meta["contract_sha256"]
            if digest(contract) != contract_hash or contract.get("paper_only") is not True:
                raise ValueError("contract-hash-or-paper-only-mismatch")
            races = [dict(row) for row in db.execute("SELECT * FROM races ORDER BY race_id")]
            if len({row["race_id"] for row in races}) != len(races):
                raise ValueError("duplicate-race-projection")
            projections = {row["race_id"]: row for row in races}
            for row in db.execute("SELECT * FROM events ORDER BY seq"):
                event_count += 1
                body = {"recorded_at": row["recorded_at"], "race_id": row["race_id"],
                        "event_type": row["event_type"], "payload": json.loads(row["payload_json"]),
                        "previous_sha256": row["previous_sha256"]}
                try:
                    if row["seq"] != event_count or row["previous_sha256"] != head:
                        raise ValueError("event-sequence-or-previous-link-mismatch")
                    strict_event = digest(body) == row["event_sha256"]
                    event_mode = "strict"
                    if row["event_type"] == "settlement_recorded":
                        payload = body["payload"]
                        if not isinstance(payload, dict) or set(payload) != {"settlement", "settlement_sha256"}:
                            raise ValueError("settlement-event-payload-shape-mismatch")
                        restored, settlement_mode = _settlement_variant(
                            payload["settlement"], payload["settlement_sha256"])
                        _check_binding(restored, row["race_id"], contract)
                        if not strict_event:
                            if settlement_mode == "strict":
                                raise ValueError("event-mismatch-not-caused-by-rank-key-roundtrip")
                            body["payload"] = {"settlement": restored,
                                               "settlement_sha256": payload["settlement_sha256"]}
                            if digest(body) != row["event_sha256"]:
                                raise ValueError("unexplained-event-hash-mismatch")
                            event_mode = settlement_mode
                        latest_settlement_events[row["race_id"]] = {
                            "settlement": payload["settlement"], "settlement_sha256": payload["settlement_sha256"]}
                    elif not strict_event:
                        raise ValueError("non-settlement-event-hash-mismatch")
                    if row["event_type"] == "capture_committed":
                        latest_capture_events[row["race_id"]] = body["payload"]
                    event_modes[event_mode] += 1
                except (ValueError, KeyError, TypeError) as exc:
                    failures.append({"scope": "event", "seq": row["seq"],
                                     "race_id": row["race_id"], "reason": str(exc)})
                head = row["event_sha256"]
            for row in races:
                race_counts[row["status"]] += 1
                try:
                    if row.get("snapshot_json") is not None:
                        snapshot_count += 1
                        envelope = json.loads(row["snapshot_json"])
                        snapshot = envelope["snapshot"]
                        decision = json.loads(row["decision_json"])
                        if (envelope["snapshot_sha256"] != row["snapshot_sha256"]
                                or digest(snapshot) != row["snapshot_sha256"]
                                or digest(decision) != row["decision_sha256"]
                                or snapshot.get("race_id") != row["race_id"]
                                or snapshot.get("post_time") != row["post_time"]
                                or snapshot.get("protocol_sha256") != contract["protocol_sha256"]
                                or snapshot.get("artifact_hashes") != contract["artifact_hashes"]):
                            raise ValueError("capture-projection-hash-or-binding-mismatch")
                        capture_event = latest_capture_events.get(row["race_id"])
                        if (not capture_event or capture_event.get("snapshot") != envelope
                                or capture_event.get("decision") != decision
                                or capture_event.get("decision_sha256") != row["decision_sha256"]):
                            raise ValueError("capture-projection-event-mismatch")
                    if row.get("settlement_json") is not None:
                        settlement_count += 1
                        settlement = json.loads(row["settlement_json"])
                        restored, mode = _settlement_variant(settlement, row["settlement_sha256"])
                        _check_binding(restored, row["race_id"], contract, row)
                        event = latest_settlement_events.get(row["race_id"])
                        if (not event or event["settlement_sha256"] != row["settlement_sha256"]
                                or event["settlement"] != settlement):
                            raise ValueError("settlement-projection-event-mismatch")
                        settlement_modes[mode] += 1
                        if mode != "strict":
                            repaired_ids.append(row["race_id"])
                except (ValueError, KeyError, TypeError) as exc:
                    failures.append({"scope": "race", "race_id": row["race_id"], "reason": str(exc)})
            if set(latest_capture_events) - set(projections) or set(latest_settlement_events) - set(projections):
                failures.append({"scope": "projection", "reason": "orphan-capture-or-settlement-event"})
        except (ValueError, KeyError, TypeError, sqlite3.DatabaseError) as exc:
            failures.append({"scope": "database", "reason": str(exc)})
        finally:
            db.rollback()
    return {
        "schema": SCHEMA, "observed_at": datetime.now(timezone.utc).isoformat(),
        "database": str(path), "contract_sha256": contract_hash,
        "status": "PASS" if not failures else "FAIL",
        "strict_existing_guard_status": ("FAIL_KNOWN_INTEGER_KEY_ROUNDTRIP" if repaired_ids and not failures
                                         else "FAIL" if failures else "PASS"),
        "compatibility_verification": "Exact match to existing digests only; not new or weakened digests",
        "permitted_transform": "official.ranks canonical decimal horse-number keys 1..18 -> parser-native int keys only",
        "data_mutated": False, "frozen_source_mutated": False,
        "guard_bypassed": False, "operational_performance_validated": False,
        "metrics_computed": False, "source": {"auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        "counts": {"races": sum(race_counts.values()), "race_status": dict(race_counts),
                   "snapshots": snapshot_count, "settlements": settlement_count,
                   "settlement_hash_modes": dict(settlement_modes), "events": event_count,
                   "event_hash_modes": dict(event_modes), "failures": len(failures)},
        "event_head_sha256": head, "legacy_rank_key_race_ids": repaired_ids,
        "failures": failures[:100], "failure_details_truncated": len(failures) > 100,
        "limitations": ["This audit does not release/promote a model or authorize a trade.",
                        "Capture timing, source availability, numeric accuracy, model inference and ROI are not independently evaluated.",
                        "The frozen collector status/aggregate command remains unchanged and may still reject legacy records.",
                        "Active collector may append records after this consistent read-only transaction."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.output and args.output.exists():
        parser.error("output already exists; never overwrite an audit artifact")
    report = audit_database(args.root / "ledger.sqlite3")
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as file:
            file.write(text)
    print(text, end="")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
