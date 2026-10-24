"""Isolated offline audit tests; use --noconftest to avoid application fixtures."""
import copy
from pathlib import Path
import sqlite3
import sys

import pytest
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import audit_authenticated_speed_pilot as audit


@pytest.fixture
def value_parser():
    return audit.load_old_value_parser()


@pytest.mark.parametrize(("html", "expected"), [
    ('<td><span class="Sort_Function_Data_Hidden">1108</span>108</td>', 108),
    ('<td><span class="Sort_Function_Data_Hidden">0</span>0</td>', 0),
    ('<td>108***</td>', 108), ('<td>-</td>', None), ('<td>未</td>', None),
    ('<td>***</td>', None), ('<td></td>', None), ('<td>108.5</td>', None),
    ('<td>108※</td>', None), ('<td>-12</td>', -12),
    ('<td><b> 1 </b><i> 08 </i></td>', 108),
    ('<td><span class="Sort_Function_Data_Hidden">1108</span>'
     '<span class="Sort_Function_Data_Hidden">999</span>108</td>', 999108),
])
def test_frozen_value_parser_exact_behavior(value_parser, html, expected):
    assert value_parser(BeautifulSoup(html, "lxml").find("td")) == expected


def test_value_parser_none_and_no_production_globals(value_parser):
    assert value_parser(None) is None
    assert set(value_parser.__globals__) == {"Optional", "__builtins__", "_parse_index_value"}
    source = 'raise RuntimeError("must not execute")\nimport forbidden_module\n' + \
        audit.OLD_SOURCE.read_text(encoding="utf-8")
    isolated = audit.extract_old_value_parser(source)
    assert isolated(BeautifulSoup('<td>42</td>', "lxml").find("td")) == 42


def projection():
    order = list(audit.CELL_FIELDS)
    return {"source_kind": "sanitized_browser_index_cell_projection", "field_order": order,
            "rows": [{"horse_number": n, "horse_name": f"Horse {n}",
                      "horse_href": f"https://db.netkeiba.com/horse/{2020000000+n}/",
                      "cells": [f'<td class="{cls}"><span class="Sort_Function_Data_Hidden">'
                                f'1000</span>{100+i}</td>' for i, cls in enumerate(order)]}
                     for n in range(1, 19)]}


def stored_rows(records):
    speed = [{field: row[field] for field in audit.SPEED_FIELDS} for row in records]
    identities = [{field: row[field] for field in audit.IDENTITY_FIELDS} for row in records]
    return speed, identities


def test_mapping_including_reverse_recency_and_raw_evidence(value_parser):
    records = audit.parse_projection(projection(), value_parser)
    assert len(records) == 18
    assert records[0]["max_index"] == 100
    assert records[0]["index_last"] == 104
    assert records[0]["index_2ago"] == 105
    assert records[0]["index_3ago"] == 106
    assert records[0]["cell_evidence"]["index_last"]["raw_dom_text"] == "1000104"
    assert records[0]["cell_evidence"]["index_last"]["text_after_first_hidden_removal"] == "104"
    assert records[0]["cell_evidence"]["index_last"]["hidden_sort_span_count"] == 1


@pytest.mark.parametrize("mutation", ["duplicate_horse", "omitted_horse", "field_order", "wrong_cell"])
def test_projection_fails_closed_on_incomplete_or_misaligned_cells(value_parser, mutation):
    source = projection()
    if mutation == "duplicate_horse":
        source["rows"][1] = copy.deepcopy(source["rows"][0])
    elif mutation == "omitted_horse":
        source["rows"].pop()
    elif mutation == "field_order":
        source["field_order"][1] = source["field_order"][0]
    else:
        source["rows"][0]["cells"][0] = '<td class="sk__index1">100</td>'
    with pytest.raises(ValueError):
        audit.parse_projection(source, value_parser)


def test_exact_match_does_not_claim_formula_or_pit(value_parser):
    records = audit.parse_projection(projection(), value_parser)
    records[0]["max_index"] = None
    comparison, summary = audit.compare(records, *stored_rows(records))
    assert len(comparison) == 126
    assert summary["status"] == "DISPLAY_VALUES_MATCH_FROZEN_DB"
    assert summary["cell_relation_counts"] == {"both_missing": 1, "equal_integer": 125}
    for field in ("strict_pit", "historical_publication_time_proven", "provider_formula_verified",
                  "RECONSTRUCTION_VALIDATED", "model_input_permitted", "outcomes_read"):
        assert summary[field] is False


def test_mismatches_distinguish_missingness_numbers_and_identity(value_parser):
    records = audit.parse_projection(projection(), value_parser)
    speed, identities = stored_rows(records)
    records[0]["max_index"] = None
    speed[1]["max_index"] = None
    speed[2]["max_index"] = -1
    identities[3]["horse_id"] = "wrong"
    _, summary = audit.compare(records, speed, identities)
    assert summary["mismatched_cells"] == 3
    assert summary["cell_relation_counts"]["source_missing_only"] == 1
    assert summary["cell_relation_counts"]["stored_missing_only"] == 1
    assert summary["cell_relation_counts"]["different_integer"] == 1
    assert not summary["identities_match"]
    assert summary["status"] == "DISPLAY_OR_IDENTITY_DIFFERENCES_REQUIRE_REVIEW"


def test_duplicate_db_identity_cannot_be_silently_deduplicated(value_parser):
    records = audit.parse_projection(projection(), value_parser)
    speed, identities = stored_rows(records)
    identities.append(identities[0])
    with pytest.raises(ValueError, match="duplicate frozen identity"):
        audit.compare(records, speed, identities)


def test_snapshot_read_preserves_hash_and_reads_only_approved_columns(tmp_path):
    path = tmp_path / "synthetic.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE speed_figures (" + ",".join(audit.SPEED_FIELDS) + ", outcome)")
        db.execute("CREATE TABLE race_results_ultimate (" + ",".join(audit.IDENTITY_FIELDS) + ", outcome)")
        db.execute("INSERT INTO speed_figures VALUES (" + ",".join("?" * 11) + ")",
                   (audit.RACE_ID, 1, "Horse 1", 100, 101, 102, 103, 104, 105, 106, "forbidden"))
        db.execute("INSERT INTO race_results_ultimate VALUES (?,?,?,?,?)",
                   (audit.RACE_ID, 1, "2020000001", "Horse 1", "forbidden"))
    before = audit.sha256(path)
    speed, identities, receipt = audit.read_frozen_rows(path, before)
    assert set(speed[0]) == set(audit.SPEED_FIELDS)
    assert set(identities[0]) == set(audit.IDENTITY_FIELDS)
    assert receipt["mode"] == "ro" and receipt["query_only"]
    assert receipt["sha256_before"] == receipt["sha256_after"] == audit.sha256(path) == before
    with pytest.raises(ValueError, match="before read"):
        audit.read_frozen_rows(path, "0" * 64)


def test_existing_output_cannot_be_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "PILOT", tmp_path)
    source = tmp_path / "source" / "projection.json"
    output = tmp_path / "speed-comparison"
    output.mkdir()
    with pytest.raises(ValueError, match="append-only"):
        audit.run(source, "0" * 64, output)
