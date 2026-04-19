"""
購入履歴・統計エンドポイント
POST /api/purchase
GET  /api/purchase_history
GET  /api/statistics

Supabase 対応版:
  - SUPABASE_ENABLED + user_id がある場合 → Supabase public.purchase_history テーブル
  - それ以外 → tracking.db (SQLite) フォールバック
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app_config import CONFIG_PATH, SUPABASE_ENABLED, get_supabase_client, logger  # type: ignore
from models import PurchaseHistoryRequest, PurchaseHistoryResponse  # type: ignore

router = APIRouter()

_TRACKING_DDL = """
CREATE TABLE IF NOT EXISTS purchase_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id TEXT NOT NULL,
    purchase_date TEXT,
    season TEXT,
    venue TEXT,
    bet_type TEXT NOT NULL,
    combinations TEXT,
    strategy_type TEXT,
    purchase_count INTEGER,
    unit_price INTEGER,
    total_cost INTEGER,
    expected_value REAL,
    expected_return REAL,
    actual_return INTEGER DEFAULT 0,
    is_hit INTEGER DEFAULT 0,
    recovery_rate REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


def _tracking_db_path():
    return CONFIG_PATH.parent / "data" / "tracking.db"


def _get_user_id(req: Request) -> Optional[str]:
    """JWT ミドルウェアがセットした user_id を取得"""
    return getattr(req.state, "user_id", None)


# ── Supabase helpers ─────────────────────────────────────────────────

def _save_purchase_supabase(user_id: str, data: dict) -> int:
    client = get_supabase_client()
    res = client.table("purchase_history").insert(data).execute()
    raw_id = res.data[0].get("id", "") if res.data else ""
    # UUID → int 変換（API の互換性维持）
    return hash(str(raw_id)) % 2_000_000_000


