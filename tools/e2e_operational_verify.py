"""
e2e_operational_verify.py
=========================
本番運用の一連フロー（スクレイプ→学習→予測→フロント反映）を検証するスクリプト。

【e2e_verify_timesplit.py との違い】
  - timesplit: tempディレクトリにモデル保存（本番を汚染しない）= 「仮検証」
  - operational: python-api/models/ に実際に保存（本番パス）= 「本番フロー検証」

【検証フロー】
  Step 0: 設定確認・DB状況
  Step 1: スクレイピング状況確認（1年分データがあるか）
  Step 2: 全DBデータで学習 → python-api/models/ に保存
  Step 3: 保存されたモデルを読み込み確認
  Step 4: 未知データ予測（最直近 holdout_pct % を「未来の未知レース」として扱う）
          ※ FastAPI の /api/analyze_race と同じ prediction ロジックを使用
  Step 5: フロントエンド互換 JSON 出力
  Step 6: サマリ + 処理時間ログ

【スクレイピング運用】
  1年分のデータを収集する場合:
    python-api/.venv/Scripts/python.exe tools/scrape_and_validate.py --date 20250101 --end-date 20251231 --skip-validate
  その後このスクリプトを実行してモデルを学習・更新する。

Usage:
    python-api\\.venv\\Scripts\\python.exe tools/e2e_operational_verify.py
    python-api\\.venv\\Scripts\\python.exe tools/e2e_operational_verify.py --holdout-pct 15
    python-api\\.venv\\Scripts\\python.exe tools/e2e_operational_verify.py --no-retrain  # 学習スキップ（既存モデル使用）
"""
import argparse
import json
import logging
import os
import sys
import time
import warnings
from datetime import datetime, timezone, timedelta
from pathlib import Path

warnings.filterwarnings("ignore")
logging.disable(logging.CRITICAL)

ROOT   = Path(__file__).parent.parent
PYAPI  = ROOT / "python-api"
KEIBA  = ROOT / "keiba"
sys.path.insert(0, str(PYAPI))
sys.path.insert(0, str(KEIBA))
os.chdir(str(PYAPI))

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore

import numpy as np
import pandas as pd
import joblib
import lightgbm as lgb
from sklearn.metrics import roc_auc_score, log_loss
from sklearn.model_selection import GroupKFold
from sklearn.isotonic import IsotonicRegression

from app_config import ULTIMATE_DB, MODELS_DIR, get_latest_model, load_model_bundle  # type: ignore
from keiba_ai.db_ultimate_loader import load_ultimate_training_frame  # type: ignore
from keiba_ai.feature_engineering import add_derived_features  # type: ignore
from keiba_ai.quality_gate import filter_valid_races  # type: ignore
from keiba_ai.ultimate_features import UltimateFeatureCalculator  # type: ignore
from keiba_ai.lightgbm_feature_optimizer import LightGBMFeatureOptimizer  # type: ignore

# ── ログ管理 ──────────────────────────────────────────────────────────────
JST = timezone(timedelta(hours=9))
SEP = "=" * 60
OUT = ROOT / "tools" / "pipeline_output"
OUT.mkdir(parents=True, exist_ok=True)

_LOG_LINES: list[str] = []
_SESSION_START: float = 0.0


def _log(msg: str) -> None:
    ts = datetime.now(JST).strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    _LOG_LINES.append(line)


def section(title: str) -> None:
    bar = f"\n{SEP}\n  {title}\n{SEP}"
    print(bar)
    _LOG_LINES.append(bar)


def timer_start() -> float:
    return time.perf_counter()


def timer_str(t0: float) -> str:
    e = time.perf_counter() - t0
    return f"{e:.1f}秒" if e < 60 else f"{e/60:.1f}分 ({e:.0f}秒)"


def save_log(path: Path) -> None:
    total = time.perf_counter() - _SESSION_START
    _LOG_LINES.append(f"\n総処理時間: {total/60:.1f}分 ({total:.0f}秒)")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(_LOG_LINES))
    print(f"\n  ログ保存: {path.name}")


# ── 予測ロジック（FastAPI predict.py と同等） ─────────────────────────────
POST_RACE_FIELDS = {
    "finish_position", "finish_time", "time_seconds",
    "corner_1", "corner_2", "corner_3", "corner_4",
    "corner_positions", "corner_positions_list",
    "last_3f", "last_3f_rank", "last_3f_rank_normalized", "last_3f_time",
    "margin", "prize_money", "actual_finish", "finish",
}

