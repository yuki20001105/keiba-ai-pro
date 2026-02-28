"""
統計・診断系エンドポイント: ヘルスチェック、データ統計、Supabase 接続テスト等。
"""

import asyncio
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Optional

import aiohttp
from fastapi import APIRouter, HTTPException

from app_config import (  # type: ignore
    CONFIG_PATH,
    MODELS_DIR,
    SUPABASE_ENABLED,
    load_config,
    logger,
)
from models import TrainRequest  # type: ignore

try:
    from supabase_client import (  # type: ignore
        get_client as get_supabase_client,
        get_data_stats_from_supabase,
    )
except ImportError:
    def get_supabase_client():  # type: ignore
        return None

    def get_data_stats_from_supabase():  # type: ignore
        return {}

try:
    from scraping.constants import SCRAPE_HEADERS  # type: ignore
except ImportError:
    SCRAPE_HEADERS = {}

# ジョブストア参照（test/task エンドポイント用）
try:
    from scraping.jobs import _scrape_jobs  # type: ignore
except ImportError:
    _scrape_jobs: dict = {}

router = APIRouter()


@router.get("/")
async def root():
    """ヘルスチェック"""
    return {
        "status": "ok",
        "service": "Keiba AI - Machine Learning API",
        "version": "1.0.0",
    }


@router.get("/api/debug")
async def debug_info():
    """Supabase接続状態のデバッグ情報"""
    import os

    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    result = {
        "supabase_enabled": SUPABASE_ENABLED,
        "supabase_url_set": bool(supabase_url),
        "supabase_key_set": bool(supabase_key),
        "supabase_url_prefix": supabase_url[:30] if supabase_url else "",
        "supabase_key_prefix": supabase_key[:15] if supabase_key else "",
    }
    if SUPABASE_ENABLED:
        try:
            client = get_supabase_client()
            result["client_created"] = client is not None
            if client:
                res = (
                    client.table("races_ultimate").select("race_id", count="exact").limit(1).execute()
                )
                result["races_ultimate_accessible"] = True
                result["races_count"] = res.count
        except Exception as e:
            result["client_error"] = str(e)
    return result


@router.post("/api/test-optuna-request")
async def test_optuna_request(request: TrainRequest):
    """Optunaリクエストのテスト用エンドポイント"""
    return {
        "received": {
            "target": request.target,
            "model_type": request.model_type,
            "use_optimizer": request.use_optimizer,
            "use_optuna": request.use_optuna,
            "optuna_trials": request.optuna_trials,
            "cv_folds": request.cv_folds,
        },
        "will_execute_optuna": (
            request.use_optuna and request.model_type == "lightgbm" and request.use_optimizer
        ),
        "message": "リクエストは正しく受信されました",
    }


@router.get("/api/data_stats")
async def get_data_stats(ultimate: bool = False):
    """
    データベース統計情報を取得

    Args:
        ultimate: Ultimate版DBを使用するかどうか
    """
    try:
        if SUPABASE_ENABLED and get_supabase_client():
            # BUG FIX: 同期ブロッキング呼び出しをイベントループから切り離す
            return await asyncio.to_thread(get_data_stats_from_supabase)

        if ultimate:
            db_path = Path(__file__).parent.parent.parent / "keiba" / "data" / "keiba_ultimate.db"
        else:
            cfg = load_config(CONFIG_PATH)
            db_path = cfg.storage.sqlite_path
            if not db_path.is_absolute():
                db_path = CONFIG_PATH.parent / db_path

        if not db_path.exists():
            return {
                "total_races": 0,
                "total_horses": 0,
                "total_models": 0,
                "db_exists": False,
            }

        con = sqlite3.connect(db_path)
        cursor = con.cursor()

        try:
            cursor.execute("SELECT COUNT(DISTINCT race_id) FROM races")
            total_races = cursor.fetchone()[0]
        except Exception:
            total_races = 0

        try:
            cursor.execute("SELECT COUNT(DISTINCT horse_id) FROM entries")
            total_horses = cursor.fetchone()[0]
        except Exception:
            try:
                cursor.execute("SELECT COUNT(*) FROM entries")
                total_horses = cursor.fetchone()[0]
            except Exception:
                total_horses = 0

        con.close()

        total_models = len(list(MODELS_DIR.glob("model_*.joblib")))

        return {
            "total_races": total_races,
            "total_horses": total_horses,
            "total_models": total_models,
            "db_exists": True,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"統計取得エラー: {str(e)}")


@router.get("/api/test/task")
async def scrape_start_debug():
    """GETテスト: asyncio.create_taskと1秒待機してstatusをconfirmedに変えるタスクを発火テスト"""
    test_id = "test_" + str(uuid.uuid4())[:4]
    _scrape_jobs[test_id] = {"status": "queued", "progress": {}, "result": None, "error": None}

    async def _quick_task():
        await asyncio.sleep(1)
        _scrape_jobs[test_id]["status"] = "confirmed"

    asyncio.get_running_loop().create_task(_quick_task())
    return {"test_id": test_id, "message": "1秒待って GET /api/scrape/status/{test_id} で確認"}


@router.get("/api/test/connectivity")
async def test_connectivity():
    """netkeiba疎通確認・Supabase書き込みテスト"""
    result = {}

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(headers=SCRAPE_HEADERS, timeout=timeout) as session:
            async with session.get("https://db.netkeiba.com/race/list/20250101/") as resp:
                content = await resp.read()
                html = content.decode("euc-jp", errors="ignore")
                ids = re.findall(r"/race/(\d{12})/", html)
                result["netkeiba"] = {
                    "status": resp.status,
                    "race_ids_found": len(set(ids)),
                    "sample": list(set(ids))[:3],
                }
    except Exception as e:
        result["netkeiba"] = {"error": str(e)}

    if SUPABASE_ENABLED:
        try:
            from supabase_client import get_client as _gc  # type: ignore

            client = _gc()
            test_race_id = "TEST000000000"
            client.table("races_ultimate").upsert(
                {"race_id": test_race_id, "data": '{"test": true}'}
            ).execute()
            client.table("races_ultimate").delete().eq("race_id", test_race_id).execute()
            result["supabase_write"] = "ok"
        except Exception as e:
            result["supabase_write"] = f"error: {e}"
    else:
        result["supabase_write"] = "disabled"

    ULTIMATE_DB = Path(__file__).parent.parent.parent / "keiba" / "data" / "keiba_ultimate.db"
    result["sqlite_path"] = str(ULTIMATE_DB)
    result["sqlite_dir_exists"] = ULTIMATE_DB.parent.exists()

    return result
