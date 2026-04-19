"""
予測・レース分析エンドポイント
POST /api/predict
POST /api/analyze_race
"""
from __future__ import annotations

import json
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException, Request

from app_config import (  # type: ignore
    SUPABASE_DATA_ENABLED,
    CONFIG_PATH,
    MODELS_DIR,
    ULTIMATE_DB,
    get_supabase_client,
    get_latest_model,
    load_model_bundle,
    _ensure_model_local,
    logger,
)
from deps.pred_limit import check_and_consume_pred_count  # type: ignore
from models import (  # type: ignore
    PredictRequest,
    PredictResponse,
    AnalyzeRaceRequest,
    AnalyzeRaceResponse,
    BatchAnalyzeRequest,
)
from keiba_ai.constants import FUTURE_FIELDS  # type: ignore

import asyncio
import time as _time

router = APIRouter()


def _save_prediction_log(
    race_id: str,
    race_info: dict,
    predictions: list,
    model_id: str,
    db_path: str,
) -> None:
    """予測結果を prediction_log テーブルに保存（同期・スレッド呼び出し専用）"""
    import sqlite3 as _sql
    conn = _sql.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prediction_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                race_id      TEXT    NOT NULL,
                race_name    TEXT,
                venue        TEXT,
                race_date    TEXT,
                horse_id     TEXT,
                horse_name   TEXT,
                horse_number INTEGER,
                predicted_rank INTEGER,
                win_probability REAL,
                p_raw        REAL,
                odds         REAL,
                popularity   INTEGER,
                model_id     TEXT,
                predicted_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_plog_race ON prediction_log(race_id)",
        )
        # 同一 race_id + model_id の既存ログを上書き
        conn.execute(
            "DELETE FROM prediction_log WHERE race_id = ? AND model_id = ?",
            (race_id, model_id),
        )
        race_name = race_info.get("race_name", "")
        venue     = race_info.get("venue", "")
        race_date = race_info.get("date", "")
        for p in predictions:
            horse_id = p.get("horse_id", "")
            # horse_id が空の場合は horse_name から補完しない（JOIN は失敗するが保存は続ける）
            conn.execute(
                """INSERT INTO prediction_log
                   (race_id, race_name, venue, race_date,
                    horse_id, horse_name, horse_number,
                    predicted_rank, win_probability, p_raw, odds, popularity, model_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    race_id, race_name, venue, race_date,
                    horse_id,
                    p.get("horse_name", ""),
                    p.get("horse_number"),
                    p.get("predicted_rank"),
                    p.get("win_probability"),
                    p.get("p_raw"),
                    p.get("odds"),
                    p.get("popularity"),
                    model_id,
                ),
            )
        conn.commit()
    finally:
        conn.close()


# ── インメモリ予測キャッシュ（race_id → (timestamp, response_dict)）
_ANALYZE_CACHE: dict[str, tuple[float, dict]] = {}
_ANALYZE_CACHE_TTL = 300  # 5分
_ANALYZE_CACHE_MAX = 200  # 最大エントリ数（超過時に最古から削除）
# DB 全履歴キャッシュ（add_derived_features の full_history_df 用）
# analyze_race / predict ごとに全 DB を再ロードするコストを削減（TTL=10分）
_HISTORY_CACHE: "tuple[float, 'pd.DataFrame'] | None" = None
_HISTORY_CACHE_TTL = 600  # 10分


def _load_hist_cached() -> "pd.DataFrame":
    """DB 全履歴 DataFrame をキャッシュ付きで返す（TTL=10分）

    race_results_ultimate の全データを読み込んで DataFrame として返す。
    10 分以内に再呼び出された場合はキャッシュを利用する。
    """
    global _HISTORY_CACHE
    try:
        from keiba_ai.db_ultimate_loader import load_ultimate_training_frame as _ltf  # type: ignore
        if _HISTORY_CACHE is not None and (_time.time() - _HISTORY_CACHE[0]) < _HISTORY_CACHE_TTL:
            return _HISTORY_CACHE[1]
        _df = _ltf(ULTIMATE_DB)
        _HISTORY_CACHE = (_time.time(), _df)
        return _df
    except Exception:
        import pandas as _pd
        return _pd.DataFrame()
# レース後確定フィールド（keiba_ai.constants.FUTURE_FIELDS を参照）
POST_RACE_FIELDS = FUTURE_FIELDS

# ── モジュールレベルヘルパー ──────────────────────────────────────────

def _resolve_model_path(model_id: "str | None") -> "Path":
    """model_id 指定 → ローカル確認 → Supabase フォールバックの順でモデルパスを解決する。"""
    from app_config import list_models_from_supabase  # type: ignore
    if model_id:
        p = _ensure_model_local(model_id)
        if not p:
            raise HTTPException(status_code=404, detail=f"モデル {model_id} が見つかりません")
        return p
    p = get_latest_model()
    if p is None and SUPABASE_DATA_ENABLED and get_supabase_client():
        sb_models = list_models_from_supabase()
        if sb_models:
            p = _ensure_model_local(sb_models[0]["model_id"])
    if p is None:
        raise HTTPException(status_code=404, detail="学習済みモデルが見つかりません")
    return p


def _drop_non_features(df: "pd.DataFrame") -> "pd.DataFrame":
    """未来情報・識別子・ object型列を推論入力から除外する（INV-01）。"""
    _exclude = (
        set(POST_RACE_FIELDS)
        | {"race_id", "horse_id", "jockey_id", "trainer_id", "owner_id", "finish_position",
           "win", "place"}
    )
    df = df.drop([c for c in _exclude if c in df.columns], axis=1)
    obj_cols = df.select_dtypes(include=["object"]).columns.tolist()
    if obj_cols:
        df = df.drop(columns=obj_cols)
    return df


def _predict_sub_model(
    model_path: "Path",
    df: "pd.DataFrame",
    full_hist: "pd.DataFrame | None" = None,
) -> "tuple[np.ndarray, np.ndarray] | None":
    """サブモデル（place3 など）で確率を計算して返す。ModelPredictor を経由して推論。

    Returns:
        (raw_scores, proba_norm) または None（失敗時）
    """
    try:
        _bundle = load_model_bundle(model_path)
        _predictor = ModelPredictor(_bundle, model_path)
        _X = _predictor.build_features(df, full_hist=full_hist)
        return _predictor.predict_scores(_X)
    except HTTPException:
        raise
    except Exception as _e:
        logger.warning(f"[sub_model] {model_path.name} 予測失敗: {_e}")
        return None


def _compute_ensemble(
    win_probs: "np.ndarray",
    place3_probs: "np.ndarray | None",
    bundle_target: str,
) -> "np.ndarray":
    """win / place3 / speed の加重平均アンサンブルスコアを返す（Σ=1 に正規化済み）。

    重みポリシー:
      - win主モデル    : win=60%, place3=40%
      - speed主モデル  : speed=50%, place3=50%
      - place3なし    : win / speed をそのまま p_norm で返す
    """
    import numpy as _np_ens
    if place3_probs is None or len(place3_probs) != len(win_probs):
        _s = win_probs.sum()
        return (win_probs / _s) if _s > 0 else win_probs
    if bundle_target == "speed_deviation":
        _ens = 0.50 * win_probs + 0.50 * place3_probs
    else:
        _ens = 0.60 * win_probs + 0.40 * place3_probs
    _s = _ens.sum()
    return (_ens / _s) if _s > 0 else win_probs


class ModelPredictor:
    """モデルバンドルを基点に推論パイプラインを管理するクラス。

    bundle["pipeline_config"] と bundle["feature_columns"] に基づいて
    各モデル固有の推論フロー（特徴量エンジニアリング → optimizer → 予測 → スコア変換）を実行する。

    ポリシー:
    - 特徴量不足は NaN 補間せず HTTP 500 を返す（再学習を促す）
    - optimizer.transform 失敗は HTTP 500 を返す（フォールバックなし）
    - スコア変換はターゲット種別（分類/回帰/ランカー）ごとに自動決定
    """

    def __init__(self, bundle: dict, model_path: "str | Path | None" = None):
        self.bundle = bundle
        self.model_name: str = Path(model_path).name if model_path else bundle.get("created_at", "unknown")
        self.target: str = bundle.get("target", "win")
        self.model = bundle["model"]
        self.optimizer = bundle.get("optimizer")
        self.calibrator = bundle.get("calibrator")
        self.feature_columns: list[str] = bundle.get("feature_columns", [])
        self.pipeline_config: dict = bundle.get("pipeline_config", {})
        self.is_ranker: bool = bool(bundle.get("_is_ranker", False))

    # ── 特徴量構築 ──────────────────────────────────────────────────────────

    def build_features(
        self,
        df: "pd.DataFrame",
        full_hist: "pd.DataFrame | None" = None,
    ) -> "pd.DataFrame":
        """推論用特徴量 DataFrame を構築し、feature_columns 順に整列して返す。

        各ステップの失敗時の挙動:
        - add_derived_features 失敗 → 警告のみ（部分失敗は次の strict check で検出）
        - optimizer.transform 失敗 → HTTP 500（モデルと特徴量セットの不一致。再学習必要）
        - feature_columns 不足 → HTTP 500（不足列名を列挙して原因を明示）
        NaN補間は行わない。
        """
        from keiba_ai.feature_engineering import add_derived_features as _adf  # type: ignore

        # Step 1: 派生特徴量エンジニアリング（部分失敗は許容 → strict check で捕捉）
        try:
            df = _adf(df, full_history_df=full_hist if full_hist is not None else df)
        except Exception as _e:
            logger.warning(f"[ModelPredictor:{self.target}] add_derived_features 部分失敗: {_e}")
        df = df.loc[:, ~df.columns.duplicated()]

        # Step 2: モデル付属 optimizer による前処理（失敗時は再学習が必要）
        if self.optimizer is not None:
            try:
                df = self.optimizer.transform(df)
            except Exception as _e:
                _cfg = self.pipeline_config
                _fe_hash_trained = _cfg.get("feature_engineering_hash", "不明")
                try:
                    import hashlib as _hl, inspect as _ins
                    import keiba_ai.feature_engineering as _fe_mod  # type: ignore
                    _fe_hash_now = _hl.md5(_ins.getsource(_fe_mod).encode()).hexdigest()[:12]
                except Exception:
                    _fe_hash_now = "不明"
                _hash_note = (
                    f"feature_engineering ハッシュ: 学習時={_fe_hash_trained} / 現在={_fe_hash_now}"
                    + (" ← 変更あり" if _fe_hash_trained != _fe_hash_now and _fe_hash_trained != "不明" else "")
                )
                raise HTTPException(
                    status_code=500,
                    detail=(
                        f"モデル '{self.model_name}' の optimizer が現在の特徴量セットと互換性がありません。\n"
                        f"特徴量変更後はモデルを再学習してください。\n"
                        f"{_hash_note}\n"
                        f"エラー ({type(_e).__name__}): {_e}"
                    ),
                )

        # Step 3: 未来情報・識別子列を除外
        X = _drop_non_features(df)

        # Step 4: 特徴量チェック（50%超の欠損は再学習を促す HTTP 500、それ以下は NaN 補完で続行）
        if self.feature_columns:
            from app_config import assert_feature_columns, verify_feature_columns  # type: ignore
            missing = [c for c in self.feature_columns if c not in X.columns]
            if missing:
                _missing_rate = len(missing) / len(self.feature_columns)
                if _missing_rate > 0.50:
                    raise HTTPException(
                        status_code=500,
                        detail=(
                            f"モデル '{self.model_name}' に必要な特徴量が計算されていません。\n"
                            f"不足特徴量 ({len(missing)}/{len(self.feature_columns)} 件): {missing[:30]}\n"
                            f"原因: feature_engineering.py またはスクレイプフィールドの変更後に"
                            f"モデルの再学習が必要です。"
                        ),
                    )
                logger.warning(
                    f"[ModelPredictor:{self.target}] {len(missing)}/{len(self.feature_columns)} 特徴量が未計算"
                    f" → NaN 補完で続行: {missing[:15]}"
                )
            return verify_feature_columns(X, self.bundle)
        return X

    # ── 推論・スコア変換 ────────────────────────────────────────────────────

    def predict_scores(
        self, X: "pd.DataFrame"
    ) -> "tuple[np.ndarray, np.ndarray]":
        """モデルの raw スコアとレース内正規化確率を返す。

        ターゲット種別に応じてスコア変換を自動選択:
        - speed_deviation / rank → softmax（回帰・ランカー）
        - win / place3 → キャリブレーション + clip [0,1]（分類）

        Returns:
            (raw_scores, proba_norm) — 両方 shape (n_horses,)
        """
        import numpy as _np_p
        raw = _np_p.array(self.model.predict(X), dtype=float)

        if self.target in ("speed_deviation", "rank"):
            finite = _np_p.isfinite(raw)
            _safe = _np_p.where(
                finite, raw,
                raw[finite].min() - 1.0 if finite.any() else -5.0,
            )
            _exp = _np_p.exp(_safe - _safe.max())
            proba = _exp / _exp.sum()
        else:
            proba = _np_p.clip(raw.copy(), 0.0, 1.0)
            if self.calibrator is not None:
                try:
                    proba = self.calibrator.predict(proba)
                except Exception as _cal_err:
                    logger.warning(
                        f"[ModelPredictor:{self.target}] キャリブレーション失敗: {_cal_err}"
                    )

        s = proba.sum()
        proba_norm = (proba / s) if s > 0 else proba
        return raw, proba_norm


@router.post("/api/predict", response_model=PredictResponse)
async def predict(request: PredictRequest, http_req: Request):
    """学習済みモデルを使用して予測を実行（free=10回/月, premium=無制限）"""
    await check_and_consume_pred_count(http_req)
    try:
        # モデルロード → ModelPredictor 初期化（モデル固有パイプラインを決定）
        model_path = _resolve_model_path(request.model_id)
        bundle = load_model_bundle(model_path)
        predictor = ModelPredictor(bundle, model_path)

        # レース後フィールドを除去して DataFrame 化
        cleaned_horses = [{k: v for k, v in h.items() if k not in POST_RACE_FIELDS} for h in request.horses]
        df = pd.DataFrame(cleaned_horses)

        # [S: Quality Gate] 不正レースを除外
        try:
            from keiba_ai.quality_gate import filter_valid_races as _fvr  # type: ignore
            if "race_id" not in df.columns:
                df["race_id"] = "202500000000"
            _n_before = len(df)
            df = _fvr(df, verbose=False)
            if len(df) == 0:
                raise HTTPException(
                    status_code=400,
                    detail="入力データに不正なレースが含まれています（distance=0, odds欠損など）。再スクレイプしてください。",
                )
            if len(df) < _n_before:
                logger.warning(f"[Quality Gate] {_n_before - len(df)} 件の不正エントリを除外")
        except HTTPException:
            raise
        except Exception as _qe:
            logger.warning(f"[Quality Gate] スキップ: {_qe}")

        if "race_id" not in df.columns:
            df["race_id"] = "202500000000"

        # [INV-01] 全履歴キャッシュ（expanding window 用、対象レースを除外）
        # NOTE: _load_hist_cached / build_features は CPU 集中型の同期処理のため
        #       asyncio.to_thread でスレッドプールに移し、イベントループをブロックしない
        try:
            _hist_df = await asyncio.to_thread(_load_hist_cached)
            if 'race_id' in _hist_df.columns:
                _hist_df = _hist_df[~_hist_df['race_id'].isin(set(df['race_id'].dropna()))]
            _full_hist = pd.concat([_hist_df, df], ignore_index=True)
        except Exception:
            _full_hist = df

        # ModelPredictor で特徴量構築（不足時は HTTP 500）
        X = await asyncio.to_thread(predictor.build_features, df, _full_hist)

        # ModelPredictor でスコア計算（ターゲット種別に応じて自動選択）
        p_raw, p_norm = predictor.predict_scores(X)

        predictions = []
        for i, (_, row) in enumerate(df.iterrows()):
            horse_num = int(row.get("horse_number", row.get("horse_no", i + 1)))
            _hn = row.get("horse_name") or row.get("horse_id") or f"Horse {horse_num}"
            predictions.append({
                "index": i,
                "horse_number": horse_num,
                "horse_name": str(_hn),
                "probability": float(p_norm[i]),
                "p_raw": float(p_raw[i]),
                "p_norm": float(p_norm[i]),
                "odds": float(row.get("odds", row.get("entry_odds", 0.0))),
            })

        predictions.sort(key=lambda x: x["p_raw"], reverse=True)
        for rank, pred in enumerate(predictions, start=1):
            pred["predicted_rank"] = rank

        model_id = bundle.get("model_id", bundle.get("created_at", "unknown"))
        return PredictResponse(
            success=True,
            predictions=predictions,
            model_id=model_id,
            message=f"{len(predictions)}頭の予測が完了しました",
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"予測中にエラーが発生: {str(e)}")


@router.post("/api/analyze_race", response_model=AnalyzeRaceResponse)
async def analyze_race(request: AnalyzeRaceRequest):
    """レース分析と購入推奨エンドポイント"""
    # ── キャッシュチェック（TTL=5分、bankroll/risk_mode が同一の場合のみ）
    _cache_key = f"{request.race_id}:{request.model_id}:{request.bankroll}:{request.risk_mode}"
    _now = _time.time()
    _cached = _ANALYZE_CACHE.get(_cache_key)
    if _cached and (_now - _cached[0]) < _ANALYZE_CACHE_TTL:
        logger.info(f"[cache hit] analyze_race {request.race_id} (age={(int(_now - _cached[0]))}s)")
        return AnalyzeRaceResponse(**_cached[1])

    try:
        from app_config import list_models_from_supabase  # type: ignore
        from betting.strategy import BettingRecommender  # type: ignore

        # Phase 0: 常に ultimate DB を使用（87特徴量モード固定）
        db_path = ULTIMATE_DB

        # モデルロード
        if request.model_id:
            model_path = _ensure_model_local(request.model_id)
            if not model_path:
                raise HTTPException(status_code=404, detail=f"モデル {request.model_id} が見つかりません")
        else:
            model_path = get_latest_model()
            if model_path is None:
                if SUPABASE_DATA_ENABLED and get_supabase_client():
                    sb_models = list_models_from_supabase()
                    if sb_models:
                        model_path = _ensure_model_local(sb_models[0]["model_id"])
                if model_path is None:
                    raise HTTPException(status_code=404, detail="訓練済みモデルが見つかりません")

        bundle = load_model_bundle(model_path)

        # Phase 0: 常に ultimate モードでデータを取得（87特徴量固定）
        if True:  # noqa (request.ultimate_mode は常に True)
            # ── Ultimate DB から出走馬データを取得 ──
            import sqlite3 as _sq3

            _conn = _sq3.connect(str(db_path))
            _cur = _conn.cursor()
            _cur.execute("SELECT data FROM races_ultimate WHERE race_id = ?", (request.race_id,))
            _rrow = _cur.fetchone()
            if not _rrow:
                _conn.close()
                # DBにない場合 → オンデマンドスクレイプして保存してから再試行
                logger.info(f"[analyze] レース {request.race_id} がDBに未登録 → オンデマンドスクレイプ開始")
                try:
                    import aiohttp as _aiohttp
                    from scraping.race import scrape_race_full as _scrape_race_full  # type: ignore
                    from scraping.storage import _save_race_to_ultimate_db  # type: ignore
                    from scraping.constants import get_random_headers  # type: ignore
                    _date_hint = request.race_id[0:4] + request.race_id[4:6] + request.race_id[6:8]
                    _timeout = _aiohttp.ClientTimeout(total=60)
                    async with _aiohttp.ClientSession(headers=get_random_headers(), timeout=_timeout) as _sess:
                        _scraped = await _scrape_race_full(_sess, request.race_id, date_hint=_date_hint)
                    if not _scraped or not _scraped.get("horses"):
                        raise HTTPException(
                            status_code=404,
                            detail=f"レース {request.race_id} のスクレイプに失敗しました（データなし）",
                        )
                    _save_race_to_ultimate_db(_scraped, ULTIMATE_DB, overwrite=True)
                    logger.info(f"[analyze] レース {request.race_id} をDBに保存完了 ({len(_scraped['horses'])}頭)")
                except HTTPException:
                    raise
                except asyncio.TimeoutError:
                    raise HTTPException(
                        status_code=503,
                        detail=f"レース {request.race_id} のスクレイプがタイムアウトしました",
                    )
                except Exception as _se:
                    raise HTTPException(
                        status_code=503,
                        detail=f"レース {request.race_id} がDBに未登録で、スクレイプにも失敗しました: {_se}",
                    )
                # 保存後に再取得
                _conn = _sq3.connect(str(db_path))
                _cur = _conn.cursor()
                _cur.execute("SELECT data FROM races_ultimate WHERE race_id = ?", (request.race_id,))
                _rrow = _cur.fetchone()
                if not _rrow:
                    _conn.close()
                    raise HTTPException(status_code=500, detail=f"レース {request.race_id} の保存後読み込みに失敗しました")
            _race_data = json.loads(_rrow[0])
            race_info = {
                "race_id": request.race_id,
                "race_name": _race_data.get("race_name", ""),
                "venue": _race_data.get("venue", ""),
                "date": _race_data.get("date", ""),
                "distance": _race_data.get("distance", 0),
                "track_type": _race_data.get("track_type", ""),
                "weather": _race_data.get("weather", ""),
                "field_condition": _race_data.get("field_condition", ""),
                "num_horses": _race_data.get("num_horses", 0),
            }
            _cur.execute(
                "SELECT data FROM race_results_ultimate WHERE race_id = ? ORDER BY json_extract(data, '$.horse_number')",
                (request.race_id,),
            )
            _hrows = _cur.fetchall()
            _conn.close()
            if not _hrows:
                # 馬データなし → レース情報はあるが horse データが未登録のためオンデマンド再スクレイプ
                logger.info(f"[analyze] レース {request.race_id} は races_ultimate にあるが horse データなし → 再スクレイプ")
                try:
                    import aiohttp as _aiohttp
                    from scraping.race import scrape_race_full as _scrape_race_full  # type: ignore
                    from scraping.storage import _save_race_to_ultimate_db  # type: ignore
                    from scraping.constants import get_random_headers  # type: ignore
                    _timeout = _aiohttp.ClientTimeout(total=60)
                    async with _aiohttp.ClientSession(headers=get_random_headers(), timeout=_timeout) as _sess:
                        _scraped = await _scrape_race_full(_sess, request.race_id)
                    if not _scraped or not _scraped.get("horses"):
                        raise HTTPException(
                            status_code=404,
                            detail=f"レース {request.race_id} の馬データが見つかりません（スクレイプでも取得できませんでした）",
                        )
                    _save_race_to_ultimate_db(_scraped, ULTIMATE_DB, overwrite=True)
                    logger.info(f"[analyze] レース {request.race_id} 馬データ再スクレイプ完了 ({len(_scraped['horses'])}頭)")
                    # 再スクレイプ後に再取得
                    _conn2 = _sq3.connect(str(db_path))
                    _cur2 = _conn2.cursor()
                    _cur2.execute(
                        "SELECT data FROM race_results_ultimate WHERE race_id = ? ORDER BY json_extract(data, '$.horse_number')",
                        (request.race_id,),
                    )
                    _hrows = _cur2.fetchall()
                    _cur2.execute("SELECT data FROM races_ultimate WHERE race_id = ?", (request.race_id,))
                    _rrow2 = _cur2.fetchone()
                    if _rrow2:
                        _race_data = json.loads(_rrow2[0])
                    _conn2.close()
                    if not _hrows:
                        raise HTTPException(status_code=404, detail=f"レース {request.race_id} の馬データが見つかりません")
                except HTTPException:
                    raise
                except asyncio.TimeoutError:
                    raise HTTPException(
                        status_code=503,
                        detail=f"レース {request.race_id} の再スクレイプがタイムアウトしました",
                    )
                except Exception as _se:
                    raise HTTPException(
                        status_code=404,
                        detail=f"レース {request.race_id} の馬データが見つかりません（再スクレイプ失敗: {_se}）",
                    )

            _horse_records = []
            for _hr in _hrows:
                _hd = json.loads(_hr[0])
                _hd["race_id"] = request.race_id
                for _k, _v in _race_data.items():
                    if _k not in _hd or _hd[_k] is None:
                        _hd[_k] = _v
                _horse_records.append(_hd)

            df_pred = pd.DataFrame(_horse_records)

            # [S] 未来情報ブラックリスト列を推論入力から強制除外
            _drop_future = [c for c in POST_RACE_FIELDS if c in df_pred.columns]
            if _drop_future:
                df_pred = df_pred.drop(columns=_drop_future)
                logger.debug(f"[S] 未来情報列を除外: {_drop_future}")

            _col_map = {
                "finish_position": "finish", "finish_time": "time",
                "track_type": "surface", "last_3f": "last_3f_time", "weight_kg": "horse_weight",
                "weight_change": "horse_weight_change",
            }
            for _old, _new in _col_map.items():
                if _old in df_pred.columns:
                    if _new not in df_pred.columns:
                        df_pred[_new] = df_pred[_old]
                    else:
                        # 列が存在するが全 NaN のケース（races_ultimate.data の surface=None など）
                        # → track_type の値で NaN を補完する
                        df_pred[_new] = df_pred[_new].fillna(df_pred[_old])

            for _url_col, _id_col, _name_col in [
                ("jockey_url", "jockey_id", "jockey_name"),
                ("trainer_url", "trainer_id", "trainer_name"),
                ("horse_url", "horse_id", "horse_name"),
            ]:
                if _id_col not in df_pred.columns:
                    if _url_col in df_pred.columns:
                        df_pred[_id_col] = df_pred[_url_col].str.extract(r"/([^/]+)/?$")[0]
                    elif _name_col in df_pred.columns:
                        df_pred[_id_col] = df_pred[_name_col]

            _numeric_cols = [
                "bracket_number", "horse_number", "jockey_weight", "odds", "popularity",
                "horse_weight", "age", "distance", "num_horses",
                "kai", "day", "corner_1", "corner_2", "corner_3", "corner_4",
                "horse_total_runs", "horse_total_wins", "horse_total_prize_money",
                "prev_race_distance", "prev_race_finish", "prev_race_weight",
            ]
            for _c in _numeric_cols:
                if _c in df_pred.columns:
                    df_pred[_c] = pd.to_numeric(df_pred[_c], errors="coerce")

            # [fix] DB保存時にodds=Noneだった出馬表データを再スクレイプして最新オッズを補完
            # 過去レース・当日レース(race_date <= today)はdb.netkeiba.com結果ページから確定オッズを取得
            # 未来レースはshutubaページから暫定オッズを取得
            _odds_missing = (
                "odds" not in df_pred.columns
                or df_pred["odds"].isna().all()
                or (df_pred["odds"].fillna(0) == 0).all()  # 全馬 0.0 もオッズ未取得扱い
            )
            if _odds_missing:
                try:
                    import aiohttp as _aiohttp2
                    from scraping.storage import _save_race_to_ultimate_db as _srtud  # type: ignore
                    from scraping.constants import get_random_headers as _get_rh  # type: ignore
                    _today_str = datetime.now().strftime("%Y%m%d")
                    _race_date_str = race_info.get("date", "") or ""
                    # 当日レース（終了済み）も结果ページで確定オッズを取得できるため <= に変更
                    _is_past = _race_date_str and _race_date_str <= _today_str
                    _timeout2 = _aiohttp2.ClientTimeout(total=60)
                    _fresh = None
                    if _is_past:
                        # 過去・当日レース → 結果ページ（確定オッズあり）
                        from scraping.race import scrape_race_full as _srf2  # type: ignore
                        async with _aiohttp2.ClientSession(headers=_get_rh(), timeout=_timeout2) as _sess2:
                            _fresh = await _srf2(_sess2, request.race_id, date_hint=_race_date_str)
                        # 結果ページにオッズがない場合（レース未了）→ 出馬表にフォールバック
                        if not _fresh or not any(h.get("odds") for h in (_fresh or {}).get("horses", [])):
                            from scraping.race import _scrape_shutuba_fallback as _ssf  # type: ignore
                            async with _aiohttp2.ClientSession(headers=_get_rh(), timeout=_timeout2) as _sess2:
                                _fresh = await _ssf(_sess2, request.race_id)
                    else:
                        # 未来レース → 出馬表ページ（暫定オッズ）
                        from scraping.race import _scrape_shutuba_fallback as _ssf  # type: ignore
                        async with _aiohttp2.ClientSession(headers=_get_rh(), timeout=_timeout2) as _sess2:
                            _fresh = await _ssf(_sess2, request.race_id)
                    if _fresh and _fresh.get("horses"):
                        _odds_map = {
                            h["horse_number"]: h.get("odds")
                            for h in _fresh["horses"]
                            if h.get("odds") is not None
                        }
                        if _odds_map:
                            def _fill_odds(row):
                                hn = row.get("horse_number") or row.get("bracket_number")
                                return _odds_map.get(hn)
                            df_pred["odds"] = df_pred.apply(_fill_odds, axis=1)
                            _pop_map = {
                                h["horse_number"]: h.get("popularity")
                                for h in _fresh["horses"]
                                if h.get("popularity") is not None
                            }
                            if _pop_map and ("popularity" not in df_pred.columns or df_pred["popularity"].isna().all()):
                                df_pred["popularity"] = df_pred.apply(
                                    lambda r: _pop_map.get(r.get("horse_number") or r.get("bracket_number")), axis=1
                                )
                            # DBも更新して次回スクレイプ不要にする
                            try:
                                _srtud(_fresh, ULTIMATE_DB, overwrite=True)
                            except Exception:
                                pass
                            _src = "結果ページ" if _is_past else "出馬表"
                            logger.info(f"[analyze] {request.race_id}: {_src}再スクレイプでodds補完完了 ({len(_odds_map)}頭)")
                        else:
                            logger.info(f"[analyze] {request.race_id}: shutuba再スクレイプ完了だがoddはまだ未公開")
                except Exception as _roe:
                    logger.warning(f"[analyze] {request.race_id}: odds再スクレイプ失敗 → NaNのまま続行: {_roe}")

                # Playwright フォールバック:
                # netkeiba のオッズは JavaScript AJAX でロードされるため、静的 HTML では ---.- のまま
                # 上記の通常スクレイプで odds が取れなかった場合のみ実行
                _still_no_odds = (
                    "odds" not in df_pred.columns
                    or df_pred["odds"].isna().all()
                    or (df_pred["odds"].fillna(0) == 0).all()
                )
                if _still_no_odds:
                    try:
                        from routers.realtime_odds import _fetch_tansho_odds_playwright as _ftopl, _store as _odds_store  # type: ignore
                        _pw_odds = await _ftopl(request.race_id)
                        if _pw_odds:
                            _pw_int = {int(k): v for k, v in _pw_odds.items()}
                            df_pred["odds"] = df_pred.apply(
                                lambda r: _pw_int.get(int(float(r.get("horse_number") or 0))), axis=1
                            )
                            # リアルタイムオッズキャッシュにも保存（オッズ更新ボタンで参照される）
                            try:
                                _odds_store(request.race_id, {
                                    "race_id": request.race_id,
                                    "fetched_at": __import__("time").time(),
                                    "odds": {"tansho": _pw_odds},
                                    "horse_count": len(_pw_odds),
                                })
                            except Exception:
                                pass
                            logger.info(
                                f"[analyze] {request.race_id}: Playwright でodds補完完了 ({len(_pw_int)}頭)"
                            )
                        else:
                            logger.info(
                                f"[analyze] {request.race_id}: Playwright でもオッズ未公開（---.-）"
                            )
                    except Exception as _pe:
                        logger.warning(f"[analyze] {request.race_id}: Playwright odds取得失敗: {_pe}")

            # odds が揃っているのに popularity が欠損している場合、odds ランク順から自動計算
            if (
                "odds" in df_pred.columns
                and not df_pred["odds"].isna().all()
                and ("popularity" not in df_pred.columns or df_pred["popularity"].isna().all())
            ):
                df_pred["popularity"] = df_pred["odds"].rank(method="min", na_option="bottom").astype("Int64")
                logger.info(f"[analyze] {request.race_id}: popularity を odds ランクから自動計算")

            if "sex_age" in df_pred.columns:
                if "sex" not in df_pred.columns or df_pred["sex"].isna().all():
                    df_pred["sex"] = df_pred["sex_age"].str.extract(r"^([牡牝セ])")[0]
                if "age" not in df_pred.columns or df_pred["age"].isna().all():
                    df_pred["age"] = pd.to_numeric(df_pred["sex_age"].str.extract(r"(\d+)$")[0], errors="coerce")

            if "corner_positions" in df_pred.columns and "corner_positions_list" not in df_pred.columns:
                def _parse_cp(s):
                    try:
                        if pd.isna(s) or s == "":
                            return []
                        return [int(x) for x in str(s).split("-") if x.strip().isdigit()]
                    except Exception:
                        return []
                df_pred["corner_positions_list"] = df_pred["corner_positions"].apply(_parse_cp)

            # [S: Quality Gate] /analyze 入力データ品質チェック
            # Q2 (odds 欠損) はレース前・オッズ未公開時に発生するため WARNING 扱い。
            # Q1 (distance=0) / Q3 (同一レース内の揺れ) のみ致命的エラーとする。
            try:
                from keiba_ai.quality_gate import validate_race_entries as _vqr  # type: ignore
                _qr_a = _vqr(df_pred)
                # Q2 (odds 欠損) / Q5 (popularity 欠損) はレース前のオッズ未公開時に発生 → WARNING 扱い
                # Q1 (distance=0) / Q3 (同一レース内の揺れ) のみ致命的エラーとする
                _fatal_ids = {
                    i.race_id for i in _qr_a.issues
                    if i.severity == "ERROR" and i.issue_code not in ("Q2", "Q5")
                }
                if _fatal_ids:
                    raise HTTPException(
                        status_code=400,
                        detail=f"[Quality Gate] レース {request.race_id} の入力データに問題があります:\n{_qr_a.summary()}",
                    )
                if _qr_a.n_bad > 0 or _qr_a.n_warn > 0:
                    logger.warning(f"[Quality Gate warn] /analyze {request.race_id}:\n{_qr_a.summary()}")
            except HTTPException:
                raise
            except Exception as _qe_a:
                logger.warning(f"[Quality Gate /analyze] スキップ: {_qe_a}")

            # [INV-01] full_history_df には対象レースの行を含めない（expanding window に確定結果が混入しないよう）
            # NOTE: _load_hist_cached / build_features は CPU 集中型の同期処理のため
            #       asyncio.to_thread でスレッドプールに移し、イベントループをブロックしない
            try:
                _hist_df2 = await asyncio.to_thread(_load_hist_cached)
                # 対象レースの確定結果が expanding stats に混入しないよう hist から除外する（INV-01）
                if 'race_id' in _hist_df2.columns:
                    _hist_df2 = _hist_df2[_hist_df2['race_id'] != request.race_id]
                # FutureWarning 回避: 全列が NaN の列を concat 前に除外
                _df_pred_for_concat = df_pred.loc[:, ~df_pred.isna().all()]
                _hist_df2_for_concat = _hist_df2.loc[:, ~_hist_df2.isna().all()]
                _full_hist2 = pd.concat([_hist_df2_for_concat, _df_pred_for_concat], ignore_index=True)
            except Exception:
                _full_hist2 = df_pred

            # ModelPredictor: per-model feature build（strict, NaN補間なし）
            # NOTE: ModelPredictor.build_features が add_derived_features を内部で呼ぶため
            #       ここでの手動呼び出しは不要（二重適用防止）。
            predictor = ModelPredictor(bundle, model_path)
            X_pred = await asyncio.to_thread(predictor.build_features, df_pred, _full_hist2)

            # ModelPredictor: per-model scoring（ターゲット種別に応じて自動選択）
            import numpy as _np2
            _wp_raw, win_probs = predictor.predict_scores(X_pred)
            _bundle_target = predictor.target
            _wp_sum = win_probs.sum()
            _wp_norm = (win_probs / _wp_sum) if _wp_sum > 0 else win_probs

            # ── place3 モデルによる複勝圏確率 ──────────────────────────────────
            _place3_probs: "_np2.ndarray | None" = None
            _place3_norm: "_np2.ndarray | None" = None
            try:
                _place3_model_files = sorted(
                    MODELS_DIR.glob("model_place3_*.joblib"),
                    key=lambda _p: _p.stat().st_mtime, reverse=True,
                )
                if _place3_model_files:
                    _sub_result = _predict_sub_model(_place3_model_files[0], df_pred, full_hist=_full_hist2)
                    if _sub_result is not None:
                        _place3_probs, _place3_norm = _sub_result
                        logger.info(
                            f"[analyze] place3モデル ({_place3_model_files[0].name}) 適用: "
                            f"top={_place3_norm.max():.3f}"
                        )
            except Exception as _p3e:
                logger.warning(f"[analyze] place3モデルロード失敗: {_p3e}")

            # ── アンサンブルスコア（win/speed + place3 の加重平均）──────────────
            # _place3_norm を使用（正規化済み分布で win_probs と整合）
            ensemble_probs = _compute_ensemble(win_probs, _place3_norm, _bundle_target)

            # [fix] 再スクレイプで df_pred["odds"] が更新された場合、
            # _horse_records には反映されないため、horse_number をキーに逆引きマップを作成
            import pandas as _pd_odds
            _df_odds_lookup: dict = {}
            if "odds" in df_pred.columns:
                for _, _drow in df_pred.iterrows():
                    _hn_key = _drow.get("horse_number") or _drow.get("horse_no")
                    _ov = _drow.get("odds")
                    if _hn_key is not None and _ov is not None and not _pd_odds.isna(_ov) and float(_ov) > 0:
                        _df_odds_lookup[int(float(_hn_key))] = float(_ov)
            _df_popularity_lookup: dict = {}
            if "popularity" in df_pred.columns:
                for _, _drow in df_pred.iterrows():
                    _hn_key = _drow.get("horse_number") or _drow.get("horse_no")
                    _pv = _drow.get("popularity")
                    if _hn_key is not None and _pv is not None and not _pd_odds.isna(_pv) and float(_pv) > 0:
                        _df_popularity_lookup[int(float(_hn_key))] = int(float(_pv))

            predictions = []
            for i, _hr in enumerate(_horse_records):
                _horse_num = _hr.get("horse_number") or _hr.get("horse_no") or (i + 1)
                # df_pred の再スクレイプ済みオッズを優先、なければ _horse_records から取得
                _odds_float: float | None = _df_odds_lookup.get(int(_horse_num))
                if _odds_float is None:
                    _raw_odds = _hr.get("odds") if _hr.get("odds") is not None else _hr.get("win_odds")
                    try:
                        _odds_float = float(_raw_odds) if _raw_odds not in (None, "", "---", 0, 0.0) else None
                    except (ValueError, TypeError):
                        _odds_float = None
                # 期待値: オッズ未取得の場合は p_norm のみ（暫定値）
                _ev = float(_wp_norm[i] * _odds_float) if _odds_float is not None else None
                predictions.append({
                    "horse_number": _horse_num, "horse_no": _horse_num,
                    "horse_id": _hr.get("horse_id", ""),
                    "horse_name": _hr.get("horse_name") or f'[{_hr.get("horse_id","") or _horse_num}]',
                    "jockey_name": _hr.get("jockey_name", ""),
                    "trainer_name": _hr.get("trainer_name", ""),
                    "sex": _hr.get("sex", ""), "age": _hr.get("age"),
                    "horse_weight": _hr.get("weight_kg") or _hr.get("horse_weight"),
                    "odds": _odds_float, "popularity": _df_popularity_lookup.get(int(_horse_num)) or _hr.get("popularity"),
                    "win_probability": float(win_probs[i]),
                    "p_raw": float(_wp_raw[i]),
                    "p_norm": float(_wp_norm[i]),
                    "p_place3": float(_place3_norm[i]) if _place3_norm is not None and i < len(_place3_norm) else None,
                    "p_ensemble": float(ensemble_probs[i]) if i < len(ensemble_probs) else float(_wp_norm[i]),
                    "expected_value": _ev,  # [A1] p_norm×odds（オッズ未取得時はNone）
                })

            # [A1] ソートは p_raw 降順、predicted_rank 割り当て
            predictions.sort(key=lambda x: x.get("p_raw", 0), reverse=True)
            for _rank, _pred in enumerate(predictions, 1):
                _pred["predicted_rank"] = _rank

        recommender = BettingRecommender(
            bankroll=request.bankroll, risk_mode=request.risk_mode,
            use_kelly=request.use_kelly, dynamic_unit=request.dynamic_unit, min_ev=request.min_ev,
        )
        result = recommender.analyze_and_recommend(predictions, race_info)

        _resp_data = dict(
            success=True,
            race_info=result["race_info"],
            pro_evaluation=result["pro_evaluation"],
            predictions=result["predictions"],
            bet_types=result["bet_types"],
            best_bet_type=result["best_bet_type"],
            best_bet_info=result["best_bet_info"],
            race_level=result["race_level"],
            recommendation=result["recommendation"],
        )
        # 予測ログをDBに非同期保存（レスポンスをブロックしない）
        try:
            _model_id_log = bundle.get("model_id", bundle.get("created_at", "unknown"))
            asyncio.create_task(asyncio.to_thread(
                _save_prediction_log,
                request.race_id, result["race_info"], result["predictions"],
                _model_id_log, str(ULTIMATE_DB)
            ))
        except Exception as _log_err:
            logger.warning(f"[prediction_log] save failed: {_log_err}")
        # キャッシュに保存（上限超過時は最古エントリを削除）
        if len(_ANALYZE_CACHE) >= _ANALYZE_CACHE_MAX:
            _oldest_keys = sorted(_ANALYZE_CACHE.keys(), key=lambda k: _ANALYZE_CACHE[k][0])
            for _ek in _oldest_keys[:len(_ANALYZE_CACHE) - _ANALYZE_CACHE_MAX + 1]:
                del _ANALYZE_CACHE[_ek]
        _ANALYZE_CACHE[_cache_key] = (_time.time(), _resp_data)
        logger.info(f"[cache store] analyze_race {request.race_id}")
        return AnalyzeRaceResponse(**_resp_data)

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"レース分析に失敗: {str(e)}")


@router.post("/api/analyze_races_batch")
async def analyze_races_batch(request: BatchAnalyzeRequest, http_req: Request):
    """複数レースを一括分析（一括予測）"""
    await check_and_consume_pred_count(http_req)
    results: dict = {}
    for race_id in request.race_ids:
        req = AnalyzeRaceRequest(
            race_id=race_id,
            model_id=request.model_id,
            bankroll=request.bankroll,
            risk_mode=request.risk_mode,
            use_kelly=request.use_kelly,
            dynamic_unit=request.dynamic_unit,
            min_ev=request.min_ev,
        )
        try:
            resp = await analyze_race(req)
            results[race_id] = {"success": True, "data": resp.dict()}
        except HTTPException as e:
            results[race_id] = {"success": False, "error": e.detail}
        except Exception as e:
            results[race_id] = {"success": False, "error": str(e)}
    return {
        "results": results,
        "total": len(request.race_ids),
        "success_count": sum(1 for v in results.values() if v["success"]),
    }
