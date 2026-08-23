-- Phase 3M fresh-project bootstrap: canonical race write domain.
--
-- This is the only bootstrap migration that defines public.races.  It merges
-- the legacy netkeiba write contract with the normalized ultimate columns so
-- later migrations never create a competing races shape.

CREATE TABLE IF NOT EXISTS public.races (
    race_id TEXT PRIMARY KEY,
    race_name TEXT,
    venue TEXT,
    date TEXT,
    kaisai_date DATE,
    post_time TIME,
    race_class TEXT,
    distance INTEGER,
    track_type TEXT,
    surface TEXT,
    course_direction TEXT,
    weather TEXT,
    field_condition TEXT,
    kai INTEGER,
    day INTEGER,
    num_horses INTEGER,
    horse_count INTEGER,
    prize_money TEXT,
    market_entropy NUMERIC(10, 4),
    top3_probability NUMERIC(10, 4),
    source TEXT,
    user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT races_distance_nonnegative CHECK (distance IS NULL OR distance >= 0),
    CONSTRAINT races_num_horses_nonnegative CHECK (num_horses IS NULL OR num_horses >= 0),
    CONSTRAINT races_horse_count_nonnegative CHECK (horse_count IS NULL OR horse_count >= 0)
);

CREATE TABLE IF NOT EXISTS public.race_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    race_id TEXT NOT NULL REFERENCES public.races(race_id) ON DELETE CASCADE,
    finish_position INTEGER,
    bracket_number INTEGER,
    horse_number INTEGER,
    umaban INTEGER,
    chakujun INTEGER,
    wakuban INTEGER,
    horse_name TEXT,
    sex TEXT,
    age INTEGER,
    jockey_weight DOUBLE PRECISION,
    kinryo DOUBLE PRECISION,
    jockey_name TEXT,
    trainer_name TEXT,
    owner_name TEXT,
    finish_time TEXT,
    time_seconds DOUBLE PRECISION,
    odds DOUBLE PRECISION,
    tansho_odds DOUBLE PRECISION,
    popularity INTEGER,
    margin TEXT,
    corner_positions TEXT,
    last_3f_time DOUBLE PRECISION,
    horse_weight INTEGER,
    weight_change INTEGER,
    prize_money BIGINT,
    user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.race_odds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    race_id TEXT NOT NULL REFERENCES public.races(race_id) ON DELETE CASCADE,
    umaban INTEGER,
    tansho_odds DOUBLE PRECISION,
    fukusho_odds_min DOUBLE PRECISION,
    fukusho_odds_max DOUBLE PRECISION,
    user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.race_payouts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    race_id TEXT NOT NULL REFERENCES public.races(race_id) ON DELETE CASCADE,
    bet_type TEXT NOT NULL,
    combination TEXT,
    payout BIGINT,
    popularity INTEGER,
    user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_races_venue ON public.races(venue);
CREATE INDEX IF NOT EXISTS idx_races_kaisai_date ON public.races(kaisai_date);
CREATE INDEX IF NOT EXISTS idx_races_track_type ON public.races(track_type);
CREATE INDEX IF NOT EXISTS idx_races_distance ON public.races(distance);
CREATE INDEX IF NOT EXISTS idx_races_user_id ON public.races(user_id);
CREATE INDEX IF NOT EXISTS idx_race_results_race_id ON public.race_results(race_id);
CREATE INDEX IF NOT EXISTS idx_race_results_user_id ON public.race_results(user_id);
CREATE INDEX IF NOT EXISTS idx_race_odds_race_id ON public.race_odds(race_id);
CREATE INDEX IF NOT EXISTS idx_race_odds_user_id ON public.race_odds(user_id);
CREATE INDEX IF NOT EXISTS idx_race_payouts_race_id ON public.race_payouts(race_id);
CREATE INDEX IF NOT EXISTS idx_race_payouts_user_id ON public.race_payouts(user_id);

CREATE OR REPLACE FUNCTION public.phase3m_set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.phase3m_set_updated_at()
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.phase3m_set_updated_at()
    TO service_role;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_trigger
        WHERE tgname = 'phase3m_races_set_updated_at'
          AND tgrelid = 'public.races'::regclass
          AND NOT tgisinternal
    ) THEN
        EXECUTE 'CREATE TRIGGER phase3m_races_set_updated_at BEFORE UPDATE ON public.races FOR EACH ROW EXECUTE FUNCTION public.phase3m_set_updated_at()';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_trigger
        WHERE tgname = 'phase3m_race_odds_set_updated_at'
          AND tgrelid = 'public.race_odds'::regclass
          AND NOT tgisinternal
    ) THEN
        EXECUTE 'CREATE TRIGGER phase3m_race_odds_set_updated_at BEFORE UPDATE ON public.race_odds FOR EACH ROW EXECUTE FUNCTION public.phase3m_set_updated_at()';
    END IF;
END;
$$;

ALTER TABLE public.races ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.race_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.race_odds ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.race_payouts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.races FORCE ROW LEVEL SECURITY;
ALTER TABLE public.race_results FORCE ROW LEVEL SECURITY;
ALTER TABLE public.race_odds FORCE ROW LEVEL SECURITY;
ALTER TABLE public.race_payouts FORCE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.races, public.race_results, public.race_odds, public.race_payouts
    FROM PUBLIC, anon, authenticated;
GRANT ALL ON TABLE
    public.races, public.race_results, public.race_odds, public.race_payouts
    TO service_role;
