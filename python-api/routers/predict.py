"""
予測・レース分析エンドポイント
POST /api/predict
POST /api/analyze_race
"""
from __future__ import annotations

import json
import traceback
import uuid
import hashlib
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
    get_active_model_id,
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
from knowledge.scenario_engine import (  # type: ignore
    build_scenario_graph,
    explain_prediction_reason,
    get_race_scenario,
    scenario_feature_dict,
)
from mlops import MLOpsStore  # type: ignore
from research.scenario_model_router import resolve_scenario_model  # type: ignore
from scraping.jobs import _new_session  # type: ignore

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
# training_data キャッシュ（調教タイム特徴量用）
_TRAINING_CACHE: "tuple[float, 'pd.DataFrame'] | None" = None
_TRAINING_CACHE_TTL = 3600  # 1時間（変化頻度が低い）
# speed_figures キャッシュ（速度指数特徴量用）
_SPEED_FIGURES_CACHE: "tuple[float, 'pd.DataFrame'] | None" = None
_SPEED_FIGURES_CACHE_TTL = 3600  # 1時間


def _canary_bucket_for_race(race_id: str) -> int:
    s = str(race_id or "")
    if not s:
        return 0
    h = hashlib.md5(s.encode("utf-8")).hexdigest()
    return int(h[:8], 16) % 100


def _resolve_router_modes(
    *,
    requested_mode: str,
    use_scenario_router: bool,
    race_id: str,
    canary_percent: int,
) -> dict[str, object]:
    mode = str(requested_mode or "off").strip().lower()
    if mode not in {"off", "shadow", "active", "canary"}:
        mode = "off"
    if mode == "off" and bool(use_scenario_router):
        mode = "active"

    pct = max(0, min(100, int(canary_percent)))
    bucket = _canary_bucket_for_race(str(race_id or ""))
    selected = bool(bucket < pct)

    effective = mode
    if mode == "canary":
        effective = "active" if selected else "shadow"

    return {
        "requested_mode": mode,
        "effective_mode": effective,
        "canary_percent": pct,
        "canary_bucket": bucket,
        "canary_selected": selected,
    }


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


def _load_training_cached() -> "pd.DataFrame":
    """training_data テーブルをキャッシュ付きで返す（TTL=1時間）。"""
    global _TRAINING_CACHE
    import sqlite3 as _sq3
    import pandas as _pd
    try:
        if _TRAINING_CACHE is not None and (_time.time() - _TRAINING_CACHE[0]) < _TRAINING_CACHE_TTL:
            return _TRAINING_CACHE[1]
        _conn = _sq3.connect(str(ULTIMATE_DB))
        _df = _pd.read_sql("SELECT * FROM training_data", _conn)
        _conn.close()
        _TRAINING_CACHE = (_time.time(), _df)
        return _df
    except Exception:
        return _pd.DataFrame()


def _load_speed_figures_cached() -> "pd.DataFrame":
    """speed_figures テーブルをキャッシュ付きで返す（TTL=1時間）。"""
    global _SPEED_FIGURES_CACHE
    import sqlite3 as _sq3sf
    import pandas as _pdsf
    try:
        if _SPEED_FIGURES_CACHE is not None and (_time.time() - _SPEED_FIGURES_CACHE[0]) < _SPEED_FIGURES_CACHE_TTL:
            return _SPEED_FIGURES_CACHE[1]
        _conn = _sq3sf.connect(str(ULTIMATE_DB))
        _df = _pdsf.read_sql("SELECT * FROM speed_figures", _conn)
        _conn.close()
        _SPEED_FIGURES_CACHE = (_time.time(), _df)
        return _df
    except Exception:
        return _pdsf.DataFrame()
# レース後確定フィールド（keiba_ai.constants.FUTURE_FIELDS を参照）
POST_RACE_FIELDS = FUTURE_FIELDS

# ── モジュールレベルヘルパー ──────────────────────────────────────────

def _resolve_model_path(model_id: "str | None") -> "Path":
    """model_id 指定 → アクティブモデル → latest の順でモデルパスを解決する。"""
    from app_config import list_models_from_supabase  # type: ignore
    if model_id:
        p = _ensure_model_local(model_id)
        if not p:
            raise HTTPException(status_code=404, detail=f"モデル {model_id} が見つかりません")
        return p
    # アクティブモデルが設定されていればそれを使う
    active_id = get_active_model_id()
    if active_id:
        p = _ensure_model_local(active_id)
        if p:
            return p
    # フォールバック: 最新 win モデル
    p = get_latest_model()
    if p is None and SUPABASE_DATA_ENABLED and get_supabase_client():
        sb_models = list_models_from_supabase()
        if sb_models:
            p = _ensure_model_local(sb_models[0]["model_id"])
    if p is None:
        raise HTTPException(status_code=404, detail="学習済みモデルが見つかりません")
    return p


