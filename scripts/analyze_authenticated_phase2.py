"""Offline, append-only Phase 2 analysis of sanitized authenticated captures.

No HTTP, application/scraper imports, training, or database mutations. Historical
DOM projections prove present-day display only. A projection is never converted
into invented source HTML to obtain a historical-parser comparison.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

import audit_authenticated_speed_pilot as legacy_speed
import audit_authenticated_training_pilot as legacy_training


ROOT = Path(__file__).resolve().parents[1]
PHASE2 = ROOT / "reports/generated/model-experiments/netkeiba-authenticated-phase2-v1"
PARSER_VERSION = "authenticated-phase2-offline-v1"
CLASSIFICATIONS = ("EXACT_CORRECT", "VALUE_CORRECT_COLUMN_WRONG", "VALUE_MISSING",
                   "TRUNCATED", "SELECTOR_ERROR", "SOURCE_MISSING")
FIELDS = legacy_speed.FIELDS
CELL_FIELDS = legacy_speed.CELL_FIELDS
SPEED_FIELDS = legacy_speed.SPEED_FIELDS
IDENTITY_FIELDS = legacy_speed.IDENTITY_FIELDS
SHA_PATTERN = re.compile(r"[0-9a-f]{64}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return legacy_speed.sha256(Path(path))


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _inside(path, root=PHASE2, must_exist=False):
    resolved = Path(path).resolve(strict=must_exist)
    require(resolved.is_relative_to(Path(root).resolve()), "path outside isolated Phase 2 root")
    return resolved


def _timestamp(value):
    require(isinstance(value, str), "timestamp must be an ISO string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None,
            "timestamp must include a timezone")
    return parsed


def capture_metadata(capture, source_hash):
    race_id = capture.get("race_id")
    require(isinstance(race_id, str) and re.fullmatch(r"\d{12}", race_id), "invalid race_id")
    require(isinstance(source_hash, str) and SHA_PATTERN.fullmatch(source_hash), "invalid source SHA256")
    url = capture.get("source_url", capture.get("url", ""))
    parsed = urlparse(url)
    require(parsed.scheme == "https" and parsed.hostname == "race.netkeiba.com" and
            parsed.username is None and parsed.password is None and not parsed.fragment,
            "unexpected race source URL")
    require(parse_qs(parsed.query).get("race_id") == [race_id], "source URL race_id mismatch")
    require(set(parse_qs(parsed.query)) <= {"race_id", "type", "rf", "mode", "page", "master"},
            "unapproved source URL query parameter")
    require("master" not in parse_qs(parsed.query) or parse_qs(parsed.query)["master"] == ["1"],
            "unobserved Master URL parameter value")
    observed = capture.get("fetched_at", capture.get("observed_at"))
    _timestamp(observed)
    require(capture.get("raw_http_available", False) is False, "only sanitized DOM captures supported")
    return {"race_id": race_id, "source_url": url, "fetched_at": observed,
            "source_page_type": capture.get("source_page_type", parsed.path.rsplit("/", 1)[-1]),
            "capture_mode": capture.get("capture_mode", "historical"),
            "source_projection_sha256": source_hash, "parser_version": PARSER_VERSION,
            "raw_http_available": False, "model_input_permitted": False}


def verify_mapping_contract(contract, fixture_hash, evidence_root):
    require(contract.get("fixture_sha256") == fixture_hash, "mapping fixture SHA256 mismatch")
    require(contract.get("schema_version") == 1 and bool(contract.get("evidence")),
            "mapping evidence and schema required")
    verified = []
    for evidence in contract["evidence"]:
        path = _inside(Path(evidence_root) / evidence["source_file"], evidence_root, True)
        require(bool(evidence.get("description")), "mapping evidence description required")
        require(sha256(path) == evidence.get("sha256"), "mapping evidence SHA256 mismatch")
        verified.append({"source_file": evidence["source_file"], "sha256": evidence["sha256"]})
    return verified


def classify_slot(comparison):
    """Classify actual legacy HTML parsing; leave unmapped semantics unclassified."""
    slot = comparison["slot"]
    findings = comparison["findings"]
    if comparison["old_match_count"] != 1:
        return "SELECTOR_ERROR"
    if "SLOT_BEYOND_OLD_FIVE_SLOT_LIMIT" in findings:
        return "TRUNCATED"
    if slot["is_placeholder"] or slot["raw_text"].strip() == "中間軽め":
        if comparison["old_time_value"] is None and comparison["old_lap_value"] is None:
            return "SOURCE_MISSING"
        return "SELECTOR_ERROR"
    if len(slot["cumulative_candidates"]) > 1 or len(slot["lap_candidates"]) > 1:
        return "SELECTOR_ERROR"
    pairs = ((slot["cumulative_value"], comparison["old_time_value"]),
             (slot["lap_value"], comparison["old_lap_value"]))
    if any(source is not None and old is None for source, old in pairs):
        return "VALUE_MISSING"
    if any(source != old for source, old in pairs):
        return "SELECTOR_ERROR"
    if "DISTANCE_LABEL_CONFLICT_WITH_CONTRACT" in findings or "LAP_LABEL_CONFLICT_WITH_CONTRACT" in findings:
        return "VALUE_CORRECT_COLUMN_WRONG"
    if slot["mapping_status"] == "UNVERIFIED":
        return None
    return "EXACT_CORRECT"


def _add_events(records, capture, metadata):
    sequences = Counter()
    race_date = capture.get("race_date", capture.get("race_date_visible"))
    try:
        race_day = date.fromisoformat(race_date) if race_date else None
    except (TypeError, ValueError):
        race_day = None
    for record in records:
        key = (record.get("horse_number"), record.get("horse_id"), record.get("horse_name"))
        sequences[key] += 1
        training_day = record.get("training_date")
        record.update(metadata)
        record.update({"training_sequence": sequences[key], "sequence_basis": "SOURCE_DISPLAY_ORDER",
                       "training_type": capture.get("training_type", "SOURCE_PAGE_SCOPE_ONLY"),
                       "is_final_workout_verified": False, "strict_pit": False,
                       "days_before_race": ((race_day - date.fromisoformat(training_day)).days
                                            if race_day and training_day else None),
                       "comment_exists": bool(record.get("comment_exists", record.get("comment_raw"))),
                       "event_key": f'{metadata["race_id"]}:{key[0]}:{record["record_ordinal"]}'})
    return records


def analyze_training_html(capture, source_hash, mapping_contract=None, evidence_root=PHASE2):
    metadata = capture_metadata(capture, source_hash)
    html = capture.get("table_html", capture.get("html"))
    require(isinstance(html, str) and html.strip(), "sanitized training table HTML required")
    fixture_hash = text_sha256(html)
    parsed = legacy_training.parse_experimental(html, metadata["race_id"])
    evidence = []
    if mapping_contract is not None:
        evidence = verify_mapping_contract(mapping_contract, fixture_hash, evidence_root)
        require(mapping_contract.get("race_id", metadata["race_id"]) == metadata["race_id"],
                "mapping contract race_id mismatch")
        legacy_training.apply_mapping_contract(parsed, mapping_contract, fixture_hash)
    old = legacy_training.load_old_parser()(html, metadata["race_id"])
    comparison, original_summary = legacy_training.compare(old, parsed)
    records = _add_events(parsed["records"], capture, metadata)
    for item in comparison:
        slot = item["slot"]
        record = records[item["record_ordinal"] - 1]
        item.update(metadata)
        item.update({"old_classification": classify_slot(item),
                     "old_parser_comparison": "RUN_ON_SANITIZED_SOURCE_DOM_HTML",
                     "new_classification": ("SOURCE_MISSING" if slot["is_placeholder"] or slot["raw_text"].strip() == "中間軽め" else
                                            "SELECTOR_ERROR" if len(slot["cumulative_candidates"]) > 1 or
                                            len(slot["lap_candidates"]) > 1 else
                                            "EXACT_CORRECT" if slot["mapping_status"] != "UNVERIFIED" else None),
                     "classification_scope": "VALUES_AND_CAPTURE_BOUND_DISTANCE_LABELS",
                     "mapping_verified": slot["mapping_status"] != "UNVERIFIED",
                     "missing_kind": ("LAYOUT_PADDING" if slot["mapping_status"] == "DOCUMENTED_PADDING" else
                                      "EXPLICIT_LIGHT_TRAINING_NO_CLOCK" if slot["raw_text"].strip() == "中間軽め" else
                                      "SOURCE_MISSING" if slot["is_placeholder"] else None),
                     "training_date": record["training_date"], "track_condition": record["track_condition_raw"],
                     "rider": record["rider_raw"], "pace": record["pace_raw"], "grade": record["grade_raw"],
                     "comment_exists": record["comment_exists"], "training_sequence": record["training_sequence"],
                     "displayed_distance_label": slot["distance_furlongs"],
                     "displayed_time": slot["cumulative_value"],
                     "displayed_lap_interval": ([slot["lap_from_furlongs"], slot["lap_to_furlongs"]]
                                                if slot["lap_from_furlongs"] is not None else None),
                     "displayed_lap_time": slot["lap_value"]})
    return {"metadata": metadata, "records": records, "comparison": comparison,
            "summary": {**original_summary, "fixture_sha256": fixture_hash,
                        "mapping_evidence": evidence,
                        "old_classification_counts": dict(Counter(x["old_classification"] for x in comparison
                                                                   if x["old_classification"] is not None)),
                        "new_classification_counts": dict(Counter(x["new_classification"] for x in comparison
                                                                   if x["new_classification"] is not None)),
                        "unclassified_distance_slots": sum(x["old_classification"] is None for x in comparison),
                        "old_parser_comparison": "RUN_ON_SANITIZED_SOURCE_DOM_HTML",
                        "strict_pit_records": 0, "model_input_permitted": False}}


def _projected_slot(text, ordinal, html=None):
    require(isinstance(text, str), "projected slot text must be a string")
    if html is not None:
        require(isinstance(html, str), "projected slot HTML must be a string")
        soup = BeautifulSoup(html, "lxml")
        elements = soup.find_all("li")
        require(len(elements) == 1, "projected slot must contain exactly one li")
        require(re.sub(r"\s+", "", elements[0].get_text()) == re.sub(r"\s+", "", text),
                "slot HTML/text disagree")
        slot = legacy_training._slot(elements[0], ordinal, 1, ordinal)
        slot["projection_scope"] = "CAPTURED_LI_ONLY_NOT_WHOLE_TABLE"
        return slot
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?|[-－—―]?)\s*(?:[（(]\s*([0-9]+(?:\.[0-9]+)?)\s*[）)])?\s*", text)
    time = float(match.group(1)) if match and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", match.group(1)) else None
    lap = float(match.group(2)) if match and match.group(2) else None
    return {"ordinal": ordinal, "raw_text": text, "raw_html": None,
            "cumulative_value": time, "lap_value": lap,
            "cumulative_candidates": [time] if time is not None else [],
            "lap_candidates": [lap] if lap is not None else [],
            "is_placeholder": text.strip() in ("", "-", "－", "—", "―"),
            "parse_status": "PARSED_TEXT_PROJECTION" if match else "AMBIGUOUS_PROJECTED_TEXT",
            "distance_furlongs": None, "lap_from_furlongs": None, "lap_to_furlongs": None,
            "mapping_status": "UNVERIFIED", "mapping_basis": None,
            "projection_scope": "VISIBLE_TEXT_ONLY"}


def analyze_training_projection(capture, source_hash):
    metadata = capture_metadata(capture, source_hash)
    require(isinstance(capture.get("rows"), list), "training projection rows required")
    records = []
    for ordinal, source in enumerate(capture["rows"], 1):
        number = source.get("horse_number")
        require(type(number) is int and 1 <= number <= 18, "invalid horse_number")
        require(isinstance(source.get("horse_name"), str) and source["horse_name"].strip(), "missing horse_name")
        texts = source.get("slot_texts", source.get("slots", []))
        require(isinstance(texts, list), "slot_texts must be a list")
        fragments = source.get("slot_html")
        require(fragments is None or len(fragments) == len(texts), "slot HTML count mismatch")
        raw_date = source.get("training_date", source.get("training_date_raw", ""))
        parsed_date = legacy_training._date(raw_date.replace("-", "/"))
        href = source.get("horse_href")
        record = {"record_ordinal": ordinal, "horse_number": number, "horse_name": source["horse_name"],
                  "horse_id": legacy_speed.horse_id_from_href(href) if href else source.get("horse_id"),
                  "training_date_raw": raw_date, "training_date": parsed_date,
                  "course_raw": source.get("course", source.get("course_raw", "")),
                  "track_condition_raw": source.get("track_condition", source.get("condition", "")),
                  "rider_raw": source.get("rider", ""), "pace_raw": source.get("pace", ""),
                  "grade_raw": source.get("grade", ""), "comment_exists": source.get("comment_exists", False),
                  "slots": [_projected_slot(text, index, fragments[index - 1] if fragments else None)
                            for index, text in enumerate(texts, 1)],
                  "old_parser_comparison": "NOT_RUN_PROJECTION_ONLY", "strict_pit": False,
                  "model_input_permitted": False}
        records.append(record)
    _add_events(records, capture, metadata)
    counts = {"horses_visible": len({r["horse_number"] for r in records}), "training_events": len(records),
              "dates_visible": sum(bool(r["training_date"]) for r in records),
              "courses_visible": dict(Counter(r["course_raw"] for r in records)),
              "numeric_clock_slots": sum(s["cumulative_value"] is not None for r in records for s in r["slots"]),
              "numeric_lap_slots": sum(s["lap_value"] is not None for r in records for s in r["slots"]),
              "source_missing_slots": sum(s["is_placeholder"] for r in records for s in r["slots"]),
              "pace_visible": sum(bool(r["pace_raw"]) for r in records),
              "grade_visible": sum(bool(r["grade_raw"]) for r in records),
              "comments_visible": sum(bool(r["comment_exists"]) for r in records)}
    return {"metadata": metadata, "records": records, "comparison": [],
            "summary": {**counts, "old_parser_comparison": "NOT_RUN_PROJECTION_ONLY",
                        "distance_mapping_verified": False, "strict_pit_records": 0,
                        "historical_publication_proven": False, "annual_coverage_estimated": False,
                        "model_input_permitted": False}}


def _space_normalized(value):
    return "" if value is None else " ".join(str(value).split())


def crosscheck_training_dom_projection(capture, records):
    """Cross-check two independently captured DOM views; do not invent source HTML."""
    projected = capture.get("rows")
    require(isinstance(projected, list) and len(projected) == len(records),
            "table and row projection event counts differ")
    pairs = (("horse_number", "horse_number"), ("horse_name", "horse_name"),
             ("training_date_raw", "training_date"), ("course_raw", "course"),
             ("track_condition_raw", "track_condition"), ("rider_raw", "rider"),
             ("pace_raw", "pace"), ("grade_raw", "grade"))
    results = []
    for record, row in zip(records, projected):
        different = [left for left, right in pairs
                     if _space_normalized(record.get(left, "")) != _space_normalized(row.get(right, ""))]
        if row.get("horse_href"):
            if legacy_speed.horse_id_from_href(row["horse_href"]) != record.get("horse_id"):
                different.append("horse_id")
        source_slots = row.get("slot_texts")
        if not isinstance(source_slots, list) or len(source_slots) != len(record["slots"]):
            different.append("slot_count")
        elif any(re.sub(r"\s+", "", source) != re.sub(r"\s+", "", parsed["raw_text"])
                 for source, parsed in zip(source_slots, record["slots"])):
            different.append("slot_texts")
        if bool(row.get("comment_exists")) != bool(record.get("comment_raw")):
            different.append("comment_exists")
        results.append({"record_ordinal": record["record_ordinal"], "horse_number": record.get("horse_number"),
                        "different_fields": different, "matches": not different})
    require(all(row["matches"] for row in results), "captured HTML and independent row projection disagree")
    return {"status": "PASS", "event_count": len(results), "checked_rows": results,
            "normalization": "Whitespace only; numeric strings and all slot positions retained",
            "scope": "Same observed DOM, two representations; not an independent publisher source"}


def build_capture_mapping_contract(capture, source_hash, source_file):
    """Bind explicit current-page help and exact major-course shapes to one capture."""
    metadata = capture_metadata(capture, source_hash)
    html = capture.get("table_html")
    require(isinstance(html, str) and html, "table HTML required for mapping evidence")
    parsed = legacy_training.parse_experimental(html, metadata["race_id"])
    crosscheck = crosscheck_training_dom_projection(capture, parsed["records"])
    guides = capture.get("guide_clock_text")
    require(isinstance(guides, list) and all(isinstance(g, str) for g in guides), "same-page clock help required")
    guide_text = "\n".join(guides)
    nonhill_parts = ("6F、5F、4F、3F、1F", "6F-5F、5F-4F、4F-3F、3F-1F、1F-ゴール")
    hill_parts = ("4F、3F、2F、1F", "4F-3F、3F-2F、2F-1F、1F-ゴール")
    nonhill_verified = all(part in guide_text for part in nonhill_parts)
    hill_verified = all(part in guide_text for part in hill_parts)
    rules, unsupported = [], []
    courses = sorted({r["course_raw"] for r in parsed["records"]})
    for course in courses:
        # Preserve the exact observed course+badge text in the rule. Only the
        # explicitly recognized display badge is separated for selecting a help block.
        base = course[:-len(" 一番時計")] if course.endswith(" 一番時計") else course
        hill = base in ("栗坂", "美坂")
        nonhill = base in ("ＣＷ", "CW", "栗Ｗ", "栗W", "美Ｗ", "美W")
        records = [r for r in parsed["records"] if r["course_raw"] == course]
        shape_ok = all(r["time_list_count"] == 1 and len(r["slots"]) == 5 for r in records)
        if not shape_ok or not ((hill and hill_verified) or (nonhill and nonhill_verified)):
            unsupported.append({"course_raw": course, "reason": "UNVERIFIED_COURSE_OR_HELP_OR_SHAPE"})
            continue
        if hill and not all(r["slots"][0]["is_placeholder"] for r in records):
            unsupported.append({"course_raw": course, "reason": "HILL_LEADING_PADDING_NOT_VERIFIED"})
            continue
        distances = (4, 3, 2, 1) if hill else (6, 5, 4, 3, 1)
        ordinals = range(2, 6) if hill else range(1, 6)
        endpoints = tuple(zip(distances, (*distances[1:], 0)))
        slots = [{"ordinal": ordinal, "distance_furlongs": distance,
                  "lap_from_furlongs": interval[0], "lap_to_furlongs": interval[1]}
                 for ordinal, distance, interval in zip(ordinals, distances, endpoints)]
        basis = ("Exact captured course and five-li layout; same-page hill help lists four clocks and intervals, "
                 "with the verified leading blank retained as layout padding." if hill else
                 "Exact captured major non-hill course and five-li layout; same-page help explicitly lists "
                 "6F,5F,4F,3F,1F and their intervals. Missing cells retain their ordinals.")
        if base != course:
            basis += " The exact source course cell includes the displayed 一番時計 badge; its text is preserved."
        rules.append({"course_raw": course, "slot_count": 5, "required_blank_ordinals": [1] if hill else [],
                      "mapping_basis": basis, "slots": slots})
    return {"schema_version": 1, "fixture_sha256": text_sha256(html),
            "source_projection_sha256": source_hash, "race_id": metadata["race_id"],
            "scope": "EXACT_CURRENT_CAPTURE_ONLY", "historical_schema_mapping_permitted": False,
            "generic_parser_rollout_permitted": False, "production_correction_permitted": False,
            "model_input_permitted": False,
            "evidence": [{"source_file": source_file, "sha256": source_hash,
                          "description": "Exact capture contains sanitized table HTML, independently projected rows, "
                                         "same-page clock help, observed URL/time, and visible race start/date."}],
            "rules": rules, "unsupported_courses": unsupported,
            "guide_verified": {"hill_clock_and_lap_order": hill_verified,
                               "nonhill_clock_and_lap_order": nonhill_verified}}, crosscheck


def analyze_training_capture_to_directory(source_path, expected_sha256, output, root=PHASE2):
    """Create capture-bound contract, cross-check, full analysis, and slot CSV once."""
    source_path = _inside(source_path, root, True)
    output = _inside(output, root)
    require(not output.exists(), "append-only analysis output already exists")
    require(sha256(source_path) == expected_sha256, "source projection SHA256 mismatch")
    capture = json.loads(source_path.read_text(encoding="utf-8"))
    contract, crosscheck = build_capture_mapping_contract(
        capture, expected_sha256, source_path.relative_to(Path(root).resolve()).as_posix())
    analysis = analyze_training_html(capture, expected_sha256, contract, root)
    numeric = any(s["cumulative_value"] is not None for r in analysis["records"] for s in r["slots"])
    pit = evaluate_forward_pit(capture, expected_sha256, numeric)
    summary = {**analysis["summary"], **analysis["metadata"], "crosscheck_status": crosscheck["status"],
               "unsupported_courses": contract["unsupported_courses"], "forward_pit": pit,
               "script_sha256": sha256(Path(__file__))}
    require(sha256(source_path) == expected_sha256, "source changed during analysis")
    output.mkdir(parents=True)
    for name, payload in (("mapping-contract.json", contract), ("dom-projection-crosscheck.json", crosscheck),
                          ("training-analysis.json", analysis), ("summary.json", summary), ("pit.json", pit)):
        with (output / name).open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
    columns = ("race_id", "horse_number", "horse_name", "training_date", "course_raw", "track_condition", "rider",
               "training_sequence", "slot_ordinal", "displayed_distance_label", "displayed_time",
               "displayed_lap_interval", "displayed_lap_time", "pace", "grade", "comment_exists",
               "old_time_field", "old_time_value", "old_lap_field", "old_lap_value", "old_classification",
               "new_classification", "mapping_verified", "missing_kind", "source_page_type", "fetched_at",
               "source_projection_sha256")
    with (output / "current-slot-audit.csv").open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in analysis["comparison"]:
            writer.writerow({**row, "slot_ordinal": row["slot"]["ordinal"],
                             "displayed_lap_interval": json.dumps(row["displayed_lap_interval"])})
    return summary


def parse_speed_projection(capture, source_hash):
    metadata = capture_metadata(capture, source_hash)
    order = capture.get("field_order")
    if order is None and capture.get("rows") and all(isinstance(c, dict) for c in capture["rows"][0].get("cells", [])):
        order = [c.get("class") for c in capture["rows"][0]["cells"]]
    if not capture.get("rows") and order is None:
        order = list(CELL_FIELDS)
    require(isinstance(order, list) and Counter(order) == Counter(CELL_FIELDS.keys()),
            "exactly seven distinct index cell classes required")
    require(isinstance(capture.get("rows"), list), "speed projection rows required")
    old_value_parser = legacy_speed.load_old_value_parser()
    records = []
    for row in capture["rows"]:
        number = row.get("horse_number")
        require(type(number) is int and 1 <= number <= 18, "invalid horse_number")
        require(isinstance(row.get("horse_name"), str) and row["horse_name"].strip(), "missing horse_name")
        cells = row.get("cells")
        require(isinstance(cells, list) and len(cells) == 7, "seven projected speed cells required")
        record = {**metadata, "horse_number": number, "horse_name": row["horse_name"],
                  "horse_id": legacy_speed.horse_id_from_href(row["horse_href"]), "cell_evidence": {}}
        for cell_class, html in zip(order, cells):
            field = CELL_FIELDS[cell_class]
            if isinstance(html, dict):
                require(html.get("class") == cell_class, "speed field projection class mismatch")
                visible = html.get("display_text")
                require(isinstance(visible, str), "speed projected display_text required")
                trimmed = visible.strip().rstrip("*")
                try:
                    value = int(trimmed) if trimmed not in ("", "-", "未", "**", "***") else None
                except ValueError:
                    value = None
                record[field] = value
                record["cell_evidence"][field] = {
                    "sanitized_html": None, "visible_text": visible,
                    "hidden_sort_text": html.get("hidden_sort_text"), "parsed_value": value,
                    "old_parser_comparison": "NOT_RUN_PROJECTION_ONLY",
                    "parsing_basis": "Frozen scalar semantics applied to captured visible text only"}
                continue
            require(isinstance(html, str), "speed cell must be sanitized HTML text")
            soup = BeautifulSoup(html, "lxml")
            tds = soup.find_all("td")
            require(len(tds) == 1 and cell_class in tds[0].get("class", []), "speed selector mismatch")
            td = tds[0]
            hidden = len(td.find_all(class_="Sort_Function_Data_Hidden"))
            require(hidden <= 1, "multiple hidden sort spans require manual review")
            raw = td.get_text()
            value = old_value_parser(td)
            record[field] = value
            record["cell_evidence"][field] = {"sanitized_html": html, "raw_dom_text": raw,
                                              "visible_text": td.get_text(strip=True),
                                              "hidden_sort_span_count": hidden, "parsed_value": value,
                                              "old_parser_comparison": "RUN_ON_SANITIZED_SOURCE_TD_HTML"}
        records.append(record)
    require(len({r["horse_number"] for r in records}) == len(records), "duplicate source speed horse key")
    expected = capture.get("expected_horse_numbers")
    if expected is not None:
        require(len(set(expected)) == len(expected) and set(expected) == {r["horse_number"] for r in records},
                "source speed horse set differs from capture receipt")
    return sorted(records, key=lambda r: r["horse_number"])


def readonly_authorizer(action, table, column, _database, _trigger):
    allowed = {"speed_figures": set(SPEED_FIELDS), "race_results_ultimate": set(IDENTITY_FIELDS)}
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ and column in allowed.get(table, set()):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def read_frozen_rows(path, expected_sha256, race_ids):
    path = Path(path).resolve(strict=True)
    require(isinstance(race_ids, (list, tuple)) and 0 < len(race_ids) <= 40,
            "bounded race list of at most 40 required")
    require(len(set(race_ids)) == len(race_ids) and all(re.fullmatch(r"\d{12}", r) for r in race_ids),
            "invalid or duplicate DB race id")
    before = sha256(path)
    require(before == expected_sha256, "frozen snapshot SHA256 mismatch before read")
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            require(db.execute("PRAGMA query_only").fetchone()[0] == 1, "query_only required")
            db.set_authorizer(readonly_authorizer)
            speed, identities = [], []
            for race_id in race_ids:
                speed.extend(dict(row) for row in db.execute(
                    f"SELECT {','.join(SPEED_FIELDS)} FROM speed_figures WHERE race_id=? ORDER BY horse_number", (race_id,)))
                identities.extend(dict(row) for row in db.execute(
                    f"SELECT {','.join(IDENTITY_FIELDS)} FROM race_results_ultimate WHERE race_id=? ORDER BY horse_number", (race_id,)))
    finally:
        after = sha256(path)
        require(after == before, "frozen snapshot changed during read")
    return speed, identities, {"sha256_before": before, "sha256_after": after, "mode": "ro",
                               "query_only": True, "authorizer_enabled": True,
                               "race_ids": list(race_ids), "outcomes_read": False, "database_writes": 0}


def _keyed(rows, label):
    keys = [(r["race_id"], r["horse_number"]) for r in rows]
    require(len(keys) == len(set(keys)), f"duplicate {label} horse key")
    return dict(zip(keys, rows))


def compare_speed(records, speed_rows, identity_rows):
    source, stored, identities = (_keyed(rows, label) for rows, label in
                                  ((records, "source"), (speed_rows, "DB speed"), (identity_rows, "DB identity")))
    comparisons, identity_comparison = [], []
    for key, row in source.items():
        saved, identity = stored.get(key), identities.get(key)
        status = ("MISSING_DB_ROW" if saved is None else "MISSING_FROZEN_IDENTITY" if identity is None else
                  "MATCH" if row["horse_name"] == saved["horse_name"] == identity["horse_name"] and
                  str(row["horse_id"]) == str(identity["horse_id"]) else "MISMATCH")
        identity_comparison.append({"race_id": key[0], "horse_number": key[1], "status": status,
                                    "source_horse_name": row["horse_name"], "source_horse_id": row["horse_id"],
                                    "db_speed_horse_name": saved["horse_name"] if saved else None,
                                    "db_identity_horse_name": identity["horse_name"] if identity else None,
                                    "db_horse_id": identity["horse_id"] if identity else None})
        for field in FIELDS:
            value, db_value = row[field], saved[field] if saved else None
            if status != "MATCH":
                relation = "identity_mismatch" if status == "MISMATCH" else status.lower()
            else:
                relation = ("both_missing" if value is None and db_value is None else
                            "exact_match" if value == db_value else "source_missing_only" if value is None else
                            "stored_missing_only" if db_value is None else "mismatch")
            comparisons.append({"race_id": key[0], "horse_number": key[1], "horse_name": row["horse_name"],
                                "horse_id": row["horse_id"], "field": field, "display_value": value,
                                "db_value": db_value, "identity_status": status, "relation": relation,
                                "compared": status == "MATCH", "source_available": value is not None,
                                "source_projection_sha256": row.get("source_projection_sha256"),
                                "capture_mode": row.get("capture_mode")})
    per_field = {}
    for field in FIELDS:
        rows = [r for r in comparisons if r["field"] == field]
        counts = Counter(r["relation"] for r in rows)
        compared = sum(r["compared"] for r in rows)
        exact, missing = counts["exact_match"], counts["both_missing"]
        mismatch = counts["mismatch"] + counts["source_missing_only"] + counts["stored_missing_only"]
        per_field[field] = {"source_cells": len(rows), "compared": compared, "exact_match": exact,
                            "both_missing": missing, "mismatch": mismatch,
                            "source_missing_only": counts["source_missing_only"],
                            "stored_missing_only": counts["stored_missing_only"],
                            "missing_db_row": counts["missing_db_row"],
                            "identity_unverified": counts["missing_frozen_identity"] + counts["identity_mismatch"],
                            "exact_match_rate": (exact + missing) / compared if compared else None,
                            "integer_exact_match_rate": exact / (compared - missing) if compared > missing else None}
    counts = Counter(r["relation"] for r in comparisons)
    compared = sum(r["compared"] for r in comparisons)
    exact, missing = counts["exact_match"], counts["both_missing"]
    summary = {"source_races": len({r["race_id"] for r in records}), "source_horses": len(records),
               "source_cells": len(comparisons), "compared_cells": compared, "exact_match": exact,
               "both_missing": missing, "mismatch": compared - exact - missing,
               "exact_match_rate": (exact + missing) / compared if compared else None,
               "rate_definition": "(exact integer matches + both_missing) / identity-verified compared cells",
               "relation_counts": dict(counts), "per_field": per_field,
               "identity_comparison": identity_comparison,
               "db_rows_outside_source": len(set(stored) - set(source)),
               "missing_db_rows_are_source_failures": False, "source_relation": "UNKNOWN",
               "strict_pit": False, "historical_publication_proven": False, "model_input_permitted": False}
    return comparisons, summary


def evaluate_forward_pit(capture, source_hash, values_present=False):
    metadata = capture_metadata(capture, source_hash)
    reasons = []
    if metadata["capture_mode"] != "forward":
        reasons.append("NOT_FORWARD_CAPTURE")
    race_date_text = capture.get("race_date_visible", capture.get("source_race_date_text", ""))
    start_text = capture.get("scheduled_start_time_visible", capture.get("source_start_time_text", ""))
    scheduled = capture.get("scheduled_start_time")
    if scheduled is None and capture.get("timezone") == "Asia/Tokyo":
        day = re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(race_date_text))
        clock = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", str(start_text))
        if day and clock:
            scheduled = f"{race_date_text}T{int(clock.group(1)):02d}:{clock.group(2)}:00+09:00"
    start = None
    try:
        start = _timestamp(scheduled)
    except (TypeError, ValueError):
        reasons.append("MISSING_OR_INVALID_SCHEDULED_START_TIMESTAMP")
    if start and start.utcoffset() != timedelta(hours=9):
        reasons.append("SCHEDULED_START_NOT_SOURCE_JAPAN_TIME")
    date_match = re.search(r"(\d{4})[年/.-](\d{1,2})[月/.-](\d{1,2})", str(race_date_text))
    clock_match = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", str(start_text))
    if not date_match:
        reasons.append("SOURCE_VISIBLE_FULL_RACE_DATE_MISSING")
    if not clock_match:
        reasons.append("SOURCE_VISIBLE_START_TIME_MISSING")
    if start and date_match:
        try:
            visible_date = date(*map(int, date_match.groups()))
            if visible_date != start.date():
                reasons.append("SOURCE_DATE_AND_SCHEDULED_START_DISAGREE")
        except ValueError:
            reasons.append("SOURCE_VISIBLE_DATE_INVALID")
    if start and clock_match and (start.hour, start.minute) != tuple(map(int, clock_match.groups())):
        reasons.append("SOURCE_CLOCK_AND_SCHEDULED_START_DISAGREE")
    before = bool(start and _timestamp(metadata["fetched_at"]) < start)
    if not before:
        reasons.append("CAPTURE_NOT_BEFORE_SCHEDULED_START")
    if not values_present:
        reasons.append("NO_CAPTURED_VALUES")
    return {**metadata, "race_date_visible": race_date_text, "scheduled_start_time_visible": start_text,
            "scheduled_start_time": scheduled, "fetched_before_scheduled_start": before,
            "strict_pit_forward_sample": not reasons, "strict_pit_historical": False,
            "pit_class": "FORWARD_PRE_SCHEDULED_START" if not reasons else "NOT_PROVEN",
            "reasons": reasons, "actual_start_time_verified": False,
            "revision_history_verified": False, "feature_leakage_policy_fixed": False}


def analyze_speed_captures_to_directory(source_specs, output, root=PHASE2):
    """Compare a small declared set of stored projections with the frozen DB."""
    require(isinstance(source_specs, list) and 0 < len(source_specs) <= 40, "bounded speed source list required")
    output = _inside(output, root)
    require(not output.exists(), "append-only analysis output already exists")
    records, sources, pit = [], [], []
    for specification in source_specs:
        path = _inside(Path(root) / specification["source_file"], root, True)
        expected = specification["sha256"]
        require(sha256(path) == expected, "source projection SHA256 mismatch")
        capture = json.loads(path.read_text(encoding="utf-8"))
        rows = parse_speed_projection(capture, expected)
        records.extend(rows)
        pit.append(evaluate_forward_pit(capture, expected, any(r[f] is not None for r in rows for f in FIELDS)))
        sources.append({"source_file": specification["source_file"], "sha256": expected,
                        "race_id": capture["race_id"], "horse_count": len(rows)})
    races = sorted({r["race_id"] for r in records})
    if races:
        saved, identities, receipt = read_frozen_rows(legacy_speed.SNAPSHOT, legacy_speed.SNAPSHOT_SHA256, races)
    else:
        saved, identities, receipt = [], [], None
    comparisons, summary = compare_speed(records, saved, identities)
    summary.update({"sources": sources, "frozen_snapshot": receipt, "forward_pit": pit,
                    "script_sha256": sha256(Path(__file__)), "parser_version": PARSER_VERSION})
    for source in sources:
        require(sha256(Path(root) / source["source_file"]) == source["sha256"], "source changed during analysis")
    output.mkdir(parents=True)
    for name, payload in (("speed-parsed.json", records), ("speed-comparison.json", comparisons),
                          ("speed-summary.json", summary), ("pit.json", pit)):
        with (output / name).open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
    columns = ("race_id", "horse_number", "horse_name", "horse_id", "field", "display_value", "db_value",
               "identity_status", "relation", "compared", "source_available", "source_projection_sha256", "capture_mode")
    with (output / "speed-current-db-comparison.csv").open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(comparisons)
    return summary


def execute(manifest_path, expected_manifest_sha256, output, root=PHASE2):
    """Analyze a bounded source manifest; outputs never overwrite existing artifacts."""
    manifest_path = _inside(manifest_path, root, True)
    output = _inside(output, root)
    require(not output.exists(), "append-only analysis output already exists")
    require(sha256(manifest_path) == expected_manifest_sha256, "input manifest SHA256 mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    entries = manifest.get("captures")
    require(isinstance(entries, list) and 0 < len(entries) <= 80, "bounded capture list required")
    require(len({e["source_file"] for e in entries}) == len(entries), "duplicate capture source_file")
    training, speed, pit, inputs = [], [], [], []
    for entry in entries:
        source_path = _inside(Path(root) / entry["source_file"], root, True)
        source_hash = sha256(source_path)
        require(source_hash == entry["sha256"], "source projection SHA256 mismatch")
        capture = json.loads(source_path.read_text(encoding="utf-8-sig"))
        kind = entry["kind"]
        if kind == "training_html":
            contract = None
            if entry.get("mapping_contract_file"):
                contract_path = _inside(Path(root) / entry["mapping_contract_file"], root, True)
                require(sha256(contract_path) == entry["mapping_contract_sha256"], "contract SHA256 mismatch")
                contract = json.loads(contract_path.read_text(encoding="utf-8-sig"))
            result = analyze_training_html(capture, source_hash, contract, root)
            training.append(result)
            values_present = any(s["cumulative_value"] is not None for r in result["records"] for s in r["slots"])
        elif kind == "training_projection":
            result = analyze_training_projection(capture, source_hash)
            training.append(result)
            values_present = result["summary"]["numeric_clock_slots"] > 0
        elif kind == "speed_projection":
            rows = parse_speed_projection(capture, source_hash)
            speed.extend(rows)
            values_present = any(r[field] is not None for r in rows for field in FIELDS)
        else:
            raise ValueError("unsupported capture kind")
        pit.append(evaluate_forward_pit(capture, source_hash, values_present))
        inputs.append({"source_file": entry["source_file"], "sha256": source_hash, "kind": kind})
    db_receipt = None
    comparisons, speed_summary = [], None
    if speed:
        db_path = legacy_speed.SNAPSHOT
        db_hash = legacy_speed.SNAPSHOT_SHA256
        speed_rows, identity_rows, db_receipt = read_frozen_rows(db_path, db_hash, sorted({r["race_id"] for r in speed}))
        comparisons, speed_summary = compare_speed(speed, speed_rows, identity_rows)
    require(sha256(manifest_path) == expected_manifest_sha256, "input manifest changed during analysis")
    for item in inputs:
        require(sha256(Path(root) / item["source_file"]) == item["sha256"], "input source changed during analysis")
    summary = {"parser_version": PARSER_VERSION, "script_sha256": sha256(Path(__file__)),
               "generated_at": datetime.now(timezone.utc).isoformat(), "input_manifest_sha256": expected_manifest_sha256,
               "inputs": inputs, "training_capture_count": len(training), "speed_summary": speed_summary,
               "frozen_snapshot": db_receipt, "strict_pit_forward_samples": sum(p["strict_pit_forward_sample"] for p in pit),
               "training_feature_research_ready": "NO", "speed_feature_research_ready": "NO",
               "network_calls": 0, "database_writes": 0, "fits_started": 0}
    output.mkdir(parents=True)
    for name, payload in (("training-analysis.json", training), ("speed-parsed.json", speed),
                          ("speed-comparison.json", comparisons), ("pit-analysis.json", pit),
                          ("analysis-summary.json", summary)):
        with (output / name).open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(execute(args.manifest, args.manifest_sha256, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
