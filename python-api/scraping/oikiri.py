"""
調教タイムスクレイピング: netkeiba.com の oikiri ページから調教データを取得する。

対象URL: https://race.netkeiba.com/race/oikiri.html?race_id={race_id}

前提: プレミアム会員としてログイン済みのセッション（scraping.constants.login_netkeiba）が必要。
ログイン未済の場合は空リストを返す（エラーにはしない）。

返り値の各要素（1調教レコード）:
  {
      "race_id": str,
      "horse_number": int | None,
      "horse_name": str,
      "training_date": str,        # "2026/04/29" 形式
      "course": str,               # "CW" / "坂路" / "栗坂" など
      "track_condition": str,      # "良" / "稍重" など
      "rider": str,                # 騎乗者
      "time_6f": float | None,     # 6ハロンタイム
      "time_5f": float | None,
      "time_4f": float | None,
      "time_3f": float | None,
      "time_1f": float | None,
      "lap_6f_5f": float | None,   # ラップ区間タイム (6F→5F)
      "lap_5f_4f": float | None,
      "lap_4f_3f": float | None,
      "lap_3f_1f": float | None,
      "lap_1f_g": float | None,    # 1F→ゴール
      "position": str,             # "馬也" / "追切" など
      "pace": str,                 # 脚色
      "grade": str,                # "A" / "B" / "C" / "D"
      "comment": str,              # 専門紙コメント
      "is_last_training": bool,    # 最終追い切りフラグ
  }
"""
from __future__ import annotations

import asyncio
import re
from typing import Optional

from bs4 import BeautifulSoup

from scraping.constants import is_cloudflare_block, SCRAPE_PROXY_URL

try:
    from app_config import logger  # type: ignore
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


async def scrape_oikiri(
    session, race_id: str, is_logged_in: bool = False
) -> list[dict]:
    """
    調教タイムページをスクレイピングして全馬の調教データを返す。

    Args:
        session: aiohttp.ClientSession（login_netkeiba でログイン済みのもの）
        race_id: 12桁レースID
        is_logged_in: ログイン済みか否か（Falseの場合は即返却）

    Returns:
        調教レコードのリスト。取得失敗・ログイン未済の場合は空リスト。
    """
    if not is_logged_in:
        logger.debug(f"[oikiri] ログイン未済のためスキップ: {race_id}")
        return []

    url = f"https://race.netkeiba.com/race/oikiri.html?race_id={race_id}"
    html: str | None = None

    for _attempt in range(3):
        try:
            if _attempt > 0:
                await asyncio.sleep(2.0 ** _attempt)
            _kwargs: dict = {}
            if SCRAPE_PROXY_URL:
                _kwargs["proxy"] = SCRAPE_PROXY_URL
            async with session.get(
                url,
                headers={"Referer": "https://race.netkeiba.com/"},
                **_kwargs,
            ) as resp:
                if resp.status == 429:
                    await asyncio.sleep(10.0 + _attempt * 5.0)
                    continue
                if resp.status != 200:
                    logger.warning(f"[oikiri] HTTP {resp.status}: {race_id}")
                    return []
                content = await resp.read()
                if is_cloudflare_block(content):
                    logger.warning(f"[oikiri] Cloudflare ブロック: {race_id}")
                    return []
                html = content.decode("euc-jp", errors="replace")
                break
        except asyncio.TimeoutError:
            logger.warning(f"[oikiri] タイムアウト {race_id} 試行{_attempt+1}/3")
        except Exception as e:
            logger.warning(f"[oikiri] 取得エラー {race_id} 試行{_attempt+1}/3: {e}")

    if not html:
        return []

    # プレミアム会員ページへのリダイレクト判定（ログイン失敗の場合）
    if "前日" in html and "公開予定" in html and "プレミアム" in html and len(html) < 40000:
        # データが未公開 or ログイン失敗
        if "ログイン" in html and "スーパープレミアム" not in html:
            logger.warning(f"[oikiri] プレミアム会員認証が必要です: {race_id}")
        else:
            logger.debug(f"[oikiri] レース前日のため調教データ未公開: {race_id}")
        return []

    results = _parse_oikiri_html(html, race_id)
    if results:
        logger.info(f"[oikiri] {race_id}: {len(results)}件の調教レコード取得")
    else:
        logger.debug(f"[oikiri] {race_id}: 調教データなし（HTMLパース結果0件）")
    return results


def _parse_oikiri_html(html: str, race_id: str) -> list[dict]:
    """
    oikiri ページHTMLをパースして調教レコードリストを返す。

    実際の構造（table.OikiriTable 内の2行ペア）:
        Row1 (id="tr_X"): td.Waku, td.Umaban, td.Horse_Info, td.TrainingReview_Cell (comment)
        Row2 (no id):     td.Training_Day, td(course), td(track), td(rider),
                          td.TrainingTimeData, td(position), td.TrainingLoad,
                          td.Training_Critic, td.Rank_X, td(video)
    """
    soup = BeautifulSoup(html, "lxml")
    records: list[dict] = []

    # OikiriTable を探す
    table = soup.find("table", class_="OikiriTable")
    if not table:
        logger.debug(f"[oikiri] OikiriTable が見つかりません: {race_id}")
        return []

    all_rows = table.find_all("tr")

    i = 0
    while i < len(all_rows):
        row = all_rows[i]
        row_id = row.get("id", "")
        row_classes = row.get("class", [])

        # "tr_X" id を持つ行が馬ヘッダー行
        if row_id.startswith("tr_") and "HorseList" in row_classes:
            horse_info = _parse_horse_header_row(row)

            # 次の行がデータ行（id なし、HorseList を持つ）
            data_row = None
            if i + 1 < len(all_rows):
                next_row = all_rows[i + 1]
                next_id = next_row.get("id", "")
                next_classes = next_row.get("class", [])
                if not next_id and "HorseList" in next_classes:
                    data_row = next_row
                    i += 1  # データ行をスキップ

            if data_row is not None:
                rec = _parse_data_row(data_row)
                if rec:
                    full_rec = {
                        "race_id": race_id,
                        "horse_number": horse_info.get("horse_number"),
                        "horse_name": horse_info.get("horse_name", ""),
                        "comment": horse_info.get("comment", ""),
                        "is_last_training": True,  # oikiri ページは最終追い切り
                        **rec,
                    }
                    records.append(full_rec)
        i += 1

    return records


