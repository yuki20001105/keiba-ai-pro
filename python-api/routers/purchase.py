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

import json as _json
import math
import re
import sqlite3
from datetime import datetime
from typing import Optional

_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I
)


def _is_valid_uuid(val: Optional[str]) -> bool:
    """Supabase の user_id として有効な UUID か確認"""
    return bool(val and _UUID_RE.match(str(val)))

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

def _safe_numeric(v: Optional[float]) -> Optional[float]:
    """NaN / Infinity → None に変換（Supabase NUMERIC 型は非有限値を拒否する）"""
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


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

        if SUPABASE_ENABLED and get_supabase_client() and _is_valid_uuid(user_id):
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
            # 外れ(actual_return=0)はセンチネル-1で保存（未入力の0と区別するため）
            db_actual_return = body.actual_return if body.actual_return > 0 else -1
            rr = round(db_actual_return / total_cost * 100, 1) if (total_cost > 0 and db_actual_return > 0) else 0
            client.table("purchase_history").update(
                {"actual_return": db_actual_return, "is_hit": body.is_hit, "recovery_rate": rr}
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
            # 外れ(actual_return=0)はセンチネル-1で保存（未入力の0と区別するため）
            db_actual_return = body.actual_return if body.actual_return > 0 else -1
            rr = round(db_actual_return / total_cost * 100, 1) if (total_cost > 0 and db_actual_return > 0) else 0
            cursor.execute(
                "UPDATE purchase_history SET actual_return = ?, is_hit = ?, recovery_rate = ? WHERE id = ?",
                (db_actual_return, 1 if body.is_hit else 0, rr, int_id),
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

        if SUPABASE_ENABLED and get_supabase_client() and _is_valid_uuid(user_id):
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

        if SUPABASE_ENABLED and get_supabase_client() and _is_valid_uuid(user_id):
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
                "expected_value": _safe_numeric(request.expected_value),
                "expected_return": _safe_numeric(request.expected_return),
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

        if SUPABASE_ENABLED and get_supabase_client() and _is_valid_uuid(user_id):
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

        if SUPABASE_ENABLED and get_supabase_client() and _is_valid_uuid(user_id):
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


# ── 自動結果入力 ────────────────────────────────────────────────────────

def _match_combo(purchased: str, paid: str) -> bool:
    """購入組合せと払戻組合せが一致するか判定。
    単勝/複勝: 馬番一致。馬連/ワイド等: '-' 区切りで並び順を無視。"""
    p1 = purchased.strip()
    p2 = paid.strip()
    if p1 == p2:
        return True
    if "-" in p1 or "-" in p2:
        return set(p1.split("-")) == set(p2.split("-"))
    return False


async def _try_scrape_race(race_id: str) -> bool:
    """払戻データがないレースを netkeiba から直接スクレイプして DB に保存する。
    払戻データを1件以上保存できたら True を返す。
    """
    from app_config import ULTIMATE_DB  # type: ignore
    from scraping.race import scrape_race_full  # type: ignore
    from scraping.storage import _save_race_sqlite_only  # type: ignore
    from scraping.constants import SCRAPE_PROXY_URL, get_random_headers  # type: ignore
    import aiohttp  # type: ignore

    if not ULTIMATE_DB.exists():
        return False

    # races_ultimate から日付を取得（date_hint に使う）
    date_hint = ""
    try:
        mc = sqlite3.connect(str(ULTIMATE_DB))
        row = mc.execute("SELECT data FROM races_ultimate WHERE race_id=?", (race_id,)).fetchone()
        mc.close()
        if row:
            d = _json.loads(row[0])
            date_hint = str(d.get("date", ""))
    except Exception:
        pass

    # 未来のレースはスクレイプ不要
    today = datetime.now().strftime("%Y%m%d")
    if date_hint and date_hint > today:
        logger.info(f"[auto-scrape] {race_id}: 未来のレース (date={date_hint}) → スキップ")
        return False

    logger.info(f"[auto-scrape] {race_id} をスクレイプ中... (date_hint={date_hint!r})")
    try:
        _timeout = aiohttp.ClientTimeout(total=30, connect=8)
        _connector = aiohttp.TCPConnector(limit=1, limit_per_host=1, force_close=True)
        _hdrs = get_random_headers()
        _kwargs: dict = {}
        if SCRAPE_PROXY_URL:
            _kwargs["trust_env"] = False
        async with aiohttp.ClientSession(
            headers=_hdrs, timeout=_timeout, connector=_connector, **_kwargs
        ) as session:
            race_data = await scrape_race_full(session, race_id, date_hint=date_hint)

        if not race_data or not race_data.get("return_tables"):
            logger.warning(f"[auto-scrape] {race_id}: 払戻データなし（レース未終了 or ブロック）")
            return False

        ok = _save_race_sqlite_only(race_data, ULTIMATE_DB)
        if ok:
            pct = len(race_data.get("return_tables", []))
            logger.info(f"[auto-scrape] {race_id}: 保存完了 (払戻{pct}件)")
        return ok
    except Exception as e:
        logger.error(f"[auto-scrape] {race_id}: {e}")
        return False


def _auto_fill_single(purchase_id: str, user_id: Optional[str]) -> dict:
    """keiba_ultimate.db の return_tables_ultimate を参照して購入結果を自動入力する。"""
    from app_config import ULTIMATE_DB  # type: ignore

    # ── 1. 購入記録取得 ──────────────────────────────────────────────
    if SUPABASE_ENABLED and get_supabase_client() and _is_valid_uuid(user_id):
        client = get_supabase_client()
        res = (
            client.table("purchase_history")
            .select("id,race_id,bet_type,combinations,unit_price,purchase_count,total_cost")
            .eq("id", purchase_id)
            .eq("user_id", user_id)
            .execute()
        )
        if not res.data:
            raise HTTPException(status_code=404, detail="購入記録が見つかりません")
        rec = res.data[0]
        race_id = rec.get("race_id") or ""
        bet_type = rec.get("bet_type") or ""
        combos_str = rec.get("combinations") or ""
        unit_price = rec.get("unit_price") or 100
        purchase_count = rec.get("purchase_count") or 1
        total_cost = rec.get("total_cost") or (unit_price * purchase_count)
    else:
        path = _tracking_db_path()
        if not path.exists():
            raise HTTPException(status_code=404, detail="購入記録が見つかりません")
        try:
            int_id = int(purchase_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="無効なID形式です")
        con = sqlite3.connect(str(path))
        row = con.execute(
            "SELECT race_id, bet_type, combinations, unit_price, purchase_count, total_cost "
            "FROM purchase_history WHERE id=?",
            (int_id,),
        ).fetchone()
        con.close()
        if not row:
            raise HTTPException(status_code=404, detail="購入記録が見つかりません")
        race_id, bet_type, combos_str, unit_price, purchase_count, total_cost = row
        unit_price = unit_price or 100
        purchase_count = purchase_count or 1
        total_cost = total_cost or (unit_price * purchase_count)

    if not race_id or not bet_type:
        raise HTTPException(status_code=422, detail="race_id または bet_type が不正です")

    # ── 2. 払戻データ取得 ────────────────────────────────────────────
    if not ULTIMATE_DB.exists():
        raise HTTPException(status_code=503, detail="メインDBが見つかりません")

    mcon = sqlite3.connect(str(ULTIMATE_DB))
    payout_rows = mcon.execute(
        "SELECT combinations, payout FROM return_tables_ultimate WHERE race_id=? AND bet_type=?",
        (race_id, bet_type),
    ).fetchall()
    mcon.close()

    if not payout_rows:
        # 該当race_idのデータが全く存在するか確認（bet_type不問）
        mcon2 = sqlite3.connect(str(ULTIMATE_DB))
        any_rows = mcon2.execute(
            "SELECT COUNT(*) FROM return_tables_ultimate WHERE race_id=?", (race_id,)
        ).fetchone()[0]
        mcon2.close()
        if any_rows > 0:
            msg = f"{race_id} の {bet_type} 払戻データがありません（他の券種はあり）"
        else:
            msg = f"{race_id} の払戻データがありません（未開催 or 未スクレイプ）"
        return {"found": False, "race_id": race_id, "message": msg}

    # ── 3. 的中判定・払戻計算 ────────────────────────────────────────
    purchased_combos = [c.strip() for c in (combos_str or "").split(",") if c.strip()]
    # 1組合せあたりの賭け金（total_cost をコンボ数で割る）
    per_unit = int(total_cost / max(len(purchased_combos), 1))

    actual_return = 0
    hit_details: list = []
    for paid_combo, payout in payout_rows:
        for purchased in purchased_combos:
            if _match_combo(purchased, paid_combo):
                gain = int(payout * per_unit / 100)
                actual_return += gain
                hit_details.append(f"{purchased}→¥{gain:,}")
                break

    is_hit = actual_return > 0

    # ── 4. 購入記録を更新 ────────────────────────────────────────────
    # 外れ(actual_return=0)はセンチネル-1で保存（未入力の0と区別し、ページ遷移後も完了セクションに表示するため）
    db_actual_return = actual_return if actual_return > 0 else -1
    rr = round(actual_return / total_cost * 100, 1) if total_cost > 0 else 0.0
    if SUPABASE_ENABLED and get_supabase_client() and _is_valid_uuid(user_id):
        client = get_supabase_client()
        client.table("purchase_history").update(
            {"actual_return": db_actual_return, "is_hit": is_hit, "recovery_rate": rr}
        ).eq("id", purchase_id).eq("user_id", user_id).execute()
    else:
        int_id = int(purchase_id)
        path = _tracking_db_path()
        con = sqlite3.connect(str(path))
        con.execute(
            "UPDATE purchase_history SET actual_return=?, is_hit=?, recovery_rate=? WHERE id=?",
            (db_actual_return, 1 if is_hit else 0, rr, int_id),
        )
        con.commit()
        con.close()

    msg = "✓ 的中" if is_hit else "✗ 外れ"
    if hit_details:
        msg += ": " + ", ".join(hit_details)
    return {"found": True, "is_hit": is_hit, "actual_return": db_actual_return, "recovery_rate": rr, "message": msg}


@router.post("/api/purchase/{purchase_id}/auto-result")
async def auto_fill_result(purchase_id: str, req: Request):
    """keiba_ultimate.db の払戻データを参照して自動的に結果を入力する。
    払戻データがない場合は自動スクレイプを試みてからリトライする。
    """
    try:
        user_id = _get_user_id(req)
        result = _auto_fill_single(purchase_id, user_id)
        if not result.get("found"):
            race_id = result.get("race_id", "")
            if race_id:
                scraped = await _try_scrape_race(race_id)
                if scraped:
                    result = _auto_fill_single(purchase_id, user_id)
        return {"success": True, **result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"自動入力に失敗: {str(e)}")


@router.post("/api/purchase/batch-auto-result")
async def batch_auto_fill(req: Request):
    """is_hit=0 かつ actual_return=0 の未入力購入を一括自動入力する"""
    try:
        user_id = _get_user_id(req)

        # 未入力の購入ID一覧を取得
        if SUPABASE_ENABLED and get_supabase_client() and _is_valid_uuid(user_id):
            client = get_supabase_client()
            res = (
                client.table("purchase_history")
                .select("id")
                .eq("user_id", user_id)
                .eq("is_hit", False)
                .eq("actual_return", 0)
                .execute()
            )
            ids = [str(r["id"]) for r in (res.data or [])]
        else:
            path = _tracking_db_path()
            if not path.exists():
                return {"processed": 0, "hit": 0, "miss": 0, "skipped": 0, "results": []}
            con = sqlite3.connect(str(path))
            rows = con.execute(
                "SELECT id FROM purchase_history WHERE is_hit=0 AND actual_return=0"
            ).fetchall()
            con.close()
            ids = [str(r[0]) for r in rows]

        hit_count = miss_count = skipped = error_count = 0
        results: list = []
        _scraped_races: set[str] = set()  # 同一レースの重複スクレイプ防止

        for pid in ids:
            try:
                r = _auto_fill_single(pid, user_id)
                if not r.get("found"):
                    race_id = r.get("race_id", "")
                    if race_id and race_id not in _scraped_races:
                        _scraped_races.add(race_id)
                        scraped = await _try_scrape_race(race_id)
                        if scraped:
                            r = _auto_fill_single(pid, user_id)
                if not r.get("found"):
                    skipped += 1
                    results.append({"id": pid, "status": "skipped", "message": r.get("message", "")})
                elif r["is_hit"]:
                    hit_count += 1
                    results.append({"id": pid, "status": "hit", "actual_return": r["actual_return"]})
                else:
                    miss_count += 1
                    results.append({"id": pid, "status": "miss"})
            except HTTPException:
                error_count += 1
                results.append({"id": pid, "status": "error"})

        return {
            "success": True,
            "processed": hit_count + miss_count,
            "hit": hit_count,
            "miss": miss_count,
            "skipped": skipped,
            "errors": error_count,
            "results": results,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"一括自動入力に失敗: {str(e)}")
