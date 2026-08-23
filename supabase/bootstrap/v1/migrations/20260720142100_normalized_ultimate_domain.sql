-- Phase 3M fresh-project bootstrap: normalized ultimate race domain.
-- public.races is intentionally not recreated here; 20260720142000 contains
-- its single canonical, merged definition.

CREATE TABLE IF NOT EXISTS public.horse_details (
    horse_id TEXT PRIMARY KEY,
    horse_name TEXT,
    birth_date DATE,
    coat_color TEXT,
    owner_name TEXT,
    breeder_name TEXT,
    breeding_farm TEXT,
    sale_price TEXT,
    total_prize_money NUMERIC(15, 2),
    total_runs INTEGER,
    total_wins INTEGER,
    total_seconds INTEGER,
    total_thirds INTEGER,
    sire TEXT,
    dam TEXT,
    damsire TEXT,
    user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.jockey_details (
    jockey_id TEXT PRIMARY KEY,
    jockey_name TEXT,
    win_rate NUMERIC(5, 2),
    place_rate_top2 NUMERIC(5, 2),
    show_rate NUMERIC(5, 2),
    graded_wins INTEGER,
    total_races INTEGER,
    user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.trainer_details (
    trainer_id TEXT PRIMARY KEY,
    trainer_name TEXT,
    win_rate NUMERIC(5, 2),
    place_rate_top2 NUMERIC(5, 2),
    show_rate NUMERIC(5, 2),
    total_races INTEGER,
    user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.entries (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    race_id TEXT NOT NULL REFERENCES public.races(race_id) ON DELETE CASCADE,
    horse_id TEXT NOT NULL,
    horse_name TEXT,
    horse_no INTEGER,
    bracket INTEGER,
    sex TEXT,
    age INTEGER,
    sex_age TEXT,
    handicap NUMERIC(5, 1),
    jockey_id TEXT,
    jockey_name TEXT,
    trainer_id TEXT,
    trainer_name TEXT,
    weight INTEGER,
    weight_diff INTEGER,
    weight_kg INTEGER,
    weight_change INTEGER,
    odds NUMERIC(10, 1),
    popularity INTEGER,
    raw_json JSONB,
    user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT entries_pkey PRIMARY KEY (race_id, horse_id),
    CONSTRAINT entries_id_key UNIQUE (id)
);

CREATE TABLE IF NOT EXISTS public.results (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    race_id TEXT NOT NULL REFERENCES public.races(race_id) ON DELETE CASCADE,
    horse_id TEXT NOT NULL,
    finish INTEGER,
    bracket_number INTEGER,
    horse_number INTEGER,
    time TEXT,
    margin TEXT,
    last3f NUMERIC(5, 1),
    last_3f_rank INTEGER,
    pass_order TEXT,
    corner_1 TEXT,
    corner_2 TEXT,
    corner_3 TEXT,
    corner_4 TEXT,
    odds NUMERIC(10, 1),
    popularity INTEGER,
    raw_json JSONB,
    user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT results_pkey PRIMARY KEY (race_id, horse_id),
    CONSTRAINT results_id_key UNIQUE (id)
);

CREATE TABLE IF NOT EXISTS public.past_performances (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    race_id TEXT NOT NULL REFERENCES public.races(race_id) ON DELETE CASCADE,
    horse_id TEXT NOT NULL,
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
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT past_performances_pkey PRIMARY KEY (race_id, horse_id),
    CONSTRAINT past_performances_id_key UNIQUE (id)
);

CREATE TABLE IF NOT EXISTS public.race_lap_times (
    race_id TEXT PRIMARY KEY REFERENCES public.races(race_id) ON DELETE CASCADE,
    lap_200m NUMERIC(6, 2),
    lap_400m NUMERIC(6, 2),
    lap_600m NUMERIC(6, 2),
    lap_800m NUMERIC(6, 2),
    lap_1000m NUMERIC(6, 2),
    lap_1200m NUMERIC(6, 2),
    lap_1400m NUMERIC(6, 2),
    lap_1600m NUMERIC(6, 2),
    lap_1800m NUMERIC(6, 2),
    lap_2000m NUMERIC(6, 2),
    lap_2200m NUMERIC(6, 2),
    lap_2400m NUMERIC(6, 2),
    lap_sect_200m NUMERIC(6, 2),
    lap_sect_400m NUMERIC(6, 2),
    lap_sect_600m NUMERIC(6, 2),
    lap_sect_800m NUMERIC(6, 2),
    lap_sect_1000m NUMERIC(6, 2),
    lap_sect_1200m NUMERIC(6, 2),
    lap_sect_1400m NUMERIC(6, 2),
    lap_sect_1600m NUMERIC(6, 2),
    lap_sect_1800m NUMERIC(6, 2),
    lap_sect_2000m NUMERIC(6, 2),
    lap_sect_2200m NUMERIC(6, 2),
    lap_sect_2400m NUMERIC(6, 2),
    pace_diff NUMERIC(6, 2),
    user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.payouts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    race_id TEXT NOT NULL REFERENCES public.races(race_id) ON DELETE CASCADE,
    bet_type TEXT,
    combination TEXT,
    payout BIGINT,
    popularity INTEGER,
    user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_entries_horse_id ON public.entries(horse_id);
CREATE INDEX IF NOT EXISTS idx_entries_jockey_id ON public.entries(jockey_id);
CREATE INDEX IF NOT EXISTS idx_entries_trainer_id ON public.entries(trainer_id);
CREATE INDEX IF NOT EXISTS idx_entries_user_id ON public.entries(user_id);
CREATE INDEX IF NOT EXISTS idx_results_horse_id ON public.results(horse_id);
CREATE INDEX IF NOT EXISTS idx_results_finish ON public.results(finish);
CREATE INDEX IF NOT EXISTS idx_results_user_id ON public.results(user_id);
CREATE INDEX IF NOT EXISTS idx_horse_details_name ON public.horse_details(horse_name);
CREATE INDEX IF NOT EXISTS idx_horse_details_sire ON public.horse_details(sire);
CREATE INDEX IF NOT EXISTS idx_horse_details_dam ON public.horse_details(dam);
CREATE INDEX IF NOT EXISTS idx_horse_details_damsire ON public.horse_details(damsire);
CREATE INDEX IF NOT EXISTS idx_horse_details_user_id ON public.horse_details(user_id);
CREATE INDEX IF NOT EXISTS idx_past_performances_horse_id ON public.past_performances(horse_id);
CREATE INDEX IF NOT EXISTS idx_past_performances_user_id ON public.past_performances(user_id);
CREATE INDEX IF NOT EXISTS idx_jockey_details_name ON public.jockey_details(jockey_name);
CREATE INDEX IF NOT EXISTS idx_jockey_details_user_id ON public.jockey_details(user_id);
CREATE INDEX IF NOT EXISTS idx_trainer_details_name ON public.trainer_details(trainer_name);
CREATE INDEX IF NOT EXISTS idx_trainer_details_user_id ON public.trainer_details(user_id);
CREATE INDEX IF NOT EXISTS idx_payouts_race_id ON public.payouts(race_id);
CREATE INDEX IF NOT EXISTS idx_payouts_user_id ON public.payouts(user_id);

DO $$
DECLARE
    target_table TEXT;
    trigger_name TEXT;
BEGIN
    FOREACH target_table IN ARRAY ARRAY['horse_details', 'jockey_details', 'trainer_details']
    LOOP
        trigger_name := 'phase3m_' || target_table || '_set_updated_at';
        IF NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_trigger
            WHERE tgname = trigger_name
              AND tgrelid = format('public.%I', target_table)::regclass
              AND NOT tgisinternal
        ) THEN
            EXECUTE format(
                'CREATE TRIGGER %I BEFORE UPDATE ON public.%I FOR EACH ROW EXECUTE FUNCTION public.phase3m_set_updated_at()',
                trigger_name,
                target_table
            );
        END IF;
    END LOOP;
END;
$$;

ALTER TABLE public.entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.results ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.horse_details ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.past_performances ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.jockey_details ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.trainer_details ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.race_lap_times ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.payouts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.entries FORCE ROW LEVEL SECURITY;
ALTER TABLE public.results FORCE ROW LEVEL SECURITY;
ALTER TABLE public.horse_details FORCE ROW LEVEL SECURITY;
ALTER TABLE public.past_performances FORCE ROW LEVEL SECURITY;
ALTER TABLE public.jockey_details FORCE ROW LEVEL SECURITY;
ALTER TABLE public.trainer_details FORCE ROW LEVEL SECURITY;
ALTER TABLE public.race_lap_times FORCE ROW LEVEL SECURITY;
ALTER TABLE public.payouts FORCE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE
    public.entries,
    public.results,
    public.horse_details,
    public.past_performances,
    public.jockey_details,
    public.trainer_details,
    public.race_lap_times,
    public.payouts
    FROM PUBLIC, anon, authenticated;
GRANT ALL ON TABLE
    public.entries,
    public.results,
    public.horse_details,
    public.past_performances,
    public.jockey_details,
    public.trainer_details,
    public.race_lap_times,
    public.payouts
    TO service_role;

CREATE OR REPLACE VIEW public.ml_training_data
WITH (security_invoker = true)
AS
SELECT
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
    res.finish,
    res.time,
    res.last3f,
    res.last_3f_rank,
    h.horse_id,
    h.horse_name,
    h.sire,
    h.dam,
    h.damsire,
    h.total_runs,
    h.total_wins,
    e.horse_no,
    e.bracket,
    e.sex_age,
    e.handicap,
    e.weight_kg,
    e.weight_change,
    e.odds,
    e.popularity,
    j.jockey_id,
    j.jockey_name,
    j.win_rate AS jockey_win_rate,
    j.place_rate_top2 AS jockey_place_rate,
    t.trainer_id,
    t.trainer_name,
    t.win_rate AS trainer_win_rate,
    t.place_rate_top2 AS trainer_place_rate,
    pp.prev_race_distance,
    pp.prev_race_finish,
    pp.distance_change,
    r.user_id
FROM public.races AS r
INNER JOIN public.results AS res
    ON r.race_id = res.race_id
   AND r.user_id IS NOT DISTINCT FROM res.user_id
INNER JOIN public.entries AS e
    ON r.race_id = e.race_id
   AND res.horse_id = e.horse_id
   AND r.user_id IS NOT DISTINCT FROM e.user_id
LEFT JOIN public.horse_details AS h
    ON e.horse_id = h.horse_id
   AND r.user_id IS NOT DISTINCT FROM h.user_id
LEFT JOIN public.jockey_details AS j
    ON e.jockey_id = j.jockey_id
   AND r.user_id IS NOT DISTINCT FROM j.user_id
LEFT JOIN public.trainer_details AS t
    ON e.trainer_id = t.trainer_id
   AND r.user_id IS NOT DISTINCT FROM t.user_id
LEFT JOIN public.past_performances AS pp
    ON r.race_id = pp.race_id
   AND e.horse_id = pp.horse_id
   AND r.user_id IS NOT DISTINCT FROM pp.user_id;

REVOKE ALL ON TABLE public.ml_training_data FROM PUBLIC, anon, authenticated;
GRANT SELECT ON TABLE public.ml_training_data TO service_role;
