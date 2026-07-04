"""
netkeiba タイム指数タブ (race/speed.html) スクレイパー

対象URL: https://race.netkeiba.com/race/speed.html?race_id={race_id}
要件: プレミアム会員ログイン済みセッション

取得データ（SpeedIndex_Table より）:
  - horse_number, horse_name
  - max_index       : 最高（過去1年の最高タイム指数）
  - avg_5_index     : ５走平均
  - dist_max_index  : 距離最高
  - course_max_index: コース最高
  - index_3ago      : 3走前
  - index_2ago      : 2走前
  - index_last      : 前走
"""
from __future__ import annotations

import asyncio
import re
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

try:
    from app_config import logger  # type: ignore
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

_SPEED_URL = "https://race.netkeiba.com/race/speed.html?race_id={race_id}"


def _parse_index_value(td) -> Optional[int]:
    """
    SpeedIndex_Table の td から整数値を抽出する。

    HTML: <td class="cellcolor_ sk__max_index">
              <span class="Sort_Function_Data_Hidden">1108</span>108
          </td>
    → 108 を返す。

    "未" / "-" / "**" → None
    末尾の "*"（コース最高マーカー）は除去。
    """
    if td is None:
        return None
    hidden = td.find(class_="Sort_Function_Data_Hidden")
    if hidden:
        hidden.decompose()
    val = td.get_text(strip=True).rstrip("*")
    if not val or val in ("-", "未", "**", "***"):
        return None
    try:
        return int(val)
    except ValueError:
        return None


async def scrape_speed_figure(
    session: aiohttp.ClientSession,
    race_id: str,
    is_logged_in: bool = True,
) -> list[dict]:
    """
    タイム指数タブをスクレイプして馬ごとの指数リストを返す。

    Returns:
        [
          {
            "race_id": str,
            "horse_number": int,
            "horse_name": str,
            "max_index": int | None,
            "avg_5_index": int | None,
            "dist_max_index": int | None,
            "course_max_index": int | None,
            "index_3ago": int | None,
            "index_2ago": int | None,
            "index_last": int | None,
          },
          ...
        ]
    """
    if not is_logged_in:
        logger.debug(f"[speed] ログイン未済のためスキップ: {race_id}")
        return []

    url = _SPEED_URL.format(race_id=race_id)
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                logger.debug(f"[speed] HTTP {resp.status}: {race_id}")
                return []
            raw = await resp.read()
            html = raw.decode("euc-jp", errors="replace")
    except Exception as e:
        logger.warning(f"[speed] 取得失敗 {race_id}: {e}")
        return []

    if len(html) < 5000:
        logger.debug(f"[speed] {race_id}: HTML短すぎ({len(html)}bytes) → スキップ")
        return []

    return _parse_speed_html(html, race_id)


def _parse_speed_html(html: str, race_id: str) -> list[dict]:
    """speed.html の SpeedIndex_Table を解析して指数リストを返す。"""
    soup = BeautifulSoup(html, "lxml")
    tbl = soup.find("table", class_="SpeedIndex_Table")
    if not tbl:
        logger.debug(f"[speed] SpeedIndex_Table なし: {race_id}")
        return []

    records: list[dict] = []
    rows = tbl.find_all("tr")

    for row in rows:
        cls = row.get("class", [])
        if "HorseList" not in cls or "List" not in cls:
            continue

        tds = row.find_all("td")
        if len(tds) < 13:
            continue

        # 馬番 (td.UmaBan)
        umaban_td = row.find("td", class_="UmaBan")
        horse_number: Optional[int] = None
        if umaban_td:
            try:
                horse_number = int(umaban_td.get_text(strip=True))
            except ValueError:
                pass

        # 馬名 (td.Horse_Name)
        horse_name = ""
        horse_name_td = row.find("td", class_="Horse_Name")
        if horse_name_td:
            a = horse_name_td.find("a")
            if a:
                horse_name = a.get_text(strip=True)
            else:
                # Sort_Function_Data_Hidden を除いてテキストを取得
                hidden_span = horse_name_td.find(class_="Sort_Function_Data_Hidden")
                if hidden_span:
                    hidden_span.decompose()
                horse_name = horse_name_td.get_text(strip=True)

        # 各指数列（class名で特定）
        def _get(cls_name: str) -> Optional[int]:
            td = row.find("td", class_=cls_name)
            # find は class_ を部分一致で探す場合がある。明示的にチェック。
            if td and cls_name in td.get("class", []):
                # 値取得のため td のコピーを使う（horse_name の decompose と分離）
                return _parse_index_value(BeautifulSoup(str(td), "lxml").find("td"))
            return None

        records.append(
            {
                "race_id": race_id,
                "horse_number": horse_number,
                "horse_name": horse_name,
                "max_index": _get("sk__max_index"),
                "avg_5_index": _get("sk__average_index"),
                "dist_max_index": _get("sk__max_distance_index"),
                "course_max_index": _get("sk__max_course_index"),
                "index_3ago": _get("sk__index3"),
                "index_2ago": _get("sk__index2"),
                "index_last": _get("sk__index1"),
            }
        )

    logger.debug(f"[speed] {race_id}: {len(records)}頭の指数を取得")
    return records
