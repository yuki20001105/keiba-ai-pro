from __future__ import annotations

import asyncio

from scraping.mobile_race import parse_mobile_race
from scraping.quality import classify_race_quality


def test_mobile_result_parser_builds_complete_dirt_race() -> None:
    html = """
    <html><body>
      <div class="RaceHeader_Value">
        <h2>3歳未勝利</h2>
        <div class="RaceData">10:10発走 ダ1800m(右) 晴 良</div>
        <span>1回中山1日目</span>
      </div>
      <table class="table_slide_body ResultsByRaceDetail">
        <tr>
          <th>着順</th><th>枠 番</th><th>馬 番</th><th>馬名</th>
          <th>性齢</th><th>斤量</th><th>騎手</th><th>タイム</th>
          <th>着差</th><th>通過</th><th>上がり</th><th>単勝</th>
          <th>人気</th><th>馬体重</th><th>調教師</th><th>馬主</th><th>賞金（万円）</th>
        </tr>
        <tr>
          <td>1</td><td>1</td><td>1</td>
          <td><a href="https://db.sp.netkeiba.com/horse/2022000001/">テストホース</a></td>
          <td>牡3</td><td>57</td>
          <td><a href="https://db.sp.netkeiba.com/jockey/00001/">騎手</a></td>
          <td>1:55.0</td><td></td><td>1-1-1-1</td><td>38.0</td>
          <td>2.5</td><td>1</td><td>480(+2)</td>
          <td><a href="https://db.sp.netkeiba.com/trainer/00001/">調教師</a></td>
          <td>馬主</td><td>550.0</td>
        </tr>
      </table>
      <table><tr><th>単勝</th><td>1</td><td>250円</td><td>1人気</td></tr></table>
    </body></html>
    """

    async def fake_horse_detail(*_args, **_kwargs):
        return {"sire": "父", "dam": "母", "damsire": "母父"}

    payload = asyncio.run(
        parse_mobile_race(
            None,
            "202506010101",
            html,
            date_hint="20250105",
            quick_mode=True,
            horse_detail_fetcher=fake_horse_detail,
        )
    )

    assert payload is not None
    assert payload["race_info"]["distance"] == 1800
    assert payload["race_info"]["track_type"] == "ダート"
    assert payload["race_info"]["source_host"] == "db.sp.netkeiba.com"
    assert payload["horses"][0]["horse_id"] == "2022000001"
    assert payload["horses"][0]["odds"] == 2.5
    assert payload["horses"][0]["weight_kg"] == 480
    assert payload["return_tables"][0]["payout"] == 250

    quality = classify_race_quality(payload)
    assert quality.valid_for_date_completion is True
    assert quality.required_errors == ()
    assert all(state == "available" for state in quality.field_states.values())
