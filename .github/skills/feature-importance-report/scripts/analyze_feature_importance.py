#!/usr/bin/env python3
"""
特徴量重要度レポート生成スクリプト
Usage: python analyze_feature_importance.py [--model <path>] [--out <output_html>]

出力: docs/reports/feature_importance_<timestamp>.html
"""
import sys
import os
import argparse
import warnings
import glob
from datetime import datetime

warnings.filterwarnings("ignore")

# --- パス設定 ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
PYTHON_API_DIR = os.path.join(REPO_ROOT, "python-api")
KEIBA_DIR = os.path.join(REPO_ROOT, "keiba")
REPORTS_DIR = os.path.join(REPO_ROOT, "docs", "reports")

sys.path.insert(0, PYTHON_API_DIR)
sys.path.insert(0, KEIBA_DIR)


def find_latest_model(models_dir: str) -> str | None:
    candidates = glob.glob(os.path.join(models_dir, "model_win_lightgbm_*.joblib"))
    if not candidates:
        return None
    # 更新日時が最新のファイルを返す（ファイル名のソートより確実）
    return max(candidates, key=os.path.getmtime)


def load_bundle(model_path: str):
    import joblib
    return joblib.load(model_path)


def compute_importance(booster, importance_type: str = "gain"):
    """lightgbm.basic.Booster から重要度を取得して dict リストで返す"""
    names = booster.feature_name()
    values = booster.feature_importance(importance_type=importance_type)
    total = max(values.sum(), 1)
    items = [
        {"feature": n, "importance": float(v), "pct": float(v) / total * 100}
        for n, v in zip(names, values)
    ]
    items.sort(key=lambda x: x["importance"], reverse=True)
    return items


def categorize_features(feature_names: list[str]) -> dict[str, list[str]]:
    """特徴量をカテゴリに分類"""
    CATEGORIES = {
        "オッズ・市場": ["odds", "popularity", "market_entropy", "top3_probability",
                        "implied_prob", "log_odds", "odds_rank_in_race", "odds_z_in_race"],
        "前走・過去成績": ["prev_race", "prev2_race", "past_10", "past_5", "past_3",
                          "recent_3", "finish_consistency", "past_5_weight"],
        "騎手・調教師": ["jockey", "trainer", "jt_combo"],
        "馬の能力": ["horse_distance", "horse_surface", "horse_venue", "horse_dist",
                    "horse_win_rate", "horse_total", "sire", "dam", "damsire", "log_prize"],
        "レース条件": ["distance", "surface", "venue", "track_type", "weather", "field_condition",
                      "race_class", "num_horses", "kai", "day"],
        "馬体・基本": ["age", "horse_weight", "horse_number", "bracket_number",
                        "burden_weight", "jockey_weight", "sex", "is_young", "is_prime", "is_veteran"],
        "ローテーション": ["days_since", "rest_", "is_first_race"],
        "コース": ["straight_length", "inner_bias", "inner_advantage", "corner_radius",
                    "gate_win_rate", "course_direction"],
        "欠損フラグ": ["_is_missing"],
    }
    result = {k: [] for k in CATEGORIES}
    result["その他"] = []
    for feat in feature_names:
        matched = False
        for cat, keywords in CATEGORIES.items():
            if any(kw in feat for kw in keywords):
                result[cat].append(feat)
                matched = True
                break
        if not matched:
            result["その他"].append(feat)
    return result


