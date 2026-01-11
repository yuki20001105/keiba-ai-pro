-- 既存のスキーマに追加

-- レーステーブル
CREATE TABLE IF NOT EXISTS races (
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
    user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- レース結果テーブル
CREATE TABLE IF NOT EXISTS race_results (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    race_id TEXT NOT NULL,
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
    user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- オッズテーブル
CREATE TABLE IF NOT EXISTS race_odds (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    race_id TEXT NOT NULL,
    umaban INTEGER,
    tansho_odds REAL,
    fukusho_odds_min REAL,
    fukusho_odds_max REAL,
    user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 払戻テーブル
CREATE TABLE IF NOT EXISTS race_payouts (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    race_id TEXT NOT NULL,
    bet_type TEXT,
    combination TEXT,
    payout INTEGER,
    popularity INTEGER,
    user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 購入履歴テーブル（既存のbetsテーブルを拡張）
ALTER TABLE public.bets ADD COLUMN IF NOT EXISTS race_id_detail TEXT;
ALTER TABLE public.bets ADD COLUMN IF NOT EXISTS season TEXT;
ALTER TABLE public.bets ADD COLUMN IF NOT EXISTS venue TEXT;
ALTER TABLE public.bets ADD COLUMN IF NOT EXISTS combinations JSONB;
ALTER TABLE public.bets ADD COLUMN IF NOT EXISTS strategy_type TEXT;
ALTER TABLE public.bets ADD COLUMN IF NOT EXISTS purchase_count INTEGER;
ALTER TABLE public.bets ADD COLUMN IF NOT EXISTS unit_price INTEGER;
ALTER TABLE public.bets ADD COLUMN IF NOT EXISTS total_cost INTEGER;
ALTER TABLE public.bets ADD COLUMN IF NOT EXISTS expected_value REAL;
ALTER TABLE public.bets ADD COLUMN IF NOT EXISTS expected_return REAL;
ALTER TABLE public.bets ADD COLUMN IF NOT EXISTS recovery_rate REAL;
ALTER TABLE public.bets ADD COLUMN IF NOT EXISTS is_hit BOOLEAN DEFAULT false;

-- 機械学習モデルテーブル
CREATE TABLE IF NOT EXISTS ml_models (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    model_name TEXT NOT NULL,
    model_type TEXT NOT NULL,
    model_data BYTEA,
    accuracy REAL,
    precision_score REAL,
    recall_score REAL,
    f1_score REAL,
    feature_importance JSONB,
    training_date TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
    is_active BOOLEAN DEFAULT true
);

-- RLS Policies for races
ALTER TABLE races ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own races"
  ON races FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own races"
  ON races FOR INSERT
  WITH CHECK (auth.uid() = user_id);

-- RLS Policies for race_results
ALTER TABLE race_results ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own race_results"
  ON race_results FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own race_results"
  ON race_results FOR INSERT
  WITH CHECK (auth.uid() = user_id);

-- RLS Policies for race_odds
ALTER TABLE race_odds ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own race_odds"
  ON race_odds FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own race_odds"
  ON race_odds FOR INSERT
  WITH CHECK (auth.uid() = user_id);

-- RLS Policies for race_payouts
ALTER TABLE race_payouts ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own race_payouts"
  ON race_payouts FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own race_payouts"
  ON race_payouts FOR INSERT
  WITH CHECK (auth.uid() = user_id);

-- RLS Policies for ml_models
ALTER TABLE ml_models ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own ml_models"
  ON ml_models FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own ml_models"
  ON ml_models FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own ml_models"
  ON ml_models FOR UPDATE
  USING (auth.uid() = user_id);
