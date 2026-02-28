"""
ydata-profiling による特徴量自動レポート生成
出力: profiling_report.html
"""
import sys, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, 'keiba')

import pandas as pd
import numpy as np

# ── データ準備 ──────────────────────────────────────────────────────────────
from keiba_ai.db_ultimate_loader import load_ultimate_training_frame
from keiba_ai.feature_engineering import add_derived_features
from keiba_ai.lightgbm_feature_optimizer import prepare_for_lightgbm_ultimate

print("データ読み込み中...")
df = load_ultimate_training_frame('keiba/data/keiba_local_validate.db')
df = add_derived_features(df, full_history_df=df.copy())

fin_col = 'finish' if 'finish' in df.columns else 'finish_position'
fin = pd.to_numeric(df[fin_col], errors='coerce')
df['win'] = (fin == 1).astype(int)
df['place'] = (fin <= 3).astype(int)

print("特徴量最適化中...")
X_all, opt, cat_feats = prepare_for_lightgbm_ultimate(df.copy(), target_col='win')

# ID列のみ除外（win/place は目的変数として残す）
ID_COLS = ['race_id', 'horse_id', 'jockey_id', 'trainer_id']
report_df = X_all.drop(columns=[c for c in ID_COLS if c in X_all.columns])

# object型除去
obj_cols = [c for c in report_df.columns if report_df[c].dtype == object]
if obj_cols:
    print(f"  object型を除外: {obj_cols}")
    report_df = report_df.drop(columns=obj_cols)

print(f"レポート対象: {report_df.shape[0]}行 × {report_df.shape[1]}列")

# ── ydata-profiling レポート生成 ─────────────────────────────────────────────
from ydata_profiling import ProfileReport

print("ydata-profiling レポート生成中（数分かかる場合があります）...")

profile = ProfileReport(
    report_df,
    title="🏇 競馬AI 特徴量プロファイリングレポート",
    dataset={
        "description": "競馬AIモデルの学習特徴量（2026年 地方競馬 96レース / 1,495頭）",
        "url": "https://keiba-ai-api.onrender.com",
    },
    # 相関: Pearson / Spearman / Kendall / Cramér's V
    correlations={
        "pearson":  {"calculate": True},
        "spearman": {"calculate": True},
        "kendall":  {"calculate": False},  # 低速のためOFF
        "phi_k":    {"calculate": False},
        "cramers":  {"calculate": True},
    },
    # 欠損値マトリックス・ヒートマップ
    missing_diagrams={
        "bar":    True,
        "matrix": True,
        "heatmap": True,
    },
    # インタラクション（散布図）: 上位特徴量のみ
    interactions={
        "continuous": True,
        "targets": ["win", "place"],
    },
    # サンプル表示
    samples={
        "head": 10,
        "tail": 10,
    },
    # 重複行チェック
    duplicates={"head": 10},
    # 進捗バー表示
    progress_bar=True,
    # HTMLをminify
    html={
        "minify_html": True,
        "navbar_show": True,
        "full_width": True,
    },
    explorative=True,  # より詳細な統計（分位数など）
)

out_path = "profiling_report.html"
print(f"HTML出力中: {out_path}")
profile.to_file(out_path)

print(f"\n✅ レポート生成完了: {out_path}")
print(f"   → ブラウザで開いてください")
