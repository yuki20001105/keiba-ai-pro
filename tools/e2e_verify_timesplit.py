"""
e2e_verify_timesplit.py
=======================
既存DBデータを時系列分割して「ゼロからの一連フロー」を検証するスクリプト。

【設計方針】
  - プロダクション環境を汚染しない（tempディレクトリにモデル保存、DB読み取り専用）
  - 実スクレイプなし（既存 keiba_ultimate.db の race_id を時系列分割）
  - race_id（JRA形式 YYYYJJKKNNRR）は時系列順 → sort で正確な時系列分割が可能

【ウィンドウ設定】
  - 学習: 最古から始まる1年分（race_id の先頭 ~80% 相当）
  - 検証: 直近 2ヶ月分（後方 ~20% から race_id 先頭4桁で絞り込み）

【出力】
  1. 学習サマリ (AUC, CV-AUC, 特徴量数, レース数)
  2. 検証 AUC / LogLoss / 単勝的中率
  3. フロントエンド互換 JSON（p_raw / p_norm / EV / predicted_rank）
     → tools/pipeline_output/e2e_verify_predictions_<date>.json

Usage:
    python-api\\.venv\\Scripts\\python.exe tools/e2e_verify_timesplit.py
    python-api\\.venv\\Scripts\\python.exe tools/e2e_verify_timesplit.py --train-months 12 --holdout-months 2
"""
import argparse
import json
import logging
import os
import sys
import tempfile
import warnings
from pathlib import Path
from datetime import datetime, timezone, timedelta

warnings.filterwarnings("ignore")
logging.disable(logging.CRITICAL)

ROOT  = Path(__file__).parent.parent
PYAPI = ROOT / "python-api"
KEIBA = ROOT / "keiba"
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

from app_config import ULTIMATE_DB
from keiba_ai.db_ultimate_loader import load_ultimate_training_frame
from keiba_ai.feature_engineering import add_derived_features
from keiba_ai.ultimate_features import UltimateFeatureCalculator
from keiba_ai.lightgbm_feature_optimizer import LightGBMFeatureOptimizer
from keiba_ai.quality_gate import filter_valid_races

OUT = ROOT / "tools" / "pipeline_output"
OUT.mkdir(exist_ok=True)

JST = timezone(timedelta(hours=9))

SEP = "=" * 60
def section(title: str) -> None:
    print(f"\n{SEP}\n  {title}\n{SEP}")


def race_id_to_year_month(race_id: str) -> tuple[int, int]:
    """race_id (YYYYJJKKNNRR) から (year, month) を推定。
    JRA race_id の先頭4桁が年、5-6桁が場コード。
    開催月はrace_idから直接取れないため、race_id ソート順を年月代替として使用。
    """
    try:
        year = int(race_id[:4])
        return year, 0  # 月は不明なのでソート順で管理
    except Exception:
        return 0, 0


