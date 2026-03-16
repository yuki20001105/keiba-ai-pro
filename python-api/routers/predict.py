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
    verify_feature_columns,
    assert_feature_columns,
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

router = APIRouter()

# レース後確定フィールド（予測時に除去するフィールドセット）
POST_RACE_FIELDS = {
    "finish_position", "finish_time", "time_seconds",
    "corner_1", "corner_2", "corner_3", "corner_4",
    "corner_positions", "corner_positions_list",
    "last_3f", "last_3f_rank", "last_3f_rank_normalized", "last_3f_time",
    "margin", "prize_money",
    # 評価用フィールド（予測 JSON に同居するが入力に使わない）
    "actual_finish", "finish",
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

        # ── [S: Quality Gate] 入力整合チェック → 不正レースを除外 ──────────
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "keiba"))
            from keiba_ai.quality_gate import filter_valid_races as _fvr  # type: ignore
            if "race_id" not in df.columns:
                df["race_id"] = "202500000000"
            _n_before = len(df)
            df = _fvr(df, verbose=False)
            if len(df) == 0:
                raise HTTPException(
                    status_code=400,
                    detail="入力データに不正なレースが含まれています（distance=0, odds欠損など）。"
                           "再スクレイプしてください。",
                )
            if len(df) < _n_before:
                logger.warning(f"[Quality Gate] {_n_before - len(df)} 件の不正エントリを除外")
        except HTTPException:
            raise
        except Exception as _qe:
            logger.warning(f"[Quality Gate] スキップ: {_qe}")
        # ───────────────────────────────────────────────────────────────────
        # race_id がない場合はダミーを設定 (add_derived_features が必須とするため)
        if "race_id" not in df.columns:
            df["race_id"] = "202500000000"
        # [fix] full_history_df に DB 全履歴を渡し rolling stats（past_N_avg_finish 等）を正しく計算
        try:
            from keiba_ai.db_ultimate_loader import load_ultimate_training_frame as _ltf  # type: ignore
            _hist_df = _ltf(ULTIMATE_DB)
            _full_hist = pd.concat([_hist_df, df], ignore_index=True)
        except Exception:
            _full_hist = df
        df = add_derived_features(df, full_history_df=_full_hist)

        if use_optimizer and optimizer is not None:
            df_optimized = optimizer.transform(df)
            # [S] 未来情報・識別子を推論入力から強制除外
            _post_drop = set(POST_RACE_FIELDS) | {"race_id", "horse_id", "jockey_id", "trainer_id", "owner_id", "finish_position"}
            exclude_cols = list(_post_drop)
            X = df_optimized.drop([c for c in exclude_cols if c in df_optimized.columns], axis=1)
            obj_cols = X.select_dtypes(include=["object"]).columns.tolist()
            if obj_cols:
                X = X.drop(columns=obj_cols)
            # [S] A-6 厳格アサート → verify（NaN補完）
            assert_feature_columns(X, bundle)
            X = verify_feature_columns(X, bundle)
            proba = model.predict(X)
        else:
            # 後方互換: optimizer なし旧バンドル → 87特徴量モードで再エンコード
            from keiba_ai.lightgbm_feature_optimizer import prepare_for_lightgbm_ultimate  # type: ignore
            df_fb, _, _ = prepare_for_lightgbm_ultimate(df, is_training=False)
            # [S] 未来情報・識別子を推論入力から強制除外
            _post_drop2 = set(POST_RACE_FIELDS) | {"race_id", "horse_id", "jockey_id", "trainer_id", "owner_id", "finish_position"}
            exclude_cols = list(_post_drop2)
            X = df_fb.drop([c for c in exclude_cols if c in df_fb.columns], axis=1)
            obj_cols = X.select_dtypes(include=["object"]).columns.tolist()
            if obj_cols:
                X = X.drop(columns=obj_cols)
            # [S] A-6 厳格アサート → verify（NaN補完）
            assert_feature_columns(X, bundle)
            X = verify_feature_columns(X, bundle)
            try:
                proba = model.predict(X)
            except Exception:
                proba = model.predict_proba(X)[:, 1]

        # [A1] p_raw：キャリブレーション前の生スコア（ランキング用・連続値）
        import numpy as _np
        p_raw = _np.array(proba, dtype=float)

        # [L3-3] 確率キャリブレーション（アイソトニック回帰）
        calibrator = bundle.get("calibrator")
        if calibrator is not None:
            try:
                proba = calibrator.predict(proba)
            except Exception as _cal_err:
                logger.warning(f"[キャリブレーションエラー] {_cal_err} → 未キャリブレースで続行")

        # [A1] p_norm：p_raw をレース内合計1に正規化（買い目設計用）
        _raw_sum = p_raw.sum()
        p_norm = (p_raw / _raw_sum) if _raw_sum > 0 else p_raw

        predictions = []
        for i, (_, row) in enumerate(df.iterrows()):
            horse_num = int(row.get("horse_number", row.get("horse_no", i + 1)))
            _hn = row.get("horse_name") or row.get("horse_id") or f"Horse {horse_num}"
            predictions.append({
                "index": i,
                "horse_number": horse_num,
                "horse_name": str(_hn),
                "probability": float(proba[i]),
                "p_raw": float(p_raw[i]),
                "p_norm": float(p_norm[i]),
                "odds": float(row.get("odds", row.get("entry_odds", 0.0))),
            })

        # [A1] ソートは p_raw ベース（キャリブ量子化によるタイ回避）
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
            model_path = get_latest_model()
            if model_path is None:
                if SUPABASE_ENABLED and get_supabase_client():
                    sb_models = list_models_from_supabase()
                    if sb_models:
                        model_path = _ensure_model_local(sb_models[0]["model_id"])
                if model_path is None:
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

            # [S] 未来情報ブラックリスト列を推論入力から強制除外
            _drop_future = [c for c in POST_RACE_FIELDS if c in df_pred.columns]
            if _drop_future:
                df_pred = df_pred.drop(columns=_drop_future)
                logger.debug(f"[S] 未来情報列を除外: {_drop_future}")

            _col_map = {
                "finish_position": "finish", "finish_time": "time",
                "track_type": "surface", "last_3f": "last_3f_time", "weight_kg": "horse_weight",
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
            try:
                from keiba_ai.quality_gate import validate_race_entries as _vqr  # type: ignore
                _qr_a = _vqr(df_pred)
                if _qr_a.n_bad > 0:
                    raise HTTPException(
                        status_code=400,
                        detail=f"[Quality Gate] レース {request.race_id} の入力データに問題があります:\n{_qr_a.summary()}",
                    )
                if _qr_a.n_warn > 0:
                    logger.warning(f"[Quality Gate warn] /analyze {request.race_id}:\n{_qr_a.summary()}")
            except HTTPException:
                raise
            except Exception as _qe_a:
                logger.warning(f"[Quality Gate /analyze] スキップ: {_qe_a}")

            # [fix] full_history_df に DB 全履歴を渡し rolling stats を正しく計算
            try:
                from keiba_ai.db_ultimate_loader import load_ultimate_training_frame as _ltf2  # type: ignore
                _hist_df2 = _ltf2(ULTIMATE_DB)
                _full_hist2 = pd.concat([_hist_df2, df_pred], ignore_index=True)
            except Exception:
                _full_hist2 = df_pred
            df_pred = _add_df(df_pred, full_history_df=_full_hist2)
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

            # [S] 識別子 + 未来情報を推論入力から強制除外
            _analyze_drop = (
                {"win", "place", "race_id", "horse_id", "jockey_id", "trainer_id", "owner_id"}
                | POST_RACE_FIELDS
            )
            X_pred = df_pred_opt.drop([c for c in _analyze_drop if c in df_pred_opt.columns], axis=1)
            obj_cols = X_pred.select_dtypes(include=["object"]).columns.tolist()
            if obj_cols:
                X_pred = X_pred.drop(columns=obj_cols)

            # [S] A-6 厳格アサート → verify（NaN補完）
            assert_feature_columns(X_pred, bundle)
            X_pred = verify_feature_columns(X_pred, bundle)

            win_probs_raw = model.predict(X_pred)

            # [A1] p_raw：キャリブレーション前の生スコア（ランキング用・連続値）
            import numpy as _np2
            _wp_raw = _np2.array(win_probs_raw, dtype=float)

            # [L3-3] キャリブレーション適用
            _cal = bundle.get("calibrator")
            win_probs = _wp_raw.copy()
            if _cal is not None:
                try:
                    win_probs = _cal.predict(win_probs)
                except Exception:
                    pass  # キャリブレーター失敗時はそのまま使用

            # [A1] p_norm：p_raw をレース内合計1に正規化（買い目設計用）
            _wp_sum = _wp_raw.sum()
            _wp_norm = (_wp_raw / _wp_sum) if _wp_sum > 0 else _wp_raw

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
                    "horse_name": _hr.get("horse_name") or f'[{_hr.get("horse_id","") or _horse_num}]',
                    "jockey_name": _hr.get("jockey_name", ""),
                    "trainer_name": _hr.get("trainer_name", ""),
                    "sex": _hr.get("sex", ""), "age": _hr.get("age"),
                    "horse_weight": _hr.get("weight_kg") or _hr.get("horse_weight"),
                    "odds": _odds_float, "popularity": _hr.get("popularity"),
                    "win_probability": float(win_probs[i]),
                    "p_raw": float(_wp_raw[i]),
                    "p_norm": float(_wp_norm[i]),
                    "expected_value": float(_wp_norm[i] * _odds_float),  # [A1] p_norm×odds（出走内正規化済み確率）
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
