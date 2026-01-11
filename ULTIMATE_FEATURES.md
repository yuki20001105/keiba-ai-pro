# Ultimate版の特徴量一覧

現在の標準版CSVは**60列**の特徴量を持っています：

## 標準版（60列）の内訳

### 1. レース基本情報（14列）
- race_id, race_name, post_time, track_type, distance
- course_direction, weather, field_condition, kai, venue
- day, race_class, horse_count, prize_money

### 2. 馬・騎手・調教師情報（10列）
- horse_name, sex_age, weight, horse_birth_date
- horse_owner, horse_breeder, horse_breeding_farm
- jockey_name, jockey_weight, trainer_name

### 3. 血統情報（3列）
- sire（父）, dam（母）, damsire（母の父）

### 4. レース結果（9列）
- finish_position, bracket_number, horse_number
- finish_time, margin, popularity, odds
- last_3f, corner_positions

### 5. 過去成績（3列）
- past_performance_1, past_performance_2, past_performance_3

### 6. 騎手・調教師統計（7列）
- jockey_win_rate, jockey_place_rate_top2, jockey_show_rate, jockey_graded_wins
- trainer_win_rate, trainer_place_rate_top2, trainer_show_rate

### 7. ラップタイム（10列）
- lap_200m, lap_400m, lap_600m, lap_800m, lap_1000m
- lap_1200m, lap_1400m, lap_1600m, lap_1800m, lap_2000m

### 8. コーナー通過順位（4列）
- corner_1, corner_2, corner_3, corner_4

---

## Ultimate版で追加される特徴量（+30列以上）

### 1. ID情報（3列）
- **horse_id**: 馬のID（リレーション用）
- **jockey_id**: 騎手のID
- **trainer_id**: 調教師のID

### 2. 馬詳細情報（7列）
- **coat_color**: 毛色
- **sale_price**: セール価格
- **total_prize_money**: 通算獲得賞金
- **total_runs**: 通算出走回数
- **total_wins**: 通算勝利数
- **total_seconds**: 通算2着回数
- **total_thirds**: 通算3着回数

### 3. 前走詳細（7列）
- **prev_race_date**: 前走日付
- **prev_venue**: 前走場所
- **prev_distance**: 前走距離
- **prev_finish**: 前走着順
- **prev_weight**: 前走馬体重
- **distance_change**: 距離変化（前走比）
- **venue_change**: 場所変更フラグ

### 4. ラップタイム拡張（15列）
- **lap_sect_200m** 〜 **lap_sect_2400m**: 区間ラップ（200m区間ごと）
- 標準版の累計ラップに加えて、区間ごとのペースを分析可能

### 5. ペース情報（1列）
- **pace_diff**: 前半後半のペース差

### 6. 血統統計（3列）
- **sire_win_rate**: 父の勝率
- **dam_win_rate**: 母の勝率
- **damsire_win_rate**: 母父の勝率

### 7. 市場データ（2列）
- **market_entropy**: 人気のエントロピー（予想の分散度）
- **top3_probability**: 3着以内確率（オッズベース）

### 8. 結果拡張（1列）
- **last_3f_rank**: 上がり3ハロン順位

---

## Ultimate版の特徴

### データベース構造
- **10テーブル構成**: races, entries, results, horse_details, past_performances, jockey_details, trainer_details, race_lap_times, bloodline_stats, models

### 分析メリット
1. **リレーショナル分析**: IDによる馬・騎手・調教師の詳細追跡
2. **ペース分析**: 区間ラップで詳細なペース変化を把握
3. **血統分析**: 父母の勝率データで血統傾向を数値化
4. **前走比較**: 前走との詳細比較で調子を判断
5. **市場分析**: オッズエントロピーで予想の難易度を測定

### 機械学習への影響
- **特徴量: 60列 → 90+列** (1.5倍)
- **予測精度**: より細かいパターン認識が可能
- **過学習リスク**: データ量に応じた適切なモデル選択が重要

---

## 実データ例

現在表示中のCSV（race_data_202006010101_20260111_151631.csv）は標準版60列です。

Ultimate版で同じレースを取得すると：
- **race_id**: 202006010101（同じ）
- **horse_id**: 2014103456（新規）
- **lap_sect_400m**: 11.2秒（新規）
- **prev_distance**: 1400m（新規）
- **market_entropy**: 2.34（新規）

などの詳細データが追加されます。