def main(train_months: int = 12, holdout_months: int = 2) -> None:
    section("Step 0: 設定確認")
    print(f"  DB           : {ULTIMATE_DB}")
    print(f"  学習期間     : 直近 {train_months}ヶ月分（race_id 時系列ウィンドウ）")
    print(f"  検証期間     : 直近 {holdout_months}ヶ月分（holdout）")
    print(f"  プロダクション: 汚染なし（tempdir にモデル保存）")

    # ── Step 1: DB 全データ読み込み ─────────────────────────────────
    section("Step 1: DB データ読み込み（全期間）")
    df_all = load_ultimate_training_frame(ULTIMATE_DB)
    print(f"  全データ: {len(df_all):,}行 × {len(df_all.columns)}列")
    if "race_id" not in df_all.columns:
        print("  ✗ race_id 列が見つかりません")
        sys.exit(1)

    # Quality Gate（distance=0 など除外）
    df_all = filter_valid_races(df_all, verbose=False)
    all_race_ids = sorted(df_all["race_id"].unique())
    n_total = len(all_race_ids)
    print(f"  有効レース数 : {n_total:,}")

    if n_total < 50:
        print("  ✗ 有効レースが少なすぎます（最低50レース必要）")
        sys.exit(1)

    # ── Step 2: 時系列分割 ──────────────────────────────────────────
    section("Step 2: 時系列分割（1年学習 + 2ヶ月 holdout）")

    # race_id の年を取得（先頭4桁）
    def get_year(rid: str) -> int:
        try: return int(rid[:4])
        except: return 0

    # race_id を時系列順ソート済み（JRA形式は辞書順=時系列順）
    # 直近 holdout_months に相当するレース数を推定
    # 月あたりのレース数は概ね (全レース) / (全月数) で推定
    first_year = get_year(all_race_ids[0])
    last_year  = get_year(all_race_ids[-1])
    total_months_approx = max(1, (last_year - first_year) * 12 + 6)  # 概算（上半期仮定）
    races_per_month = n_total / total_months_approx

    # holdout レース数: 最後の holdout_months ヶ月分
    holdout_n = max(10, int(races_per_month * holdout_months))
    train_n   = max(20, int(races_per_month * train_months))

    # holdout = 最後の holdout_n レース
    holdout_ids = set(all_race_ids[-holdout_n:])
    # 学習 = holdout より前の最大 train_n レース
    before_holdout = [r for r in all_race_ids if r not in holdout_ids]
    train_ids = set(before_holdout[-train_n:])

    print(f"  全レース数       : {n_total:,}")
    print(f"  学習用レース数   : {len(train_ids):,}  ({before_holdout[-train_n]} ～ {before_holdout[-1]})")
    print(f"  holdout レース数 : {len(holdout_ids):,}  ({all_race_ids[-holdout_n]} ～ {all_race_ids[-1]})")

    if len(train_ids) < 20:
        print("  ⚠ 学習データが少なすぎます")
        sys.exit(1)

    df_train = df_all[df_all["race_id"].isin(train_ids)].copy()
    df_hold  = df_all[df_all["race_id"].isin(holdout_ids)].copy()

    print(f"\n  学習データ : {len(df_train):,}行")
    print(f"  検証データ : {len(df_hold):,}行")

    # ── Step 3: 特徴量エンジニアリング（全データで計算して分割） ──
    section("Step 3: 特徴量エンジニアリング")
    print("  add_derived_features（全データ）...")
    df_fe = add_derived_features(df_all.copy(), full_history_df=df_all.copy())
    print(f"  → {df_fe.shape[1]} 列")

    print("  UltimateFeatureCalculator（全データ）...")
    calc = UltimateFeatureCalculator(str(ULTIMATE_DB))
    df_fe = calc.add_ultimate_features(df_fe)
    df_fe = df_fe.loc[:, ~df_fe.columns.duplicated()]
    print(f"  → {df_fe.shape[1]} 列")

    # ── Step 4: LightGBM Optimizer (学習データのみでfit) ──────────
    section("Step 4: LightGBM Feature Optimizer fit（学習データのみ）")
    fin_col = "finish" if "finish" in df_fe.columns else "finish_position"
    if fin_col in df_fe.columns:
        fin_num = pd.to_numeric(df_fe[fin_col], errors="coerce")
        df_fe["win"]    = (fin_num == 1).astype(float)
        df_fe["place3"] = (fin_num <= 3).astype(float)
        df_fe.loc[fin_num.isna(), ["win", "place3"]] = np.nan

    optimizer = LightGBMFeatureOptimizer()
    # fit は学習データのみ、transform は全データ
    df_train_fe = df_fe[df_fe["race_id"].isin(train_ids)].copy()
    df_opt_train, cat_features = optimizer.fit_transform(df_train_fe, target_col="win")
    print(f"  最適化後（学習）: {df_opt_train.shape}")
    print(f"  カテゴリカル: {cat_features[:5]}...")

    # holdout も同じ optimizer で transform（データ漏洩なし）
    df_hold_fe = df_fe[df_fe["race_id"].isin(holdout_ids)].copy()
    df_opt_hold = optimizer.transform(df_hold_fe)
    print(f"  最適化後（holdout）: {df_opt_hold.shape}")

    # 特徴量カラム決定
    EXCLUDE = {"race_id", "horse_id", "jockey_id", "trainer_id", "owner_id",
               "win", "place3", "finish", "finish_position"}
    feat_cols = [c for c in df_opt_train.columns
                 if c not in EXCLUDE and df_opt_train[c].dtype != object]
    print(f"  特徴量数: {len(feat_cols)}")

    # 学習データ準備
    X_train = df_opt_train[feat_cols].copy()
    y_train = df_opt_train["win"].copy() if "win" in df_opt_train.columns else None
    if y_train is None:
        print("  ✗ win列が見つかりません")
        sys.exit(1)

    valid_mask = y_train.notna()
    X_train, y_train = X_train[valid_mask], y_train[valid_mask]
    groups_train = df_opt_train.loc[valid_mask, "race_id"] if "race_id" in df_opt_train.columns else pd.Series(range(len(X_train)))
    print(f"  学習行数: {len(X_train):,}  正例: {y_train.sum():.0f} ({y_train.mean()*100:.1f}%)")

    # ── Step 5: LightGBM 学習 ────────────────────────────────────────
    section("Step 5: モデル学習（GroupKFold CV × 3）")
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
    oof_preds = np.zeros(len(X_train))

    for fold, (tr_idx, va_idx) in enumerate(gkf.split(X_train, y_train, groups_train), 1):
        X_tr, X_va = X_train.iloc[tr_idx], X_train.iloc[va_idx]
        y_tr, y_va = y_train.iloc[tr_idx], y_train.iloc[va_idx]
        ds_tr = lgb.Dataset(X_tr, y_tr, categorical_feature=cat_features)
        ds_va = lgb.Dataset(X_va, y_va, categorical_feature=cat_features)
        m = lgb.train(
            params, ds_tr, num_boost_round=200,
            valid_sets=[ds_va],
            callbacks=[lgb.early_stopping(20, verbose=False), lgb.log_evaluation(-1)],
        )
        preds = m.predict(X_va)
        auc = roc_auc_score(y_va, preds)
        cv_aucs.append(auc)
        oof_preds[va_idx] = preds
        print(f"  Fold{fold} AUC = {auc:.4f}")

    cv_mean = float(np.mean(cv_aucs))
    cv_std  = float(np.std(cv_aucs))
    print(f"  CV AUC: {cv_mean:.4f} ± {cv_std:.4f}")

    # キャリブレーション
    calibrator = IsotonicRegression(out_of_bounds="clip")
    calibrator.fit(oof_preds, y_train.values)

    # 全学習データで最終モデル
    ds_all_train = lgb.Dataset(X_train, y_train, categorical_feature=cat_features)
    final_model = lgb.train(
        params, ds_all_train, num_boost_round=200,
        callbacks=[lgb.log_evaluation(-1)],
    )
    full_preds = final_model.predict(X_train)
    full_auc = roc_auc_score(y_train, full_preds)
    print(f"  全データAUC (過学習確認): {full_auc:.4f}")

    # ── Step 6: temp ディレクトリにモデル保存（本番汚染なし） ───────
    section("Step 6: モデル保存（tempdir / 本番への影響なし）")
    ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    tmp_dir = Path(tempfile.mkdtemp(prefix="keiba_e2e_"))
    tmp_model_path = tmp_dir / f"model_e2e_verify_{ts}.joblib"

    bundle_tmp = {
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
        "data_count": len(X_train),
        "race_count": int(groups_train.nunique()),
        "train_race_id_from": str(groups_train.min()),
        "train_race_id_to":   str(groups_train.max()),
        "timestamp": ts,
        "note": "e2e_verify_timesplit - NOT PRODUCTION",
    }
    joblib.dump(bundle_tmp, tmp_model_path)
    print(f"  → {tmp_model_path}")
    print(f"  ※ このモデルは本番 python-api/models/ に保存されていません")

    # ── Step 7: Holdout 評価 ─────────────────────────────────────────
    section("Step 7: Holdout 評価（直近2ヶ月相当）")

    # holdout の特徴量列を学習と揃える
    missing_cols = [c for c in feat_cols if c not in df_opt_hold.columns]
    for c in missing_cols:
        df_opt_hold[c] = 0.0
    X_hold = df_opt_hold[feat_cols].copy()

    # holdout の正解ラベル
    if fin_col in df_hold_fe.columns:
        fin_hold = pd.to_numeric(df_hold_fe[fin_col].values, errors="coerce")
    elif "finish" in df_opt_hold.columns:
        fin_hold = pd.to_numeric(df_opt_hold["finish"].values, errors="coerce")
    else:
        fin_hold = np.full(len(X_hold), np.nan)

    y_hold = np.where(fin_hold == 1, 1.0, 0.0)
    valid_hold = ~np.isnan(fin_hold)
    X_hold_v = X_hold[valid_hold]
    y_hold_v  = y_hold[valid_hold]

    if len(X_hold_v) == 0:
        print("  ✗ holdout データが0件（finish列が取れていない可能性）")
        sys.exit(1)

    # p_raw（生スコア）
    p_raw_arr = final_model.predict(X_hold_v)
    # win_probability（キャリブ後）
    wp_arr = calibrator.predict(p_raw_arr)

    hold_auc = roc_auc_score(y_hold_v, p_raw_arr)
    hold_ll  = log_loss(y_hold_v, np.clip(p_raw_arr, 1e-7, 1 - 1e-7))
    print(f"  Holdout AUC     : {hold_auc:.4f}")
    print(f"  Holdout LogLoss : {hold_ll:.4f}")
    print(f"  Holdout 件数    : {len(X_hold_v):,}行")

    # 単勝的中率
    hold_meta = df_opt_hold[valid_hold][["race_id"]].copy() if "race_id" in df_opt_hold.columns else pd.DataFrame()
    if "horse_number" in df_opt_hold.columns:
        hold_meta["horse_number"] = df_opt_hold[valid_hold]["horse_number"].values
    hold_meta = hold_meta.reset_index(drop=True)
    hold_meta["p_raw"]  = p_raw_arr
    hold_meta["win"]    = y_hold_v
    hold_meta["finish"] = fin_hold[valid_hold]

    hits = 0
    total_races_hold = 0
    for rid, grp in hold_meta.groupby("race_id"):
        if grp["win"].sum() == 0:
            continue
        total_races_hold += 1
        pred_winner = grp["p_raw"].idxmax()
        actual_winner = grp[grp["win"] == 1].index
        if len(actual_winner) > 0 and pred_winner == actual_winner[0]:
            hits += 1

    win_acc = hits / total_races_hold if total_races_hold > 0 else 0.0
    print(f"  単勝的中率      : {hits}/{total_races_hold} = {win_acc:.1%}")

    # ── Step 8: フロントエンド互換 JSON 出力 ─────────────────────────
    section("Step 8: フロントエンド互換 JSON 出力（p_raw/p_norm/EV/predicted_rank）")

    # レースごとに p_norm 計算 + predicted_rank 付与
    hold_meta["wp"] = wp_arr
    output_races = []

    for rid, grp in hold_meta.groupby("race_id"):
        grp = grp.copy()
        # p_norm = p_raw / sum(p_raw) でレース内確率に正規化
        sum_raw = grp["p_raw"].sum()
        grp["p_norm"] = grp["p_raw"] / sum_raw if sum_raw > 0 else grp["p_raw"]
        # p_raw 降順で predicted_rank
        grp = grp.sort_values("p_raw", ascending=False).reset_index(drop=True)
        grp["predicted_rank"] = range(1, len(grp) + 1)
        output_races.append(grp)

    df_output = pd.concat(output_races, ignore_index=True)

    # EV（p_norm × odds）はoddsが今回のデータにないためスキップ（p_norm のみ出力）
    has_odds = "odds" in df_opt_hold.columns
    if has_odds:
        odds_vals = df_opt_hold[valid_hold]["odds"].values
        # oddsマップ（index合わせ）
        odds_map = pd.Series(odds_vals, index=df_opt_hold[valid_hold].index).reset_index(drop=True)
        # hold_metaのindexはreset済みなのでoddsも同順で対応
        # ただし race_groupbyで並び替えたためdf_outputのindexが変わっている
        # → 個別raceid/horse_numberでjoinが安全
        if "horse_number" in hold_meta.columns:
            df_output["expected_value"] = None  # 後で計算
            # build odds lookup
            odds_lookup = {}
            for idx_r, row_r in df_opt_hold[valid_hold].reset_index(drop=True).iterrows():
                key = (str(row_r.get("race_id", "")), str(row_r.get("horse_number", "")))
                odds_lookup[key] = float(row_r.get("odds", 0) or 0)
            for i, row in df_output.iterrows():
                key = (str(row.get("race_id", "")), str(row.get("horse_number", "")))
                o = odds_lookup.get(key, 0.0)
                df_output.at[i, "expected_value"] = round(row["p_norm"] * o, 4) if o > 0 else None

    # JSON生成（先頭5レースのサンプル）
    sample_races_json = []
    for rid, grp in df_output.groupby("race_id"):
        horses = []
        for _, row in grp.iterrows():
            h = {
                "horse_number": int(row.get("horse_number", 0)) if "horse_number" in row else None,
                "predicted_rank": int(row["predicted_rank"]),
                "win_probability": round(float(row["wp"]), 6),
                "p_raw": round(float(row["p_raw"]), 8),
                "p_norm": round(float(row["p_norm"]), 8),
                "actual_finish": int(row["finish"]) if pd.notna(row["finish"]) else None,
                "actual_win": int(row["win"]),
            }
            if has_odds and "expected_value" in df_output.columns:
                ev = row.get("expected_value")
                h["expected_value"] = round(float(ev), 4) if ev is not None and pd.notna(ev) else None
            horses.append(h)

        # p_norm の合計チェック
        p_norm_sum = sum(h["p_norm"] for h in horses)
        # 上位馬が勝ったか
        top1 = next((h for h in horses if h["predicted_rank"] == 1), None)
        hit = top1["actual_win"] == 1 if top1 else False

        sample_races_json.append({
            "race_id": rid,
            "n_horses": len(horses),
            "p_norm_sum": round(p_norm_sum, 6),
            "top1_hit": hit,
            "horses": horses,
        })

        if len(sample_races_json) >= 5:
            break

    # JSONファイル出力
    today_str = datetime.now(JST).strftime("%Y%m%d%H%M")
    out_json = OUT / f"e2e_verify_predictions_{today_str}.json"
    output_summary = {
        "generated_at": datetime.now(JST).isoformat(),
        "note": "e2e_verify_timesplit - NOT PRODUCTION MODEL",
        "train_config": {
            "train_months": train_months,
            "holdout_months": holdout_months,
            "train_races": len(train_ids),
            "holdout_races": len(holdout_ids),
            "feature_count": len(feat_cols),
        },
        "model_metrics": {
            "cv_auc_mean": round(cv_mean, 4),
            "cv_auc_std": round(cv_std, 4),
            "holdout_auc": round(hold_auc, 4),
            "holdout_logloss": round(hold_ll, 4),
            "win_accuracy": round(win_acc, 4),
            "win_hits": hits,
            "win_total_races": total_races_hold,
        },
        "temp_model_path": str(tmp_model_path),
        "sample_races": sample_races_json,
    }
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(output_summary, f, ensure_ascii=False, indent=2)
    print(f"  → {out_json.name}")

    # ── 最終サマリ ────────────────────────────────────────────────────
    section("E2E 検証サマリ")
    print(f"  学習データ   : {len(train_ids):,}レース  ({df_train.shape[0]:,}行)")
    print(f"  holdout      : {len(holdout_ids):,}レース  ({len(X_hold_v):,}行)")
    print(f"  特徴量数     : {len(feat_cols)}")
    print()
    print(f"  CV AUC       : {cv_mean:.4f} ± {cv_std:.4f}")
    print(f"  Holdout AUC  : {hold_auc:.4f}")
    print(f"  Holdout LL   : {hold_ll:.4f}")
    print(f"  単勝的中率   : {hits}/{total_races_hold} = {win_acc:.1%}")
    print()
    print(f"  サンプル JSON: {out_json.name}")
    print(f"  tempモデル   : {tmp_model_path}")
    print()

    # p_norm 整合性確認
    p_norm_ok = all(abs(r["p_norm_sum"] - 1.0) < 0.001 for r in sample_races_json)
    print(f"  p_norm 合計≒1: {'✓ ALL OK' if p_norm_ok else '✗ 異常あり'}")

    # サンプルレース表示
    print()
    print("  ── サンプル1レースの予測 ──")
    if sample_races_json:
        r = sample_races_json[0]
        print(f"  race_id: {r['race_id']}  ({r['n_horses']}頭)  p_norm合計={r['p_norm_sum']}")
        print(f"  {'rank':>4} {'#':>3} {'p_raw':>12} {'p_norm':>10} {'wp(cal)':>10} {'EV':>7} {'actual':>7}")
        print(f"  {'-'*60}")
        for h in r["horses"]:
            ev_str = f"{h.get('expected_value', 0) or 0:.2f}" if h.get("expected_value") is not None else "  N/A"
            fin_str = str(h.get("actual_finish", "?")) if h.get("actual_finish") else "?"
            print(f"  {h['predicted_rank']:>4}  {h.get('horse_number') or '?':>3}  "
                  f"{h['p_raw']:>12.8f} {h['p_norm']:>10.6f} "
                  f"{h['win_probability']:>10.6f} {ev_str:>7} {fin_str:>7}")

    print()
    print(f"  ✓ E2E 検証完了（プロダクション環境への影響: なし）")
    print()
    print("  【スクレイピング運用まとめ】")
    print("  - netkeiba はローカルPCのIPでスクレイプ（住宅IP = Cloudflareブロック低）")
    print("  - 毎週末：python tools/scrape_and_validate.py --date <週末日付>")
    print("  - データ蓄積後：python tools/retrain_local.py → モデル更新")
    print("  - git push → Railway は新モデルを自動デプロイ（スクレイプしない）")
    print("  - 緊急時はSCRAPE_PROXY_URL 環境変数で住宅型プロキシ経由に切替可能")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="E2E パイプライン検証（時系列分割・本番非汚染）")
    parser.add_argument("--train-months", type=int, default=12, help="学習期間（ヶ月）")
    parser.add_argument("--holdout-months", type=int, default=2, help="holdout期間（ヶ月）")
    args = parser.parse_args()
    main(train_months=args.train_months, holdout_months=args.holdout_months)
