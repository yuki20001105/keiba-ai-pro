# 競馬AI Pro - AI競馬予測システム

機械学習による競馬予測・資金管理・データ収集を一気通貫で実現するフルスタックアプリケーション。

---

## 目次

1. [システム全体フロー](#1-システム全体フロー)
2. [Step 1: データ収集（スクレイピング）](#2-step-1-データ収集スクレイピング)
3. [Step 2: DB保存構造](#3-step-2-db保存構造)
4. [Step 3: 特徴量エンジニアリング](#4-step-3-特徴量エンジニアリング)
5. [Step 4: モデル学習](#5-step-4-モデル学習)
6. [Step 5: 予測・レース分析](#6-step-5-予測レース分析)
7. [Step 6: ベッティング戦略](#7-step-6-ベッティング戦略)
8. [フロントエンド画面構成](#8-フロントエンド画面構成)
9. [APIエンドポイント一覧](#9-apiエンドポイント一覧)
10. [起動方法](#10-起動方法)
11. [Gitブランチ戦略](#11-gitブランチ戦略)

---

## 1. システム全体フロー

```
netkeiba.com
  │
  │ scrape_race_full() / _scrape_shutuba_fallback()
  ▼
race.py（スクレイピング）
  │ JSON (race_info + 馬別30項目)
  ▼
storage.py（DB保存）
  │
  ▼
keiba_ultimate.db (SQLite3)
  ├─ races_ultimate        ← レース基本情報 (JSON per row)
  └─ race_results_ultimate ← 馬別結果      (JSON per row)
  │
  │ db_ultimate_loader.load_ultimate_training_frame()
  ▼
pandas DataFrame (~ 13,000行 × 83列)
  │
  │ add_derived_features()
  ▼
派生特徴量追加 (+ ~40列 → 合計 ~125列)
  │
  │ UltimateFeatureCalculator.add_ultimate_features()
  ▼
過去統計特徴量追加 (+ ~24列 → 合計 ~137列)
  │
  │ prepare_for_lightgbm_ultimate()
  ▼
LightGBM用前処理 (Label Encoding, 未来情報除外) → 110列前後
  │
  ├─ 学習モード ─────────────────────────────────────────────┐
  │    LightGBM (binary 分類, target=win)                    │
  │    Optuna 最適化 (100試行, 5-fold CV)                    │
  │    → model_win_YYYYMMDD_YYYYMMDD_ultimate.joblib         │
  └──────────────────────────────────────────────────────────┘
  │
  └─ 推論モード（/api/analyze_race）─────────────────────────┐
       Quality Gate チェック                                  │
       model.predict_proba(X) → 勝率スコア                   │
       キャリブレーション                                      │
       期待値計算 (EV = prob × odds)                          │
       ベッティング戦略生成                                    │
       → JSON レスポンス                                      │
     ──────────────────────────────────────────────────────  │
```

---

## 2. Step 1: データ収集（スクレイピング）

### 対象URL

| 用途 | URL |
|------|-----|
| レース結果 | `https://db.netkeiba.com/race/{race_id}/` |
| 出馬表（フォールバック） | `https://race.netkeiba.com/race/shutuba.html?race_id={race_id}` |
| 当日レース一覧 | `https://race.netkeiba.com/top/race_list.html?kaisai_date={YYYYMMDD}` |

`race_id` の構造: `YYYYMMDDVVRR`
- `YYYY`: 年, `MM`: 月, `DD`: 日
- `VV`: 会場コード (`05`=東京, `06`=中山, `09`=阪神 など)
- `RR`: レース番号 (`01`～`12`)

### スクレイプされるフィールド

**レース基本情報（races_ultimate.data に JSON 格納）**

| フィールド | 型 | 例 |
|-----------|----|----|
| `race_name` | str | `"日本ダービー"` |
| `date` | str (YYYYMMDD) | `"20260601"` |
| `venue` | str | `"東京"` |
| `venue_code` | str | `"05"` |
| `distance` | int | `2400` |
| `track_type` | str | `"芝"` / `"ダート"` |
| `weather` | str | `"晴"` / `"曇"` / `"雨"` |
| `field_condition` | str | `"良"` / `"稍重"` / `"重"` / `"不良"` |
| `num_horses` | int | `18` |
| `post_time` | str | `"15:40"` |
| `kai` | int | 開催回 |
| `day` | int | 開催日 |
| `course_direction` | str | `"右"` / `"左"` / `"直線"` |

**馬別情報（race_results_ultimate.data に JSON 格納）**

| フィールド | 型 | 例 |
|-----------|----|----|
| `horse_number` | int | `7` |
| `bracket_number` | int | `4` |
| `horse_name` | str | `"ドウデュース"` |
| `horse_id` | str | `"2020110038"` |
| `sex_age` | str | `"牡3"` |
| `sex` | str | `"牡"` / `"牝"` / `"セ"` |
| `age` | int | `3` |
| `jockey_name` | str | `"武豊"` |
| `jockey_id` | str | `"00356"` |
| `jockey_weight` | float | `57.0` |
| `trainer_name` | str | `"友道康夫"` |
| `trainer_id` | str | `"01101"` |
| `weight_kg` | int | `492` |
| `weight_diff` | int | `+4` |
| `odds` | float | `4.5` |
| `popularity` | int | `2` |
| `finish_position` | int | `1` |
| `finish_time` | str | `"2:23.5"` |
| `margin` | str | `"クビ"` |
| `last_3f` | str | `"34.8"` |
| `last_3f_rank` | int | `3` |
| `corner_positions` | str | `"7-7-4-3"` |
| `corner_positions_list` | list[int] | `[7, 7, 4, 3]` |
| `prize_money` | float | `30000` |

### エラーハンドリング

- リトライ: 最大3回・指数バックオフ (2^n 秒)
- HTTP 429: 10秒 + 追加待機
- フォールバック: 結果ページ失敗 → 出馬表ページ（odds=None で保存、当日再スクレイプで補完）
- レート制限: リクエスト間隔 2〜3秒のランダム待機

---

## 3. Step 2: DB保存構造

**ファイル**: `keiba/data/keiba_ultimate.db` (SQLite3, WAL モード)

```sql
-- レース基本情報（1レース1行）
CREATE TABLE races_ultimate (
    race_id    TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 馬別データ（1頭1行）
CREATE TABLE race_results_ultimate (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id    TEXT,
    data       TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_results_race_id ON race_results_ultimate (race_id);
```

**現状規模**
- `races_ultimate`: 約 14,000+ レース
- `race_results_ultimate`: 約 180,000+ 行（平均 13 頭/レース）

---

## 4. Step 3: 特徴量エンジニアリング

### 4-1. `add_derived_features()` — 派生特徴量 (~40列追加)

**休養期間**

| 特徴量 | 型 | 説明 |
|-------|----|------|
| `days_since_last_race` | int | 現レース日 - 前走日（日数） |
| `rest_category` | str | `"short"` / `"normal"` / `"long"` / `"very_long"` |

**前走・2走前**

| 特徴量 | 説明 |
|--------|------|
| `prev_race_finish` | 前走着順 |
| `prev_race_time` | 前走タイム（秒） |
| `prev_race_weight` | 前走馬体重 |
| `prev_race_distance` | 前走距離 |
| `prev2_race_finish` | 2走前着順 |
| `prev2_race_distance` | 2走前距離 |

**距離・馬場変化**

| 特徴量 | 説明 |
|--------|------|
| `distance_change` | 今走距離 - 前走距離 (m) |
| `distance_increased` | 1/0（距離延長） |
| `distance_decreased` | 1/0（距離短縮） |
| `surface_changed` | 1/0（芝↔ダート変更） |

**コーナー通過派生**

| 特徴量 | 説明 |
|--------|------|
| `corner_position_avg` | 平均コーナー通過順位 |
| `corner_position_variance` | 通過順位の分散（低=安定した脚質） |
| `last_corner_position` | 最終コーナーでの通過順位 |
| `position_change` | 1C→最終Cの順位変化（正=追い込み） |

**近走統計（expanding window）**

| 特徴量 | 説明 |
|--------|------|
| `past3_avg_finish` | 近3走平均着順 |
| `past5_avg_finish` | 近5走平均着順 |
| `past10_avg_finish` | 近10走平均着順 |
| `past3_win_rate` | 近3走勝率 |
| `past5_win_rate` | 近5走勝率 |
| `finish_consistency` | 1/(1+std)（高=安定） |
| `recent_form_score` | max(0, 10-近3走平均)/10 |

**体重トレンド**

| 特徴量 | 説明 |
|--------|------|
| `past_5_weight_slope` | kg/走の増減傾向（線形回帰の傾き） |
| `past_5_weight_avg_change` | 過去5走の平均体重変化 (kg) |

**会場・条件別統計（ベイズ平滑化適用）**

| 特徴量 | 説明 |
|--------|------|
| `jockey_course_win_rate` | 騎手×会場×馬場での勝率 |
| `horse_surface_win_rate` | 馬×馬場種別での勝率 |
| `horse_dist_band_win_rate` | 馬×距離帯での勝率 |
| `gate_win_rate` | 枠番×会場×距離帯での勝率 |
| `jt_combo_win_rate_smooth` | 騎手×調教師コンビ勝率（平滑化） |

**オッズ系特徴量**

| 特徴量 | 説明 |
|--------|------|
| `log_odds` | log(1 + odds) |
| `implied_prob` | 1 / odds（市場の暗黙確率） |
| `odds_rank_in_race` | レース内オッズ順位 |
| `odds_z_in_race` | レース内でのオッズのZスコア |
| `market_entropy` | オッズ分布のエントロピー（高=混戦） |

### 4-2. `UltimateFeatureCalculator.add_ultimate_features()` — 過去統計 (~24列追加)

**馬の過去10走統計**（DBを参照、未来情報は参照しない）

| 特徴量 | 説明 |
|--------|------|
| `past_10_races_count` | 過去10走以内のレース数（0〜10） |
| `past_10_avg_finish` | 平均着順 |
| `past_10_std_finish` | 着順の標準偏差 |
| `past_10_best_finish` | ベスト着順 |
| `past_10_win_rate` | 勝率 |
| `past_10_place_rate` | 2着以内率 |
| `past_10_show_rate` | 3着以内率 |
| `recent_3_avg_finish` | 近3走平均着順 |
| `finish_consistency` | 1/(1+std) |
| `recent_form_score` | (10 - 近3走平均) / 10 |
| `past_5_weight_slope` | 体重トレンド（kg/走） |
| `past_5_weight_avg_change` | 平均体重変化（kg） |

**騎手統計（直近180日スライディングウィンドウ）**

| 特徴量 | 説明 |
|--------|------|
| `jockey_recent_win_rate` | 直近180日勝率 |
| `jockey_recent_place_rate` | 直近180日 2着以内率 |
| `jockey_recent_show_rate` | 直近180日 3着以内率 |
| `jockey_recent_races` | 直近180日 出走数 |
| `jockey_avg_finish` | 直近180日 平均着順 |

**調教師統計（同上）**

| 特徴量 | 説明 |
|--------|------|
| `trainer_recent_win_rate` | 直近180日勝率 |
| `trainer_recent_place_rate` | 直近180日 2着以内率 |
| `trainer_recent_show_rate` | 直近180日 3着以内率 |
| `trainer_recent_races` | 直近180日 出走数 |

### 4-3. `prepare_for_lightgbm_ultimate()` — LightGBM用前処理

**カテゴリ変数の処理**

低カーディナリティ → Label Encoding → `categorical_feature` 指定:
```
venue_encoded       : 0-9  (10会場)
track_type_encoded  : 0-2  (芝/ダート/障害)
weather_encoded     : 0-3  (晴/曇/雨/小雨)
condition_encoded   : 0-3  (良/稍重/重/不良)
race_class_encoded  : 0-8  (新馬〜G1)
direction_encoded   : 0-2  (右/左/直線)
sex_encoded         : 0-2  (牡/牝/セ)
```

**未来情報ブラックリスト（推論時に強制除外）**
```
finish_position, finish_time, time_seconds
last_3f, last_3f_time, last_3f_rank, last_3f_rank_normalized
corner_1/2/3/4, corner_positions_list
margin, prize_money, actual_finish
```

**最終列数**

| フェーズ | 列数 |
|---------|------|
| DB ロード直後 | 83列 |
| add_derived_features 後 | ~125列 |
| add_ultimate_features 後 | ~137列 |
| LightGBM前処理後 | ~110列（学習時） |
| 推論時（未来情報除外後） | ~87-90列 |

---

## 5. Step 4: モデル学習

### 目的変数

| target | 形式 | 意味 |
|--------|------|------|
| `win` | 0/1 | 1着 = 1（デフォルト） |
| `place3` | 0/1 | 3着以内 = 1 |
| `win_tie` | 0/1 | 複勝（3着以内タイ扱い） = 1 |
| `speed_deviation` | float | タイム偏差（回帰問題）→ softmax で確率変換 |

### LightGBM ハイパーパラメータ（デフォルト）

```python
{
    "objective":        "binary",
    "metric":           "auc",
    "learning_rate":    0.05,
    "num_leaves":       31,
    "min_data_in_leaf": 20,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq":     5,
    "random_state":     42,
}
# num_boost_round: 1000 (early_stopping で実際は 200-400 前後)
# stopping_rounds: 50
```

### Optuna 最適化（オプション）

```
n_trials:  30  (--optuna-trials オプションで変更可)
cv_folds:  5
timeout:   min(n_trials × 60, 1800) 秒
目標:      CV AUC 最大化

探索範囲:
  learning_rate:    [0.01, 0.1]
  num_leaves:       [10, 100]
  max_depth:        [5, 20]
  min_data_in_leaf: [10, 50]
  feature_fraction: [0.6, 1.0]
  bagging_fraction: [0.6, 1.0]
  n_estimators:     [500, 2000]
```

### モデルファイル（joblib Bundle）

**保存先**: `python-api/models/model_win_YYYYMMDD_YYYYMMDD_ultimate.joblib`

```python
bundle = {
    'model':               lgb.Booster,
    'optimizer':           LightGBMFeatureOptimizer,
    'categorical_features': ['venue_encoded', ...],
    'feature_columns':     ['bracket_number', ...],
    'target':              'win',
    'ultimate_mode':       True,
    'metrics': {
        'auc':         0.8234,
        'cv_auc_mean': 0.8145,
        'cv_auc_std':  0.0089,
    },
    'data_count':          15234,
    'training_date_from':  '2026-03-01',
    'training_date_to':    '2026-03-31',
}
```

---

## 6. Step 5: 予測・レース分析

### エンドポイント: `POST /api/analyze_race`

**入力**
```json
{
  "race_id": "202606030304",
  "model_id": null
}
```

**内部処理フロー**

```
1. keiba_ultimate.db から race_id でデータ検索
   └─ 未登録 → _scrape_shutuba_fallback() でオンデマンドスクレイプ → DB保存
2. JSON → DataFrame (14〜18行 × 83列)
3. odds が全 None → 出馬表を再スクレイプして補完
4. add_derived_features(full_history=DB全履歴)
5. UltimateFeatureCalculator.add_ultimate_features()
6. Quality Gate チェック（下記参照）
7. prepare_for_lightgbm_ultimate(is_training=False)
8. 未来情報列を強制除外
9. model.predict_proba(X)[:, 1] → 生スコア配列
10. キャリブレーション (IsotonicRegression)
11. レース内正規化 (sum = 1.0)
12. EV 計算: ev = p_norm × odds
13. ベッティング推奨生成
```

**Quality Gate チェック内容**

| コード | チェック内容 | 重大度 |
|--------|------------|--------|
| Q1 | `distance = 0` のレース | ERROR → 処理停止 |
| Q2 | `odds` が全 None | WARNING → NaN補完で続行 |
| Q3 | 同一レース内で距離の値が揺れている | ERROR → 処理停止 |
| Q4 | 重複馬番 | ERROR → 処理停止 |
| Q5 | `popularity` が全 None | WARNING → NaN補完で続行 |

**スコアの計算式**

```
生スコア (p_raw):
  model.predict_proba(X)[:, 1]

キャリブレーション後 (probability):
  calibrator.predict(p_raw)  ← IsotonicRegression

レース内正規化 (p_norm):
  p_norm[i] = probability[i] / sum(probability)

期待値 (expected_value):
  EV = p_norm × odds
  EV > 1.0 → プラス期待値（買い推奨）
```

**出力（主要部分）**
```json
{
  "success": true,
  "race_info": {
    "race_id": "202606030304",
    "race_name": "○○特別",
    "venue": "阪神",
    "distance": 1600,
    "track_type": "芝",
    "num_horses": 14
  },
  "horses": [
    {
      "horse_number": 3,
      "horse_name": "xxxxxx",
      "probability": 0.2543,
      "p_norm": 0.2511,
      "predicted_rank": 1,
      "odds": 4.5,
      "popularity": 2,
      "expected_value": 1.13
    }
  ],
  "betting_recommendations": {
    "単勝": [{"horse_no": 3, "expected_value": 1.13}],
    "馬連": [{"combination": "3-7", "expected_value": 2.45}],
    "三連複": [{"combination": "3-7-11", "expected_value": 5.2}]
  }
}
```

---

## 7. Step 6: ベッティング戦略

### 馬券種別推奨ロジック

- **単勝**: EV 上位 3頭
- **馬連**: EV 上位 5頭の組み合わせ (C(5,2)=10通り) から EV上位を選択
- **三連複**: EV 上位 5頭の組み合わせ (C(5,3)=10通り) から EV上位を選択

### Kelly 基準（賭け額計算）

```
Kelly % = (p × odds - 1) / (odds - 1)
フラクショナルKelly = Kelly % × 0.25  (破産リスク軽減)
上限 = min(Kelly%, 5%)
推奨賭け額 = 銀行残高 × フラクション後Kelly%

例）確率 p=30%, オッズ=3.0, 銀行残高 100万円
  Kelly = (0.3 × 3 - 1) / (3 - 1) = 20%
  フラクション後 = 20% × 0.25 = 5%（上限に一致）
  推奨賭け額 = 100万円 × 5% = 5万円
```

### リスク管理パラメータ

| リスク設定 | 1レース最大資金割合 |
|-----------|-----------------|
| `conservative` | 2% |
| `balanced`（デフォルト） | 3.5% |
| `aggressive` | 5% |

### レースレベル判定

```
アクション判定（以下いずれかを満たすと "勝負"）:
  難易度スコア >= 0.7                           → "勝負"  (予算の 80% を使用)
  max_EV >= 4.0 かつ max_prob >= 0.25          → "勝負"
  max_EV >= 6.0                               → "勝負"
  それ以外                                    → "通常"  (予算の 40% を使用)
```

### 動的単価

```
"勝負" かつ 予算 >= 5,000円 → 1,000円単位
"勝負" かつ 予算 >= 3,000円 → 500円単位
"通常" かつ 予算 >= 3,000円 → 200円単位
デフォルト                   → 100円単位
```

---

## 8. フロントエンド画面構成

### 予測バッチ画面 (`/predict-batch`)

```
1. 日付入力 → GET /api/races/by_date?date=YYYYMMDD
2. データ未取得の場合 → POST /api/scrape でジョブ開始
   → GET /api/scrape/status/{job_id} を3秒間隔でポーリング
3. レース一覧表示（会場フィルタ付き）
4. モデル選択 → GET /api/models?ultimate=true
5. 複数レース選択 → [一括予測開始]
   → POST /api/analyze_races_batch (レースIDリスト)
6. 結果表示: 馬別確率・EV・オッズ・推奨馬券
7. 購入 → POST /api/purchase
```

### レース分析画面 (`/race-analysis`)

```
1. 日付選択 → GET /api/races/by-date?date=YYYYMMDD
2. レース選択 → POST /api/analyze_race（ML予測）
   └─ 予測失敗時フォールバック → GET /api/races/{race_id}/horses（出走表のみ表示）
3. 予測結果タブ: 馬別確率・EV・推奨馬券
4. 特徴量分析タブ → GET /api/debug/race/{race_id}/features
5. キャッシュ済み結果は再計算ボタンで強制リフレッシュ可能
```

### データ収集画面 (`/data-collection`)

```
1. ローカルFastAPI稼働チェック → GET /api/scrape/health
2. 期間指定入力 (開始年月〜終了年月)
3. [データ取得開始] → POST /api/scrape (ジョブ開始)
   → GET /api/scrape/status/{job_id} ポーリング (3秒間隔)
4. 取得済みデータ確認 → GET /api/races/recent?limit=50 (1回だけ)
5. 詳細表示 → GET /api/races/{race_id}/horses (ML推論なし・軽量)
```

### 本番前チェック画面 (`/production-readiness`)

```
1. Premium/Admin 権限で [本番前チェックを実行]
2. POST /api/production-readiness
3. read-only checks を順次実行
  - Frontend build
  - FastAPI health / scrape health
  - analyze_race smoke / smoke suite summary
  - secret scan (Notion token prefix)
  - git status 注意
  - write flag / APP_ENV safety
4. pass / warn / fail / unknown をカード表示
  - 認証トークン未設定時の 401/403 は auth-required (warn) として表示
  - KEIBA_AUTH_BEARER_TOKEN 設定時は認証必須 smoke を通常の pass/fail で評価
5. write API は呼ばない（sandbox write-readback は別管理）
```

### 学習画面 (`/train`)

```
1. 条件設定 (target: win / place3 / win_tie / speed_deviation / 期間 / Optuna試行数 / CV分割数)
2. [学習開始] → POST /api/train/start
3. GET /api/train/status/{job_id} ポーリング
4. 完了 → AUC / LogLoss / CV統計 表示
5. 特徴量ラボへのリンク → /feature-lab
```

### 特徴量ラボ画面 (`/feature-lab`)

```
1. ターゲット選択 (win / place3 / speed_deviation)
2. サマリータブ → GET /api/features/summary
   - 未来フィールド数・スクレイプ列数・FE特徴量数・除外列数・ステージ別内訳
3. 重要度タブ → GET /api/features/importance?target=***&top_n=**&importance_type=gain|split
   - 上位N件の特徴量重要度バー表示・ツールチップで説明
4. カバレッジタブ → GET /api/features/coverage?target=***
   - カタログ有効特徴量のモデル内一致率確認
   - 不足特徴量・余剰特徴量をバッジ表示
```

### データ閲覧画面 (`/data-view`)

```
1. 日付範囲・会場フィルタでレース検索
2. レース一覧表示 → クリックで出走馬詳細
3. GET /api/races/{race_id}/horses（ML推論なし）
```

### ダッシュボード画面 (`/dashboard`)

```
1. 購入履歴の集計・回収率サマリー表示
2. GET /api/purchase_history, GET /api/statistics
```

### 管理画面 (`/admin`)

```
1. モデル管理・スクレイプ実行（管理者専用）
2. GET /api/models, POST /api/scrape/start
```

### 認証・権限

| ロール | 権限 |
|-------|------|
| `user` | 一般ユーザー（予測・閲覧） |
| `admin` | 管理者（スクレイプ開始・モデル管理含む全機能）|

---

## 9. APIエンドポイント一覧

| Method | Path | 説明 |
|--------|------|------|
| POST | `/api/scrape` | スクレイプ同期実行（結果を直接返却、土日月のみ対応） |
| POST | `/api/scrape/start` | Admin用スクレイプ開始（非同期ジョブ） |
| GET | `/api/scrape/status/{job_id}` | スクレイプ進捗取得 |
| GET | `/api/races/by_date?date=YYYYMMDD` | 指定日のレース一覧 |
| GET | `/api/races/recent?limit=50` | 最近取得したレース一覧（軽量） |
| GET | `/api/races/{race_id}/horses` | 出走馬一覧（ML推論なし） |
| POST | `/api/analyze_race` | 単一レース分析・予測 |
| POST | `/api/analyze_races_batch` | 複数レース一括分析 |
| POST | `/api/train/start` | モデル学習開始（非同期ジョブ） |
| GET | `/api/train/status/{job_id}` | 学習進捗取得 |
| GET | `/api/models` | 保存済みモデル一覧 |
| GET | `/api/data_stats?ultimate=true` | DB統計（レース数・馬数・最終取得日） |
| POST | `/api/purchase` | 購入推奨の保存 |
| GET | `/api/purchase_history` | 購入履歴取得 |
| GET | `/api/statistics` | 購入統計・回収率サマリー |
| GET | `/api/debug/race/{race_id}` | 生データ確認（Premium） |
| GET | `/api/debug/race/{race_id}/features` | 特徴量確認（Premium） |
| GET | `/api/features/catalog` | 特徴量カタログ全件取得 |
| GET | `/api/features/summary` | 特徴量カタログ統計（カウント・ステージ別） |
| GET | `/api/features/importance` | モデル特徴量重要度（gain/split） |
| GET | `/api/features/coverage` | カタログ特徴量のモデル内カバレッジ検証 |
| GET | `/api/realtime-odds/{race_id}` | リアルタイムオッズ取得（キャッシュ） |
| POST | `/api/realtime-odds/refresh` | オッズ強制再取得（Playwright） |
| POST | `/api/export/bet-list` | 馬券購入リスト出力（JSON） |
| POST | `/api/export/bet-list/csv` | 馬券購入リスト出力（CSV） |
| POST | `/api/profiling/start` | ydata-profiling レポート生成ジョブ開始 |
| GET | `/api/profiling/status/{job_id}` | プロファイリングジョブ進捗 |
| GET | `/api/profiling/html/{job_id}` | プロファイリング HTML レポート取得 |
| POST | `/api/backfill/nar-pedigree` | NAR血統情報バックフィル |
| POST | `/api/backfill/coat-color` | 毛色情報バックフィル |
| GET | `/health` | サーバー死活確認 |

---

## 10. 起動方法

### 必要環境

- Python 3.11+
- Node.js 18+
- `python-api/.venv` (FastAPI 用)

### ローカル起動

**FastAPI サーバー（ポート 8000）**
```bash
cd python-api
python main.py
# または VS Code タスク「Start FastAPI」
```

**Next.js フロントエンド（ポート 3000）**
```bash
npm run dev
# または VS Code タスク「Start Next.js」
```

### 環境変数（`.env.local`）

```env
# Supabase（オプション: 未設定でもローカル動作可）
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=

# FastAPI エンドポイント
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### 初回セットアップの流れ

```
1. FastAPI 起動 (python-api/main.py)
2. /data-collection ページでデータ取得（期間指定）
3. /train ページでモデル学習
4. /predict-batch ページで当日レース予測
```

### keibaAI 実行ガイド（Notebook E2E / API Smoke / Notion）

日次の実行確認は、以下の順番を推奨します。

```
Notebook E2E audit
  ↓
Analyze Race API smoke
  ↓
Race-list proxy smoke
  ↓
Race preflight smoke
  ↓
Race dry-run smoke
  ↓
Payload contract diff
  ↓
Write guard smoke
  ↓
Notion output
```

### 検証・本番 runbook

**検証時に実行するコマンド**

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
npm run lint
npm run build
python -m compileall -q python-api scripts
python scripts/run_keiba_smoke_suite.py
python scripts/smoke_analyze_race_api.py
python scripts/smoke_notion_report_api.py
python scripts/run_keiba_notebook_e2e.py --mode audit
git grep -n -I "<notion-token-prefix>"
git status --short
```

認証必須 endpoint を厳密評価する場合:

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
$env:KEIBA_AUTH_BEARER_TOKEN="<bearer-token>"
python scripts/run_keiba_smoke_suite.py
python scripts/smoke_analyze_race_api.py
```

注意:
- `KEIBA_AUTH_BEARER_TOKEN` の値はログや JSON summary に出力しない
- トークン未設定時の 401/403 は auth-required として warn 分類

**UI での本番前チェック手順**

```
1. /production-readiness を開く
2. [本番前チェックを実行] を押す
3. fail/warn があるカードの summary を確認
4. write flag が false であることを確認
5. sandbox write-readback は別管理であることを確認
```

**本番運用ルール**

- `NETKEIBA_RACE_WRITE_ENABLED=false`
- `ALLOW_STAGING_WRITE=false`
- `APP_ENV=production`
- production/base table write は禁止
- sandbox write は本番で実行しない
- default smoke / default suite では write を実行しない
- UI / 予測 / analyze_race / health check / read-only API のみ本番利用可

**P1-16 sandbox runtime 確認コマンド**

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python scripts/smoke_netkeiba_race_write_guard.py --expect-sandbox-precheck
python scripts/smoke_netkeiba_race_write_guard.py --expect-sandbox-write-readback
```

注意:
- P1-16 sandbox write-readback actual pass は upstream ready 後の別管理
- production/base table write はこの手順に含めない
- DB / reports JSON / metadata は Git 管理対象に含めない

**1) Notebook E2E audit**

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe scripts\run_keiba_notebook_e2e.py --mode audit
```

結果確認:

```powershell
Get-Content reports\keiba_notebook_e2e_result.json -Raw
```

**2) Analyze Race API smoke**

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe scripts\smoke_analyze_race_api.py
```

**3) Race-list proxy smoke**

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe scripts\smoke_netkeiba_race_list_proxy.py
```

**4) Race preflight smoke (contract-only mode)**

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe scripts\smoke_netkeiba_race_preflight.py
```

判定ルール（preflight）:
- ready: PASS
- degraded: WARN
- unavailable: WARN (環境によってはSKIP相当)
- contract error: FAIL

strict モード（non-ready も fail）:

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe scripts\smoke_netkeiba_race_preflight.py --fail-on-nonready
```

**5) Race dry-run smoke (contract-only mode)**

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe scripts\smoke_netkeiba_race_dry_run.py
```

判定ルール（dry-run）:
- ready: PASS
- degraded: WARN
- unavailable: WARN (環境によってはSKIP相当)
- invalid: WARN
- contract error: FAIL

strict モード（non-ready も fail）:

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe scripts\smoke_netkeiba_race_dry_run.py --fail-on-nonready
```

**6) Payload contract diff**

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe scripts\compare_netkeiba_race_payload_contract.py
```

判定ルール（payload diff）:
- pass: contracts-compatible
- warn: contract-diff-detected / dry-run non-ready
- fail: contract-error only

**7) Write guard smoke**

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe scripts\smoke_netkeiba_race_write_guard.py
```

判定ルール（write guard）:
- pass: default disabled (write_performed=false)
- warn: blocked / guarded-noop / invalid
- fail: contract-error

P1-12 writer-stub 契約（enabled 時）:
- guarded-stub でも write_performed は常に false
- table whitelist: races / race_results / race_payouts
- row limit: races<=1, race_results<=30, race_payouts<=100
- idempotency key と payload hash をレスポンスで確認
- audit payload preview をレスポンスで確認（永続保存はしない）

P1-13 sandbox write（明示実行時のみ）:
- `sandbox_write=true` + `target_mode=sandbox` 指定時のみ sandbox write 候補
- 対象は sandbox table 限定（本体テーブルへの write 禁止）
- production では常に blocked
- sandbox table 未存在時は stopped（warn）

P1-14 sandbox precheck（read-only）:
- `GET /api/netkeiba/race/sandbox/precheck` で sandbox readiness を確認
- 対象テーブル:
  - `sandbox_netkeiba_races`
  - `sandbox_netkeiba_race_results`
  - `sandbox_netkeiba_race_payouts`
- 確認内容:
  - table existence
  - required column existence (`race_id`, `data|payload`)
  - type compatibility
  - row-limit support metadata
  - base table references が無いこと
- precheck は常に `write_performed=false`
- sandbox table 未存在は stopped/warn（hard fail ではない）

P1-14.5 sandbox DDL / migration plan（手動適用のみ）:
- DDLファイル: `docs/migrations/netkeiba_sandbox_tables.sql`
- 対象は sandbox table のみ（本番テーブルは変更しない）
- 自動適用処理は追加しない

手動適用（SQLite, 例）:

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe -c "import sqlite3,pathlib; db=pathlib.Path('keiba/data/keiba_ultimate.db'); sql=pathlib.Path('docs/migrations/netkeiba_sandbox_tables.sql').read_text(encoding='utf-8'); con=sqlite3.connect(str(db)); con.executescript(sql); con.commit(); con.close(); print('applied:', db)"
```

rollback / drop（手動）:

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe -c "import sqlite3,pathlib; db=pathlib.Path('keiba/data/keiba_ultimate.db'); con=sqlite3.connect(str(db)); con.executescript('BEGIN; DROP TABLE IF EXISTS sandbox_netkeiba_race_payouts; DROP TABLE IF EXISTS sandbox_netkeiba_race_results; DROP TABLE IF EXISTS sandbox_netkeiba_races; COMMIT;'); con.close(); print('dropped sandbox tables from:', db)"
```

precheck ready 条件（P1-15 着手ゲート）:
- 3 sandbox table がすべて存在
- 必須カラムが揃っている
- 型互換チェックが通る
- base table reference 検知がない
- `status=ready` になるまで write/readback には進まない

P1-15 実施結果（手動DDL適用後の ready 確認）:
- `docs/migrations/netkeiba_sandbox_tables.sql` を手動適用
- FastAPI 再起動後に precheck smoke を実行
- `python scripts/smoke_netkeiba_race_write_guard.py --expect-sandbox-precheck`
  - 結果: `verdict=pass` / `verdict_reason=sandbox-precheck-ready`
- このフェーズでは sandbox write/readback は実施していない
- default suite は引き続き write 非実行

P1-16 実施内容（sandbox write 後 readback 検証）:
- sandbox write 成功直後に sandbox table 限定で readback 検証を実施
- readback 対象:
  - `sandbox_netkeiba_races`
  - `sandbox_netkeiba_race_results`
  - `sandbox_netkeiba_race_payouts`
- readback 検証キー: `race_id + idempotency_key`
- 検証項目:
  - `records_written` と readback count 一致
  - target table が sandbox 限定
  - `idempotency_key` 一致
  - `payload_hash` 一致
  - `audit_payload` 存在
- 不一致時は `status=sandbox-readback-mismatch`

禁止事項（継続）:
- production write は禁止
- base table (`races`, `race_results`, `race_payouts`) への write/readback は禁止
- default smoke/default suite で write 実行しない
- sandbox write/readback は明示オプション時のみ

feature flag ON の限定検証（永続化しない）:

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro\python-api
$env:NETKEIBA_RACE_WRITE_ENABLED = "true"
$env:ALLOW_STAGING_WRITE = "true"
$env:APP_ENV = "staging"
..\.venv\Scripts\python.exe main.py
```

別ターミナルで enabled-mode smoke:

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe scripts\smoke_netkeiba_race_write_guard.py --expect-enabled
```

enabled-mode では次を追加確認:
- guarded-stub 契約が成立
- row-limit 超過ケースが blocked
- audit/idempotency の必須フィールドが存在

enabled-mode 付き suite（任意）:

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe scripts\run_keiba_smoke_suite.py --verify-write-guard-enabled
```

flag-only blocked 確認（任意）:

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro\python-api
$env:NETKEIBA_RACE_WRITE_ENABLED = "true"
$env:ALLOW_STAGING_WRITE = "false"
$env:APP_ENV = "development"
..\.venv\Scripts\python.exe main.py
```

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe scripts\smoke_netkeiba_race_write_guard.py --expect-flag-only
```

production 強制ブロック確認（任意）:

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro\python-api
$env:NETKEIBA_RACE_WRITE_ENABLED = "true"
$env:ALLOW_STAGING_WRITE = "true"
$env:APP_ENV = "production"
..\.venv\Scripts\python.exe main.py
```

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe scripts\smoke_netkeiba_race_write_guard.py --expect-production-block
```

staging lock 不足ブロック確認（任意）:

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro\python-api
$env:NETKEIBA_RACE_WRITE_ENABLED = "true"
$env:ALLOW_STAGING_WRITE = "false"
$env:APP_ENV = "staging"
..\.venv\Scripts\python.exe main.py
```

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe scripts\smoke_netkeiba_race_write_guard.py --expect-staging-lock-missing
```

suite optional:

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe scripts\run_keiba_smoke_suite.py --verify-write-guard-flag-only --verify-write-guard-production-block --verify-write-guard-staging-lock-missing
```

sandbox write smoke（明示実行時のみ）:

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro\python-api
$env:NETKEIBA_RACE_WRITE_ENABLED = "true"
$env:ALLOW_STAGING_WRITE = "true"
$env:APP_ENV = "staging"
..\.venv\Scripts\python.exe main.py
```

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe scripts\smoke_netkeiba_race_write_guard.py --expect-sandbox-write
```

注意:
- この smoke は default 実行には含めない
- sandbox table が未作成の場合は stopped/warn が正常

sandbox write + readback smoke（明示実行時のみ）:

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe scripts\smoke_netkeiba_race_write_guard.py --expect-sandbox-write-readback
```

注意:
- この smoke は default 実行には含めない
- precheck 未ready時は stopped/warn が正常
- readback 不一致時は `sandbox-readback-mismatch` を返す

sandbox precheck smoke（read-only, 明示実行時のみ）:

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe scripts\smoke_netkeiba_race_write_guard.py --expect-sandbox-precheck
```

suite optional（precheck + sandbox write を明示実行）:

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe scripts\run_keiba_smoke_suite.py --verify-write-guard-sandbox-precheck --verify-write-guard-sandbox-write
```

suite optional（sandbox write + readback を明示実行）:

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe scripts\run_keiba_smoke_suite.py --verify-write-guard-sandbox-write-readback
```

**8) Notion output**

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe scripts\upload_to_notion.py
```

**8.5) Notion output (Premium/Admin UI)**

```
1. /notion-report を開く
2. レポート種別を選択
3. [preview 生成] で内容確認
4. [Notion へ送信] で送信
```

UI の安全条件:
- `NOTION_TOKEN` / `NOTION_PARENT_PAGE_ID` は server-side env のみで利用
- token 実値は画面・レスポンス・ログに出力しない
- 非 Premium/Admin は API 直叩きでも `403`
- 任意ファイルパス指定は不可（reportType allowlist のみ）

**統合実行（Smoke Suite）**

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe scripts\run_keiba_smoke_suite.py
```

**Notion output UI/API smoke**

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe scripts\smoke_notion_report_api.py
```

補足:
- `KEIBA_AUTH_BEARER_TOKEN` 未設定時は `auth-required` として warn
- `KEIBA_AUTH_BEARER_TOKEN_NONPREMIUM` を設定すると non-Premium/Admin の 403 検証を実施
- send は `sent` または `config-missing/warn` を許容

strict preflight で統合実行する場合:

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe scripts\run_keiba_smoke_suite.py --strict-preflight
```

**UI連携 最終確認**

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
npm run lint
npm run build
python scripts/run_keiba_smoke_suite.py
git grep -n -I "secret-prefix-check"
git status --short
```

確認方針:
- UI画面 / Next API / FastAPI の連携は完成扱い
- P1-16 sandbox write-readback runtime actual pass は別管理
- default UI/API 動線で危険な write は走らない
- DB / reports JSON / metadata は Git 管理対象に含めない

### 生成物の保存先

- Notebook実行済みファイル: `reports/e2e_notebooks/`
- Notebook E2E結果JSON: `reports/keiba_notebook_e2e_result.json`
- Analyze Race smoke結果JSON: `reports/analyze_race_smoke_result.json`
- Race-list proxy smoke結果JSON: `reports/netkeiba_race_list_proxy_smoke_result.json`
- Race preflight smoke結果JSON: `reports/netkeiba_race_preflight_smoke_result.json`
- Race dry-run smoke結果JSON: `reports/netkeiba_race_dry_run_smoke_result.json`
- Payload contract diff結果JSON: `reports/netkeiba_race_payload_contract_diff.json`
- Write guard smoke結果JSON: `reports/netkeiba_race_write_guard_smoke_result.json`
- Write guard enabled検証結果JSON: `reports/netkeiba_race_write_guard_enabled_smoke_result.json`
- Write guard flag-only検証結果JSON: `reports/netkeiba_race_write_guard_flag_only_smoke_result.json`
- Write guard production検証結果JSON: `reports/netkeiba_race_write_guard_production_smoke_result.json`
- Write guard staging lock検証結果JSON: `reports/netkeiba_race_write_guard_staging_lock_smoke_result.json`
- Write guard sandbox precheck検証結果JSON: `reports/netkeiba_race_write_guard_sandbox_precheck_smoke_result.json`
- Write guard sandbox write検証結果JSON: `reports/netkeiba_race_write_guard_sandbox_write_smoke_result.json`
- Write guard sandbox write-readback検証結果JSON: `reports/netkeiba_race_write_guard_sandbox_write_readback_smoke_result.json`
- Smoke suite結果JSON: `reports/keiba_smoke_suite_result.json`
- Notion output UI/API smoke結果JSON: `reports/notion_report_smoke_result.json`
- 監査ログ（任意）: `reports/e2e_logs/`

### モデル再設計ワークベンチ仕様

- 仕様書: `docs/specs/model-redesign-workbench.md`
- 範囲: 画面、API、ジョブ管理、提案→承認→再学習フロー、本番反映ガード
- 現状: 仕様確定済み（実装は別フェーズ）

---

## 11. ディレクトリ構成

```
keiba-ai-pro/
├── src/                        # Next.js フロントエンド (App Router)
│   ├── app/                    # ページ・APIルート
│   ├── components/             # UIコンポーネント
│   ├── contexts/               # React コンテキスト
│   ├── hooks/                  # カスタムフック
│   └── lib/                    # ユーティリティ・API設定
├── python-api/                 # FastAPI バックエンド
│   ├── main.py                 # エントリポイント
│   ├── routers/                # APIルーター群
│   ├── scraping/               # スクレイピング処理
│   ├── models/                 # 学習済みモデル (.joblib)
│   │   └── archive/            # 旧バージョンモデル
│   ├── middleware/             # 認証ミドルウェア
│   └── tests/                  # pytest テスト
├── keiba/                      # ML パイプライン (Python)
│   └── keiba_ai/               # コアMLモジュール
│       ├── db_ultimate_loader.py
│       ├── feature_engineering.py
│       ├── ultimate_features.py
│       ├── lightgbm_feature_optimizer.py
│       └── constants.py
├── tools/                      # 開発・メンテナンスツール
├── validation/                 # データ品質・特徴量検証
├── scripts/                    # 起動・運用スクリプト
├── supabase/                   # DBスキーマ定義
├── docs/                       # ドキュメント
├── patch_missing_data.py       # DBデータ補完ツール（ルート固定）
├── generate_feature_report.py  # 特徴量重要度レポート生成
└── generate_profiling_report.py # データプロファイリングレポート生成
```

---

## 12. 起動・運用スクリプト (scripts/)

### サーバー起動・停止

```powershell
# PowerShell: 全サーバー起動
.\scripts\start-all.ps1

# PowerShell: 全サーバー停止
.\scripts\stop-all.ps1

# 開発モード（Next.jsのみ）
.\scripts\start-dev.ps1
```

バッチファイル版 (`start-all.bat`, `stop-all.bat`, `start-dev.bat`) も同等の動作をします。

### その他ユーティリティ

| スクリプト | 用途 |
|-----------|------|
| `scripts/check_server.ps1` | サーバー起動状態の確認 |
| `scripts/create-desktop-shortcut.ps1` | デスクトップショートカット作成 |
| `scripts/start_playwright_server.ps1` | Playwright E2E テストサーバー起動 |
| `scripts/stop_playwright_server.ps1` | Playwright サーバー停止 |

---

## 13. データ補完・メンテナンス (tools/)

本番 DB (`keiba/data/keiba_ultimate.db`) のデータ補完・診断のためのスクリプト群です。

### DBデータ補完 (`patch_missing_data.py`)

> **注意**: 実行中は移動不可のため、ルートディレクトリに配置しています。

```powershell
# 確認のみ（DBは更新しない）
.venv\Scripts\python.exe patch_missing_data.py --dry-run

# Phase 指定実行
.venv\Scripts\python.exe patch_missing_data.py --phase 3

# 全フェーズ実行（バックグラウンド）
Start-Process .venv\Scripts\python.exe -ArgumentList "patch_missing_data.py" `
  -RedirectStandardOutput tools\logs\patch_log.txt `
  -RedirectStandardError tools\logs\patch_log_err.txt
```

| Phase | 対象 | 内容 |
|-------|------|------|
| 0 | `races_ultimate` | レース名・天気・開催情報補完 |
| 1 | `race_results_ultimate` | 馬名・馬番・騎手など基本情報補完 |
| 2 | `race_results_ultimate` | 血統情報（sire/dam/damsire）補完 |
| 3 | `race_results_ultimate` | 前走情報補完（日付フィルタ付き） |

### その他ツール (tools/)

| ファイル | 用途 |
|---------|------|
| `tools/check_dbs.py` | DB一覧とレコード数確認 |
| `tools/inspect_db.py` | DBフィールド別充填率詳細 |
| `tools/check_missing.py` | 欠損フィールドチェック |
| `tools/run_local_pipeline.py` | ローカルパイプライン実行テスト |
| `tools/repair_db.py` | DB修復ユーティリティ |
| `tools/patch_horse_names.py` | 馬名データ修正 |
| `tools/rescrape_horse_stats.py` | 馬統計の再スクレイプ |
| `tools/retrain_local.py` | ローカル再学習 |
| `tools/verify_pipeline_full.py` | フルパイプライン検証 |
| `tools/leakage_audit.py` | データリーク監査 |

---

## 14. データ品質・特徴量検証 (validation/)

開発・デバッグ・品質確認のためのスクリプト群です（本番運用向けではありません）。

```powershell
# 充填率確認（パッチ完了後に必ず実行）
.venv\Scripts\python.exe validation\check_null_rates3.py

# データリーク診断
.venv\Scripts\python.exe validation\check_date_leakage.py

# 特徴量詳細確認
.venv\Scripts\python.exe validation\check_features_detail.py

# 最終特徴量一覧
.venv\Scripts\python.exe validation\check_final_features.py
```

| ファイル | 用途 | 実行タイミング |
|---------|------|--------------|
| `check_null_rates3.py` | DB全JSONキー充填率確認 | パッチ実行後・再学習前 |
| `check_date_leakage.py` | 日付ズレ・データリーク診断 | 特徴量修正後 |
| `check_features_detail.py` | 生特徴量 vs モデル入力の詳細比較 | FE変更後 |
| `check_final_features.py` | 最終モデル入力特徴量の一覧 | モデル再学習前 |

> これらのスクリプトはDBを**読み取り専用**で参照します（更新なし）。

---

## 15. ドキュメント一覧 (docs/)

詳細なドキュメントは [docs/README.md](docs/README.md) を参照してください。

| ディレクトリ | 内容 |
|------------|------|
| [docs/setup/](docs/setup/) | セットアップ・初期設定ガイド |
| [docs/deployment/](docs/deployment/) | デプロイガイド (Vercel + Railway) |
| [docs/development/](docs/development/) | 開発者向けドキュメント |
| [docs/features/](docs/features/) | 機能・API仕様 |

---

## 16. Gitブランチ戦略

```
develop  ← 日常開発ブランチ（デフォルト作業場所）
   │
   │ git merge develop --ff-only
   ▼
main     ← 安定版（PR・マージ後の状態）
   │
   │ git merge main --ff-only
   ▼
release  ← 本番ブランチ（デプロイ済み）
   │
   │ git tag vN && git push origin vN
   ▼
GitHub Release（release.yml が自動作成）
```

### ブランチの役割

| ブランチ | 役割 |
|---------|------|
| `develop` | 日常的な開発・実験 |
| `main` | レビュー済み・テスト通過の安定コード |
| `release` | デプロイ済み本番コード（タグ `v1`, `v2`, … が打たれる） |

### よく使うコマンド

```powershell
# develop で作業してコミット
git checkout develop
git add .
git commit -m "feat: ○○機能を追加"
git push origin develop

# main にマージ
git checkout main
git merge develop --ff-only
git push origin main

# release にマージしてタグ
git checkout release
git merge main --ff-only
git push origin release
git tag v4
git push origin v4

# feature ブランチを作成
git checkout develop
git checkout -b feature/my-feature
```

### コミットメッセージ規約

| プレフィックス | 用途 |
|-------------|------|
| `feat:` | 新機能 |
| `fix:` | バグ修正 |
| `refactor:` | リファクタリング |
| `chore:` | 設定・依存関係変更 |
| `docs:` | ドキュメント更新 |
| `ci:` | GitHub Actions 変更 |
| `test:` | テスト追加・修正 |

詳細は `.github/skills/git-workflow/SKILL.md` を参照。