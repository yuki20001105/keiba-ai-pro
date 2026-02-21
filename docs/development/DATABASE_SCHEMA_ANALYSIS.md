# データベーススキーマ分析と更新提案

## 📊 現状のスキーマ分析

### 1. SQLite（ローカルDB）- `keiba_ai/db.py`

**既存のテーブル:**

#### `races` テーブル
```sql
CREATE TABLE races (
  race_id TEXT PRIMARY KEY,
  kaisai_date TEXT,
  source TEXT,
  created_at TEXT
);
```
**列数:** 4列
**不足している情報:** レース名、距離、トラック種別、天候、馬場状態、賞金等

#### `entries` テーブル（出馬表）
```sql
CREATE TABLE entries (
  race_id TEXT,
  horse_id TEXT,
  horse_name TEXT,
  horse_no INTEGER,
  bracket INTEGER,
  sex TEXT,
  age INTEGER,
  handicap REAL,
  jockey_id TEXT,
  jockey_name TEXT,
  trainer_id TEXT,
  trainer_name TEXT,
  weight INTEGER,
  weight_diff INTEGER,
  odds REAL,
  popularity INTEGER,
  raw_json TEXT,
  created_at TEXT,
  PRIMARY KEY (race_id, horse_id)
);
```
**列数:** 18列
**不足している情報:** 血統、過去成績、騎手勝率、調教師勝率、馬体重の分解（kg/change）

#### `results` テーブル（結果）
```sql
CREATE TABLE results (
  race_id TEXT,
  horse_id TEXT,
  finish INTEGER,
  time TEXT,
  margin TEXT,
  last3f REAL,
  pass_order TEXT,
  odds REAL,
  popularity INTEGER,
  raw_json TEXT,
  created_at TEXT,
  PRIMARY KEY (race_id, horse_id)
);
```
**列数:** 11列
**不足している情報:** 上がり順位、馬体重（kg/change分解）

---

### 2. Supabase（クラウドDB）- `supabase/race_schema.sql`

#### `races` テーブル
```sql
CREATE TABLE races (
    race_id TEXT PRIMARY KEY,
    race_name TEXT,
    venue TEXT,
    date TEXT,
    race_class TEXT,
    distance INTEGER,
    track_type TEXT,
    weather TEXT,
    field_condition TEXT,
    num_horses INTEGER,
    surface TEXT,
    user_id UUID,
    created_at TIMESTAMP
);
```
**列数:** 13列
**不足している情報:** 発走時刻、コース方向、開催回、開催日、賞金、市場エントロピー、人気集中度

#### `race_results` テーブル
```sql
CREATE TABLE race_results (
    id UUID PRIMARY KEY,
    race_id TEXT,
    umaban INTEGER,
    chakujun INTEGER,
    wakuban INTEGER,
    horse_name TEXT,
    sex TEXT,
    age INTEGER,
    kinryo REAL,
    jockey_name TEXT,
    trainer_name TEXT,
    owner_name TEXT,
    tansho_odds REAL,
    popularity INTEGER,
    time_seconds REAL,
    margin TEXT,
    corner_positions TEXT,
    last_3f_time REAL,
    horse_weight INTEGER,
    weight_change INTEGER,
    prize_money INTEGER,
    user_id UUID,
    created_at TIMESTAMP
);
```
**列数:** 23列
**不足している情報:** horse_id, jockey_id, trainer_id, 血統, 上がり順位, 過去成績

---

## 🎯 Ultimate版（90列）との比較

### 不足している主要カテゴリ:

| カテゴリ | Ultimate版の列数 | 既存SQLite | 既存Supabase | 不足列数 |
|---------|----------------|-----------|-------------|---------|
| レース基本情報 | 16 | 4 | 13 | 3-12 |
| 結果テーブル | 20 | 11 | 23 | 0-9 |
| 馬詳細 | 14 | 0 | 0 | **14** |
| 過去成績派生 | 6 | 0 | 0 | **6** |
| 騎手詳細 | 4 | 2 (名前のみ) | 1 (名前のみ) | **3-4** |
| 調教師詳細 | 3 | 2 (名前のみ) | 1 (名前のみ) | **2-3** |
| ラップタイム累計 | 12 | 0 | 0 | **12** |
| ラップタイム区間 | 12 | 0 | 0 | **12** |
| コーナー通過 | 4 | 1 (pass_order) | 1 (corner_positions) | **3** |

