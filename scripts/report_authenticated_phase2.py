"""Assemble and seal the bounded Phase 2 pilot only after explicit --finalize.

Offline analysis reads immutable source projections and the frozen audit DB.
All outputs are exclusive creations. No browser, application DB, collector,
model, historical artifact, or prior parser file is modified.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

import analyze_authenticated_phase2 as parser
from ingest_authenticated_phase2 import canonical_bytes, decode_packet, scan_sanitized
from plan_authenticated_phase2 import PRIOR, PRIOR_MANIFEST_SHA256
from training_reconstruction_common import ROOT, SNAPSHOT, invariant_projection, now, protection, read, sha, write
from verify_market_segments_outputs import verify_files

OUT = ROOT / "reports/generated/model-experiments/netkeiba-authenticated-phase2-v1"
REQUIRED = (
    "phase2_pilot_plan.json", "training_current_probe_expanded.csv",
    "training_historical_probe_expanded.csv", "training_course_mapping_evidence.csv",
    "training_intermediate_workout_audit.json", "speed_current_db_comparison_expanded.csv",
    "speed_historical_probe.csv", "speed_field_definition_audit.json",
    "speed_formula_availability.json", "speed_master_probe.csv", "pit_forward_probe.csv",
    "source_hash_manifest.json", "feature_readiness_registry.json", "backfill_readiness.json",
    "summary.md", "audit.md", "artifact_manifest.json",
)
GENERATED = tuple(n for n in REQUIRED if n not in ("phase2_pilot_plan.json", "artifact_manifest.json")) + (
    "report_metrics.json", "protection-after.json", "security-check.json", "speed_master_audit.json")
TRAINING_COLUMNS = (
    "race_id", "horse_number", "horse_name", "training_date", "course", "track_condition", "rider",
    "training_sequence", "slot_ordinal", "displayed_distance_label", "displayed_time",
    "displayed_lap_interval", "displayed_lap_time", "pace", "grade", "comment_exists",
    "old_time_field", "old_time_value", "old_lap_field", "old_lap_value",
    "old_classification", "new_classification", "mapping_verified", "missing_kind",
    "source_page_type", "fetched_at", "source_projection_sha256",
)
HISTORICAL_COLUMNS = (
    "year", "race_id", "planned_race_date", "source_race_date", "market_h04_cohort_member",
    "attempted", "page_exists", "horses_visible", "training_events", "training_dates_visible",
    "courses", "numeric_clock_slots", "numeric_lap_slots", "pace_visible", "grade_visible",
    "comments_visible", "source_missing_slots", "status", "historical_availability_class",
    "strict_pit", "annual_availability_rate_inferred", "old_parser_comparison",
    "source_page_type", "fetched_at", "source_projection_sha256", "source_url", "reason",
)
SPEED_COLUMNS = (
    "race_id", "horse_number", "horse_name", "horse_id", "field", "display_value", "db_value",
    "identity_status", "relation", "compared", "source_available", "source_projection_sha256", "capture_mode",
)
SPEED_HISTORICAL_COLUMNS = (
    "year", "race_id", "planned_race_date", "source_race_date", "market_h04_cohort_member", "attempted",
    "page_exists", "horses_visible", "seven_field_cells", "numeric_cells", "missing_cells",
    "past_indices_visible", "distance_index_available", "course_index_available",
    "status", "historical_availability_class", "strict_pit", "annual_availability_rate_inferred",
    "fetched_at", "source_projection_sha256", "source_url", "reason",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def csv_file(name, columns, rows):
    with (OUT / name).open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list, tuple))
                             else value for key, value in row.items()})


def markdown(name, text):
    with (OUT / name).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def verify_prior_and_protection():
    require(sha(PRIOR / "artifact_manifest.json") == PRIOR_MANIFEST_SHA256, "prior pilot manifest changed")
    old = read(PRIOR / "artifact_manifest.json")
    verified = {"files": verify_files(PRIOR, old["files"]),
                "code_and_tests": verify_files(ROOT, old["code_and_tests_sha256"])}
    state = protection()
    before = read(OUT / "protection-before.json")["state"]
    require(invariant_projection(before) == invariant_projection(state), "protected state changed since Phase 2 baseline")
    return state, verified


def navigation_receipt():
    manual_path = OUT / "manual-navigation-reservation.json"
    manual = read(manual_path) if manual_path.exists() else {"count": 0, "lease_released": True}
    require(manual.get("lease_released") is True, "manual browser lease remains outstanding")
    admissions = [read(p) for p in sorted((OUT / "navigation").glob("*-admission.json"))]
    passed = [row for row in admissions if row.get("admitted")]
    for row in passed:
        release = OUT / "navigation" / f"{row['seq']:03d}-released.json"
        require(release.is_file(), "browser navigation lease remains outstanding")
        require(read(release)["owner"] == row["owner"], "navigation release owner mismatch")
    reserved = manual["count"] + len(passed)
    require(reserved <= read(OUT / "phase2_pilot_plan.json")["page_budget"]["hard_max_browser_navigations"],
            "hard navigation ceiling exceeded")
    timing_path = OUT / "navigation-timing-deviations.json"
    timing = read(timing_path) if timing_path.is_file() else None
    if timing:
        verify_files(OUT, timing["source_files_sha256"])
    return {"reserved_browser_navigations": reserved, "manual_reservations": manual["count"],
            "admitted_gate_reservations": len(passed), "blocked_admission_attempts": len(admissions) - len(passed),
            "hard_limit": 48, "all_own_leases_released": True, "actual_http_request_count": None,
            "count_scope": "Reserved browser navigations, including known unused reservations and attempted/failed navigation; not browser subresource HTTP count",
            "known_unused_admission_reservations": timing.get("known_unused_admission_reservations", 0) if timing else None,
            "executed_action_timing_deviations": timing.get("executed_action_timing_deviations", 0) if timing else None,
            "admission_action_timing": "DEVIATION_OBSERVED" if timing and timing["executed_action_timing_deviations"] else "NOT_INDEPENDENTLY_AUDITED",
            "timing_audit_file": timing_path.relative_to(OUT).as_posix() if timing else None,
            "timing_audit_sha256": sha(timing_path) if timing else None}


def test_receipt():
    paths = list(OUT.glob("parser-test-receipt*.json"))
    require(paths, "final parser-test-receipt*.json required")
    path = max(paths, key=lambda p: (read(p).get("observed_at", read(p).get("recorded_at", "")), p.name))
    receipt = read(path)
    passed = receipt.get("passed", receipt.get("tests_passed"))
    require(isinstance(passed, int) and passed >= 114, "focused test receipt must document at least 114 passed tests")
    require(receipt.get("failed", 0) == 0, "focused test failures remain")
    for key in ("code_and_tests_sha256", "files_sha256", "source_sha256"):
        if isinstance(receipt.get(key), dict):
            verify_files(ROOT, receipt[key])
    return {"source_file": path.relative_to(OUT).as_posix(), "sha256": sha(path),
            "passed": passed, "failed": 0, "command": receipt.get("command"),
            "scope": "Focused parser/transport and prior pilot regression tests; not model quality or full repository suite"}


def load_captures():
    captures, hashes = [], []
    paths = sorted([*(OUT / "captures").glob("*.json"), *(OUT / "captures/master").glob("*.json"),
                    *(OUT / "captures/intermediate").glob("*.json")])
    require(bool(paths), "no saved source captures")
    require(len(paths) <= 48, "too many source capture files for bounded pilot")
    for path in paths:
        raw = path.read_bytes()
        capture = json.loads(raw.decode("utf-8-sig"))
        scan_sanitized(capture)
        digest = sha(path)
        packet_path = OUT / "transport" / path.relative_to(OUT / "captures")
        transport = None
        if packet_path.exists():
            packet = read(packet_path)
            decoded, decoded_value = decode_packet(packet)
            require(decoded == raw and decoded_value == capture, "transport/capture canonical bytes mismatch")
            transport = {"path": packet_path.relative_to(OUT).as_posix(), "sha256": sha(packet_path),
                         "codec": packet["codec"], "canonical_transfer_verified": True}
        require(isinstance(capture, dict) and isinstance(capture.get("race_id"), str), "unsupported capture shape")
        captures.append({"capture": capture, "sha256": digest, "source_file": path.relative_to(OUT).as_posix()})
        hashes.append({"source_file": path.relative_to(OUT).as_posix(), "sha256": digest, "bytes": len(raw),
                       "race_id": capture["race_id"], "source_page_type": capture.get("source_page_type"),
                       "source_variant": capture.get("source_variant", "standard"),
                       "source_url": capture.get("source_url"), "fetched_at": capture.get("fetched_at", capture.get("observed_at")),
                       "source_kind": capture.get("source_kind", "sanitized_browser_DOM_projection"),
                       "raw_http_available": False, "transport": transport})
    return captures, hashes


def load_historical_projections():
    paths = sorted((OUT / "captures/historical").glob("*.json"))
    if not paths and (OUT / "historical-projections.json").is_file():
        paths = [OUT / "historical-projections.json"]
    if not paths:
        return [], []
    result, hashes, keys = [], [], set()
    for path in paths:
        body = read(path)
        if isinstance(body, list):
            rows, pointer = body, ""
        else:
            require(body.get("schema") == "phase2-historical-availability-batch-v1", "unknown historical bundle schema")
            rows, pointer = body.get("records"), "/records"
        require(isinstance(rows, list) and len(result) + len(rows) <= 33, "historical projection list exceeds predeclared bound")
        bundle_hash = sha(path)
        transport_path = OUT / "transport/historical" / path.name
        transport = None
        if transport_path.is_file():
            packet = read(transport_path)
            decoded, decoded_value = decode_packet(packet)
            require(decoded == path.read_bytes() and decoded_value == body, "historical transport mismatch")
            transport = {"path": transport_path.relative_to(OUT).as_posix(), "sha256": sha(transport_path),
                         "codec": packet["codec"], "canonical_transfer_verified": True}
        for index, item in enumerate(rows):
            require(isinstance(item, dict), "historical projection must be an object")
            scan_sanitized(item)
            require(item.get("schema") in (None, "phase2-historical-availability-projection-v1"), "unknown projection schema")
            require(item.get("capture_mode") == "historical" and item.get("raw_http_available") is False,
                    "historical availability projection must be explicitly historical and sanitized")
            require(item.get("source_page_type") in ("training", "speed"), "unexpected historical page type")
            require(type(item.get("rows")) is int and 0 <= item["rows"] <= 100, "invalid visible row count")
            digest = hashlib.sha256(canonical_bytes(item)).hexdigest()
            metadata = parser.capture_metadata(item, digest)
            key = (item["race_id"], item["source_page_type"])
            require(key not in keys, "duplicate historical race/page projection")
            keys.add(key)
            result.append({"capture": item, "sha256": digest, "metadata": metadata})
            hashes.append({"source_file": path.relative_to(OUT).as_posix(), "sha256": bundle_hash,
                           "json_pointer": f"{pointer}/{index}", "projection_fragment_sha256": digest,
                           "fragment_hash_basis": "Canonical exact saved summary; source bundle transport is verified separately when provided",
                           "race_id": item["race_id"], "source_page_type": item["source_page_type"],
                           "source_url": item["source_url"], "fetched_at": metadata["fetched_at"],
                           "source_kind": "historical_availability_summary_DOM_projection", "raw_http_available": False,
                           "per_horse_values_preserved": False, "bytes": len(canonical_bytes(item)), "transport": transport})
    return result, hashes


def analyze(captures):
    training, speed, pit, masters = [], [], [], []
    for source in captures:
        capture, digest = source["capture"], source["sha256"]
        kind = capture.get("source_page_type")
        if capture.get("source_variant") == "master" or kind in ("speed_master", "master"):
            parser.capture_metadata(capture, digest)
            masters.append(source)
        elif kind in ("training", "training_intermediate"):
            if capture.get("table_html"):
                contract, crosscheck = parser.build_capture_mapping_contract(capture, digest, source["source_file"])
                result = parser.analyze_training_html(capture, digest, contract, OUT)
                result["mapping_contract"] = contract
                result["crosscheck"] = crosscheck
            else:
                result = parser.analyze_training_projection(capture, digest)
            result["source_capture"] = capture
            result["source_file"] = source["source_file"]
            training.append(result)
            numeric = any(slot["cumulative_value"] is not None for row in result["records"] for slot in row["slots"])
            pit.append({**parser.evaluate_forward_pit(capture, digest, numeric), "values_source_file": source["source_file"]})
        elif kind == "speed":
            rows = parser.parse_speed_projection(capture, digest)
            speed.append({"records": rows, "source_capture": capture, "source_file": source["source_file"],
                          "sha256": digest})
            numeric = any(row[field] is not None for row in rows for field in parser.FIELDS)
            pit.append({**parser.evaluate_forward_pit(capture, digest, numeric), "values_source_file": source["source_file"]})
        else:
            raise ValueError("unsupported source_page_type: " + str(kind))
    return training, speed, pit, masters


def training_slot_rows(training):
    rows = []
    for result in training:
        if (result["metadata"]["capture_mode"] != "forward" or
                result["metadata"]["source_page_type"] != "training" or
                "type=1" in result["source_capture"].get("source_url", "")):
            continue
        for comparison in result["comparison"]:
            slot = comparison["slot"]
            rows.append({**comparison, "course": comparison.get("course_raw"),
                         "slot_ordinal": slot["ordinal"]})
    return rows


def mapping_rows(training):
    rows = []
    for result in training:
        for comparison in result["comparison"]:
            rows.append({**comparison, "course": comparison.get("course_raw"),
                         "slot_ordinal": comparison["slot"]["ordinal"],
                         "scope": "EXACT_CAPTURE_ONLY", "historical_rollout_permitted": False})
    return rows


def training_historical_rows(plan, training, historical_summaries=()):
    captures = {r["metadata"]["race_id"]: r for r in training if r["metadata"]["capture_mode"] != "forward"}
    summaries = {r["capture"]["race_id"]: r for r in historical_summaries if r["capture"]["source_page_type"] == "training"}
    rows = []
    for target in plan["selections"]["historical_training"]:
        row = {"year": int(target["race_date"][:4]), "race_id": target["race_id"],
               "planned_race_date": target["race_date"], "market_h04_cohort_member": target["market_h04_cohort_member"],
               "attempted": False, "status": "NOT_CAPTURED", "historical_availability_class": "UNKNOWN",
               "strict_pit": False, "annual_availability_rate_inferred": False,
               "reason": "No saved projection for this fixed sample"}
        result = captures.get(target["race_id"])
        if result:
            records = result["records"]
            source = result["source_capture"]
            slots = [slot for record in records for slot in record["slots"]]
            clocks = sum(slot["cumulative_value"] is not None for slot in slots)
            row.update({**result["metadata"], "attempted": True, "page_exists": source.get("table_present"),
                        "source_race_date": source.get("race_date_visible", source.get("race_date")),
                        "horses_visible": len({r["horse_number"] for r in records}), "training_events": len(records),
                        "training_dates_visible": sum(bool(r.get("training_date")) for r in records),
                        "courses": dict(Counter(r["course_raw"] for r in records)), "numeric_clock_slots": clocks,
                        "numeric_lap_slots": sum(s["lap_value"] is not None for s in slots),
                        "pace_visible": sum(bool(r.get("pace_raw")) for r in records),
                        "grade_visible": sum(bool(r.get("grade_raw")) for r in records),
                        "comments_visible": sum(bool(r.get("comment_exists")) for r in records),
                        "source_missing_slots": sum(s["is_placeholder"] for s in slots),
                        "status": "CLOCKS_DISPLAYED" if clocks else "CAPTURED_NO_NUMERIC_CLOCKS",
                        "historical_availability_class": "DISPLAY_ONLY" if clocks else "UNKNOWN",
                        "old_parser_comparison": result["summary"].get("old_parser_comparison"), "reason": ""})
        elif target["race_id"] in summaries:
            source = summaries[target["race_id"]]
            capture = source["capture"]
            counts = capture.get("counts", {})
            clock_horses = counts.get("horses_with_numeric", 0)
            date_matches = capture.get("race_date_visible") == target["race_date"]
            row.update({**source["metadata"], "attempted": True, "page_exists": capture.get("table_present"),
                        "source_race_date": capture.get("race_date_visible"), "horses_visible": len(set(capture.get("horse_numbers", []))),
                        "training_events": capture["rows"], "training_dates_visible": counts.get("training_date"),
                        "courses": capture.get("courses", {}), "numeric_clock_slots": None, "numeric_lap_slots": None,
                        "pace_visible": counts.get("pace"), "grade_visible": counts.get("grade"),
                        "comments_visible": counts.get("comment_exists"), "source_missing_slots": None,
                        "status": "SOURCE_DATE_CONFLICT" if not date_matches else "CLOCKS_DISPLAYED_SUMMARY_ONLY" if clock_horses else "NO_NUMERIC_CLOCKS_IN_PROJECTION",
                        "historical_availability_class": "DISPLAY_ONLY" if clock_horses and date_matches else "UNKNOWN",
                        "old_parser_comparison": "NOT_RUN_SUMMARY_PROJECTION_ONLY",
                        "reason": "Counts/patterns only; numeric slot values are not preserved. No table is UNKNOWN, not annual NONE.",
                        "horses_with_numeric": clock_horses, "horses_with_laps": counts.get("horses_with_laps"),
                        "training_date_min": capture.get("training_date_min"), "training_date_max": capture.get("training_date_max"),
                        "slot_patterns": capture.get("slot_patterns", {}), "before_race": counts.get("before_race"),
                        "same_or_after_race": counts.get("same_or_after_race"), "per_horse_values_preserved": False})
        rows.append(row)
    return rows


def speed_historical_rows(plan, speed, historical_summaries=()):
    by_race = {x["source_capture"]["race_id"]: x for x in speed if x["source_capture"].get("capture_mode") != "forward"}
    summaries = {r["capture"]["race_id"]: r for r in historical_summaries if r["capture"]["source_page_type"] == "speed"}
    rows = []
    for target in plan["selections"]["historical_speed"]:
        row = {"year": int(target["race_date"][:4]), "race_id": target["race_id"],
               "planned_race_date": target["race_date"], "market_h04_cohort_member": target["market_h04_cohort_member"],
               "attempted": False, "status": "NOT_CAPTURED", "historical_availability_class": "UNKNOWN",
               "strict_pit": False, "annual_availability_rate_inferred": False,
               "reason": "No saved projection for this fixed sample"}
        source = by_race.get(target["race_id"])
        if source:
            capture, records = source["source_capture"], source["records"]
            values = [record[field] for record in records for field in parser.FIELDS]
            row.update({"attempted": True, "page_exists": capture.get("table_present"),
                        "source_race_date": capture.get("race_date_visible", capture.get("race_date")),
                        "horses_visible": len(records), "seven_field_cells": len(values),
                        "numeric_cells": sum(v is not None for v in values), "missing_cells": sum(v is None for v in values),
                        "past_indices_visible": any(r[f] is not None for r in records for f in ("index_last", "index_2ago", "index_3ago")),
                        "distance_index_available": any(r["dist_max_index"] is not None for r in records),
                        "course_index_available": any(r["course_max_index"] is not None for r in records),
                        "status": "INDICES_DISPLAYED" if any(v is not None for v in values) else "CAPTURED_NO_NUMERIC_INDICES",
                        "historical_availability_class": "DISPLAY_ONLY" if any(v is not None for v in values) else "UNKNOWN",
                        "fetched_at": capture.get("fetched_at", capture.get("observed_at")),
                        "source_projection_sha256": source["sha256"], "source_url": capture.get("source_url"), "reason": ""})
        elif target["race_id"] in summaries:
            source = summaries[target["race_id"]]
            capture = source["capture"]
            fields = capture.get("fields", {})
            numeric = sum(v.get("numeric", 0) for v in fields.values())
            date_matches = capture.get("race_date_visible") == target["race_date"]
            row.update({**source["metadata"], "attempted": True, "page_exists": capture.get("table_present"),
                        "source_race_date": capture.get("race_date_visible"), "horses_visible": len(set(capture.get("horse_numbers", []))),
                        "seven_field_cells": sum(v.get("present", 0) for v in fields.values()), "numeric_cells": numeric,
                        "missing_cells": sum(v.get("missing", 0) for v in fields.values()),
                        "past_indices_visible": any(fields.get(f, {}).get("numeric", 0) > 0 for f in ("sk__index1", "sk__index2", "sk__index3")),
                        "distance_index_available": fields.get("sk__max_distance_index", {}).get("numeric", 0) > 0,
                        "course_index_available": fields.get("sk__max_course_index", {}).get("numeric", 0) > 0,
                        "status": "SOURCE_DATE_CONFLICT" if not date_matches else "INDICES_DISPLAYED_SUMMARY_ONLY" if numeric else "NO_NUMERIC_INDICES_IN_PROJECTION",
                        "historical_availability_class": "DISPLAY_ONLY" if numeric and date_matches else "UNKNOWN",
                        "reason": "Field availability counts only; per-horse numeric values not retained or compared with DB",
                        "field_counts": fields, "per_horse_values_preserved": False})
        rows.append(row)
    return rows


def current_speed_comparison(plan, speed):
    ids = {row["race_id"] for row in plan["selections"]["current_db_speed"]}
    records = [r for source in speed if source["source_capture"]["race_id"] in ids for r in source["records"]]
    if not records:
        return [], {"source_races": 0, "source_horses": 0, "compared_cells": 0, "exact_match": 0,
                    "both_missing": 0, "mismatch": 0, "exact_match_rate": None, "per_field": {},
                    "source_relation": "UNKNOWN", "reason": "No current-DB comparison captures saved"}, None
    stored, identities, receipt = parser.read_frozen_rows(SNAPSHOT, parser.legacy_speed.SNAPSHOT_SHA256,
                                                        sorted({r["race_id"] for r in records}))
    comparisons, summary = parser.compare_speed(records, stored, identities)
    return comparisons, summary, receipt


def master_probe_rows(masters):
    expected = ("sk__max_index", "sk__start_index", "sk__chase_index", "sk__last_index",
                "sk__average_index", "sk__max_distance_index", "sk__max_course_index",
                "sk__index1", "sk__index2", "sk__index3")
    rows, probes = [], []
    for source in masters:
        capture, digest = source["capture"], source["sha256"]
        require(capture.get("schema") == "phase2-master-dom-projection-v1", "unsupported Master source schema")
        require(capture.get("source_variant") == "master", "Master source variant required")
        require(capture.get("capture_mode") == "historical", "Master PIT interpretation currently supports historical captures only")
        metadata = parser.capture_metadata(capture, digest)
        order = capture.get("field_order")
        require(order == list(expected), "exact observed ten-field Master order required")
        source_rows = capture.get("rows")
        require(isinstance(source_rows, list) and len(source_rows) <= 18, "bounded Master runner rows required")
        seen, numeric, missing, other = set(), 0, 0, 0
        for record in source_rows:
            number = record.get("horse_number")
            require(type(number) is int and 1 <= number <= 18 and number not in seen, "invalid or duplicate Master horse number")
            require(isinstance(record.get("horse_name"), str) and record["horse_name"].strip(), "Master horse name missing")
            seen.add(number)
            values = record.get("display_values")
            require(isinstance(values, list) and len(values) == len(expected) and all(isinstance(v, str) for v in values),
                    "ten exact displayed Master strings required")
            for field, text in zip(order, values):
                normalized = text.strip()
                match = re.fullmatch(r"(-?\d+)([＊*※]?)", normalized)
                value = int(match.group(1)) if match else None
                is_missing = normalized in ("", "-", "－", "—", "―", "未", "**", "***")
                numeric += value is not None
                missing += is_missing
                other += value is None and not is_missing
                rows.append({**metadata, "source_variant": "master", "source_page_type": "speed_master",
                             "parser_version": "phase2-master-display-report-v1", "horse_number": number,
                             "horse_name": record["horse_name"], "field": field, "display_value": text,
                             "parsed_integer": value, "display_marker": match.group(2) if match else None,
                             "source_missing": is_missing, "pit_class": "HISTORICAL_DISPLAY_ONLY",
                             "value_semantics": "Provider-displayed past-performance aggregate or prior-performance index; exact aggregation rules remain unverified",
                             "target_race_prediction_claimed": False, "target_race_result_claimed": False,
                             "strict_pit": False, "values_source_file": source["source_file"]})
        probes.append({"race_id": capture["race_id"], "source_file": source["source_file"],
                       "source_projection_sha256": digest, "source_url": metadata["source_url"],
                       "fetched_at": metadata["fetched_at"], "source_race_date": capture.get("race_date_visible"),
                       "heading": capture.get("heading"), "headers": capture.get("headers"),
                       "horses": len(source_rows), "fields_per_horse": len(expected), "numeric_cells": numeric,
                       "source_missing_cells": missing, "unparsed_cells": other,
                       "reference_section": capture.get("reference_section"),
                       "reference_section_interpretation": "UNRESOLVED_REFERENCE_THRESHOLDS; not asserted to be target-race predictions or realized target-race performance",
                       "observed_view": "Master table in the entry-side view", "index_results_tab_clicked": False,
                       "view_evidence_basis": "Root agent's direct UI observation; no results tab navigation reported",
                       "strict_pit": False, "pit_reason": "Historical race page captured after its scheduled date"})
    audit = {"schema": "phase2-master-display-audit-v1", "recorded_at": now(),
             "availability": "YES" if rows else "PARTIAL" if masters else "UNKNOWN",
             "availability_scope": "Numeric Master display observed in this exact sample only; not all-year coverage or feature readiness",
             "probes": probes, "horses": sum(p["horses"] for p in probes), "display_cells": len(rows),
             "numeric_cells": sum(p["numeric_cells"] for p in probes),
             "field_interpretations": {"sk__max_index": "Master maximum display; do not import the standard-table lookback without verification",
                 "sk__start_index": "Official Master guide associates start with opening two furlongs",
                 "sk__chase_index": "Official Master guide associates chase with the first half of race distance",
                 "sk__last_index": "Official Master guide associates closing with final three furlongs",
                 "remaining_fields": "Retain source class and displayed strings; Master aggregation, lookbacks and prior-start eligibility not established here"},
             "standard_speed_db_comparison_includes_master": False, "master_and_standard_values_conflated": False,
             "raw_http_available": False, "strict_pit": False, "model_input_permitted": False,
             "reference_thresholds_are_model_predictions": "NOT_ESTABLISHED",
             "reference_thresholds_are_target_race_outcomes": "NOT_ESTABLISHED",
             "official_vs_current_ui_scope_conflict": "UNRESOLVED_SCOPE_CONFLICT"}
    return rows, audit


def intermediate_audit(training, definitions):
    probes = []
    for result in training:
        source = result["source_capture"]
        if source.get("source_page_type") != "training_intermediate" and "type=1" not in source.get("source_url", ""):
            continue
        counts = Counter(r["horse_number"] for r in result["records"])
        probes.append({"race_id": source["race_id"], "source_file": result["source_file"],
                       "source_projection_sha256": result["metadata"]["source_projection_sha256"],
                       "selected_view": source.get("selected_view"), "events": len(result["records"]),
                       "horses": len(counts), "horses_with_multiple_events": sum(v > 1 for v in counts.values()),
                       "training_dates": sorted({r["training_date"] for r in result["records"] if r.get("training_date")})})
    status = "YES" if any(p["horses_with_multiple_events"] for p in probes) else "PARTIAL" if probes else "UNKNOWN"
    return {"status": status, "probes": probes,
            "official_documentation": definitions["training_intermediate_documentation"],
            "schema": {"event_key": ["race_id", "horse_number", "source_projection_sha256", "training_sequence"],
                       "fields": ["training_date", "days_before_race", "training_type", "is_final_workout_verified"],
                       "training_sequence_basis": "SOURCE_DISPLAY_ORDER", "is_last_training_equals_1_used": False},
            "scope_warning": "No intermediate event visible in one race page does not establish nonexistence",
            "model_input_permitted": False}


def availability(rows):
    return "PARTIAL" if any(r.get("historical_availability_class") == "DISPLAY_ONLY" for r in rows) else "UNKNOWN"


def finalize():
    require(not (OUT / "artifact_manifest.json").exists(), "Phase 2 manifest already sealed")
    for name in GENERATED:
        require(not (OUT / name).exists(), "final output exists; refuse overwrite: " + name)
    state, prior_verified = verify_prior_and_protection()
    nav = navigation_receipt()
    tests = test_receipt()
    plan = read(OUT / "phase2_pilot_plan.json")
    definitions = read(OUT / "definitions/speed_field_definition_audit.json")
    conflict_path = OUT / "definitions/master-scope-conflict.json"
    if conflict_path.is_file():
        definitions["master_scope_conflict_addendum"] = {"source_file": conflict_path.relative_to(OUT).as_posix(),
                                                        "sha256": sha(conflict_path), "audit": read(conflict_path)}
    formula = read(OUT / "definitions/speed_formula_availability.json")
    captures, hashes = load_captures()
    historical_summaries, historical_hashes = load_historical_projections()
    training, speed, pit, masters = analyze(captures)
    current_rows = training_slot_rows(training)
    course_rows = mapping_rows(training)
    historical_training = training_historical_rows(plan, training, historical_summaries)
    historical_speed = speed_historical_rows(plan, speed, historical_summaries)
    speed_comparisons, speed_summary, db_receipt = current_speed_comparison(plan, speed)
    intermediate = intermediate_audit(training, definitions)
    forward = [row for row in pit if row["capture_mode"] == "forward"]
    verified_forward = [row for row in forward if row["strict_pit_forward_sample"]]
    current_training = [r for r in training if r["metadata"]["capture_mode"] == "forward"
                        and r["metadata"]["source_page_type"] == "training"
                        and "type=1" not in r["source_capture"].get("source_url", "")]
    counts = Counter(row.get("old_classification") or "UNCLASSIFIED" for row in current_rows)
    new_counts = Counter(row.get("new_classification") or "UNCLASSIFIED" for row in current_rows)
    master_rows, master_audit = master_probe_rows(masters)
    years = {}
    for year in (2016, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025):
        tr = [r for r in historical_training if r["year"] == year]
        sp = [r for r in historical_speed if r["year"] == year]
        years[str(year)] = {"training": "DISPLAY_ONLY" if any(r["historical_availability_class"] == "DISPLAY_ONLY" for r in tr) else "UNKNOWN",
                            "training_new_races_with_clocks": sum(r["historical_availability_class"] == "DISPLAY_ONLY" for r in tr),
                            "speed": "DISPLAY_ONLY" if any(r["historical_availability_class"] == "DISPLAY_ONLY" for r in sp) else "UNKNOWN",
                            "speed_new_races_with_indices": sum(r["historical_availability_class"] == "DISPLAY_ONLY" for r in sp),
                            "annual_availability_rate": None, "sample_only": True}
    metrics = {
        "generated_at": now(), "initial_status": "PARTIAL_PILOT", "status": "PARTIAL_PILOT_EXPANDED",
        "phase_complete": False, "current_training_new_races": len({r["metadata"]["race_id"] for r in current_training}),
        "current_training_events": sum(len(r["records"]) for r in current_training),
        "current_training_slots": len(current_rows), "old_parser_classification_counts": dict(counts),
        "new_parser_classification_counts": dict(new_counts), "current_mapping": "PARTIAL" if current_rows else "NO",
        "historical_training_availability": availability(historical_training), "historical_speed_availability": availability(historical_speed),
        "2021_COVERAGE_PATH": "PARTIAL" if years["2021"]["training_new_races_with_clocks"] else "NO",
        "2022_COVERAGE_PATH": "PARTIAL" if years["2022"]["training_new_races_with_clocks"] else "NO",
        "2021_SPEED_AVAILABLE": "PARTIAL" if years["2021"]["speed_new_races_with_indices"] else "NO",
        "2022_SPEED_AVAILABLE": "PARTIAL" if years["2022"]["speed_new_races_with_indices"] else "NO",
        "coverage_no_meaning": "NO means the required new fixed-cohort evidence was not established, not proof that all provider pages are unavailable",
        "year_sample_evidence": years, "speed_current_db": speed_summary,
        "prior_speed_reference": {"races": 1, "compared_cells": 126, "exact_integer": 114, "both_missing": 12,
                                  "mismatch": 0, "scope": "Prior sealed pilot; excluded from new Phase 2 counts"},
        "cumulative_speed_sample": {"races": 1 + speed_summary["source_races"],
                                    "compared_cells": 126 + speed_summary["compared_cells"],
                                    "exact_integer": 114 + speed_summary["exact_match"],
                                    "both_missing": 12 + speed_summary["both_missing"], "mismatch": speed_summary["mismatch"],
                                    "scope": "Separate cumulative reference across prior and new samples; no population inference"},
        "CURRENT_DB_SOURCE_RELATION": "UNKNOWN", "source_relation_scope": "Exact display equivalence in sampled rows is not a global causal provenance proof",
        "strict_pit_forward_source_captures": len(verified_forward),
        "strict_pit_forward_unique_races": len({p["race_id"] for p in verified_forward}),
        "strict_pit_forward_both_training_and_speed_races": sum(
            {p["source_page_type"] for p in verified_forward if p["race_id"] == race} >= {"training", "speed"}
            for race in {p["race_id"] for p in verified_forward}),
        "strict_pit_historical": 0, "intermediate_training_available": intermediate["status"],
        "speed_master_available": master_audit["availability"],
        "speed_master_numeric_cells": master_audit["numeric_cells"],
        "speed_master_horses": master_audit["horses"],
        "speed_formula": "PROPRIETARY", "navigation": nav, "tests": tests,
        "source_capture_count": len(captures), "historical_summary_projections": len(historical_summaries), "RAW_HTTP_AVAILABLE": False,
        "TRAINING_FEATURE_RESEARCH_READY": False, "SPEED_FEATURE_RESEARCH_READY": False,
        "MODEL_PRODUCTION_READY": False, "BACKFILL_PILOT_READY": False,
        "database_writes": 0, "model_fits": 0, "bulk_backfill_started": False,
        "database_write_scope": "Research/production data DBs; shared pacing lease coordination writes are separate and expected",
        "committed_or_pushed": False,
    }
    # Validate everything above before producing any final report file.
    for entry in hashes + historical_hashes:
        require(sha(OUT / entry["source_file"]) == entry["sha256"], "capture changed during assembly")
    csv_file("training_current_probe_expanded.csv", TRAINING_COLUMNS, current_rows)
    csv_file("training_course_mapping_evidence.csv", TRAINING_COLUMNS + ("scope", "historical_rollout_permitted"), course_rows)
    csv_file("training_historical_probe_expanded.csv", HISTORICAL_COLUMNS + (
        "horses_with_numeric", "horses_with_laps", "training_date_min", "training_date_max", "slot_patterns",
        "before_race", "same_or_after_race", "per_horse_values_preserved"), historical_training)
    write(OUT / "training_intermediate_workout_audit.json", intermediate)
    csv_file("speed_current_db_comparison_expanded.csv", SPEED_COLUMNS, speed_comparisons)
    csv_file("speed_historical_probe.csv", SPEED_HISTORICAL_COLUMNS + ("field_counts", "per_horse_values_preserved"), historical_speed)
    write(OUT / "speed_field_definition_audit.json", definitions)
    write(OUT / "speed_formula_availability.json", formula)
    csv_file("speed_master_probe.csv", ("race_id", "horse_number", "horse_name", "field", "display_value", "pit_class",
             "parsed_integer", "display_marker", "source_missing", "value_semantics", "source_variant",
             "target_race_prediction_claimed", "target_race_result_claimed", "source_page_type", "fetched_at",
             "source_projection_sha256", "source_url", "strict_pit", "values_source_file", "parser_version"), master_rows)
    write(OUT / "speed_master_audit.json", master_audit)
    csv_file("pit_forward_probe.csv", ("race_id", "source_page_type", "fetched_at", "scheduled_start_time",
             "race_date_visible", "scheduled_start_time_visible", "fetched_before_scheduled_start", "strict_pit_forward_sample",
             "pit_class", "parser_version", "source_projection_sha256", "source_url", "actual_start_time_verified",
             "revision_history_verified", "feature_leakage_policy_fixed", "values_source_file", "reasons"), forward)
    write(OUT / "source_hash_manifest.json", {"schema": "phase2-source-projections-v1", "recorded_at": now(),
          "sources": hashes + historical_hashes, "RAW_HTTP_AVAILABLE": False,
          "hash_scope": "Saved sanitized DOM projections and transport packets, not raw HTTP responses or publisher attestations",
          "official_definition_notes": {"path": "definitions/official_source_notes.md", "sha256": sha(OUT / "definitions/official_source_notes.md")},
          "frozen_db_receipt": db_receipt, "historical_capture_is_contemporary_publication_proof": False})
    write(OUT / "feature_readiness_registry.json", {
        "TRAINING_FEATURE_RESEARCH_READY": "NO", "SPEED_FEATURE_RESEARCH_READY": "NO", "MODEL_PRODUCTION_READY": "NO",
        "training_candidates": {"READY": 0, "HOLD": 19, "REJECT": 1},
        "training_blockers": ["Historical mapping is not demonstrated by current HTML", "Unsupported course or layout cases remain",
                              "Research-period coverage path is sample evidence only", "Missing-value and leakage policies are not fixed"],
        "speed_blockers": ["Official average semantics conflict", "Distance/course and prior-start inclusion rules unresolved",
                           "Row-specific historical publication/version evidence absent", "Insufficient coverage and leakage policy"],
        "historical_availability_by_year": years, "annual_coverage_inference_permitted": False,
        "CURRENT_DB_SOURCE_RELATION": "UNKNOWN", "sample_match_metrics": speed_summary,
        "forward_capture_evidence": {"captures": len(verified_forward), "races": metrics["strict_pit_forward_unique_races"],
                                      "scope": "Observed before displayed scheduled start; actual start and complete revision history unverified"},
        "prior_2021_training_cohort_member": False, "prior_2022_training_cohort_member": True,
        "H04_correlation_and_rank_agreement": "DEFERRED_INSUFFICIENT_SAMPLE", "models_fitted": 0})
    write(OUT / "backfill_readiness.json", {"BACKFILL_PILOT_READY": "NO", "bulk_backfill_started": False,
          "bulk_backfill_separate_authorization_required_even_if_gate_yes": True,
          "checks": {"terms_scope_for_bulk": "NOT_ESTABLISHED_BY_SMALL_PILOT", "source_stability": "LIMITED_SAMPLE_ONLY",
                     "parser_stability": "PARTIAL", "resume": "APPEND_ONLY_PILOT_ARTIFACTS_ONLY_NOT_BULK_COLLECTOR",
                     "request_pacing_design": "SINGLE_BROWSER_LANE_MINIMUM_3_SECONDS_SHARED_PACER",
                     "Track_A_priority": "PRESERVED_BY_ADMISSION_GATE", "target_period": "PREDECLARED_SMALL_SAMPLE",
                     "bulk_storage_estimate": "NOT_ESTABLISHED", "admission_to_browser_action_timing": nav["admission_action_timing"]},
          "operational_blockers": ["Navigation sequence 32 occurred about 55.6 seconds after admission, exceeding the declared 30-second action limit"]
                                  if nav["executed_action_timing_deviations"] else [],
          "storage_observed_bytes": sum((OUT / relative).stat().st_size for relative in {r["source_file"] for r in hashes + historical_hashes}),
          "estimate_caution": "Current projection bytes are measured; no all-race rowcount, request count or storage extrapolation is asserted"})
    write(OUT / "report_metrics.json", metrics)
    summary = f"""# Phase 2 限定パイロット

