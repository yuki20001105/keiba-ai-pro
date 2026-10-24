"""Offline Phase 2 guards; run with --noconftest, without application fixtures."""
import copy
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import analyze_authenticated_phase2 as audit
import ingest_authenticated_phase2 as ingest
import audit_authenticated_intermediate_phase2 as intermediate


RACE = "202606040612"
HASH = "a" * 64


def capture(**changes):
    result = {"race_id": RACE, "source_url": f"https://race.netkeiba.com/race/oikiri.html?race_id={RACE}",
              "observed_at": "2026-09-20T04:00:00+00:00", "source_page_type": "final_workout",
              "capture_mode": "historical", "raw_http_available": False,
              "race_date": "2026-09-20", "race_date_visible": "2026-09-20",
              "scheduled_start_time_visible": "16:30発走", "timezone": "Asia/Tokyo"}
    result.update(changes)
    return result


def html_fixture(slots, course="栗坂", extra=""):
    return f'''<table class="OikiriTable"><tr><th>調教タイム</th></tr>
    <tr id="tr_3" class="HorseList"><td class="Umaban">3</td>
    <td class="Horse_Info"><div class="Horse_Name"><a href="https://db.netkeiba.com/horse/2020101234/">テスト馬</a></div></td>
    <td class="TrainingReview_Cell">調教コメント</td></tr>
    <tr class="HorseList"><td class="Training_Day">2026/09/16(水)</td><td>{course}</td><td>良</td><td>助手</td>
    <td class="TrainingTimeData"><ul class="TrainingTimeDataList">{slots}</ul></td><td>5</td>
    <td class="TrainingLoad">馬也</td><td class="Training_Critic">好気配</td><td class="Rank_B">B</td></tr>
    {extra}</table>'''


def mapping(html, tmp_path):
    path = tmp_path / "same-page-help.txt"
    path.write_text("Synthetic same-page hill: 4F,3F,2F,1F with leading blank", encoding="utf-8")
    return {"schema_version": 1, "fixture_sha256": audit.text_sha256(html), "race_id": RACE,
            "evidence": [{"source_file": path.name, "sha256": audit.sha256(path),
                          "description": "Synthetic exact fixture and help guard test"}],
            "rules": [{"course_raw": "栗坂", "slot_count": 5, "required_blank_ordinals": [1],
                       "mapping_basis": "Synthetic explicit test only", "slots": [
                           {"ordinal": n, "distance_furlongs": 6 - n, "lap_from_furlongs": 6 - n,
                            "lap_to_furlongs": 5 - n} for n in range(2, 6)]}]}


def projection_row(number=3, **changes):
    result = {"horse_number": number, "horse_name": f"テスト馬{number}",
              "horse_href": f"https://db.netkeiba.com/horse/{2020000000 + number}/",
              "training_date": "2026/09/16(水)", "course": "栗坂", "track_condition": "良",
              "rider": "助手", "slot_texts": ["-", "55.3 (15.8)", "39.5 (14.1)", "25.4 (12.7)", "12.7 (12.7)"],
              "pace": "馬也", "grade": "B", "comment_exists": True}
    result.update(changes)
    return result


def speed_capture(numbers=(1, 3), projected=False, **changes):
    order = list(audit.CELL_FIELDS)
    rows = []
    for number in numbers:
        cells = [{"class": cls, "hidden_sort_text": "1100", "display_text": str(100 + i)}
                 if projected else f'<td class="{cls}"><span class="Sort_Function_Data_Hidden">1100</span>{100+i}</td>'
                 for i, cls in enumerate(order)]
        rows.append({"horse_number": number, "horse_name": f"テスト馬{number}",
                     "horse_href": f"https://db.netkeiba.com/horse/{2020000000 + number}/", "cells": cells})
    return capture(source_url=f"https://race.netkeiba.com/race/speed.html?race_id={RACE}",
                   field_order=order, rows=rows, **changes)


def db_rows(records):
    return ([{key: r[key] for key in audit.SPEED_FIELDS} for r in records],
            [{key: r[key] for key in audit.IDENTITY_FIELDS} for r in records])


