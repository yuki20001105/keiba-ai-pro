"""Non-destructive result completion with transactional, reversible row evidence.

This does not replace the ordinary writer: it is an explicit repair operation.
Healthy existing result values and row IDs are retained. An unfinished card can
be finalized only from a separately validated complete result source.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

from scraping.acquisition_scope import is_jra_race_id, normalized_date
from scraping.quality import classify_race_quality
from scraping.constants import VENUE_MAP
from scraping.source_normalization import normalize_race_payload


class RepairConflict(ValueError):
    pass


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def absent(value):
    return value is None or (isinstance(value, str) and value.strip() in {"", "**", "--", "-"})


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def _time(value):
    text = str(value or "").strip()
    if ":" in text:
        try:
            minute, second = text.split(":")
            return float(minute) * 60 + float(second)
        except ValueError:
            return None
    return _number(value)


def _equal(key, left, right):
    if str(left).strip() == str(right).strip():
        return True
    if key in {"date", "race_date"}:
        return normalized_date(left) == normalized_date(right)
    if key == "finish_time":
        return _time(left) is not None and _time(left) == _time(right)
    if key in {"distance", "num_horses", "horse_number", "finish_position"}:
        return _number(left) is not None and _number(left) == _number(right)
    return str(left).strip() == str(right).strip()


def _row_key(horse):
    number = _number(horse.get("horse_number"))
    horse_id = str(horse.get("horse_id") or "").strip()
    if number is None or number < 1 or int(number) != number or not horse_id:
        raise RepairConflict("repair-runner-identity-missing")
    return int(number), horse_id


def _surface(info):
    aliases = {"芝":"芝", "turf":"芝", "ダ":"ダート", "ダート":"ダート",
               "dirt":"ダート", "障害":"障害", "障":"障害"}
    values = {aliases[str(info[key]).strip().lower()] for key in ("track_type","surface")
              if not absent(info.get(key)) and str(info[key]).strip().lower() in aliases}
    if len(values) > 1:
        raise RepairConflict("repair-header-surface-alias-conflict")
    return next(iter(values)) if values else None


FINAL_FIELDS = {
    "finish_position", "finish_position_raw", "finish_annotation", "finish_time", "margin",
    "odds", "popularity", "corner_positions", "corner_positions_list",
    "corner_1", "corner_2", "corner_3", "corner_4", "last_3f", "last_3f_time", "last_3f_rank",
    "prize_money", "weight", "weight_kg", "weight_change", "horse_weight", "horse_weight_change",
}


def _validate_payouts(candidate):
    race_id = candidate["race_info"]["race_id"]
    seen = {}
    for payout in candidate.get("return_tables", []):
        if not isinstance(payout, dict):
            raise RepairConflict("repair-payout-record-invalid")
        if payout.get("race_id") is not None and payout["race_id"] != race_id:
            raise RepairConflict("repair-payout-race-id-conflict")
        key = (payout.get("bet_type"), str(payout.get("combinations")))
        amount = _number(payout.get("payout"))
        if amount is not None and amount > 0:
            if amount != int(amount):
                raise RepairConflict("repair-payout-noninteger")
            if key in seen and seen[key] != amount:
                raise RepairConflict("repair-payout-source-conflict")
            seen[key] = amount


def merge_result(existing, candidate):
    """Return a complete merge, or reject contradictory identities/results."""
    incoming = deepcopy(candidate)
    for horse in incoming.get("horses", []):
        if absent(horse.get("finish_position")) and not absent(horse.get("finish")):
            horse["finish_position"] = horse["finish"]
        if absent(horse.get("finish_time")) and not absent(horse.get("time_seconds")):
            horse["finish_time"] = horse["time_seconds"]
        supplied_times = [_time(horse[field]) for field in ("finish_time","time_seconds")
                          if not absent(horse.get(field)) and horse[field] not in (0,"0")]
        valid_times = [value for value in supplied_times if value is not None]
        if len(valid_times)>1 and max(valid_times)-min(valid_times)>1e-8:
            raise RepairConflict("repair-source-time-alias-conflict")
    quality = classify_race_quality(incoming)
    if not quality.valid_for_date_completion:
        raise RepairConflict("repair-source-not-complete:" + ";".join(quality.required_errors))
    info = incoming["race_info"]
    race_id = info.get("race_id")
    if not is_jra_race_id(race_id) or not normalized_date(info.get("date")):
        raise RepairConflict("repair-source-not-dated-jra")
    if info.get("venue") != VENUE_MAP.get(race_id[4:6]):
        raise RepairConflict("repair-source-venue-id-conflict")
    fresh_surface = _surface(info)
    _validate_payouts(incoming)
    if existing is None:
        return incoming
    old_info = existing["race_info"]
    old_horses = existing.get("horses", [])
    entry_header = bool(old_horses) and all(
        all(absent(h.get(field)) or h.get(field) in (0,"0") for field in ("finish_position","finish"))
        for h in old_horses)
    if old_info.get("race_id") != race_id:
        raise RepairConflict("repair-race-id-conflict")
    old_surface = _surface(old_info)
    if old_surface and fresh_surface and old_surface != fresh_surface and not entry_header:
        raise RepairConflict("repair-header-surface-conflict")
    for key in ("date", "venue", "distance", "track_type"):
        old, new = old_info.get(key), info.get(key)
        if entry_header and key in {"distance","track_type"}:
            continue
        if not absent(old) and not absent(new) and not _equal(key, old, new):
            raise RepairConflict("repair-header-conflict:" + key)
    old_keys = [_row_key(horse) for horse in old_horses]
    new_keys = [_row_key(horse) for horse in incoming["horses"]]
    if len(set(old_keys)) != len(old_keys) or len(set(new_keys)) != len(new_keys):
        raise RepairConflict("repair-duplicate-identity")
    if not set(old_keys).issubset(set(new_keys)):
        raise RepairConflict("repair-existing-runner-not-in-source")
    by_key = dict(zip(old_keys, old_horses))
    merged = []
    finalized_card = bool(old_info.get("_shutuba") or old_info.get("_provisional_result"))
    for fresh in incoming["horses"]:
        key = _row_key(fresh)
        old = by_key.get(key)
        if old is None:
            merged.append(fresh)
            continue
        old_finishes = [old[field] for field in ("finish_position","finish")
                        if not absent(old.get(field)) and old[field] not in (0,"0")]
        finalizing = not old_finishes
        finalized_card = finalized_card or finalizing or bool(old.get("_shutuba"))
        if not finalizing:
            old_times = [old[field] for field in ("finish_time","time_seconds")
                         if not absent(old.get(field)) and old[field] not in (0,"0")]
            for field,left in [("finish_position",v) for v in old_finishes] + [("finish_time",v) for v in old_times]:
                right = fresh.get(field)
                if absent(left) or absent(right):
                    continue
                if field == "finish_position" and re.fullmatch(r"[1-9][0-9]*[（(]降[）)]", str(left)):
                    if int(re.match(r"\d+", str(left)).group()) == _number(right):
                        continue
                if not _equal(field, left, right):
                    raise RepairConflict("repair-existing-result-conflict:" + field)
        result = deepcopy(old)
        for field, value in fresh.items():
            if absent(value):
                continue
            if absent(result.get(field)) or (finalizing and field in FINAL_FIELDS):
                result[field] = value
        # A source-verified normalized finish retains the original annotation.
        annotated = [value for value in old_finishes if re.fullmatch(r"[1-9][0-9]*[（(]降[）)]",str(value))]
        if annotated:
            raw_annotation = annotated[0]
            if fresh.get("finish_position_raw") == raw_annotation:
                normalized = fresh.get("_source_normalization",{}).get("finish_position",{})
                source = candidate.get("_source_evidence",{})
                if (normalized.get("status") != "source_verified_demotion"
                        or not normalized.get("evidence_decoded_html_sha256")
                        or normalized["evidence_decoded_html_sha256"] != source.get("decoded_html_sha256")):
                    raise RepairConflict("repair-demotion-source-evidence-unverified")
                result["finish_position_raw"] = raw_annotation
                result["finish_position"] = fresh["finish_position"]
                if "finish" in old:
                    result["finish"] = fresh["finish_position"]
                if fresh.get("finish_annotation"):
                    result["finish_annotation"] = fresh["finish_annotation"]
        if finalizing:
            # A card's stale alternative columns must not win the canonical
            # loader's alias precedence after a verified result is installed.
            aliases = {
                "finish": fresh.get("finish_position"),
                "time_seconds": _time(fresh.get("finish_time")),
                "horse_weight": fresh.get("weight_kg", fresh.get("horse_weight")),
                "horse_weight_change": fresh.get("weight_change", fresh.get("horse_weight_change")),
                "weight_diff": fresh.get("weight_change", fresh.get("horse_weight_change")),
            }
            for alias,value in aliases.items():
                if alias in old:
                    if absent(value):
                        result.pop(alias, None)
                    else:
                        result[alias] = value
            result.pop("_shutuba", None)
            result.pop("_provisional_result", None)
        merged.append(result)
    merged_info = deepcopy(old_info)
    for key, value in info.items():
        if absent(merged_info.get(key)) and not absent(value):
            merged_info[key] = value
    if finalized_card:
        if entry_header:
            for key in ("distance","track_type"):
                if not absent(info.get(key)):
                    merged_info[key] = info[key]
            if "surface" in merged_info and fresh_surface is not None:
                merged_info["surface"] = fresh_surface
        for key in ("field_condition","field_condition_raw","weather","odds_status"):
            if not absent(info.get(key)):
                merged_info[key] = info[key]
        for key in ("data_observed_at","source_observed_at","odds_observed_at","captured_at"):
            if key in merged_info:
                merged_info.setdefault("_entry_observation_before_result",{})[key] = merged_info.pop(key)
        merged_info["result_observed_at_utc"] = datetime.now(timezone.utc).isoformat()
    # The verified full result table gives the complete listed-runner count.
    # Only a previously incomplete card/count is corrected, with before-image.
    if _number(old_info.get("num_horses")) != len(merged):
        merged_info["num_horses"] = len(merged)
    for key in ("_shutuba", "_provisional", "_provisional_result"):
        merged_info.pop(key, None)
    result = {"race_info": merged_info, "horses": merged,
              "return_tables": incoming.get("return_tables", [])}
    report = classify_race_quality(result)
    if not report.valid_for_date_completion:
        raise RepairConflict("repair-merged-not-complete:" + ";".join(report.required_errors))
    return result


def initialize_journal(db_path):
    with sqlite3.connect(str(db_path), timeout=5) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS data_quality_repair_journal_v1 (
            id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL,
            table_name TEXT NOT NULL, row_key TEXT NOT NULL, race_id TEXT NOT NULL,
            before_data TEXT, after_data TEXT NOT NULL, before_sha256 TEXT,
            after_sha256 TEXT NOT NULL, evidence_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL)""")


