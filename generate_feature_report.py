"""
特徴量可視化レポート生成スクリプト
出力: feature_report.html（ブラウザで開ける単一HTMLファイル）

可視化内容:
  1. LightGBM 特徴量重要度（gain / split）
  2. SHAP値（各特徴量の影響方向と大きさ）
  3. 欠損値マップ
  4. 数値特徴量の相関ヒートマップ（上位30列）
"""
import sys, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, 'keiba')

import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import lightgbm as lgb

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

# ID・目的変数を除外して学習用Xを作成
EXCLUDE = {'race_id', 'horse_id', 'jockey_id', 'trainer_id', 'win', 'place'}
X = X_all.drop(columns=[c for c in EXCLUDE if c in X_all.columns])
y = X_all['win'].values

# object型を強制的に除去
obj_cols = [c for c in X.columns if X[c].dtype == object]
if obj_cols:
    print(f"  object型を除外: {obj_cols}")
    X = X.drop(columns=obj_cols)

print(f"学習データ: {X.shape}")

# ── LightGBM 学習 ────────────────────────────────────────────────────────────
print("LightGBM 学習中...")
cat_feats_available = [c for c in (cat_feats or []) if c in X.columns]
dtrain = lgb.Dataset(X, label=y, categorical_feature=cat_feats_available or 'auto')
params = {
    'objective': 'binary',
    'metric': 'auc',
    'num_leaves': 31,
    'learning_rate': 0.05,
    'verbose': -1,
    'n_jobs': -1,
}
model = lgb.train(params, dtrain, num_boost_round=200,
                  callbacks=[lgb.log_evaluation(-1)])

# ── 特徴量重要度 ─────────────────────────────────────────────────────────────
imp_gain  = pd.Series(model.feature_importance('gain'),  index=X.columns).sort_values(ascending=False)
imp_split = pd.Series(model.feature_importance('split'), index=X.columns).sort_values(ascending=False)

top_n = 40
fig_imp = make_subplots(rows=1, cols=2,
                        subplot_titles=['特徴量重要度（Gain）', '特徴量重要度（Split回数）'])

fig_imp.add_trace(
    go.Bar(x=imp_gain.head(top_n).values[::-1],
           y=imp_gain.head(top_n).index[::-1],
           orientation='h', marker_color='steelblue', name='Gain'),
    row=1, col=1
)
fig_imp.add_trace(
    go.Bar(x=imp_split.head(top_n).values[::-1],
           y=imp_split.head(top_n).index[::-1],
           orientation='h', marker_color='coral', name='Split'),
    row=1, col=2
)
fig_imp.update_layout(height=900, title_text='LightGBM 特徴量重要度（上位40特徴量）',
                      showlegend=False)

# ── SHAP値 ──────────────────────────────────────────────────────────────────
print("SHAP値計算中（時間がかかる場合があります）...")
import shap
# サンプリング（全件だと遅い）
sample_idx = np.random.choice(len(X), min(500, len(X)), replace=False)
X_sample = X.iloc[sample_idx]

explainer = shap.TreeExplainer(model)
shap_values = explainer.shap_values(X_sample)

shap_df = pd.DataFrame(np.abs(shap_values), columns=X.columns)
shap_mean = shap_df.mean().sort_values(ascending=False).head(top_n)

fig_shap = go.Figure(go.Bar(
    x=shap_mean.values[::-1],
    y=shap_mean.index[::-1],
    orientation='h',
    marker_color='mediumseagreen',
))
fig_shap.update_layout(
    height=900,
    title_text=f'SHAP 平均絶対値（上位{top_n}特徴量, n={len(X_sample)}サンプル）',
    xaxis_title='mean(|SHAP value|)',
)

# ── 欠損値マップ ─────────────────────────────────────────────────────────────
miss_pct = (X.isna().sum() / len(X) * 100).sort_values(ascending=False)
miss_pct = miss_pct[miss_pct > 0]

if len(miss_pct) > 0:
    fig_miss = go.Figure(go.Bar(
        x=miss_pct.values,
        y=miss_pct.index,
        orientation='h',
        marker_color='tomato',
    ))
    fig_miss.update_layout(
        height=max(400, len(miss_pct) * 22),
        title_text='特徴量別 欠損率（%）',
        xaxis_title='欠損率 (%)',
    )
else:
    fig_miss = go.Figure()
    fig_miss.add_annotation(text="欠損なし ✅", x=0.5, y=0.5, showarrow=False, font_size=20)
    fig_miss.update_layout(title_text='欠損値マップ')

# ── 相関ヒートマップ（数値列上位30） ────────────────────────────────────────
print("相関行列計算中...")
num_cols = X.select_dtypes(include=[np.number]).columns.tolist()
# SHAP重要度上位30列
top_cols_for_corr = [c for c in shap_mean.head(30).index if c in num_cols]
corr = X[top_cols_for_corr].corr()

