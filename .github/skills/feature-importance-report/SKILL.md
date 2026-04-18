---
name: feature-importance-report
description: 'モデル特徴量重要度レポート生成スキル。Use when: どの特徴量が予測に寄与しているか分析したい / feature importance を可視化したい / モデルの評価レポートを作りたい / 過学習チェックをしたい / 特徴量を削減・整理したい / モデルの改善ヒントを得たい。Keywords: 特徴量重要度, feature importance, モデル評価, 寄与度, AUC, 過学習, レポート, gain, split'
argument-hint: 'モデルファイルパス (省略可: 最新モデルを自動選択)'
---

# 特徴量重要度レポート生成スキル

LightGBM 学習済みモデルの特徴量重要度を分析し、HTML レポートを生成する。

## 実行方法

```bash
# 最新モデルで自動実行（推奨）
python .github/skills/feature-importance-report/scripts/analyze_feature_importance.py

# モデルを指定して実行
python .github/skills/feature-importance-report/scripts/analyze_feature_importance.py \
  --model python-api/models/model_win_lightgbm_XXXX.joblib \
  --out docs/reports/feature_importance.html
```

出力先: `docs/reports/feature_importance_<timestamp>.html`

---

## 生成レポートの見方

### カード指標

| 指標 | 正常範囲 | 要注意 |
|-----|---------|-------|
| 訓練AUC | 0.70〜0.95 | >0.95 は過学習の疑い |
| CV AUC (mean) | 0.65〜0.85 | <0.60 は予測力不足 |
| 訓練AUC − CV AUC | <0.15 | ≥0.15 は過学習の疑い |

### Gain（情報量）
各ノードでの分割による不純度の減少量の合計。**実際の予測精度への貢献度**を表す。
→ 予測に最も重要な特徴量を知りたい場合はこちらを使う。

### Split（使用頻度）
モデル全体で特徴量が何回分割に使われたかのカウント。
→ モデルが「よく参照する」特徴量を知りたい場合はこちら。

**Gain >> Split の場合**: 少ない分割回数で大きな情報量 → 強く効く特徴量  
**Split >> Gain の場合**: 頻繁に使われるが情報量は少ない → ノイズになっている可能性

---

## カテゴリ別分類

| カテゴリ | 含まれる特徴量の例 | 期待される重要度 |
|---------|-----------------|---------------|
| オッズ・市場 | odds, popularity, implied_prob | 最高（市場は集合知） |
| 前走・過去成績 | prev_race_finish, past_10_avg_finish | 高 |
| 騎手・調教師 | jockey_win_rate, trainer_win_rate | 中〜高 |
| 馬の能力 | horse_distance_win_rate, horse_total_wins | 中〜高 |
| レース条件 | distance, surface, venue | 中 |
| 馬体・基本 | age, horse_weight, bracket_number | 低〜中 |
| 欠損フラグ | *_is_missing | 低（補助的） |

---

## 過学習チェック手順

1. **訓練AUC > 0.95 かつ CV AUC < 0.85**の場合 → データリーク監査を実施
   - [model-leakage-check スキル](./../model-leakage-check/SKILL.md)を使用
2. **CV AUC std > 0.02** の場合 → 学習データが少ない/偏りがある
3. **Gain上位にoddが独占**している場合 → オッズ抜きモデルでも検証する

---

## 現在のモデル評価結果（最終確認: 2026-04）

```
訓練AUC  : 1.0000   ⚠ 訓練セット完全適合（過学習の外観）
CV AUC   : 0.9004 ± 0.0066  ← こちらが実態
AUC差分  : 0.0996  ✅ 0.15未満（許容範囲）
特徴量数 : 110
木の数   : 200
```

**注意**: LightGBM の訓練AUC=1.0 は `early_stopping` なしで最終ツリーまで学習した場合の
in-sample 評価のため、過学習指標としては不適切。CV AUC の 0.90 が実際の汎化性能。

---

## 改善ヒント（レポート読み方）

- **odds が Gain シェア 40%超**: 市場依存が強い。オッズ特徴量グループ（implied_prob, log_odds等）
  の相関を整理してみる
- **prev_race_finish > horse_distance_win_rate**: 個別実績重視。トラック適性データを増やすと改善の余地あり
- **欠損フラグが Top30 に入る**: 元特徴量そのものが欠損しているサンプルが多い。スクレイプ補完を検討
