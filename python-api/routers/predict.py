"""
予測・レース分析エンドポイント
POST /api/predict
POST /api/analyze_race
"""
from __future__ import annotations

import json
import os
import re
import traceback
from functools import lru_cache
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request, Response

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
from routers.predict_data_refresh import (  # type: ignore
    merge_fresh_pre_race_data,
    race_metadata_invalid,
)
from services.race_snapshot import (  # type: ignore
    fetch_fresh_race_snapshot,
    save_valid_race_snapshot,
    stored_rows_need_refresh,
)
from betting.value_policy import POLICY_VERSION, build_live_context  # type: ignore
from training.db_context_registry import SHARED_PIPELINES  # type: ignore

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
def _load_prediction_history(
    current_df: "pd.DataFrame", excluded_race_ids: set[str]
) -> "pd.DataFrame":
    """Load a request-scoped history frame without retaining the full DB."""
    try:
        from keiba_ai.db_ultimate_loader import load_prediction_history_frame  # type: ignore

        history = load_prediction_history_frame(
            ULTIMATE_DB,
            current_df,
            excluded_race_ids=excluded_race_ids,
        )
        memory_mb = float(history.memory_usage(index=True, deep=True).sum()) / (1024 ** 2)
        logger.info(
            "prediction history scoped load: rows=%s columns=%s memory_mb=%.1f",
            len(history),
            len(history.columns),
            memory_mb,
        )
        return history
    except Exception as exc:
        logger.warning(f"prediction history scoped load failed: {exc}")
        import pandas as _pd
        return _pd.DataFrame()
# レース後確定フィールド（keiba_ai.constants.FUTURE_FIELDS を参照）
POST_RACE_FIELDS = FUTURE_FIELDS

# ── モジュールレベルヘルパー ──────────────────────────────────────────

def _resolve_model_path(model_id: "str | None") -> "Path":
    """model_id 指定 → アクティブモデル → latest の順でモデルパスを解決する。"""
    from app_config import list_models_from_supabase  # type: ignore
    if model_id:
        candidate = _local_candidate_spec(model_id=model_id)
        if candidate is not None:
            # Research candidates are explicit selections only, never latest
            # or active fallbacks, and never a downloaded/unsigned artifact.
            return Path(candidate["source_path"])
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


def _resolve_saved_model_path(model_id: "str | None") -> "Path":
    """Resolve local artifacts only; freshness checks/reruns never download."""
    selected = model_id or get_active_model_id()
    if selected:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", selected):
            raise HTTPException(status_code=400, detail="モデルIDが不正です")
        candidate = _local_candidate_spec(model_id=selected)
        if candidate is not None:
            path = Path(candidate["source_path"])
        else:
            path = MODELS_DIR / f"{selected}.joblib"
            if not path.is_file():
                matches = sorted(MODELS_DIR.glob(f"*{selected}*.joblib"))
                if len(matches) == 1:
                    path = matches[0]
        if path.is_file():
            return path
        raise HTTPException(status_code=404, detail="モデルがこのPCにありません。保存済みモデルを選択してください。")
    path = get_latest_model()
    if path is None:
        raise HTTPException(status_code=404, detail="保存済みの学習モデルがありません")
    return path


@router.get("/api/prediction-freshness")
async def prediction_freshness(response: Response, model_id: str | None = Query(None, max_length=200)):
    """Authenticated, read-only revision check without predictions or quota."""
    from services.prediction_freshness import freshness_metadata
    response.headers["Cache-Control"] = "no-store"
    model_path = await asyncio.to_thread(_resolve_saved_model_path, model_id)
    if not Path(ULTIMATE_DB).is_file():
        raise HTTPException(status_code=503, detail="保存済みDBを確認できません")
    try:
        return await asyncio.to_thread(
            freshness_metadata, db_path=ULTIMATE_DB, model_path=model_path, source="saved_db",
        )
    except OSError as exc:
        raise HTTPException(status_code=503, detail="保存済みデータとモデルの更新状態を確認できません") from exc


def _local_candidate_spec(*, model_id: str | None = None, path: str | Path | None = None) -> dict | None:
    from app_config import APP_ENV
    if APP_ENV not in {"development", "local", "test"}:
        return None
    from services.local_candidate_input import candidate_specs
    for spec in candidate_specs():
        if (model_id is not None and spec["model_id"] == model_id
                or path is not None and Path(spec["source_path"]).resolve() == Path(path).resolve()):
            return spec
    return None


def _load_served_model_bundle(path: Path) -> dict:
    if path.name.startswith("model_win_reference_"):
        from services.win_nll_reference_input import load_published_bundle
        return dict(load_published_bundle(path), model_id=path.stem)
    candidate = _local_candidate_spec(path=path)
    if candidate is not None:
        from services.local_candidate_input import load_candidate_bundle
        return load_candidate_bundle(path, model_id=candidate["model_id"])
    from services.db_shared_input import PREFIX, validate_published_artifact
    if path.name.startswith(PREFIX):
        digest = validate_published_artifact(path)
        bundle = load_model_bundle(path)
        if validate_published_artifact(path) != digest:
            raise ValueError("shared-artifact-changed-during-load")
        bundle["_shared_artifact_sha256"] = digest
        return bundle
    return load_model_bundle(path)


