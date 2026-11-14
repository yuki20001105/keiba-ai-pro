from __future__ import annotations

import sys
from pathlib import Path

from bs4 import BeautifulSoup


PYTHON_API = Path(__file__).resolve().parents[1]
if str(PYTHON_API) not in sys.path:
    sys.path.insert(0, str(PYTHON_API))

from scraping.race import _find_result_table, _parse_return_tables, parse_current_result_snapshot


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


def test_modern_payouts_preserve_ticket_groups_and_popularity() -> None:
    soup = BeautifulSoup("""
    <table class="Payout_Detail_Table">
      <tr><th>馬連</th><td class="Result"><ul>
        <li><span>5</span></li><li><span>13</span></li><li></li>
      </ul></td><td class="Payout"><span>1,510円</span></td>
      <td class="Ninki"><span>5人気</span></td></tr>
      <tr><th>ワイド</th><td class="Result">
        <ul><li><span>5</span></li><li><span>13</span></li><li></li></ul>
        <ul><li><span>4</span></li><li><span>5</span></li><li></li></ul>
      </td><td class="Payout"><span>590円<br>2,320円</span></td>
      <td class="Ninki"><span>5人気</span><span>24人気</span></td></tr>
      <tr><th>複勝</th><td class="Result">
        <div><span>5</span></div><div><span></span></div>
        <div><span>13</span></div><div><span></span></div>
      </td><td class="Payout"><span>110円<br>330円</span></td>
      <td class="Ninki"><span>1人気</span><span>5人気</span></td></tr>
    </table>""", "lxml")
    assert _parse_return_tables(soup, "202608041001") == [
        {"bet_type": "馬連", "combinations": "5-13", "payout": 1510, "popularity": 5},
        {"bet_type": "ワイド", "combinations": "5-13", "payout": 590, "popularity": 5},
        {"bet_type": "ワイド", "combinations": "4-5", "payout": 2320, "popularity": 24},
        {"bet_type": "複勝", "combinations": "5", "payout": 110, "popularity": 1},
        {"bet_type": "複勝", "combinations": "13", "payout": 330, "popularity": 5},
    ]


def test_modern_payout_misalignment_does_not_invent_zero_payouts() -> None:
    soup = BeautifulSoup("""
    <table class="Payout_Detail_Table"><tr><th>ワイド</th>
    <td class="Result"><ul><li>5</li><li>13</li></ul>
    <ul><li>4</li><li>5</li></ul></td>
    <td class="Payout"><span>590円</span></td></tr></table>
    """, "lxml")
    assert _parse_return_tables(soup, "202608041001") == []