状態はPARTIAL_PILOT_EXPANDED。調教・Speedの研究再開、Production、大量backfillはいずれもNO。今回の追加は認証画面の意味・構造・時点を確かめる少数標本であり、年間coverage率を推定していない。

| 項目 | 判定 |
|---|---|
| A CURRENT_MAPPING | {metrics['current_mapping']} |
| B HISTORICAL_TRAINING_AVAILABILITY | {metrics['historical_training_availability']} |
| C 2021調教coverage path | {metrics['2021_COVERAGE_PATH']} |
| D 2022調教coverage path | {metrics['2022_COVERAGE_PATH']} |
| E INTERMEDIATE_TRAINING_AVAILABLE | {intermediate['status']} |
| F TRAINING_FEATURE_RESEARCH_READY | NO |
| G 今回のDB指数照合 | {speed_summary['compared_cells']}セル：整数一致{speed_summary['exact_match']}、双方欠損{speed_summary['both_missing']}、不一致{speed_summary['mismatch']}、一致率{speed_summary['exact_match_rate']} |
| H CURRENT_DB_SOURCE_RELATION | UNKNOWN（標本一致と全DBの来歴は別） |
| I HISTORICAL_SPEED_AVAILABILITY | {metrics['historical_speed_availability']} |
| J 2021指数availability | {metrics['2021_SPEED_AVAILABLE']} |
| K 2022指数availability | {metrics['2022_SPEED_AVAILABLE']} |
| L SPEED_FORMULA | PROPRIETARY |
| M SPEED_MASTER_AVAILABLE | {metrics['speed_master_available']} |
| N SPEED_FEATURE_RESEARCH_READY | NO |
| O STRICT_PIT_FORWARD_SAMPLE | {len(verified_forward)}画面capture／{metrics['strict_pit_forward_unique_races']}レース |
| P historical / forward の分離 | YES |
| Q BACKFILL_PILOT_READY | NO |

