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
                CAST(json_extract(rr.data, '$.finish_position') AS INTEGER) AS actual_finish,
                json_extract(rr.data, '$.finish_time')               AS finish_time,
                CAST(json_extract(rr.data, '$.odds') AS REAL)         AS actual_odds
            FROM prediction_log pl
            LEFT JOIN race_results_ultimate rr
                ON  rr.race_id = pl.race_id
                AND json_extract(rr.data, '$.horse_id') = pl.horse_id
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
        # EV = win_probability * odds - 1
        ev: Optional[float] = None
        wp = row["win_probability"]
        od = row["odds"]
        if wp is not None and od is not None and od > 0:
            ev = round(wp * od - 1, 3)
        races[rid]["predictions"].append({
            "horse_id":        row["horse_id"],
            "horse_name":      row["horse_name"],
            "horse_number":    row["horse_number"],
            "predicted_rank":  row["predicted_rank"],
            "win_probability": row["win_probability"],
            "p_raw":           row["p_raw"],
            "odds":            row["odds"],
            "ev":              ev,
            "actual_finish":   row["actual_finish"],
            "finish_time":     row["finish_time"],
            "actual_odds":     row["actual_odds"],
        })
    return list(races.values())


def _calc_stats(races: list[dict]) -> dict:
    """的中率・ROI・EV などの集計（Task5: クラス別・競馬場別・距離別ROI追加）"""
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

    # ROI シミュレーション: predicted_rank=1 の馬に毎回100円賭けた場合
    top1_preds = [
        p for r in decided
        for p in r["predictions"]
        if p["predicted_rank"] == 1
    ]
    n_bets = len(top1_preds)
    roi: Optional[float] = None
    avg_ev: Optional[float] = None
    positive_ev_rate: Optional[float] = None
    avg_odds: Optional[float] = None
    avg_kelly: Optional[float] = None
    if n_bets > 0:
        # 実際のオッズ（確定値）を優先、なければ予測時オッズを使用
        total_return = sum(
            (p["actual_odds"] or p["odds"] or 0)
            for p in top1_preds
            if p["actual_finish"] == 1
        )
        roi = round((total_return / n_bets - 1) * 100, 1)

        ev_list = [p["ev"] for p in top1_preds if p["ev"] is not None]
        if ev_list:
            avg_ev = round(sum(ev_list) / len(ev_list), 3)
            positive_ev_rate = round(sum(1 for e in ev_list if e > 0) / len(ev_list) * 100, 1)

        # 平均オッズ（Task5）
        odds_list = [p["odds"] or p["actual_odds"] for p in top1_preds
                     if (p["odds"] or p["actual_odds"])]
        if odds_list:
            avg_odds = round(sum(odds_list) / len(odds_list), 2)

        # 平均Kelly（Task5: ev から近似）
        kelly_list = []
        for p in top1_preds:
            wp = p.get("win_probability")
            od = p.get("odds") or p.get("actual_odds")
            if wp and od and od > 1.0 and wp * od >= 1.3:
                k = (wp * od - 1) / (od - 1) * 0.25
                kelly_list.append(min(k, 0.05))
        if kelly_list:
            avg_kelly = round(sum(kelly_list) / len(kelly_list), 4)

    # ── クラス別・競馬場別・距離別ROI（Task5）──────────────────────────────
    def _roi_by_key(key: str) -> list[dict]:
        from collections import defaultdict
        groups: dict[str, dict] = defaultdict(lambda: {"bets": 0, "returns": 0.0})
        for r in decided:
            grp_val = r.get(key, "不明") or "不明"
            for p in r["predictions"]:
                if p["predicted_rank"] != 1:
                    continue
                groups[str(grp_val)]["bets"] += 1
                if p["actual_finish"] == 1:
                    groups[str(grp_val)]["returns"] += (p["actual_odds"] or p["odds"] or 0)
        result = []
        for k, v in sorted(groups.items()):
            nb = v["bets"]
            nr = v["returns"]
            result.append({
                "key": k,
                "bets": nb,
                "roi": round((nr / nb - 1) * 100, 1) if nb > 0 else None,
            })
        return result

    return {
        "total_races":         total,
        "decided_races":       n,
        "n_bets":              n_bets,
        "top1_win_rate":       round(top1_hit / n * 100, 1),
        "top1_place3_rate":    round(top3_hit / n * 100, 1),
        "roi":                 roi,
        "avg_ev":              avg_ev,
        "positive_ev_rate":    positive_ev_rate,
        "avg_odds":            avg_odds,
        "avg_kelly":           avg_kelly,
        "roi_by_venue":        _roi_by_key("venue"),
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


def _query_history_since(db_path: str, days: Optional[int]) -> list[dict]:
    """days日以内の全予測を取得（None=全期間）"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='prediction_log'"
        ).fetchone()
        if not exists:
            return []
        date_filter = ""
        params: list = []
        if days is not None:
            date_filter = "AND pl.race_date >= strftime('%Y%m%d', date('now', ?))"
            params.append(f"-{days} days")
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
                CAST(json_extract(rr.data, '$.finish_position') AS INTEGER) AS actual_finish,
                json_extract(rr.data, '$.finish_time')               AS finish_time,
                CAST(json_extract(rr.data, '$.odds') AS REAL)         AS actual_odds
            FROM prediction_log pl
            LEFT JOIN race_results_ultimate rr
                ON  rr.race_id = pl.race_id
                AND json_extract(rr.data, '$.horse_id') = pl.horse_id
            WHERE 1=1 {date_filter}
            ORDER BY pl.race_date DESC, pl.race_id DESC, pl.predicted_rank ASC
            LIMIT 5000
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


@router.get("/api/prediction-history/summary")
async def prediction_history_summary(
    current_user: dict = Depends(require_premium),
):
    """期間別の的中率・ROI サマリーを返す"""
    import asyncio

    periods = [
        {"label": "直近30日",  "days": 30},
        {"label": "直近90日",  "days": 90},
        {"label": "直近180日", "days": 180},
        {"label": "全期間",    "days": None},
    ]

    results = []
    for p in periods:
        rows = await asyncio.to_thread(_query_history_since, str(ULTIMATE_DB), p["days"])
        races = _group_by_race(rows)
        stats = _calc_stats(races)
        results.append({"period": p["label"], **stats})

    # モデル別成績（全期間）
    all_rows = await asyncio.to_thread(_query_history_since, str(ULTIMATE_DB), None)
    model_stats = _calc_stats_by_model(all_rows)

    return {"periods": results, "by_model": model_stats}


def _calc_stats_by_model(rows: list[dict]) -> list[dict]:
    """model_id別の的中率・ROI を集計"""
    from collections import defaultdict
    by_model: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("predicted_rank") == 1:
            by_model[row["model_id"]].append(row)

    result = []
    for model_id, preds in by_model.items():
        n = len(preds)
        decided = [p for p in preds if p["actual_finish"] is not None]
        nd = len(decided)
        if nd == 0:
            continue
        wins = sum(1 for p in decided if p["actual_finish"] == 1)
        places = sum(1 for p in decided if (p["actual_finish"] or 99) <= 3)
        total_return = sum(
            (p["actual_odds"] or p["odds"] or 0)
            for p in decided
            if p["actual_finish"] == 1
        )
        roi = round((total_return / nd - 1) * 100, 1) if nd > 0 else None
        result.append({
            "model_id": model_id,
            "total_bets": n,
            "decided": nd,
            "win_rate": round(wins / nd * 100, 1),
            "place3_rate": round(places / nd * 100, 1),
            "roi": roi,
        })
    result.sort(key=lambda x: x["win_rate"], reverse=True)
    return result


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
            # 同一 race_id + horse_id の最新予測のみを取得（重複回避）
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
                    CAST(json_extract(rr.data, '$.finish_position') AS INTEGER) AS actual_finish,
                    json_extract(rr.data, '$.finish_time')               AS finish_time,
                    CAST(json_extract(rr.data, '$.last3f') AS REAL)       AS actual_last3f,
                    CAST(json_extract(rr.data, '$.odds') AS REAL)         AS actual_odds
                FROM prediction_log pl
                INNER JOIN (
                    SELECT horse_id, MAX(predicted_at) AS latest_at
                    FROM prediction_log
                    WHERE race_id = ?
                    GROUP BY horse_id
                ) latest ON pl.horse_id = latest.horse_id
                         AND pl.predicted_at = latest.latest_at
                LEFT JOIN race_results_ultimate rr
                    ON  rr.race_id = pl.race_id
                    AND json_extract(rr.data, '$.horse_id') = pl.horse_id
                WHERE pl.race_id = ?
                ORDER BY pl.predicted_rank ASC
                """,
                (rid, rid),
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
