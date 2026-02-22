"""
Supabase クライアント - レースデータとモデルの永続化
- レースデータ: Supabase テーブル (races_ultimate, race_results_ultimate)
- モデルファイル: Supabase Storage (models バケット)
"""

import os
import json
import tempfile
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

_client = None


def get_client():
    """Supabase クライアントを取得（シングルトン）"""
    global _client
    if _client is not None:
        return _client
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        logger.warning("Supabase 環境変数未設定: SUPABASE_URL / SUPABASE_SERVICE_KEY")
        return None
    try:
        from supabase import create_client
        _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        logger.info("Supabase クライアント初期化完了")
        return _client
    except Exception as e:
        logger.error(f"Supabase クライアント初期化失敗: {e}")
        return None


# ─────────────────────────────────────────
# レースデータ保存・取得
# ─────────────────────────────────────────

def save_race_to_supabase(race_data: dict) -> bool:
    """スクレイピング結果を Supabase に保存"""
    client = get_client()
    if not client:
        return False

    race_info = race_data["race_info"]
    horses = race_data["horses"]
    race_id = race_info["race_id"]

    try:
        # races_ultimate にアップサート
        client.table("races_ultimate").upsert({
            "race_id": race_id,
            "data": json.dumps(race_info, ensure_ascii=False)
        }).execute()

        # race_results_ultimate: 既存削除→挿入
        client.table("race_results_ultimate").delete().eq("race_id", race_id).execute()
        rows = [
            {"race_id": race_id, "data": json.dumps(h, ensure_ascii=False)}
            for h in horses
        ]
        if rows:
            client.table("race_results_ultimate").insert(rows).execute()

        logger.info(f"Supabase 保存完了: {race_id} ({len(horses)}頭)")
        return True

    except Exception as e:
        logger.error(f"Supabase 保存失敗 {race_id}: {e}")
        return False


def get_data_stats_from_supabase() -> dict:
    """Supabase からデータ統計を取得"""
    client = get_client()
    if not client:
        return {"total_races": 0, "total_horses": 0, "total_models": 0, "db_exists": False}

    try:
        races_res = client.table("races_ultimate").select("race_id", count="exact").execute()
        total_races = races_res.count or 0

        horses_res = client.table("race_results_ultimate").select("race_id", count="exact").execute()
        total_horses = horses_res.count or 0

        models_res = client.table("model_metadata").select("model_id", count="exact").execute()
        total_models = models_res.count or 0

        return {
            "total_races": total_races,
            "total_horses": total_horses,
            "total_models": total_models,
            "db_exists": True,
        }
    except Exception as e:
        logger.error(f"Supabase 統計取得失敗: {e}")
        return {"total_races": 0, "total_horses": 0, "total_models": 0, "db_exists": False}