**総計:** Ultimate版90列に対し、既存スキーマは**約55-60列不足**

---

## 🔧 推奨スキーマ更新

### Option 1: 既存テーブルの拡張（推奨）

#### 🟢 メリット:
- 既存データとの互換性維持
- 段階的な移行が可能
- 既存のクエリが動作し続ける

#### 🔴 デメリット:
- テーブルが巨大化（100列超）
- ALTER TABLEの実行が必要

---

### Option 2: 正規化設計（長期的推奨）

複数の関連テーブルに分割:

```
races (レース情報)
  ├── race_details (詳細: 天候、馬場、賞金等)
  ├── race_market_metrics (市場指標: エントロピー、人気集中度)
  └── race_lap_times (ラップタイム)

horses (馬マスタ)
  ├── horse_pedigrees (血統)
  ├── horse_career_stats (通算成績)
  └── horse_past_performances (過去成績)

race_entries (出走馬)
  ├── entry_results (結果)
  ├── entry_corner_positions (コーナー通過)
  └── entry_derived_features (派生特徴: 上がり順位等)

jockeys (騎手マスタ)
trainers (調教師マスタ)
```

#### 🟢 メリット:
- データの重複がない
- クエリが柔軟
- 保守性が高い

#### 🔴 デメリット:
- 複雑なJOINが必要
- 初期設計コストが高い
- 既存システムの大幅変更

---

## 📝 具体的な更新SQL

### A. SQLite用更新スキーマ（既存拡張）