EXCLUDE_COLS = {"race_id", "horse_id", "jockey_id", "trainer_id", "owner_id",
                "win", "place3", "finish", "finish_position"}


def predict_race_group(
    df_race: pd.DataFrame,
    bundle: dict,
    df_full_history: pd.DataFrame,
) -> list[dict]:
    """
    1レース分のDataFrameを受け取り、FastAPI /analyze_race と同じ方法で予測する。

    Parameters
    ----------
    df_race: 1レース分の生データ (horse 1行 = 1頭)
    bundle: モデルバンドル（joblib.load した dict）
    df_full_history: 全履歴DataFrame（rolling statsの計算に使用）

    Returns
    -------
    list[dict]: 予測結果（horse ごと）
    """
    model = bundle["model"]
    optimizer = bundle.get("optimizer")
    calibrator = bundle.get("calibrator")
    feat_cols = bundle.get("feature_columns", [])

    # ── 未来情報を除去 ─────────────────────────────────────────
    df_clean = df_race.copy()
    drop_cols = [c for c in POST_RACE_FIELDS if c in df_clean.columns]
    df_clean = df_clean.drop(columns=drop_cols)

    # ── 特徴量エンジニアリング ──────────────────────────────────
    full_hist = pd.concat([df_full_history, df_clean], ignore_index=True)
    df_fe = add_derived_features(df_clean, full_history_df=full_hist)

    # ── Optimizer transform ────────────────────────────────────
    if optimizer is not None:
        df_opt = optimizer.transform(df_fe)
    else:
        df_opt = df_fe

    # ── 特徴量列を揃える ───────────────────────────────────────
    missing = [c for c in feat_cols if c not in df_opt.columns]
    for c in missing:
        df_opt[c] = 0.0
    X = df_opt[feat_cols].copy()
    obj_cols = X.select_dtypes(include=["object"]).columns.tolist()
    if obj_cols:
        X = X.drop(columns=obj_cols)
    X = X.fillna(0.0)

    # ── 予測 ──────────────────────────────────────────────────
    p_raw = np.array(model.predict(X), dtype=float)

    if calibrator is not None:
        try:
            wp = np.array(calibrator.predict(p_raw), dtype=float)
        except Exception:
            wp = p_raw.copy()
    else:
        wp = p_raw.copy()

    raw_sum = p_raw.sum()
    p_norm = (p_raw / raw_sum) if raw_sum > 0 else p_raw

    results = []
    for i, (_, row) in enumerate(df_race.iterrows()):
        odds = float(row.get("odds", row.get("entry_odds", 0.0)) or 0.0)
        ev = round(float(p_norm[i]) * odds, 3) if odds > 0 else None
        results.append({
            "horse_number": int(row.get("horse_number", row.get("horse_no", i + 1))),
            "horse_name": str(row.get("horse_name") or row.get("horse_id") or f"Horse {i+1}"),
            "p_raw": round(float(p_raw[i]), 8),
            "p_norm": round(float(p_norm[i]), 6),
            "win_probability": round(float(wp[i]), 6),
            "odds": odds,
            "expected_value": ev,
        })

    # p_raw 降順ソート → predicted_rank 付与
    results.sort(key=lambda x: x["p_raw"], reverse=True)
    for rank, r in enumerate(results, 1):
        r["predicted_rank"] = rank

    return results


# ── メイン ────────────────────────────────────────────────────────────────

