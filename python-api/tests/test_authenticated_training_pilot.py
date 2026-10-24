"""Run with --noconftest to avoid importing the application's scrape/DB fixtures."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import audit_authenticated_training_pilot as audit


def fixture(slots, course="栗坂", date="2026/09/16(水)", extra=""):
    return f'''<table class="OikiriTable"><tr><th>調教タイム</th></tr>
    <tr id="tr_3" class="HorseList"><td class="Umaban">3</td>
    <td class="Horse_Info"><div class="Horse_Name"><a href="https://db.netkeiba.com/horse/2020101234/">テスト馬</a></div></td>
    <td class="TrainingReview_Cell">調教コメント</td></tr>
    <tr class="HorseList"><td class="Training_Day">{date}</td><td>{course}</td><td>良</td><td>助手</td>
    <td class="TrainingTimeData"><ul class="TrainingTimeDataList">{slots}</ul></td><td>5</td>
    <td class="TrainingLoad">馬也</td><td class="Training_Critic">好気配</td><td class="Rank_B">B</td></tr>
    {extra}</table>'''


def test_default_has_no_distance_inference_and_preserves_placeholders():
    html = fixture('<li>-</li><li>55.3<span class="RapTime">(15.8)</span></li><li>39.5</li><li>25.4</li><li>12.7</li>')
    parsed = audit.parse_experimental(html, "202606040611")
    record = parsed["records"][0]
    assert record["horse_id"] == "2020101234"
    assert record["training_date"] == "2026-09-16"
    assert record["training_date_raw"] == "2026/09/16(水)"
    assert record["course_raw"] == "栗坂"
    assert len(record["slots"]) == 5
    assert record["slots"][0]["is_placeholder"]
    assert record["slots"][1]["cumulative_value"] == 55.3
    assert record["slots"][1]["lap_value"] == 15.8
    assert all(slot["distance_furlongs"] is None for slot in record["slots"])
    assert not record["strict_pit"] and not record["model_input_permitted"]


def test_nested_values_and_more_than_five_slots_are_not_dropped():
    html = fixture('<li><b>82.4</b><span class="RapTime"><em>(14.3)</em></span></li>' +
                   ''.join(f'<li>{n}.0</li>' for n in range(70, 10, -10)))
    experimental = audit.parse_experimental(html, "202606040611")
    old = audit.load_old_parser()(html, "202606040611")
    slots = experimental["records"][0]["slots"]
    assert len(slots) == 7
    assert slots[0]["cumulative_value"] == 82.4 and slots[0]["lap_value"] == 14.3
    assert slots[0]["text_nodes"][0]["ancestors"] == [{"tag": "b", "classes": []}]
    assert "<b>82.4</b>" in slots[0]["raw_html"]
    assert old[0]["time_6f"] is None
    comparison, summary = audit.compare(old, experimental)
    assert "CUMULATIVE_VALUE_DIFFERS" in comparison[0]["findings"]
    assert summary["finding_counts"]["SLOT_BEYOND_OLD_FIVE_SLOT_LIMIT"] == 2
    assert summary["mapped_distance_slots"] == 0


def test_multiple_candidates_remain_ambiguous_and_keep_raw_text():
    parsed = audit.parse_experimental(fixture('<li><b>55.3</b><i>54.0</i><span>(12.1)</span><span>(12.2)</span></li>'), "202606040611")
    slot = parsed["records"][0]["slots"][0]
    assert slot["cumulative_candidates"] == [55.3, 54.0]
    assert slot["lap_candidates"] == [12.1, 12.2]
    assert slot["cumulative_value"] is None and slot["lap_value"] is None
    assert slot["raw_text"] == "55.354.0(12.1)(12.2)"


def test_lap_parentheses_and_number_split_across_nested_nodes():
    parsed = audit.parse_experimental(fixture('<li><b>55.3</b><span class="RapTime">(<em>15.8</em>)</span></li>'), "202606040611")
    slot = parsed["records"][0]["slots"][0]
    assert slot["cumulative_candidates"] == [55.3]
    assert slot["lap_candidates"] == [15.8]


def test_invalid_date_is_retained_and_orphan_does_not_invent_identity():
    html = fixture('<li>12.3</li>', date="2026/02/30(水)")
    parsed = audit.parse_experimental(html, "202606040611")
    assert parsed["records"][0]["training_date"] is None
    assert "INVALID_OR_MISSING_FULL_DATE" in parsed["records"][0]["warnings"]
    orphan = '<table class="OikiriTable"><tr><td class="Training_Day">2026/09/16</td></tr></table>'
    row = audit.parse_experimental(orphan, "202606040611")["records"][0]
    assert "horse_number" not in row and "NO_PRECEDING_HORSE_HEADER" in row["warnings"]


def test_ast_extraction_does_not_execute_top_level_imports_or_scraper():
    source = audit.OLD_SOURCE.read_text(encoding="utf-8")
    source = 'raise RuntimeError("top level must not run")\nimport nonexistent_secret_loader\n' + source
    parser = audit.extract_old_parser(source)
    assert len(parser(fixture('<li>12.3</li>'), "202606040611")) == 1
    assert "scrape_oikiri" not in parser.__globals__
    assert "asyncio" not in parser.__globals__
    assert "scraping" not in parser.__globals__


def contract():
    return {"schema_version": 1, "fixture_sha256": "a" * 64,
            "evidence": [{"source_file": "synthetic-only.html", "sha256": "b" * 64,
                          "description": "Synthetic contract for guard testing, no real mapping claim"}],
            "rules": [{"course_raw": "栗坂", "slot_count": 5, "required_blank_ordinals": [1],
                       "mapping_basis": "Synthetic explicit format contract only",
                       "slots": [{"ordinal": ordinal, "distance_furlongs": 6 - ordinal,
                                  "lap_from_furlongs": 6 - ordinal, "lap_to_furlongs": 5 - ordinal}
                                 for ordinal in range(2, 6)]}]}


def test_capture_bound_contract_reports_old_hill_label_conflicts():
    html = fixture('<li>-</li><li>55.3</li><li>39.5</li><li>25.4</li><li>12.7</li>')
    parsed = audit.parse_experimental(html, "202606040611")
    audit.apply_mapping_contract(parsed, contract(), "a" * 64)
    old = audit.load_old_parser()(html, "202606040611")
    rows, summary = audit.compare(old, parsed)
    assert [slot["distance_furlongs"] for slot in parsed["records"][0]["slots"]] == [None, 4, 3, 2, 1]
    assert rows[0]["slot"]["mapping_status"] == "DOCUMENTED_PADDING"
    assert summary["finding_counts"]["DISTANCE_LABEL_CONFLICT_WITH_CONTRACT"] == 3
    assert summary["finding_counts"]["LAP_LABEL_CONFLICT_WITH_CONTRACT"] == 3
    assert summary["model_input_gate"] == "NO" and summary["historical_corrections_applied"] == 0


@pytest.mark.parametrize("change", ["hash", "count", "blank", "evidence", "duplicate", "ordinal"])
def test_mapping_fail_closed_on_wrong_capture_or_shape(change):
    html = fixture('<li>-</li><li>55.3</li><li>39.5</li><li>25.4</li><li>12.7</li>')
    parsed, mapping = audit.parse_experimental(html, "202606040611"), contract()
    if change == "hash": mapping["fixture_sha256"] = "c" * 64
    if change == "count": mapping["rules"][0]["slot_count"] = 4
    if change == "blank": mapping["rules"][0]["required_blank_ordinals"] = [2]
    if change == "evidence": mapping["evidence"] = []
    if change == "duplicate": mapping["rules"].append(mapping["rules"][0])
    if change == "ordinal": mapping["rules"][0]["slots"][0]["ordinal"] = -1
    with pytest.raises(ValueError):
        audit.apply_mapping_contract(parsed, mapping, "a" * 64)


def test_exact_course_not_guessed_from_normalized_course_name():
    parsed = audit.parse_experimental(fixture('<li>55.3</li>', course="ＣＷ"), "202606040611")
    audit.apply_mapping_contract(parsed, contract(), "a" * 64)
    assert parsed["records"][0]["slots"][0]["distance_furlongs"] is None


def test_output_guard_rejects_path_outside_pilot(tmp_path):
    with pytest.raises(ValueError, match="isolated pilot"):
        audit._inside_pilot(tmp_path / "comparison")
