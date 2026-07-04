"""
スクレイピングエンドポイント
POST /api/scrape/start       → 非同期ジョブ開始
GET  /api/scrape/status/{id} → ジョブ進捗確認
POST /api/rescrape_incomplete → 不完全レコード再取得
POST /api/scrape/repair/{id} → 単一レース修復
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app_config import ULTIMATE_DB, logger  # type: ignore
from deps.auth import require_admin  # type: ignore
from models import ScrapeRequest, ScrapeResponse, RescrapeResponse  # type: ignore
import sqlite3

from scraping.jobs import (  # type: ignore
    _scrape_jobs, _JOBS_LOCK, _purge_old_jobs, _run_scrape_job, get_job,
    _new_session, _jitter, request_cancel_job, _JOBS_DB_PATH,
    get_quality_history, get_quality_summary,
)
from scraping.quality_guard import (  # type: ignore
    analyze_quality_trends,
    evaluate_dataset_gate,
    generate_alerts,
    load_alert_rules,
)
from scraping.race import scrape_race_full  # type: ignore
from scraping.storage import _save_race_to_ultimate_db  # type: ignore

router = APIRouter()


@router.get("/api/scrape/login-status")
async def get_login_status():
    """netkeiba プレミアム会員ログイン設定状況を返す（管理者確認用）。
    調教データ・スピード指数の取得に NETKEIBA_EMAIL / NETKEIBA_PASSWORD が必要。
    """
    from scraping.constants import NETKEIBA_EMAIL, NETKEIBA_PASSWORD  # type: ignore
    email_set = bool(NETKEIBA_EMAIL)
    pass_set = bool(NETKEIBA_PASSWORD)
    return {
        "netkeiba_login_enabled": email_set and pass_set,
        "email_configured": email_set,
        "password_configured": pass_set,
        "message": (
            "ログイン設定完了 — 調教データ・スピード指数の取得が有効です。"
            if (email_set and pass_set)
            else "NETKEIBA_EMAIL / NETKEIBA_PASSWORD が未設定です。"
            " .env に設定することで調教データ・スピード指数を自動取得できます。"
        ),
    }


@router.post("/api/scrape/start")
async def scrape_start(request: ScrapeRequest, _: dict = Depends(require_admin)):
    """スクレイピングをバックグラウンドで開始し、即座に job_id を返す（Admin専用）"""
    # 既に running / queued のジョブがあれば拒否（並列実行によるIPブロック防止）
    with _JOBS_LOCK:
        for _jid, _jdata in _scrape_jobs.items():
            if _jdata.get("status") in ("running", "queued"):
                raise HTTPException(
                    status_code=409,
                    detail=f"既存のジョブ {_jid} が {_jdata['status']} 中です。完了またはサーバー再起動後に再試行してください。"
                )
    job_id = str(uuid.uuid4())[:8]
    with _JOBS_LOCK:
        _purge_old_jobs(_scrape_jobs)
        _scrape_jobs[job_id] = {
            "status": "queued",
            "progress": {"done": 0, "total": 0, "message": "開始待ち"},
            "result": None,
            "error": None,
        }
    try:
        import threading
        def _bg() -> None:
            # Windows の ProactorEventLoop(IOCP) が main loop と干渉しないよう
            # スレッド内では SelectorEventLoop を明示的に使用する
            import asyncio
            import sys
            if sys.platform == "win32":
                loop = asyncio.SelectorEventLoop()
            else:
                loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    _run_scrape_job(
                        job_id, request.start_date, request.end_date, request.force_rescrape,
                    )
                )
            finally:
                loop.close()
        threading.Thread(target=_bg, daemon=True, name=f"scrape-{job_id}").start()
        logger.info(
            f"ジョブ {job_id} スケジュール完了:"
            f" start={request.start_date} end={request.end_date}"
            f" force_rescrape={request.force_rescrape}"
        )
    except Exception as e:
        logger.error(f"スレッド起動失敗: {e}")
        _scrape_jobs[job_id]["status"] = "error"
        _scrape_jobs[job_id]["error"] = f"タスク起動失敗: {e}"
    return {"job_id": job_id, "status": _scrape_jobs[job_id]["status"]}


@router.post("/api/scrape/cancel/{job_id}")
async def scrape_cancel(job_id: str, _: dict = Depends(require_admin)):
    """実行中のスクレイピングジョブをキャンセルする（Admin専用）"""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"ジョブ {job_id} が見つかりません")
    if job.get("status") not in ("running", "queued"):
        return {
            "success": False,
            "job_id": job_id,
            "message": f"キャンセル不要（現在のステータス: {job.get('status')}）",
        }
    success = request_cancel_job(job_id)
    if success:
        return {"success": True, "job_id": job_id, "message": "キャンセル要求を送信しました。次の日付処理時に停止します。"}
    return {"success": False, "job_id": job_id, "message": "キャンセル要求の送信に失敗しました"}


@router.get("/api/scrape/status/{job_id}")
async def scrape_status(job_id: str):
    """スクレイピングジョブの進捗・結果を返す（メモリ → SQLite の順で検索）"""
    job = get_job(job_id)
    if not job:
        return {
            "job_id": job_id,
            "status": "not_found",
            "progress": {},
            "result": None,
            "error": f"ジョブ {job_id} が見つかりません（サーバー再起動の可能性）",
        }
    return {
        "job_id": job_id,
        "status": job["status"],
        "progress": job["progress"],
        "result": job.get("result"),
        "error": job.get("error"),
    }


@router.get("/api/scrape/quality/history")
async def scrape_quality_history(limit: int = 30, _: dict = Depends(require_admin)):
    """品質ダッシュボード向け: 直近N件のスクレイプ品質履歴を返す（Admin専用）。"""
    return {
        "limit": max(1, min(int(limit), 200)),
        "items": get_quality_history(limit=limit),
    }


@router.get("/api/scrape/quality/summary")
async def scrape_quality_summary(limit: int = 30, _: dict = Depends(require_admin)):
    """品質ダッシュボード向け: 直近N件の集計サマリーを返す（Admin専用）。"""
    return get_quality_summary(limit=limit)


@router.get("/api/scrape/quality/trends")
async def scrape_quality_trends(limit: int = 30, _: dict = Depends(require_admin)):
    """品質トレンド分析を返す（短期/長期の劣化検知）。"""
    rules = load_alert_rules()
    history = get_quality_history(limit=limit)
    return analyze_quality_trends(history, rules)


@router.get("/api/scrape/quality/alerts")
async def scrape_quality_alerts(limit: int = 30, _: dict = Depends(require_admin)):
    """アラート判定結果を返す（閾値 + ErrorCode急増）。"""
    rules = load_alert_rules()
    history = get_quality_history(limit=limit)
    summary = get_quality_summary(limit=limit)
    alerts = generate_alerts(history, summary, rules)
    return {
        "window": summary.get("window", {"jobs": len(history), "limit": int(limit)}),
        "alerts": alerts,
        "counts": {
            "total": len(alerts),
            "critical": len([a for a in alerts if str(a.get("severity")) == "critical"]),
            "warning": len([a for a in alerts if str(a.get("severity")) == "warning"]),
        },
    }


@router.get("/api/scrape/quality/gate")
async def scrape_quality_gate(limit: int = 30, _: dict = Depends(require_admin)):
    """データセット生成/学習実行のゲート判定を返す。"""
    rules = load_alert_rules()
    history = get_quality_history(limit=limit)
    summary = get_quality_summary(limit=limit)
    gate = evaluate_dataset_gate(history, summary, rules)
    return {
        "window": summary.get("window", {"jobs": len(history), "limit": int(limit)}),
        "gate": gate,
    }


@router.post("/api/rescrape_incomplete")
async def rescrape_incomplete(limit: int = 50) -> RescrapeResponse:
    """
    keiba_ultimate.db 内の不完全レコード（trainer_name=NULL / distance=0 等）を再スクレイプして上書き保存する。
    """
    import sqlite3 as _sq3

    start_time = time.time()

    conn = _sq3.connect(str(ULTIMATE_DB))
    rows = conn.execute("SELECT DISTINCT race_id FROM race_results_ultimate").fetchall()
    all_race_ids = [r[0] for r in rows]

    # races_ultimate から既存の date を取得（date_hint として再利用）
    date_map: dict[str, str] = {}
    # distance=0 または _invalid_distance=True のレースを収集
    invalid_dist_ids: set[str] = set()
    for row in conn.execute("SELECT race_id, data FROM races_ultimate").fetchall():
        try:
            import json as _json
            _d = _json.loads(row[1] or "{}")
            if _d.get("date"):
                date_map[row[0]] = _d["date"]
            if _d.get("_invalid_distance") or _d.get("distance", -1) == 0:
                invalid_dist_ids.add(row[0])
        except Exception:
            pass

    incomplete_ids = []
    for rid in all_race_ids:
        if rid in invalid_dist_ids:
            incomplete_ids.append(rid)
            continue
        sample = conn.execute(
            "SELECT data FROM race_results_ultimate WHERE race_id = ? LIMIT 1", (rid,)
        ).fetchone()
        if sample:
            d = json.loads(sample[0])
            if d.get("trainer_name") is None and d.get("horse_weight") is None:
                incomplete_ids.append(rid)
    conn.close()

    to_process = incomplete_ids[:limit]
    logger.info(f"不完全レース: {len(incomplete_ids)}件中 {len(to_process)}件を再取得")

    updated_races = 0
    updated_horses = 0

    session = _new_session()
    try:
        for race_id in to_process:
            _date_hint = date_map.get(race_id, "")
            await asyncio.sleep(_jitter(3.0))  # レース間インターバル（INV-07）
            race_data = await scrape_race_full(session, race_id, date_hint=_date_hint)
            if race_data and race_data["horses"]:
                _save_race_to_ultimate_db(race_data, ULTIMATE_DB)
                updated_races += 1
                updated_horses += len(race_data["horses"])
                logger.info(f"  更新: {race_id} ({len(race_data['horses'])}頭)")
            else:
                logger.warning(f"  スキップ: {race_id} (取得失敗)")
    finally:
        await session.close()

    elapsed = time.time() - start_time
    remaining = len(incomplete_ids) - updated_races

    return RescrapeResponse(
        success=True,
        message=f"{updated_races}レース/{updated_horses}頭を更新。残り不完全: {remaining}件",
        updated_races=updated_races,
        updated_horses=updated_horses,
        elapsed_time=elapsed,
    )


@router.post("/api/scrape/repair/{race_id}")
async def repair_race(race_id: str, _: dict = Depends(require_admin)) -> dict:
    """
    指定レース ID を再スクレイプして DB を上書き修復する（distance=0 等の修正用）。
    """
    import sqlite3 as _sq3

    if not re.fullmatch(r"\d{12}", race_id):
        raise HTTPException(status_code=400, detail="race_id は12桁の数字である必要があります")

    # 既存の date_hint を取得
    date_hint = ""
    try:
        conn = _sq3.connect(str(ULTIMATE_DB))
        row = conn.execute("SELECT data FROM races_ultimate WHERE race_id = ?", (race_id,)).fetchone()
        conn.close()
        if row:
            date_hint = json.loads(row[0] or "{}").get("date", "")
    except Exception:
        pass

    session = _new_session()
    try:
        race_data = await scrape_race_full(session, race_id, date_hint=date_hint)
    finally:
        await session.close()

    if not race_data or not race_data.get("horses"):
        raise HTTPException(status_code=502, detail=f"レース {race_id} の再スクレイプに失敗しました")

    _save_race_to_ultimate_db(race_data, ULTIMATE_DB)
    ri = race_data["race_info"]
    return {
        "success": True,
        "race_id": race_id,
        "race_name": ri.get("race_name", ""),
        "distance": ri.get("distance", 0),
        "track_type": ri.get("track_type", ""),
        "horses": len(race_data["horses"]),
    }


# ---------------------------------------------------------------------------
# 出馬表プリフェッチ（予測高速化: 70s → ~5s）
# ---------------------------------------------------------------------------

# バックグラウンドプリフェッチのジョブ状態（インメモリ）
_prefetch_status: dict = {"running": False, "last_result": None, "last_error": None}


@router.post("/api/prefetch_today")
async def prefetch_today(
    background_tasks: BackgroundTasks,
    date: str | None = None,
    force: bool = False,
):
    """
    当日（または指定日）の全出馬表＋馬詳細をバックグラウンドでプリフェッチする。

    analyze_race は races_ultimate を先に確認するため、このエンドポイントで
    事前保存しておけばオンデマンドスクレイプ（~70s）が不要になる（~5s に短縮）。

    - date: YYYYMMDD 形式。省略時は今日
    - force: True にすると既キャッシュのレースも再取得する
    """
    from scraping.prefetch import prefetch_shutuba_for_date  # type: ignore
    from datetime import datetime as _dt

    target_date = date if date and re.fullmatch(r"\d{8}", date) else _dt.now().strftime("%Y%m%d")

    if _prefetch_status["running"]:
        return {
            "success": False,
            "message": "プリフェッチが既に実行中です。完了後に再試行してください。",
            "date": target_date,
        }

    async def _run_prefetch() -> None:
        _prefetch_status["running"] = True
        _prefetch_status["last_error"] = None
        try:
            result = await prefetch_shutuba_for_date(target_date, str(ULTIMATE_DB), force=force)
            _prefetch_status["last_result"] = result
            logger.info(
                f"[prefetch_api] {target_date} 完了: "
                f"取得={result['races_fetched']}, スキップ={result['races_already_cached']}, "
                f"失敗={result['races_failed']}"
            )
        except Exception as e:
            _prefetch_status["last_error"] = str(e)
            logger.error(f"[prefetch_api] {target_date} 失敗: {e}")
        finally:
            _prefetch_status["running"] = False

    background_tasks.add_task(_run_prefetch)

    return {
        "success": True,
        "message": f"{target_date} のプリフェッチをバックグラウンドで開始しました",
        "date": target_date,
        "force": force,
    }


@router.get("/api/prefetch_today/status")
async def prefetch_today_status():
    """プリフェッチの実行状態と最終結果を返す。"""
    return {
        "running": _prefetch_status["running"],
        "last_result": _prefetch_status["last_result"],
        "last_error": _prefetch_status["last_error"],
    }


@router.get("/api/scrape/param_logs")
async def get_scrape_param_logs(
    limit: int = 50,
    event: str | None = None,
    _: dict = Depends(require_admin),
):
    """
    スクレイプパラメータ履歴を返す。
    HTTP 400 発生時・ジョブ開始・強制停止のパラメータ記録を閲覧し、
    最適なスリープ設定の調整に利用する。
    """
    try:
        conn = sqlite3.connect(str(_JOBS_DB_PATH))
        query = """
            SELECT id, job_id, timestamp, event, date_processing,
                   pre_sleep_recent, pre_sleep_old, inter_race_sleep,
                   post_sleep_recent, post_sleep_old, stop_on_first_400,
                   pre_sleep_actual, inter_race_sleep_actual, post_sleep_actual,
                   is_recent, consecutive_400_count, races_scraped, days_scraped, note
            FROM scrape_param_log
        """
        params: list = []
        if event:
            query += " WHERE event = ?"
            params.append(event)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        conn.close()
        keys = [
            "id", "job_id", "timestamp", "event", "date_processing",
            "pre_sleep_recent", "pre_sleep_old", "inter_race_sleep",
            "post_sleep_recent", "post_sleep_old", "stop_on_first_400",
            "pre_sleep_actual", "inter_race_sleep_actual", "post_sleep_actual",
            "is_recent", "consecutive_400_count", "races_scraped", "days_scraped", "note",
        ]
        return {"logs": [dict(zip(keys, row)) for row in rows], "count": len(rows)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"パラメータログ取得失敗: {e}")
