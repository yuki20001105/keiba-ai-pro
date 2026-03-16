"""
レース一覧エンドポイント
GET /api/races/by_date - 指定日のDB取得済みレース一覧
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
    "30": "門別", "35": "盛岡", "36": "水沢", "42": "浦和",
    "43": "船橋", "44": "大井", "45": "川崎", "46": "金沢",
    "47": "笠松", "48": "名古屋", "51": "園田", "53": "高知",
    "54": "佐賀",
}


@router.get("/api/races/by_date")
async def get_races_by_date(
    date: str = Query(..., description="日付 YYYYMMDD形式 (例: 20260316)"),
):
    """指定日のDB取得済みレース一覧を返す"""
    try:
        conn = sqlite3.connect(str(ULTIMATE_DB))
        cur = conn.cursor()
        # race_id は YYYY から始まるので年プレフィクスで絞り込む
        year_prefix = date[:4]
        cur.execute(
            "SELECT race_id, data FROM races_ultimate WHERE race_id LIKE ?",
            (f"{year_prefix}%",),
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        logger.error(f"/api/races/by_date DB error: {e}")
        return {"races": [], "count": 0, "date": date, "error": str(e)}

    result = []
    for race_id, data_str in rows:
        try:
            d = json.loads(data_str)
        except Exception:
            continue
        race_date = d.get("date", "")
        if race_date != date:
            continue
        venue_code = race_id[4:6] if len(race_id) >= 6 else ""
        venue_name = d.get("venue") or VENUE_NAME_MAP.get(venue_code, venue_code)
        result.append({
            "race_id": race_id,
            "race_name": d.get("race_name") or d.get("name") or "",
            "venue": venue_name,
            "venue_code": venue_code,
            "race_no": int(race_id[10:12]) if len(race_id) >= 12 else 0,
            "distance": d.get("distance", 0),
            "track_type": d.get("track_type") or d.get("surface", ""),
            "num_horses": d.get("num_horses", 0),
            "date": race_date,
        })

    result.sort(key=lambda x: x["race_id"])
    return {"races": result, "count": len(result), "date": date}
