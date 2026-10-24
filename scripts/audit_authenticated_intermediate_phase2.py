"""Offline multiple-event audit for a captured all-workouts DOM text projection.

Latest training date is a candidate, never an inferred final-workout label. The
frozen final-only reference is compared only on displayed identity/date/course/
slot text. No legacy parser is run against reconstructed or invented HTML.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse

import analyze_authenticated_phase2 as phase2


PILOT = phase2.legacy_training.PILOT
REFERENCE_RECEIPT = PILOT / "source/202606040611-training-capture.json"
REFERENCE_RECEIPT_SHA256 = "61a44d45761e33c43e91bb5776c920d5eebd3bdb03e8bda3a09da0e02bcd46c0"
REFERENCE_HTML = PILOT / "source/202606040611-training-table.html"
REFERENCE_HTML_SHA256 = "5b2078778d652f2a268ae042c2b38c6e259ddf114995a6a907baeeb7c21b94bc"
PARSER_VERSION = "authenticated-phase2-intermediate-events-v1"


def _identity(record):
    return (record.get("horse_number"), record.get("horse_id"), record.get("horse_name"))


def _clock_signature(record):
    return tuple(re.sub(r"\s+", "", slot["raw_text"]) for slot in record["slots"])


def _event_signature(record):
    return (record.get("training_date"), phase2._space_normalized(record.get("course_raw", "")),
            _clock_signature(record))


def load_reference():
    phase2.require(phase2.sha256(REFERENCE_RECEIPT) == REFERENCE_RECEIPT_SHA256,
                   "frozen final-only receipt SHA256 mismatch")
    phase2.require(phase2.sha256(REFERENCE_HTML) == REFERENCE_HTML_SHA256,
                   "frozen final-only HTML SHA256 mismatch")
    receipt = json.loads(REFERENCE_RECEIPT.read_text(encoding="utf-8"))
    phase2.require(receipt["source_sha256"] == REFERENCE_HTML_SHA256 and
                   receipt.get("view", "").startswith("最終追切のみ"), "reference is not bound to final-only view")
    result = phase2.legacy_training.parse_experimental(REFERENCE_HTML.read_text(encoding="utf-8"), receipt["race_id"])
    return result["records"], {"race_id": receipt["race_id"], "source_url": receipt["source_url"],
                               "observed_at": receipt["observed_at"], "source_view": receipt["view"],
                               "source_html_sha256": REFERENCE_HTML_SHA256,
                               "receipt_sha256": REFERENCE_RECEIPT_SHA256}


def audit_events(capture, source_hash, reference_records, reference_metadata):
    phase2.require(capture.get("selected_view") == "中間の調教全て", "all-workouts selected view not verified")
    phase2.require(parse_qs(urlparse(capture.get("source_url", "")).query).get("type") == ["1"],
                   "all-workouts URL type=1 not verified")
    phase2.require(capture.get("capture_mode") in ("current", "historical"),
                   "this intermediate audit requires non-forward capture mode")
    phase2.require(not capture.get("table_html"), "this audit expects captured text projection only")
    phase2.require(capture.get("race_id") == reference_metadata.get("race_id"), "reference race_id mismatch")
    result = phase2.analyze_training_projection(capture, source_hash)
    records = result["records"]
    phase2.require(0 < len(records) <= 360, "bounded intermediate events required")
    grouped, reference = defaultdict(list), defaultdict(list)
    for record in records:
        grouped[_identity(record)].append(record)
    for record in reference_records:
        reference[_identity(record)].append(record)
    horses = []
    for key, events in grouped.items():
        dates = [event["training_date"] for event in events if event["training_date"]]
        latest = max(dates) if dates else None
        latest_events = [event for event in events if latest and event["training_date"] == latest]
        final_reference = reference.get(key, [])
        matches = [event for event in events if len(final_reference) == 1 and
                   _event_signature(event) == _event_signature(final_reference[0])]
        relation = ("REFERENCE_IDENTITY_ABSENT" if not final_reference else
                    "REFERENCE_EVENT_AMBIGUOUS" if len(final_reference) != 1 else
                    "DISPLAY_EXACT_MATCH" if len(matches) == 1 else
                    "MATCHING_DISPLAY_EVENTS_AMBIGUOUS" if len(matches) > 1 else "NO_MATCHING_DISPLAY_EVENT")
        order = ("UNKNOWN" if len(dates) != len(events) else "ONE_DATE_ONLY" if len(set(dates)) <= 1 else
                 "DESCENDING" if dates == sorted(dates, reverse=True) else
                 "ASCENDING" if dates == sorted(dates) else "MIXED")
        for event in events:
            is_latest = event in latest_events
            reference_match = event in matches
            event.update({"source_selected_view": capture["selected_view"],
                          "training_type": "ALL_WORKOUTS_PAGE_UNCLASSIFIED",
                          "latest_observed_date_candidate": is_latest,
                          "matches_prior_final_only_display": reference_match,
                          "final_only_reference_source_sha256": reference_metadata.get("source_html_sha256"),
                          "is_final_workout_verified": False,
                          "final_verification_policy": "Dates and matching text identify candidates only; no final label inferred",
                          "strict_pit": False, "old_parser_comparison": "NOT_RUN_PROJECTION_ONLY",
                          "event_parser_version": PARSER_VERSION})
        horses.append({"horse_number": key[0], "horse_id": key[1], "horse_name": key[2],
                       "training_event_count": len(events), "source_date_order": order,
                       "training_date_min": min(dates) if dates else None, "training_date_max": latest,
                       "latest_date_candidate_sequences": [event["training_sequence"] for event in latest_events],
                       "final_reference_match_sequences": [event["training_sequence"] for event in matches],
                       "final_reference_match_relation": relation,
                       "reference_match_is_latest_date_candidate": bool(matches) and all(event in latest_events for event in matches),
                       "unique_latest_candidate_matches_reference": len(latest_events) == len(matches) == 1 and
                                                                    latest_events[0] == matches[0],
                       "is_final_workout_verified": False})
    pit = phase2.evaluate_forward_pit(capture, source_hash,
                                     any(slot["cumulative_value"] is not None for event in records for slot in event["slots"]))
    phase2.require(not pit["strict_pit_forward_sample"], "non-forward intermediate audit cannot claim strict PIT")
    summary = {"race_id": capture["race_id"], "source_projection_sha256": source_hash,
               "observed_at": result["metadata"]["fetched_at"], "selected_view": capture["selected_view"],
               "horses_visible": len(horses), "training_event_count": len(records),
               "slot_count": sum(len(event["slots"]) for event in records),
               "horses_with_multiple_events": sum(horse["training_event_count"] > 1 for horse in horses),
               "events_per_horse": dict(Counter(str(horse["training_event_count"]) for horse in horses)),
               "reference_horse_count": len(reference), "reference_comparison_scope": "DISPLAY_IDENTITY_DATE_COURSE_SLOT_TEXT_ONLY",
               "final_reference_relations": dict(Counter(horse["final_reference_match_relation"] for horse in horses)),
               "latest_candidate_and_reference_agree_horses": sum(horse["unique_latest_candidate_matches_reference"] for horse in horses),
               "reference_match_not_latest_date_horses": sum(bool(horse["final_reference_match_sequences"]) and
                                                            not horse["reference_match_is_latest_date_candidate"] for horse in horses),
               "courses": dict(Counter(event["course_raw"] for event in records)),
               "training_date_min": min((event["training_date"] for event in records if event["training_date"]), default=None),
               "training_date_max": max((event["training_date"] for event in records if event["training_date"]), default=None),
               "final_workout_verified_events": 0, "legacy_is_last_training_used": False,
               "old_parser_comparison": "NOT_RUN_PROJECTION_ONLY", "raw_http_available": False,
               "historical_strict_pit": False, "forward_pit": pit, "historical_rows_modified": 0,
               "model_input_permitted": False, "parser_version": PARSER_VERSION,
               "reference_capture": reference_metadata}
    return {"metadata": result["metadata"], "events": records, "horses": horses, "summary": summary}


def execute(source_path, expected_sha256, output, root=phase2.PHASE2):
    source_path = phase2._inside(source_path, root, True)
    output = phase2._inside(output, root)
    phase2.require(not output.exists(), "append-only intermediate output already exists")
    phase2.require(phase2.sha256(source_path) == expected_sha256, "intermediate source SHA256 mismatch")
    capture = json.loads(source_path.read_text(encoding="utf-8"))
    reference, metadata = load_reference()
    result = audit_events(capture, expected_sha256, reference, metadata)
    result["summary"].update({"source_file": source_path.relative_to(Path(root).resolve()).as_posix(),
                              "generated_at": datetime.now(timezone.utc).isoformat(),
                              "script_sha256": phase2.sha256(Path(__file__)),
                              "base_analyzer_sha256": phase2.sha256(Path(phase2.__file__))})
    phase2.require(phase2.sha256(source_path) == expected_sha256, "intermediate source changed during analysis")
    output.mkdir(parents=True)
    for name, value in (("intermediate-events.json", result["events"]), ("per-horse-audit.json", result["horses"]),
                        ("summary.json", result["summary"])):
        with (output / name).open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
    columns = ("event_key", "race_id", "horse_number", "horse_name", "training_sequence", "training_date",
               "course_raw", "track_condition_raw", "rider_raw", "pace_raw", "grade_raw", "comment_exists",
               "days_before_race", "training_type", "latest_observed_date_candidate", "matches_prior_final_only_display",
               "is_final_workout_verified", "source_selected_view", "strict_pit", "fetched_at", "source_projection_sha256")
    with (output / "intermediate-events.csv").open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(result["events"])
    return result["summary"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(execute(args.source, args.source_sha256, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