現行調教の追加は{metrics['current_training_new_races']}レース・{metrics['current_training_events']}event・{len(current_rows)}slot。旧parserの分類は{json.dumps(dict(counts), ensure_ascii=False)}。距離未確定slotをEXACTへ含めず、過去85,040行へ補正を適用していない。

202606040612では馬情報と調教が同じtr内にある構造のため、凍結した旧parserは0eventとなり80slotがSELECTOR_ERROR。新parserは16eventを保持した。このHTML構造差も一般化前に必要な検証であり、既存parserやDBを自動修正していない。

Speedの一致率は双方欠損を含む定義で、実値一致率もreport_metrics.jsonのfield別集計に別記する。旧pilotの126セル（整数114・双方欠損12）は今回の追加セル数へ混ぜていない。前向きcaptureは表示された予定発走時刻より前に保存した証拠であり、実際の発走時刻・改訂履歴や全特徴のleakage policyまで認証しない。

公式定義は[詳細メモ](definitions/official_source_notes.md)参照。平均は画面説明と公式FAQの不一致が残る。Masterの公表対象は2023年重賞・2024年以降の障害を除くレースで、2021・2022年の補完経路にはならない。

追加UI確認ではMaster対象が2025年以降という案内も表示され、公式FAQとの適用範囲の相違は未解決。ページ・版・モードの差を確定できず、片方を全画面へ一般化しない。

