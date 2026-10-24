"""Offline, append-only comparison of one sanitized authenticated DOM capture.

This is an isolated experiment: no scraping/application imports, credentials,
database access, network, model fitting, or correction of historical records.
Distances remain unknown unless an exact-capture evidence contract supplies them.
The historical parser is AST-extracted from the pinned provenance copy only.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Optional

from bs4 import BeautifulSoup, NavigableString, Tag

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "reports/generated/model-experiments/netkeiba-authenticated-pilot-v1"
OLD_SOURCE = ROOT / (
    "reports/generated/model-experiments/training-data-reconstruction-v1/provenance/"
    "training-parser-sources/05f6fac8db50193a8bc2aed1e6b892decf23c69b/"
    "python-api/scraping/oikiri.py"
)
OLD_SHA256 = "a3560073e2afc73378e80477e68ea038dc268550c3d04d78bcc90250f6e1e306"
OLD_FUNCTIONS = ("_parse_oikiri_html", "_parse_horse_header_row", "_parse_data_row")
OLD_TIME_FIELDS = ("time_6f", "time_5f", "time_4f", "time_3f", "time_1f")
OLD_LAP_FIELDS = ("lap_6f_5f", "lap_5f_4f", "lap_4f_3f", "lap_3f_1f", "lap_1f_g")
OLD_DISTANCES = (6, 5, 4, 3, 1)
OLD_LAP_ENDPOINTS = ((6, 5), (5, 4), (4, 3), (3, 1), (1, 0))
OUTPUT_NAMES = ("old-parser-output.json", "experimental-parser-output.json",
                "parser-comparison.json", "parser-comparison-summary.json")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


class _QuietLogger:
    def debug(self, *_args, **_kwargs):
        pass


class _RemoveLocalBs4Import(ast.NodeTransformer):
    def visit_ImportFrom(self, node):
        require(node.module == "bs4" and len(node.names) == 1 and
                node.names[0].name == "NavigableString" and
                node.names[0].asname == "_NavStr", "unexpected import inside old parser")
        return ast.copy_location(ast.Pass(), node)

    def visit_Import(self, node):
        raise ValueError("unexpected import inside old parser")


def extract_old_parser(source_text: str):
    """Compile only three parsing functions, with their one bs4 import injected.

    No top-level imports, constants, decorators, defaults, or async scraping
    functions are executed. Production callers additionally pin the source hash.
    """
    tree = ast.parse(source_text)
    functions = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name in OLD_FUNCTIONS]
    require(Counter(node.name for node in functions) == Counter(OLD_FUNCTIONS),
            "old parser function set differs")
    for function in functions:
        require(not function.decorator_list and not function.args.defaults and
                not any(function.args.kw_defaults), "unexpected old function initialization")
    module = ast.Module(body=functions, type_ignores=[])
    module = ast.fix_missing_locations(_RemoveLocalBs4Import().visit(module))
    namespace = {"BeautifulSoup": BeautifulSoup, "_NavStr": NavigableString,
                 "re": re, "Optional": Optional, "logger": _QuietLogger()}
    exec(compile(module, "<pinned-old-parser-functions-only>", "exec"), namespace)
    return namespace["_parse_oikiri_html"]


def load_old_parser():
    require(sha256(OLD_SOURCE) == OLD_SHA256, "frozen historical parser SHA256 mismatch")
    return extract_old_parser(OLD_SOURCE.read_text(encoding="utf-8"))


def _text(node, separator=" "):
    return node.get_text(separator, strip=True) if node is not None else ""


def _numeric(text):
    text = text.strip()
    return float(text) if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", text) else None


def _date(raw):
    match = re.search(r"(?<!\d)(\d{4})/(\d{2})/(\d{2})(?!\d)", raw)
    if not match:
        return None
    try:
        return date(*map(int, match.groups())).isoformat()
    except ValueError:
        return None


def _slot(li, ordinal, list_ordinal, ordinal_in_list):
    """Keep DOM serialization, text nodes and every numeric candidate separately."""
    nodes, cumulative = [], []
    lap_pattern = r"[（(]\s*([0-9]+(?:\.[0-9]+)?)\s*[）)]"
    for node in li.descendants:
        if not isinstance(node, NavigableString):
            continue
        ancestors = []
        is_lap_node = False
        parent = node.parent
        while parent is not None and parent is not li:
            ancestors.append({"tag": parent.name, "classes": parent.get("class", [])})
            is_lap_node |= "RapTime" in parent.get("class", []) or bool(
                re.fullmatch(lap_pattern, parent.get_text(" ", strip=True)))
            parent = parent.parent
        raw = str(node)
        nodes.append({"raw_text": raw, "ancestors": ancestors})
        # Parenthesized display values are retained as lap candidates, not
        # assigned an interval until a capture-bound contract says so.
        if not is_lap_node and _numeric(raw) is not None:
            cumulative.append(_numeric(raw))
    raw_text = li.get_text("", strip=False)
    laps = [float(value) for value in re.findall(lap_pattern, li.get_text(" ", strip=False))]
    return {"ordinal": ordinal, "list_ordinal": list_ordinal,
            "ordinal_in_list": ordinal_in_list, "raw_html": str(li),
            "raw_text": raw_text, "text_nodes": nodes,
            "cumulative_candidates": cumulative, "lap_candidates": laps,
            "cumulative_value": cumulative[0] if len(cumulative) == 1 else None,
            "lap_value": laps[0] if len(laps) == 1 else None,
            "is_placeholder": raw_text.strip() in ("", "-", "－", "—", "―"),
            "distance_furlongs": None, "lap_from_furlongs": None,
            "lap_to_furlongs": None, "mapping_status": "UNVERIFIED",
            "mapping_basis": None}


def parse_experimental(html: str, race_id: str) -> dict:
    """Extract observations without guessing distance, identity, or publication."""
    soup = BeautifulSoup(html, "lxml")
    tables = soup.select("table.OikiriTable")
    records, headers, warnings = [], [], []
    for table_ordinal, table in enumerate(tables, 1):
        current = None
        rows = [row for row in table.find_all("tr") if row.find_parent("table") is table]
        for row_ordinal, row in enumerate(rows, 1):
            row_id = row.get("id", "")
            if row_id.startswith("tr_") and "HorseList" in row.get("class", []):
                number = _text(row.find("td", class_="Umaban"))
                info = row.find("td", class_="Horse_Info")
                name = info.select_one(".Horse_Name") if info else None
                anchor = name.find("a") if name else info.find("a") if info else None
                name = anchor if anchor else name
                link = anchor.get("href", "") if anchor else ""
                horse_id = re.search(r"(?:^|/)horse/(\d+)(?:/|$|[?#])", link)
                current = {"horse_header_row_id": row_id,
                           "horse_number_raw": number,
                           "horse_number": int(number) if re.fullmatch(r"\d+", number) else None,
                           "horse_name": _text(name), "horse_href_raw": link,
                           "horse_id": horse_id.group(1) if horse_id else None,
                           "comment_raw": _text(row.find("td", class_="TrainingReview_Cell")),
                           "table_ordinal": table_ordinal, "row_ordinal": row_ordinal}
                headers.append(dict(current))
            cells = row.find_all("td", recursive=False)
            date_indexes = [index for index, cell in enumerate(cells)
                            if "Training_Day" in cell.get("class", [])]
            if not date_indexes:
                continue
            index = date_indexes[0]
            def offset(number):
                return _text(cells[index + number]) if index + number < len(cells) else ""
            time_cell = row.find("td", class_="TrainingTimeData")
            lists = time_cell.select("ul.TrainingTimeDataList") if time_cell else []
            slots = []
            for list_ordinal, time_list in enumerate(lists, 1):
                for local_ordinal, li in enumerate(time_list.find_all("li", recursive=False), 1):
                    slots.append(_slot(li, len(slots) + 1, list_ordinal, local_ordinal))
            raw_date = _text(cells[index])
            problems = []
            if current is None:
                problems.append("NO_PRECEDING_HORSE_HEADER")
            if _date(raw_date) is None:
                problems.append("INVALID_OR_MISSING_FULL_DATE")
            if len(lists) != 1:
                problems.append("TIME_LIST_COUNT_NOT_ONE")
            if len(date_indexes) != 1:
                problems.append("DATE_CELL_COUNT_NOT_ONE")
            record = {"record_ordinal": len(records) + 1, "race_id": race_id,
                      **(current or {}), "table_ordinal": table_ordinal,
                      "data_row_ordinal": row_ordinal, "training_date_raw": raw_date,
                      "training_date": _date(raw_date), "course_raw": offset(1),
                      "track_condition_raw": offset(2), "rider_raw": offset(3),
                      "position_raw": offset(5), "pace_raw": _text(row.select_one(".TrainingLoad")),
                      "grade_raw": _text(row.select_one(".Rank_A,.Rank_B,.Rank_C,.Rank_D")),
                      "critic_raw": _text(row.select_one(".Training_Critic")),
                      "raw_cells": [{"ordinal": n, "classes": cell.get("class", []),
                                     "raw_text": cell.get_text("", strip=False), "raw_html": str(cell)}
                                    for n, cell in enumerate(cells, 1)],
                      "time_list_count": len(lists), "slots": slots,
                      "warnings": problems, "strict_pit": False,
                      "model_input_permitted": False}
            records.append(record)
    if len(tables) != 1:
        warnings.append("OIKIRI_TABLE_COUNT_NOT_ONE")
    return {"table_count": len(tables), "horse_headers": headers,
            "records": records, "warnings": warnings}


def apply_mapping_contract(experimental, contract, fixture_sha256):
    """Apply only explicit, exact-course/shape rules for this particular fixture."""
    require(contract.get("schema_version") == 1, "mapping schema mismatch")
    require(contract.get("fixture_sha256") == fixture_sha256, "mapping fixture SHA256 mismatch")
    require(bool(contract.get("evidence")), "mapping evidence required")
    rules = contract.get("rules", [])
    require(len({rule["course_raw"] for rule in rules}) == len(rules), "duplicate mapping course")
    for record in experimental["records"]:
        matches = [rule for rule in rules if rule["course_raw"] == record["course_raw"]]
        if not matches:
            continue
        rule = matches[0]
        slots = record["slots"]
        require(record["time_list_count"] == 1 and len(slots) == rule["slot_count"],
                "mapping list shape mismatch")
        require(bool(rule.get("mapping_basis")), "mapping basis required")
        blank_ordinals = rule.get("required_blank_ordinals", [])
        for ordinal in blank_ordinals:
            require(type(ordinal) is int and 1 <= ordinal <= len(slots) and
                    slots[ordinal - 1]["is_placeholder"], "required blank slot is not blank")
            slots[ordinal - 1].update(mapping_status="DOCUMENTED_PADDING",
                                      mapping_basis=rule["mapping_basis"])
        mappings = rule["slots"]
        require(len({item["ordinal"] for item in mappings}) == len(mappings), "duplicate mapped ordinal")
        for item in mappings:
            ordinal = item["ordinal"]
            require(type(ordinal) is int and 1 <= ordinal <= len(slots) and
                    ordinal not in blank_ordinals, "invalid mapped ordinal")
            require(type(item.get("distance_furlongs")) is int and item["distance_furlongs"] > 0,
                    "invalid mapped distance")
            endpoints = (item.get("lap_from_furlongs"), item.get("lap_to_furlongs"))
            require(endpoints == (None, None) or
                    all(type(n) is int for n in endpoints) and endpoints[0] > endpoints[1] >= 0,
                    "invalid lap endpoints")
            slots[ordinal - 1].update({key: item.get(key) for key in
                                      ("distance_furlongs", "lap_from_furlongs", "lap_to_furlongs")})
            slots[ordinal - 1].update(mapping_status="CAPTURE_BOUND_CONTRACT",
                                     mapping_basis=rule["mapping_basis"])
    return experimental


def compare(old_records, experimental):
    by_key = {}
    for old in old_records:
        key = (old.get("horse_number"), old.get("horse_name"),
               _date(old.get("training_date", "")), old.get("course"))
        by_key.setdefault(key, []).append(old)
    comparisons, issues = [], Counter()
    for record in experimental["records"]:
        key = (record.get("horse_number"), record.get("horse_name"),
               record["training_date"], record["course_raw"])
        matched = by_key.get(key, [])
        old = matched[0] if len(matched) == 1 else None
        for slot in record["slots"]:
            index = slot["ordinal"] - 1
            old_indexed = old is not None and index < 5
            old_time = old.get(OLD_TIME_FIELDS[index]) if old_indexed else None
            old_lap = old.get(OLD_LAP_FIELDS[index]) if old_indexed else None
            findings = []
            if old is None:
                findings.append("OLD_RECORD_ABSENT_OR_AMBIGUOUS")
            if index >= 5:
                findings.append("SLOT_BEYOND_OLD_FIVE_SLOT_LIMIT")
            if old_indexed and old_time != slot["cumulative_value"]:
                findings.append("CUMULATIVE_VALUE_DIFFERS")
            if old_indexed and old_lap != slot["lap_value"]:
                findings.append("LAP_VALUE_DIFFERS")
            if old_indexed and slot["distance_furlongs"] is not None and (
                    slot["distance_furlongs"] != OLD_DISTANCES[index]):
                findings.append("DISTANCE_LABEL_CONFLICT_WITH_CONTRACT")
            endpoints = (slot["lap_from_furlongs"], slot["lap_to_furlongs"])
            if old_indexed and endpoints[0] is not None and endpoints != OLD_LAP_ENDPOINTS[index]:
                findings.append("LAP_LABEL_CONFLICT_WITH_CONTRACT")
            if slot["mapping_status"] == "UNVERIFIED":
                findings.append("DISTANCE_SEMANTICS_UNVERIFIED")
            issues.update(findings)
            comparisons.append({"record_ordinal": record["record_ordinal"],
                                "horse_number": record.get("horse_number"),
                                "horse_name": record.get("horse_name"),
                                "training_date_raw": record["training_date_raw"],
                                "course_raw": record["course_raw"],
                                "slot": slot, "old_match_count": len(matched),
                                "old_time_field": OLD_TIME_FIELDS[index] if old_indexed else None,
                                "old_time_value": old_time,
                                "old_lap_field": OLD_LAP_FIELDS[index] if old_indexed else None,
                                "old_lap_value": old_lap, "findings": findings})
    summary = {"old_record_count": len(old_records),
               "experimental_record_count": len(experimental["records"]),
               "horse_header_count": len(experimental["horse_headers"]),
               "slot_count": len(comparisons), "finding_counts": dict(issues),
               "course_record_counts": dict(Counter(r["course_raw"] for r in experimental["records"])),
               "slot_shape_counts": dict(Counter(str(len(r["slots"])) for r in experimental["records"])),
               "mapped_distance_slots": sum(c["slot"]["distance_furlongs"] is not None for c in comparisons),
               "record_warning_counts": dict(Counter(warning for record in experimental["records"]
                                                      for warning in record["warnings"])),
               "model_input_gate": "NO", "strict_pit_records": 0,
               "historical_corrections_applied": 0,
               "capture_does_not_prove_pre_race_publication": True}
    return comparisons, summary


def _inside_pilot(path, must_exist=False):
    path = Path(path).resolve(strict=must_exist)
    require(path.is_relative_to(PILOT.resolve()), "path must stay inside isolated pilot directory")
    return path


def execute(fixture, expected_sha256, race_id, output, mapping_contract=None):
    fixture = _inside_pilot(fixture, must_exist=True)
    output = _inside_pilot(output)
    require(re.fullmatch(r"\d{12}", race_id), "race_id must contain 12 digits")
    require(fixture.suffix.lower() == ".html", "sanitized HTML fixture required")
    require(sha256(fixture) == expected_sha256, "fixture SHA256 mismatch")
    require(not any((output / name).exists() for name in OUTPUT_NAMES), "refusing to overwrite comparison")
    script_sha = sha256(Path(__file__))
    html = fixture.read_text(encoding="utf-8")
    old = load_old_parser()(html, race_id)
    experimental = parse_experimental(html, race_id)
    evidence_hashes = {}
    contract_sha = None
    if mapping_contract:
        contract_path = _inside_pilot(mapping_contract, must_exist=True)
        contract_sha = sha256(contract_path)
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        for evidence in contract.get("evidence", []):
            evidence_path = _inside_pilot(PILOT / evidence["source_file"], must_exist=True)
            require(sha256(evidence_path) == evidence["sha256"], "mapping evidence SHA256 mismatch")
            require(bool(evidence.get("description")), "mapping evidence description required")
            evidence_hashes[evidence["source_file"]] = evidence["sha256"]
        apply_mapping_contract(experimental, contract, expected_sha256)
    comparison, summary = compare(old, experimental)
    summary.update({"generated_at": datetime.now(timezone.utc).isoformat(),
                    "race_id": race_id, "fixture": fixture.relative_to(PILOT).as_posix(),
                    "fixture_sha256": expected_sha256, "fixture_kind": "sanitized_serialized_dom",
                    "race_id_binding": "caller supplied; independently bind using capture receipt",
                    "runner_identity_scope": "source-displayed identity; no external roster verification",
                    "old_parser_source": OLD_SOURCE.relative_to(ROOT).as_posix(),
                    "old_parser_sha256": OLD_SHA256, "audit_script_sha256": script_sha,
                    "mapping_contract_sha256": contract_sha, "mapping_evidence_sha256": evidence_hashes,
                    "old_extraction": "three AST-selected parsing functions; no source module imports",
                    "network_calls": 0, "database_writes": 0, "fits_started": 0})
    require(sha256(fixture) == expected_sha256 and sha256(OLD_SOURCE) == OLD_SHA256 and
            sha256(Path(__file__)) == script_sha, "audit source changed during comparison")
    if mapping_contract:
        require(sha256(contract_path) == contract_sha, "mapping contract changed during comparison")
        for name, expected in evidence_hashes.items():
            require(sha256(PILOT / name) == expected, "mapping evidence changed during comparison")
    output.mkdir(parents=True, exist_ok=True)
    for name, value in zip(OUTPUT_NAMES, (old, experimental, comparison, summary)):
        with (output / name).open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--fixture-sha256", required=True)
    parser.add_argument("--race-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mapping-contract", type=Path)
    args = parser.parse_args()
    summary = execute(args.fixture, args.fixture_sha256, args.race_id, args.output, args.mapping_contract)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
