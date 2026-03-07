"""
ドリフトレポート: 学習データ vs 推論データの特徴量差分を定量評価
==================================================================

使用方法:
  # 基本: pipeline_output の学習特徴量 vs 最新推論レース
  python tools/drift_report.py

  # 推論 CSV を明示指定
  python tools/drift_report.py --infer tools/pipeline_output/05_prediction_features_202610011210.csv

  # カテゴリドリフトも含めて出力
  python tools/drift_report.py --include-cat

出力:
  tools/drift_report_output.csv   … PSI/欠損率差分 テーブル
  コンソール … 要注意(PSI>=0.2 or 欠損率差分>=20%)列の一覧

ドリフト指標の目安:
  PSI < 0.10  : no change (無視可)
  0.10-0.20   : moderate change (監視推奨)
  PSI >= 0.20 : significant shift (再学習検討)
  欠損率差分 >= 20%  : 入力品質の劣化 → Quality Gate で確認
"""

from __future__ import annotations

import argparse
import sys
import os
from pathlib import Path
from typing import Optional, List

import pandas as pd
import numpy as np

# project root をパスに追加
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "keiba"))
sys.path.insert(0, str(ROOT / "python-api"))

from keiba_ai.quality_gate import check_feature_drift, validate_race_entries  # type: ignore


OUTPUT_DIR = ROOT / "tools" / "pipeline_output"
DRIFT_OUT  = ROOT / "tools" / "drift_report_output.csv"


def load_train_features(path: Optional[Path] = None) -> pd.DataFrame:
    """pipeline_output/02_feature_engineered.csv (学習特徴量) を読み込む"""
    if path is None:
        path = OUTPUT_DIR / "02_feature_engineered.csv"
    if not path.exists():
        raise FileNotFoundError(f"学習特徴量ファイルが見つかりません: {path}")
    df = pd.read_csv(path, encoding="utf-8-sig")
    print(f"[train] {path.name}: {df.shape[0]} rows x {df.shape[1]} cols")
    return df


def load_infer_features(path: Optional[Path] = None) -> pd.DataFrame:
    """推論特徴量 CSV を読み込む (デフォルト: 最新の 05_prediction_features_*.csv)"""
    if path is None:
        candidates = sorted(OUTPUT_DIR.glob("05_prediction_features_*.csv"))
        if not candidates:
            raise FileNotFoundError("05_prediction_features_*.csv が見つかりません")
        path = candidates[-1]  # 最新
    if not path.exists():
        raise FileNotFoundError(f"推論特徴量ファイルが見つかりません: {path}")
    df = pd.read_csv(path, encoding="utf-8-sig")
    print(f"[infer] {path.name}: {df.shape[0]} rows x {df.shape[1]} cols")
    return df


def report_category_drift(
    train_df: pd.DataFrame,
    infer_df: pd.DataFrame,
    cat_cols: Optional[list] = None,
) -> pd.DataFrame:
    """カテゴリカル列の分布差分をカウントレベルで比較する"""
    if cat_cols is None:
        cat_cols = []
        for df in (train_df, infer_df):
            cat_cols += df.select_dtypes(include=["object", "category"]).columns.tolist()
        cat_cols = list(set(cat_cols))

    rows = []
    for col in sorted(cat_cols):
        t_counts = train_df[col].value_counts(normalize=True) if col in train_df.columns else pd.Series(dtype=float)
        i_counts = infer_df[col].value_counts(normalize=True) if col in infer_df.columns else pd.Series(dtype=float)
        # 推論側に学習にない新規カテゴリが出ていないか
        new_cats = set(i_counts.index) - set(t_counts.index)
        rows.append({
            "feature": col,
            "train_unique": len(t_counts),
            "infer_unique": len(i_counts),
            "new_categories": sorted(new_cats)[:5],  # 最大5件
            "n_new_categories": len(new_cats),
        })
    return pd.DataFrame(rows)


