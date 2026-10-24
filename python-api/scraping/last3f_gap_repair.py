"""Narrow, journalled repairs of missing clocks; never overwrite good values."""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import unicodedata
import re

from scraping.acquisition_scope import normalized_date, is_jra_race_id
from scraping.last3f_evidence import classify_last3f
from scraping.quality import classify_race_quality
from scraping.safe_repair import initialize_journal, _journal, encode, load_saved, RepairConflict

CLOCK_FIELDS = ("last_3f_time", "last_3f")


def identity(item):
    text = lambda value: re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))
    return int(item["horse_number"]), text(item["horse_id"]), text(item["horse_name"])


def clock_state(item):
    states = [classify_last3f(item.get(field)) for field in CLOCK_FIELDS]
    numbers = {number for state, number in states if state == "valid"}
    if len(numbers) > 1:
        return "alias_conflict", None
    if numbers:
        first = next((classify_last3f(item[field])[0] for field in CLOCK_FIELDS
                      if item.get(field) is not None and str(item[field]).strip()), "missing")
        if first == "masked":
            return "masked_shadowing_valid_alias", next(iter(numbers))
        if first == "missing":
            return "missing_shadowing_valid_alias", next(iter(numbers))
        if first == "invalid":
            return "alias_conflict", None
        return "valid", next(iter(numbers))
    # Invalid is not equivalent to a missing slot and requires investigation.
    if any(state == "invalid" for state, _ in states):
        return "invalid", None
    return ("masked" if any(state == "masked" for state, _ in states) else "missing"), None


def apply_evidence(db_path, evidence, *, batch_id):
    rid, day = evidence["race_id"], evidence["race_date"]
    if not is_jra_race_id(rid, day) or not "20160101" <= day <= "20261016":
        raise RepairConflict("last3f-repair-scope-invalid")
    source = evidence["source"]
    if (source.get("http_status") != 200 or source.get("all_runner_identity_verified") is not True
            or source.get("visible_race_date_verified") is not True
            or source.get("historical_capture_time_proven") is not False
            or not re.fullmatch(r"[a-f0-9]{64}", source.get("raw_body_sha256", ""))
            or source.get("raw_digest_verification") != "verified_against_bytes"):
        raise RepairConflict("last3f-source-proof-required")
    source_rows = {identity(item): item for item in evidence["rows"]}
    if len(source_rows) != len(evidence["rows"]) or len(source_rows) != evidence["runner_count"]:
        raise RepairConflict("last3f-source-identity-duplicate")
    initialize_journal(db_path)
    changed = []
    with closing(sqlite3.connect(str(db_path), timeout=5)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            payload = load_saved(db, rid)
            if (payload is None or normalized_date(payload["race_info"].get("date")) != day
                    or not classify_race_quality(payload).valid_for_date_completion):
                raise RepairConflict("last3f-current-race-not-complete")
            current_rows = list(db.execute("SELECT id,data FROM race_results_ultimate WHERE race_id=? ORDER BY id", (rid,)))
            keys = [identity(json.loads(raw)) for _, raw in current_rows]
            if len(set(keys)) != len(keys) or set(keys) != set(source_rows):
                raise RepairConflict("last3f-current-all-runner-identity-changed")
            proposals = []
            for row_id, raw in current_rows:
                item = json.loads(raw)
                observed = source_rows[identity(item)]
                state, number = clock_state(item)
                if state == "alias_conflict":
                    raise RepairConflict("last3f-saved-clock-alias-conflict")
                fresh_state, fresh_number = classify_last3f(observed.get("last3f_seconds"))
                if observed["last3f_state"] != "valid":
                    continue  # Masked/missing/invalid source never becomes zero.
                if fresh_state != "valid":
                    raise RepairConflict("last3f-source-numeric-proof-conflict")
                if state in {"valid", "masked_shadowing_valid_alias", "missing_shadowing_valid_alias"}:
                    if abs(number - fresh_number) > 1e-8:
                        raise RepairConflict("last3f-existing-clock-source-conflict")
                    if state == "valid":
                        continue
                if state == "invalid":
                    continue
                after = deepcopy(item)
                for field in CLOCK_FIELDS:
                    if classify_last3f(item.get(field))[0] != "valid":
                        after[field] = fresh_number
                after["_last3f_gap_repair"] = {
                    "version": "last3f-gap-repair-v1", "batch_id": batch_id,
                    "raw_values": {field: item.get(field) for field in CLOCK_FIELDS},
                    "source_body_sha256": source["raw_body_sha256"],
                    "source_url": source["source_url"], "source_race_date": day,
                    "source_observed_at_utc": source["observed_at_utc"],
                    "repaired_at_utc": datetime.now(timezone.utc).isoformat(),
                    "historical_prestart_capture_proven": False,
                    "usage": "past race clock only; history_race_date < target_race_date",
                    "legacy_last3f_rank_recomputed": False,
                }
                # All unrelated numeric/result/identity fields retain exact values.
                untouched = set(item) - set(CLOCK_FIELDS) - {"_last3f_gap_repair"}
                if any(after[field] != item[field] for field in untouched):
                    raise RepairConflict("last3f-unrelated-field-change")
                proposals.append((row_id, raw, encode(after)))
            for row_id, raw, after in proposals:
                cursor = db.execute("UPDATE race_results_ultimate SET data=? WHERE id=? AND data=?", (after, row_id, raw))
                if cursor.rowcount != 1:
                    raise RepairConflict("last3f-current-row-changed")
                _journal(db, batch_id, "race_results_ultimate", row_id, rid, raw, after,
                         {"repair_kind": "last3f_clock", "source": source, "source_race_date": day,
                          "normal_values_overwritten": False, "zero_fill": False})
                changed.append(row_id)
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return {"race_id": rid, "updated_runners": len(changed), "updated_row_ids": changed,
            "source_numeric_runners": sum(row["last3f_state"] == "valid" for row in evidence["rows"]),
            "source_missing_runners": sum(row["last3f_state"] != "valid" for row in evidence["rows"]),
            "source_body_sha256": source["raw_body_sha256"]}
