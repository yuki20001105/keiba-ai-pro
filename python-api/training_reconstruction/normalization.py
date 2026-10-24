"""Lossless legacy-workout normalization and fail-closed future PIT policies.

Legacy DB rows cannot establish source publication, capture or clock distance.
The export path never accepts external evidence and never generates features.
Evidence-aware pure functions below are tested with synthetic receipts only;
their caller must independently authenticate any future evidence manifest.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import re
import statistics
import unicodedata

TIMES = ("time_6f", "time_5f", "time_4f", "time_3f", "time_1f")
LAPS = ("lap_6f_5f", "lap_5f_4f", "lap_4f_3f", "lap_3f_1f", "lap_1f_g")
SLOTS = TIMES + LAPS
JST = timezone(timedelta(hours=9))
RAW_CANDIDATES = ("training_time_5f", "training_time_4f", "training_time_3f", "training_time_1f", "final_lap", "pace", "grade", "is_last_training")
DERIVED_CANDIDATES = ("last_1f_zscore", "4f_zscore", "5f_zscore", "acceleration_score", "final_lap_rank", "course_relative_time", "condition_relative_time", "training_days_before_race")
HORSE_CANDIDATES = ("current_vs_previous_training", "current_vs_horse_average", "best_recent_training", "training_trend")

EVENT_FIELDS = [
    ("event_id", "string"), ("source_training_id", "int64"), ("race_id", "string"), ("race_date", "string"),
    ("horse_id", "string"), ("horse_number", "int64"), ("horse_name", "string"), ("identity_verified", "bool"),
    ("training_date_raw", "string"), ("training_date", "string"), ("course_raw", "string"), ("course_normalized", "string"),
    ("track_condition_raw", "string"), ("track_condition", "string"), ("rider", "string"), ("position", "string"),
    ("pace_raw", "string"), ("pace_category", "string"), ("grade_raw", "string"), ("grade_category", "string"),
    ("comment_raw", "string"), ("legacy_is_last_training", "int64"), ("is_last_training_verified", "bool"),
    ("source_db_created_at", "string"), ("source_db_sha256", "string"), ("source_record_sha256", "string"),
    ("source_url", "string"), ("source_hash", "string"), ("retrieved_at", "string"), ("historical_backfill", "bool"),
    ("raw_original_available", "bool"), ("capture_provenance", "string"), ("event_validity", "string"),
    ("pit_class", "string"), ("legacy_pit_class", "string"), ("distance_mapping_verified", "bool"),
    ("quarantined", "bool"), ("quarantine_reasons", "string"), ("missing_reason", "string"), ("model_input_allowed", "bool"),
]
MEASUREMENT_FIELDS = [
    ("event_id", "string"), ("source_training_id", "int64"), ("race_id", "string"), ("horse_id", "string"),
    ("horse_number", "int64"), ("training_date", "string"), ("course_normalized", "string"), ("track_condition", "string"),
    ("measurement_kind", "string"), ("original_slot", "string"), ("original_slot_index", "int64"),
    ("original_value", "string"), ("original_value_type", "string"), ("time_seconds", "float64"),
    ("measurement_distance", "int64"), ("lap_from_distance", "int64"), ("lap_to_distance", "int64"),
    ("distance_mapping_verified", "bool"), ("value_status", "string"), ("missing_reason", "string"),
    ("pit_class", "string"), ("quarantined", "bool"), ("model_input_allowed", "bool"),
]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def text(value):
    return None if value is None else str(value)


def category(value):
    if value is None:
        return None
    return unicodedata.normalize("NFKC", str(value)).strip() or None


def day(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not re.fullmatch(r"[0-9]{8}|[0-9]{4}/[0-9]{2}/[0-9]{2}|[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return None
    compact = value.replace("/", "").replace("-", "")
    try:
        return date(int(compact[:4]), int(compact[4:6]), int(compact[6:8]))
    except ValueError:
        return None


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo is not None and result.utcoffset() is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def event_validity(training_date, race_date):
    workout, race = day(training_date), day(race_date)
    if workout is None or race is None:
        return "invalid"
    return "training_date_before_race" if workout < race else "same_day_unknown" if workout == race else "after_race"


def classify_pit(event, evidence=None, *, cutoff_at=None):
    """Pure receipt policy, not an external evidence authentication mechanism.

    A requires row-bound, separately verified historical publication/capture.
    B requires an actual verified later historical-page response, not created_at.
    Missing evidence stays C even if an older audit called the event-date class B.
    """
    base = {"pit_class": "C_INVALID_OR_UNKNOWN", "capture_provenance": "capture_time_unknown",
            "raw_original_available": False, "historical_backfill": None}
    if not evidence:
        return base
    digest = evidence.get("raw_source_sha256", "")
    bound = all(event.get(key) is not None and evidence.get(key) == event.get(key)
                for key in ("race_id", "horse_number", "training_date", "course_normalized"))
    original = (evidence.get("raw_original_available") is True and evidence.get("raw_hash_verified") is True
                and evidence.get("row_values_verified") is True and bound
                and isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) is not None
                and re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("evidence_manifest_sha256", ""))) is not None
                and str(evidence.get("source_url", "")).startswith("https://"))
    if not original:
        return base
    base["raw_original_available"] = True
    received = timestamp(evidence.get("retrieved_at"))
    cutoff = timestamp(cutoff_at)
    workout, race = day(event.get("training_date")), day(event.get("race_date"))
    if received is None or workout is None or race is None:
        return base
    received_day = received.astimezone(JST).date()
    if received_day < workout:
        return base
    later = received_day > race and evidence.get("source_historical_page") is True
    if later:
        base.update(historical_backfill=True, capture_provenance="source_historical_page_retrieved_later")
    valid = event.get("identity_verified") is True and event_validity(event.get("training_date"), event.get("race_date")) == "training_date_before_race"
    if not valid or event.get("quarantined") is True:
        return base
    strict = (cutoff is not None and cutoff.astimezone(JST).date() == race and received < cutoff
              and evidence.get("contemporaneous_snapshot_verified") is True
              and evidence.get("publication_version_verified") is True)
    if strict:
        base.update(pit_class="A_STRICT_PIT", capture_provenance="contemporaneous_snapshot_verified", historical_backfill=False)
    elif later:
        base["pit_class"] = "B_HISTORICAL_EVENT_VALID"
    return base


def _value(value):
    # Raw JSON preserves SQL null, 0, text and numeric types without shifting slots.
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    kind = "null" if value is None else "boolean" if isinstance(value, bool) else "integer" if isinstance(value, int) else "real" if isinstance(value, float) else "text"
    if value is None or (isinstance(value, str) and value.strip().lower() in {"", "-", "--", "---", "nan", "none", "null", "n/a", "―", "—", "－"}):
        return raw, kind, None, "MISSING", "UNKNOWN"
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return raw, kind, None, "INVALID_VALUE", "INVALID_VALUE_UNRESOLVED"
    return raw, kind, float(value), "PRESENT_NUMERIC_DISTANCE_UNVERIFIED", None


def normalize_event(raw, prior, source_db_sha256):
    require(re.fullmatch(r"[0-9a-f]{64}", source_db_sha256) is not None, "invalid source DB hash")
    require(int(prior["training_id"]) == raw["id"] and prior["race_id"] == raw["race_id"], "source/audit identity mismatch")
    require(prior["training_date_raw"] == (raw["training_date"] or ""), "source/audit date mismatch")
    require(prior["normalized_training_name"] == (category(raw["horse_name"]) or ""), "source/audit name mismatch")
    require(raw["horse_number"] is None or (not isinstance(raw["horse_number"], bool) and int(raw["horse_number"]) == raw["horse_number"]), "nonintegral source horse number")
    require((raw["horse_number"] is None and not prior["horse_number"]) or float(prior["horse_number"]) == raw["horse_number"], "source/audit horse number mismatch")
    verified = prior["identity_verified"].lower() == "true"
    require(not verified or bool(prior.get("resolved_horse_id")), "verified identity has no horse id")
    relation = event_validity(raw["training_date"], prior["race_date"])
    old_relation = {"training_date_before_race": "A_BEFORE_RACE", "same_day_unknown": "B_SAME_DATE_TIME_UNKNOWN", "after_race": "C_AFTER_RACE", "invalid": "D_DATE_OR_RACE_UNKNOWN"}[relation]
    require(old_relation == prior["event_date_class"], "source/audit event relation mismatch")
    quarantine = ([] if verified else ["horse_identity_unverified"]) + ([] if relation == "training_date_before_race" else [relation])
    event = {
        "event_id": "legacy-training-" + str(raw["id"]), "source_training_id": raw["id"], "race_id": raw["race_id"],
        "race_date": day(prior["race_date"]).strftime("%Y%m%d") if day(prior["race_date"]) else None,
        "horse_id": prior.get("resolved_horse_id") or None if verified else None,
        "horse_number": raw["horse_number"], "horse_name": text(raw["horse_name"]), "identity_verified": verified,
        "training_date_raw": text(raw["training_date"]), "training_date": day(raw["training_date"]).strftime("%Y%m%d") if day(raw["training_date"]) else None,
        "course_raw": text(raw["course"]), "course_normalized": category(raw["course"]),
        "track_condition_raw": text(raw["track_condition"]), "track_condition": category(raw["track_condition"]),
        "rider": text(raw["rider"]), "position": text(raw["position"]), "pace_raw": text(raw["pace"]), "pace_category": category(raw["pace"]),
        "grade_raw": text(raw["grade"]), "grade_category": category(raw["grade"]), "comment_raw": text(raw["comment"]),
        "legacy_is_last_training": raw["is_last_training"], "is_last_training_verified": False,
        "source_db_created_at": text(raw["created_at"]), "source_db_sha256": source_db_sha256,
        "source_record_sha256": hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
        "source_url": None, "source_hash": None, "retrieved_at": None,
        "event_validity": relation, "legacy_pit_class": prior["record_pit_class"], "distance_mapping_verified": False,
        "quarantined": bool(quarantine), "quarantine_reasons": json.dumps(quarantine, ensure_ascii=False),
        "missing_reason": "raw_original_missing;capture_time_unknown;distance_mapping_unverified",
        "model_input_allowed": False,
    }
    event.update(classify_pit(event))
    measures = []
    for index, slot in enumerate(SLOTS):
        original, value_type, seconds, status, reason = _value(raw[slot])
        measures.append({"event_id": event["event_id"], "source_training_id": raw["id"], "race_id": raw["race_id"],
            "horse_id": event["horse_id"], "horse_number": raw["horse_number"], "training_date": event["training_date"],
            "course_normalized": event["course_normalized"], "track_condition": event["track_condition"],
            "measurement_kind": "cumulative" if index < 5 else "lap", "original_slot": slot,
            "original_slot_index": index % 5, "original_value": original, "original_value_type": value_type,
            "time_seconds": seconds, "measurement_distance": None, "lap_from_distance": None, "lap_to_distance": None,
            "distance_mapping_verified": False, "value_status": status, "missing_reason": reason,
            "pit_class": event["pit_class"], "quarantined": event["quarantined"], "model_input_allowed": False})
    require(set(event) == {name for name, _ in EVENT_FIELDS}, "event schema mismatch")
    return event, measures


def schema_definition():
    return {"schema": "training-normalized-long-v1", "event_fields": dict(EVENT_FIELDS), "measurement_fields": dict(MEASUREMENT_FIELDS),
        "measurement_distance_unit": "meters, nullable until original header mapping is verified",
        "original_value_encoding": "canonical JSON literal preserves original SQL null/numeric/text type; never zero-filled",
        "original_slot_meaning": "old DB column identifier only, not evidence of actual measured distance",
        "lap_segments": "measurement_kind=lap rows; distance endpoints remain null until verified",
        "source_hash_meaning": "original response-body hash only; not the DB hash or reconstructed-row hash",
        "historical_backfill_null": "retrieval provenance unknown, not contemporaneous and not verified backfill",
        "course_normalization": "NFKC plus outer whitespace only; no inferred aliases or OTHER pooling",
        "pit_class_migration": "old B_SOURCE_DATE_VALID_BUT_NOT_CAPTURE_TIME_VERIFIED does not imply new B_HISTORICAL_EVENT_VALID",
        "quarantine": "retain source rows separately flagged, never repair dates or treat unknown horse keys as cancellations",
        "feature_generation_in_this_export": False}


def candidate_registry():
    items = []
    for kind, names in (("raw", RAW_CANDIDATES), ("derived", DERIVED_CANDIDATES), ("horse_relative", HORSE_CANDIDATES)):
        for name in names:
            reject = name == "is_last_training"
            items.append({"feature": name, "kind": kind, "status": "REJECT" if reject else "HOLD", "model_input_allowed": False,
                "reason": "legacy flag hardcoded true, not independently reconstructed latest workout" if reject else "PIT/source and required measurement semantics unverified; insufficient audited study coverage",
                "encoding": "categorical_no_ordinal" if name in {"pace", "grade"} else "not_generated",
                "future_requirements": ["verified source and stable horse key", "separately approved experiment and adequate race/runner coverage", "frozen feature definition",
                    "verified course/condition/distance for numeric comparisons", "strictly earlier verified history and train-fold-only reference statistics"]})
    return {"schema": "training-feature-candidates-v1", "candidates": items,
        "counts": {"READY": 0, "HOLD": 19, "REJECT": 1}, "TRAINING_FEATURE_RESEARCH_READY": "NO",
        "comment_policy": {"status": "EXCLUDED", "in_twenty_candidate_count": False, "reason": "publication/edit version and source provenance unverified; separate NLP research required"},
        "B_backfill_policy": "never admitted to existing strict research; separate preregistered experiment required",
        "global_audit_statistics_are_feature_transforms": False, "model_training_performed": False}


def prior_reference(history, target, *, cutoff_at):
    """Synthetic-tested future policy only; never called on exported legacy rows.

    Conservative: references must have both an earlier workout day AND an
    earlier target-race day. No same-day ordering or whole-period statistics.
    """
    cutoff = timestamp(cutoff_at)
    require(cutoff is not None, "timezone-aware prediction cutoff required")
    def eligible(row):
        observed = timestamp(row.get("retrieved_at"))
        distance = row.get("measurement_distance")
        workout, race = day(row.get("training_date")), day(row.get("race_date"))
        return (row.get("pit_class") == "A_STRICT_PIT" and row.get("raw_original_available") is True
            and row.get("capture_provenance") == "contemporaneous_snapshot_verified"
            and row.get("distance_mapping_verified") is True and row.get("identity_verified") is True
            and row.get("quarantined") is not True
            and isinstance(distance, (int, float)) and not isinstance(distance, bool) and math.isfinite(distance) and distance > 0
            and re.fullmatch(r"[0-9a-f]{64}", str(row.get("source_hash", ""))) is not None
            and re.fullmatch(r"[0-9a-f]{64}", str(row.get("evidence_manifest_sha256", ""))) is not None
            and isinstance(row.get("time_seconds"), (int, float)) and not isinstance(row.get("time_seconds"), bool)
            and math.isfinite(row["time_seconds"]) and row["time_seconds"] > 0
            and observed is not None and observed < cutoff
            and workout is not None and race is not None and workout <= observed.astimezone(JST).date() <= race
            and event_validity(row.get("training_date"), row.get("race_date")) == "training_date_before_race")
    require(eligible(target) and cutoff.astimezone(JST).date() == day(target.get("race_date")), "target lacks verified PIT/distance/cutoff")
    group = (target.get("course_normalized"), target.get("track_condition"), target.get("measurement_distance"), target.get("measurement_kind"))
    require(all(value is not None and value != "" for value in group) and target["measurement_distance"] > 0, "unknown comparison group")
    values = []
    for row in history:
        if not eligible(row):
            continue
        other = (row.get("course_normalized"), row.get("track_condition"), row.get("measurement_distance"), row.get("measurement_kind"))
        if other == group and day(row["training_date"]) < day(target["training_date"]) and day(row["race_date"]) < day(target["race_date"]):
            values.append(float(row["time_seconds"]))
    return {"count": len(values), "mean_seconds": statistics.mean(values) if values else None,
        "std_seconds_sample": statistics.stdev(values) if len(values) > 1 else None,
        "faster_or_equal_percentile": sum(value <= target["time_seconds"] for value in values) / len(values) if values else None,
        "reference_policy": "strictly prior workout and prior race; verified A-only; same course/condition/distance/kind; no global fallback"}