def test_bound_html_mapping_exposes_three_hill_label_conflicts(tmp_path):
    html = html_fixture('<li>-</li><li>55.3<span class="RapTime">(15.8)</span></li>'
                        '<li>39.5<span class="RapTime">(14.1)</span></li>'
                        '<li>25.4<span class="RapTime">(12.7)</span></li>'
                        '<li>12.7<span class="RapTime">(12.7)</span></li>')
    result = audit.analyze_training_html(capture(table_html=html), HASH, mapping(html, tmp_path), tmp_path)
    assert [r["old_classification"] for r in result["comparison"]] == [
        "SOURCE_MISSING", "VALUE_CORRECT_COLUMN_WRONG", "VALUE_CORRECT_COLUMN_WRONG",
        "VALUE_CORRECT_COLUMN_WRONG", "EXACT_CORRECT"]
    assert result["comparison"][0]["missing_kind"] == "LAYOUT_PADDING"
    assert result["comparison"][1]["displayed_distance_label"] == 4
    assert result["comparison"][1]["displayed_lap_interval"] == [4, 3]
    assert result["records"][0]["days_before_race"] == 4
    assert result["summary"]["model_input_permitted"] is False


@pytest.mark.parametrize("change", ["hash", "evidence", "traversal", "race", "shape"])
def test_mapping_fails_closed_on_unbound_evidence(tmp_path, change):
    html = html_fixture('<li>-</li><li>55.3</li><li>39.5</li><li>25.4</li><li>12.7</li>')
    contract = mapping(html, tmp_path)
    if change == "hash": contract["fixture_sha256"] = "b" * 64
    if change == "evidence": contract["evidence"][0]["sha256"] = "b" * 64
    if change == "traversal": contract["evidence"][0]["source_file"] = "../outside.txt"
    if change == "race": contract["race_id"] = "202609040611"
    if change == "shape": contract["rules"][0]["slot_count"] = 4
    with pytest.raises((ValueError, FileNotFoundError)):
        audit.analyze_training_html(capture(table_html=html), HASH, contract, tmp_path)


def test_unmapped_html_never_claims_correct_distance():
    result = audit.analyze_training_html(capture(table_html=html_fixture('<li>55.3</li>')), HASH)
    assert result["comparison"][0]["old_classification"] is None
    assert result["comparison"][0]["new_classification"] is None
    assert result["summary"]["unclassified_distance_slots"] == 1


def test_html_legacy_missing_nested_value_and_truncation_are_distinct():
    html = html_fixture('<li><b>82.4</b></li>' + ''.join(f'<li>{n}.0</li>' for n in range(70, 10, -10)), course="ＣＷ")
    result = audit.analyze_training_html(capture(table_html=html), HASH)
    assert result["comparison"][0]["old_classification"] == "VALUE_MISSING"
    assert result["summary"]["old_classification_counts"]["TRUNCATED"] == 2


def test_text_projection_preserves_missing_and_multiple_events_without_fake_html(monkeypatch):
    def forbidden():
        raise AssertionError("legacy table parser must not run on text projection")
    monkeypatch.setattr(audit.legacy_training, "load_old_parser", forbidden)
    result = audit.analyze_training_projection(capture(rows=[projection_row(), projection_row()]), HASH)
    assert result["comparison"] == []
    assert result["summary"]["old_parser_comparison"] == "NOT_RUN_PROJECTION_ONLY"
    assert result["summary"]["horses_visible"] == 1
    assert result["summary"]["training_events"] == 2
    assert [r["training_sequence"] for r in result["records"]] == [1, 2]
    assert result["records"][0]["slots"][1]["cumulative_value"] == 55.3
    assert result["records"][0]["slots"][1]["raw_html"] is None
    assert all(r["is_final_workout_verified"] is False for r in result["records"])
    assert all(s["distance_furlongs"] is None for r in result["records"] for s in r["slots"])


def test_optional_captured_li_must_match_projected_text():
    row = projection_row(slot_texts=["55.3 (15.8)"], slot_html=['<li><b>55.3</b><span>(15.8)</span></li>'])
    result = audit.analyze_training_projection(capture(rows=[row]), HASH)
    assert result["records"][0]["slots"][0]["cumulative_value"] == 55.3
    row["slot_texts"] = ["55.4 (15.8)"]
    with pytest.raises(ValueError, match="disagree"):
        audit.analyze_training_projection(capture(rows=[row]), HASH)


@pytest.mark.parametrize("projected", [True, False])
def test_speed_variable_rosters_and_reverse_recency_fields(projected):
    records = audit.parse_speed_projection(speed_capture((2, 5, 9), projected=projected), HASH)
    assert [r["horse_number"] for r in records] == [2, 5, 9]
    assert records[0]["max_index"] == 100
    assert records[0]["index_last"] == 104
    assert records[0]["index_2ago"] == 105
    assert records[0]["index_3ago"] == 106
    assert records[0]["cell_evidence"]["max_index"]["sanitized_html"] is None if projected else True