async def _scrape_race_on_demand(race_id: str, date_hint: str = "") -> dict:
    """レースをオンデマンドでスクレイプしてDBに保存し、scraped dict を返す。

    両呼び出し箇所（races_ultimate 未登録 / horse データなし）で共用する。
    セッションは _new_session() を使い bot 回避設定を統一する。
    """
    from scraping.race import scrape_race_full as _srfull  # type: ignore
    from scraping.storage import _save_race_to_ultimate_db  # type: ignore
    from scraping.constants import login_netkeiba, IPBlockedError, jitter_sleep, warm_up_netkeiba  # type: ignore

    _sess = _new_session()
    try:
        await warm_up_netkeiba(_sess)    # トップページ訪問でCookieを取得（人間らしいナビゲーション）
        await login_netkeiba(_sess)
        await jitter_sleep(1.0, 2.5)     # ログイン後のランダム待機（ロボット検知回避）
        _scraped = await _srfull(_sess, race_id, date_hint=date_hint)
    except IPBlockedError as _ib:
        raise HTTPException(
            status_code=503,
            detail=f"IPブロック: {_ib}",
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=503,
            detail=f"レース {race_id} のスクレイプがタイムアウトしました",
        )
    finally:
        await _sess.close()

    if not _scraped or not _scraped.get("horses"):
        raise HTTPException(
            status_code=404,
            detail=f"レース {race_id} のスクレイプに失敗しました（データなし）",
        )
    _save_race_to_ultimate_db(_scraped, ULTIMATE_DB)
    return _scraped