def _journal(db, batch, table, key, race_id, before, after, evidence):
    db.execute("""INSERT INTO data_quality_repair_journal_v1
        (batch_id,table_name,row_key,race_id,before_data,after_data,before_sha256,
         after_sha256,evidence_json,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (batch, table, str(key), race_id, before, after, digest(before) if before is not None else None,
         digest(after), encode(evidence), datetime.now(timezone.utc).isoformat()))


def load_saved(db, race_id):
    row = db.execute("SELECT data FROM races_ultimate WHERE race_id=?", (race_id,)).fetchone()
    if row is None:
        return None
    return {"race_info": json.loads(row[0]), "horses": [json.loads(row[0]) for row in db.execute(
        "SELECT data FROM race_results_ultimate WHERE race_id=? ORDER BY id", (race_id,))]}


def save_result_repair(db_path, candidate, *, batch_id, evidence):
    """Short transaction; no DELETE/REPLACE, preserve row IDs/created_at."""
    initialize_journal(db_path)
    race_id = candidate["race_info"]["race_id"]
    changed = {"inserted_races": 0, "updated_races": 0, "inserted_runners": 0,
               "updated_runners": 0, "inserted_payouts": 0}
    with sqlite3.connect(str(db_path), timeout=5) as db:
        db.execute("BEGIN IMMEDIATE")
        old = load_saved(db, race_id)
        merged = merge_result(old, candidate)
        meta = db.execute("SELECT data FROM races_ultimate WHERE race_id=?", (race_id,)).fetchone()
        raw_meta = encode(merged["race_info"])
        if meta is None:
            db.execute("INSERT INTO races_ultimate(race_id,data) VALUES (?,?)", (race_id, raw_meta))
            _journal(db,batch_id,"races_ultimate",race_id,race_id,None,raw_meta,evidence)
            changed["inserted_races"] += 1
        elif json.loads(meta[0]) != merged["race_info"]:
            db.execute("UPDATE races_ultimate SET data=? WHERE race_id=?", (raw_meta,race_id))
            _journal(db,batch_id,"races_ultimate",race_id,race_id,meta[0],raw_meta,evidence)
            changed["updated_races"] += 1
        rows = {_row_key(json.loads(raw)): (identifier,raw) for identifier,raw in db.execute(
            "SELECT id,data FROM race_results_ultimate WHERE race_id=?", (race_id,))}
        for horse in merged["horses"]:
            key = _row_key(horse)
            encoded = encode(horse)
            saved = rows.get(key)
            if saved is None:
                cursor = db.execute("INSERT INTO race_results_ultimate(race_id,data) VALUES (?,?)", (race_id,encoded))
                _journal(db,batch_id,"race_results_ultimate",cursor.lastrowid,race_id,None,encoded,evidence)
                changed["inserted_runners"] += 1
            elif json.loads(saved[1]) != horse:
                db.execute("UPDATE race_results_ultimate SET data=? WHERE id=?", (encoded,saved[0]))
                _journal(db,batch_id,"race_results_ultimate",saved[0],race_id,saved[1],encoded,evidence)
                changed["updated_runners"] += 1
        for payout in merged.get("return_tables", []):
            amount = _number(payout.get("payout"))
            if amount is None or amount <= 0 or amount != int(amount):
                continue  # Pending is never a synthetic zero payout.
            fields = (race_id,payout.get("bet_type"),str(payout.get("combinations")),int(amount))
            exists = db.execute("SELECT 1 FROM return_tables_ultimate WHERE race_id=? AND bet_type=? AND combinations=? AND payout=?", fields).fetchone()
            if not exists:
                conflict = db.execute("SELECT 1 FROM return_tables_ultimate WHERE race_id=? AND bet_type=? AND combinations=?", fields[:3]).fetchone()
                if conflict:
                    raise RepairConflict("repair-payout-conflict")
                cursor = db.execute("INSERT INTO return_tables_ultimate(race_id,bet_type,combinations,payout,popularity) VALUES (?,?,?,?,?)", (*fields,payout.get("popularity")))
                _journal(db,batch_id,"return_tables_ultimate",cursor.lastrowid,race_id,None,encode(payout),evidence)
                changed["inserted_payouts"] += 1
    return changed, merged


def normalize_saved_going(db_path, *, batch_id, date_from="20160101", date_to="20261016"):
    """Known immutable notation only; preserve original value and full raw row."""
    initialize_journal(db_path)
    changes = []
    # Read in pages and commit tiny batches; never hold a full-DB read transaction
    # or copy a multi-GB live database while the forward collector is active.
    cursor_id = ""
    while True:
        with sqlite3.connect(str(db_path), timeout=5) as db:
            rows = db.execute("SELECT race_id,data FROM races_ultimate WHERE race_id>? ORDER BY race_id LIMIT 250", (cursor_id,)).fetchall()
        if not rows:
            break
        cursor_id = rows[-1][0]
        candidates = []
        for race_id, raw in rows:
            if not is_jra_race_id(race_id):
                continue
            info = json.loads(raw)
            day = normalized_date(info.get("date"))
            old = info.get("field_condition")
            if day is None or not date_from <= day <= date_to or old not in {"稍", "不"}:
                continue
            info = normalize_race_payload({"race_info":info,"horses":[]})["race_info"]
            candidates.append((race_id,raw,encode(info)))
        if candidates:
            with sqlite3.connect(str(db_path), timeout=5) as db:
                db.execute("BEGIN IMMEDIATE")
                for race_id,before,after in candidates:
                    current = db.execute("SELECT data FROM races_ultimate WHERE race_id=?", (race_id,)).fetchone()
                    if current is None or current[0] != before:
                        raise RepairConflict("repair-concurrent-metadata-change")
                    db.execute("UPDATE races_ultimate SET data=? WHERE race_id=?", (after,race_id))
                    _journal(db,batch_id,"races_ultimate",race_id,race_id,before,after,
                             {"kind":"known-going-abbreviation","raw_retained":True,"not_new_capture_evidence":True})
                    changes.append(race_id)
    return changes