def _drop_non_features(df: "pd.DataFrame") -> "pd.DataFrame":
    """未来情報・識別子・ object型列を推論入力から除外する（INV-01）。"""
    _exclude = (
        set(POST_RACE_FIELDS)
        | {"race_id", "horse_id", "jockey_id", "trainer_id", "owner_id", "finish_position",
           "win", "place"}
    )
    df = df.drop([c for c in _exclude if c in df.columns], axis=1)
    obj_cols = [column for column in df.columns if (
        pd.api.types.is_object_dtype(df[column].dtype)
        or pd.api.types.is_string_dtype(df[column].dtype)
    )]
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
    - 過半数の未計算列は HTTP 500、それ以下は既存のNaN補完と参考表示を維持
    - 入力構造と学習時ソースの不一致はdiagnosticsで公開（同等性を保証しない）
    - optimizer.transform 失敗は HTTP 500 を返す（フォールバックなし）
    - スコア変換はターゲット種別（分類/回帰/ランカー）ごとに自動決定
    """

    def __init__(self, bundle: dict, model_path: "str | Path | None" = None, *, inference_threads: int | None = None, db_path: str | Path | None = None, race_info: dict | None = None, trusted_input_context: dict | None = None):
        self.bundle = bundle
        self.model_name: str = Path(model_path).name if model_path else bundle.get("created_at", "unknown")
        self.target: str = bundle.get("target", "win")
        self.model = bundle["model"]
        self.optimizer = bundle.get("optimizer")
        self.calibrator = bundle.get("calibrator")
        self.calibrator_applied = False
        self.feature_columns: list[str] = bundle.get("feature_columns", [])
        self.pipeline_config: dict = bundle.get("pipeline_config", {})
        self.is_ranker: bool = bool(bundle.get("_is_ranker", False))
        self.inference_threads = inference_threads
        self.input_diagnostics: dict | None = None
        self.db_path = Path(db_path) if db_path is not None else ULTIMATE_DB
        self.race_info = race_info
        self.model_path = Path(model_path) if model_path else None
        self.trusted_input_context = trusted_input_context

    def _thread_options(self, model: object) -> dict:
        # Only LightGBM supports this keyword; retain non-LightGBM contracts.
        if self.inference_threads is not None and type(model).__module__.startswith("lightgbm"):
            return {"num_threads": self.inference_threads}
        return {}

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
        - feature_columns 過半数不足 → HTTP 500（不足列名を明示）
        - それ以下の不足 → 既存NaN補完。欠落をdiagnosticsに保持して参考表示
        """
        if self.bundle.get("_win_nll_reference") is True:
            from services.win_nll_reference_input import build_input
            features, self.input_diagnostics = build_input(
                self.bundle, df, db_path=self.db_path, race_info=self.race_info,
                model_path=self.model_path,
            )
            return features
        if self.bundle.get("_local_candidate"):
            from services.local_candidate_input import build_candidate_input
            features, self.input_diagnostics = build_candidate_input(
                self.bundle, df, db_path=self.db_path, race_info=self.race_info,
            )
            return features
        if self.bundle.get("feature_pipeline") in SHARED_PIPELINES:
            from services.db_shared_input import build_shared_input
            features, self.input_diagnostics = build_shared_input(
                self.bundle, df, db_path=self.db_path, race_info=self.race_info,
                model_path=self.model_path, trusted_context=self.trusted_input_context,
            )
            return features
        from keiba_ai.feature_engineering import add_derived_features as _adf  # type: ignore
        from services.prediction_input_contract import describe_input
        self.input_diagnostics = None

        # Step 1: 派生特徴量エンジニアリング（部分失敗は許容 → strict check で捕捉）
        try:
            df = _adf(df, full_history_df=full_hist if full_hist is not None else df)
        except Exception as _e:
            logger.warning(f"[ModelPredictor:{self.target}] add_derived_features 部分失敗: {_e}")
        df = df.loc[:, ~df.columns.duplicated()]
        engineered_df = df.copy()

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

        # Bundles created before the optimizer pruning update can legitimately
        # require numeric engineered fields (for example age and season) that
        # the current optimizer removes. Restore only fields that were really
        # computed from the pre-race input; never invent or zero-fill absent
        # history. The final bundle-order verification remains authoritative.
        restored_columns = []
        for required_column in self.feature_columns:
            if required_column in df.columns or required_column not in engineered_df.columns:
                continue
            source = engineered_df[required_column]
            if pd.api.types.is_numeric_dtype(source) or pd.api.types.is_bool_dtype(source):
                df[required_column] = pd.to_numeric(source, errors="coerce")
                restored_columns.append(required_column)

        # Step 3: 未来情報・識別子列を除外
        X = _drop_non_features(df)

        # Step 4: 特徴量チェック（50%超の欠損は再学習を促す HTTP 500、それ以下は NaN 補完で続行）
        if self.feature_columns:
            from app_config import verify_feature_columns  # type: ignore
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
            X = verify_feature_columns(X, self.bundle)
            self.input_diagnostics = describe_input(
                self.bundle, X, missing_columns=missing, restored_columns=restored_columns,
            )
            return X
        self.input_diagnostics = describe_input(
            self.bundle, X, missing_columns=[], restored_columns=restored_columns,
        )
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
        if self.bundle.get("_win_nll_reference") is True:
            from services.win_nll_reference_input import predict
            result = predict(self.bundle, X, model_path=self.model_path)
            # Rank by the actually served, conditioned candidate probability,
            # not the residual or pre-calibration rank. Live calibration and
            # purchase eligibility remain unverified, despite the CAL fit.
            self.calibrator_applied = False
            return result["probability"].copy(), result["probability"]
        if self.bundle.get("_local_candidate"):
            from services.local_candidate_input import predict_candidate_scores
            self.calibrator_applied = False
            return predict_candidate_scores(self.bundle, X, num_threads=self.inference_threads or 2)
        if self.bundle.get("feature_pipeline") in SHARED_PIPELINES:
            from training.db_context_registry import fit_module
            profile = fit_module(self.bundle["feature_pipeline"])
            raw, probabilities = profile.predict_encoded_shared_scores(
                self.bundle, X, n_threads=self.inference_threads or 2,
            )
            if probabilities is None:
                weights = np.exp(raw - raw.max())
                probabilities = weights / weights.sum()
            self.calibrator_applied = bool(
                self.bundle.get("probability_validation", {}).get("validated") is True
                and self.bundle.get("calibrator") is not None
            )
            return raw, probabilities
        import numpy as _np_p
        self.calibrator_applied = False
        raw = _np_p.array(self.model.predict(X, **self._thread_options(self.model)), dtype=float)

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
                    calibrated_proba = _np_p.asarray(self.calibrator.predict(proba), dtype=float)
                    if (calibrated_proba.shape != raw.shape
                        or not _np_p.isfinite(calibrated_proba).all()
                        or not ((calibrated_proba >= 0) & (calibrated_proba <= 1)).all()
                        or calibrated_proba.sum() <= 0):
                        raise ValueError("校正器の確率出力が不正です")
                    proba = calibrated_proba
                    self.calibrator_applied = True
                except Exception as _cal_err:
                    logger.warning(
                        f"[ModelPredictor:{self.target}] キャリブレーション失敗: {_cal_err}"
                    )

        s = proba.sum()
        proba_norm = (proba / s) if s > 0 else proba
        return raw, proba_norm

    def explain_scores(
        self,
        X: "pd.DataFrame",
        top_n: int = 6,
    ) -> "list[dict]":
        """LightGBM TreeSHAP で各行の予測スコアへの寄与を返す。

        ``pred_contrib`` は通常の予測をやり直さず、同じ特徴量行に対する
        TreeSHAP 寄与度だけを高速に計算する。未対応モデルでは例外を返し、
        呼び出し側が説明なしで通常予測を継続する。
        """
        if self.bundle.get("_win_nll_reference") is True:
            # Residual TreeSHAP alone would not explain the market-conditioned
            # winner probability. Do not mislabel it as a speed contribution.
            raise ValueError("win-nll-reference-probability-explanation-not-validated")
        if self.bundle.get("_local_candidate"):
            from services.local_candidate_input import explain_candidate_scores
            return explain_candidate_scores(self.bundle, X, top_n=top_n, num_threads=self.inference_threads or 2)
        import numpy as _np_exp

        booster = getattr(self.model, "booster_", None) or self.model
        if not hasattr(booster, "predict"):
            raise TypeError("このモデルは特徴量寄与度の計算に対応していません")

        raw_contrib = booster.predict(X, pred_contrib=True, **self._thread_options(booster))
        contrib = _np_exp.asarray(raw_contrib, dtype=float)
        expected_width = len(X.columns) + 1
        if contrib.ndim != 2 or contrib.shape != (len(X), expected_width):
            raise ValueError(
                "特徴量寄与度の形が不正です: "
                f"actual={contrib.shape}, expected=({len(X)}, {expected_width})"
            )

        descriptions = _feature_description_map()
        explanations: list[dict] = []
        for row_index in range(len(X)):
            impacts = contrib[row_index, :-1]
            total_abs = float(_np_exp.abs(impacts).sum()) or 1.0
            ranked_indices = _np_exp.argsort(_np_exp.abs(impacts))[::-1]
            features: list[dict] = []
            for feature_index in ranked_indices:
                impact = float(impacts[feature_index])
                if not _np_exp.isfinite(impact) or abs(impact) < 1e-12:
                    continue
                feature_name = str(X.columns[int(feature_index)])
                description = descriptions.get(feature_name, "")
                raw_value = X.iloc[row_index, int(feature_index)]
                if raw_value is None or not _np_exp.isscalar(raw_value):
                    value: object | None = None
                elif isinstance(raw_value, (str, bool)):
                    value = raw_value
                else:
                    try:
                        numeric_value = float(raw_value)
                        value = round(numeric_value, 6) if _np_exp.isfinite(numeric_value) else None
                    except (TypeError, ValueError):
                        value = str(raw_value)[:80]
                features.append({
                    "feature": feature_name,
                    "label": _public_feature_label(feature_name, description),
                    "description": description,
                    "value": value,
                    "contribution": round(impact, 8),
                    "impact_pct": round(abs(impact) / total_abs * 100.0, 2),
                    "direction": "positive" if impact > 0 else "negative",
                })
                if len(features) >= max(1, min(top_n, 10)):
                    break
            explanations.append({
                "method": "tree_shap",
                "base_value": round(float(contrib[row_index, -1]), 8),
                "features": features,
            })
        return explanations