```sql
-- races テーブル拡張
ALTER TABLE races ADD COLUMN race_name TEXT;
ALTER TABLE races ADD COLUMN post_time TEXT;
ALTER TABLE races ADD COLUMN track_type TEXT;
ALTER TABLE races ADD COLUMN distance INTEGER;
ALTER TABLE races ADD COLUMN course_direction TEXT;
ALTER TABLE races ADD COLUMN weather TEXT;
ALTER TABLE races ADD COLUMN field_condition TEXT;
ALTER TABLE races ADD COLUMN kai INTEGER;
ALTER TABLE races ADD COLUMN venue TEXT;
ALTER TABLE races ADD COLUMN day INTEGER;
ALTER TABLE races ADD COLUMN race_class TEXT;
ALTER TABLE races ADD COLUMN horse_count INTEGER;
ALTER TABLE races ADD COLUMN prize_money TEXT;
ALTER TABLE races ADD COLUMN market_entropy REAL;
ALTER TABLE races ADD COLUMN top3_probability REAL;

-- results テーブル拡張
ALTER TABLE results ADD COLUMN last_3f_rank INTEGER;
ALTER TABLE results ADD COLUMN weight_kg INTEGER;
ALTER TABLE results ADD COLUMN weight_change INTEGER;
ALTER TABLE results ADD COLUMN bracket_number INTEGER;
ALTER TABLE results ADD COLUMN horse_number INTEGER;
ALTER TABLE results ADD COLUMN sex_age TEXT;
ALTER TABLE results ADD COLUMN jockey_weight REAL;
ALTER TABLE results ADD COLUMN jockey_id TEXT;
ALTER TABLE results ADD COLUMN trainer_id TEXT;
ALTER TABLE results ADD COLUMN corner_1 TEXT;
ALTER TABLE results ADD COLUMN corner_2 TEXT;
ALTER TABLE results ADD COLUMN corner_3 TEXT;
ALTER TABLE results ADD COLUMN corner_4 TEXT;

-- 馬詳細テーブル（新規）
CREATE TABLE IF NOT EXISTS horse_details (
    horse_id TEXT PRIMARY KEY,
    birth_date TEXT,
    coat_color TEXT,
    owner_name TEXT,
    breeder_name TEXT,
    breeding_farm TEXT,
    sale_price TEXT,
    total_prize_money REAL,
    total_runs INTEGER,
    total_wins INTEGER,
    sire TEXT,
    dam TEXT,
    damsire TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- 過去成績テーブル（新規）
CREATE TABLE IF NOT EXISTS past_performances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id TEXT,
    horse_id TEXT,
    past_performance_1 TEXT,
    past_performance_2 TEXT,
    prev_race_date TEXT,
    prev_race_venue TEXT,
    prev_race_distance INTEGER,
    prev_race_finish INTEGER,
    prev_race_weight TEXT,
    distance_change INTEGER,
    FOREIGN KEY (race_id) REFERENCES races(race_id),
    FOREIGN KEY (horse_id) REFERENCES horse_details(horse_id)
);

-- 騎手詳細テーブル（新規）
CREATE TABLE IF NOT EXISTS jockey_details (
    jockey_id TEXT PRIMARY KEY,
    jockey_name TEXT,
    win_rate REAL,
    place_rate_top2 REAL,
    show_rate REAL,
    graded_wins INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- 調教師詳細テーブル（新規）
CREATE TABLE IF NOT EXISTS trainer_details (
    trainer_id TEXT PRIMARY KEY,
    trainer_name TEXT,
    win_rate REAL,
    place_rate_top2 REAL,
    show_rate REAL,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- ラップタイムテーブル（新規）
CREATE TABLE IF NOT EXISTS race_lap_times (
    race_id TEXT PRIMARY KEY,
    lap_200m REAL,
    lap_400m REAL,
    lap_600m REAL,
    lap_800m REAL,
    lap_1000m REAL,
    lap_1200m REAL,
    lap_1400m REAL,
    lap_1600m REAL,
    lap_1800m REAL,
    lap_2000m REAL,
    lap_2200m REAL,
    lap_2400m REAL,
    lap_sect_200m REAL,
    lap_sect_400m REAL,
    lap_sect_600m REAL,
    lap_sect_800m REAL,
    lap_sect_1000m REAL,
    lap_sect_1200m REAL,
    lap_sect_1400m REAL,
    lap_sect_1600m REAL,
    lap_sect_1800m REAL,
    lap_sect_2000m REAL,
    lap_sect_2200m REAL,
    lap_sect_2400m REAL,
    FOREIGN KEY (race_id) REFERENCES races(race_id)
);

-- インデックス作成
CREATE INDEX IF NOT EXISTS idx_horse_details_sire ON horse_details(sire);
CREATE INDEX IF NOT EXISTS idx_horse_details_dam ON horse_details(dam);
CREATE INDEX IF NOT EXISTS idx_past_performances_horse_id ON past_performances(horse_id);
CREATE INDEX IF NOT EXISTS idx_results_race_id ON results(race_id);
CREATE INDEX IF NOT EXISTS idx_results_horse_id ON results(horse_id);
```

---

### B. Supabase用更新スキーマ

