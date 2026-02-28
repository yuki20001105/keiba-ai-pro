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

# ── 変換ヘルパー ───────────────────────────────────────

import re as _re


def _to_int(v) -> Optional[int]:
    if v is None or v == "" or v == "---":
        return None
    try:
        return int(float(str(v)))
    except (ValueError, TypeError):
        return None


def _to_float(v) -> Optional[float]:
    if v is None or v == "" or v == "---":
        return None
    try:
        return float(str(v))
    except (ValueError, TypeError):
        return None


def _id_from_url(url: Optional[str]) -> Optional[str]:
    """正規表現で URL 末尾の ID 文字列を抽出"""
    if not url:
        return None
    m = _re.search(r"/([^/]+)/?$", url)
    return m.group(1) if m else None


def save_race_to_supabase(race_data: dict) -> bool:
    """スクレイピング結果を Supabase に保存

    保存先（両方根㑻ませる 渡移期偵にjsonまㄅ1テーブル）:
      1. 正規化テーブル（races / entries / results / horse_details / past_performances / payouts）
      2. blob テーブル（races_ultimate / race_results_ultimate）← sync_supabase_to_sqlite の互换維持用
    """
    client = get_client()
    if not client:
        return False

    race_info = race_data["race_info"]
    horses = race_data["horses"]
    race_id = race_info["race_id"]

    try:
        # ── 1. races ──────────────────────────────────────────
        race_row = {
            "race_id": race_id,
            "race_name": race_info.get("race_name"),
            "post_time": race_info.get("post_time"),
            "track_type": race_info.get("track_type"),
            "distance": _to_int(race_info.get("distance")),
            "course_direction": race_info.get("course_direction"),
            "weather": race_info.get("weather"),
            "field_condition": race_info.get("field_condition"),
            "kai": _to_int(race_info.get("kai")),
            "venue": race_info.get("venue"),
            "day": _to_int(race_info.get("day")),
            "race_class": race_info.get("race_class"),
            "horse_count": _to_int(race_info.get("num_horses")),
            "market_entropy": _to_float(race_info.get("market_entropy")),
            "top3_probability": _to_float(race_info.get("top3_probability")),
            "kaisai_date": race_info.get("date"),
            "source": "scraping",
        }
        client.table("races").upsert(race_row).execute()

        # ── 2. entries / results / horse_details / past_performances ──
        for h in horses:
            horse_id = _id_from_url(h.get("horse_url")) or h.get("horse_name")
            jockey_id = _id_from_url(h.get("jockey_url")) or h.get("jockey_name")
            trainer_id = _id_from_url(h.get("trainer_url")) or h.get("trainer_name")

            # entries
            entry_row = {
                "race_id": race_id,
                "horse_id": horse_id,
                "horse_name": h.get("horse_name"),
                "horse_no": _to_int(h.get("horse_number")),
                "bracket": _to_int(h.get("bracket_number")),
                "sex": h.get("sex"),
                "age": _to_int(h.get("age")),
                "sex_age": h.get("sex_age"),
                "handicap": _to_float(h.get("jockey_weight")),
                "jockey_id": jockey_id,
                "jockey_name": h.get("jockey_name"),
                "trainer_id": trainer_id,
                "trainer_name": h.get("trainer_name"),
                "weight_kg": _to_int(h.get("weight_kg")),
                "weight_change": _to_int(h.get("weight_change")),
                "odds": _to_float(h.get("odds") or h.get("win_odds")),
                "popularity": _to_int(h.get("popularity")),
            }
            client.table("entries").upsert(entry_row).execute()

            # results (着順があるもののみ)
            finish = _to_int(h.get("finish_position"))
            if finish is not None:
                result_row = {
                    "race_id": race_id,
                    "horse_id": horse_id,
                    "finish": finish,
                    "horse_number": _to_int(h.get("horse_number")),
                    "bracket_number": _to_int(h.get("bracket_number")),
                    "time": h.get("finish_time"),
                    "margin": h.get("margin"),
                    "last3f": _to_float(h.get("last_3f")),
                    "pass_order": h.get("corner_positions"),
                    "odds": _to_float(h.get("odds") or h.get("win_odds")),
                    "popularity": _to_int(h.get("popularity")),
                }
                client.table("results").upsert(result_row).execute()

            # horse_details (マスタテーブル)
            if horse_id:
                horse_row = {
                    "horse_id": horse_id,
                    "horse_name": h.get("horse_name"),
                    "total_runs": _to_int(h.get("horse_total_runs")),
                    "total_wins": _to_int(h.get("horse_total_wins")),
                    "total_prize_money": _to_float(h.get("horse_total_prize_money")),
                    "sire": h.get("sire"),
                    "dam": h.get("dam"),
                    "damsire": h.get("damsire"),
                }
                client.table("horse_details").upsert(horse_row).execute()

            # past_performances
            if h.get("prev_race_date") or h.get("prev_race_distance"):
                pp_row = {
                    "race_id": race_id,
                    "horse_id": horse_id,
                    "prev_race_date": h.get("prev_race_date"),
                    "prev_race_venue": h.get("prev_race_venue"),
                    "prev_race_distance": _to_int(h.get("prev_race_distance")),
                    "prev_race_finish": _to_int(h.get("prev_race_finish")),
                    "distance_change": _to_int(h.get("distance_change")),
                }
                client.table("past_performances").upsert(pp_row).execute()

        # ── 3. payouts ──────────────────────────────────────────
        for p in race_data.get("payouts", []):
            payout_row = {
                "race_id": race_id,
                "bet_type": p.get("bet_type"),
                "combination": p.get("combination"),
                "payout": _to_int(p.get("payout")),
                "popularity": _to_int(p.get("popularity")),
            }
            client.table("payouts").insert(payout_row).execute()

        # ── 4. blob テーブル（sync_supabase_to_sqlite の互换維持）─────────
        try:
            client.table("races_ultimate").upsert({
                "race_id": race_id,
                "data": json.dumps(race_info, ensure_ascii=False),
            }).execute()
            client.table("race_results_ultimate").delete().eq("race_id", race_id).execute()
            blob_rows = [
                {"race_id": race_id, "data": json.dumps(h, ensure_ascii=False)}
                for h in horses
            ]
            if blob_rows:
                client.table("race_results_ultimate").insert(blob_rows).execute()
        except Exception as blob_err:
            logger.warning(f"blob テーブル書き込み失敗（非致命的）: {blob_err}")

        logger.info(f"Supabase 正規化保存完了: {race_id} ({len(horses)}頭)")
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
    """モデルファイルを Supabase Storage にアップロード（{user_id}/{model_id}.joblib）"""
    client = get_client()
    if not client:
        return False

    try:
        user_id = metadata.get("user_id") or "shared"
        storage_path = f"{user_id}/{model_id}.joblib"
        with open(model_path, "rb") as f:
            client.storage.from_(STORAGE_BUCKET).upload(
                storage_path,
                f,
                file_options={"upsert": "true"}
            )

        # モデルメタデータを Supabase テーブルに保存
        client.table("model_metadata").upsert({
            "model_id": model_id,
            "user_id": user_id,
            "storage_path": storage_path,
            "metadata": json.dumps(metadata, ensure_ascii=False)
        }).execute()

        logger.info(f"モデルを Supabase Storage にアップロード: {storage_path}")
        return True

    except Exception as e:
        logger.error(f"モデルアップロード失敗 {model_id}: {e}")
        return False


def download_model_from_supabase(model_id: str, dest_path: Path) -> bool:
    """Supabase Storage からモデルをダウンロード（metadataのstorage_pathを優先使用）"""
    client = get_client()
    if not client:
        return False

    try:
        # model_metadata から storage_path を取得（user_id prefix 対応）
        storage_path = f"{model_id}.joblib"  # fallback
        try:
            row = client.table("model_metadata").select("storage_path").eq("model_id", model_id).single().execute()
            if row.data and row.data.get("storage_path"):
                storage_path = row.data["storage_path"]
        except Exception:
            pass

        data = client.storage.from_(STORAGE_BUCKET).download(storage_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(data)
        logger.info(f"モデルを Supabase からダウンロード: {storage_path}")
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
        # metadata から storage_path を取得（user_id prefix 対応）
        storage_path = f"{model_id}.joblib"  # fallback
        try:
            row = client.table("model_metadata").select("storage_path").eq("model_id", model_id).single().execute()
            if row.data and row.data.get("storage_path"):
                storage_path = row.data["storage_path"]
        except Exception:
            pass
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