@pytest.mark.parametrize(("text", "expected"), [("108***", 108), ("未", None), ("-", None),
                                                 ("***", None), ("0", 0), ("-12", -12), ("108.5", None)])
def test_speed_text_projection_scalar_values(text, expected):
    source = speed_capture((1,), projected=True)
    source["rows"][0]["cells"][0]["display_text"] = text
    assert audit.parse_speed_projection(source, HASH)[0]["max_index"] == expected


@pytest.mark.parametrize("change", ["duplicate", "class", "count", "hidden", "roster"])
def test_speed_capture_rejects_structural_ambiguity(change):
    source = speed_capture()
    if change == "duplicate": source["rows"].append(copy.deepcopy(source["rows"][0]))
    if change == "class": source["rows"][0]["cells"][0] = '<td class="sk__index1">10</td>'
    if change == "count": source["rows"][0]["cells"].pop()
    if change == "hidden": source["rows"][0]["cells"][0] = ('<td class="sk__max_index">'
        '<span class="Sort_Function_Data_Hidden">1100</span><span class="Sort_Function_Data_Hidden">1100</span>100</td>')
    if change == "roster": source["expected_horse_numbers"] = [1, 2, 3]
    with pytest.raises(ValueError):
        audit.parse_speed_projection(source, HASH)


def test_speed_missing_db_is_separate_from_both_missing_and_source_failure():
    records = audit.parse_speed_projection(speed_capture((1, 2, 3)), HASH)
    speed, identities = db_rows(records)
    records[0]["max_index"] = speed[0]["max_index"] = None
    records[1]["max_index"] = None
    speed.pop()
    rows, summary = audit.compare_speed(records, speed, identities)
    assert len(rows) == 21
    assert summary["compared_cells"] == 14
    assert summary["exact_match"] == 12
    assert summary["both_missing"] == 1
    assert summary["mismatch"] == 1
    assert summary["relation_counts"]["missing_db_row"] == 7
    assert summary["missing_db_rows_are_source_failures"] is False
    field = summary["per_field"]["max_index"]
    assert field["compared"] == 2 and field["both_missing"] == 1 and field["source_missing_only"] == 1
    assert field["exact_match_rate"] == 0.5
    assert summary["source_relation"] == "UNKNOWN"


def test_identity_mismatch_is_excluded_from_comparison_denominator():
    records = audit.parse_speed_projection(speed_capture(), HASH)
    speed, identities = db_rows(records)
    identities[0]["horse_id"] = "9999999999"
    _, summary = audit.compare_speed(records, speed, identities)
    assert summary["compared_cells"] == 7
    assert summary["relation_counts"]["identity_mismatch"] == 7
    assert summary["mismatch"] == 0
    assert summary["per_field"]["max_index"]["identity_unverified"] == 1


def test_db_duplicate_keys_raise():
    records = audit.parse_speed_projection(speed_capture(), HASH)
    speed, identities = db_rows(records)
    with pytest.raises(ValueError, match="duplicate DB speed"):
        audit.compare_speed(records, speed + [speed[0]], identities)


def test_readonly_db_scope_hash_and_authorizer(tmp_path):
    path = tmp_path / "synthetic.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE speed_figures (" + ",".join(audit.SPEED_FIELDS) + ", outcome)")
        db.execute("CREATE TABLE race_results_ultimate (" + ",".join(audit.IDENTITY_FIELDS) + ", outcome)")
        db.execute("INSERT INTO speed_figures VALUES (" + ",".join("?" * 11) + ")",
                   (RACE, 1, "馬", 100, 101, 102, 103, 104, 105, 106, "forbidden"))
        db.execute("INSERT INTO race_results_ultimate VALUES (?,?,?,?,?)", (RACE, 1, "2020000001", "馬", "forbidden"))
    before = audit.sha256(path)
    speed, identities, receipt = audit.read_frozen_rows(path, before, [RACE])
    assert set(speed[0]) == set(audit.SPEED_FIELDS)
    assert set(identities[0]) == set(audit.IDENTITY_FIELDS)
    assert receipt["sha256_before"] == receipt["sha256_after"] == before == audit.sha256(path)
    assert receipt["query_only"] and receipt["authorizer_enabled"]
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE speed_figures (race_id, outcome)")
        db.set_authorizer(audit.readonly_authorizer)
        db.execute("SELECT race_id FROM speed_figures").fetchall()
        for query in ("SELECT outcome FROM speed_figures", "DELETE FROM speed_figures", "ATTACH DATABASE ':memory:' AS x"):
            with pytest.raises(sqlite3.DatabaseError):
                db.execute(query)