```sql
-- races テーブル拡張
ALTER TABLE races ADD COLUMN IF NOT EXISTS post_time TIME;
ALTER TABLE races ADD COLUMN IF NOT EXISTS course_direction TEXT;
ALTER TABLE races ADD COLUMN IF NOT EXISTS kai INTEGER;
ALTER TABLE races ADD COLUMN IF NOT EXISTS day INTEGER;
ALTER TABLE races ADD COLUMN IF NOT EXISTS prize_money TEXT;
ALTER TABLE races ADD COLUMN IF NOT EXISTS market_entropy NUMERIC(10,4);
ALTER TABLE races ADD COLUMN IF NOT EXISTS top3_probability NUMERIC(10,4);

-- race_results テーブル拡張
ALTER TABLE race_results ADD COLUMN IF NOT EXISTS horse_id TEXT;
ALTER TABLE race_results ADD COLUMN IF NOT EXISTS jockey_id TEXT;
ALTER TABLE race_results ADD COLUMN IF NOT EXISTS trainer_id TEXT;
ALTER TABLE race_results ADD COLUMN IF NOT EXISTS last_3f_rank INTEGER;
ALTER TABLE race_results ADD COLUMN IF NOT EXISTS weight_kg INTEGER;
ALTER TABLE race_results ADD COLUMN IF NOT EXISTS corner_1 TEXT;
ALTER TABLE race_results ADD COLUMN IF NOT EXISTS corner_2 TEXT;
ALTER TABLE race_results ADD COLUMN IF NOT EXISTS corner_3 TEXT;
ALTER TABLE race_results ADD COLUMN IF NOT EXISTS corner_4 TEXT;

-- 馬詳細テーブル（新規）
CREATE TABLE IF NOT EXISTS horse_details (
    horse_id TEXT PRIMARY KEY,
    birth_date DATE,
    coat_color TEXT,
    owner_name TEXT,
    breeder_name TEXT,
    breeding_farm TEXT,
    sale_price TEXT,
    total_prize_money NUMERIC(15,2),
    total_runs INTEGER,
    total_wins INTEGER,
    sire TEXT,
    dam TEXT,
    damsire TEXT,
    user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 過去成績テーブル（新規）
CREATE TABLE IF NOT EXISTS past_performances (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    race_id TEXT,
    horse_id TEXT,
    past_performance_1 TEXT,
    past_performance_2 TEXT,
    prev_race_date DATE,
    prev_race_venue TEXT,
    prev_race_distance INTEGER,
    prev_race_finish INTEGER,
    prev_race_weight TEXT,
    distance_change INTEGER,
    user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 騎手詳細テーブル（新規）
CREATE TABLE IF NOT EXISTS jockey_details (
    jockey_id TEXT PRIMARY KEY,
    jockey_name TEXT,
    win_rate NUMERIC(5,2),
    place_rate_top2 NUMERIC(5,2),
    show_rate NUMERIC(5,2),
    graded_wins INTEGER,
    user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 調教師詳細テーブル（新規）
CREATE TABLE IF NOT EXISTS trainer_details (
    trainer_id TEXT PRIMARY KEY,
    trainer_name TEXT,
    win_rate NUMERIC(5,2),
    place_rate_top2 NUMERIC(5,2),
    show_rate NUMERIC(5,2),
    user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ラップタイムテーブル（新規）
CREATE TABLE IF NOT EXISTS race_lap_times (
    race_id TEXT PRIMARY KEY,
    lap_200m NUMERIC(6,2),
    lap_400m NUMERIC(6,2),
    lap_600m NUMERIC(6,2),
    lap_800m NUMERIC(6,2),
    lap_1000m NUMERIC(6,2),
    lap_1200m NUMERIC(6,2),
    lap_1400m NUMERIC(6,2),
    lap_1600m NUMERIC(6,2),
    lap_1800m NUMERIC(6,2),
    lap_2000m NUMERIC(6,2),
    lap_2200m NUMERIC(6,2),
    lap_2400m NUMERIC(6,2),
    lap_sect_200m NUMERIC(6,2),
    lap_sect_400m NUMERIC(6,2),
    lap_sect_600m NUMERIC(6,2),
    lap_sect_800m NUMERIC(6,2),
    lap_sect_1000m NUMERIC(6,2),
    lap_sect_1200m NUMERIC(6,2),
    lap_sect_1400m NUMERIC(6,2),
    lap_sect_1600m NUMERIC(6,2),
    lap_sect_1800m NUMERIC(6,2),
    lap_sect_2000m NUMERIC(6,2),
    lap_sect_2200m NUMERIC(6,2),
    lap_sect_2400m NUMERIC(6,2),
    user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- RLS Policies
ALTER TABLE horse_details ENABLE ROW LEVEL SECURITY;
ALTER TABLE past_performances ENABLE ROW LEVEL SECURITY;
ALTER TABLE jockey_details ENABLE ROW LEVEL SECURITY;
ALTER TABLE trainer_details ENABLE ROW LEVEL SECURITY;
ALTER TABLE race_lap_times ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own horse_details"
  ON horse_details FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own horse_details"
  ON horse_details FOR INSERT
  WITH CHECK (auth.uid() = user_id);

-- (他のテーブルも同様)

-- インデックス作成
CREATE INDEX IF NOT EXISTS idx_horse_details_sire ON horse_details(sire);
CREATE INDEX IF NOT EXISTS idx_horse_details_dam ON horse_details(dam);
CREATE INDEX IF NOT EXISTS idx_past_performances_horse_id ON past_performances(horse_id);
CREATE INDEX IF NOT EXISTS idx_race_results_race_id ON race_results(race_id);
CREATE INDEX IF NOT EXISTS idx_race_results_horse_id ON race_results(horse_id);
```