Masterは{master_audit['horses']}頭・{master_audit['display_cells']}表示cellを独立保存し、うち数値{master_audit['numeric_cells']}cell。参照欄の閾値は用途未確定として原表示を保持し、対象レースの予測値・実績値と断定しない。過去ページの後日取得でありSTRICT PIT=false。標準7項目のDB一致集計へ混ぜていない。

固定標本の未取得行はUNKNOWN。過去の表示成功もDISPLAY_ONLYであり、当時の版・公開時刻を証明しない。旧2021標本はMarket+H04 cohort外、旧2022標本と今回選定した両年各4レースはcohort内。この違いを維持する。

次の作業は最大3点：平均定義と距離・コース集計の公式確認、未解決のHTML構造と中間eventの限定検証、独立した前向き少数captureによる版・欠損・時点policyの固定。大量取得は開始しない。
"""
    markdown("summary.md", summary)
    markdown("audit.md", f"""# Phase 2 監査境界

旧pilot manifest {PRIOR_MANIFEST_SHA256} の{prior_verified['files']}成果物と{prior_verified['code_and_tests']}コードを再検証。固定DBはread-only/query-only/列authorizer付きで、指数比較に必要な識別情報と7指数だけを参照。結果・odds・label・モデル評価を対象選定へ使っていない。調教historicalはprojectionのみなら旧HTMLparserを実行したと報告しない。