@pytest.mark.parametrize("change", ["mode", "date", "clock", "after", "no_values", "timezone"])
def test_pit_requires_actual_forward_visible_date_clock_and_prestart(change):
    source = capture(capture_mode="forward")
    present = True
    if change == "mode": source["capture_mode"] = "historical"
    if change == "date": source.pop("race_date_visible")
    if change == "clock": source.pop("scheduled_start_time_visible")
    if change == "after": source["observed_at"] = "2026-09-20T08:00:00+00:00"
    if change == "no_values": present = False
    if change == "timezone": source.pop("timezone")
    result = audit.evaluate_forward_pit(source, HASH, present)
    assert result["strict_pit_forward_sample"] is False
    assert result["strict_pit_historical"] is False


def test_pit_derives_source_visible_japan_start_and_keeps_historical_policy_separate():
    result = audit.evaluate_forward_pit(capture(capture_mode="forward"), HASH, values_present=True)
    assert result["scheduled_start_time"] == "2026-09-20T16:30:00+09:00"
    assert result["strict_pit_forward_sample"] is True
    assert result["strict_pit_historical"] is False
    assert result["revision_history_verified"] is False
    assert result["model_input_permitted"] is False


def test_pit_rejects_schedule_visible_evidence_mismatch():
    result = audit.evaluate_forward_pit(capture(capture_mode="forward", scheduled_start_time="2026-09-21T16:30:00+09:00"), HASH)
    assert "SOURCE_DATE_AND_SCHEDULED_START_DISAGREE" in result["reasons"]
    assert not result["strict_pit_forward_sample"]


def test_pit_rejects_wrong_schedule_offset_and_metadata_without_values():
    wrong_offset = capture(capture_mode="forward", scheduled_start_time="2026-09-20T16:30:00+00:00")
    result = audit.evaluate_forward_pit(wrong_offset, HASH, values_present=True)
    assert "SCHEDULED_START_NOT_SOURCE_JAPAN_TIME" in result["reasons"]
    assert not result["strict_pit_forward_sample"]
    metadata_only = audit.evaluate_forward_pit(capture(capture_mode="forward"), HASH)
    assert "NO_CAPTURED_VALUES" in metadata_only["reasons"]


def test_empty_table_does_not_infer_nonexistence():
    result = audit.analyze_training_projection(capture(rows=[]), HASH)
    assert result["summary"]["horses_visible"] == 0
    assert "availability" not in result["summary"]
    assert not result["summary"]["annual_coverage_estimated"]
    assert audit.parse_speed_projection(speed_capture(numbers=()), HASH) == []


def test_capture_binding_rejects_tokens_wrong_race_and_naive_time():
    for source in (capture(source_url=f"https://race.netkeiba.com/race/oikiri.html?race_id={RACE}&token=secret"),
                   capture(source_url="https://race.netkeiba.com/race/oikiri.html?race_id=202609040611"),
                   capture(observed_at="2026-09-20T04:00:00")):
        with pytest.raises(ValueError):
            audit.capture_metadata(source, HASH)


def test_execute_is_append_only_and_rechecks_hash(tmp_path):
    source_path = tmp_path / "capture.json"
    source_path.write_text(json.dumps(capture(rows=[projection_row()]), ensure_ascii=False), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"captures": [{"source_file": source_path.name,
        "sha256": audit.sha256(source_path), "kind": "training_projection"}]}), encoding="utf-8")
    output = tmp_path / "analysis"
    result = audit.execute(manifest, audit.sha256(manifest), output, tmp_path)
    assert result["training_capture_count"] == 1
    assert result["fits_started"] == result["network_calls"] == result["database_writes"] == 0
    assert (output / "analysis-summary.json").exists()
    with pytest.raises(ValueError, match="append-only"):
        audit.execute(manifest, audit.sha256(manifest), output, tmp_path)
    with pytest.raises(ValueError, match="manifest SHA256"):
        audit.execute(manifest, "0" * 64, tmp_path / "bad", tmp_path)


def test_frozen_previous_real_table_regression():
    pilot = audit.legacy_training.PILOT
    html = (pilot / "source/202606040611-training-table.html").read_text(encoding="utf-8")
    contract = json.loads((pilot / "current-page-mapping-contract.json").read_text(encoding="utf-8"))
    source = capture(race_id="202606040611", source_url="https://race.netkeiba.com/race/oikiri.html?race_id=202606040611", table_html=html)
    result = audit.analyze_training_html(source, HASH, contract, pilot)
    assert result["summary"]["slot_count"] == 65
    assert result["summary"]["old_classification_counts"] == {
        "EXACT_CORRECT": 35, "VALUE_CORRECT_COLUMN_WRONG": 21, "SOURCE_MISSING": 9}
    assert result["summary"]["unclassified_distance_slots"] == 0


