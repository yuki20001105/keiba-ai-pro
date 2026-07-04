"""
WIN5 エンドポイント
GET /api/win5/races?date=YYYYMMDD  — WIN5 対象 5 レースの情報を返す
"""
from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Query

from app_config import ULTIMATE_DB, logger  # type: ignore

router = APIRouter()

VENUE_NAME_MAP = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟",
    "05": "東京", "06": "中山", "07": "中京", "08": "京都",
    "09": "阪神", "10": "小倉",
}


def _race_info_from_db(race_id: str) -> dict | None:
    """DBからレース基本情報を取得する。"""
    try:
        conn = sqlite3.connect(str(ULTIMATE_DB))
        cur = conn.cursor()
        cur.execute("SELECT data FROM races_ultimate WHERE race_id = ?", (race_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        d = json.loads(row[0])
        venue_code = race_id[4:6] if len(race_id) >= 6 else ""
        venue_name = d.get("venue") or VENUE_NAME_MAP.get(venue_code, venue_code)
        horses = d.get("horses") or []
        return {
            "race_id": race_id,
            "race_name": d.get("race_name") or d.get("name") or "",
            "venue": venue_name,
            "race_no": int(race_id[10:12]) if len(race_id) >= 12 else 0,
            "distance": d.get("distance", 0),
            "track_type": d.get("track_type") or d.get("surface", ""),
            "post_time": d.get("post_time", ""),
            "num_horses": len(horses),
            "in_db": True,
        }
    except Exception as e:
        logger.warning(f"[WIN5] DB lookup error for {race_id}: {e}")
        return None


@router.get("/api/win5/races")
async def get_win5_races(date: str = Query(..., description="YYYYMMDD 形式の日付")):
    """
    指定日の WIN5 対象レース情報を返す。
    1. netkeiba WIN5 ページから 5 race_id をスクレイプ
    2. 各 race_id についてDBから基本情報を補完
    """
    from scraping.win5 import get_win5_race_ids  # type: ignore
    from scraping.jobs import _new_session  # type: ignore

    sess = _new_session()
    try:
        race_ids = await get_win5_race_ids(date, session=sess)
    finally:
        await sess.close()

    if not race_ids:
        return {
            "date": date,
            "race_ids": [],
            "races": [],
            "message": f"{date} の WIN5 対象レースが見つかりませんでした（WIN5 未実施日、または IP 制限の可能性があります）",
        }

    races = []
    for rid in race_ids:
        info = _race_info_from_db(rid)
        if info:
            races.append(info)
        else:
            # DB未登録の場合はIDのみ返す
            races.append({
                "race_id": rid,
                "race_name": "",
                "venue": "",
                "race_no": int(rid[10:12]) if len(rid) >= 12 else 0,
                "distance": 0,
                "track_type": "",
                "post_time": "",
                "num_horses": 0,
                "in_db": False,
            })

    return {
        "date": date,
        "race_ids": race_ids,
        "races": races,
        "count": len(races),
    }