def _get_history_supabase(user_id: str, limit: int) -> list:
    client = get_supabase_client()
    res = (
        client.table("purchase_history")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    rows = res.data or []
    for row in rows:
        if isinstance(row.get("combinations"), str):
            row["combinations"] = row["combinations"].split(",") if row["combinations"] else []
    return rows


def _get_stats_supabase(user_id: str) -> dict:
    client = get_supabase_client()
    res = (
        client.table("purchase_history")
        .select("bet_type, season, total_cost, actual_return, is_hit")
        .eq("user_id", user_id)
        .execute()
    )
    rows = res.data or []

    bt: dict = {}
    ss: dict = {}
    for r in rows:
        t = r.get("bet_type", "")
        bt.setdefault(t, {"bet_type": t, "count": 0, "total_cost": 0, "total_return": 0, "hit_count": 0})
        bt[t]["count"] += 1
        bt[t]["total_cost"] += r.get("total_cost") or 0
        bt[t]["total_return"] += r.get("actual_return") or 0
        bt[t]["hit_count"] += 1 if r.get("is_hit") else 0

        s = r.get("season", "")
        ss.setdefault(s, {"season": s, "count": 0, "total_cost": 0, "total_return": 0})
        ss[s]["count"] += 1
        ss[s]["total_cost"] += r.get("total_cost") or 0
        ss[s]["total_return"] += r.get("actual_return") or 0

    def _rate(ret, cost):
        return round(ret / cost * 100, 1) if cost > 0 else 0

    bet_type_stats = [
        {**v,
         "recovery_rate": _rate(v["total_return"], v["total_cost"]),
         "hit_rate": round(v["hit_count"] / v["count"] * 100, 1) if v["count"] > 0 else 0}
        for v in bt.values()
    ]
    season_stats = [
        {**v, "recovery_rate": _rate(v["total_return"], v["total_cost"])}
        for v in ss.values()
    ]
    return {"by_bet_type": bet_type_stats, "by_season": season_stats}


class UpdatePurchaseResultRequest(BaseModel):
    actual_return: int
    is_hit: bool


@router.patch("/api/purchase/{purchase_id}")
async def update_purchase_result(purchase_id: str, body: UpdatePurchaseResultRequest, req: Request):
    """購入結果更新（実際の払戻金・的中フラグ）"""
    try:
        user_id = _get_user_id(req)

        if SUPABASE_ENABLED and get_supabase_client() and user_id:
            client = get_supabase_client()
            res = (
                client.table("purchase_history")
                .select("total_cost")
                .eq("id", purchase_id)
                .eq("user_id", user_id)
                .execute()
            )
            if not res.data:
                raise HTTPException(status_code=404, detail="購入記録が見つかりません")
            total_cost = res.data[0].get("total_cost") or 0
            rr = round(body.actual_return / total_cost * 100, 1) if total_cost > 0 else 0
            client.table("purchase_history").update(
                {"actual_return": body.actual_return, "is_hit": body.is_hit, "recovery_rate": rr}
            ).eq("id", purchase_id).eq("user_id", user_id).execute()
        else:
            path = _tracking_db_path()
            if not path.exists():
                raise HTTPException(status_code=404, detail="購入記録が見つかりません")
            try:
                int_id = int(purchase_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="無効なID形式です")
            con = sqlite3.connect(str(path))
            cursor = con.cursor()
            cursor.execute("SELECT total_cost FROM purchase_history WHERE id = ?", (int_id,))
            row = cursor.fetchone()
            if not row:
                con.close()
                raise HTTPException(status_code=404, detail="購入記録が見つかりません")
            total_cost = row[0] or 0
            rr = round(body.actual_return / total_cost * 100, 1) if total_cost > 0 else 0
            cursor.execute(
                "UPDATE purchase_history SET actual_return = ?, is_hit = ?, recovery_rate = ? WHERE id = ?",
                (body.actual_return, 1 if body.is_hit else 0, rr, int_id),
            )
            con.commit()
            con.close()

        return {"success": True, "message": "結果を更新しました"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新に失敗: {str(e)}")


@router.delete("/api/purchase/{purchase_id}")
async def delete_purchase(purchase_id: str, req: Request):
    """購入履歴削除エンドポイント"""
    try:
        user_id = _get_user_id(req)

        if SUPABASE_ENABLED and get_supabase_client() and user_id:
            client = get_supabase_client()
            res = (
                client.table("purchase_history")
                .select("id")
                .eq("id", purchase_id)
                .eq("user_id", user_id)
                .execute()
            )
            if not res.data:
                raise HTTPException(status_code=404, detail="購入記録が見つかりません")
            client.table("purchase_history").delete().eq("id", purchase_id).eq("user_id", user_id).execute()
        else:
            path = _tracking_db_path()
            if not path.exists():
                raise HTTPException(status_code=404, detail="購入記録が見つかりません")
            try:
                int_id = int(purchase_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="無効なID形式です")
            con = sqlite3.connect(str(path))
            cursor = con.cursor()
            cursor.execute("SELECT id FROM purchase_history WHERE id = ?", (int_id,))
            if not cursor.fetchone():
                con.close()
                raise HTTPException(status_code=404, detail="購入記録が見つかりません")
            cursor.execute("DELETE FROM purchase_history WHERE id = ?", (int_id,))
            con.commit()
            con.close()

        return {"success": True, "message": "削除しました"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"削除に失敗: {str(e)}")


@router.post("/api/purchase", response_model=PurchaseHistoryResponse)
async def save_purchase_history(request: PurchaseHistoryRequest, req: Request):
    """購入履歴保存エンドポイント（Supabase / SQLite 自動選択）"""
    try:
        purchase_date = datetime.now().strftime("%Y-%m-%d")
        month = datetime.now().month
        season = "春" if 3 <= month <= 5 else "夏" if 6 <= month <= 8 else "秋" if 9 <= month <= 11 else "冬"
        combinations_str = ",".join(request.combinations)
        user_id = _get_user_id(req)

        if SUPABASE_ENABLED and get_supabase_client() and user_id:
            data = {
                "user_id": user_id,
                "race_id": request.race_id,
                "purchase_date": purchase_date,
                "season": season,
                "venue": request.venue,
                "bet_type": request.bet_type,
                "combinations": combinations_str,
                "strategy_type": request.strategy_type,
                "purchase_count": request.purchase_count,
                "unit_price": request.unit_price,
                "total_cost": request.total_cost,
                "expected_value": request.expected_value,
                "expected_return": request.expected_return,
            }
            purchase_id = _save_purchase_supabase(user_id, data)
            label = "Supabase"
        else:
            # SQLite フォールバック
            path = _tracking_db_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(str(path))
            cursor = con.cursor()
            cursor.execute(_TRACKING_DDL)
            cursor.execute(
                """
                INSERT INTO purchase_history (
                    race_id, purchase_date, season, venue, bet_type, combinations,
                    strategy_type, purchase_count, unit_price, total_cost,
                    expected_value, expected_return
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.race_id, purchase_date, season, request.venue,
                    request.bet_type, combinations_str,
                    request.strategy_type, request.purchase_count,
                    request.unit_price, request.total_cost,
                    request.expected_value, request.expected_return,
                ),
            )
            purchase_id = cursor.lastrowid
            con.commit()
            con.close()
            label = "SQLite"

        return PurchaseHistoryResponse(
            success=True,
            purchase_id=purchase_id,
            message=f"購入履歴を保存しました (ID: {purchase_id}, store: {label})",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"購入履歴の保存に失敗: {str(e)}")


@router.get("/api/purchase_history")
async def get_purchase_history(req: Request, limit: int = 50):
    """購入履歴取得エンドポイント（Supabase / SQLite 自動選択）"""
    try:
        user_id = _get_user_id(req)

        if SUPABASE_ENABLED and get_supabase_client() and user_id:
            history = _get_history_supabase(user_id, limit)
        else:
            # SQLite フォールバック
            path = _tracking_db_path()
            if not path.exists():
                return {"success": True, "history": [], "count": 0, "message": "購入履歴がまだありません"}
            con = sqlite3.connect(str(path))
            cursor = con.cursor()
            cursor.execute(
                """
                SELECT id, race_id, purchase_date, season, bet_type, combinations,
                       strategy_type, purchase_count, unit_price, total_cost,
                       expected_value, expected_return, actual_return,
                       is_hit, recovery_rate, created_at
                FROM purchase_history ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            con.close()
            history = [
                {
                    "id": row[0], "race_id": row[1], "purchase_date": row[2], "season": row[3],
                    "bet_type": row[4], "combinations": row[5].split(",") if row[5] else [],
                    "strategy_type": row[6], "purchase_count": row[7], "unit_price": row[8],
                    "total_cost": row[9], "expected_value": row[10], "expected_return": row[11],
                    "actual_return": row[12], "is_hit": bool(row[13]),
                    "recovery_rate": row[14], "created_at": row[15],
                }
                for row in rows
            ]

        if not history:
            return {"success": True, "history": [], "count": 0, "message": "購入履歴がまだありません"}

        total_cost = sum(h.get("total_cost") or 0 for h in history)
        total_return = sum(h.get("actual_return") or 0 for h in history)
        hit_count = sum(1 for h in history if h.get("is_hit"))

        return {
            "success": True, "history": history, "count": len(history),
            "summary": {
                "total_cost": total_cost, "total_return": total_return,
                "recovery_rate": round(total_return / total_cost * 100, 1) if total_cost > 0 else 0,
                "hit_count": hit_count,
                "hit_rate": round(hit_count / len(history) * 100, 1) if history else 0,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"購入履歴の取得に失敗: {str(e)}")


@router.get("/api/statistics")
async def get_statistics(req: Request):
    """統計サマリー取得エンドポイント（Supabase / SQLite 自動選択）"""
    try:
        user_id = _get_user_id(req)

        if SUPABASE_ENABLED and get_supabase_client() and user_id:
            stats = _get_stats_supabase(user_id)
            return {"success": True, "statistics": stats}

        # SQLite フォールバック
        path = _tracking_db_path()
        if not path.exists():
            return {"success": True, "statistics": {}, "message": "統計データがまだありません"}

        con = sqlite3.connect(str(path))
        cursor = con.cursor()

        cursor.execute("""
            SELECT bet_type, COUNT(*) as count,
                   SUM(total_cost) as total_cost, SUM(actual_return) as total_return,
                   SUM(is_hit) as hit_count
            FROM purchase_history GROUP BY bet_type
        """)
        bet_type_stats = [
            {
                "bet_type": r[0], "count": r[1], "total_cost": r[2], "total_return": r[3],
                "recovery_rate": round(r[3] / r[2] * 100, 1) if r[2] > 0 else 0,
                "hit_count": r[4], "hit_rate": round(r[4] / r[1] * 100, 1) if r[1] > 0 else 0,
            }
            for r in cursor.fetchall()
        ]

        cursor.execute("""
            SELECT season, COUNT(*) as count,
                   SUM(total_cost) as total_cost, SUM(actual_return) as total_return
            FROM purchase_history GROUP BY season
        """)
        season_stats = [
            {
                "season": r[0], "count": r[1], "total_cost": r[2], "total_return": r[3],
                "recovery_rate": round(r[3] / r[2] * 100, 1) if r[2] > 0 else 0,
            }
            for r in cursor.fetchall()
        ]
        con.close()

        return {"success": True, "statistics": {"by_bet_type": bet_type_stats, "by_season": season_stats}}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"統計の取得に失敗: {str(e)}")
