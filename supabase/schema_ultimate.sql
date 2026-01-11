-- Ultimate版Supabaseスキーマ (90列対応)
-- PostgreSQL用スキーマ定義

-- ============================================================
-- 1. レースマスタテーブル（拡張版）
-- ============================================================
CREATE TABLE IF NOT EXISTS races (
  race_id TEXT PRIMARY KEY,
  race_name TEXT,
  post_time TIME,
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
  market_entropy NUMERIC(10,4),
  top3_probability NUMERIC(10,4),
  kaisai_date DATE,
  source TEXT,
  user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ============================================================
-- 2. 出走馬テーブル（エントリー情報）
-- ============================================================
CREATE TABLE IF NOT EXISTS entries (
  id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  race_id TEXT,
  horse_id TEXT,
  horse_name TEXT,
  horse_no INTEGER,
  bracket INTEGER,
  sex TEXT,
  age INTEGER,
  sex_age TEXT,
  handicap NUMERIC(5,1),
  jockey_id TEXT,
  jockey_name TEXT,
  trainer_id TEXT,
  trainer_name TEXT,
  weight INTEGER,
  weight_diff INTEGER,
  weight_kg INTEGER,
  weight_change INTEGER,
  odds NUMERIC(10,1),
  popularity INTEGER,
  raw_json JSONB,
  user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  UNIQUE (race_id, horse_id)
);

-- ============================================================
-- 3. レース結果テーブル（拡張版）
-- ============================================================
CREATE TABLE IF NOT EXISTS results (
  id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  race_id TEXT,
  horse_id TEXT,
  finish INTEGER,
  bracket_number INTEGER,
  horse_number INTEGER,
  time TEXT,
  margin TEXT,
  last3f NUMERIC(5,1),
  last_3f_rank INTEGER,
  pass_order TEXT,
  corner_1 TEXT,
  corner_2 TEXT,
  corner_3 TEXT,
  corner_4 TEXT,
  odds NUMERIC(10,1),
  popularity INTEGER,
  raw_json JSONB,
  user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  UNIQUE (race_id, horse_id)
);

-- ============================================================
-- 4. 馬詳細マスタテーブル（新規）
-- ============================================================
CREATE TABLE IF NOT EXISTS horse_details (
  horse_id TEXT PRIMARY KEY,
  horse_name TEXT,
  birth_date DATE,
  coat_color TEXT,
  owner_name TEXT,
  breeder_name TEXT,
  breeding_farm TEXT,
  sale_price TEXT,
  total_prize_money NUMERIC(15,2),
  total_runs INTEGER,
  total_wins INTEGER,
  total_seconds INTEGER,
  total_thirds INTEGER,
  sire TEXT,
  dam TEXT,
  damsire TEXT,
  user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ============================================================
-- 5. 過去成績テーブル（新規）
-- ============================================================
CREATE TABLE IF NOT EXISTS past_performances (
  id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  race_id TEXT,
  horse_id TEXT,
  past_performance_1 TEXT,
  past_performance_2 TEXT,
  past_performance_3 TEXT,
  prev_race_date DATE,
  prev_race_venue TEXT,
  prev_race_distance INTEGER,
  prev_race_finish INTEGER,
  prev_race_weight TEXT,
  distance_change INTEGER,
  venue_change INTEGER,
  user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ============================================================
-- 6. 騎手マスタテーブル（新規）
-- ============================================================
CREATE TABLE IF NOT EXISTS jockey_details (
  jockey_id TEXT PRIMARY KEY,
  jockey_name TEXT,
  win_rate NUMERIC(5,2),
  place_rate_top2 NUMERIC(5,2),
  show_rate NUMERIC(5,2),
  graded_wins INTEGER,
  total_races INTEGER,
  user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ============================================================
-- 7. 調教師マスタテーブル（新規）
-- ============================================================
CREATE TABLE IF NOT EXISTS trainer_details (
  trainer_id TEXT PRIMARY KEY,
  trainer_name TEXT,
  win_rate NUMERIC(5,2),
  place_rate_top2 NUMERIC(5,2),
  show_rate NUMERIC(5,2),
  total_races INTEGER,
  user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ============================================================
-- 8. ラップタイムテーブル（新規）
-- ============================================================
CREATE TABLE IF NOT EXISTS race_lap_times (
  race_id TEXT PRIMARY KEY,
  -- 累計ラップ
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
  -- 区間ラップ
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
  -- ペース情報
  pace_diff NUMERIC(6,2),
  user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ============================================================
-- 9. 払戻情報テーブル（新規）
-- ============================================================
CREATE TABLE IF NOT EXISTS payouts (
  id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  race_id TEXT,
  bet_type TEXT,
  combination TEXT,
  payout INTEGER,
  popularity INTEGER,
  user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ============================================================
-- Row Level Security (RLS) ポリシー
-- ============================================================

ALTER TABLE races ENABLE ROW LEVEL SECURITY;
ALTER TABLE entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE results ENABLE ROW LEVEL SECURITY;
ALTER TABLE horse_details ENABLE ROW LEVEL SECURITY;
ALTER TABLE past_performances ENABLE ROW LEVEL SECURITY;
ALTER TABLE jockey_details ENABLE ROW LEVEL SECURITY;
ALTER TABLE trainer_details ENABLE ROW LEVEL SECURITY;
ALTER TABLE race_lap_times ENABLE ROW LEVEL SECURITY;
ALTER TABLE payouts ENABLE ROW LEVEL SECURITY;

-- races ポリシー
CREATE POLICY "Users can view own races"
  ON races FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own races"
  ON races FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own races"
  ON races FOR UPDATE
  USING (auth.uid() = user_id);

CREATE POLICY "Users can delete own races"
  ON races FOR DELETE
  USING (auth.uid() = user_id);

-- entries ポリシー
CREATE POLICY "Users can view own entries"
  ON entries FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own entries"
  ON entries FOR INSERT
  WITH CHECK (auth.uid() = user_id);

-- results ポリシー
CREATE POLICY "Users can view own results"
  ON results FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own results"
  ON results FOR INSERT
  WITH CHECK (auth.uid() = user_id);

-- horse_details ポリシー
CREATE POLICY "Users can view own horse_details"
  ON horse_details FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own horse_details"
  ON horse_details FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own horse_details"
  ON horse_details FOR UPDATE
  USING (auth.uid() = user_id);

-- past_performances ポリシー
CREATE POLICY "Users can view own past_performances"
  ON past_performances FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own past_performances"
  ON past_performances FOR INSERT
  WITH CHECK (auth.uid() = user_id);

-- jockey_details ポリシー
CREATE POLICY "Users can view own jockey_details"
  ON jockey_details FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own jockey_details"
  ON jockey_details FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own jockey_details"
  ON jockey_details FOR UPDATE
  USING (auth.uid() = user_id);

-- trainer_details ポリシー
CREATE POLICY "Users can view own trainer_details"
  ON trainer_details FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own trainer_details"
  ON trainer_details FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own trainer_details"
  ON trainer_details FOR UPDATE
  USING (auth.uid() = user_id);

-- race_lap_times ポリシー
CREATE POLICY "Users can view own race_lap_times"
  ON race_lap_times FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own race_lap_times"
  ON race_lap_times FOR INSERT
  WITH CHECK (auth.uid() = user_id);

-- payouts ポリシー
CREATE POLICY "Users can view own payouts"
  ON payouts FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own payouts"
  ON payouts FOR INSERT
  WITH CHECK (auth.uid() = user_id);

-- ============================================================
-- インデックス作成
-- ============================================================

-- レース検索用
CREATE INDEX IF NOT EXISTS idx_races_venue ON races(venue);
CREATE INDEX IF NOT EXISTS idx_races_kaisai_date ON races(kaisai_date);
CREATE INDEX IF NOT EXISTS idx_races_track_type ON races(track_type);
CREATE INDEX IF NOT EXISTS idx_races_distance ON races(distance);
CREATE INDEX IF NOT EXISTS idx_races_user_id ON races(user_id);

-- 馬詳細検索用
CREATE INDEX IF NOT EXISTS idx_horse_details_sire ON horse_details(sire);
CREATE INDEX IF NOT EXISTS idx_horse_details_dam ON horse_details(dam);
CREATE INDEX IF NOT EXISTS idx_horse_details_damsire ON horse_details(damsire);
CREATE INDEX IF NOT EXISTS idx_horse_details_name ON horse_details(horse_name);
CREATE INDEX IF NOT EXISTS idx_horse_details_user_id ON horse_details(user_id);

-- 過去成績検索用
CREATE INDEX IF NOT EXISTS idx_past_performances_horse_id ON past_performances(horse_id);
CREATE INDEX IF NOT EXISTS idx_past_performances_race_id ON past_performances(race_id);
CREATE INDEX IF NOT EXISTS idx_past_performances_user_id ON past_performances(user_id);

-- 結果検索用
CREATE INDEX IF NOT EXISTS idx_results_race_id ON results(race_id);
CREATE INDEX IF NOT EXISTS idx_results_horse_id ON results(horse_id);
CREATE INDEX IF NOT EXISTS idx_results_finish ON results(finish);
CREATE INDEX IF NOT EXISTS idx_results_user_id ON results(user_id);

-- エントリー検索用
CREATE INDEX IF NOT EXISTS idx_entries_race_id ON entries(race_id);
CREATE INDEX IF NOT EXISTS idx_entries_horse_id ON entries(horse_id);
CREATE INDEX IF NOT EXISTS idx_entries_jockey_id ON entries(jockey_id);
CREATE INDEX IF NOT EXISTS idx_entries_trainer_id ON entries(trainer_id);
CREATE INDEX IF NOT EXISTS idx_entries_user_id ON entries(user_id);

-- 騎手・調教師検索用
CREATE INDEX IF NOT EXISTS idx_jockey_details_name ON jockey_details(jockey_name);
CREATE INDEX IF NOT EXISTS idx_jockey_details_user_id ON jockey_details(user_id);
CREATE INDEX IF NOT EXISTS idx_trainer_details_name ON trainer_details(trainer_name);
CREATE INDEX IF NOT EXISTS idx_trainer_details_user_id ON trainer_details(user_id);

-- ============================================================
-- トリガー：updated_at自動更新
-- ============================================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_races_updated_at
BEFORE UPDATE ON races
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_horse_details_updated_at
BEFORE UPDATE ON horse_details
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_jockey_details_updated_at
BEFORE UPDATE ON jockey_details
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_trainer_details_updated_at
BEFORE UPDATE ON trainer_details
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

-- ============================================================
-- ビュー：フル結合データ（機械学習用）
-- ============================================================

CREATE OR REPLACE VIEW ml_training_data AS
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
  pp.distance_change,
  
  -- ユーザーID
  r.user_id
  
FROM races r
INNER JOIN results res ON r.race_id = res.race_id AND r.user_id = res.user_id
INNER JOIN entries e ON r.race_id = e.race_id AND res.horse_id = e.horse_id AND r.user_id = e.user_id
LEFT JOIN horse_details h ON e.horse_id = h.horse_id AND r.user_id = h.user_id
LEFT JOIN jockey_details j ON e.jockey_id = j.jockey_id AND r.user_id = j.user_id
LEFT JOIN trainer_details t ON e.trainer_id = t.trainer_id AND r.user_id = t.user_id
LEFT JOIN past_performances pp ON r.race_id = pp.race_id AND e.horse_id = pp.horse_id AND r.user_id = pp.user_id;
