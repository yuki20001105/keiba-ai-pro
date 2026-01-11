# CSVエクスポート機能 - 使い方ガイド

## 生成されたCSVファイル

✅ **取得完了**: `race_data_202006010101_20260111_151631.csv`

### 📊 データ概要

- **総行数**: 16頭（1レース分）
- **総列数**: 60列
- **データ充足率**: 
  - 父馬: 100%
  - 母馬: 100%
  - 過去成績: 100%

---

## 📁 特徴量カテゴリー（全60列）

### 1. レース基本情報（14列）
- `race_id` - レースID（12桁）
- `race_name` - レース名
- `post_time` - 発走時刻
- `track_type` - 芝/ダート
- `distance` - 距離（メートル）
- `course_direction` - 左/右
- `weather` - 天候
- `field_condition` - 馬場状態
- `kai` - 開催回
- `venue` - 競馬場名
- `day` - 開催日目
- `race_class` - レースクラス
- `horse_count` - 出走頭数
- `prize_money` - 賞金

### 2. 結果テーブル（15列）
- `finish_position` - 着順 ⭐
- `bracket_number` - 枠番
- `horse_number` - 馬番
- `horse_name` - 馬名
- `sex_age` - 性齢
- `jockey_weight` - 斤量
- `jockey_name` - 騎手名
- `finish_time` - タイム ⭐
- `margin` - 着差
- `popularity` - 人気
- `odds` - 単勝オッズ
- `last_3f` - 後3F（上がり）
- `corner_positions` - コーナー通過順
- `trainer_name` - 調教師名
- `weight` - 馬体重

### 3. 馬詳細（10列）
- `horse_birth_date` - 生年月日
- `horse_owner` - 馬主
- `horse_breeder` - 生産者
- `horse_breeding_farm` - 産地
- `sire` - 父馬 🧬
- `dam` - 母馬 🧬
- `damsire` - 母父馬 🧬
- `past_performance_1` - 過去成績1
- `past_performance_2` - 過去成績2
- `past_performance_3` - 過去成績3

### 4. 騎手詳細（4列）
- `jockey_win_rate` - 勝率
- `jockey_place_rate_top2` - 連対率
- `jockey_show_rate` - 複勝率
- `jockey_graded_wins` - 重賞勝利数

### 5. 調教師詳細（3列）
- `trainer_win_rate` - 勝率
- `trainer_place_rate_top2` - 連対率
- `trainer_show_rate` - 複勝率

### 6. ラップタイム（10列）
- `lap_200m` ~ `lap_2000m` - 各地点のラップタイム

### 7. コーナー通過順位（4列）
- `corner_1` ~ `corner_4` - 各コーナー通過順位

---

## 💻 使用方法

### Excel で開く
```
1. CSVファイルをダブルクリック
2. UTF-8エンコーディングで自動的に開きます
```

### Python (Pandas) で分析
```python
import pandas as pd

# CSVを読み込み
df = pd.read_csv('race_data_202006010101_20260111_151631.csv')

# 基本統計
print(df.describe())

# 父馬ごとの勝率
sire_stats = df.groupby('sire')['finish_position'].apply(lambda x: (x == '1').sum())
print(sire_stats)

# 距離別の平均タイム
print(df.groupby('distance')['finish_time'].mean())

# 騎手勝率と着順の相関
print(df[['jockey_win_rate', 'finish_position']].corr())
```

### 機械学習モデルの訓練
```python
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier

# データ読み込み
df = pd.read_csv('race_data_202006010101_20260111_151631.csv')

# 特徴量選択（例）
features = [
    'distance', 'jockey_weight', 'popularity', 'odds',
    'jockey_win_rate', 'jockey_place_rate_top2',
    'trainer_win_rate'
]

# 欠損値を処理
X = df[features].fillna(0)

# 目的変数（3着以内に入るか）
y = df['finish_position'].astype(int) <= 3

# 訓練
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2)
model = RandomForestClassifier()
model.fit(X_train, y_train)

# 精度確認
print(f"Accuracy: {model.score(X_test, y_test):.2%}")

# 重要な特徴量
feature_importance = pd.DataFrame({
    'feature': features,
    'importance': model.feature_importances_
}).sort_values('importance', ascending=False)
print(feature_importance)
```

---

## 📈 複数レース一括取得

日付を指定して複数レースを一括取得：

```python
# export_bulk_to_csv.py を実行
python export_bulk_to_csv.py

# 例: 2020年1月5日の全レース
# → race_data_bulk_20200105_XXXXXX.csv が生成される
```

---

## 🔄 定期的なデータ収集

### バッチスクリプト例
```python
import subprocess
from datetime import datetime, timedelta

# 過去1週間のデータを取得
start_date = datetime(2020, 1, 1)
for i in range(7):
    date = start_date + timedelta(days=i)
    date_str = date.strftime('%Y%m%d')
    
    # スクレイピング実行
    print(f"Collecting data for {date_str}...")
    # ... スクレイピング処理
```

---

## ⚠️ 注意事項

1. **VPN接続**: ProtonVPNに接続してから実行してください
2. **レート制限**: 3-7秒間隔でスクレイピングします
3. **実行時間**: 
   - 1レース（詳細あり）: 約4分
   - 1レース（詳細なし）: 約10秒
4. **エンコーディング**: UTF-8 with BOM（Excelで文字化けしない）

---

## 📝 生成されるファイル

### 単一レース
- `race_data_{race_id}_{timestamp}.csv`
- 例: `race_data_202006010101_20260111_151631.csv`

### 複数レース
- `race_data_bulk_{kaisai_date}_{timestamp}.csv`
- 例: `race_data_bulk_20200105_20260111_160000.csv`

---

## 🎯 次のステップ

1. ✅ CSVエクスポート完成
2. ⏳ データベーススキーマの更新
3. ⏳ 大量データの収集（2020-2024年）
4. ⏳ 機械学習モデルの構築
5. ⏳ 予測システムの実装

現在は **60個の特徴量** を取得可能！
詳細ページを含めると **70個以上** に拡張可能！