---

## 🚀 実装ステップ

### Phase 1: ローカルSQLite更新（即時実行可能）
1. ✅ 新しいスキーマSQLを実行
2. ✅ Ultimate版スクレイピングでデータ収集
3. ✅ 新テーブルへのデータ挿入ロジック実装

### Phase 2: データ移行（既存データ保持）
1. 既存`entries`テーブルから`horse_details`へ移行
2. 既存`results`テーブルから拡張列への移行

### Phase 3: Supabase更新（本番環境）
1. Supabase管理画面でスキーマ更新実行
2. RLSポリシーのテスト
3. 本番データ投入

---

## 📊 容量見積もり

### 1レースあたりのデータサイズ:

| テーブル | 行数 | カラム数 | サイズ/行 | 合計 |
|---------|-----|---------|----------|------|
| races | 1 | 16 | 500B | 500B |
| results | 16 | 20 | 400B | 6.4KB |
| horse_details | 16 | 14 | 600B | 9.6KB |
| past_performances | 16 | 10 | 300B | 4.8KB |
| jockey_details | 16 | 6 | 200B | 3.2KB |
| trainer_details | 16 | 5 | 200B | 3.2KB |
| race_lap_times | 1 | 26 | 300B | 300B |

**1レース合計:** 約28KB

**年間10,000レース:** 約280MB
**5年分:** 約1.4GB

→ SQLiteでも十分対応可能

---

## ⚠️ 注意事項

### データ整合性:
- `horse_id`, `jockey_id`, `trainer_id` の外部キー制約
- `race_id` の一貫性（12桁フォーマット）

### パフォーマンス:
- 大量JOINが発生する場合、適切なインデックス必須
- 血統検索（sire, dam）が頻繁ならインデックス推奨

### CSV→DB変換:
- Ultimate版CSV（90列）を上記テーブル構造に分解
- Pandasでの変換スクリプトが必要

---

## 💡 推奨アクション

### 🔥 最優先（今すぐ実行）:
1. ✅ SQLite用の新スキーマSQLファイル作成
2. ✅ `keiba_ai/db.py` に新テーブル用の upsert 関数追加
3. ✅ Ultimate版スクレイピングから新DBへの挿入ロジック実装

### 🟡 短期（1週間以内）:
4. CSV→DB変換スクリプト作成
5. 既存データの新スキーマへの移行
6. データ整合性チェック

### 🟢 中期（1ヶ月以内）:
7. Supabase本番スキーマ更新
8. フロントエンド（Next.js）の対応
9. パフォーマンステストと最適化

---

## 📝 次のステップ

以下のファイルを作成することを推奨:

1. ✅ `keiba/keiba_ai/schema_ultimate.sql` - 新スキーマ定義
2. ✅ `keiba/keiba_ai/db_ultimate.py` - 新テーブル用のCRUD関数
3. ✅ `csv_to_db_ultimate.py` - CSV→DB変換スクリプト
4. ✅ `supabase/schema_ultimate.sql` - Supabase用スキーマ

これらの作成を開始しますか？
