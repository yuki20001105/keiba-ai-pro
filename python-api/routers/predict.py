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
from keiba_ai.constants import FUTURE_FIELDS  # type: ignore

import asyncio
import time as _time

router = APIRouter()

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
    if p is None and SUPABASE_ENABLED and get_supabase_client():
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


def _predict_sub_model(
    model_path: "Path",
    X_pred: "pd.DataFrame",
) -> "tuple[np.ndarray, np.ndarray] | None":
    """サブモデル（place3 など）で確率を計算して返す。
    
    Returns:
        (probs_raw, probs_norm) または None（失敗時）
    """
    import numpy as _np_sub
    try:
        _bundle = load_model_bundle(model_path)
        _X = verify_feature_columns(X_pred.copy(), _bundle)
        _raw = _bundle["model"].predict(_X)
        _cal = _bundle.get("calibrator")
        if _cal is not None:
            try:
                _raw = _cal.predict(_np_sub.array(_raw, dtype=float))
            except Exception:
                pass
        _probs = _np_sub.clip(_np_sub.array(_raw, dtype=float), 0.0, 1.0)
        _s = _probs.sum()
        _norm = (_probs / _s) if _s > 0 else _probs
        return _probs, _norm
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
    df = df.drop([c for c in _exclude if c in df.columns], axis=1)
    obj_cols = df.select_dtypes(include=["object"]).columns.tolist()
    if obj_cols:
        df = df.drop(columns=obj_cols)
    return df


