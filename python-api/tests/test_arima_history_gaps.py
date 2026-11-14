import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def parser():
    script = Path(__file__).resolve().parents[2] / "scripts" / "audit_arima_history_gaps.py"
    spec = importlib.util.spec_from_file_location("arima_history_fixture", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.parse_profile


def page(count=2):
    return f"""<html><link rel="canonical" href="https://db.netkeiba.com/horse/2022104614/">
    <table class="db_prof_table"><tr><th>通算成績</th><td>{count}戦1勝</td></tr></table>
    <table><tr><th>日付</th><th>開催</th><th>距離</th></tr>
    <tr><td>2025/10/26</td><td>京都</td><td>芝3000</td></tr>
    <tr><td>2025/12/28</td><td>中山</td><td>芝2500</td></tr></table></html>"""


def test_target_race_is_not_used_to_invent_prior_course_experience(parser):
    proof = parser(page(), "2022104614", "20251228", ["safe_horse_course_win_rate"])
    assert proof["coverage_matches_published_career_count"] is True
    assert proof["prior_rows"] == 1
    assert proof["prior_same_condition_rows"] == []


def test_partial_career_table_is_not_claimed_complete(parser):
    proof = parser(page(count=10), "2022104614", "20251228", ["safe_horse_course_win_rate"])
    assert proof["coverage_matches_published_career_count"] is False


def test_source_from_another_horse_is_rejected(parser):
    with pytest.raises(ValueError, match="child-identity-unconfirmed"):
        parser(page(), "2022104615", "20251228", ["safe_horse_course_win_rate"])