def print_drift_summary(drift_df: pd.DataFrame, top_n: int = 20) -> None:
    """PSI が高い順 / 欠損ドリフトが大きい順に要注意列を表示"""
    flagged = drift_df[drift_df["drift_flag"] == True].copy()
    if flagged.empty:
        print("\n✅ ドリフトの兆候は検出されませんでした（PSI < 0.20 かつ 欠損率差分 < 20%）")
        return

    print(f"\n⚠  ドリフト要注意: {len(flagged)} 列")
    print("-" * 80)
    cols_show = ["feature", "train_missing_pct", "infer_missing_pct",
                 "missing_drift", "train_median", "infer_median", "psi"]
    # psi で降順ソート
    flagged = flagged.sort_values("psi", ascending=False, na_position="last")
    print(flagged[cols_show].head(top_n).to_string(index=False))


def main():
    parser = argparse.ArgumentParser(description="学習 vs 推論 特徴量ドリフトレポート")
    parser.add_argument("--train", type=str, default=None,
                        help="学習特徴量 CSV パス (default: pipeline_output/02_feature_engineered.csv)")
    parser.add_argument("--infer", type=str, default=None,
                        help="推論特徴量 CSV パス (default: 最新の 05_prediction_features_*.csv)")
    parser.add_argument("--include-cat", action="store_true",
                        help="カテゴリカル変数のドリフトも含める")
    parser.add_argument("--psi-threshold", type=float, default=0.2,
                        help="PSI のアラートしきい値 (default: 0.2)")
    parser.add_argument("--out", type=str, default=str(DRIFT_OUT),
                        help="出力 CSV パス")
    args = parser.parse_args()

    print("=" * 70)
    print("ドリフトレポート: 学習データ vs 推論データ")
    print("=" * 70)

    train_df = load_train_features(Path(args.train) if args.train else None)
    infer_df = load_infer_features(Path(args.infer) if args.infer else None)

    # ── [Quality Gate] 推論データの入力品質チェック ─────────────────────────
    print("\n[Quality Gate] 推論データの整合チェック:")
    if "race_id" in infer_df.columns:
        qr = validate_race_entries(infer_df)
        print(qr.summary())
    else:
        print("  race_id 列なし → 全体を 1 レースとしてチェック")
        qr = validate_race_entries(infer_df)
        print(qr.summary())

    # ── 数値特徴量ドリフト ──────────────────────────────────────────────────
    print("\n[数値特徴量ドリフト] PSI / 欠損率差分 計算中...")
    # 両方にある数値列に絞る
    num_cols_train = set(train_df.select_dtypes(include=[np.number]).columns)
    num_cols_infer = set(infer_df.select_dtypes(include=[np.number]).columns)
    # ID・フラグ系は除外
    skip_prefix = ("race_id", "horse_id", "jockey_id", "trainer_id")
    num_cols = sorted([
        c for c in (num_cols_train & num_cols_infer)
        if not any(c.startswith(p) for p in skip_prefix)
    ])

    drift_df = check_feature_drift(
        train_df, infer_df,
        numeric_cols=num_cols,
        psi_threshold=args.psi_threshold,
    )

    print_drift_summary(drift_df, top_n=25)

    # ── カテゴリドリフト ─────────────────────────────────────────────────────
    if args.include_cat:
        print("\n[カテゴリ変数ドリフト]")
        cat_drift = report_category_drift(train_df, infer_df)
        new_cat_rows = cat_drift[cat_drift["n_new_categories"] > 0]
        if new_cat_rows.empty:
            print("  ✅ 新規カテゴリ値なし")
        else:
            print(f"  ⚠  {len(new_cat_rows)} 列で学習未知のカテゴリを検出:")
            print(new_cat_rows[["feature", "train_unique", "infer_unique",
                                "n_new_categories", "new_categories"]].to_string(index=False))

    # ── 欠損率比較サマリ ─────────────────────────────────────────────────────
    print("\n[欠損率サマリ (train vs infer)] 上位10:")
    miss_df = drift_df[["feature", "train_missing_pct", "infer_missing_pct", "missing_drift"]]\
        .sort_values("missing_drift", ascending=False).head(10)
    print(miss_df.to_string(index=False))

    # ── CSV 保存 ─────────────────────────────────────────────────────────────
    out_path = Path(args.out)
    drift_df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n✅ 結果を保存しました: {out_path}")
    print(f"   {len(drift_df)} 列 / {drift_df['drift_flag'].sum()} 列が要注意フラグ")


if __name__ == "__main__":
    main()
