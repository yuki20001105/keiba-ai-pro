from __future__ import annotations

import sys
from pathlib import Path

from bs4 import BeautifulSoup


PYTHON_API = Path(__file__).resolve().parents[1]
if str(PYTHON_API) not in sys.path:
    sys.path.insert(0, str(PYTHON_API))

from scraping.race import _find_result_table, parse_current_result_snapshot


def test_result_table_accepts_legacy_class() -> None:
    soup = BeautifulSoup('<table class="race_table_01"></table>', "lxml")
    assert _find_result_table(soup) is not None


def test_result_table_accepts_current_class() -> None:
    soup = BeautifulSoup(
        '<table class="RaceTable01 RaceCommon_Table ResultRefund Table_Show_All"></table>',
        "lxml",
    )
    assert _find_result_table(soup) is not None


def test_result_table_rejects_entry_table() -> None:
    soup = BeautifulSoup('<table class="Shutuba_Table"></table>', "lxml")
    assert _find_result_table(soup) is None


def test_current_result_snapshot_extracts_finishes_and_tansho_payout() -> None:
    html = """
    <table class="RaceTable01 RaceCommon_Table ResultRefund">
      <tr><th>result</th><th>frame</th><th>number</th></tr>
      <tr><td>1</td><td>2</td><td>3</td></tr>
      <tr><td>2</td><td>4</td><td>8</td></tr>
    </table>
    <table class="Payout_Detail_Table">
      <tr class="Tansho">
        <th>win</th>
        <td class="Result"><span>3</span><span></span></td>
        <td class="Payout"><span>1,290 yen</span></td>
      </tr>
    </table>
    """
    assert parse_current_result_snapshot(html, "202601020211") == {
        "race_id": "202601020211",
        "horses": [
            {"horse_number": 3, "finish_position": 1},
            {"horse_number": 8, "finish_position": 2},
        ],
        "payouts": [
            {"bet_type": "tansho", "combination": "3", "payout": 1290}
        ],
    }


def test_current_result_snapshot_fails_closed_on_partial_payout() -> None:
    html = """
    <table class="RaceTable01"><tr><td>1</td><td>1</td><td>3</td></tr></table>
    <table class="Payout_Detail_Table"><tr class="Tansho">
      <td class="Result"><span>3</span><span>8</span></td>
      <td class="Payout"><span>500 yen</span></td>
    </tr></table>
    """
    assert parse_current_result_snapshot(html, "202601020211") is None