Transportがあるcaptureはbrowser側canonical SHA-256、展開後JSON bytes、保存JSONが一致することを検証。これに発行元の真正性や過去の公開証明は含まれない。RAW_HTTP_AVAILABLE=false。資格情報・cookie・token・個人情報を保存する方式を使っていない。秘密patternの確認は全形式の不存在証明ではない。

Browser予算は予約{nav['reserved_browser_navigations']}/48。HTTP subresource件数は不明。公開FAQ参照にもnetworkがあるため全作業network0とは記載しない。本report組立自体はnetwork0。Track A優先、共通pacer、1lane、保護時間帯を適用し、既存collectorには変更を加えていない。正常なcollector進捗・heartbeatだけ保護一致条件から除外する。

ただし画面操作の時刻制約は完全遵守ではない。seq12は30秒を過ぎた予約を未使用のまま解放し、seq13で再取得した。旧releaseの汎用outcomeを実アクセスの証拠とせず、未使用予約も予算に残す。seq32のMaster切替はadmissionから約55.6秒後で、30秒制限を超過した。実操作時刻の概算と保存済みTrack A予定を照合すると保護時間帯との重複はなく、ルート担当も競合を観測していないが、逸脱そのものは取り消さない。navigation-timing-deviations.jsonに原receiptのhashと訂正を保存し、BACKFILL_PILOT_READY=NOの運用要因へ含めた。