def sync_supabase_to_sqlite(db_path: Path) -> int:
    """Supabase のレースデータをローカル SQLite に同期（学習用）"""
    import sqlite3

    client = get_client()
    if not client:
        logger.warning("Supabase 未接続: SQLite 同期スキップ")
        return 0

    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()

        # テーブル作成
        cur.execute("""
            CREATE TABLE IF NOT EXISTS races_ultimate (
                race_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS race_results_ultimate (
                race_id TEXT,
                data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Supabase から全レース取得（ページネーション）
        page_size = 1000
        offset = 0
        total_races = 0

        while True:
            res = client.table("races_ultimate").select("race_id,data").range(offset, offset + page_size - 1).execute()
            rows = res.data
            if not rows:
                break
            for row in rows:
                # data が dict (JSONB) の場合は JSON文字列に変換
                data_val = row["data"]
                if isinstance(data_val, dict):
                    import json as _json
                    data_val = _json.dumps(data_val, ensure_ascii=False)
                cur.execute(
                    "INSERT OR REPLACE INTO races_ultimate (race_id, data) VALUES (?, ?)",
                    (row["race_id"], data_val)
                )
            total_races += len(rows)
            offset += page_size
            if len(rows) < page_size:
                break

        # race_results_ultimate
        offset = 0
        cur.execute("DELETE FROM race_results_ultimate")
        while True:
            res = client.table("race_results_ultimate").select("race_id,data").range(offset, offset + page_size - 1).execute()
            rows = res.data
            if not rows:
                break
            for row in rows:
                # data が dict (JSONB) の場合は JSON文字列に変換
                data_val = row["data"]
                if isinstance(data_val, dict):
                    import json as _json
                    data_val = _json.dumps(data_val, ensure_ascii=False)
                cur.execute(
                    "INSERT INTO race_results_ultimate (race_id, data) VALUES (?, ?)",
                    (row["race_id"], data_val)
                )
            offset += page_size
            if len(rows) < page_size:
                break

        conn.commit()
        conn.close()
        logger.info(f"Supabase → SQLite 同期完了: {total_races} レース")
        return total_races

    except Exception as e:
        logger.error(f"Supabase → SQLite 同期失敗: {e}")
        return 0


# ─────────────────────────────────────────
# モデルファイル保存・取得（Supabase Storage）
# ─────────────────────────────────────────

STORAGE_BUCKET = "models"


def upload_model_to_supabase(model_path: Path, model_id: str, metadata: dict) -> bool:
    """モデルファイルを Supabase Storage にアップロード"""
    client = get_client()
    if not client:
        return False

    try:
        storage_path = f"{model_id}.joblib"
        with open(model_path, "rb") as f:
            client.storage.from_(STORAGE_BUCKET).upload(
                storage_path,
                f,
                file_options={"upsert": "true"}
            )

        # モデルメタデータを Supabase テーブルに保存
        client.table("model_metadata").upsert({
            "model_id": model_id,
            "storage_path": storage_path,
            "metadata": json.dumps(metadata, ensure_ascii=False)
        }).execute()

        logger.info(f"モデルを Supabase Storage にアップロード: {model_id}")
        return True

    except Exception as e:
        logger.error(f"モデルアップロード失敗 {model_id}: {e}")
        return False


def download_model_from_supabase(model_id: str, dest_path: Path) -> bool:
    """Supabase Storage からモデルをダウンロード"""
    client = get_client()
    if not client:
        return False

    try:
        storage_path = f"{model_id}.joblib"
        data = client.storage.from_(STORAGE_BUCKET).download(storage_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(data)
        logger.info(f"モデルを Supabase からダウンロード: {model_id}")
        return True

    except Exception as e:
        logger.error(f"モデルダウンロード失敗 {model_id}: {e}")
        return False


def list_models_from_supabase() -> list:
    """Supabase からモデル一覧を取得"""
    client = get_client()
    if not client:
        return []

    try:
        res = client.table("model_metadata").select("*").order("created_at", desc=True).execute()
        models = []
        for row in res.data:
            meta = json.loads(row.get("metadata", "{}"))
            meta["model_id"] = row["model_id"]
            models.append(meta)
        return models

    except Exception as e:
        logger.error(f"モデル一覧取得失敗: {e}")
        return []


def delete_model_from_supabase(model_id: str) -> bool:
    """Supabase からモデルを削除"""
    client = get_client()
    if not client:
        return False

    try:
        storage_path = f"{model_id}.joblib"
        client.storage.from_(STORAGE_BUCKET).remove([storage_path])
        client.table("model_metadata").delete().eq("model_id", model_id).execute()
        logger.info(f"モデルを Supabase から削除: {model_id}")
        return True

    except Exception as e:
        logger.error(f"モデル削除失敗 {model_id}: {e}")
        return False


# ─────────────────────────────────────────
# 血統キャッシュ（horse_pedigree テーブル）
# 同じ馬の血統を毎回スクレイピングしなくて済むようにする
# ─────────────────────────────────────────

def get_pedigree_cache_batch(horse_ids: list) -> dict:
    """複数馬の血統を1回のクエリで取得。{horse_id: {sire,dam,damsire}} を返す。"""
    client = get_client()
    if not client or not horse_ids:
        return {}
    try:
        res = client.table("horse_pedigree").select("horse_id,sire,dam,damsire").in_("horse_id", horse_ids).execute()
        result = {}
        for row in (res.data or []):
            hid = row.get("horse_id")
            if hid and (row.get("sire") or row.get("dam") or row.get("damsire")):
                result[hid] = row
        return result
    except Exception as e:
        logger.debug(f"血統バッチキャッシュ取得失敗: {e}")
        return {}


def get_pedigree_cache(horse_id: str) -> Optional[dict]:
    """馬の血統 (sire/dam/damsire) を Supabase キャッシュから取得。
    キャッシュなし or 全項目空の場合は None を返す。"""
    client = get_client()
    if not client:
        return None
    try:
        res = client.table("horse_pedigree").select("sire,dam,damsire").eq("horse_id", horse_id).execute()
        if res.data:
            row = res.data[0]
            # 少なくとも1項目が入っていればキャッシュヒットとみなす
            if row.get("sire") or row.get("dam") or row.get("damsire"):
                return row
        return None
    except Exception as e:
        logger.debug(f"血統キャッシュ取得失敗 {horse_id}: {e}")
        return None


def save_pedigree_cache(horse_id: str, sire: str, dam: str, damsire: str) -> bool:
    """馬の血統を Supabase キャッシュに保存（upsert）。
    取得失敗（全空）でも保存することで二重リクエストを防ぐ。"""
    client = get_client()
    if not client:
        return False
    try:
        client.table("horse_pedigree").upsert({
            "horse_id": horse_id,
            "sire": sire or None,
            "dam": dam or None,
            "damsire": damsire or None,
        }).execute()
        logger.debug(f"血統キャッシュ保存: {horse_id} sire={sire}")
        return True
    except Exception as e:
        logger.debug(f"血統キャッシュ保存失敗 {horse_id}: {e}")
        return False