@lru_cache(maxsize=1)
def _feature_description_map() -> "dict[str, str]":
    """特徴量カタログの説明を、表示用ラベル生成に再利用する。"""
    try:
        from routers.feature_analysis import _build_desc_map, _get_catalog  # type: ignore

        descriptions = _build_desc_map(_get_catalog())
        from training.db_context_features import FEATURE_LABELS
        return {**descriptions, **FEATURE_LABELS}
    except Exception as exc:
        logger.warning(f"特徴量カタログの読み込みに失敗: {exc}")
        return {}


def _public_feature_label(feature_name: str, description: str) -> str:
    """技術的な列名を、短い日本語ラベルへ変換する。"""
    label = re.split(r"[（(]", description, maxsplit=1)[0].strip()
    if not label or len(label) > 36:
        label = feature_name.replace("_encoded", "").replace("_", " ").strip()
    return label[:36]


@router.post("/api/predict", response_model=PredictResponse)
async def predict(request: PredictRequest, http_req: Request):
    """学習済みモデルを使用して予測を実行（free=10回/月, premium=無制限）"""
    await check_and_consume_pred_count(http_req)
    try:
        # モデルロード → ModelPredictor 初期化（モデル固有パイプラインを決定）
        model_path = await asyncio.to_thread(_resolve_model_path, request.model_id)
        _model_stat = model_path.stat()
        _model_identity = (_model_stat.st_mtime_ns, _model_stat.st_size)
        bundle = await asyncio.to_thread(_load_served_model_bundle, model_path)
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

        from services.prediction_compute import run_prediction_compute, ensure_model_identity
        computed = await run_prediction_compute(
            frame=df, db_path=str(ULTIMATE_DB), model_path=str(model_path),
            excluded_race_ids={str(value) for value in df['race_id'].dropna()},
            expected_model_identity=_model_identity,
            include_explanation=False,
            race_info=request.race_info,
        )
        p_raw, p_norm = computed["raw"], computed["probabilities"]
        ensure_model_identity(model_path, _model_identity)

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
            input_diagnostics=computed.get("input_diagnostics"),
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"予測中にエラーが発生: {str(e)}")