def current_capture_for_contract(course="栗坂"):
    row = projection_row(horse_name="テスト馬", horse_href="https://db.netkeiba.com/horse/2020101234/", course=course,
                         slot_texts=["-", "55.3", "39.5", "25.4", "12.7"])
    return capture(rows=[row], table_html=html_fixture('<li>-</li><li>55.3</li><li>39.5</li><li>25.4</li><li>12.7</li>', course=course),
                   guide_clock_text=["6F、5F、4F、3F、1F 6F-5F、5F-4F、4F-3F、3F-1F、1F-ゴール",
                                     "4F、3F、2F、1F 4F-3F、3F-2F、2F-1F、1F-ゴール"])


def test_contract_builder_preserves_exact_badge_label_and_binds_capture():
    source = current_capture_for_contract("栗坂 一番時計")
    source["rows"][0]["course"] = "栗坂\n一番時計"
    contract, crosscheck = audit.build_capture_mapping_contract(source, HASH, "captures/synthetic.json")
    assert crosscheck["status"] == "PASS"
    assert contract["source_projection_sha256"] == HASH
    assert contract["fixture_sha256"] == audit.text_sha256(source["table_html"])
    assert contract["rules"][0]["course_raw"] == "栗坂 一番時計"
    assert contract["rules"][0]["required_blank_ordinals"] == [1]
    assert not contract["historical_schema_mapping_permitted"]


def test_contract_builder_does_not_extrapolate_unknown_course():
    source = current_capture_for_contract("ＤＰ 一番時計")
    contract, _ = audit.build_capture_mapping_contract(source, HASH, "captures/synthetic.json")
    assert contract["rules"] == []
    assert contract["unsupported_courses"][0]["course_raw"] == "ＤＰ 一番時計"


def test_contract_builder_requires_all_clock_and_lap_help():
    source = current_capture_for_contract()
    source["guide_clock_text"] = ["4F、3F、2F、1F"]
    contract, _ = audit.build_capture_mapping_contract(source, HASH, "captures/synthetic.json")
    assert contract["rules"] == []


def test_dom_projection_crosscheck_rejects_slot_and_identity_differences():
    source = current_capture_for_contract()
    source["rows"][0]["slot_texts"][1] = "54.3"
    with pytest.raises(ValueError, match="disagree"):
        audit.build_capture_mapping_contract(source, HASH, "captures/synthetic.json")
    source = current_capture_for_contract()
    source["rows"][0]["horse_name"] = "別馬"
    with pytest.raises(ValueError, match="disagree"):
        audit.build_capture_mapping_contract(source, HASH, "captures/synthetic.json")


