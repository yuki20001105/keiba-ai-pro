"""
レース一覧エンドポイント
GET /api/races/by_date           - 指定日のDB取得済みレース一覧
GET /api/races/recent            - 最近取得したレース一覧（直近N件）
GET /api/races/{race_id}/horses  - 特定レースの出走馬一覧（ML推論なし）
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


def _parse_race_row(race_id: str, data_str: str) -> dict:
    d = json.loads(data_str)
    venue_code = race_id[4:6] if len(race_id) >= 6 else ""
    venue_name = d.get("venue") or VENUE_NAME_MAP.get(venue_code, venue_code)
    return {
        "race_id": race_id,
        "race_name": d.get("race_name") or d.get("name") or "",
        "venue": venue_name,
        "venue_code": venue_code,
        "race_no": int(race_id[10:12]) if len(race_id) >= 12 else 0,
        "distance": d.get("distance", 0),
        "track_type": d.get("track_type") or d.get("surface", ""),
        "weather": d.get("weather", ""),
        "field_condition": d.get("field_condition", ""),
        "num_horses": d.get("num_horses", 0),
        "date": d.get("date", ""),
    }


@router.get("/api/races/recent")
async def get_races_recent(limit: int = Query(50, ge=1, le=500)):
    """最近取得したレース一覧を返す（limit件、race_id降順）"""
    try:
        conn = sqlite3.connect(str(ULTIMATE_DB))
        cur = conn.cursor()
        cur.execute(
            "SELECT race_id, data FROM races_ultimate ORDER BY race_id DESC LIMIT ?",
            (limit,),
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        logger.error(f"/api/races/recent DB error: {e}")
        return {"races": [], "count": 0, "error": str(e)}

    result = []
    for race_id, data_str in rows:
        try:
            result.append(_parse_race_row(race_id, data_str))
        except Exception:
            continue

    return {"races": result, "count": len(result)}


@router.get("/api/races/{race_id}/horses")
async def get_race_horses(race_id: str):
    """指定レースの出走馬一覧を返す（ML推論なし・軽量）"""
    try:
        conn = sqlite3.connect(str(ULTIMATE_DB))
        cur = conn.cursor()

        cur.execute("SELECT data FROM races_ultimate WHERE race_id = ?", (race_id,))
        rrow = cur.fetchone()
        if not rrow:
            conn.close()
            return {"race_id": race_id, "horses": [], "error": "レースが見つかりません"}
        race_info = json.loads(rrow[0])

        cur.execute(
            "SELECT data FROM race_results_ultimate WHERE race_id = ?"
            " ORDER BY json_extract(data, '$.horse_number')",
            (race_id,),
        )
        hrows = cur.fetchall()
        conn.close()
    except Exception as e:
        logger.error(f"/api/races/{race_id}/horses DB error: {e}")
        return {"race_id": race_id, "horses": [], "error": str(e)}

    horses = []
    for hr in hrows:
        try:
            hd = json.loads(hr[0])
            horses.append({
                "finish_position": hd.get("finish_position"),
                "bracket_number": hd.get("bracket_number"),
                "horse_number": hd.get("horse_number"),
                "horse_name": hd.get("horse_name", ""),
                "sex_age": hd.get("sex_age", ""),
                "sex": hd.get("sex", ""),
                "age": hd.get("age"),
                "jockey_weight": hd.get("jockey_weight"),
                "jockey_name": hd.get("jockey_name", ""),
                "finish_time": hd.get("finish_time"),
                "odds": hd.get("odds"),
                "popularity": hd.get("popularity"),
                "weight_kg": hd.get("weight_kg"),
                "weight_diff": hd.get("weight_diff"),
                "trainer_name": hd.get("trainer_name", ""),
            })
        except Exception:
            continue

    return {
        "race_id": race_id,
        "race_name": race_info.get("race_name", ""),
        "venue": race_info.get("venue", ""),
        "date": race_info.get("date", ""),
        "distance": race_info.get("distance", 0),
        "track_type": race_info.get("track_type", ""),
        "horses": horses,
    }


@router.get("/api/races/by_date")
async def get_races_by_date(
    date: str = Query(..., description="日付 YYYYMMDD形式 (例: 20260316)"),
):
    """指定日のDB取得済みレース一覧を返す"""
    try:
        conn = sqlite3.connect(str(ULTIMATE_DB))
        cur = conn.cursor()
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
            r = _parse_race_row(race_id, data_str)
        except Exception:
            continue
        if r["date"] != date:
            continue
        result.append(r)

    result.sort(key=lambda x: x["race_id"])
    return {"races": result, "count": len(result), "date": date}