def _filter_hist_for_race(hist_df: "pd.DataFrame", df_pred: "pd.DataFrame") -> "pd.DataFrame":
    """全履歴を予測対象レースの関連エンティティに絞り込む（高速化）。

    _fe_history は全行に対して expanding window 統計を計算するため、無関係なエンティティの
    データを含めると不要な計算コストが発生する（49,699行 → ~18秒）。
    予測対象レースに登場する馬・騎手・調教師・血統・開催場のデータのみに絞ることで
    計算対象行数を1/5〜1/10に削減し、処理時間を 3〜4倍高速化する。

    correctness 保証:
    - 各エンティティの全過去レースを含めるため expanding stats の値は変わらない ✓
    - venue フィルタにより gate_bias 統計の精度を維持する ✓
    - 無関係エンティティのデータを除外するだけなので INV-01 に影響しない ✓
    """
    if hist_df.empty:
        return hist_df

    conditions: "list[pd.Series]" = []
    for _col in ('horse_id', 'jockey_id', 'trainer_id'):
        if _col in hist_df.columns and _col in df_pred.columns:
            _ids = set(df_pred[_col].dropna())
            if _ids:
                conditions.append(hist_df[_col].isin(_ids))
    # 血統（sire / damsire）— _feh_entity_career が使用
    for _col in ('sire', 'damsire'):
        if _col in hist_df.columns and _col in df_pred.columns:
            _ids = set(df_pred[_col].dropna())
            if _ids:
                conditions.append(hist_df[_col].isin(_ids))
    # 開催場 — _feh_gate_bias が全レースの枠番バイアスを計算するため venue 行は全て含める
    _venue = df_pred['venue'].iloc[0] if 'venue' in df_pred.columns and not df_pred['venue'].empty else None
    if _venue and 'venue' in hist_df.columns:
        conditions.append(hist_df['venue'] == _venue)

    if not conditions:
        return hist_df

    import functools
    mask = functools.reduce(lambda a, b: a | b, conditions)
    filtered = hist_df[mask]
    logger.debug(
        f"[hist filter] {len(hist_df):,} → {len(filtered):,} rows "
        f"({len(filtered)/len(hist_df)*100:.0f}%)"
    )
    return filtered


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
    training_df: "pd.DataFrame | None" = None,
    speed_figures_df: "pd.DataFrame | None" = None,
    _df_precomputed: "pd.DataFrame | None" = None,
) -> "tuple[np.ndarray, np.ndarray] | None":
    """サブモデル（place3 など）で確率を計算して返す。ModelPredictor を経由して推論。

    _df_precomputed: add_derived_features 適用済み DataFrame（渡すと特徴量再計算をスキップ）。
    Returns:
        (raw_scores, proba_norm) または None（失敗時）
    """
    try:
        _bundle = load_model_bundle(model_path)
        _predictor = ModelPredictor(_bundle, model_path)
        _X = _predictor.build_features(
            df,
            full_hist=full_hist,
            training_df=training_df,
            speed_figures_df=speed_figures_df,
            _df_precomputed=_df_precomputed,
        )
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
    # 常に 50/50 ブレンド（speed+place3 / win+place3 / クロス いずれも同率）
    _ens = 0.50 * win_probs + 0.50 * place3_probs
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
        training_df: "pd.DataFrame | None" = None,
        speed_figures_df: "pd.DataFrame | None" = None,
        _df_precomputed: "pd.DataFrame | None" = None,
    ) -> "pd.DataFrame":
        """推論用特徴量 DataFrame を構築し、feature_columns 順に整列して返す。

        _df_precomputed: analyze_race が事前に add_derived_features を 1回だけ呼び、
                         その結果を 3 モデル（win/place3/cross）で共有する際に渡す。
                         渡された場合は Step 1（特徴量エンジニアリング）をスキップして
                         optimizer.transform から始める。各モデルが独立して transform
                         できるよう copy() して使用する。

        各ステップの失敗時の挙動:
        - add_derived_features 失敗 → 警告のみ（部分失敗は次の strict check で検出）
        - optimizer.transform 失敗 → HTTP 500（モデルと特徴量セットの不一致。再学習必要）
        - feature_columns 不足 → HTTP 500（不足列名を列挙して原因を明示）
        NaN補間は行わない。
        """
        from keiba_ai.feature_engineering import add_derived_features as _adf  # type: ignore

        # Step 1: 派生特徴量エンジニアリング
        # _df_precomputed が渡された場合はスキップ（analyze_race が 1回だけ計算済み）
        if _df_precomputed is not None:
            df = _df_precomputed.copy()  # 各モデルの optimizer.transform が独立して動けるよう copy
        else:
            try:
                df = _adf(
                    df,
                    full_history_df=full_hist if full_hist is not None else df,
                    training_df=training_df,
                    speed_figures_df=speed_figures_df,
                )
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
            # P-3: bundle に保存された校正済み温度 T を使用（デフォルト 1.0）
            # T_min=0.3 フロアを設けて Kelly が過剰賭けしないよう安全化
            _T_RAW = float(self.bundle.get("softmax_temperature", 1.0))
            _T = max(_T_RAW, 0.3)
            finite = _np_p.isfinite(raw)
            _safe = _np_p.where(
                finite, raw,
                raw[finite].min() - 1.0 if finite.any() else -5.0,
            )
            _scaled = _safe / _T
            _exp = _np_p.exp(_scaled - _scaled.max())
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
            # 高速化: 予測対象レースの関連エンティティのみに絞り込む（3〜4倍速）
            _hist_df = _filter_hist_for_race(_hist_df, df)
            _full_hist = pd.concat([_hist_df, df], ignore_index=True)
            # [高速化] race_id で事前ソート → _expanding_* 内の sort_values が trivial になる
            if 'race_id' in _full_hist.columns:
                _full_hist = _full_hist.sort_values('race_id', kind='mergesort').reset_index(drop=True)
        except Exception:
            _full_hist = df

        # 調教タイムキャッシュ
        try:
            _training_df = await asyncio.to_thread(_load_training_cached)
        except Exception:
            _training_df = None
        # 速度指数キャッシュ
        try:
            _speed_figures_df = await asyncio.to_thread(_load_speed_figures_cached)
        except Exception:
            _speed_figures_df = None

        # ModelPredictor で特徴量構築（不足時は HTTP 500）
        X = await asyncio.to_thread(predictor.build_features, df, _full_hist, _training_df, _speed_figures_df)

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
    _router_mode_info = _resolve_router_modes(
        requested_mode=str(getattr(request, "router_mode", "") or "off"),
        use_scenario_router=bool(request.use_scenario_router),
        race_id=str(request.race_id),
        canary_percent=int(getattr(request, "canary_percent", 0) or 0),
    )
    _req_router_mode = str(_router_mode_info.get("requested_mode") or "off")
    _effective_router_mode = str(_router_mode_info.get("effective_mode") or "off")
    _canary_percent = int(_router_mode_info.get("canary_percent") or 0)
    _canary_bucket = int(_router_mode_info.get("canary_bucket") or 0)
    _canary_selected = bool(_router_mode_info.get("canary_selected"))

    # ── キャッシュチェック（TTL=5分、bankroll/risk_mode が同一の場合のみ）
    _cache_key = (
        f"{request.race_id}:{request.model_id}:{request.bankroll}:{request.risk_mode}:"
        f"router={_req_router_mode}:{_effective_router_mode}:{_canary_percent}:"
        f"{request.router_target or ''}"
    )
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

        _router_meta = {
            "selected_model_id": "",
            "route_type": "NO_MODEL",
            "matched_scenario_key": "",
            "matched_scenario_value": "",
            "router_reason": "router not used",
            "fallback_used": False,
        }
        _shadow_router_meta = {
            "shadow_selected_model_id": "",
            "shadow_route_type": "",
            "shadow_matched_scenario_key": "",
            "shadow_matched_scenario_value": "",
            "shadow_router_reason": "",
            "shadow_fallback_used": False,
        }

        _resolved_model_id = str(request.model_id or "")
        if _effective_router_mode in {"active", "shadow"}:
            try:
                _route = resolve_scenario_model(
                    race_id=str(request.race_id),
                    race_db_path=str(ULTIMATE_DB),
                    target=(str(request.router_target) if request.router_target else None),
                    use_router=True,
                    store=MLOpsStore(),
                )
                _matched_policy = _route.get("matched_policy") if isinstance(_route.get("matched_policy"), dict) else {}
                _router_meta = {
                    "selected_model_id": str(_route.get("selected_model_id") or ""),
                    "route_type": str(_route.get("route_type") or "NO_MODEL"),
                    "matched_scenario_key": str((_matched_policy or {}).get("scenario_key") or ""),
                    "matched_scenario_value": str((_matched_policy or {}).get("scenario_value") or ""),
                    "router_reason": str(_route.get("router_reason") or ""),
                    "fallback_used": bool(_route.get("fallback_used")),
                }
                _shadow_router_meta = {
                    "shadow_selected_model_id": str(_route.get("selected_model_id") or ""),
                    "shadow_route_type": str(_route.get("route_type") or "NO_MODEL"),
                    "shadow_matched_scenario_key": str((_matched_policy or {}).get("scenario_key") or ""),
                    "shadow_matched_scenario_value": str((_matched_policy or {}).get("scenario_value") or ""),
                    "shadow_router_reason": str(_route.get("router_reason") or ""),
                    "shadow_fallback_used": bool(_route.get("fallback_used")),
                }
                if _effective_router_mode == "active" and _router_meta["selected_model_id"]:
                    _resolved_model_id = str(_router_meta["selected_model_id"])
            except Exception as _router_err:
                logger.warning(f"[scenario_router] resolve failed: {_router_err}")
                if _effective_router_mode == "active":
                    _router_meta = {
                        "selected_model_id": "",
                        "route_type": "FALLBACK_GLOBAL",
                        "matched_scenario_key": "",
                        "matched_scenario_value": "",
                        "router_reason": f"router error fallback: {_router_err}",
                        "fallback_used": True,
                    }
                else:
                    _shadow_router_meta = {
                        "shadow_selected_model_id": "",
                        "shadow_route_type": "NO_MODEL",
                        "shadow_matched_scenario_key": "",
                        "shadow_matched_scenario_value": "",
                        "shadow_router_reason": f"shadow resolve failed: {_router_err}",
                        "shadow_fallback_used": True,
                    }

        # モデルロード
        if _resolved_model_id:
            model_path = _ensure_model_local(_resolved_model_id)
            if not model_path:
                if _effective_router_mode == "active":
                    model_path = get_latest_model()
                    if model_path is not None:
                        _router_meta["selected_model_id"] = model_path.stem
                        _router_meta["route_type"] = "FALLBACK_GLOBAL"
                        _router_meta["router_reason"] = "resolved model missing; fallback to latest model"
                        _router_meta["fallback_used"] = True
                    else:
                        raise HTTPException(status_code=404, detail=f"モデル {_resolved_model_id} が見つかりません")
                else:
                    raise HTTPException(status_code=404, detail=f"モデル {_resolved_model_id} が見つかりません")
        else:
            model_path = get_latest_model()
            if model_path is None:
                if SUPABASE_DATA_ENABLED and get_supabase_client():
                    sb_models = list_models_from_supabase()
                    if sb_models:
                        model_path = _ensure_model_local(sb_models[0]["model_id"])
                if model_path is None:
                    raise HTTPException(status_code=404, detail="訓練済みモデルが見つかりません")
            if _effective_router_mode == "active":
                _router_meta["selected_model_id"] = model_path.stem
                _router_meta["route_type"] = "FALLBACK_GLOBAL"
                _router_meta["router_reason"] = "no routed model found; fallback to latest model"
                _router_meta["fallback_used"] = True

        if _effective_router_mode == "shadow":
            _router_meta = {
                "selected_model_id": str(model_path.stem),
                "route_type": "SHADOW_ONLY",
                "matched_scenario_key": "",
                "matched_scenario_value": "",
                "router_reason": "shadow mode: prediction uses actual model only",
                "fallback_used": False,
            }

        bundle = load_model_bundle(model_path)

        # Phase 0: 常に ultimate モードでデータを取得（87特徴量固定）
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
                _date_hint = request.race_id[0:4] + request.race_id[4:6] + request.race_id[6:8]
                _scraped = await _scrape_race_on_demand(request.race_id, date_hint=_date_hint)
                logger.info(f"[analyze] レース {request.race_id} をDBに保存完了 ({len(_scraped['horses'])}頭)")
            except HTTPException:
                raise
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
        _scenario_payload = get_race_scenario(
            race_db_path=str(ULTIMATE_DB),
            race_id=str(request.race_id),
            auto_rebuild_if_missing=True,
        )
        _scenario_graph = build_scenario_graph(_scenario_payload)
        _scenario_features = scenario_feature_dict(_scenario_payload)
        race_info["scenario"] = {
            "scenario_id": _scenario_graph.get("scenario_id"),
            "scenario_hash": _scenario_graph.get("scenario_hash"),
            "expected_pace": _scenario_payload.get("expected_pace"),
            "expected_bias": _scenario_payload.get("expected_bias"),
            "winning_pattern": _scenario_payload.get("winning_pattern"),
            "race_complexity": _scenario_payload.get("race_complexity"),
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
                _scraped = await _scrape_race_on_demand(request.race_id)
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
        def _check_odds_missing() -> bool:
            return (
                "odds" not in df_pred.columns
                or df_pred["odds"].isna().all()
                or (df_pred["odds"].fillna(0) == 0).all()  # 全馬 0.0 もオッズ未取得扱い
            )
        _odds_missing = _check_odds_missing()
        # [高速化] リアルタイムオッズキャッシュを確認（Playwright再スクレイプより優先）
        # predict-batch が事前に /api/realtime-odds/refresh を呼んでいる場合、ここで即補完できる
        if _odds_missing:
            try:
                from routers.realtime_odds import _cached as _rto_cached  # type: ignore
                _rto_hit = _rto_cached(request.race_id)
                if _rto_hit and _rto_hit.get("odds", {}).get("tansho"):
                    _tansho = _rto_hit["odds"]["tansho"]
                    _odds_by_num = {int(float(k)): float(v) for k, v in _tansho.items() if v}
                    if _odds_by_num:
                        df_pred["odds"] = df_pred.apply(
                            lambda _r: _odds_by_num.get(
                                int(float(_r.get("horse_number") or _r.get("horse_no") or 0))
                            ),
                            axis=1,
                        )
                        logger.info(
                            f"[analyze] {request.race_id}: リアルタイムオッズキャッシュ補完 ({len(_odds_by_num)}頭)"
                        )
                        _odds_missing = _check_odds_missing()
            except Exception as _rto_e:
                logger.debug(f"[analyze] realtime_odds cache check failed: {_rto_e}")
        if _odds_missing:
            try:
                from scraping.storage import _save_race_to_ultimate_db as _srtud  # type: ignore
                from scraping.race import scrape_race_full as _srf2, _scrape_shutuba_fallback as _ssf  # type: ignore
                from scraping.constants import IPBlockedError as _IPBlockedError, jitter_sleep as _jsleep  # type: ignore
                _today_str = datetime.now().strftime("%Y%m%d")
                _race_date_str = race_info.get("date", "") or ""
                # 当日レース（終了済み）も結果ページで確定オッズを取得できるため <= に変更
                _is_past = _race_date_str and _race_date_str <= _today_str
                _fresh = None
                _needs_shutuba = True
                # [最適化] DBに _shutuba=True のキャッシュがある場合はオンデマンドスクレイプを全スキップ
                # 馬詳細は既取得済みのため scrape_race_full (~55s) を省略し Playwright (~7s) に直行する
                _has_shutuba_cache = any(json.loads(_hr[0]).get("_shutuba") for _hr in _hrows)
                if _has_shutuba_cache:
                    logger.info(
                        f"[analyze] {request.race_id}: _shutuba キャッシュ済み → "
                        f"オンデマンドスクレイプをスキップ (Playwright へ直行)"
                    )
                    _fresh = None
                    _needs_shutuba = False
                elif _is_past:
                    # 過去・当日レース → 結果ページ（確定オッズあり）
                    await _jsleep(1.5, 3.0)  # 人間らしい待機（INV-07）
                    _sess2 = _new_session()
                    try:
                        _fresh = await _srf2(_sess2, request.race_id, date_hint=_race_date_str)
                    finally:
                        await _sess2.close()
                    # 結果ページにオッズがない場合（レース未了）→ 出馬表にフォールバック
                    _needs_shutuba = not _fresh or not any(h.get("odds") for h in (_fresh or {}).get("horses", []))
                    # scrape_race_full が内部で _scrape_shutuba_fallback にフォールバック済みの場合は
                    # 再呼び出しをスキップ（_shutuba=True フラグで判定）。Playwright へ直行する。
                    if _needs_shutuba and _fresh and any(h.get("_shutuba") for h in _fresh.get("horses", [])):
                        _needs_shutuba = False
                        logger.debug(f"[analyze] {request.race_id}: shutuba既取得 → shutuba再スクレイプをスキップ")
                if _needs_shutuba:
                    # 未来レース or 結果ページにオッズなし → 出馬表ページ（暫定オッズ）
                    await _jsleep(1.5, 3.0)  # 人間らしい待機（INV-07）
                    _sess2 = _new_session()
                    try:
                        _fresh = await _ssf(_sess2, request.race_id)
                    finally:
                        await _sess2.close()
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
                            _srtud(_fresh, ULTIMATE_DB)
                        except Exception:
                            pass
                        _src = "結果ページ" if _is_past else "出馬表"
                        logger.info(f"[analyze] {request.race_id}: {_src}再スクレイプでodds補完完了 ({len(_odds_map)}頭)")
                    else:
                        logger.info(f"[analyze] {request.race_id}: shutuba再スクレイプ完了だがoddはまだ未公開")
            except _IPBlockedError as _ib:
                raise HTTPException(
                    status_code=503,
                    detail=f"[analyze] {request.race_id}: IPブロック (HTTP 400) — 予測を中止します: {_ib}",
                )
            except Exception as _roe:
                logger.warning(f"[analyze] {request.race_id}: odds再スクレイプ失敗 → NaNのまま続行: {_roe}")

            # Playwright フォールバック:
            # netkeiba のオッズは JavaScript AJAX でロードされるため、静的 HTML では ---.- のまま
            # 上記の通常スクレイプで odds が取れなかった場合のみ実行
            _still_no_odds = _check_odds_missing()
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
            # 高速化: 予測対象レースの関連エンティティのみに絞り込む（3〜4倍速）
            _hist_df2 = _filter_hist_for_race(_hist_df2, df_pred)
            # FutureWarning 回避: 全列が NaN の列を concat 前に除外
            _df_pred_for_concat = df_pred.loc[:, ~df_pred.isna().all()]
            _hist_df2_for_concat = _hist_df2.loc[:, ~_hist_df2.isna().all()]
            _full_hist2 = pd.concat([_hist_df2_for_concat, _df_pred_for_concat], ignore_index=True)
            # [高速化] race_id で事前ソート＋reset_index → _expanding_* 内の sort_values が trivial になる
            if 'race_id' in _full_hist2.columns:
                _full_hist2 = _full_hist2.sort_values('race_id', kind='mergesort').reset_index(drop=True)
        except Exception:
            _full_hist2 = df_pred

        # 調教タイムキャッシュ（/analyze）
        try:
            _training_df2 = await asyncio.to_thread(_load_training_cached)
        except Exception:
            _training_df2 = None
        # 速度指数キャッシュ（/analyze）
        try:
            _speed_figures_df2 = await asyncio.to_thread(_load_speed_figures_cached)
        except Exception:
            _speed_figures_df2 = None

        # ── [高速化] add_derived_features を 1 回だけ計算して 3 モデルで共有 ──
        # win / place3 / cross の各 build_features が独立して _adf を呼ぶと
        # 同じ expanding stats を 3 回計算するため、1 回化で ~3x 高速になる。
        _df_precomputed: "pd.DataFrame | None" = None
        try:
            from keiba_ai.feature_engineering import add_derived_features as _adf_once  # type: ignore
            _t_fe = _time.time()
            _df_precomputed = await asyncio.to_thread(
                _adf_once,
                df_pred,
                _full_hist2,
                _training_df2,
                _speed_figures_df2,
            )
            logger.info(f"[analyze] add_derived_features: {_time.time()-_t_fe:.2f}s (1回, 3モデル共有)")
        except Exception as _fe_e:
            logger.warning(f"[analyze] add_derived_features 事前計算失敗 → 各モデルで個別計算: {_fe_e}")
            _df_precomputed = None

        # ModelPredictor: per-model feature build（_df_precomputed があれば _adf スキップ）
        predictor = ModelPredictor(bundle, model_path)
        X_pred = await asyncio.to_thread(
            predictor.build_features, df_pred, _full_hist2, _training_df2, _speed_figures_df2,
            _df_precomputed,
        )

        # ModelPredictor: per-model scoring（ターゲット種別に応じて自動選択）
        import numpy as _np2
        _wp_raw, win_probs = predictor.predict_scores(X_pred)
        _bundle_target = predictor.target
        _wp_sum = win_probs.sum()
        _wp_norm = (win_probs / _wp_sum) if _wp_sum > 0 else win_probs

        # ── place3 モデルによる複勝圏確率 ──────────────────────────────────
        _place3_probs: "_np2.ndarray | None" = None
        _place3_norm: "_np2.ndarray | None" = None
        _place3_is_model = False
        try:
            # place3_model_id 指定時は指定モデルを優先、なければ最新を自動選択
            if request.place3_model_id:
                _p3_specified = MODELS_DIR / f"{request.place3_model_id}.joblib"
                if not _p3_specified.exists():
                    _p3_specified_local = _ensure_model_local(request.place3_model_id)
                    _p3_specified = Path(_p3_specified_local) if _p3_specified_local else _p3_specified
                _place3_model_files = [_p3_specified] if _p3_specified.exists() else []
            else:
                _place3_model_files = sorted(
                    MODELS_DIR.glob("model_place3_*.joblib"),
                    key=lambda _p: _p.stat().st_mtime, reverse=True,
                )
            if _place3_model_files:
                _sub_result = _predict_sub_model(
                    _place3_model_files[0], df_pred,
                    full_hist=_full_hist2, training_df=_training_df2, speed_figures_df=_speed_figures_df2,
                    _df_precomputed=_df_precomputed,
                )
                if _sub_result is not None:
                    _place3_probs, _place3_norm = _sub_result
                    _place3_is_model = True
                    logger.info(
                        f"[analyze] place3モデル ({_place3_model_files[0].name}) 適用: "
                        f"top={_place3_norm.max():.3f}"
                    )
        except Exception as _p3e:
            logger.warning(f"[analyze] place3モデルロード失敗: {_p3e}")

        # place3 モデル未存在 → Harville 近似: P(top3) ≈ min(1, 3 × p_norm)
        if _place3_norm is None:
            _place3_norm = _np2.minimum(1.0, 3.0 * _wp_norm)
            logger.debug("[analyze] place3モデル未存在 → Harville近似で複勝圏確率を推定")

        # ── アンサンブルスコア ──────────────────────────────────────────────
        # place3あり: win/speed + place3 の加重平均（既存ロジック）
        # place3なし: win ↔ speed_deviation クロスモデルで 50/50 ブレンド
        _cross_norm: "_np2.ndarray | None" = None
        if not _place3_is_model:
            try:
                if _bundle_target == "win":
                    _cross_files = sorted(MODELS_DIR.glob("model_speed_deviation_*.joblib"),
                                          key=lambda _p: _p.stat().st_mtime, reverse=True)
                else:
                    _cross_files = sorted(MODELS_DIR.glob("model_win_*.joblib"),
                                          key=lambda _p: _p.stat().st_mtime, reverse=True)
                if _cross_files:
                    _cr = _predict_sub_model(
                        _cross_files[0], df_pred,
                        full_hist=_full_hist2, training_df=_training_df2, speed_figures_df=_speed_figures_df2,
                        _df_precomputed=_df_precomputed,
                    )
                    if _cr is not None:
                        _, _cross_norm = _cr
                        logger.info(f"[analyze] クロスアンサンブル ({_cross_files[0].name}) 50/50")
            except Exception as _cre:
                logger.warning(f"[analyze] クロスモデルロード失敗: {_cre}")
        # アンサンブル: place3あり → 50/50、place3なし+クロスあり → 50/50、それ以外 → p_norm
        _ens_sub = _cross_norm if (not _place3_is_model and _cross_norm is not None) else (_place3_norm if _place3_is_model else None)
        ensemble_probs = _compute_ensemble(win_probs, _ens_sub, "speed_deviation")  # 常に 50/50

        # [fix] 再スクレイプで df_pred["odds"] が更新された場合、
        # _horse_records には反映されないため、horse_number をキーに逆引きマップを作成
        import pandas as _pd_odds
        _df_odds_lookup: dict = {}
        _df_popularity_lookup: dict = {}
        _has_odds_col = "odds" in df_pred.columns
        _has_pop_col = "popularity" in df_pred.columns
        if _has_odds_col or _has_pop_col:
            for _, _drow in df_pred.iterrows():
                _hn_key = _drow.get("horse_number") or _drow.get("horse_no")
                if _hn_key is None:
                    continue
                _hn_int = int(float(_hn_key))
                if _has_odds_col:
                    _ov = _drow.get("odds")
                    if _ov is not None and not _pd_odds.isna(_ov) and float(_ov) > 0:
                        _df_odds_lookup[_hn_int] = float(_ov)
                if _has_pop_col:
                    _pv = _drow.get("popularity")
                    if _pv is not None and not _pd_odds.isna(_pv) and float(_pv) > 0:
                        _df_popularity_lookup[_hn_int] = int(float(_pv))

        predictions = []
        for i, _hr in enumerate(_horse_records):
            _horse_num = _hr.get("horse_number") or _hr.get("horse_no") or (i + 1)
            # df_pred の再スクレイプ済みオッズを優先、なければ _horse_records から取得
            _odds_float: float | None = _df_odds_lookup.get(int(_horse_num))
            if _odds_float is None:
                # INV-02: `or` による 0.0 falsy 誤判定を防ぐため is not None で判定
                _raw_odds = _hr.get("odds") if _hr.get("odds") is not None else _hr.get("win_odds")
                try:
                    _odds_float = float(_raw_odds) if _raw_odds not in (None, "", "---", 0.0) else None
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
                "bracket_number": _hr.get("bracket_number"),
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

            _exp = explain_prediction_reason(
                prediction=predictions[-1],
                scenario=_scenario_payload,
                race_info=race_info,
            )
            predictions[-1]["scenario_reason"] = _exp.get("reason")
            predictions[-1]["scenario_reasons"] = _exp.get("reasons")
            predictions[-1]["scenario_confidence"] = _exp.get("confidence")
            predictions[-1]["scenario_fit"] = _exp.get("scenario_fit")
            predictions[-1]["winning_pattern"] = _exp.get("winning_pattern")
            predictions[-1]["expected_pace"] = _exp.get("pace")
            predictions[-1]["expected_bias"] = _exp.get("bias")

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

        # Prediction Registry へ非同期保存（モデル/実験/特徴量バージョンを追跡）
        try:
            _prediction_id = f"pred_{request.race_id}_{uuid.uuid4().hex[:10]}"
            _model_id_reg = str(bundle.get("model_id", bundle.get("created_at", "unknown")))
            _actual_model_id = str(model_path.stem)
            _mlops_store = MLOpsStore()
            _model_meta = _mlops_store.get_latest_model_meta(_model_id_reg) or {}
            _quality_gate = {
                "feature_quality_score": _model_meta.get("feature_quality_score"),
                "feature_store_version": _model_meta.get("feature_store_version"),
            }
            _top_pred = (result.get("predictions") or [{}])[0] if (result.get("predictions") or []) else {}
            _metadata = {
                "race_name": race_info.get("race_name", ""),
                "venue": race_info.get("venue", ""),
                "risk_mode": request.risk_mode,
                "bankroll": int(request.bankroll),
                "best_bet_type": result.get("best_bet_type", ""),
                "recommendation": result.get("recommendation", {}),
                "routing": {
                    "selected_model_id": str(_router_meta.get("selected_model_id") or _actual_model_id),
                    "route_type": str(_router_meta.get("route_type") or ""),
                    "matched_scenario_key": str(_router_meta.get("matched_scenario_key") or ""),
                    "matched_scenario_value": str(_router_meta.get("matched_scenario_value") or ""),
                    "router_reason": str(_router_meta.get("router_reason") or ""),
                    "fallback_used": bool(_router_meta.get("fallback_used")),
                    "router_mode": str(_req_router_mode),
                    "effective_router_mode": str(_effective_router_mode),
                    "actual_model_id": _actual_model_id,
                    "canary_percent": int(_canary_percent),
                    "canary_bucket": int(_canary_bucket),
                    "canary_selected": bool(_canary_selected),
                    "shadow_selected_model_id": str(_shadow_router_meta.get("shadow_selected_model_id") or ""),
                    "shadow_route_type": str(_shadow_router_meta.get("shadow_route_type") or ""),
                    "shadow_matched_scenario_key": str(_shadow_router_meta.get("shadow_matched_scenario_key") or ""),
                    "shadow_matched_scenario_value": str(_shadow_router_meta.get("shadow_matched_scenario_value") or ""),
                    "shadow_router_reason": str(_shadow_router_meta.get("shadow_router_reason") or ""),
                    "shadow_fallback_used": bool(_shadow_router_meta.get("shadow_fallback_used")),
                },
                "scenario": {
                    "scenario_id": _scenario_graph.get("scenario_id"),
                    "scenario_hash": _scenario_graph.get("scenario_hash"),
                    "scenario_features": _scenario_features,
                    "reason": _top_pred.get("scenario_reason", ""),
                    "confidence": _top_pred.get("scenario_confidence"),
                    "winning_pattern": _scenario_payload.get("winning_pattern", ""),
                    "pace": _scenario_payload.get("expected_pace", ""),
                    "bias": _scenario_payload.get("expected_bias", ""),
                    "graph": _scenario_graph,
                },
            }
            asyncio.create_task(asyncio.to_thread(
                _mlops_store.record_prediction_run,
                prediction_id=_prediction_id,
                race_id=str(request.race_id),
                race_date=str(race_info.get("date", "")),
                model_id=_model_id_reg,
                experiment_id=(str(_model_meta.get("experiment_id", "")) if _model_meta else None),
                feature_store_version=(str(_model_meta.get("feature_store_version", "")) if _model_meta else None),
                prediction_version="v1",
                quality_gate=_quality_gate,
                metadata=_metadata,
                predictions=result.get("predictions", []),
            ))
        except Exception as _pred_reg_err:
            logger.warning(f"[prediction_registry] save failed: {_pred_reg_err}")
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
