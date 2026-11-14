"""Recovery trust-boundary tests use small synthetic result pages, never HTTP."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _tool():
    path = ROOT / "scripts" / "recover_pace_laps.py"
    assert path.exists(), "Offline recovery tool has not been implemented"
    spec = importlib.util.spec_from_file_location("recover_pace_laps", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ID = "202601010101"
URL = f"https://db.netkeiba.com/race/{ID}/"
EXPECTED = {"race_id": ID, "date": "20260101", "distance": 600}
HTML = f"""<html><head><meta charset="utf-8">
<title>Test race | 2026年1月1日</title></head><body>
<div class="mainrace_data">芝右600m</div>
<p class="smalltxt">2026年1月1日</p>
<span id="Race_Result_Horse_Laptime"></span>
<script>
get_race_result_horse_laptime('{ID}', 'Race_Result_Horse_Laptime', 1);
</script>
<table class="race_table_01"><tr><th>着順</th><th>タイム</th></tr>
<tr><td>1</td><td>0:35.0</td></tr><tr><td>2</td><td>0:35.2</td></tr></table>
<table summary="ラップタイム"><tr><th>ラップ</th><td>12.0 - 11.0 - 12.0</td></tr>
<tr><th>ペース</th><td>12.0 - 23.0 - 35.0 (35.0-35.0)</td></tr></table>
</body></html>"""


def _recover(html=HTML, expected=None):
    return _tool().recover_observation(html.encode(), source_url=URL, final_url=URL,
                                       http_status=200, expected=expected or EXPECTED)


def test_recovery_binds_exact600_label_to_original_bytes_and_result_page_identity():
    actual, reason = _recover()
    assert reason is None
    assert actual["first600_seconds"] == 35.0
    assert actual["source_sha256"] == hashlib.sha256(HTML.encode()).hexdigest()
    assert actual["validation"]["page_identity_method"] == "inline_result_laptime_render_call"
    assert actual["validation"]["winner_finish_match"] is True
    assert actual["validated"] is True
    assert actual["lap_evidence"]["sectional_seconds"] == {200: 12.0, 400: 11.0, 600: 12.0}


@pytest.mark.parametrize("html,reason", [
    (HTML.replace(f"get_race_result_horse_laptime('{ID}', 'Race_Result_Horse_Laptime', 1);",
                  f"<a href='{URL}'>This race</a>"), "page_identity_missing"),
    (HTML.replace(f"get_race_result_horse_laptime('{ID}',", "get_race_result_horse_laptime('202601010102',"),
     "page_identity_mismatch"),
    (HTML.replace("<span id=\"Race_Result_Horse_Laptime\"></span>", ""), "page_identity_missing"),
    (HTML.replace("get_race_result_horse_laptime(", "// get_race_result_horse_laptime("),
     "page_identity_missing"),
    (HTML.replace("get_race_result_horse_laptime(", "/* get_race_result_horse_laptime(")
     .replace(", 1);", ", 1); */"), "page_identity_missing"),
    (HTML.replace("2026年1月1日", "2026年1月2日"), "displayed_date_mismatch"),
    (HTML.replace("芝右600m", "芝右800m"), "displayed_distance_mismatch"),
    (HTML.replace("0:35.0", "0:39.0"), "winner_finish_mismatch"),
    (HTML.replace("<td>1</td><td>0:35.0", "<td>3</td><td>0:35.0"), "displayed_winner_missing"),
    (HTML.replace("<tr><th>ラップ</th><td>12.0 - 11.0 - 12.0</td></tr>", ""),
     "sectional_row_not_observed"),
])
def test_recovery_rejects_navigation_mismatch_missing_proof_and_unobserved_sectionals(html, reason):
    actual, rejection = _recover(html)
    assert actual is None
    assert rejection == reason


def test_conflicting_canonical_is_rejected_even_with_matching_result_render_call():
    html = HTML.replace("<meta charset=\"utf-8\">",
                        '<meta charset="utf-8"><link rel="canonical" href="https://db.netkeiba.com/race/202601010102/">')
    actual, reason = _recover(html)
    assert actual is None
    assert reason == "page_identity_mismatch"


def test_canonical_identity_does_not_require_inline_result_call():
    html = HTML.replace("<meta charset=\"utf-8\">", f'<meta charset="utf-8"><link rel="canonical" href="{URL}">')
    html = html.replace(f"get_race_result_horse_laptime('{ID}', 'Race_Result_Horse_Laptime', 1);", "")
    actual, reason = _recover(html)
    assert reason is None
    assert actual["validation"]["page_identity_method"] == "publisher_canonical"


def test_odd_initial_segment_is_verified_but_not_interpolated_into_exact600_label():
    html = HTML.replace("芝右600m", "芝右700m").replace("0:35.0", "0:42.0").replace("0:35.2", "0:42.2")
    html = html.replace("12.0 - 11.0 - 12.0", "7.0 - 12.0 - 11.0 - 12.0")
    html = html.replace("12.0 - 23.0 - 35.0", "7.0 - 19.0 - 30.0 - 42.0")
    actual, reason = _recover(html, {**EXPECTED, "distance": 700})
    assert reason is None
    assert actual["first600_seconds"] is None
    assert actual["validated"] is False
    assert actual["validation"]["exact600_boundary"] is False
    assert actual["validation_reasons"] == ["no_exact_600m_boundary"]


def test_source_and_final_urls_and_status_are_verified_not_asserted():
    tool = _tool()
    for kwargs, reason in [
        ({"final_url": "https://db.netkeiba.com/race/202601010102/"}, "transport_identity_mismatch"),
        ({"http_status": 403}, "http_not_success"),
        ({"source_url": URL + "?secret=hidden"}, "transport_identity_invalid"),
    ]:
        actual, rejection = tool.recover_observation(
            HTML.encode(), expected=EXPECTED,
            **({"source_url": URL, "final_url": URL, "http_status": 200} | kwargs))
        assert actual is None
        assert rejection == reason


def test_aggregate_distinguishes_verified_races_exact600_labels_and_year_splits():
    tool = _tool()
    good, _ = _recover()
    odd = {**good, "race_id": "202401010101", "race_date": "20240101", "first600_seconds": None}
    report = tool.aggregate_recovery([good, odd], {"page_identity_missing": 1}, candidate_count=3)
    assert report["verified_races"] == 2
    assert report["exact600_labels"] == 1
    assert report["coverage_by_split"]["TRAIN"]["exact600_labels"] == 0
    assert report["coverage_by_split"]["TEST"]["exact600_labels"] == 1
    assert report["coverage_by_year"]["2026"]["dates"] == 1
    assert report["excluded_reasons"] == {"page_identity_missing": 1}


@pytest.mark.parametrize("replacement", [
    "<script type=\"application/json\">\n{call}\n</script>",
    "<script>\nconst example = `\n{call}\n`;\n</script>",
])
def test_nonexecuting_script_data_is_not_page_identity(replacement):
    call = f"get_race_result_horse_laptime('{ID}', 'Race_Result_Horse_Laptime', 1);"
    html = HTML.replace(f"<script>\n{call}\n</script>", replacement.format(call=call))
    actual, reason = _recover(html)
    assert actual is None
    assert reason == "page_identity_missing"


@pytest.fixture
def isolated_cache_files():
    directory = ROOT / "reports" / "generated" / "pace-stack" / ("test-fixtures-" + uuid.uuid4().hex)
    directory.mkdir(parents=True)
    cache_path, race_path = directory / "cache.db", directory / "races.db"
    with closing(sqlite3.connect(cache_path)) as cache, cache:
        cache.execute("CREATE TABLE http_cache(normalized_url TEXT PRIMARY KEY,final_url TEXT,status INTEGER,body BLOB)")
        cache.execute("INSERT INTO http_cache VALUES(?,?,?,?)", (URL, URL, 200, HTML.encode()))
    with closing(sqlite3.connect(race_path)) as races, races:
        races.execute("CREATE TABLE races_ultimate(race_id TEXT PRIMARY KEY,data TEXT)")
        races.execute("INSERT INTO races_ultimate VALUES(?,?)", (ID, json.dumps(EXPECTED)))
    try:
        yield directory, cache_path, race_path
    finally:
        shutil.rmtree(directory)


def test_bounded_recovery_persists_only_hashed_observations_without_changing_input_databases(isolated_cache_files):
    directory, cache_path, race_path = isolated_cache_files
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (cache_path, race_path)]
    report = _tool().recover_cache(cache_path, race_path, directory / "output", max_candidates=1)
    assert report["candidate_count"] == 1
    assert report["verified_races"] == 1
    assert report["exact600_labels"] == 1
    observations_file = directory / "output" / "recovered-laps.json"
    payload = json.loads(observations_file.read_text(encoding="utf-8"))
    assert payload["schema"] == "verified-pace-lap-recovery-v1"
    assert payload["observations"][0]["first600_seconds"] == 35.0
    assert payload["observations"][0]["validated"] is True
    assert report["observations_file_sha256"] == hashlib.sha256(observations_file.read_bytes()).hexdigest()
    assert before == [hashlib.sha256(path.read_bytes()).hexdigest() for path in (cache_path, race_path)]
    assert sorted(path.name for path in (directory / "output").iterdir()) == [
        "recovered-laps.json", "recovery-report.json"]


def test_candidate_bound_stops_before_any_observation_output(isolated_cache_files):
    directory, cache_path, race_path = isolated_cache_files
    other = "https://db.netkeiba.com/race/202601010102/"
    with closing(sqlite3.connect(cache_path)) as cache, cache:
        cache.execute("INSERT INTO http_cache VALUES(?,?,?,?)", (other, other, 200, HTML.encode()))
    with pytest.raises(ValueError, match="candidate_bound_exceeded"):
        _tool().recover_cache(cache_path, race_path, directory / "output", max_candidates=1)
    assert not (directory / "output").exists()


def test_training_export_omits_verified_laps_without_exact600_and_incomplete_proof():
    tool = _tool()
    good, _ = _recover()
    odd = {**good, "first600_seconds": None, "validated": False}
    incomplete = {**good, "validation": {**good["validation"], "page_identity_verified": False}}
    exported = tool.training_observations([good, odd, incomplete])
    assert len(exported) == 1
    assert exported[0]["validated"] is True
    assert exported[0]["first600_seconds"] == 35.0