fig_corr = go.Figure(go.Heatmap(
    z=corr.values,
    x=corr.columns,
    y=corr.index,
    colorscale='RdBu',
    zmid=0,
    text=np.round(corr.values, 2),
    texttemplate='%{text}',
    textfont_size=8,
))
fig_corr.update_layout(
    height=700,
    title_text='相関ヒートマップ（SHAP重要度上位30特徴量）',
)

# ── 特徴量分布（SHAP上位12） ─────────────────────────────────────────────────
top12 = [c for c in shap_mean.head(12).index if c in X.columns]
rows_d, cols_d = 3, 4
fig_dist = make_subplots(rows=rows_d, cols=cols_d,
                          subplot_titles=[c[:25] for c in top12])
for i, col in enumerate(top12):
    r, c = divmod(i, cols_d)
    vals = X[col].dropna()
    fig_dist.add_trace(
        go.Histogram(x=vals, name=col, showlegend=False,
                     marker_color='royalblue', opacity=0.7),
        row=r+1, col=c+1
    )
fig_dist.update_layout(height=700, title_text='重要特徴量の分布（上位12）')

# ── HTML出力 ─────────────────────────────────────────────────────────────────
print("HTML生成中...")

html_parts = []

def fig_to_div(fig):
    return fig.to_html(full_html=False, include_plotlyjs=False)

html_parts.append("""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>競馬AI 特徴量レポート</title>
<script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
<style>
  body { font-family: 'Helvetica Neue', Arial, sans-serif; margin: 20px; background: #f5f5f5; }
  h1 { color: #333; border-bottom: 3px solid #4a90d9; padding-bottom: 10px; }
  h2 { color: #555; margin-top: 40px; border-left: 5px solid #4a90d9; padding-left: 10px; }
  .card { background: white; border-radius: 8px; padding: 20px; margin: 20px 0;
          box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
  .meta { color: #666; font-size: 14px; }
  .stats-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin: 20px 0; }
  .stat-box { background: #4a90d9; color: white; border-radius: 8px; padding: 16px; text-align: center; }
  .stat-box .val { font-size: 28px; font-weight: bold; }
  .stat-box .lbl { font-size: 13px; opacity: 0.9; }
</style>
</head>
<body>
""")

# サマリ
total_rows = len(X)
miss_count = (X.isna().sum() > 0).sum()
win_rate = float(y.mean() * 100)
html_parts.append(f"""
<h1>🏇 競馬AI 特徴量可視化レポート</h1>
<div class="card meta">
  <p>生成日時: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}&nbsp;&nbsp;
     データ: 2026年 96レース / 1,495頭</p>
</div>
<div class="stats-grid">
  <div class="stat-box"><div class="val">{len(X.columns)}</div><div class="lbl">学習特徴量数</div></div>
  <div class="stat-box"><div class="val">{total_rows:,}</div><div class="lbl">サンプル数</div></div>
  <div class="stat-box"><div class="val">{miss_count}</div><div class="lbl">欠損ありカラム</div></div>
  <div class="stat-box"><div class="val">{win_rate:.1f}%</div><div class="lbl">1着率</div></div>
</div>

<h2>1. 特徴量重要度（LightGBM）</h2>
<div class="card">
<p class="meta">Gain: 各特徴量が分岐に使われた際の不純度削減量の合計。モデルへの貢献度の主指標。<br>
Split: 各特徴量が分岐に使われた回数。Gainと組み合わせて解釈する。</p>
""")
html_parts.append(fig_to_div(fig_imp))
html_parts.append("</div>")

html_parts.append("""<h2>2. SHAP値（予測への影響量）</h2>
<div class="card">
<p class="meta">各特徴量が個々の予測値を平均的にどれだけ動かしているかの絶対値平均。<br>
Gainと異なり、特徴量間のスケール差を考慮した解釈可能な重要度。</p>
""")
html_parts.append(fig_to_div(fig_shap))
html_parts.append("</div>")

html_parts.append("""<h2>3. 欠損値マップ</h2>
<div class="card">
<p class="meta">LightGBMはNaNを自動処理するため欠損自体は問題なし。ただし欠損パターンが予測に使われている可能性がある。</p>
""")
html_parts.append(fig_to_div(fig_miss))
html_parts.append("</div>")

html_parts.append("""<h2>4. 相関ヒートマップ（重要特徴量上位30）</h2>
<div class="card">
<p class="meta">相関係数が±0.8以上の特徴量ペアは多重共線性のリスクあり。LightGBMは多重共線性に比較的ロバストだが、解釈に注意。</p>
""")
html_parts.append(fig_to_div(fig_corr))
html_parts.append("</div>")

html_parts.append("""<h2>5. 重要特徴量の分布（上位12）</h2>
<div class="card">
<p class="meta">分布の偏りや外れ値を確認。LightGBMはスケール不変だが、極端な外れ値はモデルの分岐集中を引き起こす場合がある。</p>
""")
html_parts.append(fig_to_div(fig_dist))
html_parts.append("</div>")

html_parts.append("</body></html>")

out_path = 'feature_report.html'
with open(out_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(html_parts))

print(f"\n✅ レポート生成完了: {out_path}")
print(f"   → ブラウザで開いてください")