async def _analyze_race_impl(request: AnalyzeRaceRequest):
    """レース分析と購入推奨エンドポイント"""
    _observation_started_at = _time.perf_counter()
    # Bind both caches and queued workers to the resolved artifact, including
    # default-model changes. Never reuse a previous active model's response.
    model_path = await asyncio.to_thread(
        _resolve_saved_model_path if request.data_refresh == "saved_db" else _resolve_model_path,
        request.model_id,
    )
    if model_path.name.startswith("model_win_reference_"):
        # Cached responses must not outlive the publication or frozen input
        # source contract. Check before any response-cache early return.
        from services.win_nll_reference_input import validate_published_artifact
        await asyncio.to_thread(validate_published_artifact, model_path)
    _model_stat = model_path.stat()
    _model_identity = (_model_stat.st_mtime_ns, _model_stat.st_size)
    _candidate_spec = _local_candidate_spec(path=model_path)
    if _candidate_spec is not None:
        from services.local_candidate_input import validate_candidate_sources
        _validated_spec = await asyncio.to_thread(validate_candidate_sources, model_path)
        if _validated_spec["model_id"] != _candidate_spec["model_id"]:
            raise ValueError("local-candidate-selected-model-id-mismatch")
    from services.db_shared_input import PREFIX, verify_bundle_sources, shared_pre_race_input_needs_refresh
    _shared_model = model_path.name.startswith(PREFIX)
    if _shared_model:
        # A cached skip can still carry EV/probability labels. Revalidate the
        # exact publisher/hash and regenerate time-sensitive DB evidence.
        _shared_bundle = await asyncio.to_thread(_load_served_model_bundle, model_path)
        await asyncio.to_thread(verify_bundle_sources, _shared_bundle)
    from services.prediction_freshness import data_revision, model_runtime_revision, freshness_metadata
    _initial_data_revision = await asyncio.to_thread(data_revision, ULTIMATE_DB)
    _runtime_revision = await asyncio.to_thread(model_runtime_revision, model_path)
    # New/repaired DB history, WAL writes, model and serving changes invalidate
    # old responses. An explicit saved-DB rerun always bypasses response reuse.
    _cache_key = (
        f"{request.race_id}:{model_path}:{_model_identity}:{request.bankroll}:"
        f"{request.risk_mode}:{int(request.include_explanation)}:"
        f"{request.min_ev}:{request.use_kelly}:{request.dynamic_unit}:{POLICY_VERSION}:"
        f"{request.data_refresh}:{_initial_data_revision}:{_runtime_revision}"
    )
    _now = _time.time()
    _cached = None if request.force_recompute else _ANALYZE_CACHE.get(_cache_key)
    if _cached and (_now - _cached[0]) < _ANALYZE_CACHE_TTL:
        _cached_predictions = _cached[1].get("predictions", [])
        _cached_market_complete = bool(_cached_predictions) and all(
            prediction.get("odds") not in (None, 0, 0.0, "")
            for prediction in _cached_predictions
        )
        _cached_policy = _cached[1].get("recommendation", {})
        # A purchase recommendation must be rebuilt from fresh evidence; do
        # not serve a five-minute-old stake across the start of a race.
        if _cached_market_complete and _cached_policy.get("decision") == "skip" and not _shared_model:
            logger.info(f"[cache hit] analyze_race {request.race_id} (age={(int(_now - _cached[0]))}s)")
            data = {**_cached[1]}
            data["freshness"] = await asyncio.to_thread(
                freshness_metadata, db_path=ULTIMATE_DB, model_path=model_path,
                source=request.data_refresh, cache_hit=True,
                initial_revision=_initial_data_revision,
                generated_at=(_cached[1].get("freshness") or {}).get("generated_at"),
            )
            if (not data["freshness"]["data_changed_during_prediction"]
                    and data["freshness"]["model_runtime_revision"] == _runtime_revision):
                return AnalyzeRaceResponse(**data)
        logger.info(f"[cache bypass] analyze_race {request.race_id}: market data incomplete")

    try:
        from betting.strategy import BettingRecommender  # type: ignore

        # Phase 0: 常に ultimate DB を使用（87特徴量モード固定）
        db_path = ULTIMATE_DB

        # モデルロード（model_id指定 → active model pointer → latest の順）
        bundle = await asyncio.to_thread(_load_served_model_bundle, model_path)
        _row_explanations: list[dict] | None = None

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
                if request.data_refresh == "saved_db":
                    raise HTTPException(status_code=404, detail="保存済みDBにレースがありません。データ取得後に再予測してください。")
                # DBにない場合 → オンデマンドスクレイプして保存してから再試行
                logger.info(f"[analyze] レース {request.race_id} がDBに未登録 → オンデマンドスクレイプ開始")
                try:
                    import aiohttp as _aiohttp
                    from scraping.constants import get_random_headers  # type: ignore
                    _timeout = _aiohttp.ClientTimeout(total=60)
                    async with _aiohttp.ClientSession(headers=get_random_headers(), timeout=_timeout) as _sess:
                        _scraped = await fetch_fresh_race_snapshot(
                            _sess,
                            request.race_id,
                        )
                    if not _scraped or not _scraped.get("horses"):
                        raise HTTPException(
                            status_code=404,
                            detail=f"レース {request.race_id} のスクレイプに失敗しました（データなし）",
                        )
                    save_valid_race_snapshot(_scraped, ULTIMATE_DB, request.race_id)
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
            _cur.execute(
                "SELECT data FROM race_results_ultimate WHERE race_id = ? ORDER BY json_extract(data, '$.horse_number')",
                (request.race_id,),
            )
            _hrows = _cur.fetchall()
            _conn.close()
            if not _hrows:
                if request.data_refresh == "saved_db":
                    raise HTTPException(status_code=404, detail="保存済みDBに出走馬がありません。データ取得後に再予測してください。")
                # 馬データなし → レース情報はあるが horse データが未登録のためオンデマンド再スクレイプ
                logger.info(f"[analyze] レース {request.race_id} は races_ultimate にあるが horse データなし → 再スクレイプ")
                try:
                    import aiohttp as _aiohttp
                    from scraping.constants import get_random_headers  # type: ignore
                    _timeout = _aiohttp.ClientTimeout(total=60)
                    async with _aiohttp.ClientSession(headers=get_random_headers(), timeout=_timeout) as _sess:
                        _scraped = await fetch_fresh_race_snapshot(
                            _sess,
                            request.race_id,
                            _race_data.get("date", ""),
                        )
                    if not _scraped or not _scraped.get("horses"):
                        raise HTTPException(
                            status_code=404,
                            detail=f"レース {request.race_id} の馬データが見つかりません（スクレイプでも取得できませんでした）",
                        )
                    save_valid_race_snapshot(_scraped, ULTIMATE_DB, request.race_id)
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

            # Existing Render SQLite rows may be an incomplete pre-publication
            # snapshot.  Refresh the whole race (not odds alone), validate it,
            # and replace metadata + every horse row in one transaction.
            # Only a published, source-verified shared DB model adds a freshness
            # reason. Preserve legacy/past/result repair behavior unchanged.
            _shared_stale_input = _shared_model and shared_pre_race_input_needs_refresh(
                bundle, request.race_id, _race_data, _hrows, trusted_db_card=True,
            )
            if request.data_refresh != "saved_db" and (
                stored_rows_need_refresh(request.race_id, _race_data, _hrows) or _shared_stale_input
            ):
                try:
                    import aiohttp as _aiohttp_refresh
                    from scraping.constants import get_random_headers as _refresh_headers  # type: ignore

                    _refresh_timeout = _aiohttp_refresh.ClientTimeout(total=60)
                    async with _aiohttp_refresh.ClientSession(
                        headers=_refresh_headers(),
                        timeout=_refresh_timeout,
                    ) as _refresh_session:
                        _fresh_snapshot = await fetch_fresh_race_snapshot(
                            _refresh_session,
                            request.race_id,
                            _race_data.get("date", ""),
                        )
                    if _fresh_snapshot:
                        save_valid_race_snapshot(_fresh_snapshot, ULTIMATE_DB, request.race_id)
                        _race_data = dict(_fresh_snapshot["race_info"])
                        _hrows = [
                            (json.dumps(horse, ensure_ascii=False),)
                            for horse in _fresh_snapshot["horses"]
                        ]
                        logger.info(
                            f"[analyze] {request.race_id}: fresh race snapshotを原子的に更新 "
                            f"({len(_fresh_snapshot['horses'])}頭, distance={_race_data.get('distance')})"
                        )
                except Exception as _refresh_error:
                    logger.warning(
                        f"[analyze] {request.race_id}: fresh race snapshot更新失敗; "
                        f"品質ゲートで旧入力を拒否します: {_refresh_error}"
                    )

            # Rebuild from the final in-memory snapshot. This also covers the
            # "race row exists but horse rows were missing" repair path above.
            race_info = {
                **_race_data,
                "race_id": request.race_id,
                "race_name": _race_data.get("race_name", ""),
                "venue": _race_data.get("venue", ""),
                "date": _race_data.get("date", ""),
                "distance": _race_data.get("distance", 0),
                "track_type": _race_data.get("track_type", ""),
                "weather": _race_data.get("weather", ""),
                "field_condition": _race_data.get("field_condition", ""),
                "num_horses": _race_data.get("num_horses", 0),
                "post_time": _race_data.get("post_time", ""),
                "data_observed_at": _race_data.get("data_observed_at"),
                "odds_status": _race_data.get("odds_status"),
            }

            _horse_records = []
            for _hr in _hrows:
                _hd = json.loads(_hr[0])
                _hd["race_id"] = request.race_id
                for _k, _v in _race_data.items():
                    if _k not in _hd or _hd[_k] is None:
                        _hd[_k] = _v
                _horse_records.append(_hd)

            _betting_source_records = _horse_records

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
                if (bundle.get("_local_candidate") or bundle.get("_win_nll_reference") is True or bundle.get("feature_pipeline") in SHARED_PIPELINES) and _id_col == "horse_id":
                    # Never substitute a profile URL/name for canonical identity.
                    df_pred[_id_col] = [row.get("horse_id") or "" for row in _horse_records]
                    continue
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

            # Point-in-time history requires the actual calendar date.  Race
            # metadata stores it as ``date`` while the feature contract uses
            # ``race_date``.
            if "race_date" not in df_pred.columns:
                df_pred["race_date"] = race_info.get("date")
            else:
                df_pred["race_date"] = df_pred["race_date"].fillna(
                    race_info.get("date")
                )

            # [fix] DB保存時にodds=Noneだった出馬表データを再スクレイプして最新オッズを補完
            # 過去レース・当日レース(race_date <= today)はdb.netkeiba.com結果ページから確定オッズを取得
            # 未来レースはshutubaページから暫定オッズを取得
            _odds_missing = (
                "odds" not in df_pred.columns
                or df_pred["odds"].isna().all()
                or (df_pred["odds"].fillna(0) == 0).all()  # 全馬 0.0 もオッズ未取得扱い
            )
            _metadata_invalid = race_metadata_invalid(df_pred)
            if request.data_refresh != "saved_db" and (_odds_missing or _metadata_invalid):
                try:
                    import aiohttp as _aiohttp2
                    from scraping.constants import get_random_headers as _get_rh  # type: ignore
                    _timeout2 = _aiohttp2.ClientTimeout(total=60)
                    async with _aiohttp2.ClientSession(headers=_get_rh(), timeout=_timeout2) as _sess2:
                        _fresh = await fetch_fresh_race_snapshot(
                            _sess2,
                            request.race_id,
                            race_info.get("date", ""),
                        )
                    if _fresh and _fresh.get("horses"):
                        _fresh_changed = merge_fresh_pre_race_data(
                            df_pred,
                            race_info,
                            _fresh,
                        )
                        # Bind new market values to the snapshot that supplied
                        # them, not the old DB timestamp. No result fields are
                        # merged into the model features.
                        _betting_source_records = _fresh["horses"]
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
                                save_valid_race_snapshot(_fresh, ULTIMATE_DB, request.race_id)
                            except Exception:
                                pass
                            logger.info(f"[analyze] {request.race_id}: fresh snapshotでodds補完完了 ({len(_odds_map)}頭)")
                        else:
                            logger.info(f"[analyze] {request.race_id}: shutuba再スクレイプ完了だがoddはまだ未公開")
                        if _fresh_changed:
                            # Persist the same complete pre-race snapshot used
                            # by this request. This prevents a later request
                            # from reloading an old distance=0 record.
                            try:
                                _srtud(_fresh, ULTIMATE_DB, overwrite=True)
                            except Exception as _save_error:
                                logger.warning(
                                    f"[analyze] {request.race_id}: fresh race snapshot save failed: {_save_error}"
                                )
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

            # CPU-bound history/features/inference use isolated, bounded local
            # processes. External requests still use the shared provider pacer.
            from services.prediction_compute import run_prediction_compute, ensure_model_identity
            predictor = ModelPredictor(bundle, model_path)
            _place3_model_files = sorted(
                MODELS_DIR.glob("model_place3_*.joblib"),
                key=lambda path: path.stat().st_mtime, reverse=True,
            )
            _computed = await run_prediction_compute(
                frame=df_pred, db_path=str(ULTIMATE_DB), model_path=str(model_path),
                excluded_race_ids={request.race_id},
                expected_model_identity=_model_identity,
                include_explanation=request.include_explanation,
                race_info=race_info,
                trusted_input_context={
                    "trusted_db_card": True,
                    "race_has_result": any(row.get(key) not in (None, "", 0, "0")
                                           for row in _betting_source_records
                                           for key in ("finish_position", "finish")),
                },
                place3_model_path=(str(_place3_model_files[0]) if _place3_model_files and not bundle.get("_local_candidate") and bundle.get("_win_nll_reference") is not True and bundle.get("feature_pipeline") not in SHARED_PIPELINES else None),
            )
            if request.data_refresh == "saved_db" and (
                await asyncio.to_thread(data_revision, ULTIMATE_DB) != _initial_data_revision
            ):
                # Do not publish mixed-version card/history input or attach a
                # purchasing/performance snapshot after a concurrent repair.
                raise HTTPException(status_code=409, detail="予測中に保存済みデータが更新されました。データ取得が完了してから、最新データで再予測してください。")
            X_pred = _computed["features"]
            predictor.calibrator_applied = _computed["calibrator_applied"]
            predictor.input_diagnostics = _computed.get("input_diagnostics")
            import numpy as _np2
            _wp_raw, win_probs = _computed["raw"], _computed["probabilities"]
            _bundle_target = predictor.target
            _wp_sum = win_probs.sum()
            _wp_norm = (win_probs / _wp_sum) if _wp_sum > 0 else win_probs
            _row_explanations = _computed["explanations"]
            _place3_probs: "_np2.ndarray | None" = None
            _place3_norm: "_np2.ndarray | None" = None
            if _computed["place3"] is not None:
                _place3_probs, _place3_norm = _computed["place3"]
            _compute_timings = _computed["timings"]
            logger.info(
                "[prediction timing] race=%s history_rows=%s timing=%s",
                request.race_id, _computed["history_rows"], _compute_timings,
            )
            del _computed
            ensure_model_identity(model_path, _model_identity)

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
                    "explanation": (
                        _row_explanations[i]
                        if _row_explanations is not None and i < len(_row_explanations)
                        else None
                    ),
                })

            # [A1] ソートは p_raw 降順、predicted_rank 割り当て
            predictions.sort(key=lambda x: x.get("p_raw", 0), reverse=True)
            for _rank, _pred in enumerate(predictions, 1):
                _pred["predicted_rank"] = _rank

        recommender = BettingRecommender(
            bankroll=request.bankroll, risk_mode=request.risk_mode,
            use_kelly=request.use_kelly, dynamic_unit=request.dynamic_unit, min_ev=request.min_ev,
        )
        ensure_model_identity(model_path, _model_identity)
        _betting_context = build_live_context(
            model_path=model_path, predictor=predictor,
            source_records=_betting_source_records, feature_frame=X_pred,
        )
        result = recommender.analyze_and_recommend(predictions, race_info, context=_betting_context)

        _resp_data = dict(
            success=True,
            model_id=bundle.get("model_id", model_path.stem),
            input_diagnostics=predictor.input_diagnostics,
            explanation_method=("tree_shap" if _row_explanations else None),
            race_info=result["race_info"],
            pro_evaluation=result["pro_evaluation"],
            predictions=result["predictions"],
            bet_types=result["bet_types"],
            best_bet_type=result["best_bet_type"],
            best_bet_info=result["best_bet_info"],
            race_level=result["race_level"],
            recommendation=result["recommendation"],
            processing_timings={
                **_compute_timings,
                "elapsed_seconds": _time.perf_counter() - _observation_started_at,
            },
        )
        from betting.reference import build_reference_bets
        _resp_data["reference_bets"] = build_reference_bets(
            result["predictions"], runner_count=result["race_info"].get("num_horses"),
        )
        from app_config import APP_ENV
        from betting.reference_value import build_reference_expectation
        _resp_data["reference_expectation"] = build_reference_expectation(
            result["predictions"], result["race_info"], result["recommendation"],
            context=_betting_context, model_id=_resp_data["model_id"], environment=APP_ENV,
        )
        # Phase 3N strict observation capture is an explicit deployed-runtime
        # opt-in. Production is accepted only in the independently authorized
        # limited-observation mode; all other production configurations fail
        # closed before an observation write.
        # When enabled it is awaited before responding and fails closed: a
        # prediction is never claimed as observed unless PostgreSQL accepted the
        # append-only rows. Missing authoritative source timestamps are rejected.
        try:
            from observation.capture import capture_analyze_predictions  # type: ignore

            _observation_result = await asyncio.to_thread(
                capture_analyze_predictions,
                bundle=bundle,
                model_path=model_path,
                feature_frame=X_pred,
                source_records=_horse_records,
                race_id=request.race_id,
                race_info=result["race_info"],
                predictions=result["predictions"],
                latency_ms=(_time.perf_counter() - _observation_started_at) * 1000.0,
            )
            if _observation_result.get("enabled"):
                logger.info(
                    "[phase3n-observation] accepted predictions: inserted=%s duplicate=%s",
                    _observation_result["inserted"],
                    _observation_result["duplicates"],
                )
        except Exception as _observation_error:
            # Invalid/partial opt-in is also fail-closed. Only explicit false
            # values may bypass capture; this avoids a malformed switch turning
            # an intended Staging observation run into an unobserved response.
            _observation_switch = os.environ.get(
                "PHASE3N_OBSERVATION_ENABLED", ""
            ).strip().lower()
            if _observation_switch not in {"", "false", "0", "no", "off"}:
                _observation_code = getattr(
                    _observation_error,
                    "code",
                    type(_observation_error).__name__,
                )
                logger.error(
                    "[phase3n-observation] fail closed: %s",
                    _observation_code,
                )
                raise HTTPException(
                    status_code=503,
                    detail="strict observation capture failed closed",
                ) from _observation_error
            logger.debug("[phase3n-observation] disabled")
        ensure_model_identity(model_path, _model_identity)
        _resp_data["processing_timings"]["elapsed_seconds"] = (
            _time.perf_counter() - _observation_started_at
        )
        # Complete this derived display log before taking the revision. Paper
        # decisions, purchases and sealed forward records stay separate.
        try:
            _model_id_log = bundle.get("model_id", bundle.get("created_at", "unknown"))
            await asyncio.to_thread(
                _save_prediction_log,
                request.race_id, result["race_info"], result["predictions"],
                _model_id_log, str(ULTIMATE_DB)
            )
        except Exception as _log_err:
            logger.warning(f"[prediction_log] save failed: {_log_err}")
        _resp_data["freshness"] = await asyncio.to_thread(
            freshness_metadata, db_path=ULTIMATE_DB, model_path=model_path,
            source=request.data_refresh, initial_revision=_initial_data_revision,
        )
        if _resp_data["freshness"]["model_runtime_revision"] != _runtime_revision:
            raise HTTPException(status_code=503, detail="予測中にモデルまたは入力処理が更新されました。最新データで再予測してください。")
        if request.data_refresh == "saved_db" and _resp_data["freshness"]["data_changed_during_prediction"]:
            raise HTTPException(status_code=409, detail="予測中に保存済みデータが更新されました。データ取得が完了してから、最新データで再予測してください。")
        # Cache only when the DB did not change while the input was built. This
        # stamp is invalidation metadata, not a pre-race capture receipt.
        if _resp_data["freshness"]["data_changed_during_prediction"]:
            return AnalyzeRaceResponse(**_resp_data)
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