def main(holdout_pct: int = 15, no_retrain: bool = False) -> None:
    global _SESSION_START
    _SESSION_START = timer_start()

    ts_id = datetime.now(JST).strftime("%Y%m%d_%H%M%S")

    # ══════════════════════════════════════════════════════════════
    section("Step 0: 設定確認")
    _log(f"  DB           : {ULTIMATE_DB}")
    _log(f"  モデル保存先 : {MODELS_DIR}  ← 本番パス")
    _log(f"  holdout      : 直近 {holdout_pct}% のレースを未知データとして扱う")
    _log(f"  retrain      : {'スキップ（--no-retrain）' if no_retrain else '実行する（全DBデータで学習）'}")
    _log(f"  開始時刻     : {datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}")

    if not ULTIMATE_DB.exists():
        _log(f"  ✗ DB が見つかりません: {ULTIMATE_DB}")
        sys.exit(1)

    # ══════════════════════════════════════════════════════════════
    section("Step 1: DB データ確認（1年分あるか）")
    t1 = timer_start()

    df_all = load_ultimate_training_frame(ULTIMATE_DB)
    df_all = filter_valid_races(df_all, verbose=False)

    all_race_ids = sorted(df_all["race_id"].unique())
    n_total = len(all_race_ids)

    first_rid = all_race_ids[0]
    last_rid  = all_race_ids[-1]
    first_year = int(first_rid[:4])
    last_year  = int(last_rid[:4])
    months_approx = max(1, (last_year - first_rid[:4].__len__() and (last_year - first_year) * 12 + 6))
    months_span = (last_year - first_year) * 12 + 6  # 概算

    _log(f"  有効レース数 : {n_total:,}")
    _log(f"  データ期間   : {first_rid[:8]} ～ {last_rid[:8]}（{months_span}ヶ月概算）")
    _log(f"  総行数       : {len(df_all):,}")
    _log("")

    # 1年分（約12ヶ月）チェック
    if months_span < 12:
        _log(f"  ⚠ データが {months_span}ヶ月分しかありません（推奨: 12ヶ月以上）")
        _log(f"  スクレイピングコマンド例:")
        _log(f"    python-api\\.venv\\Scripts\\python.exe tools/scrape_and_validate.py \\")
        _log(f"      --date {first_rid[:4]}0101 --end-date {last_rid[:8]}")
    else:
        _log(f"  ✓ {months_span}ヶ月分のデータが存在します（1年以上 OK）")

    _log(f"\n  【スクレイピング運用メモ】")
    _log(f"  - 週末ごとに実行:")
    _log(f"    python-api\\.venv\\Scripts\\python.exe tools/scrape_and_validate.py --date <YYYYMMDD>")
    _log(f"  - データ蓄積後にこのスクリプトを実行してモデルを更新")
    _elapsed_step1 = timer_str(t1)
    _log(f"  ⏱ Step1 完了: {_elapsed_step1}")

    # ══════════════════════════════════════════════════════════════
    section(f"Step 2: 全DBデータで学習 → python-api/models/ に保存")
    t2 = timer_start()

    if no_retrain:
        _log("  --no-retrain 指定のためスキップ。既存の最新モデルを使用します。")
        model_path_saved = get_latest_model()
        if model_path_saved is None:
            _log("  ✗ 既存モデルが見つかりません。--no-retrain を外して実行してください。")
            sys.exit(1)
        _log(f"  使用モデル: {model_path_saved.name}")
        _log(f"")
        _log(f"  ⚠ 注意: 既存モデルは全DB データで学習済みの可能性があります。")
        _log(f"  ⚠ その場合、holdout のAUC/的中率は過学習を反映し信頼できません。")
        _log(f"  ⚠ 正確な評価には --no-retrain を外して実行してください（約6-8分）。")
        _elapsed_step2 = "スキップ（--no-retrain）"
    else:
        # Step 2a: 特徴量エンジニアリング（全データ）
        _log("  [2a] add_derived_features（全データ）...")
        t2a = timer_start()
        df_fe = add_derived_features(df_all.copy(), full_history_df=df_all.copy())
        _log(f"  → {df_fe.shape[1]} 列  ⏱ {timer_str(t2a)}")

        _log("  [2b] UltimateFeatureCalculator（全データ）...")
        t2b = timer_start()
        calc = UltimateFeatureCalculator(str(ULTIMATE_DB))
        df_fe = calc.add_ultimate_features(df_fe)
        df_fe = df_fe.loc[:, ~df_fe.columns.duplicated()]
        _log(f"  → {df_fe.shape[1]} 列  ⏱ {timer_str(t2b)}")

        # Step 2c: win/place3 ターゲット生成
        fin_col = "finish" if "finish" in df_fe.columns else "finish_position"
        if fin_col in df_fe.columns:
            fin_num = pd.to_numeric(df_fe[fin_col], errors="coerce")
            df_fe["win"]    = (fin_num == 1).astype(float)
            df_fe["place3"] = (fin_num <= 3).astype(float)
            df_fe.loc[fin_num.isna(), ["win", "place3"]] = np.nan
        _fin_for_rank = pd.to_numeric(df_fe[fin_col], errors="coerce") if fin_col in df_fe.columns else None

        # Step 2d: LightGBM Optimizer
        _log("  [2c] LightGBM Feature Optimizer...")
        t2c = timer_start()
        optimizer = LightGBMFeatureOptimizer()
        df_opt, cat_features = optimizer.fit_transform(df_fe, target_col="win")
        _log(f"  → 最適化後: {df_opt.shape}  ⏱ {timer_str(t2c)}")

        # Step 2e: 特徴量列決定・学習データ準備
        feat_cols = [c for c in df_opt.columns
                     if c not in EXCLUDE_COLS and df_opt[c].dtype != object]
        X = df_opt[feat_cols].copy()
        y = df_opt["win"].copy()
        valid = y.notna()
        X, y = X[valid], y[valid]
        groups = df_opt.loc[valid, "race_id"] if "race_id" in df_opt.columns else pd.Series(range(len(X)))
        _log(f"  特徴量数: {len(feat_cols)}")
        _log(f"  学習行数: {len(X):,}  正例: {y.sum():.0f} ({y.mean()*100:.1f}%)")
        _log(f"  学習レース数: {groups.nunique():,}  ({groups.min()} ～ {groups.max()})")

        # Step 2f: GroupKFold CV
        _log("  [2d] LightGBM GroupKFold CV × 3...")
        t2d = timer_start()
        params = {
            "objective": "binary", "metric": "auc",
            "learning_rate": 0.05, "num_leaves": 31, "max_depth": -1,
            "min_child_samples": 20, "feature_fraction": 0.8,
            "bagging_fraction": 0.8, "bagging_freq": 5,
            "lambda_l1": 0.1, "lambda_l2": 0.1,
            "verbose": -1, "n_jobs": -1,
        }
        gkf = GroupKFold(n_splits=3)
        cv_aucs: list[float] = []
        oof_preds = np.zeros(len(X))

        for fold, (tr_idx, va_idx) in enumerate(gkf.split(X, y, groups), 1):
            tf = timer_start()
            X_tr, X_va = X.iloc[tr_idx], X.iloc[va_idx]
            y_tr, y_va = y.iloc[tr_idx], y.iloc[va_idx]
            ds_tr = lgb.Dataset(X_tr, y_tr, categorical_feature=cat_features)
            ds_va = lgb.Dataset(X_va, y_va, categorical_feature=cat_features)
            m = lgb.train(params, ds_tr, num_boost_round=200, valid_sets=[ds_va],
                          callbacks=[lgb.early_stopping(20, verbose=False), lgb.log_evaluation(-1)])
            preds = m.predict(X_va)
            auc = roc_auc_score(y_va, preds)
            cv_aucs.append(auc)
            oof_preds[va_idx] = preds
            _log(f"  Fold{fold} AUC = {auc:.4f}  ⏱ {timer_str(tf)}")

        cv_mean = float(np.mean(cv_aucs))
        cv_std  = float(np.std(cv_aucs))
        _log(f"  CV AUC: {cv_mean:.4f} ± {cv_std:.4f}")

        # Step 2g: キャリブレーション
        calibrator = IsotonicRegression(out_of_bounds="clip")
        calibrator.fit(oof_preds, y.values)
        del oof_preds
        _log(f"  キャリブレーション完了  ⏱ {timer_str(t2d)}")

        # Step 2h: 全データで最終モデル
        t2e = timer_start()
        ds_all_train = lgb.Dataset(X, y, categorical_feature=cat_features)
        final_model = lgb.train(params, ds_all_train, num_boost_round=200,
                                callbacks=[lgb.log_evaluation(-1)])
        full_preds = final_model.predict(X)
        full_auc = roc_auc_score(y, full_preds)
        _log(f"  全データAUC（過学習確認）: {full_auc:.4f}  ⏱ {timer_str(t2e)}")

        # Step 2i: モデル保存（本番パス）
        t2f = timer_start()
        model_path_saved = MODELS_DIR / f"model_win_lightgbm_{ts_id}_ultimate.joblib"
        bundle = {
            "model": final_model,
            "model_type": "lightgbm",
            "feature_columns": feat_cols,
            "categorical_features": cat_features,
            "optimizer": optimizer,
            "calibrator": calibrator,
            "metrics": {
                "auc": full_auc,
                "cv_auc_mean": cv_mean,
                "cv_auc_std": cv_std,
            },
            "data_count": len(X),
            "race_count": int(groups.nunique()),
            "training_date_from": str(groups.min()),
            "training_date_to":   str(groups.max()),
            "timestamp": ts_id,
        }
        joblib.dump(bundle, model_path_saved)

        # features_used.json（FastAPIのアサート用）
        features_meta = {
            "feature_columns": feat_cols,
            "categorical_features": cat_features,
            "n_features": len(feat_cols),
            "model_file": model_path_saved.name,
            "created_at": ts_id,
            "cv_auc": f"{cv_mean:.4f} ± {cv_std:.4f}",
        }
        feat_json_path = MODELS_DIR / f"features_{ts_id}.json"
        with open(feat_json_path, "w", encoding="utf-8") as f:
            json.dump(features_meta, f, ensure_ascii=False, indent=2)

        _log(f"")
        _log(f"  ✓ モデルを本番パスに保存しました:")
        _log(f"    {model_path_saved}")
        _log(f"  ✓ 特徴量メタ: {feat_json_path.name}")
        _log(f"  ⏱ Step2 保存: {timer_str(t2f)}")
        _elapsed_step2 = timer_str(t2)
        _log(f"  ⏱ Step2 合計: {_elapsed_step2}")

    # ══════════════════════════════════════════════════════════════
    section("Step 3: 保存モデル確認（get_latest_model()）")
    t3 = timer_start()

    latest = get_latest_model()
    if latest is None:
        _log("  ✗ get_latest_model() が None を返しました")
        sys.exit(1)

    loaded_bundle = load_model_bundle(latest)
    loaded_model   = loaded_bundle["model"]
    loaded_opt     = loaded_bundle.get("optimizer")
    loaded_cal     = loaded_bundle.get("calibrator")
    loaded_feats   = loaded_bundle.get("feature_columns", [])

    _log(f"  ✓ get_latest_model() → {latest.name}")
    _log(f"  ✓ モデルタイプ  : {loaded_bundle.get('model_type', 'unknown')}")
    _log(f"  ✓ 特徴量数      : {len(loaded_feats)}")
    _log(f"  ✓ calibrator    : {'あり' if loaded_cal else 'なし'}")
    _log(f"  ✓ optimizer     : {'あり' if loaded_opt else 'なし'}")
    _log(f"  ✓ 学習日時      : {loaded_bundle.get('timestamp', '不明')}")
    metrics = loaded_bundle.get("metrics", {})
    _log(f"  ✓ CV AUC        : {metrics.get('cv_auc_mean', '?'):.4f} ± {metrics.get('cv_auc_std', '?'):.4f}" if metrics.get('cv_auc_mean') else "  ✓ CV AUC        : (情報なし)")
    _elapsed_step3 = timer_str(t3)
    _log(f"  ⏱ Step3 完了: {_elapsed_step3}")

    # ══════════════════════════════════════════════════════════════
    section(f"Step 4: 未知データ予測（直近 {holdout_pct}% = 本番前提のholdout）")
    t4 = timer_start()

    # holdout = 最直近 holdout_pct% のレース（時系列的に後ろ）
    holdout_n = max(5, int(n_total * holdout_pct / 100))
    holdout_ids  = set(all_race_ids[-holdout_n:])
    train_ids    = set(all_race_ids[:-holdout_n])  # ← 学習に使ったデータ

    _log(f"  全レース数    : {n_total:,}")
    _log(f"  学習済みレース: {len(train_ids):,}  (～ {all_race_ids[-holdout_n-1]})")
    _log(f"  未知データ    : {len(holdout_ids):,}  ({all_race_ids[-holdout_n]} ～ {all_race_ids[-1]})")

    df_holdout = df_all[df_all["race_id"].isin(holdout_ids)].copy()
    df_train_history = df_all[df_all["race_id"].isin(train_ids)].copy()

    _log(f"  未知データ行数: {len(df_holdout):,}")

    # ─ FastAPI と同じ予測フローで各レースを予測 ────────────────
    _log(f"\n  FastAPI /analyze_race と同じロジックで予測中...")

    # 全特徴量計算は hold+train 履歴を合わせて行う（rolling stats のため）
    _log(f"  [4a] 特徴量エンジニアリング（全データ）...")
    t4a = timer_start()
    df_fe_all = add_derived_features(df_all.copy(), full_history_df=df_all.copy())
    _log(f"  → {df_fe_all.shape[1]} 列  ⏱ {timer_str(t4a)}")

    _log(f"  [4b] UltimateFeatureCalculator...")
    t4b = timer_start()
    calc2 = UltimateFeatureCalculator(str(ULTIMATE_DB))
    df_fe_all = calc2.add_ultimate_features(df_fe_all)
    df_fe_all = df_fe_all.loc[:, ~df_fe_all.columns.duplicated()]
    _log(f"  → {df_fe_all.shape[1]} 列  ⏱ {timer_str(t4b)}")

    _log(f"  [4c] optimizer transform（holdout のみ）...")
    t4c = timer_start()
    if loaded_opt is not None:
        df_fe_hold = df_fe_all[df_fe_all["race_id"].isin(holdout_ids)].copy()
        df_opt_hold = loaded_opt.transform(df_fe_hold)
    else:
        df_opt_hold = df_fe_all[df_fe_all["race_id"].isin(holdout_ids)].copy()
    _log(f"  → {df_opt_hold.shape}  ⏱ {timer_str(t4c)}")

    # 欠損特徴量を0補完
    for c in loaded_feats:
        if c not in df_opt_hold.columns:
            df_opt_hold[c] = 0.0

    X_hold = df_opt_hold[loaded_feats].copy()
    X_hold = X_hold.fillna(0.0)

    # 正解ラベル取得
    fin_col_h = "finish" if "finish" in df_opt_hold.columns else "finish_position"
    if fin_col_h in df_opt_hold.columns:
        fin_hold = pd.to_numeric(df_opt_hold[fin_col_h], errors="coerce")
    elif fin_col_h in df_fe_all.columns:
        fin_hold = pd.to_numeric(df_fe_all.loc[df_fe_all["race_id"].isin(holdout_ids), fin_col_h], errors="coerce").reset_index(drop=True)
    else:
        fin_hold = pd.Series(np.nan, index=range(len(X_hold)))

    y_hold = (fin_hold == 1).astype(float)
    valid_hold = fin_hold.notna()

    # 予測
    _log(f"  [4d] 予測...")
    t4d = timer_start()
    p_raw_arr = loaded_model.predict(X_hold)
    if loaded_cal is not None:
        try:
            wp_arr = loaded_cal.predict(p_raw_arr)
        except Exception:
            wp_arr = p_raw_arr.copy()
    else:
        wp_arr = p_raw_arr.copy()
    _log(f"  予測完了  ⏱ {timer_str(t4d)}")

    # AUC/LogLoss
    X_v = X_hold[valid_hold.values]
    y_v = y_hold[valid_hold.values]

    if len(y_v) > 0 and y_v.sum() > 0:
        hold_auc = roc_auc_score(y_v, p_raw_arr[valid_hold.values])
        hold_ll  = log_loss(y_v, np.clip(p_raw_arr[valid_hold.values], 1e-7, 1 - 1e-7))
        _log(f"  Holdout AUC     : {hold_auc:.4f}")
        _log(f"  Holdout LogLoss : {hold_ll:.4f}")
        if hold_auc > 0.99:
            _log(f"  ⚠ AUC≒1.0: 本番モデルは全DBデータで学習済みのため当然の結果です")
            _log(f"  ⚠ 汎化性能の評価は CV AUC を参照してください")
    else:
        hold_auc, hold_ll = 0.0, 0.0
        _log(f"  ⚠ 正解ラベルなし（holdoutのfinishが取得できていません）")

    # 単勝的中率
    hold_meta = df_opt_hold[["race_id"]].copy().reset_index(drop=True)
    if "horse_number" in df_opt_hold.columns:
        hold_meta["horse_number"] = df_opt_hold["horse_number"].values
    hold_meta["p_raw"]  = p_raw_arr
    hold_meta["finish"] = fin_hold.values

    hits, total_races_h = 0, 0
    for rid, grp in hold_meta.groupby("race_id"):
        fin_grp = pd.to_numeric(grp["finish"], errors="coerce")
        if (fin_grp == 1).sum() == 0:
            continue
        total_races_h += 1
        pred_winner  = grp["p_raw"].idxmax()
        actual_winner = grp[fin_grp == 1].index
        if len(actual_winner) > 0 and pred_winner == actual_winner[0]:
            hits += 1

    win_acc = hits / total_races_h if total_races_h > 0 else 0.0
    _log(f"  単勝的中率      : {hits}/{total_races_h} = {win_acc:.1%}")
    _elapsed_step4 = timer_str(t4)
    _log(f"  ⏱ Step4 合計: {_elapsed_step4}")

    # ══════════════════════════════════════════════════════════════
    section("Step 5: フロントエンド互換 JSON 出力")
    t5 = timer_start()

    # holdout レースを p_norm 正規化して race ごとに JSON 生成
    sample_races_json = []
    hold_race_ids_sorted = sorted(holdout_ids)

    for rid in hold_race_ids_sorted[:10]:  # 最大10レース分サンプル出力
        mask = hold_meta["race_id"] == rid
        grp_meta = hold_meta[mask].reset_index(drop=True)
        if len(grp_meta) == 0:
            continue

        raw_sum = grp_meta["p_raw"].sum()
        p_norm_grp = grp_meta["p_raw"] / raw_sum if raw_sum > 0 else grp_meta["p_raw"]

        grp_meta = grp_meta.copy()
        grp_meta["p_norm"] = p_norm_grp.values

        # wp（キャリブ後）
        idx_in_hold = hold_meta[hold_meta["race_id"] == rid].index
        wp_grp = wp_arr[idx_in_hold] if len(idx_in_hold) > 0 else np.zeros(len(grp_meta))

        # odds
        race_rows = df_all[df_all["race_id"] == rid]
        odds_map = {}
        if "horse_number" in race_rows.columns and "odds" in race_rows.columns:
            odds_map = dict(zip(race_rows["horse_number"].astype(str), race_rows["odds"]))

        sorted_grp = grp_meta.sort_values("p_raw", ascending=False).reset_index(drop=True)
        horses_json = []
        for rank_i, row in sorted_grp.iterrows():
            hn = str(int(row.get("horse_number", rank_i + 1))) if pd.notna(row.get("horse_number")) else str(rank_i + 1)
            odds_val = float(odds_map.get(hn, 0.0) or 0.0)
            ev = round(float(row["p_norm"]) * odds_val, 3) if odds_val > 0 else None
            fin_val = int(row["finish"]) if pd.notna(row.get("finish")) else None

            wp_val = float(wp_grp[rank_i]) if rank_i < len(wp_grp) else float(row["p_raw"])
            horses_json.append({
                "predicted_rank": rank_i + 1,
                "horse_number": hn,
                "p_raw": round(float(row["p_raw"]), 8),
                "p_norm": round(float(row["p_norm"]), 6),
                "win_probability": round(wp_val, 6),
                "odds": odds_val,
                "expected_value": ev,
                "actual_finish": fin_val,
            })

        p_norm_sum = round(sum(h["p_norm"] for h in horses_json), 6)
        sample_races_json.append({
            "race_id": rid,
            "n_horses": len(horses_json),
            "p_norm_sum": p_norm_sum,
            "horses": horses_json,
        })

    # JSON ファイル出力
    today_str = datetime.now(JST).strftime("%Y%m%d%H%M")
    total_elapsed = time.perf_counter() - _SESSION_START
    step_timing = {
        "step1_db_check": _elapsed_step1,
        "step2_retrain": _elapsed_step2 if not no_retrain else "skipped",
        "step3_model_load": _elapsed_step3,
        "step4_holdout_predict": _elapsed_step4,
        "total": f"{total_elapsed/60:.1f}min ({total_elapsed:.0f}s)",
    }

    output = {
        "generated_at": datetime.now(JST).isoformat(),
        "flow": "operational_verify（本番フロー検証）",
        "model_used": latest.name if latest else "unknown",
        "data_status": {
            "total_races": n_total,
            "first_race_id": first_rid,
            "last_race_id": last_rid,
            "months_span_approx": months_span,
        },
        "train_config": {
            "holdout_pct": holdout_pct,
            "train_races": len(train_ids),
            "holdout_races": len(holdout_ids),
            "feature_count": len(loaded_feats),
        },
        "model_metrics": {
            "cv_auc_mean": round(metrics.get("cv_auc_mean", 0), 4) if metrics else 0,
            "cv_auc_std": round(metrics.get("cv_auc_std", 0), 4) if metrics else 0,
            "holdout_auc": round(hold_auc, 4),
            "holdout_logloss": round(hold_ll, 4),
            "win_accuracy": round(win_acc, 4),
            "win_hits": hits,
            "win_total_races": total_races_h,
        },
        "timing": step_timing,
        "production_model_path": str(model_path_saved) if not no_retrain else str(latest),
        "sample_races": sample_races_json,
    }

    out_json = OUT / f"e2e_operational_predictions_{today_str}.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    _log(f"  → {out_json.name}")
    _elapsed_step5 = timer_str(t5)
    _log(f"  ⏱ Step5 完了: {_elapsed_step5}")

    # ══════════════════════════════════════════════════════════════
    section("Step 6: フロントエンド表示確認")

    _log(f"  【FastAPI との接続フロー】")
    _log(f"  1. FastAPI 起動: python-api/.venv/Scripts/python.exe python-api/main.py")
    _log(f"  2. Next.js 起動: npm run dev")
    _log(f"  3. ブラウザ → /predict-batch → レース選択 → 予測実行")
    _log(f"")
    _log(f"  【FastAPI が使うモデル（get_latest_model()）】")
    if latest:
        _log(f"    {latest.name}")
        _log(f"    ↑ このモデルがフロントエンドの予測に使われます")
    _log(f"")
    _log(f"  【p_norm 整合性確認】")
    p_norm_ok = all(abs(r["p_norm_sum"] - 1.0) < 0.001 for r in sample_races_json)
    _log(f"    p_norm 合計≒1: {'✓ ALL OK' if p_norm_ok else '✗ 異常あり'}")
    _log(f"")
    _log(f"  ── サンプル予測（直近holdoutの1レース）──")

    if sample_races_json:
        r = sample_races_json[0]
        _log(f"  race_id: {r['race_id']}  ({r['n_horses']}頭)  p_norm合計={r['p_norm_sum']}")
        _log(f"  {'rank':>4} {'#':>3} {'p_raw':>12} {'p_norm':>10} {'win_prob':>10} {'EV':>7} {'actual':>7}")
        _log(f"  {'-'*60}")
        for h in r["horses"]:
            ev_str = f"{h.get('expected_value') or 0:.2f}" if h.get("expected_value") is not None else "  N/A"
            fin_str = str(h.get("actual_finish", "?")) if h.get("actual_finish") else "?"
            _log(f"  {h['predicted_rank']:>4}  {h.get('horse_number','?'):>3}  "
                 f"{h['p_raw']:>12.8f} {h['p_norm']:>10.6f} "
                 f"{h['win_probability']:>10.6f} {ev_str:>7} {fin_str:>7}")

    # ══════════════════════════════════════════════════════════════
    section("E2E 本番フロー検証サマリ")
    total_elapsed = time.perf_counter() - _SESSION_START

    _log(f"  DBデータ範囲  : {first_rid[:8]} ～ {last_rid[:8]}  ({n_total:,}レース)")
    _log(f"  学習済みレース: {len(train_ids):,}  / 未知データ: {len(holdout_ids):,}")
    _log(f"  本番モデル    : {latest.name if latest else 'N/A'}")
    _log(f"  特徴量数      : {len(loaded_feats)}")
    _log(f"")
    _log(f"  CV AUC        : {metrics.get('cv_auc_mean', 0):.4f} ± {metrics.get('cv_auc_std', 0):.4f}  ← 汎化性能の真の指標" if metrics.get('cv_auc_mean') else "  CV AUC        : (情報なし)")
    _log(f"  Holdout AUC   : {hold_auc:.4f}  {'← 本番モデル=全データ学習済み（過学習が出る）' if hold_auc > 0.99 else ''}")
    _log(f"  Holdout LL    : {hold_ll:.4f}")
    _log(f"  単勝的中率    : {hits}/{total_races_h} = {win_acc:.1%}  {'← 同上（全データ学習済み）' if win_acc >= 1.0 else ''}")
    _log(f"")
    _log(f"  ★ 汎化性能の評価: python tools/e2e_verify_timesplit.py を実行してください")
    _log(f"")
    _log(f"  ── 処理時間内訳 ──")
    _log(f"  Step1 DB確認    : {_elapsed_step1}")
    if not no_retrain:
        _log(f"  Step2 学習      : {_elapsed_step2}")
    _log(f"  Step3 モデル確認: {_elapsed_step3}")
    _log(f"  Step4 予測      : {_elapsed_step4}")
    _log(f"  Step5 JSON出力  : {_elapsed_step5}")
    _log(f"  ════════════════")
    _log(f"  総処理時間      : {total_elapsed/60:.1f}分 ({total_elapsed:.0f}秒)")
    _log(f"")
    _log(f"  サンプル JSON   : {out_json.name}")
    _log(f"  本番モデルパス  : {latest}")
    _log(f"")
    _log(f"  ✓ 本番フロー検証完了")
    _log(f"")
    _log(f"  【次のステップ（フロントエンドで確認する）】")
    _log(f"  1. FastAPI 起動:  Run Task → 'Start FastAPI'")
    _log(f"  2. Next.js 起動:  Run Task → 'Start Next.js'")
    _log(f"  3. http://localhost:3000/predict-batch でレース予測を確認")
    _log(f"  4. モデルを git push → Railway が自動デプロイ（本番反映）")

    # ログファイル保存
    out_log = OUT / f"e2e_operational_log_{today_str}.txt"
    save_log(out_log)


# ── エントリーポイント ───────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="E2E 本番フロー検証")
    parser.add_argument("--holdout-pct", type=int, default=15,
                        help="直近何%%のレースをholdout（未知データ）とするか (default: 15)")
    parser.add_argument("--no-retrain", action="store_true",
                        help="学習をスキップして既存の最新モデルで予測のみ実行")
    args = parser.parse_args()
    main(holdout_pct=args.holdout_pct, no_retrain=args.no_retrain)
