-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- スクレイピング用テーブル統合スキーマ
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- このファイルをSupabaseのSQL Editorで実行してください

-- 0. 既存テーブルを削除（外部キー制約問題を回避）
DROP TABLE IF EXISTS public.race_payouts CASCADE;
DROP TABLE IF EXISTS public.race_odds CASCADE;
DROP TABLE IF EXISTS public.race_results CASCADE;
DROP TABLE IF EXISTS public.results CASCADE;  -- 古いテーブル名も削除
DROP TABLE IF EXISTS public.races CASCADE;

-- 1. レーステーブル
CREATE TABLE IF NOT EXISTS public.races (
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
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 2. レース結果テーブル
CREATE TABLE IF NOT EXISTS public.race_results (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    race_id TEXT NOT NULL,
    finish_position INTEGER,
    bracket_number INTEGER,
    horse_number INTEGER,
    horse_name TEXT,
    sex TEXT,
    age INTEGER,
    jockey_weight REAL,
    jockey_name TEXT,
    finish_time TEXT,
    odds REAL,
    popularity INTEGER,
    trainer_name TEXT,
    owner_name TEXT,
    margin TEXT,
    corner_positions TEXT,
    last_3f_time REAL,
    horse_weight INTEGER,
    weight_change INTEGER,
    prize_money INTEGER,
    user_id UUID,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 3. オッズテーブル
CREATE TABLE IF NOT EXISTS public.race_odds (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    race_id TEXT NOT NULL,
    umaban INTEGER,
    tansho_odds REAL,
    fukusho_odds_min REAL,
    fukusho_odds_max REAL,
    user_id UUID,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 4. 払戻金テーブル
CREATE TABLE IF NOT EXISTS public.race_payouts (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    race_id TEXT NOT NULL,
    bet_type TEXT NOT NULL,
    combination TEXT,
    payout BIGINT,
    popularity INTEGER,
    user_id UUID,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 5. RLS無効化（開発用 - 本番環境では有効化してください）
ALTER TABLE public.races DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.race_results DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.race_odds DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.race_payouts DISABLE ROW LEVEL SECURITY;

-- 6. 既存ポリシー削除（エラー回避）
DROP POLICY IF EXISTS "Users can view own races" ON public.races;
DROP POLICY IF EXISTS "Users can insert own races" ON public.races;
DROP POLICY IF EXISTS "Users can update own races" ON public.races;
DROP POLICY IF EXISTS "Users can delete own races" ON public.races;

DROP POLICY IF EXISTS "Users can view own results" ON public.race_results;
DROP POLICY IF EXISTS "Users can insert own results" ON public.race_results;
DROP POLICY IF EXISTS "Users can update own results" ON public.race_results;
DROP POLICY IF EXISTS "Users can delete own results" ON public.race_results;

DROP POLICY IF EXISTS "Users can view own odds" ON public.race_odds;
DROP POLICY IF EXISTS "Users can insert own odds" ON public.race_odds;
DROP POLICY IF EXISTS "Users can update own odds" ON public.race_odds;
DROP POLICY IF EXISTS "Users can delete own odds" ON public.race_odds;

DROP POLICY IF EXISTS "Users can view own payouts" ON public.race_payouts;
DROP POLICY IF EXISTS "Users can insert own payouts" ON public.race_payouts;
DROP POLICY IF EXISTS "Users can update own payouts" ON public.race_payouts;
DROP POLICY IF EXISTS "Users can delete own payouts" ON public.race_payouts;

-- 7. racesテーブルのRLSポリシー
CREATE POLICY "Users can view own races" ON public.races
  FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own races" ON public.races
  FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own races" ON public.races
  FOR UPDATE
  USING (auth.uid() = user_id);

CREATE POLICY "Users can delete own races" ON public.races
  FOR DELETE
  USING (auth.uid() = user_id);

-- 8. race_resultsテーブルのRLSポリシー
CREATE POLICY "Users can view own results" ON public.race_results
  FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own results" ON public.race_results
  FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own results" ON public.race_results
  FOR UPDATE
  USING (auth.uid() = user_id);

CREATE POLICY "Users can delete own results" ON public.race_results
  FOR DELETE
  USING (auth.uid() = user_id);

-- 9. race_oddsテーブルのRLSポリシー
CREATE POLICY "Users can view own odds" ON public.race_odds
  FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own odds" ON public.race_odds
  FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own odds" ON public.race_odds
  FOR UPDATE
  USING (auth.uid() = user_id);

CREATE POLICY "Users can delete own odds" ON public.race_odds
  FOR DELETE
  USING (auth.uid() = user_id);

-- 10. race_payoutsテーブルのRLSポリシー
CREATE POLICY "Users can view own payouts" ON public.race_payouts
  FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own payouts" ON public.race_payouts
  FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own payouts" ON public.race_payouts
  FOR UPDATE
  USING (auth.uid() = user_id);

CREATE POLICY "Users can delete own payouts" ON public.race_payouts
  FOR DELETE
  USING (auth.uid() = user_id);

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- 完了メッセージ
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- 実行完了！
-- 以下のテーブルが作成されました:
--   ✓ races (レース基本情報)
--   ✓ race_results (レース結果)
--   ✓ race_odds (オッズ情報)
--   ✓ race_payouts (払戻金情報)
-- 
-- RLSポリシーも設定済み:
--   ✓ ユーザーは自分のデータのみアクセス可能
