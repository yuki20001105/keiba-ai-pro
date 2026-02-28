"""
データエクスポートエンドポイント
GET    /api/export-data
GET    /api/export-db
DELETE /api/data/all
GET    /api/debug/race-ids

OOM対策:
- N+1クエリ廃止 → バッチ IN クエリで結果を一括取得 (BATCH_RESULTS = 100件ずつ)
- export_db: SQLite ファイルを tempfile に書き出し後、チャンク読み込みでストリーミング送信
  ファイル全体を bytes に読み込まないことで二重メモリを回避
- export_data: limit パラメータでレース上限を設定 (デフォルト 5000)
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sqlite3
import tempfile
from typing import Iterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app_config import SUPABASE_ENABLED, get_supabase_client  # type: ignore

router = APIRouter()

# バッチ IN クエリの最大件数: Supabase の URL 長制限に収まる安全な値
_BATCH_RESULTS = 100
# ストリーミング送信のチャンクサイズ (64 KB)
_STREAM_CHUNK = 65536


def _require_supabase():
    """Supabase クライアントを取得。未設定なら 503 を raise。"""
    if not SUPABASE_ENABLED:
        raise HTTPException(status_code=503, detail="Supabase 未接続")
    client = get_supabase_client()
    if not client:
        raise HTTPException(status_code=503, detail="Supabase クライアント未初期化")
    return client


def _fetch_races_paged(client, prefix_filter: str, limit: int = 0) -> list:
    """races_ultimate をページネーションで全件取得。
    limit > 0 のとき最大 limit 件で打ち切り。"""
    all_races: list = []
    offset = 0
    while True:
        page_size = 1000
        if limit > 0:
            page_size = min(1000, limit - len(all_races))
            if page_size <= 0:
                break
        q = (client.table("races_ultimate")
             .select("race_id,data")
             .range(offset, offset + page_size - 1))
        if prefix_filter:
            q = q.like("race_id", f"{prefix_filter}%")
        resp = q.execute()
        chunk = resp.data or []
        all_races.extend(chunk)
        if len(chunk) < page_size:
            break
        if limit > 0 and len(all_races) >= limit:
            break
        offset += page_size
    return all_races


def _fetch_results_batch(client, race_ids: list) -> list:
    """race_results_ultimate を IN クエリでバッチ取得。N+1 を回避。"""
    all_results: list = []
    for i in range(0, len(race_ids), _BATCH_RESULTS):
        chunk_ids = race_ids[i: i + _BATCH_RESULTS]
        resp = (client.table("race_results_ultimate")
                .select("race_id,data")
                .in_("race_id", chunk_ids)
                .execute())
        all_results.extend(resp.data or [])
    return all_results


def _iter_file_chunks(path: str, chunk_size: int = _STREAM_CHUNK) -> Iterator[bytes]:
    """ファイルを chunk_size ずつ yield してストリーム送信し、完了後にファイルを削除。"""
    try:
        with open(path, "rb") as f:
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                yield data
    finally:
        with contextlib.suppress(Exception):
            os.unlink(path)


@router.get("/api/export-data")
async def export_data(date: str = "", limit: int = 5000):
    """Supabase から JSON でデータをエクスポート。

    Args:
        date:  race_id プレフィックスフィルタ (例: "202501" → 2025年1月)
        limit: 取得する最大レース件数 (デフォルト 5000、0 = 無制限)
    """
    client = _require_supabase()
    try:
        def _sync_fetch():
            all_races = _fetch_races_paged(client, date, limit=limit)
            race_ids = [r["race_id"] for r in all_races]
            # バッチ IN クエリ: N+1 ではなく 100 件ずつまとめて取得
            all_results = _fetch_results_batch(client, race_ids)
            return all_races, all_results

        all_races, all_results = await asyncio.to_thread(_sync_fetch)
        return {
            "races_count": len(all_races),
            "results_count": len(all_results),
            "races": all_races,
            "results": all_results,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"データ取得エラー: {str(e)}")


@router.get("/api/export-db")
async def export_db(date: str = "", limit: int = 5000):
    """指定日プレフィックスの race_id を SQLite に書き出してダウンロード。

    OOM 回避:
    - バッチ IN クエリで results を取得 (N+1 廃止)
    - SQLite を tempfile に書き出し後、64 KB チャンクでストリーミング送信
      (bytes 全体を Python ヒープに読み込まない)
    """
    client = _require_supabase()
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(tmp_fd)

    try:
        def _sync_build_db():
            all_races = _fetch_races_paged(client, date, limit=limit)
            race_ids = [r["race_id"] for r in all_races]
            # バッチ IN クエリで一括取得 (N+1 廃止)
            all_results = _fetch_results_batch(client, race_ids)

            conn = sqlite3.connect(tmp_path)
            try:
                cur = conn.cursor()
                cur.execute("CREATE TABLE races_ultimate (race_id TEXT PRIMARY KEY, data TEXT NOT NULL)")
                cur.execute("CREATE TABLE race_results_ultimate (race_id TEXT, data TEXT NOT NULL)")
                cur.execute("CREATE INDEX idx_results_race_id ON race_results_ultimate (race_id)")
                for r in all_races:
                    dv = r["data"] if isinstance(r["data"], str) else json.dumps(r["data"], ensure_ascii=False)
                    cur.execute("INSERT OR REPLACE INTO races_ultimate VALUES (?,?)", (r["race_id"], dv))
                for r2 in all_results:
                    dv2 = r2["data"] if isinstance(r2["data"], str) else json.dumps(r2["data"], ensure_ascii=False)
                    cur.execute("INSERT INTO race_results_ultimate VALUES (?,?)", (r2["race_id"], dv2))
                conn.commit()
            finally:
                conn.close()
            return len(all_races)

        race_count = await asyncio.to_thread(_sync_build_db)

    except HTTPException:
        with contextlib.suppress(Exception):
            os.unlink(tmp_path)
        raise
    except Exception as e:
        with contextlib.suppress(Exception):
            os.unlink(tmp_path)
        raise HTTPException(status_code=500, detail=f"DB構築エラー: {str(e)}")

    fname = f"keiba_ultimate_{date or 'all'}.db"
    # ファイルをチャンク送信。_iter_file_chunks は完了後に tempfile を自動削除する
    return StreamingResponse(
        _iter_file_chunks(tmp_path),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "X-Race-Count": str(race_count),
        },
    )


@router.delete("/api/data/all")
async def delete_all_data(date_prefix: str = ""):
    """Supabase の races_ultimate / race_results_ultimate を全削除（検証用）"""
    client = _require_supabase()
    try:
        def _sync_delete():
            deleted_races = 0
            if date_prefix:
                res = client.table("races_ultimate").select("race_id").like("race_id", f"{date_prefix}%").execute()
                for row in (res.data or []):
                    client.table("race_results_ultimate").delete().eq("race_id", row["race_id"]).execute()
                    client.table("races_ultimate").delete().eq("race_id", row["race_id"]).execute()
                    deleted_races += 1
            else:
                offset = 0
                while True:
                    res = client.table("races_ultimate").select("race_id").range(offset, offset + 999).execute()
                    rows = res.data or []
                    if not rows:
                        break
                    for row in rows:
                        client.table("race_results_ultimate").delete().eq("race_id", row["race_id"]).execute()
                        client.table("races_ultimate").delete().eq("race_id", row["race_id"]).execute()
                        deleted_races += 1
                    offset += len(rows)
                    if len(rows) < 1000:
                        break
            return deleted_races

        deleted_races = await asyncio.to_thread(_sync_delete)
        return {"success": True, "deleted_races": deleted_races, "date_prefix": date_prefix or "all"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"削除エラー: {str(e)}")


@router.get("/api/debug/race-ids")
async def debug_race_ids(limit: int = 10):
    """race_id のサンプルを返す（デバッグ用）"""
    client = _require_supabase()
    try:
        res = await asyncio.to_thread(
            lambda: client.table("races_ultimate").select("race_id").limit(limit).execute()
        )
        ids = [r["race_id"] for r in (res.data or [])]
        return {"race_ids": ids, "count": len(ids)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