def generate_html_report(bundle: dict, model_path: str, output_path: str) -> None:
    booster = bundle["model"]
    feature_cols = bundle.get("feature_columns", booster.feature_name())
    metrics = bundle.get("metrics", {})
    timestamp = bundle.get("timestamp", "unknown")
    train_from = bundle.get("training_date_from", "")
    train_to = bundle.get("training_date_to", "")
    data_count = bundle.get("data_count", 0)
    race_count = bundle.get("race_count", 0)

    gain_items = compute_importance(booster, "gain")
    split_items = compute_importance(booster, "split")

    # カテゴリ別集計 (gain)
    categories = categorize_features([x["feature"] for x in gain_items])
    cat_gain: dict[str, float] = {}
    gain_map = {x["feature"]: x["importance"] for x in gain_items}
    for cat, feats in categories.items():
        cat_gain[cat] = sum(gain_map.get(f, 0) for f in feats)
    total_gain = max(sum(cat_gain.values()), 1)
    cat_pct = {k: v / total_gain * 100 for k, v in cat_gain.items() if v > 0}
    cat_pct_sorted = dict(sorted(cat_pct.items(), key=lambda x: x[1], reverse=True))

    # AUC 指標
    auc_train = metrics.get("auc", metrics.get("train_auc", None))
    cv_mean = metrics.get("cv_auc_mean", metrics.get("cv_mean", None))
    cv_std = metrics.get("cv_auc_std", metrics.get("cv_std", None))
    auc_gap = (auc_train - cv_mean) if (auc_train and cv_mean) else None

    # Top30 表の行
    def table_rows(items, limit=30):
        rows = []
        for i, it in enumerate(items[:limit], 1):
            bar_w = int(it["pct"] * 3)  # max ~300px
            rows.append(
                f'<tr><td>{i}</td><td class="fname">{it["feature"]}</td>'
                f'<td><div class="bar" style="width:{bar_w}px"></div></td>'
                f'<td class="num">{it["importance"]:,.1f}</td>'
                f'<td class="num">{it["pct"]:.2f}%</td></tr>'
            )
        return "\n".join(rows)

    gain_rows = table_rows(gain_items)
    split_rows = table_rows(split_items)

    # カテゴリ棒グラフ行
    cat_bar_rows = []
    for cat, pct in cat_pct_sorted.items():
        w = int(pct * 4)
        cat_bar_rows.append(
            f'<tr><td class="fname">{cat}</td>'
            f'<td><div class="bar cat-bar" style="width:{w}px"></div></td>'
            f'<td class="num">{pct:.1f}%</td></tr>'
        )
    cat_bar_html = "\n".join(cat_bar_rows)

    # AUC 警告
    overfit_warn = ""
    if auc_gap is not None and auc_gap > 0.15:
        overfit_warn = f'''<div class="warn">
            ⚠ 過学習の疑い: 訓練AUC ({auc_train:.4f}) と CV AUC ({cv_mean:.4f}) の差が
            {auc_gap:.4f} です。残留データリークがないか確認してください。
        </div>'''
    elif auc_gap is not None:
        overfit_warn = f'''<div class="ok">
            ✅ 過学習チェック: 訓練AUC={auc_train:.4f}、CV AUC={cv_mean:.4f}、差={auc_gap:.4f}（正常範囲）
        </div>'''

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>特徴量重要度レポート</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         margin: 0; padding: 24px; background: #f5f7fa; color: #1a1a2e; }}
  h1 {{ font-size: 1.6rem; margin-bottom: 4px; }}
  .subtitle {{ color: #666; font-size: 0.9rem; margin-bottom: 24px; }}
  .cards {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 32px; }}
  .card {{ background: white; border-radius: 8px; padding: 16px 20px;
           box-shadow: 0 1px 4px rgba(0,0,0,.08); min-width: 140px; }}
  .card .label {{ font-size: 0.75rem; color: #888; text-transform: uppercase; }}
  .card .value {{ font-size: 1.6rem; font-weight: 700; margin-top: 4px; }}
  .card .value.red {{ color: #e74c3c; }}
  .card .value.green {{ color: #27ae60; }}
  .card .value.blue {{ color: #2980b9; }}
  .card .value.orange {{ color: #e67e22; }}
  section {{ background: white; border-radius: 8px; padding: 20px 24px;
             box-shadow: 0 1px 4px rgba(0,0,0,.08); margin-bottom: 24px; }}
  h2 {{ font-size: 1.1rem; margin: 0 0 16px; border-left: 4px solid #3498db;
        padding-left: 10px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.85rem; }}
  th {{ background: #f0f4f8; text-align: left; padding: 8px 10px;
        border-bottom: 2px solid #dde; font-size: 0.75rem; color: #555; }}
  td {{ padding: 6px 10px; border-bottom: 1px solid #eef; vertical-align: middle; }}
  td.fname {{ font-family: monospace; font-size: 0.82rem; max-width: 260px;
              overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  td.num {{ text-align: right; color: #444; font-variant-numeric: tabular-nums; }}
  .bar {{ height: 14px; background: #3498db; border-radius: 3px; min-width: 2px; }}
  .cat-bar {{ background: #e67e22; }}
  tr:hover td {{ background: #f9fbff; }}
  .warn {{ background: #fff3cd; border: 1px solid #ffc107; border-radius: 6px;
           padding: 12px 16px; margin-bottom: 16px; font-size: 0.9rem; }}
  .ok {{ background: #d4edda; border: 1px solid #28a745; border-radius: 6px;
         padding: 12px 16px; margin-bottom: 16px; font-size: 0.9rem; }}
  .tabs {{ display: flex; gap: 8px; margin-bottom: 16px; }}
  .tab {{ cursor: pointer; padding: 6px 16px; border-radius: 4px; font-size: 0.85rem;
          background: #eee; border: none; }}
  .tab.active {{ background: #3498db; color: white; }}
  .tab-content {{ display: none; }}
  .tab-content.active {{ display: block; }}
  .generated {{ font-size: 0.75rem; color: #aaa; text-align: right; }}
</style>
</head>
<body>
<h1>特徴量重要度レポート</h1>
<div class="subtitle">モデル: {os.path.basename(model_path)} | 生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>

{overfit_warn}

<div class="cards">
  <div class="card">
    <div class="label">特徴量数</div>
    <div class="value blue">{len(feature_cols)}</div>
  </div>
  <div class="card">
    <div class="label">訓練データ数</div>
    <div class="value">{data_count:,}</div>
  </div>
  <div class="card">
    <div class="label">レース数</div>
    <div class="value">{race_count:,}</div>
  </div>
  <div class="card">
    <div class="label">訓練AUC</div>
    <div class="value {'red' if auc_train and auc_train > 0.95 else 'green'}">{f'{auc_train:.4f}' if auc_train else 'N/A'}</div>
  </div>
  <div class="card">
    <div class="label">CV AUC (mean)</div>
    <div class="value green">{f'{cv_mean:.4f}' if cv_mean else 'N/A'}</div>
  </div>
  <div class="card">
    <div class="label">CV AUC (std)</div>
    <div class="value">{f'{cv_std:.4f}' if cv_std else 'N/A'}</div>
  </div>
  <div class="card">
    <div class="label">訓練期間</div>
    <div class="value orange" style="font-size:1rem">{train_from}〜{train_to}</div>
  </div>
  <div class="card">
    <div class="label">木の数</div>
    <div class="value">{booster.num_trees()}</div>
  </div>
</div>

<section>
  <h2>カテゴリ別重要度 (Gain)</h2>
  <table>
    <tr><th>カテゴリ</th><th>重要度バー</th><th>割合</th></tr>
    {cat_bar_html}
  </table>
</section>

<section>
  <h2>特徴量別重要度 Top 30</h2>
  <div class="tabs">
    <button class="tab active" onclick="switchTab('gain', this)">Gain (情報量)</button>
    <button class="tab" onclick="switchTab('split', this)">Split (使用頻度)</button>
  </div>
  <div id="tab-gain" class="tab-content active">
    <table>
      <tr><th>#</th><th>特徴量名</th><th>重要度バー</th><th>Gain合計</th><th>割合</th></tr>
      {gain_rows}
    </table>
  </div>
  <div id="tab-split" class="tab-content">
    <table>
      <tr><th>#</th><th>特徴量名</th><th>重要度バー</th><th>Split回数</th><th>割合</th></tr>
      {split_rows}
    </table>
  </div>
</section>

<section>
  <h2>過学習チェック</h2>
  <table>
    <tr><th>指標</th><th>値</th><th>判定</th></tr>
    <tr>
      <td>訓練 AUC</td>
      <td class="num">{f'{auc_train:.4f}' if auc_train else 'N/A'}</td>
      <td>{'⚠ 高すぎる可能性 (>0.95)' if auc_train and auc_train > 0.95 else '✅ 正常'}</td>
    </tr>
    <tr>
      <td>CV AUC (mean ± std)</td>
      <td class="num">{f'{cv_mean:.4f} ± {cv_std:.4f}' if cv_mean and cv_std else 'N/A'}</td>
      <td>{{'✅ 正常範囲 (>0.75)' if cv_mean and cv_mean >= 0.75 else '⚠ 低すぎる可能性 (<0.75)'}}</td>
    </tr>
    <tr>
      <td>訓練AUC - CV AUC</td>
      <td class="num">{f'{auc_gap:.4f}' if auc_gap is not None else 'N/A'}</td>
      <td>{'⚠ 0.15超 → 過学習の疑い' if auc_gap and auc_gap > 0.15 else '✅ 正常 (<0.15)'}</td>
    </tr>
  </table>
</section>

<div class="generated">Generated by .github/skills/feature-importance-report/scripts/analyze_feature_importance.py</div>

<script>
function switchTab(name, btn) {{
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
}}
</script>
</body>
</html>"""

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Report saved: {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="特徴量重要度レポート生成")
    parser.add_argument("--model", default=None, help="モデルファイルパス (.joblib)")
    parser.add_argument("--out", default=None, help="出力HTMLファイルパス")
    args = parser.parse_args()

    models_dir = os.path.join(PYTHON_API_DIR, "models")
    model_path = args.model or find_latest_model(models_dir)
    if not model_path:
        print(f"Error: モデルファイルが見つかりません ({models_dir})")
        sys.exit(1)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = args.out or os.path.join(REPORTS_DIR, f"feature_importance_{ts}.html")

    print(f"Loading: {model_path}")
    bundle = load_bundle(model_path)
    booster = bundle["model"]

    # サマリー表示
    gain_items = compute_importance(booster, "gain")
    print(f"\n=== Top 10 特徴量 (Gain) ===")
    for i, item in enumerate(gain_items[:10], 1):
        print(f"  {i:2d}. {item['feature']:<40s} {item['pct']:6.2f}%")
    metrics = bundle.get("metrics", {})
    print(f"\n=== モデル評価 ===")
    print(f"  訓練AUC  : {metrics.get('auc', 'N/A')}")
    print(f"  CV AUC   : {metrics.get('cv_auc_mean', 'N/A')} ± {metrics.get('cv_auc_std', 'N/A')}")
    auc_t = metrics.get("auc")
    cv_m = metrics.get("cv_auc_mean")
    if auc_t and cv_m:
        gap = auc_t - cv_m
        status = "⚠ 過学習の疑い" if gap > 0.15 else "✅ 正常"
        print(f"  AUC差分  : {gap:.4f}  {status}")

    generate_html_report(bundle, model_path, out_path)


if __name__ == "__main__":
    main()
