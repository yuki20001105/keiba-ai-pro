from __future__ import annotations

from copy import deepcopy
import hashlib

import pytest

from scraping.source_normalization import (
    finish_column_evidence_from_html,
    normalize_field_condition,
    normalize_finish_position,
    normalize_race_payload,
)


RACE_ID = "201606010101"
URL = f"https://db.netkeiba.com/race/{RACE_ID}/"
HTML = """<table class="race_table_01">
<tr><th>着順</th><th>枠 番</th><th>馬 番</th><th>馬名</th></tr>
<tr><td>1</td><td>1</td><td>1</td><td>First</td></tr>
<tr><td>2(降)</td><td>2</td><td>3</td><td>Demoted</td></tr>
</table>"""


@pytest.mark.parametrize("raw,expected", [("良", "良"), ("稍", "稍重"), ("稍重", "稍重"),
                                            ("重", "重"), ("不", "不良"), ("不良", "不良")])
def test_going_has_only_explicit_lossless_aliases(raw, expected):
    actual = normalize_field_condition(raw)
    assert actual["value"] == expected
    assert actual["raw"] == raw
    assert actual["status"] == "recognized"
    assert actual["changed"] is (raw != expected)


@pytest.mark.parametrize("raw", ["荒場", "稍不", "Heavy", "**", "不明"])
def test_unknown_going_is_not_guessed(raw):
    actual = normalize_field_condition(raw)
    assert actual["value"] == actual["raw"] == raw
    assert actual["status"] == "unrecognized" and actual["changed"] is False


@pytest.mark.parametrize("raw", [None, "", "  "])
def test_missing_going_is_not_zero_or_normal(raw):
    actual = normalize_field_condition(raw)
    assert actual["value"] == raw and actual["status"] == "missing"


@pytest.mark.parametrize("raw", ["取", "取消", "除外", "中", "中止", "失", "失格"])
def test_legitimate_nonfinishers_are_not_fabricated_numeric_results(raw):
    actual = normalize_finish_position(raw)
    assert actual["value"] == actual["raw"] == raw
    assert actual["status"] == "nonfinisher" and actual["changed"] is False


@pytest.mark.parametrize("raw", ["2(降)", "２（降）", " 2 ( 降 ) "])
def test_demotion_requires_actual_result_column_race_and_runner_evidence(raw):
    unverified = normalize_finish_position(raw, race_id=RACE_ID, horse_number=3)
    assert unverified["value"] == raw and unverified["status"] == "quarantined"
    evidence = finish_column_evidence_from_html(HTML, race_id=RACE_ID, source_url=URL)
    actual = normalize_finish_position(raw, column_evidence=evidence, race_id=RACE_ID, horse_number=3)
    assert actual["value"] == 2 and actual["raw"] == raw
    assert actual["annotation"] == "降" and actual["status"] == "source_verified_demotion"
    assert actual["evidence_decoded_html_sha256"] == hashlib.sha256(HTML.encode()).hexdigest()


@pytest.mark.parametrize("patch", [
    {"race_id": "201606010102"}, {"column_header": "入線順位"}, {"horse_number_header": "枠番"},
    {"source_url": URL.replace("https:", "http:")},
    {"source_url": URL.replace("db.netkeiba.com", "not-netkeiba.example")},
    {"decoded_html_sha256": "unverified"}, {"finish_labels_by_horse_number": {"3": "3(降)"}},
    {"finish_labels_by_horse_number": None},
])
def test_mismatched_or_malformed_evidence_fails_closed(patch):
    evidence = finish_column_evidence_from_html(HTML, race_id=RACE_ID, source_url=URL)
    evidence.update(patch)
    actual = normalize_finish_position("2(降)", column_evidence=evidence, race_id=RACE_ID, horse_number=3)
    assert actual["value"] == "2(降)" and actual["status"] == "quarantined"


def test_evidence_cannot_be_transferred_to_another_horse():
    evidence = finish_column_evidence_from_html(HTML, race_id=RACE_ID, source_url=URL)
    actual = normalize_finish_position("2(降)", column_evidence=evidence, race_id=RACE_ID, horse_number=1)
    assert actual["status"] == "quarantined"


@pytest.mark.parametrize("raw", ["2(失)", "降2", "2降", "同着", 0, 100, -1, True, 1.5])
def test_unsupported_finish_tokens_are_not_invented(raw):
    actual = normalize_finish_position(raw)
    assert actual["value"] == raw and actual["status"] == "quarantined"


@pytest.mark.parametrize("url", [
    "https://db.netkeiba.com/race/201606010102/",
    "https://db.netkeiba.com/race/201606010101/?race_id=201606010102",
    "https://db.netkeiba.com/race/list/", "https://db.netkeiba.com:8443/race/201606010101/",
    "https://user:password@db.netkeiba.com/race/201606010101/",
])
def test_unbound_or_conflicting_source_urls_do_not_establish_evidence(url):
    assert finish_column_evidence_from_html(HTML, race_id=RACE_ID, source_url=url) is None


@pytest.mark.parametrize("html", [
    HTML.replace('class="race_table_01"', 'class="Shutuba_Table"'),
    HTML.replace("着順", "入線順位"), HTML.replace("馬 番", "枠番"),
    HTML.replace("<td>3</td>", "<td>1</td>"),
])
def test_ambiguous_or_nonresult_columns_cannot_authorize_demotion(html):
    assert finish_column_evidence_from_html(html, race_id=RACE_ID, source_url=URL) is None


def test_normalized_payload_is_deep_lossless_and_repeatable():
    original = {"race_info": {"race_id": RACE_ID, "field_condition": "稍"},
                "horses": [{"horse_number": 3, "finish_position": "2(降)", "finish_time": "1:36.2",
                            "sire": "saved father", "nested": {"retained": True}}]}
    untouched = deepcopy(original)
    evidence = finish_column_evidence_from_html(HTML, race_id=RACE_ID, source_url=URL)
    actual = normalize_race_payload(original, finish_column_evidence=evidence)
    assert original == untouched
    assert actual["race_info"]["field_condition"] == "稍重"
    assert actual["race_info"]["field_condition_raw"] == "稍"
    horse = actual["horses"][0]
    assert horse["finish_position"] == 2 and horse["finish_position_raw"] == "2(降)"
    assert horse["finish_annotation"] == "降" and horse["sire"] == "saved father"
    assert normalize_race_payload(actual, finish_column_evidence=evidence) == actual
    horse["nested"]["retained"] = False
    assert original["horses"][0]["nested"]["retained"] is True


def test_payload_without_source_proof_preserves_annotated_result_and_issue():
    original = {"race_info": {"race_id": RACE_ID, "field_condition": "不"},
                "horses": [{"horse_number": 3, "finish_position": "2(降)"}]}
    actual = normalize_race_payload(original)
    assert actual["race_info"]["field_condition"] == "不良"
    assert actual["horses"][0]["finish_position"] == "2(降)"
    assert actual["normalization_issues"][0]["reason"] == "demotion_result_column_evidence_unverified"


def test_entry_card_missing_finish_has_no_quarantine_or_fabricated_value():
    actual = normalize_race_payload({"race_info": {"race_id": RACE_ID},
                                     "horses": [{"horse_number": 1, "finish_position": None}]})
    assert actual["horses"][0]["finish_position"] is None
    assert "normalization_issues" not in actual