def _parse_horse_header_row(row) -> dict:
    """馬ヘッダー行（id="tr_X"）から馬番・馬名・コメントを抽出。"""
    result: dict = {}

    # 馬番
    umaban_td = row.find("td", class_="Umaban")
    if umaban_td:
        txt = umaban_td.get_text(strip=True)
        try:
            result["horse_number"] = int(txt)
        except ValueError:
            pass

    # 馬名（Horse_Info > Horse_Name > a）
    horse_info_td = row.find("td", class_="Horse_Info")
    if horse_info_td:
        horse_name_div = horse_info_td.find(class_="Horse_Name")
        if horse_name_div:
            a = horse_name_div.find("a")
            result["horse_name"] = a.get_text(strip=True) if a else horse_name_div.get_text(strip=True)
        else:
            a = horse_info_td.find("a")
            if a:
                result["horse_name"] = a.get_text(strip=True)

    # コメント（TrainingReview_Cell）
    comment_td = row.find("td", class_="TrainingReview_Cell")
    if comment_td:
        result["comment"] = comment_td.get_text(" ", strip=True)

    return result


def _parse_data_row(row) -> Optional[dict]:
    """データ行から調教タイム・コース等を抽出。"""
    try:
        # 日付
        date_td = row.find("td", class_="Training_Day")
        if not date_td:
            return None
        date_str = re.sub(r"（.+?）|\(.+?\)", "", date_td.get_text(strip=True)).strip()
        if not re.search(r"\d{4}/\d{2}/\d{2}", date_str):
            return None

        tds = row.find_all("td")

        # 日付以降のセルを順番に取得
        date_idx = next((i for i, td in enumerate(tds) if "Training_Day" in td.get("class", [])), 0)

        def _td_text(offset: int) -> str:
            idx = date_idx + offset
            return tds[idx].get_text(" ", strip=True) if idx < len(tds) else ""

        # date_idx+0 = 日付
        # date_idx+1 = コース
        # date_idx+2 = 馬場
        # date_idx+3 = 乗り手
        course = _td_text(1)
        track_condition = _td_text(2)
        rider = _td_text(3)

        # タイムデータ（TrainingTimeData > ul.TrainingTimeDataList > li）
        time_td = row.find("td", class_="TrainingTimeData")
        times_cum = [None, None, None, None, None]   # [6F, 5F, 4F, 3F, 1F]
        laps = [None, None, None, None, None]         # [6f_5f, 5f_4f, 4f_3f, 3f_1f, 1f_g]

        if time_td:
            lis = time_td.select("ul.TrainingTimeDataList li")
            for j, li in enumerate(lis[:5]):
                # BeautifulSoup: NavigableString も .name=None を持つので isinstance で区別
                from bs4 import NavigableString as _NavStr
                for content in li.children:
                    if isinstance(content, _NavStr):
                        # テキストノード = 累積タイム
                        cum_txt = str(content).strip()
                        if re.match(r"[\d.]+$", cum_txt):
                            try:
                                times_cum[j] = float(cum_txt)
                            except ValueError:
                                pass
                    elif content.name == "span":
                        # RapTime: "(14.3)" 形式のラップ
                        lp = re.search(r"\(([\d.]+)\)", content.get_text())
                        if lp:
                            laps[j] = float(lp.group(1))

        # 位置
        pos_idx = date_idx + 5
        position = tds[pos_idx].get_text(strip=True) if pos_idx < len(tds) else ""

        # 脚色（TrainingLoad）
        load_td = row.find("td", class_="TrainingLoad")
        pace = load_td.get_text(strip=True) if load_td else ""

        # 評価テキスト（Training_Critic）
        critic_td = row.find("td", class_="Training_Critic")
        grade_text = critic_td.get_text(strip=True) if critic_td else ""

        # 評価グレード（Rank_A / Rank_B / Rank_C / Rank_D）
        grade = ""
        for rank_cls in ["Rank_A", "Rank_B", "Rank_C", "Rank_D"]:
            rank_td = row.find("td", class_=rank_cls)
            if rank_td:
                grade = rank_td.get_text(strip=True)
                break

        return {
            "training_date": date_str,
            "course": course,
            "track_condition": track_condition,
            "rider": rider,
            "time_6f": times_cum[0],
            "time_5f": times_cum[1],
            "time_4f": times_cum[2],
            "time_3f": times_cum[3],
            "time_1f": times_cum[4],
            "lap_6f_5f": laps[0],
            "lap_5f_4f": laps[1],
            "lap_4f_3f": laps[2],
            "lap_3f_1f": laps[3],
            "lap_1f_g": laps[4],
            "position": position,
            "pace": pace,
            "grade": grade,
        }

    except Exception as e:
        logger.debug(f"[oikiri] データ行パースエラー: {e}")
        return None
