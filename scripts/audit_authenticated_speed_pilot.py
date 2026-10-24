"""Offline comparison of one sanitized speed-index DOM projection and frozen DB.

No application imports, credentials, network, outcomes, model fitting, or DB writes.
This compares today's displayed provider cells with stored values; equality proves
neither a provider formula nor historical point-in-time availability.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "reports/generated/model-experiments/netkeiba-authenticated-pilot-v1"
SNAPSHOT = ROOT / "reports/generated/data-quality/feature-pit-source-v1/audit-source.sqlite"
SNAPSHOT_SHA256 = "4cd3235848dd7574c4217f14fa7974d9af3a40a1ff895b66dec65f663e2a907c"
OLD_SOURCE = ROOT / (
    "reports/generated/model-experiments/speed-figure-pit-v1/provenance/sources/"
    "05f6fac8db50193a8bc2aed1e6b892decf23c69b/python-api/scraping/speed_figure.py"
)
OLD_SOURCE_SHA256 = "82f45de5441f59677cfeda4ad978fa5ccb623bcd9fba197d6e76a3cf47fefeda"
RACE_ID = "202609030411"
EXPECTED_HORSE_NUMBERS = frozenset(range(1, 19))
CELL_FIELDS = {
    "sk__max_index": "max_index",
    "sk__average_index": "avg_5_index",
    "sk__max_distance_index": "dist_max_index",
    "sk__max_course_index": "course_max_index",
    "sk__index1": "index_last",
    "sk__index2": "index_2ago",
    "sk__index3": "index_3ago",
}
FIELDS = tuple(CELL_FIELDS.values())
IDENTITY_FIELDS = ("race_id", "horse_number", "horse_id", "horse_name")
SPEED_FIELDS = ("race_id", "horse_number", "horse_name", *FIELDS)
OUTPUT_NAMES = ("parsed-display-values.json", "cell-comparison.json", "summary.json")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_old_value_parser(source_text: str):
    """Execute only the frozen pure parsing function, never module initialization."""
    tree = ast.parse(source_text)
    functions = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name == "_parse_index_value"]
    require(len(functions) == 1, "historical value parser must occur exactly once")
    function = functions[0]
    require(not function.decorator_list and not function.args.defaults and
            not any(function.args.kw_defaults), "unexpected function initialization")
    require(not any(isinstance(node, (ast.Import, ast.ImportFrom))
                    for node in ast.walk(function)), "unexpected function import")
    namespace = {"Optional": Optional}
    module = ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[]))
    exec(compile(module, "<frozen-speed-value-parser-only>", "exec"), namespace)
    return namespace["_parse_index_value"]


def load_old_value_parser():
    require(sha256(OLD_SOURCE) == OLD_SOURCE_SHA256, "historical parser SHA256 mismatch")
    return extract_old_value_parser(OLD_SOURCE.read_text(encoding="utf-8"))


def horse_id_from_href(href: str) -> str:
    parsed = urlparse(href)
    require(parsed.scheme in ("http", "https") and parsed.hostname == "db.netkeiba.com"
            and parsed.username is None and parsed.password is None,
            "unexpected horse URL origin")
    match = re.fullmatch(r"/horse/(\d{10})/?", parsed.path)
    require(match is not None and not parsed.query and not parsed.fragment,
            "unexpected horse URL path")
    return match.group(1)


def parse_projection(source: dict, value_parser) -> list[dict]:
    require(source.get("source_kind") == "sanitized_browser_index_cell_projection",
            "source must identify a sanitized browser projection")
    order = source.get("field_order")
    require(isinstance(order, list) and Counter(order) == Counter(CELL_FIELDS.keys()),
            "projection must contain exactly the seven approved cell classes")
    require(isinstance(source.get("rows"), list), "projection rows must be a list")
    records = []
    for row in source["rows"]:
        number = row.get("horse_number")
        require(type(number) is int and number in EXPECTED_HORSE_NUMBERS,
                "unexpected horse number")
        require(isinstance(row.get("horse_name"), str) and row["horse_name"].strip(),
                "missing horse name")
        cells = row.get("cells")
        require(isinstance(cells, list) and len(cells) == len(order),
                "projection cell count differs from field order")
        record = {"race_id": RACE_ID, "horse_number": number,
                  "horse_name": row["horse_name"],
                  "horse_id": horse_id_from_href(row["horse_href"]),
                  "cell_evidence": {}}
        for cell_class, html in zip(order, cells):
            require(isinstance(html, str), "projected cell must be HTML text")
            soup = BeautifulSoup(html, "lxml")
            tds = soup.find_all("td")
            require(len(tds) == 1 and cell_class in tds[0].get("class", []),
                    "projected cell selector differs from declared field order")
            td = tds[0]
            field = CELL_FIELDS[cell_class]
            evidence = {"cell_class": cell_class, "raw_dom_text": td.get_text(),
                        "sanitized_html": html,
                        "hidden_sort_span_count": len(td.find_all(
                            class_="Sort_Function_Data_Hidden"))}
            value = value_parser(td)
            evidence["text_after_first_hidden_removal"] = td.get_text(strip=True)
            evidence["parsed_value"] = value
            record[field] = value
            record["cell_evidence"][field] = evidence
        records.append(record)
    require(len(records) == 18 and {row["horse_number"] for row in records} ==
            EXPECTED_HORSE_NUMBERS, "projection must contain each of 18 horse numbers once")
    return sorted(records, key=lambda row: row["horse_number"])


def read_frozen_rows(path: Path, expected_sha256: str):
    """Read only approved identity and seven-index columns through two SELECTs."""
    before = sha256(path)
    require(before == expected_sha256, "frozen snapshot SHA256 mismatch before read")
    allowed = {"speed_figures": set(SPEED_FIELDS),
               "race_results_ultimate": set(IDENTITY_FIELDS)}

    def authorizer(action, table, column, _database, _trigger):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and column in allowed.get(table, set()):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            require(db.execute("PRAGMA query_only").fetchone()[0] == 1,
                    "query_only mode is required")
            db.set_authorizer(authorizer)
            speed = [dict(row) for row in db.execute(
                f"SELECT {','.join(SPEED_FIELDS)} FROM speed_figures "
                "WHERE race_id=? ORDER BY horse_number", (RACE_ID,))]
            identity = [dict(row) for row in db.execute(
                f"SELECT {','.join(IDENTITY_FIELDS)} FROM race_results_ultimate "
                "WHERE race_id=? ORDER BY horse_number", (RACE_ID,))]
            db.set_authorizer(None)
    finally:
        after = sha256(path)
        require(after == before, "frozen snapshot changed during read")
    return speed, identity, {"sha256_before": before, "sha256_after": after,
                             "mode": "ro", "query_only": True,
                             "allowed_columns": {k: sorted(v) for k, v in allowed.items()}}


def keyed_rows(rows, label):
    keys = [(row["race_id"], row["horse_number"]) for row in rows]
    require(len(keys) == len(set(keys)), f"duplicate {label} horse key")
    require(set(keys) == {(RACE_ID, number) for number in EXPECTED_HORSE_NUMBERS},
            f"{label} horse keys must be the exact 18 expected keys")
    return dict(zip(keys, rows))


def compare(records, speed_rows, identity_rows):
    source = keyed_rows(records, "projection")
    stored = keyed_rows(speed_rows, "stored speed")
    identity = keyed_rows(identity_rows, "frozen identity")
    comparisons, identities = [], []
    for key, row in source.items():
        db_row, id_row = stored[key], identity[key]
        identities.append({"race_id": key[0], "horse_number": key[1],
                           "source_horse_id": row["horse_id"],
                           "frozen_horse_id": id_row["horse_id"],
                           "horse_id_matches": row["horse_id"] == id_row["horse_id"],
                           "source_horse_name": row["horse_name"],
                           "stored_speed_horse_name": db_row["horse_name"],
                           "frozen_identity_horse_name": id_row["horse_name"],
                           "all_names_match": row["horse_name"] == db_row["horse_name"] ==
                           id_row["horse_name"]})
        for field in FIELDS:
            value, saved = row[field], db_row[field]
            relation = ("both_missing" if value is None and saved is None else
                        "equal_integer" if value == saved else
                        "source_missing_only" if value is None else
                        "stored_missing_only" if saved is None else "different_integer")
            comparisons.append({"race_id": key[0], "horse_number": key[1],
                                "horse_id": row["horse_id"], "field": field,
                                "display_parsed_value": value, "stored_value": saved,
                                "relation": relation, "exact_match": value == saved})
    identity_ok = all(row["horse_id_matches"] and row["all_names_match"] for row in identities)
    matches = sum(row["exact_match"] for row in comparisons)
    summary = {
        "race_id": RACE_ID, "source_rows": len(records), "stored_rows": len(speed_rows),
        "identity_rows": len(identity_rows), "exact_18_horse_key_sets_match": True,
        "identities_match": identity_ok, "identities": identities,
        "field_order": list(FIELDS), "compared_cells": len(comparisons),
        "exact_match_cells_including_nulls": matches,
        "mismatched_cells": len(comparisons) - matches,
        "cell_relation_counts": dict(Counter(row["relation"] for row in comparisons)),
        "per_field": {field: dict(Counter(row["relation"] for row in comparisons
                                          if row["field"] == field)) for field in FIELDS},
        "status": ("DISPLAY_VALUES_MATCH_FROZEN_DB" if identity_ok and matches == 126
                   else "DISPLAY_OR_IDENTITY_DIFFERENCES_REQUIRE_REVIEW"),
        "strict_pit": False, "historical_publication_time_proven": False,
        "provider_formula_verified": False, "RECONSTRUCTION_VALIDATED": False,
        "model_input_permitted": False, "outcomes_read": False,
        "interpretation": "Equality concerns today's sanitized displayed cells and frozen DB "
                          "values only. It does not establish historical availability, the "
                          "provider's formula, or a single-start reconstruction.",
    }
    return comparisons, summary


def run(source_path: Path, expected_source_sha256: str, output: Path,
        capture_receipt_path: Optional[Path] = None):
    source_path, output = source_path.resolve(), output.resolve()
    require(source_path.is_relative_to((PILOT / "source").resolve()),
            "source must be inside the new pilot/source directory")
    require(output.is_relative_to((PILOT / "speed-comparison").resolve()),
            "output must be inside the new pilot/speed-comparison directory")
    require(not output.exists(), "append-only output directory already exists")
    source_hash = sha256(source_path)
    require(source_hash == expected_source_sha256, "source projection SHA256 mismatch")
    source = json.loads(source_path.read_text(encoding="utf-8-sig"))
    capture_receipt_path = (capture_receipt_path or source_path.with_name(
        f"{RACE_ID}-speed-capture.json")).resolve()
    require(capture_receipt_path.is_relative_to((PILOT / "source").resolve()),
            "capture receipt must be inside the new pilot/source directory")
    receipt_hash = sha256(capture_receipt_path)
    capture = json.loads(capture_receipt_path.read_text(encoding="utf-8-sig"))
    require(capture.get("race_id") == RACE_ID and capture.get("source_url") ==
            f"https://race.netkeiba.com/race/speed.html?race_id={RACE_ID}",
            "capture receipt does not bind the expected race URL")
    require((PILOT / capture["source_file"]).resolve() == source_path and
            capture.get("observed_at") == source.get("observed_at") and
            capture.get("source_kind") == source.get("source_kind"),
            "capture receipt does not bind this source projection")
    require(capture.get("original_http_response_preserved") is False,
            "capture receipt must distinguish DOM projection from raw HTTP response")
    records = parse_projection(source, load_old_value_parser())
    speed, identities, snapshot_receipt = read_frozen_rows(SNAPSHOT, SNAPSHOT_SHA256)
    comparison, summary = compare(records, speed, identities)
    require(sha256(source_path) == source_hash, "source changed during comparison")
    require(sha256(capture_receipt_path) == receipt_hash, "capture receipt changed during comparison")
    require(sha256(OLD_SOURCE) == OLD_SOURCE_SHA256, "historical parser changed during comparison")
    require(sha256(SNAPSHOT) == SNAPSHOT_SHA256, "frozen snapshot changed during comparison")
    summary.update({"schema_version": "authenticated-speed-pilot-comparison-v1",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "source_projection": {"path": source_path.relative_to(ROOT).as_posix(),
                                          "sha256": source_hash,
                                          "source_kind": source["source_kind"],
                                          "observed_at": source.get("observed_at"),
                                          "raw_http_response": False},
                    "capture_receipt": {
                        "path": capture_receipt_path.relative_to(ROOT).as_posix(),
                        "sha256": receipt_hash, "source_url": capture["source_url"],
                        "race_date": capture.get("race_date")},
                    "historical_value_parser": {"path": OLD_SOURCE.relative_to(ROOT).as_posix(),
                                                "sha256": OLD_SOURCE_SHA256,
                                                "extraction": "AST single pure function only"},
                    "frozen_snapshot": {"path": SNAPSHOT.relative_to(ROOT).as_posix(),
                                        **snapshot_receipt}})
    output.mkdir(parents=True)
    payloads = (records, comparison, summary)
    for name, payload in zip(OUTPUT_NAMES, payloads):
        with (output / name).open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--capture-receipt", type=Path)
    parser.add_argument("--output", type=Path, default=PILOT / "speed-comparison")
    args = parser.parse_args()
    summary = run(args.source, args.source_sha256, args.output, args.capture_receipt)
    print(json.dumps({key: summary[key] for key in (
        "status", "compared_cells", "exact_match_cells_including_nulls",
        "mismatched_cells", "cell_relation_counts", "identities_match")}, ensure_ascii=True))


if __name__ == "__main__":
    main()
