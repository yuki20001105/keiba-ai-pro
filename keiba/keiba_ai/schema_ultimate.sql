-- Ultimate版データベーススキーマ (90列対応)
-- SQLite用スキーマ定義

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ============================================================
-- 1. レースマスタテーブル（拡張版）
-- ============================================================
CREATE TABLE IF NOT EXISTS races (
  race_id TEXT PRIMARY KEY,
  race_name TEXT,
  post_time TEXT,
  track_type TEXT,
  distance INTEGER,
  course_direction TEXT,
  weather TEXT,
  field_condition TEXT,
  kai INTEGER,
  venue TEXT,
  day INTEGER,
  race_class TEXT,
  horse_count INTEGER,
  prize_money TEXT,
  market_entropy REAL,
  top3_probability REAL,
  kaisai_date TEXT,
  source TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- 2. 出走馬テーブル（エントリー情報）
-- ============================================================
CREATE TABLE IF NOT EXISTS entries (
  race_id TEXT,
  horse_id TEXT,
  horse_name TEXT,
  horse_no INTEGER,
  bracket INTEGER,
  sex TEXT,
  age INTEGER,
  sex_age TEXT,
  handicap REAL,
  jockey_id TEXT,
  jockey_name TEXT,
  trainer_id TEXT,
  trainer_name TEXT,
  weight INTEGER,
  weight_diff INTEGER,
  weight_kg INTEGER,
  weight_change INTEGER,
  odds REAL,
  popularity INTEGER,
  raw_json TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (race_id, horse_id),
  FOREIGN KEY (race_id) REFERENCES races(race_id) ON DELETE CASCADE,
  FOREIGN KEY (horse_id) REFERENCES horse_details(horse_id) ON DELETE SET NULL,
  FOREIGN KEY (jockey_id) REFERENCES jockey_details(jockey_id) ON DELETE SET NULL,
  FOREIGN KEY (trainer_id) REFERENCES trainer_details(trainer_id) ON DELETE SET NULL
);

-- ============================================================
-- 3. レース結果テーブル（拡張版）
-- ============================================================
CREATE TABLE IF NOT EXISTS results (
  race_id TEXT,
  horse_id TEXT,
  finish INTEGER,
  bracket_number INTEGER,
  horse_number INTEGER,
  time TEXT,
  margin TEXT,
  last3f REAL,
  last_3f_rank INTEGER,
  pass_order TEXT,
  corner_1 TEXT,
  corner_2 TEXT,
  corner_3 TEXT,
  corner_4 TEXT,
  odds REAL,
  popularity INTEGER,
  raw_json TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (race_id, horse_id),
  FOREIGN KEY (race_id) REFERENCES races(race_id) ON DELETE CASCADE,
  FOREIGN KEY (horse_id) REFERENCES horse_details(horse_id) ON DELETE SET NULL
);

-- ============================================================
-- 4. 馬詳細マスタテーブル（新規）
-- ============================================================
CREATE TABLE IF NOT EXISTS horse_details (
  horse_id TEXT PRIMARY KEY,
  horse_name TEXT,
  birth_date TEXT,
  coat_color TEXT,
  owner_name TEXT,
  breeder_name TEXT,
  breeding_farm TEXT,
  sale_price TEXT,
  total_prize_money REAL,
  total_runs INTEGER,
  total_wins INTEGER,
  total_seconds INTEGER,
  total_thirds INTEGER,
  sire TEXT,
  dam TEXT,
  damsire TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- 5. 過去成績テーブル（新規）
-- ============================================================
CREATE TABLE IF NOT EXISTS past_performances (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  race_id TEXT,
  horse_id TEXT,
  past_performance_1 TEXT,
  past_performance_2 TEXT,
  past_performance_3 TEXT,
  prev_race_date TEXT,
  prev_race_venue TEXT,
  prev_race_distance INTEGER,
  prev_race_finish INTEGER,
  prev_race_weight TEXT,
  distance_change INTEGER,
  venue_change INTEGER,
  created_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY (race_id) REFERENCES races(race_id) ON DELETE CASCADE,
  FOREIGN KEY (horse_id) REFERENCES horse_details(horse_id) ON DELETE CASCADE
);

-- ============================================================
-- 6. 騎手マスタテーブル（新規）
-- ============================================================
CREATE TABLE IF NOT EXISTS jockey_details (
  jockey_id TEXT PRIMARY KEY,
  jockey_name TEXT,
  win_rate REAL,
  place_rate_top2 REAL,
  show_rate REAL,
  graded_wins INTEGER,
  total_races INTEGER,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- 7. 調教師マスタテーブル（新規）
-- ============================================================
CREATE TABLE IF NOT EXISTS trainer_details (
  trainer_id TEXT PRIMARY KEY,
  trainer_name TEXT,
  win_rate REAL,
  place_rate_top2 REAL,
  show_rate REAL,
  total_races INTEGER,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- 8. ラップタイムテーブル（新規）
-- ============================================================
CREATE TABLE IF NOT EXISTS race_lap_times (
  race_id TEXT PRIMARY KEY,
  -- 累計ラップ
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
  -- 区間ラップ
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
  -- ペース情報
  pace_diff REAL,
  created_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY (race_id) REFERENCES races(race_id) ON DELETE CASCADE
);

-- ============================================================
-- 9. 払戻情報テーブル（新規）
-- ============================================================
CREATE TABLE IF NOT EXISTS payouts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  race_id TEXT,
  bet_type TEXT,
  combination TEXT,
  payout INTEGER,
  popularity INTEGER,
  created_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY (race_id) REFERENCES races(race_id) ON DELETE CASCADE
);

-- ============================================================
-- 10. モデルテーブル（既存）
-- ============================================================
CREATE TABLE IF NOT EXISTS models (
  model_id TEXT PRIMARY KEY,
  created_at TEXT,
  target TEXT,
  n_rows INTEGER,
  notes TEXT
);

-- ============================================================
-- インデックス作成
-- ============================================================

-- レース検索用
CREATE INDEX IF NOT EXISTS idx_races_venue ON races(venue);
CREATE INDEX IF NOT EXISTS idx_races_kaisai_date ON races(kaisai_date);
CREATE INDEX IF NOT EXISTS idx_races_track_type ON races(track_type);
CREATE INDEX IF NOT EXISTS idx_races_distance ON races(distance);

-- 馬詳細検索用
CREATE INDEX IF NOT EXISTS idx_horse_details_sire ON horse_details(sire);
CREATE INDEX IF NOT EXISTS idx_horse_details_dam ON horse_details(dam);
CREATE INDEX IF NOT EXISTS idx_horse_details_damsire ON horse_details(damsire);
CREATE INDEX IF NOT EXISTS idx_horse_details_name ON horse_details(horse_name);

-- 過去成績検索用
CREATE INDEX IF NOT EXISTS idx_past_performances_horse_id ON past_performances(horse_id);
CREATE INDEX IF NOT EXISTS idx_past_performances_race_id ON past_performances(race_id);

-- 結果検索用
CREATE INDEX IF NOT EXISTS idx_results_race_id ON results(race_id);
CREATE INDEX IF NOT EXISTS idx_results_horse_id ON results(horse_id);
CREATE INDEX IF NOT EXISTS idx_results_finish ON results(finish);

-- エントリー検索用
CREATE INDEX IF NOT EXISTS idx_entries_race_id ON entries(race_id);
CREATE INDEX IF NOT EXISTS idx_entries_horse_id ON entries(horse_id);
CREATE INDEX IF NOT EXISTS idx_entries_jockey_id ON entries(jockey_id);
CREATE INDEX IF NOT EXISTS idx_entries_trainer_id ON entries(trainer_id);

-- 騎手・調教師検索用
CREATE INDEX IF NOT EXISTS idx_jockey_details_name ON jockey_details(jockey_name);
CREATE INDEX IF NOT EXISTS idx_trainer_details_name ON trainer_details(trainer_name);

-- ============================================================
-- ビュー：フル結合データ（機械学習用）
-- ============================================================
CREATE VIEW IF NOT EXISTS ml_training_data AS
SELECT 
  -- レース情報
  r.race_id,
  r.race_name,
  r.venue,
  r.kaisai_date,
  r.track_type,
  r.distance,
  r.weather,
  r.field_condition,
  r.race_class,
  r.horse_count,
  r.market_entropy,
  r.top3_probability,
  
  -- 結果
  res.finish,
  res.time,
  res.last3f,
  res.last_3f_rank,
  
  -- 馬情報
  h.horse_id,
  h.horse_name,
  h.sire,
  h.dam,
  h.damsire,
  h.total_runs,
  h.total_wins,
  
  -- エントリー情報
  e.horse_no,
  e.bracket,
  e.sex_age,
  e.handicap,
  e.weight_kg,
  e.weight_change,
  e.odds,
  e.popularity,
  
  -- 騎手情報
  j.jockey_id,
  j.jockey_name,
  j.win_rate as jockey_win_rate,
  j.place_rate_top2 as jockey_place_rate,
  
  -- 調教師情報
  t.trainer_id,
  t.trainer_name,
  t.win_rate as trainer_win_rate,
  t.place_rate_top2 as trainer_place_rate,
  
  -- 過去成績
  pp.prev_race_distance,
  pp.prev_race_finish,
  pp.distance_change
  
FROM races r
INNER JOIN results res ON r.race_id = res.race_id
INNER JOIN entries e ON r.race_id = e.race_id AND res.horse_id = e.horse_id
LEFT JOIN horse_details h ON e.horse_id = h.horse_id
LEFT JOIN jockey_details j ON e.jockey_id = j.jockey_id
LEFT JOIN trainer_details t ON e.trainer_id = t.trainer_id
LEFT JOIN past_performances pp ON r.race_id = pp.race_id AND e.horse_id = pp.horse_id;

-- ============================================================
-- トリガー：updated_at自動更新
-- ============================================================
CREATE TRIGGER IF NOT EXISTS update_races_timestamp 
AFTER UPDATE ON races
BEGIN
  UPDATE races SET updated_at = datetime('now') WHERE race_id = NEW.race_id;
END;

CREATE TRIGGER IF NOT EXISTS update_horse_details_timestamp 
AFTER UPDATE ON horse_details
BEGIN
  UPDATE horse_details SET updated_at = datetime('now') WHERE horse_id = NEW.horse_id;
END;

CREATE TRIGGER IF NOT EXISTS update_jockey_details_timestamp 
AFTER UPDATE ON jockey_details
BEGIN
  UPDATE jockey_details SET updated_at = datetime('now') WHERE jockey_id = NEW.jockey_id;
END;

CREATE TRIGGER IF NOT EXISTS update_trainer_details_timestamp 
AFTER UPDATE ON trainer_details
BEGIN
  UPDATE trainer_details SET updated_at = datetime('now') WHERE trainer_id = NEW.trainer_id;
END;