def test_capture_specific_analysis_writes_contract_crosscheck_and_csv_once(tmp_path):
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(current_capture_for_contract(), ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "analysis"
    summary = audit.analyze_training_capture_to_directory(path, audit.sha256(path), output, tmp_path)
    assert summary["mapped_distance_slots"] == 4
    assert summary["old_classification_counts"]["VALUE_CORRECT_COLUMN_WRONG"] == 3
    assert (output / "current-slot-audit.csv").read_text(encoding="utf-8").count("\n") == 6
    assert (output / "mapping-contract.json").exists()
    with pytest.raises(ValueError, match="append-only"):
        audit.analyze_training_capture_to_directory(path, audit.sha256(path), output, tmp_path)


def test_missing_pace_null_and_empty_are_same_captured_absence():
    source = current_capture_for_contract()
    source["table_html"] = source["table_html"].replace('<td class="TrainingLoad">馬也</td>', '<td class="TrainingLoad"></td>')
    source["rows"][0]["pace"] = None
    _, crosscheck = audit.build_capture_mapping_contract(source, HASH, "captures/synthetic.json")
    assert crosscheck["status"] == "PASS"


def test_explicit_light_training_marker_is_source_clock_absence():
    html = html_fixture('<li>-</li><li>-</li><li>-</li><li>-</li><li>中間軽め</li>', course="")
    result = audit.analyze_training_html(capture(table_html=html), HASH)
    assert result["comparison"][4]["old_classification"] == "SOURCE_MISSING"
    assert result["comparison"][4]["new_classification"] == "SOURCE_MISSING"
    assert result["comparison"][4]["missing_kind"] == "EXPLICIT_LIGHT_TRAINING_NO_CLOCK"
    assert result["comparison"][4]["slot"]["raw_text"] == "中間軽め"


def test_single_row_horse_and_training_layout_exposes_legacy_selector_failure():
    html = html_fixture('<li>-</li><li>55.3</li><li>39.5</li><li>25.4</li><li>12.7</li>')
    html = html.replace('</tr>\n    <tr class="HorseList"><td class="Training_Day">', '<td class="Training_Day">')
    result = audit.analyze_training_html(capture(table_html=html), HASH)
    assert result["summary"]["old_record_count"] == 0
    assert result["summary"]["experimental_record_count"] == 1
    assert result["summary"]["old_classification_counts"] == {"SELECTOR_ERROR": 5}
    assert result["records"][0]["horse_name"] == "テスト馬"
    assert result["records"][0]["slots"][1]["cumulative_value"] == 55.3


def lzw_packet(raw):
    # Independent encoder used only to generate synthetic transport fixtures.
    table = {bytes((n,)): n for n in range(256)}
    word, codes = b"", []
    for byte in raw:
        char = bytes((byte,))
        if word + char in table:
            word += char
        else:
            codes.append(table[word])
            if len(table) < 65536:
                table[word + char] = len(table)
            word = char
    if word:
        codes.append(table[word])
    data = base64.b64encode(b"".join(code.to_bytes(2, "little") for code in codes)).decode("ascii")
    return {"codec": ingest.CODEC, "canonical_sha256": hashlib.sha256(raw).hexdigest(), "data": data}


def test_lzw_browser_utf8_known_vector():
    raw = ingest.decode_lzw16le("YQBiAGMA5gCXAKUA5gCcAKwA6ACqAJ4A")
    assert raw.decode("utf-8") == "abc日本語"
    assert hashlib.sha256(raw).hexdigest() == "89cc339f5ee0694c711634a248d4659b686242596c9bb3e509c289f12ce2cc4f"


@pytest.mark.parametrize("text", ["a", "aaaaaaaaaaaaaaaa", "ABABABA", "日本語" * 50])
def test_lzw_dictionary_special_case_and_unicode(text):
    raw = ingest.canonical_bytes({"value": text})
    decoded, value = ingest.decode_packet(lzw_packet(raw))
    assert decoded == raw and value == {"value": text}


@pytest.mark.parametrize("data", ["!notbase64", "AA==", "AAE=", "YQAsAQ=="])
def test_lzw_invalid_codes_and_base64_fail(data):
    with pytest.raises(ValueError):
        ingest.decode_lzw16le(data)


def test_lzw_size_bound_prevents_large_output():
    packet = lzw_packet(b"a" * 200)
    with pytest.raises(ValueError, match="size bound"):
        ingest.decode_lzw16le(packet["data"], max_bytes=32)


@pytest.mark.parametrize("raw", [b'{"z":1,"a":2}', b'{"a":1}\n', b'not JSON', b'{"a":1,"a":2}',
                                  b'{"a":NaN}', b'[]', b'{"a":"\xff"}'])
def test_transport_rejects_noncanonical_nonjson_and_duplicate_keys(raw):
    with pytest.raises(ValueError):
        ingest.decode_packet(lzw_packet(raw))


@pytest.mark.parametrize("value", [{"password": "anything"}, {"text": "user@example.com"},
                                    {"table_html": '<table onclick="bad()"></table>'},
                                    {"table_html": '<script>bad()</script>'},
                                    {"text": "bearer " + "x" * 30},
                                    {"source_url": "https://example.com/?token=short"}])
def test_transport_rejects_sensitive_or_unsanitized_content_without_echo(value):
    with pytest.raises(ValueError, match="possible sensitive") as failure:
        ingest.decode_packet(lzw_packet(ingest.canonical_bytes(value)))
    assert "example.com" not in str(failure.value)
    assert "bad()" not in str(failure.value)


def test_transport_hash_mismatch_rejected_before_content_parse():
    packet = lzw_packet(b'{"race_id":"202606040612"}')
    packet["canonical_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        ingest.decode_packet(packet)


def test_ingest_exclusive_bytes_exact_and_path_guard(tmp_path):
    transport = tmp_path / "transport"
    transport.mkdir()
    source = transport / "packet.json"
    raw = ingest.canonical_bytes(capture(rows=[projection_row()]))
    source.write_text(json.dumps(lzw_packet(raw)), encoding="utf-8")
    output = tmp_path / "captures" / "capture.json"
    receipt = ingest.ingest(source, output, tmp_path)
    assert output.read_bytes() == raw and not raw.endswith(b"\n")
    assert receipt["canonical_sha256"] == hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError, match="append-only"):
        ingest.ingest(source, output, tmp_path)
    with pytest.raises(ValueError, match="outside isolated"):
        ingest.ingest(source, tmp_path.parent / "outside.json", tmp_path)
    with pytest.raises(ValueError, match="inside captures"):
        ingest.ingest(source, tmp_path / "other.json", tmp_path)


def test_lzss_browser_utf8_known_vector():
    raw = ingest.decode_lzss16le("AGFiY+aXpeacEKzoqp4MABU=")
    assert raw.decode("utf-8") == "abc日本語abc日本語abc日本語"


def test_lzss_overlapping_reference():
    # Flag bit 1 marks a distance-1 length-258 reference after the literal a.
    encoded = base64.b64encode(bytes([2, ord("a"), 1, 0, 255])).decode("ascii")
    assert ingest.decode_lzss16le(encoded) == b"a" * 259
    with pytest.raises(ValueError, match="size bound"):
        ingest.decode_lzss16le(encoded, max_bytes=258)


@pytest.mark.parametrize("packed", [b"", b"\x00", b"\x01\x01\x00\x00", b"\x02a\x00\x00\x00",
                                      b"\x02a\x02\x00\x00", b"\x02a\x01", b"\x80a"])
def test_lzss_invalid_empty_distance_truncation_and_flags(packed):
    with pytest.raises(ValueError):
        ingest.decode_lzss16le(base64.b64encode(packed).decode("ascii"))


def test_lzss_packet_canonical_hash_and_both_codecs_supported():
    raw = ingest.canonical_bytes({"race_id": RACE, "value": "日本語"})
    # A literal-only stream verifies final partial groups independently.
    packed = b"".join(b"\x00" + raw[index:index + 8] for index in range(0, len(raw), 8))
    packet = {"codec": ingest.LZSS_CODEC, "canonical_sha256": hashlib.sha256(raw).hexdigest(),
              "data": base64.b64encode(packed).decode("ascii")}
    assert ingest.decode_packet(packet)[0] == raw
    assert ingest.decode_packet(lzw_packet(raw))[0] == raw
    packet["canonical_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        ingest.decode_packet(packet)


def historical_summary_batch(key="records"):
    row = capture(schema="phase2-historical-availability-projection-v1", source_page_type="training",
                  rows=2, table_present=True, horse_numbers=[1, 2], counts={"horses_with_numeric": 2})
    return {"schema": "phase2-historical-availability-batch-v1", key: [row], "raw_http_available": False}


@pytest.mark.parametrize("key", ["records", "projections"])
def test_historical_batch_ingest_keeps_exact_bytes_and_integer_row_counts(tmp_path, key):
    payload = historical_summary_batch(key)
    raw = ingest.canonical_bytes(payload)
    transport = tmp_path / "transport"
    transport.mkdir()
    path = transport / "batch.json"
    path.write_text(json.dumps(lzw_packet(raw)), encoding="utf-8")
    output = tmp_path / "captures" / "historical.json"
    result = ingest.ingest(path, output, tmp_path)
    assert output.read_bytes() == raw
    assert result["payload_validation"]["record_array_key"] == key
    assert result["payload_validation"]["historical_strict_pit"] is False
    assert json.loads(output.read_text(encoding="utf-8"))[key][0]["rows"] == 2


@pytest.mark.parametrize("mutation", ["url", "race", "raw", "mode", "naive_time", "rows_list", "empty", "too_many"])
def test_each_historical_record_fails_closed_on_invalid_binding(mutation):
    payload = historical_summary_batch()
    row = payload["records"][0]
    if mutation == "url": row["source_url"] = f"https://example.com/race/oikiri.html?race_id={RACE}"
    if mutation == "race": row["race_id"] = "202609040611"
    if mutation == "raw": row["raw_http_available"] = True
    if mutation == "mode": row["capture_mode"] = "forward"
    if mutation == "naive_time": row["observed_at"] = "2026-09-20T04:00:00"
    if mutation == "rows_list": row["rows"] = []
    if mutation == "empty": payload["records"] = []
    if mutation == "too_many": payload["records"] = [row] * 81
    with pytest.raises(ValueError):
        ingest.validate_capture_payload(payload)


def test_single_master_and_intermediate_bounded_source_binding():
    master = capture(schema="phase2-master-projection-v1", source_page_type="master",
                     source_url=f"https://race.netkeiba.com/race/speed.html?race_id={RACE}&master=1")
    intermediate = capture(schema="phase2-intermediate-projection-v1", source_page_type="training_intermediate",
                           source_url=f"https://race.netkeiba.com/race/oikiri.html?race_id={RACE}&type=1")
    assert ingest.validate_capture_payload(master)["record_count"] == 1
    assert audit.capture_metadata(master, HASH)["source_url"].endswith("&master=1")
    assert ingest.validate_capture_payload(intermediate)["record_count"] == 1


@pytest.mark.parametrize("suffix", ["master=0", "master=2", "master=1&master=1"])
def test_unobserved_or_duplicate_master_value_fails_closed(suffix):
    source = capture(source_url=f"https://race.netkeiba.com/race/speed.html?race_id={RACE}&{suffix}")
    with pytest.raises(ValueError, match="unobserved Master"):
        ingest.validate_capture_payload(source)
    with pytest.raises(ValueError, match="unobserved Master"):
        audit.capture_metadata(source, HASH)


def test_batch_nested_secret_marker_is_rejected_before_save():
    payload = historical_summary_batch()
    payload["records"][0]["access_token"] = "synthetic-only"
    with pytest.raises(ValueError, match="possible sensitive"):
        ingest.decode_packet(lzw_packet(ingest.canonical_bytes(payload)))


def intermediate_capture_and_reference():
    source = capture(source_url=f"https://race.netkeiba.com/race/oikiri.html?race_id={RACE}&type=1",
                     capture_mode="current", observed_at="2026-09-20T08:00:00+00:00",
                     selected_view="中間の調教全て", rows=[projection_row(), projection_row(training_date="2026/09/09(水)")])
    reference = audit.analyze_training_projection(capture(rows=[projection_row()]), HASH)["records"]
    metadata = {"race_id": RACE, "source_html_sha256": "b" * 64, "source_view": "最終追切のみ"}
    return source, reference, metadata


def test_intermediate_multiple_events_preserves_sequence_and_poststart_pit():
    source, reference, metadata = intermediate_capture_and_reference()
    result = intermediate.audit_events(source, HASH, reference, metadata)
    assert [event["training_sequence"] for event in result["events"]] == [1, 2]
    assert result["summary"]["horses_with_multiple_events"] == 1
    assert result["summary"]["latest_candidate_and_reference_agree_horses"] == 1
    assert result["horses"][0]["source_date_order"] == "DESCENDING"
    assert result["events"][0]["matches_prior_final_only_display"]
    assert all(event["is_final_workout_verified"] is False and "is_last_training" not in event for event in result["events"])
    assert not result["summary"]["forward_pit"]["strict_pit_forward_sample"]
    assert "CAPTURE_NOT_BEFORE_SCHEDULED_START" in result["summary"]["forward_pit"]["reasons"]


def test_intermediate_latest_date_does_not_override_final_display_reference():
    source, reference, metadata = intermediate_capture_and_reference()
    source["rows"].insert(0, projection_row(training_date="2026/09/19(土)"))
    result = intermediate.audit_events(source, HASH, reference, metadata)
    assert result["summary"]["reference_match_not_latest_date_horses"] == 1
    assert result["horses"][0]["final_reference_match_sequences"] == [2]
    assert result["horses"][0]["latest_date_candidate_sequences"] == [1]
    assert result["summary"]["final_workout_verified_events"] == 0


def test_intermediate_duplicate_matching_events_remain_ambiguous():
    source, reference, metadata = intermediate_capture_and_reference()
    source["rows"].insert(0, projection_row())
    result = intermediate.audit_events(source, HASH, reference, metadata)
    assert result["horses"][0]["final_reference_match_relation"] == "MATCHING_DISPLAY_EVENTS_AMBIGUOUS"
    assert result["summary"]["latest_candidate_and_reference_agree_horses"] == 0


@pytest.mark.parametrize("change", ["view", "url", "mode", "race"])
def test_intermediate_source_view_and_race_binding_are_required(change):
    source, reference, metadata = intermediate_capture_and_reference()
    if change == "view": source["selected_view"] = "最終追切のみ"
    if change == "url": source["source_url"] = source["source_url"].replace("type=1", "type=2")
    if change == "mode": source["capture_mode"] = "forward"
    if change == "race": metadata["race_id"] = "202609040611"
    with pytest.raises(ValueError):
        intermediate.audit_events(source, HASH, reference, metadata)