@router.post("/api/predict", response_model=PredictResponse)
async def predict(request: PredictRequest, http_req: Request):
    """学習済みモデルを使用して予測を実行（free=10回/月, premium=無制限）"""
    await check_and_consume_pred_count(http_req)
    try:
        from keiba_ai.feature_engineering import add_derived_features  # type: ignore

        # モデルロード
        model_path = _resolve_model_path(request.model_id)

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
        # [INV-01] full_history_df には対象レースの行を含めない（expanding window に確定結果が混入しないよう）
        try:
            _hist_df = _load_hist_cached()
            if 'race_id' in _hist_df.columns and 'race_id' in df.columns:
                _current_race_ids = set(df['race_id'].dropna().unique())
                _hist_df = _hist_df[~_hist_df['race_id'].isin(_current_race_ids)]
            _full_hist = pd.concat([_hist_df, df], ignore_index=True)
        except Exception:
            _full_hist = df
        try:
            df = add_derived_features(df, full_history_df=_full_hist)
        except Exception as _afe:
            logger.warning(f"[predict] add_derived_features 部分失敗: {_afe} → 基本特徴量のみで続行")
        df = df.loc[:, ~df.columns.duplicated()]

        if use_optimizer and optimizer is not None:
            try:
                df_optimized = optimizer.transform(df)
            except Exception as _opt_e:
                logger.warning(f"[predict] optimizer.transform 失敗({type(_opt_e).__name__}): {_opt_e} → prepare_for_lightgbm_ultimate へフォールバック")
                from keiba_ai.lightgbm_feature_optimizer import prepare_for_lightgbm_ultimate  # type: ignore
                df_optimized, _, _ = prepare_for_lightgbm_ultimate(df, is_training=False)
            # [S] 未来情報・識別子を推論入力から強制除外
            X = _drop_non_features(df_optimized)
            # [S] A-6 厳格アサート → verify（NaN補完）
            assert_feature_columns(X, bundle)
            X = verify_feature_columns(X, bundle)
            proba = model.predict(X)
        else:
            # 後方互換: optimizer なし旧バンドル → 87特徴量モードで再エンコード
            from keiba_ai.lightgbm_feature_optimizer import prepare_for_lightgbm_ultimate  # type: ignore
            df_fb, _, _ = prepare_for_lightgbm_ultimate(df, is_training=False)
            # [S] 未来情報・識別子を推論入力から強制除外
            X = _drop_non_features(df_fb)
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
    # ── キャッシュチェック（TTL=5分、bankroll/risk_mode が同一の場合のみ）
    _cache_key = f"{request.race_id}:{request.model_id}:{request.bankroll}:{request.risk_mode}"
    _now = _time.time()
    _cached = _ANALYZE_CACHE.get(_cache_key)
    if _cached and (_now - _cached[0]) < _ANALYZE_CACHE_TTL:
        logger.info(f"[cache hit] analyze_race {request.race_id} (age={(int(_now - _cached[0]))}s)")
        return AnalyzeRaceResponse(**_cached[1])

    try:
        from app_config import list_models_from_supabase  # type: ignore
        from keiba_ai.feature_engineering import add_derived_features  # type: ignore
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
            # _load_hist_cached() で 10 分 TTL キャッシュを利用し全 DB 再ロードを削減。
            try:
                _hist_df2 = _load_hist_cached()
                # 対象レースの確定結果が expanding stats に混入しないよう hist から除外する（INV-01）
                if 'race_id' in _hist_df2.columns:
                    _hist_df2 = _hist_df2[_hist_df2['race_id'] != request.race_id]
                # FutureWarning 回避: 全列が NaN の列を concat 前に除外
                _df_pred_for_concat = df_pred.loc[:, ~df_pred.isna().all()]
                _hist_df2_for_concat = _hist_df2.loc[:, ~_hist_df2.isna().all()]
                _full_hist2 = pd.concat([_hist_df2_for_concat, _df_pred_for_concat], ignore_index=True)
            except Exception:
                _full_hist2 = df_pred
            try:
                df_pred = _add_df(df_pred, full_history_df=_full_hist2)
            except Exception as _afe2:
                logger.warning(f"[analyze] add_derived_features 部分失敗: {_afe2} → 基本特徴量のみで続行")
            # NOTE: UltimateFeatureCalculator は feature_engineering.py で同等の特徴量を
            # ベクトル化計算済みのため除去（zero-variance ・遠い問題も解消）
            df_pred = df_pred.loc[:, ~df_pred.columns.duplicated()]

            bundle_optimizer = bundle.get("optimizer")
            bundle_cat_features = bundle.get("categorical_features", [])
            if bundle_optimizer:
                try:
                    df_pred_opt = bundle_optimizer.transform(df_pred)
                except Exception as _opt_err:
                    logger.warning(
                        f"[analyze] optimizer.transform 失敗({type(_opt_err).__name__}): {_opt_err}"
                        f" → prepare_for_lightgbm_ultimate へフォールバック"
                    )
                    from keiba_ai.lightgbm_feature_optimizer import prepare_for_lightgbm_ultimate  # type: ignore
                    df_pred_opt, _, bundle_cat_features = prepare_for_lightgbm_ultimate(
                        df_pred, is_training=False, optimizer=None
                    )
            else:
                from keiba_ai.lightgbm_feature_optimizer import prepare_for_lightgbm_ultimate  # type: ignore
                df_pred_opt, _, bundle_cat_features = prepare_for_lightgbm_ultimate(
                    df_pred, is_training=False, optimizer=None
                )

            # [S] 識別子 + 未来情報を推論入力から強制除外
            X_pred = _drop_non_features(df_pred_opt)

            # [S] A-6 特徴量整合チェック → verify（NaN補完）
            # 当日レースは出馬表データのみのため欠損特徴が発生しやすい。
            # assert が閾値超過で RuntimeError を返した場合も verify で NaN 補完して続行。
            try:
                assert_feature_columns(X_pred, bundle)
            except RuntimeError as _ae:
                logger.warning(f"[A-6 ASSERT warn] {_ae} → NaN補完で続行")
            X_pred = verify_feature_columns(X_pred, bundle)

            win_probs_raw = model.predict(X_pred)

            # [A1] p_raw：キャリブレーション前の生スコア（ランキング用・連続値）
            import numpy as _np2
            _wp_raw = _np2.array(win_probs_raw, dtype=float)

            # speed_deviation（回帰）/ rank モデル: スコアを softmax で確率変換
            _bundle_target = bundle.get("target", "win")
            if _bundle_target == "speed_deviation":
                # NaN は最低値で補完してから softmax
                _finite_mask = _np2.isfinite(_wp_raw)
                _wp_raw = _np2.where(_finite_mask, _wp_raw, _wp_raw[_finite_mask].min() - 1.0 if _finite_mask.any() else -5.0)
                _exp = _np2.exp(_wp_raw - _wp_raw.max())
                win_probs = _exp / _exp.sum()
            elif _bundle_target == "rank":
                # LambdaRank: predict() はスコア（高いほど上位）→ softmax で確率化
                _finite_mask = _np2.isfinite(_wp_raw)
                _wp_raw = _np2.where(_finite_mask, _wp_raw, _wp_raw[_finite_mask].min() - 1.0 if _finite_mask.any() else -5.0)
                _exp = _np2.exp(_wp_raw - _wp_raw.max())
                win_probs = _exp / _exp.sum()
            else:
                # [L3-3] キャリブレーション適用
                _cal = bundle.get("calibrator")
                win_probs = _wp_raw.copy()
                if _cal is not None:
                    try:
                        win_probs = _cal.predict(win_probs)
                    except Exception:
                        pass  # キャリブレーター失敗時はそのまま使用

            # [A1] p_norm：win_probs（softmax/キャリブレーション済）をレース内合計1に正規化
            # _wp_raw（生スコア）は speed_deviation では z 値（負あり）のため使わない
            _wp_pnorm_base = win_probs  # 必ず calibrated / softmax 変換後を使う
            _wp_sum = _wp_pnorm_base.sum()
            _wp_norm = (_wp_pnorm_base / _wp_sum) if _wp_sum > 0 else _wp_pnorm_base

            # ── place3 モデルによる複勝圏確率 ──────────────────────────────────
            _place3_probs: "_np2.ndarray | None" = None
            _place3_norm: "_np2.ndarray | None" = None
            try:
                _place3_model_files = sorted(
                    MODELS_DIR.glob("model_place3_*_ultimate.joblib"),
                    key=lambda _p: _p.stat().st_mtime, reverse=True,
                )
                if _place3_model_files:
                    _sub_result = _predict_sub_model(_place3_model_files[0], X_pred)
                    if _sub_result is not None:
                        _place3_probs, _place3_norm = _sub_result
                        logger.info(
                            f"[analyze] place3モデル ({_place3_model_files[0].name}) 適用: "
                            f"top={_place3_norm.max():.3f}"
                        )
            except Exception as _p3e:
                logger.warning(f"[analyze] place3モデルロード失敗: {_p3e}")

            # ── アンサンブルスコア（win/speed + place3 の加重平均）──────────────
            ensemble_probs = _compute_ensemble(win_probs, _place3_probs, _bundle_target)

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
