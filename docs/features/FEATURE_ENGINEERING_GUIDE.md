# 特徴量エンジニアリング完全ガイド

競馬AI Proで使用する特徴量の収集、生成、最適化の完全ドキュメント

---

## 📊 目次

1. [特徴量の概要](#特徴量の概要)
2. [Ultimate版特徴量（90+列）](#ultimate版特徴量90列)
3. [特徴量の収集方法](#特徴量の収集方法)
4. [特徴量の生成・計算](#特徴量の生成計算)
5. [LightGBM最適化](#lightgbm最適化)

---

## 特徴量の概要

競馬AI Proは**Ultimate版特徴量（90+列）**を使用します。

### データフロー

```
netkeiba.com
    ↓ スクレイピング
レース基本情報
    ↓ 保存
SQLiteデータベース
    ↓ 統計計算
Ultimate特徴量（90+列）
    ↓ 最適化
LightGBM用特徴量（80列）
```

---

## Ultimate版特徴量（90+列）

### 特徴量カテゴリ一覧

### 1. ID情報（3列）

| 特徴量 | 説明 | データ型 | 用途 |
|--------|------|---------|------|
| horse_id | 馬ID | string | データベースリレーション |
| jockey_id | 騎手ID | string | 統計計算のキー |
| trainer_id | 調教師ID | string | 統計計算のキー |

**生成方法**: netkeiba.comのURLから抽出

---

### 2. 過去10走統計（13列）

データベースから過去10走のデータを集計して生成

| 特徴量 | 説明 | 計算式 |
|--------|------|--------|
| past10_avg_finish | 平均着順 | mean(finish_position) |
| past10_std_finish | 着順標準偏差 | std(finish_position) |
| past10_best_finish | 最高着順 | min(finish_position) |
| past10_worst_finish | 最低着順 | max(finish_position) |
| past10_win_rate | 勝率 | count(1着) / count(全) |
| past10_place_rate | 連対率 | count(1-2着) / count(全) |
| past10_show_rate | 複勝率 | count(1-3着) / count(全) |
| past10_recent3_avg | 最近3走平均 | mean(last 3 races) |
| past10_consistency | 一貫性スコア | 1 / (std + 1) |
| past10_form_score | 調子スコア | weighted avg (近い方が重い) |
| past10_distance_aptitude | 距離適性 | 類似距離の成績 |
| past10_surface_aptitude | 芝ダ適性 | 同表面の成績 |
| past10_venue_aptitude | 場所適性 | 同競馬場の成績 |

**計算コード**: `keiba_ai/ultimate_features.py` の `UltimateFeatureCalculator`

---

### 3. 騎手統計（5列）

データベースから直近180日の騎手成績を集計

| 特徴量 | 説明 | 計算式 |
|--------|------|--------|
| jockey_180d_win_rate | 直近180日勝率 | count(1着) / count(全) |
| jockey_180d_place_rate | 直近180日連対率 | count(1-2着) / count(全) |
| jockey_180d_show_rate | 直近180日複勝率 | count(1-3着) / count(全) |
| jockey_180d_avg_finish | 直近180日平均着順 | mean(finish_position) |
| jockey_180d_race_count | 直近180日レース数 | count(*) |

---

### 5. 調教師統計（4列）

データベースから直近180日の調教師成績を集計

| 特徴量 | 説明 | 計算式 |
|--------|------|--------|
| trainer_180d_win_rate | 直近180日勝率 | count(1着) / count(全) |
| trainer_180d_place_rate | 直近180日連対率 | count(1-2着) / count(全) |
| trainer_180d_show_rate | 直近180日複勝率 | count(1-3着) / count(全) |
| trainer_180d_race_count | 直近180日レース数 | count(*) |

---

### 6. 前走詳細（7列）

| 特徴量 | 説明 | データソース |
|--------|------|-------------|
| prev_race_date | 前走日付 | データベース |
| prev_venue | 前走競馬場 | データベース |
| prev_distance | 前走距離 | データベース |
| prev_finish | 前走着順 | データベース |
| prev_weight | 前走馬体重 | データベース |
| distance_change | 距離変化 | distance - prev_distance |
| venue_change | 場所変更フラグ | venue != prev_venue |

---

### 7. 血統統計（3列）

| 特徴量 | 説明 | 計算方法 |
|--------|------|---------|
| sire_win_rate | 父の産駒勝率 | 父の全産駒成績から計算 |
| dam_win_rate | 母の産駒勝率 | 母の全産駒成績から計算 |
| damsire_win_rate | 母父の産駒勝率 | 母父の全産駒成績から計算 |

---

## 特徴量の収集方法

### フェーズ1: レース一覧取得

```python
# API: /api/race_list
# 入力: 開催日（例: "20240101"）
# 出力: race_id一覧

POST http://localhost:8000/api/race_list
{
  "kaisai_date": "20240101"
}

# レスポンス
{
  "race_ids": ["202401010101", "202401010102", ...]
}
```

### フェーズ2: レース詳細スクレイピング

```python
# API: /api/scrape
# 入力: race_id
# 出力: 標準版60列の特徴量

POST http://localhost:8000/api/scrape
{
  "race_id": "202401010101"
}

# 自動的にデータベースに保存
```

### フェーズ3: Ultimate特徴量計算

```python
# keiba_ai/ultimate_features.py
from keiba_ai.ultimate_features import UltimateFeatureCalculator

calculator = UltimateFeatureCalculator("keiba_ultimate.db")
df_ultiレース基本情報（20列）

レース、馬、騎手、調教師の基本情報をnetkeiba.comから収集

| カテゴリ | 特徴量例 | データ型 |
|---------|---------|---------|
| レース情報 | race_id, race_name, distance, venue, weather | category/int |
| 馬情報 | horse_id, horse_name, sex_age, weight | string/int |
| 騎手・調教師 | jockey_id, jockey_name, trainer_id, trainer_name | string |
| 血統 | sire, dam, damsire | string |
| オッズ・人気 | odds, popularity, bracket_number, horse_number | float/int |

**収集方法**: 出馬表ページから直接スクレイピング
### 実装ファイル

| ファイル | 役割 |
|---------|------|
| `keiba_ai/netkeiba/parsers.py` | スクレイピング・パース |
| `keiba_ai/ultimate_features.py` | Ultimate特徴量計算 |
| `keiba_ai/feature_engineering.py` | 派生特徴量生成 |
| `keiba_ai/lightgbm_feature_optimizer.py` | LightGBM最適化 |

### 計算フロー

```python
# 1. レース情報のスクレイピング
from keiba_ai.netkeiba.client import NetkeibaClient
client = NetkeibaClient()
html = client.fetch_race_shutuba("202401010101")
entries = parse_shutuba_table(html)

# 2. データベースに保存
conn = sqlite3.connect("keiba_ultimate.db")
save_to_database(conn, entries)

# 3. Ultimate特徴量の計算（過去10走統計など）
calculaラップタイム・ペース（15列）

| 特徴量 | 説明 | データソース |
|--------|------|-------------|
| lap_200m ~ lap_2400m | 累計・区間ラップ | レース結果ページ |
| last_3f | 上がり3ハロン | レース結果ページ |
| pace_diff | 前半後半ペース差 | 計算 |
| corner_1 ~ corner_4 | コーナー通過順位 | レース結果ページ |

---

### 4. tor = UltimateFeatureCalculator("keiba_ultimate.db")
df_ultimate = calculator.add_ultimate_features(entries)  # 90+列

# 4. 派生特徴量の生成
from keiba_ai.feature_engineering import add_derived_features
df_final = add_derived_features(df_ultimate)  # 100+列
```

---

## LightGBM最適化

詳細は [lightgbm_feature_optimization_guide.md](../development/lightgbm_feature_optimization_guide.md) を参照

### カテゴリカル特徴量の処理

```python
# 低カーディナリティ（10種類以下）
categorical_features = [
    'venue',           # 競馬場（10箇所）
    'weather',         # 天候（3種類）
    'field_condition', # 馬場状態（4種類）
    'track_type',      # 芝/ダート（2種類）
    'sex',             # 性別（3種類）
]

# Label Encodingして使用
lgb_params = {
    'objective': 'multiclass',
    'num_class': 18,
    'categorical_feature': categorical_features
}
```

### 高カーディナリティの処理

```python
# 騎手名、調教師名、馬名は統計量に変換
# ❌ ワンホットエンコーディング → 100+列に爆発
# ✅ 統計特徴量化 → 3-5列に圧縮

# jockey_name='C.ル基本情報 |
| Ultimate統計追加 | 90列 | 過去10走、騎手統計など |
| 派生特徴量 | 100列 | 距離適性、調子スコアなど5
# jockey_avg_finish=3.2  
# jockey_race_count=1500
```

### 最終的な特徴量数

| 段階 | 特徴量数 | 説明 |
|------|---------|------|
| スクレイピング | 60列 | 標準版 |
| Ultimate追加 | 90列 | +30列 |
| 派生特徴量 | 100列 | +10列 |
| LightGBM最適化 | 80列 | カテゴリ処理後 |

---

## ベストプラクティス

### ✅ DO（推奨）

1. **データベースに蓄積してから統計計算**
   - 過去10走、騎手統計は必ずDBから計算
   - メモリ効率が良い

2. **カテゴリカル変数はLightGBMに任せる**
   - Label Encoding + categorical_feature指定
   - ワンホットは使わない

3. **高カーディナリティは統計化**
   - 騎手名 → 勝率、平均着順など
   - 新人にも対応可能

4. **欠損値の適切な処理**
   - 数値: -999や中央値で埋める
   - カテゴリ: "unknown"カテゴリを作る

### ❌ DON'T（非推奨）

1. **ワンホットエンコーディングの乱用**
   - メモリ爆発
   - 過学習の原因

2. **未来の情報を使う**
   - レース結果を予測に使わない
   - リーケージに注意

3. **スケーリング**
   - LightGBMには不要
   - 解釈性が下がる

4. **特徴量の過剰な追加**
   - 100列を超えると過学習リスク
   - 重要度を確認して削減

---

## トラブルシューティング

### Q: Ultimate特徴量が計算されない

**原因**: データベースに過去データがない

**解決策**:
```bash
# 過去データを収集
python collect_ultimate_data.py --start-date 20231001 --end-date 20231231
```

### Q: メモリ不足エラー

**原因**: ワンホットエンコーディングで特徴量爆発

**解決策**:
```python
# lightgbm_feature_optimizer.py を使用
from keiba_ai.lightgbm_feature_optimizer import prepare_for_lightgbm_ultimate
X_optimized = prepare_for_lightgbm_ultimate(X_raw)
```

### Q: 新人騎手・新馬の予測ができない

**原因**: 統計データが不足

**解決策**:
```python
# デフォルト値を設定
jockey_win_rate = jockey_stats.get('win_rate', 0.05)  # 新人は5%
trainer_win_rate = trainer_stats.get('win_rate', 0.08)  # 新人は8%
```

---

## 関連ドキュメント

- [ULTIMATE_FEATURES.md](ULTIMATE_FEATURES.md) - Ultimate版詳細
- [lightgbm_feature_optimization_guide.md](../development/lightgbm_feature_optimization_guide.md) - LightGBM最適化
- [DATABASE_SCHEMA_ANALYSIS.md](../development/DATABASE_SCHEMA_ANALYSIS.md) - データベース設計
- [SCRAPING_README.md](../development/SCRAPING_README.md) - スクレイピング詳細

---

**最終更新**: 2026-02-15
