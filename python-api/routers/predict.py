"""
予測・レース分析エンドポイント
POST /api/predict
POST /api/analyze_race
"""
from __future__ import annotations

import json
import traceback
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException, Request

from app_config import (  # type: ignore
    SUPABASE_ENABLED,
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
)

router = APIRouter()

# レース後確定フィールド（予測時に除去するフィールドセット）
POST_RACE_FIELDS = {
    "finish_position", "finish_time", "time_seconds",
    "corner_1", "corner_2", "corner_3", "corner_4",
    "corner_positions", "corner_positions_list",
    "last_3f", "last_3f_rank", "last_3f_rank_normalized", "last_3f_time",
    "margin", "prize_money",
}


@router.post("/api/predict", response_model=PredictResponse)
async def predict(request: PredictRequest, http_req: Request):
    """学習済みモデルを使用して予測を実行（free=10回/月, premium=無制限）"""
    await check_and_consume_pred_count(http_req)
    try:
        from app_config import list_models_from_supabase  # type: ignore
        from keiba_ai.feature_engineering import add_derived_features  # type: ignore

        # モデルロード
        if request.model_id:
            model_path = _ensure_model_local(request.model_id)
            if not model_path:
                raise HTTPException(status_code=404, detail=f"モデル {request.model_id} が見つかりません")
        else:
            model_path = get_latest_model()
            if model_path is None:
                if SUPABASE_ENABLED and get_supabase_client():
                    sb_models = list_models_from_supabase()
                    if sb_models:
                        model_path = _ensure_model_local(sb_models[0]["model_id"])
                if model_path is None:
                    raise HTTPException(status_code=404, detail="学習済みモデルが見つかりません")

        bundle = load_model_bundle(model_path)
        model = bundle["model"]
        optimizer = bundle.get("optimizer")
        use_optimizer = bundle.get("use_optimizer", False)

        # レース後フィールドを除去
        cleaned_horses = [{k: v for k, v in h.items() if k not in POST_RACE_FIELDS} for h in request.horses]
        df = pd.DataFrame(cleaned_horses)
        # race_id がない場合はダミーを設定 (add_derived_features が必須とするため)
        if "race_id" not in df.columns:
            df["race_id"] = "202500000000"
        df = add_derived_features(df, full_history_df=None)

        if use_optimizer and optimizer is not None:
            df_optimized = optimizer.transform(df)
            exclude_cols = ["race_id", "horse_id", "jockey_id", "trainer_id", "owner_id", "finish_position"]
            X = df_optimized.drop([c for c in exclude_cols if c in df_optimized.columns], axis=1)
            obj_cols = X.select_dtypes(include=["object"]).columns.tolist()
            if obj_cols:
                X = X.drop(columns=obj_cols)
            saved_feature_columns = bundle.get("feature_columns")
            if saved_feature_columns:
                for col in saved_feature_columns:
                    if col not in X.columns:
                        X[col] = 0.0
                X = X[saved_feature_columns]
            proba = model.predict(X)
        else:
            # 後方互換: optimizer なし旧バンドル → 87特徴量モードで再エンコード
            from keiba_ai.lightgbm_feature_optimizer import prepare_for_lightgbm_ultimate  # type: ignore
            df_fb, _, _ = prepare_for_lightgbm_ultimate(df, is_training=False)
            exclude_cols = ["race_id", "horse_id", "jockey_id", "trainer_id", "owner_id", "finish_position"]
            X = df_fb.drop([c for c in exclude_cols if c in df_fb.columns], axis=1)
            obj_cols = X.select_dtypes(include=["object"]).columns.tolist()
            if obj_cols:
                X = X.drop(columns=obj_cols)
            saved_features = bundle.get("feature_columns")
            if saved_features:
                for col in saved_features:
                    if col not in X.columns:
                        X[col] = 0.0
                X = X[[c for c in saved_features if c in X.columns]]
            try:
                proba = model.predict(X)
            except Exception:
                proba = model.predict_proba(X)[:, 1]

        predictions = []
        for i, (_, row) in enumerate(df.iterrows()):
            horse_num = int(row.get("horse_number", row.get("horse_no", i + 1)))
            predictions.append({
                "index": i,
                "horse_number": horse_num,
                "horse_name": str(row.get("horse_name", f"Horse {horse_num}")),
                "probability": float(proba[i]),
                "odds": float(row.get("odds", row.get("entry_odds", 0.0))),
            })

        predictions.sort(key=lambda x: x["probability"], reverse=True)
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
    try:
        from app_config import list_models_from_supabase  # type: ignore
        from keiba_ai.feature_engineering import add_derived_features  # type: ignore
        from betting_strategy import BettingRecommender  # type: ignore

        # Phase 0: 常に ultimate DB を使用（87特徴量モード固定）
        db_path = ULTIMATE_DB

        # モデルロード
        if request.model_id:
            model_path = _ensure_model_local(request.model_id)
            if not model_path:
                raise HTTPException(status_code=404, detail=f"モデル {request.model_id} が見つかりません")
        else:
            if SUPABASE_ENABLED and get_supabase_client():
                sb_models = list_models_from_supabase()
                model_path = _ensure_model_local(sb_models[0]["model_id"]) if sb_models else None
            else:
                model_path = None
            if not model_path:
                um = sorted(MODELS_DIR.glob("model_*_ultimate.joblib"), key=lambda p: p.stat().st_mtime, reverse=True)
                model_path = um[0] if um else get_latest_model()
            if not model_path:
                raise HTTPException(status_code=404, detail="訓練済みモデルが見つかりません")

        bundle = load_model_bundle(model_path)
        model = bundle["model"]

        # Phase 0: 常に ultimate モードでデータを取得（87特徴量固定）
        if True:  # noqa (request.ultimate_mode は常に True)
            # ── Ultimate DB から出走馬データを取得 ──
            import sqlite3 as _sq3
            from keiba_ai.feature_engineering import add_derived_features as _add_df  # type: ignore
            from keiba_ai.ultimate_features import UltimateFeatureCalculator  # type: ignore

            _conn = _sq3.connect(str(db_path))
            _cur = _conn.cursor()
            _cur.execute("SELECT data FROM races_ultimate WHERE race_id = ?", (request.race_id,))
            _rrow = _cur.fetchone()
            if not _rrow:
                _conn.close()
                raise HTTPException(status_code=404, detail=f"レース {request.race_id} が races_ultimate に見つかりません")
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
                raise HTTPException(status_code=404, detail=f"レース {request.race_id} の馬データが見つかりません")

            _horse_records = []
            for _hr in _hrows:
                _hd = json.loads(_hr[0])
                _hd["race_id"] = request.race_id
                for _k, _v in _race_data.items():
                    if _k not in _hd or _hd[_k] is None:
                        _hd[_k] = _v
                _horse_records.append(_hd)

            df_pred = pd.DataFrame(_horse_records)
            _col_map = {
                "finish_position": "finish", "finish_time": "time",
                "track_type": "surface", "last_3f": "last_3f_time", "weight_kg": "horse_weight",
            }
            for _old, _new in _col_map.items():
                if _old in df_pred.columns and _new not in df_pred.columns:
                    df_pred[_new] = df_pred[_old]

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

            df_pred = _add_df(df_pred, full_history_df=df_pred)
            calculator = UltimateFeatureCalculator(str(db_path))
            df_pred = calculator.add_ultimate_features(df_pred)
            df_pred = df_pred.loc[:, ~df_pred.columns.duplicated()]

            bundle_optimizer = bundle.get("optimizer")
            bundle_cat_features = bundle.get("categorical_features", [])
            if bundle_optimizer:
                df_pred_opt = bundle_optimizer.transform(df_pred)
            else:
                from keiba_ai.lightgbm_feature_optimizer import prepare_for_lightgbm_ultimate  # type: ignore
                df_pred_opt, _, bundle_cat_features = prepare_for_lightgbm_ultimate(
                    df_pred, is_training=False, optimizer=None
                )

            exclude_cols = ["win", "place", "race_id", "horse_id", "jockey_id", "trainer_id", "owner_id", "finish_position", "finish"]
            X_pred = df_pred_opt.drop([c for c in exclude_cols if c in df_pred_opt.columns], axis=1)
            obj_cols = X_pred.select_dtypes(include=["object"]).columns.tolist()
            if obj_cols:
                X_pred = X_pred.drop(columns=obj_cols)

            if hasattr(model, "feature_name"):
                trained_features = model.feature_name()
            elif hasattr(model, "booster_"):
                trained_features = model.booster_.feature_name()
            else:
                trained_features = list(X_pred.columns)
            for _mf in [f for f in trained_features if f not in X_pred.columns]:
                X_pred[_mf] = 0.0
            X_pred = X_pred.drop(columns=[c for c in X_pred.columns if c not in trained_features], errors="ignore")
            X_pred = X_pred[trained_features]

            win_probs = model.predict(X_pred)

            predictions = []
            for i, _hr in enumerate(_horse_records):
                _raw_odds = _hr.get("odds") or _hr.get("win_odds")
                try:
                    _odds_float = float(_raw_odds) if _raw_odds not in (None, "", "---") else 5.0
                except (ValueError, TypeError):
                    _odds_float = 5.0
                _horse_num = _hr.get("horse_number") or _hr.get("horse_no") or (i + 1)
                predictions.append({
                    "horse_number": _horse_num, "horse_no": _horse_num,
                    "horse_name": _hr.get("horse_name", ""),
                    "jockey_name": _hr.get("jockey_name", ""),
                    "trainer_name": _hr.get("trainer_name", ""),
                    "sex": _hr.get("sex", ""), "age": _hr.get("age"),
                    "horse_weight": _hr.get("weight_kg") or _hr.get("horse_weight"),
                    "odds": _odds_float, "popularity": _hr.get("popularity"),
                    "win_probability": float(win_probs[i]),
                    "expected_value": float(win_probs[i] * _odds_float),
                })

        recommender = BettingRecommender(
            bankroll=request.bankroll, risk_mode=request.risk_mode,
            use_kelly=request.use_kelly, dynamic_unit=request.dynamic_unit, min_ev=request.min_ev,
        )
        result = recommender.analyze_and_recommend(predictions, race_info)

        return AnalyzeRaceResponse(
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

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"レース分析に失敗: {str(e)}")