@router.post("/api/analyze_race", response_model=AnalyzeRaceResponse)
async def analyze_race(request: AnalyzeRaceRequest, http_req: Request):
    """単一レース分析（1リクエスト = 1 quota unit）。"""
    await check_and_consume_pred_count(http_req, units=1)
    from routers.performance import attach_performance_snapshot
    response = await _analyze_race_impl(request)
    return await attach_performance_snapshot(response, getattr(http_req.state, "user_id", None), request.race_id)


@router.post("/api/analyze_races_batch")
async def analyze_races_batch(request: BatchAnalyzeRequest, http_req: Request):
    """複数レースを一括分析（重複除外件数を事前に原子的に quota 消費）。"""
    unique_race_ids = list(dict.fromkeys(request.race_ids))
    if not unique_race_ids:
        raise HTTPException(status_code=400, detail="race_ids must contain at least one item")
    if len(unique_race_ids) > 100:
        raise HTTPException(status_code=400, detail="race_ids must contain at most 100 unique items")

    await check_and_consume_pred_count(http_req, units=len(unique_race_ids))

    from services.prediction_compute import worker_count
    slots = asyncio.Semaphore(worker_count())

    async def analyze_one(race_id: str) -> tuple[str, dict]:
        req = AnalyzeRaceRequest(
            race_id=race_id,
            model_id=request.model_id,
            include_explanation=request.include_explanation,
            force_recompute=request.force_recompute,
            data_refresh=request.data_refresh,
            bankroll=request.bankroll,
            risk_mode=request.risk_mode,
            use_kelly=request.use_kelly,
            dynamic_unit=request.dynamic_unit,
            min_ev=request.min_ev,
        )
        try:
            async with slots:
                resp = await _analyze_race_impl(req)
                from routers.performance import attach_performance_snapshot
                resp = await attach_performance_snapshot(resp, getattr(http_req.state, "user_id", None), race_id)
                return race_id, {"success": True, "data": resp.model_dump()}
        except HTTPException as e:
            return race_id, {"success": False, "error": e.detail}
        except Exception as e:
            return race_id, {"success": False, "error": str(e)}

    # Gather preserves request order; one failed race does not cancel siblings.
    results = dict(await asyncio.gather(*(analyze_one(race_id) for race_id in unique_race_ids)))
    return {
        "results": results,
        "total": len(unique_race_ids),
        "requested_total": len(request.race_ids),
        "charged_units": len(unique_race_ids),
        "success_count": sum(1 for v in results.values() if v["success"]),
    }