DB書込0の対象は研究・ProductionのデータDB。共通request_pacing.dbの通常のlease取得・解放は認可された調整用書込であり、調教・指数データへの書込と混同しない。テストの一時的なsynthetic DB作成も本番データ変更とは区別する。

Focused testsは{tests['passed']} passed。集計単位は画面capture、レース、馬、event、slot、cellを分離。forwardは{len(verified_forward)}capture/{metrics['strict_pit_forward_unique_races']}レース、historical STRICT_PITは0。両者を混同しない。phase完了や本番投入とは判定しない。

学習・Optuna・ROI探索・実購入・既存DB補正・H04変更・Production変更・大量backfill・commit・pushはなし。成果物は新規作成のみで既存pilotを上書きしない。サンプルが得られない対象を0coverageとせずUNKNOWNとして残す。
""")
    after, after_prior = verify_prior_and_protection()
    require(after_prior == prior_verified, "prior verification count changed")
    for entry in hashes + historical_hashes:
        require(sha(OUT / entry["source_file"]) == entry["sha256"], "source changed before sealing")
    write(OUT / "protection-after.json", {"observed_at": now(), "state": after,
          "invariant_projection_matches": True, "prior_pilot_verified": after_prior,
          "prior_pilot_manifest_sha256": PRIOR_MANIFEST_SHA256,
          "collector_dynamic_state_excluded_from_equality": True, "no_protected_mutations": True})
    write(OUT / "security-check.json", {"recorded_at": now(), "source_projection_sanitization_scan": "PASS",
          "source_files_checked": sorted({s["source_file"] for s in hashes + historical_hashes}), "raw_http_available": False,
          "credential_or_account_data_exported": False,
          "limitations": "Allowlisted DOM minimization plus bounded patterns; not a proof against every possible secret format"})
    for name in REQUIRED[:-1]:
        require((OUT / name).is_file(), "missing required artifact: " + name)
    files = {p.relative_to(OUT).as_posix(): {"sha256": sha(p), "bytes": p.stat().st_size}
             for p in sorted(OUT.rglob("*")) if p.is_file() and p.suffix != ".lock" and p != OUT / "artifact_manifest.json"}
    code_paths = [ROOT / "scripts" / name for name in (
        "plan_authenticated_phase2.py", "phase2_browser_gate.py", "ingest_authenticated_phase2.py",
        "analyze_authenticated_phase2.py", "report_authenticated_phase2.py")]
    code_paths.append(ROOT / "python-api/tests/test_authenticated_phase2.py")
    code = {p.relative_to(ROOT).as_posix(): {"sha256": sha(p), "bytes": p.stat().st_size} for p in code_paths}
    manifest = {"schema": "netkeiba-authenticated-phase2-artifact-manifest-v1", "recorded_at": now(),
                "status": metrics["status"], "phase_complete": False, "required_artifacts": list(REQUIRED),
                "TRAINING_FEATURE_RESEARCH_READY": False, "SPEED_FEATURE_RESEARCH_READY": False,
                "MODEL_PRODUCTION_READY": False, "BACKFILL_PILOT_READY": False,
                "prior_pilot_manifest_sha256": PRIOR_MANIFEST_SHA256,
                "files": files, "code_and_tests_sha256": code,
                "navigation_lock_file_excluded": "navigation/admission.lock is operational synchronization state",
                "models_fitted": 0, "database_writes": 0, "bulk_backfill_started": False, "committed_or_pushed": False}
    write(OUT / "artifact_manifest.json", manifest)
    verify_files(OUT, files)
    verify_files(ROOT, code)
    return {"status": metrics["status"], "required_artifacts": len(REQUIRED),
            "verified_artifacts": len(files), "manifest_sha256": sha(OUT / "artifact_manifest.json"),
            "protected_state_unchanged": True, "strict_pit_forward_captures": len(verified_forward),
            "strict_pit_forward_races": metrics["strict_pit_forward_unique_races"], "tests_passed": tests["passed"]}


def main():
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("--finalize", action="store_true", help="Assemble exclusive final outputs after source collection is complete")
    args = argument_parser.parse_args()
    if not args.finalize:
        argument_parser.error("--finalize is required; collection may still be in progress")
    print(json.dumps(finalize(), ensure_ascii=False))


if __name__ == "__main__":
    main()
