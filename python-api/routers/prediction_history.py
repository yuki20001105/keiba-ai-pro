"""
予測履歴・精度確認エンドポイント
GET /api/prediction-history  — 過去の予測と実際の着順を返す
GET /api/prediction-history/stats — 的中率サマリー
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app_config import ULTIMATE_DB  # type: ignore
from deps.auth import require_premium  # type: ignore

router = APIRouter()


def _query_history(db_path: str, limit: int, race_date: Optional[str] = None) -> list[dict]:
    """prediction_log LEFT JOIN results で予測+実績を取得"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # prediction_log テーブルが存在しない場合は空を返す
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='prediction_log'"
        ).fetchone()
        if not exists:
            return []

        params: list = []
        date_filter = ""
        if race_date:
            date_filter = "AND pl.race_date = ?"
            params.append(race_date)

        rows = conn.execute(
            f"""
            SELECT
                pl.race_id,
                pl.race_name,
                pl.venue,
                pl.race_date,
                pl.horse_id,
                pl.horse_name,
                pl.horse_number,
                pl.predicted_rank,
                pl.win_probability,
                pl.p_raw,
                pl.odds,
                pl.popularity,
                pl.model_id,
                pl.predicted_at,
                r.finish  AS actual_finish,
                r.time    AS finish_time,
                r.odds    AS actual_odds
            FROM prediction_log pl
            LEFT JOIN results r
                   ON pl.race_id = r.race_id AND pl.horse_id = r.horse_id
            WHERE 1=1 {date_filter}
            ORDER BY pl.race_date DESC, pl.race_id DESC, pl.predicted_rank ASC
            LIMIT ?
            """,
            params + [limit],
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _group_by_race(rows: list[dict]) -> list[dict]:
    """行をレース単位にグループ化して返す"""
    races: dict[str, dict] = {}
    for row in rows:
        rid = row["race_id"]
        if rid not in races:
            races[rid] = {
                "race_id":     rid,
                "race_name":   row["race_name"],
                "venue":       row["venue"],
                "race_date":   row["race_date"],
                "model_id":    row["model_id"],
                "predicted_at": row["predicted_at"],
                "predictions": [],
            }
        races[rid]["predictions"].append({
            "horse_id":        row["horse_id"],
            "horse_name":      row["horse_name"],
            "horse_number":    row["horse_number"],
            "predicted_rank":  row["predicted_rank"],
            "win_probability": row["win_probability"],
            "p_raw":           row["p_raw"],
            "odds":            row["odds"],
            "actual_finish":   row["actual_finish"],
            "finish_time":     row["finish_time"],
            "actual_odds":     row["actual_odds"],
        })
    return list(races.values())


def _calc_stats(races: list[dict]) -> dict:
    """的中率・top3 命中率などの集計"""
    total = len(races)
    if total == 0:
        return {"total_races": 0}

    # 結果が確定しているレースのみ集計（actual_finish がある予測が1件以上存在）
    decided = [
        r for r in races
        if any(p["actual_finish"] is not None for p in r["predictions"])
    ]
    n = len(decided)
    if n == 0:
        return {"total_races": total, "decided_races": 0}

    top1_hit = sum(
        1 for r in decided
        if any(p["predicted_rank"] == 1 and p["actual_finish"] == 1 for p in r["predictions"])
    )
    top3_hit = sum(
        1 for r in decided
        if any(p["predicted_rank"] == 1 and (p["actual_finish"] or 99) <= 3 for p in r["predictions"])
    )
    return {
        "total_races":    total,
        "decided_races":  n,
        "top1_win_rate":  round(top1_hit / n * 100, 1),
        "top1_place3_rate": round(top3_hit / n * 100, 1),
    }


@router.get("/api/prediction-history")
async def prediction_history(
    limit: int = Query(default=200, le=1000),
    race_date: Optional[str] = Query(default=None, description="YYYYMMDD形式でフィルタ"),
    current_user: dict = Depends(require_premium),
):
    """過去の予測と実際の着順をレース単位で返す"""
    import asyncio
    rows = await asyncio.to_thread(_query_history, str(ULTIMATE_DB), limit, race_date)
    races = _group_by_race(rows)
    stats = _calc_stats(races)
    return {"races": races, "stats": stats}


@router.get("/api/prediction-history/{race_id}")
async def prediction_history_by_race(
    race_id: str,
    current_user: dict = Depends(require_premium),
):
    """特定レースの予測 vs 実際の着順を返す"""
    import asyncio

    def _query_one(db_path: str, rid: str) -> list[dict]:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='prediction_log'"
            ).fetchone()
            if not exists:
                return []
            rows = conn.execute(
                """
                SELECT
                    pl.horse_id,
                    pl.horse_name,
                    pl.horse_number,
                    pl.predicted_rank,
                    pl.win_probability,
                    pl.p_raw,
                    pl.odds,
                    pl.popularity,
                    pl.model_id,
                    pl.predicted_at,
                    r.finish  AS actual_finish,
                    r.time    AS finish_time,
                    r.last3f  AS actual_last3f,
                    r.odds    AS actual_odds
                FROM prediction_log pl
                LEFT JOIN results r
                       ON pl.race_id = r.race_id AND pl.horse_id = r.horse_id
                WHERE pl.race_id = ?
                ORDER BY pl.predicted_rank ASC
                """,
                (rid,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    rows = await asyncio.to_thread(_query_one, str(ULTIMATE_DB), race_id)

    # 結果が確定しているかどうか
    has_result = any(r["actual_finish"] is not None for r in rows)
    top1 = next((r for r in rows if r["predicted_rank"] == 1), None)

    return {
        "race_id": race_id,
        "has_prediction": len(rows) > 0,
        "has_result": has_result,
        "top1_win": top1 is not None and top1["actual_finish"] == 1,
        "top1_place3": top1 is not None and (top1["actual_finish"] or 99) <= 3,
        "predictions": rows,
    }
