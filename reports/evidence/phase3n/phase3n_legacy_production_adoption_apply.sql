BEGIN;
-- phase3n legacy Production adoption candidate 86a2d314a641160e852d3597396aadcd03e81347
-- phase3n adoption contract sha256 27372764c3e577e0cf9d158d50dbf2673bd7b05edfc6739911985740ece589e8
-- provider project ref (out-of-band binding) grfwkutcsavqicaimssn
-- execution target scope production
-- execution target project ref grfwkutcsavqicaimssn
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '300s';
SET LOCAL idle_in_transaction_session_timeout = '300s';
SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('phase3n-legacy-adoption:27372764c3e577e0cf9d158d50dbf2673bd7b05edfc6739911985740ece589e8', 0));
DO $phase3n_legacy_preflight$
BEGIN
    IF (SELECT array_agg(c.relname::TEXT ORDER BY c.relname::TEXT)
          FROM pg_catalog.pg_class AS c
          JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p'))
       IS DISTINCT FROM ARRAY['bank_records','bets','entries','horse_details','horse_pedigree','jockey_details','ml_models','model_metadata','ocr_usage','past_performances','payouts','predictions','profiles','purchase_history','race_lap_times','race_odds','race_payouts','race_results','race_results_ultimate','races','races_ultimate','results','trainer_details','users']::TEXT[] THEN
        RAISE EXCEPTION 'phase3n-legacy-public-table-set-changed';
    END IF;
    IF to_regnamespace('phase3n_legacy_20260816') IS NOT NULL THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-already-exists';
    END IF;
    IF to_regclass('phase3m_internal.bootstrap_history') IS NOT NULL THEN
        RAISE EXCEPTION 'phase3n-legacy-bootstrap-history-unexpected';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_class AS c
          JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public'
           AND c.relkind IN ('r', 'p')
           AND c.relname LIKE 'phase3n_%'
    ) THEN
        RAISE EXCEPTION 'phase3n-legacy-phase3n-object-unexpected';
    END IF;
    IF (SELECT count(*) FROM public.bank_records) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:bank_records';
    END IF;
    IF (SELECT count(*) FROM public.bets) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:bets';
    END IF;
    IF (SELECT count(*) FROM public.entries) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:entries';
    END IF;
    IF (SELECT count(*) FROM public.horse_details) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:horse_details';
    END IF;
    IF (SELECT count(*) FROM public.horse_pedigree) <> 1805 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:horse_pedigree';
    END IF;
    IF (SELECT count(*) FROM public.jockey_details) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:jockey_details';
    END IF;
    IF (SELECT count(*) FROM public.ml_models) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:ml_models';
    END IF;
    IF (SELECT count(*) FROM public.model_metadata) <> 73 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:model_metadata';
    END IF;
    IF (SELECT count(*) FROM public.ocr_usage) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:ocr_usage';
    END IF;
    IF (SELECT count(*) FROM public.past_performances) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:past_performances';
    END IF;
    IF (SELECT count(*) FROM public.payouts) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:payouts';
    END IF;
    IF (SELECT count(*) FROM public.predictions) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:predictions';
    END IF;
    IF (SELECT count(*) FROM public.profiles) <> 3 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:profiles';
    END IF;
    IF (SELECT count(*) FROM public.purchase_history) <> 4 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:purchase_history';
    END IF;
    IF (SELECT count(*) FROM public.race_lap_times) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:race_lap_times';
    END IF;
    IF (SELECT count(*) FROM public.race_odds) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:race_odds';
    END IF;
    IF (SELECT count(*) FROM public.race_payouts) <> 5847 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:race_payouts';
    END IF;
    IF (SELECT count(*) FROM public.race_results) <> 9588 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:race_results';
    END IF;
    IF (SELECT count(*) FROM public.race_results_ultimate) <> 719 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:race_results_ultimate';
    END IF;
    IF (SELECT count(*) FROM public.races) <> 288 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:races';
    END IF;
    IF (SELECT count(*) FROM public.races_ultimate) <> 72 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:races_ultimate';
    END IF;
    IF (SELECT count(*) FROM public.results) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:results';
    END IF;
    IF (SELECT count(*) FROM public.trainer_details) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:trainer_details';
    END IF;
    IF (SELECT count(*) FROM public.users) <> 1 THEN
        RAISE EXCEPTION 'phase3n-legacy-row-count-changed:users';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT horse_id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM public.horse_pedigree AS t
        ) AS source_rows
    )
       IS DISTINCT FROM '9074ba1d4d4b89c52619cc770ec99a579416b2b9ce818fc855a9cb08d6e2d644' THEN
        RAISE EXCEPTION 'phase3n-legacy-row-digest-changed:horse_pedigree';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT model_id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM public.model_metadata AS t
        ) AS source_rows
    )
       IS DISTINCT FROM 'ad6ecf1c56bc921f7b1f20ca8efcb44fed309f547947a8ac46b3f989a8084359' THEN
        RAISE EXCEPTION 'phase3n-legacy-row-digest-changed:model_metadata';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM public.profiles AS t
        ) AS source_rows
    )
       IS DISTINCT FROM '67b0f8b62d310af3b6fa9d293335f2faa91cb5b5075e097af594a9d864bcaef1' THEN
        RAISE EXCEPTION 'phase3n-legacy-row-digest-changed:profiles';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM public.purchase_history AS t
        ) AS source_rows
    )
       IS DISTINCT FROM 'c7bf6e3b4ee949eb4c569c660285fa64cf1308467b9075d927142cff27244e93' THEN
        RAISE EXCEPTION 'phase3n-legacy-row-digest-changed:purchase_history';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM public.race_payouts AS t
        ) AS source_rows
    )
       IS DISTINCT FROM 'a4ea839ff5bae8c1776edec41df7e647532bf027ea5c4da0463adda595ea460d' THEN
        RAISE EXCEPTION 'phase3n-legacy-row-digest-changed:race_payouts';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM public.race_results AS t
        ) AS source_rows
    )
       IS DISTINCT FROM 'eb48e6dab0d2dd6514fe807dedf18fac494a9c1f33c0aa4b540c66f2588ad019' THEN
        RAISE EXCEPTION 'phase3n-legacy-row-digest-changed:race_results';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM public.race_results_ultimate AS t
        ) AS source_rows
    )
       IS DISTINCT FROM '112d562f8bf89e8e771b23d8b4f80aa8b41f9a798cde01fdb41df21af6af42f2' THEN
        RAISE EXCEPTION 'phase3n-legacy-row-digest-changed:race_results_ultimate';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT race_id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM public.races AS t
        ) AS source_rows
    )
       IS DISTINCT FROM '96aa605a704d04c938b48c43982e55c41c73f0fe6ac4d7c79cb063f8cc48a258' THEN
        RAISE EXCEPTION 'phase3n-legacy-row-digest-changed:races';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT race_id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM public.races_ultimate AS t
        ) AS source_rows
    )
       IS DISTINCT FROM 'a27b2555c7ef8b1f7f5dd7784dd6f1d7b4115737c2225788f1f272ce985671da' THEN
        RAISE EXCEPTION 'phase3n-legacy-row-digest-changed:races_ultimate';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM public.users AS t
        ) AS source_rows
    )
       IS DISTINCT FROM '04b79d98eebf1bd137cdbcae02e950ff6e13dd0d116e1417b3ddbd5e5a9aceaf' THEN
        RAISE EXCEPTION 'phase3n-legacy-row-digest-changed:users';
    END IF;
    IF (SELECT count(*) FROM public.race_payouts AS source
        LEFT JOIN public.profiles AS p ON p.id = source.user_id
        WHERE source.user_id IS NOT NULL AND p.id IS NULL) <> 5847
       OR (SELECT count(DISTINCT source.user_id) FROM public.race_payouts AS source
        LEFT JOIN public.profiles AS p ON p.id = source.user_id
        WHERE source.user_id IS NOT NULL AND p.id IS NULL) <> 1
       OR (SELECT count(*) FROM public.race_payouts AS source
        LEFT JOIN auth.users AS u ON u.id = source.user_id
        WHERE source.user_id IS NOT NULL AND u.id IS NULL) <> 5847 THEN
        RAISE EXCEPTION 'phase3n-legacy-user-reference-contract-changed:race_payouts';
    END IF;
    IF (SELECT count(*) FROM public.race_results AS source
        LEFT JOIN public.profiles AS p ON p.id = source.user_id
        WHERE source.user_id IS NOT NULL AND p.id IS NULL) <> 9588
       OR (SELECT count(DISTINCT source.user_id) FROM public.race_results AS source
        LEFT JOIN public.profiles AS p ON p.id = source.user_id
        WHERE source.user_id IS NOT NULL AND p.id IS NULL) <> 1
       OR (SELECT count(*) FROM public.race_results AS source
        LEFT JOIN auth.users AS u ON u.id = source.user_id
        WHERE source.user_id IS NOT NULL AND u.id IS NULL) <> 9588 THEN
        RAISE EXCEPTION 'phase3n-legacy-user-reference-contract-changed:race_results';
    END IF;
    IF (SELECT count(*) FROM public.races AS source
        LEFT JOIN public.profiles AS p ON p.id = source.user_id
        WHERE source.user_id IS NOT NULL AND p.id IS NULL) <> 288
       OR (SELECT count(DISTINCT source.user_id) FROM public.races AS source
        LEFT JOIN public.profiles AS p ON p.id = source.user_id
        WHERE source.user_id IS NOT NULL AND p.id IS NULL) <> 1
       OR (SELECT count(*) FROM public.races AS source
        LEFT JOIN auth.users AS u ON u.id = source.user_id
        WHERE source.user_id IS NOT NULL AND u.id IS NULL) <> 288 THEN
        RAISE EXCEPTION 'phase3n-legacy-user-reference-contract-changed:races';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.race_results AS rr
        LEFT JOIN public.races AS r ON r.race_id = rr.race_id
        WHERE r.race_id IS NULL
    ) OR EXISTS (
        SELECT 1 FROM public.race_payouts AS rp
        LEFT JOIN public.races AS r ON r.race_id = rp.race_id
        WHERE r.race_id IS NULL
    ) OR EXISTS (
        SELECT 1 FROM public.race_results_ultimate AS rr
        LEFT JOIN public.races_ultimate AS r ON r.race_id = rr.race_id
        WHERE r.race_id IS NULL
    ) THEN
        RAISE EXCEPTION 'phase3n-legacy-orphan-reference-detected';
    END IF;
    IF EXISTS (
        WITH parsed AS (
            SELECT race_id, (data #>> '{}')::JSONB AS data
            FROM public.race_results_ultimate
        )
        SELECT 1 FROM parsed
        WHERE jsonb_typeof(data) <> 'object'
           OR COALESCE(
                NULLIF(btrim(data ->> 'horse_number'), ''),
                NULLIF(btrim(data ->> 'horse_num'), '')
           ) IS NULL
    ) OR EXISTS (
        WITH parsed AS (
            SELECT race_id,
                   COALESCE(
                       NULLIF(btrim(((data #>> '{}')::JSONB) ->> 'horse_number'), ''),
                       NULLIF(btrim(((data #>> '{}')::JSONB) ->> 'horse_num'), '')
                   ) AS horse_number
            FROM public.race_results_ultimate
        )
        SELECT 1 FROM parsed GROUP BY race_id, horse_number HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'phase3n-legacy-ultimate-transform-invalid';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.profiles AS p
        LEFT JOIN auth.users AS u ON u.id = p.id
        WHERE u.id IS NULL OR p.role IS NULL OR p.role NOT IN ('user', 'admin')
           OR p.subscription_tier IS NULL OR p.subscription_tier NOT IN ('free', 'premium')
    ) OR EXISTS (
        SELECT 1 FROM public.purchase_history AS ph
        LEFT JOIN public.profiles AS p ON p.id = ph.user_id
        WHERE p.id IS NULL
    ) THEN
        RAISE EXCEPTION 'phase3n-legacy-identity-contract-invalid';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM storage.buckets WHERE id = 'models' AND name = 'models'
    ) OR (SELECT count(*) FROM storage.objects WHERE bucket_id = 'models') <> 146
       OR EXISTS (
           SELECT 1 FROM public.model_metadata AS m
           LEFT JOIN storage.objects AS o
             ON o.bucket_id = 'models' AND o.name = m.storage_path
           WHERE o.id IS NULL
       ) THEN
        RAISE EXCEPTION 'phase3n-legacy-model-storage-contract-invalid';
    END IF;
END
$phase3n_legacy_preflight$;
-- Archive legacy objects without deleting rows.
CREATE SCHEMA phase3n_legacy_20260816 AUTHORIZATION postgres;
REVOKE ALL ON SCHEMA phase3n_legacy_20260816 FROM PUBLIC, anon, authenticated, service_role;
ALTER VIEW public.ml_training_data SET SCHEMA phase3n_legacy_20260816;
ALTER TABLE public.bank_records SET SCHEMA phase3n_legacy_20260816;
ALTER TABLE public.bets SET SCHEMA phase3n_legacy_20260816;
ALTER TABLE public.entries SET SCHEMA phase3n_legacy_20260816;
ALTER TABLE public.horse_details SET SCHEMA phase3n_legacy_20260816;
ALTER TABLE public.horse_pedigree SET SCHEMA phase3n_legacy_20260816;
ALTER TABLE public.jockey_details SET SCHEMA phase3n_legacy_20260816;
ALTER TABLE public.ml_models SET SCHEMA phase3n_legacy_20260816;
ALTER TABLE public.model_metadata SET SCHEMA phase3n_legacy_20260816;
ALTER TABLE public.ocr_usage SET SCHEMA phase3n_legacy_20260816;
ALTER TABLE public.past_performances SET SCHEMA phase3n_legacy_20260816;
ALTER TABLE public.payouts SET SCHEMA phase3n_legacy_20260816;
ALTER TABLE public.predictions SET SCHEMA phase3n_legacy_20260816;
ALTER TABLE public.profiles SET SCHEMA phase3n_legacy_20260816;
ALTER TABLE public.purchase_history SET SCHEMA phase3n_legacy_20260816;
ALTER TABLE public.race_lap_times SET SCHEMA phase3n_legacy_20260816;
ALTER TABLE public.race_odds SET SCHEMA phase3n_legacy_20260816;
ALTER TABLE public.race_payouts SET SCHEMA phase3n_legacy_20260816;
ALTER TABLE public.race_results SET SCHEMA phase3n_legacy_20260816;
ALTER TABLE public.race_results_ultimate SET SCHEMA phase3n_legacy_20260816;
ALTER TABLE public.races SET SCHEMA phase3n_legacy_20260816;
ALTER TABLE public.races_ultimate SET SCHEMA phase3n_legacy_20260816;
ALTER TABLE public.results SET SCHEMA phase3n_legacy_20260816;
ALTER TABLE public.trainer_details SET SCHEMA phase3n_legacy_20260816;
ALTER TABLE public.users SET SCHEMA phase3n_legacy_20260816;
ALTER FUNCTION public.consume_pred_count(UUID) SET SCHEMA phase3n_legacy_20260816;
ALTER FUNCTION public.handle_new_user() SET SCHEMA phase3n_legacy_20260816;
ALTER FUNCTION public.reset_ocr_counter() SET SCHEMA phase3n_legacy_20260816;
ALTER FUNCTION public.reset_pred_count_if_needed(UUID) SET SCHEMA phase3n_legacy_20260816;
ALTER FUNCTION public.update_updated_at_column() SET SCHEMA phase3n_legacy_20260816;
REVOKE ALL ON ALL TABLES IN SCHEMA phase3n_legacy_20260816 FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA phase3n_legacy_20260816 FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA phase3n_legacy_20260816 FROM PUBLIC, anon, authenticated;
-- canonical migration 20260720141000 (supabase/bootstrap/v1/migrations/20260720141000_core_identity_finance.sql)
-- Phase 3M fresh-project bootstrap: core identity and user finance tables.
--
-- This migration intentionally uses CREATE TABLE without IF NOT EXISTS.  The
-- bootstrap is for an empty Supabase project and must fail rather than accept
-- an unknown, pre-existing shape.

CREATE TABLE public.profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email TEXT NOT NULL UNIQUE,
    full_name TEXT,
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    subscription_tier TEXT NOT NULL DEFAULT 'free'
        CHECK (subscription_tier IN ('free', 'premium')),
    stripe_customer_id TEXT UNIQUE,
    stripe_subscription_id TEXT UNIQUE,
    ocr_monthly_limit INTEGER NOT NULL DEFAULT 10 CHECK (ocr_monthly_limit >= 0),
    ocr_used_this_month INTEGER NOT NULL DEFAULT 0 CHECK (ocr_used_this_month >= 0),
    ocr_reset_date TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE public.predictions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    race_name TEXT NOT NULL,
    race_date DATE NOT NULL,
    horse_data JSONB NOT NULL,
    predicted_results JSONB NOT NULL,
    confidence_score NUMERIC(5, 2),
    bet_type TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT predictions_confidence_range
        CHECK (confidence_score IS NULL OR confidence_score BETWEEN 0 AND 100),
    CONSTRAINT predictions_id_user_key UNIQUE (id, user_id)
);

CREATE TABLE public.bets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    prediction_id UUID,
    race_name TEXT NOT NULL,
    race_date DATE NOT NULL,
    bet_type TEXT NOT NULL,
    bet_amount INTEGER NOT NULL CHECK (bet_amount >= 0),
    odds NUMERIC(10, 2) CHECK (odds IS NULL OR odds >= 0),
    actual_result JSONB,
    payout INTEGER NOT NULL DEFAULT 0 CHECK (payout >= 0),
    profit_loss INTEGER,
    ocr_scanned BOOLEAN NOT NULL DEFAULT FALSE,
    scanned_image_url TEXT,
    race_id_detail TEXT,
    season TEXT,
    venue TEXT,
    combinations JSONB,
    strategy_type TEXT,
    purchase_count INTEGER CHECK (purchase_count IS NULL OR purchase_count >= 0),
    unit_price INTEGER CHECK (unit_price IS NULL OR unit_price >= 0),
    total_cost INTEGER CHECK (total_cost IS NULL OR total_cost >= 0),
    expected_value NUMERIC(12, 4),
    expected_return NUMERIC(12, 4),
    recovery_rate NUMERIC(12, 4),
    is_hit BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT bets_prediction_owner_fk
        FOREIGN KEY (prediction_id, user_id)
        REFERENCES public.predictions(id, user_id) ON DELETE SET NULL (prediction_id)
);

CREATE TABLE public.bank_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE REFERENCES public.profiles(id) ON DELETE CASCADE,
    initial_bank INTEGER NOT NULL DEFAULT 100000 CHECK (initial_bank >= 0),
    current_bank INTEGER NOT NULL DEFAULT 100000 CHECK (current_bank >= 0),
    total_bet INTEGER NOT NULL DEFAULT 0 CHECK (total_bet >= 0),
    total_return INTEGER NOT NULL DEFAULT 0 CHECK (total_return >= 0),
    roi NUMERIC(12, 4) NOT NULL DEFAULT 0,
    recovery_rate NUMERIC(12, 4) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE public.ocr_usage (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    image_url TEXT,
    extracted_text TEXT,
    corrected_data JSONB,
    success BOOLEAN NOT NULL DEFAULT TRUE,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX predictions_user_created_idx
    ON public.predictions (user_id, created_at DESC);
CREATE INDEX bets_user_created_idx
    ON public.bets (user_id, created_at DESC);
CREATE INDEX bets_prediction_idx
    ON public.bets (prediction_id)
    WHERE prediction_id IS NOT NULL;
CREATE INDEX ocr_usage_user_created_idx
    ON public.ocr_usage (user_id, created_at DESC);

CREATE FUNCTION public.phase3m_touch_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER profiles_touch_updated_at
BEFORE UPDATE ON public.profiles
FOR EACH ROW EXECUTE FUNCTION public.phase3m_touch_updated_at();

CREATE TRIGGER bank_records_touch_updated_at
BEFORE UPDATE ON public.bank_records
FOR EACH ROW EXECUTE FUNCTION public.phase3m_touch_updated_at();

CREATE FUNCTION public.phase3m_handle_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.email IS NULL OR btrim(NEW.email) = '' THEN
        RAISE EXCEPTION 'an email address is required to create a profile'
            USING ERRCODE = '23502';
    END IF;

    INSERT INTO public.profiles (id, email, full_name)
    VALUES (
        NEW.id,
        NEW.email,
        LEFT(NULLIF(btrim(NEW.raw_user_meta_data ->> 'full_name'), ''), 200)
    );

    INSERT INTO public.bank_records (user_id)
    VALUES (NEW.id);

    RETURN NEW;
END;
$$;

CREATE TRIGGER on_auth_user_created
AFTER INSERT ON auth.users
FOR EACH ROW EXECUTE FUNCTION public.phase3m_handle_new_user();

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.profiles FORCE ROW LEVEL SECURITY;
ALTER TABLE public.predictions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.predictions FORCE ROW LEVEL SECURITY;
ALTER TABLE public.bets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bets FORCE ROW LEVEL SECURITY;
ALTER TABLE public.bank_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bank_records FORCE ROW LEVEL SECURITY;
ALTER TABLE public.ocr_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ocr_usage FORCE ROW LEVEL SECURITY;

CREATE POLICY profiles_select_own
    ON public.profiles FOR SELECT TO authenticated
    USING ((SELECT auth.uid()) = id);
CREATE POLICY profiles_update_own
    ON public.profiles FOR UPDATE TO authenticated
    USING ((SELECT auth.uid()) = id)
    WITH CHECK ((SELECT auth.uid()) = id);

CREATE POLICY predictions_select_own
    ON public.predictions FOR SELECT TO authenticated
    USING ((SELECT auth.uid()) = user_id);
CREATE POLICY predictions_insert_own
    ON public.predictions FOR INSERT TO authenticated
    WITH CHECK ((SELECT auth.uid()) = user_id);
CREATE POLICY predictions_update_own
    ON public.predictions FOR UPDATE TO authenticated
    USING ((SELECT auth.uid()) = user_id)
    WITH CHECK ((SELECT auth.uid()) = user_id);
CREATE POLICY predictions_delete_own
    ON public.predictions FOR DELETE TO authenticated
    USING ((SELECT auth.uid()) = user_id);

CREATE POLICY bets_select_own
    ON public.bets FOR SELECT TO authenticated
    USING ((SELECT auth.uid()) = user_id);
CREATE POLICY bets_insert_own
    ON public.bets FOR INSERT TO authenticated
    WITH CHECK ((SELECT auth.uid()) = user_id);
CREATE POLICY bets_update_own
    ON public.bets FOR UPDATE TO authenticated
    USING ((SELECT auth.uid()) = user_id)
    WITH CHECK ((SELECT auth.uid()) = user_id);
CREATE POLICY bets_delete_own
    ON public.bets FOR DELETE TO authenticated
    USING ((SELECT auth.uid()) = user_id);

CREATE POLICY bank_records_select_own
    ON public.bank_records FOR SELECT TO authenticated
    USING ((SELECT auth.uid()) = user_id);
CREATE POLICY bank_records_insert_own
    ON public.bank_records FOR INSERT TO authenticated
    WITH CHECK ((SELECT auth.uid()) = user_id);
CREATE POLICY bank_records_update_own
    ON public.bank_records FOR UPDATE TO authenticated
    USING ((SELECT auth.uid()) = user_id)
    WITH CHECK ((SELECT auth.uid()) = user_id);

CREATE POLICY ocr_usage_select_own
    ON public.ocr_usage FOR SELECT TO authenticated
    USING ((SELECT auth.uid()) = user_id);
CREATE POLICY ocr_usage_insert_own
    ON public.ocr_usage FOR INSERT TO authenticated
    WITH CHECK ((SELECT auth.uid()) = user_id);

REVOKE ALL ON TABLE public.profiles FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.predictions FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.bets FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.bank_records FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.ocr_usage FROM PUBLIC, anon, authenticated;

GRANT SELECT (
    id, email, full_name, role, subscription_tier,
    ocr_monthly_limit, ocr_used_this_month, ocr_reset_date,
    created_at, updated_at
) ON public.profiles TO authenticated;
GRANT UPDATE (full_name) ON public.profiles TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.predictions TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.bets TO authenticated;
GRANT SELECT, INSERT, UPDATE ON public.bank_records TO authenticated;
GRANT SELECT, INSERT ON public.ocr_usage TO authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.profiles TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.predictions TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.bets TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.bank_records TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.ocr_usage TO service_role;

REVOKE ALL ON FUNCTION public.phase3m_touch_updated_at() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.phase3m_handle_new_user() FROM PUBLIC, anon, authenticated;
-- canonical migration 20260720141100 (supabase/bootstrap/v1/migrations/20260720141100_prediction_quota.sql)
-- Phase 3M fresh-project bootstrap: monthly prediction quota and atomic RPCs.

ALTER TABLE public.profiles
    ADD COLUMN pred_count_remaining INTEGER NOT NULL DEFAULT 10,
    ADD COLUMN pred_count_reset_at TIMESTAMPTZ NOT NULL
        DEFAULT (date_trunc('month', NOW()) + INTERVAL '1 month'),
    ADD CONSTRAINT profiles_pred_count_remaining_valid
        CHECK (pred_count_remaining = -1 OR pred_count_remaining >= 0);

CREATE FUNCTION public.reset_pred_count_if_needed(p_user_id UUID)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_remaining INTEGER;
    v_reset_at TIMESTAMPTZ;
    v_tier TEXT;
BEGIN
    IF p_user_id IS NULL THEN
        RETURN -999;
    END IF;

    SELECT p.pred_count_remaining, p.pred_count_reset_at, p.subscription_tier
      INTO v_remaining, v_reset_at, v_tier
      FROM public.profiles AS p
     WHERE p.id = p_user_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RETURN -999;
    END IF;

    -- Subscription changes take effect on the next call, rather than waiting
    -- for the next calendar reset.  This also prevents a downgraded account
    -- from retaining the premium sentinel indefinitely.
    IF v_tier = 'premium' THEN
        IF v_remaining <> -1 THEN
            UPDATE public.profiles AS p
               SET pred_count_remaining = -1
             WHERE p.id = p_user_id;
        END IF;
        RETURN -1;
    ELSIF v_remaining = -1 THEN
        v_remaining := 10;
        UPDATE public.profiles AS p
           SET pred_count_remaining = v_remaining,
               pred_count_reset_at = date_trunc('month', NOW()) + INTERVAL '1 month'
         WHERE p.id = p_user_id;
        RETURN v_remaining;
    END IF;

    IF NOW() >= v_reset_at THEN
        v_remaining := 10;
        UPDATE public.profiles AS p
           SET pred_count_remaining = v_remaining,
               pred_count_reset_at = date_trunc('month', NOW()) + INTERVAL '1 month'
         WHERE p.id = p_user_id;
    END IF;

    RETURN v_remaining;
END;
$$;

CREATE FUNCTION public.consume_pred_count_batch(p_user_id UUID, p_units INTEGER)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_remaining INTEGER;
BEGIN
    IF p_user_id IS NULL THEN
        RETURN -999;
    END IF;
    IF p_units IS NULL OR p_units < 1 OR p_units > 100 THEN
        RAISE EXCEPTION 'p_units must be between 1 and 100'
            USING ERRCODE = '22023';
    END IF;

    v_remaining := public.reset_pred_count_if_needed(p_user_id);

    IF v_remaining = -999 THEN
        RETURN -999;
    END IF;
    IF v_remaining = -1 THEN
        RETURN -1;
    END IF;
    IF v_remaining < p_units THEN
        RETURN -999;
    END IF;

    UPDATE public.profiles AS p
       SET pred_count_remaining = p.pred_count_remaining - p_units
     WHERE p.id = p_user_id
     RETURNING p.pred_count_remaining INTO v_remaining;

    RETURN COALESCE(v_remaining, -999);
END;
$$;

CREATE FUNCTION public.consume_pred_count(p_user_id UUID)
RETURNS INTEGER
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT public.consume_pred_count_batch(p_user_id, 1);
$$;

REVOKE ALL ON FUNCTION public.reset_pred_count_if_needed(UUID)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.consume_pred_count_batch(UUID, INTEGER)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.consume_pred_count(UUID)
    FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.reset_pred_count_if_needed(UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.consume_pred_count_batch(UUID, INTEGER) TO service_role;
GRANT EXECUTE ON FUNCTION public.consume_pred_count(UUID) TO service_role;

-- Extend the authenticated read projection without permitting quota mutation.
GRANT SELECT (pred_count_remaining, pred_count_reset_at)
    ON public.profiles TO authenticated;
-- canonical migration 20260720141200 (supabase/bootstrap/v1/migrations/20260720141200_purchase_history.sql)
-- Phase 3M fresh-project bootstrap: per-user purchase history.

CREATE TABLE public.purchase_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    race_id TEXT NOT NULL,
    purchase_date DATE,
    season TEXT,
    venue TEXT,
    bet_type TEXT NOT NULL,
    combinations TEXT,
    strategy_type TEXT,
    purchase_count INTEGER CHECK (purchase_count IS NULL OR purchase_count >= 0),
    unit_price INTEGER CHECK (unit_price IS NULL OR unit_price >= 0),
    total_cost INTEGER CHECK (total_cost IS NULL OR total_cost >= 0),
    expected_value NUMERIC(12, 4),
    expected_return NUMERIC(12, 4),
    actual_return INTEGER NOT NULL DEFAULT 0 CHECK (actual_return >= 0),
    is_hit BOOLEAN NOT NULL DEFAULT FALSE,
    recovery_rate NUMERIC(12, 4) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX purchase_history_user_created_idx
    ON public.purchase_history (user_id, created_at DESC);
CREATE INDEX purchase_history_race_idx
    ON public.purchase_history (race_id);

ALTER TABLE public.purchase_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.purchase_history FORCE ROW LEVEL SECURITY;

CREATE POLICY purchase_history_select_own
    ON public.purchase_history FOR SELECT TO authenticated
    USING ((SELECT auth.uid()) = user_id);
CREATE POLICY purchase_history_insert_own
    ON public.purchase_history FOR INSERT TO authenticated
    WITH CHECK ((SELECT auth.uid()) = user_id);
CREATE POLICY purchase_history_update_own
    ON public.purchase_history FOR UPDATE TO authenticated
    USING ((SELECT auth.uid()) = user_id)
    WITH CHECK ((SELECT auth.uid()) = user_id);
CREATE POLICY purchase_history_delete_own
    ON public.purchase_history FOR DELETE TO authenticated
    USING ((SELECT auth.uid()) = user_id);

REVOKE ALL ON TABLE public.purchase_history FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.purchase_history TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.purchase_history TO service_role;
-- canonical migration 20260720142000 (supabase/bootstrap/v1/migrations/20260720142000_race_domain.sql)
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
-- canonical migration 20260720142100 (supabase/bootstrap/v1/migrations/20260720142100_normalized_ultimate_domain.sql)
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
-- canonical migration 20260720142200 (supabase/bootstrap/v1/migrations/20260720142200_server_ml_storage.sql)
-- Phase 3M fresh-project bootstrap: service-role-only ML/blob persistence.
-- Browser roles receive neither table grants nor storage-object access.

CREATE TABLE IF NOT EXISTS public.races_ultimate (
    race_id TEXT PRIMARY KEY,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT races_ultimate_data_object CHECK (jsonb_typeof(data) = 'object')
);

CREATE TABLE IF NOT EXISTS public.race_results_ultimate (
    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    race_id TEXT NOT NULL REFERENCES public.races_ultimate(race_id) ON DELETE CASCADE,
    horse_number TEXT NOT NULL,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT race_results_ultimate_race_horse_key UNIQUE (race_id, horse_number),
    CONSTRAINT race_results_ultimate_data_object CHECK (jsonb_typeof(data) = 'object')
);

CREATE TABLE IF NOT EXISTS public.model_metadata (
    model_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'shared',
    storage_path TEXT NOT NULL,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT model_metadata_object
        CHECK (metadata IS NULL OR jsonb_typeof(metadata) = 'object')
);

CREATE TABLE IF NOT EXISTS public.horse_pedigree (
    horse_id TEXT PRIMARY KEY,
    sire TEXT,
    dam TEXT,
    damsire TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.ml_models (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_name TEXT NOT NULL,
    model_type TEXT NOT NULL,
    model_data BYTEA,
    accuracy DOUBLE PRECISION,
    precision_score DOUBLE PRECISION,
    recall_score DOUBLE PRECISION,
    f1_score DOUBLE PRECISION,
    feature_importance JSONB,
    training_date TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
    is_active BOOLEAN NOT NULL DEFAULT true
);

CREATE INDEX IF NOT EXISTS idx_race_results_ultimate_race_id
    ON public.race_results_ultimate(race_id);
CREATE INDEX IF NOT EXISTS idx_model_metadata_created_at
    ON public.model_metadata(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ml_models_active
    ON public.ml_models(is_active, training_date DESC);

CREATE OR REPLACE FUNCTION public.phase3m_normalize_ultimate_horse_number()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
BEGIN
    NEW.horse_number := COALESCE(
        NULLIF(btrim(NEW.horse_number), ''),
        NULLIF(btrim(NEW.data ->> 'horse_number'), ''),
        NULLIF(btrim(NEW.data ->> 'horse_num'), '')
    );
    IF NEW.horse_number IS NULL THEN
        RAISE EXCEPTION 'race_results_ultimate horse_number is required'
            USING ERRCODE = '23502';
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.phase3m_normalize_ultimate_horse_number()
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.phase3m_normalize_ultimate_horse_number()
    TO service_role;

DO $$
DECLARE
    target_table TEXT;
    trigger_name TEXT;
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_trigger
        WHERE tgname = 'phase3m_race_results_ultimate_horse_number'
          AND tgrelid = 'public.race_results_ultimate'::regclass
          AND NOT tgisinternal
    ) THEN
        EXECUTE 'CREATE TRIGGER phase3m_race_results_ultimate_horse_number BEFORE INSERT OR UPDATE ON public.race_results_ultimate FOR EACH ROW EXECUTE FUNCTION public.phase3m_normalize_ultimate_horse_number()';
    END IF;

    FOREACH target_table IN ARRAY ARRAY[
        'races_ultimate',
        'race_results_ultimate',
        'model_metadata',
        'horse_pedigree'
    ]
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

ALTER TABLE public.races_ultimate ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.race_results_ultimate ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.model_metadata ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.horse_pedigree ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ml_models ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.races_ultimate FORCE ROW LEVEL SECURITY;
ALTER TABLE public.race_results_ultimate FORCE ROW LEVEL SECURITY;
ALTER TABLE public.model_metadata FORCE ROW LEVEL SECURITY;
ALTER TABLE public.horse_pedigree FORCE ROW LEVEL SECURITY;
ALTER TABLE public.ml_models FORCE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE
    public.races_ultimate,
    public.race_results_ultimate,
    public.model_metadata,
    public.horse_pedigree,
    public.ml_models
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON SEQUENCE public.race_results_ultimate_id_seq
    FROM PUBLIC, anon, authenticated;
GRANT ALL ON TABLE
    public.races_ultimate,
    public.race_results_ultimate,
    public.model_metadata,
    public.horse_pedigree,
    public.ml_models
    TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.race_results_ultimate_id_seq
    TO service_role;

INSERT INTO storage.buckets (
    id,
    name,
    public,
    file_size_limit,
    allowed_mime_types
)
VALUES (
    'models',
    'models',
    false,
    104857600,
    ARRAY['application/octet-stream', 'application/x-python-serialized-object']::TEXT[]
)
ON CONFLICT (id) DO UPDATE
SET name = EXCLUDED.name,
    public = false,
    file_size_limit = EXCLUDED.file_size_limit,
    allowed_mime_types = EXCLUDED.allowed_mime_types;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_policies
        WHERE schemaname = 'storage'
          AND tablename = 'objects'
          AND policyname = 'phase3m_models_browser_deny'
    ) THEN
        EXECUTE $policy$
            CREATE POLICY phase3m_models_browser_deny
            ON storage.objects
            AS RESTRICTIVE
            FOR ALL
            TO anon, authenticated
            USING (bucket_id <> 'models')
            WITH CHECK (bucket_id <> 'models')
        $policy$;
    END IF;
END;
$$;
-- canonical migration 20260720143000 (supabase/migrations/20260718_scrape_uncertainty_review_ledger.sql)
-- ============================================================
-- Phase 3F: server-authoritative scrape uncertainty review ledger
--
-- This migration is intentionally declarative only. Applying it to any
-- environment is a separate, explicitly approved operation.
--
-- Trust boundary:
-- - browsers never access these tables or functions directly;
-- - trusted Next server routes supply an already verified Admin UUID through service_role;
-- - all mutations occur in SECURITY DEFINER RPCs;
-- - an approval is review-only and cannot unlock or execute a scrape.
-- ============================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;

-- Browser sessions may edit only non-authoritative presentation fields. The
-- legacy own-row UPDATE policy remains useful for those fields, but table-wide
-- UPDATE would let a user self-promote role/subscription/quota attributes.
REVOKE UPDATE ON TABLE public.profiles FROM PUBLIC, anon, authenticated;
GRANT UPDATE (full_name, updated_at) ON TABLE public.profiles TO authenticated;

CREATE TABLE IF NOT EXISTS public.scrape_uncertainty_review_requests (
    review_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
    client_request_id UUID NOT NULL,
    failure_kind TEXT NOT NULL CHECK (failure_kind IN ('monitoring', 'client_stop')),
    start_period TEXT NOT NULL CHECK (start_period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    end_period TEXT NOT NULL CHECK (end_period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    force_rescrape BOOLEAN NOT NULL,
    uncertainty_occurred_at TIMESTAMPTZ NOT NULL,
    reason TEXT NOT NULL CHECK (
        char_length(reason) BETWEEN 20 AND 500
        AND reason !~ '[[:cntrl:]]'
    ),
    request_payload_hash TEXT NOT NULL CHECK (request_payload_hash ~ '^[0-9a-f]{64}$'),
    status TEXT NOT NULL DEFAULT 'pending_review' CHECK (
        status IN ('pending_review', 'approved', 'rejected', 'revoked', 'expired')
    ),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    decided_by UUID NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
    decided_at TIMESTAMPTZ NULL,
    decision_reason TEXT NULL CHECK (
        decision_reason IS NULL OR (
            char_length(decision_reason) BETWEEN 20 AND 500
            AND decision_reason !~ '[[:cntrl:]]'
        )
    ),
    approval_scope TEXT NOT NULL DEFAULT 'review_only' CHECK (approval_scope = 'review_only'),
    authoritative BOOLEAN NOT NULL DEFAULT TRUE CHECK (authoritative = TRUE),
    execution_enabled BOOLEAN NOT NULL DEFAULT FALSE CHECK (execution_enabled = FALSE),
    lock_release_allowed BOOLEAN NOT NULL DEFAULT FALSE CHECK (lock_release_allowed = FALSE),
    CHECK (
        (status = 'pending_review'
            AND decided_by IS NULL
            AND decided_at IS NULL
            AND decision_reason IS NULL)
        OR (status IN ('approved', 'rejected', 'revoked')
            AND decided_by IS NOT NULL
            AND decided_at IS NOT NULL
            AND decision_reason IS NOT NULL)
        OR (status = 'expired'
            AND decided_at IS NOT NULL
            AND decision_reason IS NOT NULL)
    ),
    CHECK (start_period <= end_period),
    CHECK (expires_at > requested_at),
    UNIQUE (owner_user_id, client_request_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_scrape_uncertainty_active_payload
    ON public.scrape_uncertainty_review_requests (owner_user_id, request_payload_hash)
    WHERE status IN ('pending_review', 'approved');

CREATE INDEX IF NOT EXISTS idx_scrape_uncertainty_owner_requested
    ON public.scrape_uncertainty_review_requests (owner_user_id, requested_at DESC);

CREATE INDEX IF NOT EXISTS idx_scrape_uncertainty_reviewable
    ON public.scrape_uncertainty_review_requests (status, expires_at, requested_at)
    WHERE status = 'pending_review';

CREATE TABLE IF NOT EXISTS public.scrape_uncertainty_review_events (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    review_id UUID NOT NULL REFERENCES public.scrape_uncertainty_review_requests(review_id) ON DELETE RESTRICT,
    event_seq INTEGER NOT NULL CHECK (event_seq >= 1),
    event_type TEXT NOT NULL CHECK (event_type IN ('created', 'approved', 'rejected', 'revoked', 'expired')),
    actor_user_id UUID NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
    from_status TEXT NULL CHECK (
        from_status IS NULL OR from_status IN ('pending_review', 'approved', 'rejected', 'revoked', 'expired')
    ),
    to_status TEXT NOT NULL CHECK (
        to_status IN ('pending_review', 'approved', 'rejected', 'revoked', 'expired')
    ),
    record_version INTEGER NOT NULL CHECK (record_version >= 1),
    request_payload_hash TEXT NOT NULL CHECK (request_payload_hash ~ '^[0-9a-f]{64}$'),
    reason TEXT NULL CHECK (
        reason IS NULL OR (
            char_length(reason) BETWEEN 20 AND 500
            AND reason !~ '[[:cntrl:]]'
        )
    ),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (review_id, event_seq)
);

ALTER TABLE public.scrape_uncertainty_review_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scrape_uncertainty_review_events ENABLE ROW LEVEL SECURITY;

-- No browser-facing RLS policies are created. Even service_role receives no
-- direct mutation privilege; SECURITY DEFINER functions below are the only
-- mutation boundary.
REVOKE ALL ON TABLE public.scrape_uncertainty_review_requests
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON TABLE public.scrape_uncertainty_review_events
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.scrape_uncertainty_review_events_event_id_seq
    FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON TABLE public.scrape_uncertainty_review_requests TO service_role;
GRANT SELECT ON TABLE public.scrape_uncertainty_review_events TO service_role;

CREATE OR REPLACE FUNCTION public._reject_scrape_uncertainty_event_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RAISE EXCEPTION 'scrape uncertainty review events are append-only'
        USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_scrape_uncertainty_events_immutable
    ON public.scrape_uncertainty_review_events;
CREATE TRIGGER trg_scrape_uncertainty_events_immutable
    BEFORE UPDATE OR DELETE ON public.scrape_uncertainty_review_events
    FOR EACH ROW EXECUTE FUNCTION public._reject_scrape_uncertainty_event_mutation();

CREATE OR REPLACE FUNCTION public._scrape_uncertainty_require_admin(p_actor_user_id UUID)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF p_actor_user_id IS NULL OR NOT EXISTS (
        SELECT 1
        FROM public.profiles AS p
        WHERE p.id = p_actor_user_id
          AND lower(COALESCE(p.role, '')) = 'admin'
    ) THEN
        RAISE EXCEPTION 'admin role required' USING ERRCODE = '42501';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public._scrape_uncertainty_payload_hash(
    p_owner_user_id UUID,
    p_failure_kind TEXT,
    p_start_period TEXT,
    p_end_period TEXT,
    p_force_rescrape BOOLEAN,
    p_uncertainty_occurred_at TIMESTAMPTZ,
    p_reason TEXT,
    p_server_state_unverified BOOLEAN,
    p_no_unlock_or_retry BOOLEAN
)
RETURNS TEXT
LANGUAGE sql
STABLE
STRICT
SET search_path = public, extensions
AS $$
    SELECT encode(
        extensions.digest(
            convert_to(
                concat_ws(
                    '|',
                    'scrape-uncertainty-v1',
                    p_owner_user_id::TEXT,
                    p_failure_kind,
                    p_start_period,
                    p_end_period,
                    CASE WHEN p_force_rescrape THEN '1' ELSE '0' END,
                    to_char(
                        p_uncertainty_occurred_at AT TIME ZONE 'UTC',
                        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                    ),
                    regexp_replace(btrim(p_reason), '[[:space:]]+', ' ', 'g'),
                    CASE WHEN p_server_state_unverified THEN '1' ELSE '0' END,
                    CASE WHEN p_no_unlock_or_retry THEN '1' ELSE '0' END
                ),
                'UTF8'
            ),
            'sha256'
        ),
        'hex'
    );
$$;

CREATE OR REPLACE FUNCTION public._expire_scrape_uncertainty_review_if_needed(p_review_id UUID)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_row public.scrape_uncertainty_review_requests%ROWTYPE;
    v_new_version INTEGER;
BEGIN
    SELECT * INTO v_row
    FROM public.scrape_uncertainty_review_requests AS r
    WHERE r.review_id = p_review_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN;
    END IF;

    IF v_row.status IN ('pending_review', 'approved')
       AND v_row.expires_at <= clock_timestamp() THEN
        v_new_version := v_row.version + 1;
        UPDATE public.scrape_uncertainty_review_requests AS r
        SET status = 'expired',
            version = v_new_version,
            decided_at = COALESCE(r.decided_at, clock_timestamp()),
            decision_reason = COALESCE(r.decision_reason, 'Review validity expired before an executable authorization existed.')
        WHERE r.review_id = p_review_id;

        INSERT INTO public.scrape_uncertainty_review_events (
            review_id, event_seq, event_type, actor_user_id,
            from_status, to_status, record_version, request_payload_hash, reason
        ) VALUES (
            v_row.review_id, v_new_version, 'expired', NULL,
            v_row.status, 'expired', v_new_version, v_row.request_payload_hash,
            'Review validity expired before an executable authorization existed.'
        );
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.create_scrape_uncertainty_review(
    p_actor_user_id UUID,
    p_client_request_id UUID,
    p_failure_kind TEXT,
    p_start_period TEXT,
    p_end_period TEXT,
    p_force_rescrape BOOLEAN,
    p_uncertainty_occurred_at TIMESTAMPTZ,
    p_reason TEXT,
    p_server_state_unverified BOOLEAN,
    p_no_unlock_or_retry BOOLEAN
)
RETURNS TABLE (
    review_id UUID,
    client_request_id UUID,
    status TEXT,
    version INTEGER,
    request_payload_hash TEXT,
    failure_kind TEXT,
    start_period TEXT,
    end_period TEXT,
    force_rescrape BOOLEAN,
    uncertainty_occurred_at TIMESTAMPTZ,
    reason TEXT,
    requested_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    decided_by UUID,
    decided_at TIMESTAMPTZ,
    decision_reason TEXT,
    approval_scope TEXT,
    authoritative BOOLEAN,
    execution_enabled BOOLEAN,
    lock_release_allowed BOOLEAN
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, extensions
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_reason TEXT;
    v_hash TEXT;
    v_existing public.scrape_uncertainty_review_requests%ROWTYPE;
    v_active_id UUID;
    v_review_id UUID;
BEGIN
    PERFORM public._scrape_uncertainty_require_admin(p_actor_user_id);

    v_reason := regexp_replace(btrim(COALESCE(p_reason, '')), '[[:space:]]+', ' ', 'g');
    IF p_client_request_id IS NULL
       OR p_failure_kind IS NULL
       OR p_failure_kind NOT IN ('monitoring', 'client_stop')
       OR p_start_period IS NULL
       OR p_start_period !~ '^[0-9]{4}-(0[1-9]|1[0-2])$'
       OR p_end_period IS NULL
       OR p_end_period !~ '^[0-9]{4}-(0[1-9]|1[0-2])$'
       OR p_start_period > p_end_period
       OR p_force_rescrape IS NULL
       OR p_uncertainty_occurred_at IS NULL
       OR p_uncertainty_occurred_at < v_now - INTERVAL '24 hours'
       OR p_uncertainty_occurred_at > v_now + INTERVAL '5 minutes'
       OR p_reason ~ '[[:cntrl:]]'
       OR char_length(v_reason) NOT BETWEEN 20 AND 500
       OR p_server_state_unverified IS DISTINCT FROM TRUE
       OR p_no_unlock_or_retry IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'invalid scrape uncertainty review request' USING ERRCODE = '22023';
    END IF;

    v_hash := public._scrape_uncertainty_payload_hash(
        p_actor_user_id,
        p_failure_kind,
        p_start_period,
        p_end_period,
        p_force_rescrape,
        p_uncertainty_occurred_at,
        v_reason,
        p_server_state_unverified,
        p_no_unlock_or_retry
    );

    IF v_hash IS NULL THEN
        RAISE EXCEPTION 'invalid scrape uncertainty review request' USING ERRCODE = '22023';
    END IF;

    -- Serialize retries for the owner/client idempotency tuple.
    PERFORM pg_advisory_xact_lock(
        hashtextextended(p_actor_user_id::TEXT || ':' || p_client_request_id::TEXT, 0)
    );

    SELECT * INTO v_existing
    FROM public.scrape_uncertainty_review_requests AS r
    WHERE r.owner_user_id = p_actor_user_id
      AND r.client_request_id = p_client_request_id
    FOR UPDATE;

    IF FOUND THEN
        IF v_existing.request_payload_hash IS DISTINCT FROM v_hash THEN
            RAISE EXCEPTION 'client_request_id payload conflict' USING ERRCODE = '23505';
        END IF;
        v_review_id := v_existing.review_id;
        PERFORM public._expire_scrape_uncertainty_review_if_needed(v_review_id);
    ELSE
        -- Expire an old active record with the same canonical payload before
        -- enforcing the active-payload uniqueness boundary.
        FOR v_active_id IN
            SELECT r.review_id
            FROM public.scrape_uncertainty_review_requests AS r
            WHERE r.owner_user_id = p_actor_user_id
              AND r.request_payload_hash = v_hash
              AND r.status IN ('pending_review', 'approved')
            FOR UPDATE
        LOOP
            PERFORM public._expire_scrape_uncertainty_review_if_needed(v_active_id);
        END LOOP;

        IF EXISTS (
            SELECT 1
            FROM public.scrape_uncertainty_review_requests AS r
            WHERE r.owner_user_id = p_actor_user_id
              AND r.request_payload_hash = v_hash
              AND r.status IN ('pending_review', 'approved')
        ) THEN
            RAISE EXCEPTION 'active review already exists for payload' USING ERRCODE = '23505';
        END IF;

        v_review_id := gen_random_uuid();
        INSERT INTO public.scrape_uncertainty_review_requests (
            review_id, owner_user_id, client_request_id, failure_kind,
            start_period, end_period, force_rescrape, uncertainty_occurred_at,
            reason, request_payload_hash, status, version, requested_at, expires_at,
            approval_scope, authoritative, execution_enabled, lock_release_allowed
        ) VALUES (
            v_review_id, p_actor_user_id, p_client_request_id, p_failure_kind,
            p_start_period, p_end_period, p_force_rescrape, p_uncertainty_occurred_at,
            v_reason, v_hash, 'pending_review', 1, v_now, v_now + INTERVAL '30 minutes',
            'review_only', TRUE, FALSE, FALSE
        );

        INSERT INTO public.scrape_uncertainty_review_events (
            review_id, event_seq, event_type, actor_user_id,
            from_status, to_status, record_version, request_payload_hash, reason
        ) VALUES (
            v_review_id, 1, 'created', p_actor_user_id,
            NULL, 'pending_review', 1, v_hash, v_reason
        );
    END IF;

    RETURN QUERY
    SELECT
        r.review_id, r.client_request_id, r.status, r.version,
        r.request_payload_hash, r.failure_kind, r.start_period, r.end_period,
        r.force_rescrape, r.uncertainty_occurred_at, r.reason,
        r.requested_at, r.expires_at, r.decided_by, r.decided_at,
        r.decision_reason, r.approval_scope, r.authoritative,
        r.execution_enabled, r.lock_release_allowed
    FROM public.scrape_uncertainty_review_requests AS r
    WHERE r.review_id = v_review_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.get_scrape_uncertainty_review(
    p_actor_user_id UUID,
    p_review_id UUID
)
RETURNS TABLE (
    review_id UUID,
    client_request_id UUID,
    status TEXT,
    version INTEGER,
    request_payload_hash TEXT,
    failure_kind TEXT,
    start_period TEXT,
    end_period TEXT,
    force_rescrape BOOLEAN,
    uncertainty_occurred_at TIMESTAMPTZ,
    reason TEXT,
    requested_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    decided_by UUID,
    decided_at TIMESTAMPTZ,
    decision_reason TEXT,
    approval_scope TEXT,
    authoritative BOOLEAN,
    execution_enabled BOOLEAN,
    lock_release_allowed BOOLEAN
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    PERFORM public._scrape_uncertainty_require_admin(p_actor_user_id);
    PERFORM public._expire_scrape_uncertainty_review_if_needed(p_review_id);

    RETURN QUERY
    SELECT
        r.review_id, r.client_request_id, r.status, r.version,
        r.request_payload_hash, r.failure_kind, r.start_period, r.end_period,
        r.force_rescrape, r.uncertainty_occurred_at, r.reason,
        r.requested_at, r.expires_at, r.decided_by, r.decided_at,
        r.decision_reason, r.approval_scope, r.authoritative,
        r.execution_enabled, r.lock_release_allowed
    FROM public.scrape_uncertainty_review_requests AS r
    WHERE r.review_id = p_review_id
      AND (
          r.owner_user_id = p_actor_user_id
          OR r.decided_by = p_actor_user_id
          OR (r.status = 'pending_review' AND r.owner_user_id <> p_actor_user_id)
      );
END;
$$;

CREATE OR REPLACE FUNCTION public.list_scrape_uncertainty_reviews(
    p_actor_user_id UUID,
    p_scope TEXT DEFAULT 'mine',
    p_limit INTEGER DEFAULT 20
)
RETURNS TABLE (
    review_id UUID,
    client_request_id UUID,
    status TEXT,
    version INTEGER,
    request_payload_hash TEXT,
    failure_kind TEXT,
    start_period TEXT,
    end_period TEXT,
    force_rescrape BOOLEAN,
    uncertainty_occurred_at TIMESTAMPTZ,
    reason TEXT,
    requested_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    decided_by UUID,
    decided_at TIMESTAMPTZ,
    decision_reason TEXT,
    approval_scope TEXT,
    authoritative BOOLEAN,
    execution_enabled BOOLEAN,
    lock_release_allowed BOOLEAN
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_review_id UUID;
BEGIN
    PERFORM public._scrape_uncertainty_require_admin(p_actor_user_id);
    IF p_scope IS NULL
       OR p_scope NOT IN ('mine', 'reviewable')
       OR p_limit IS NULL OR p_limit < 1 OR p_limit > 100 THEN
        RAISE EXCEPTION 'invalid review list query' USING ERRCODE = '22023';
    END IF;

    FOR v_review_id IN
        SELECT r.review_id
        FROM public.scrape_uncertainty_review_requests AS r
        WHERE (p_scope = 'mine' AND r.owner_user_id = p_actor_user_id)
           OR (p_scope = 'reviewable' AND r.owner_user_id <> p_actor_user_id AND r.status = 'pending_review')
        ORDER BY r.requested_at DESC
        LIMIT p_limit
    LOOP
        PERFORM public._expire_scrape_uncertainty_review_if_needed(v_review_id);
    END LOOP;

    RETURN QUERY
    SELECT
        r.review_id, r.client_request_id, r.status, r.version,
        r.request_payload_hash, r.failure_kind, r.start_period, r.end_period,
        r.force_rescrape, r.uncertainty_occurred_at, r.reason,
        r.requested_at, r.expires_at, r.decided_by, r.decided_at,
        r.decision_reason, r.approval_scope, r.authoritative,
        r.execution_enabled, r.lock_release_allowed
    FROM public.scrape_uncertainty_review_requests AS r
    WHERE (p_scope = 'mine' AND r.owner_user_id = p_actor_user_id)
       OR (p_scope = 'reviewable'
           AND r.owner_user_id <> p_actor_user_id
           AND r.status = 'pending_review'
           AND r.expires_at > clock_timestamp())
    ORDER BY r.requested_at DESC
    LIMIT p_limit;
END;
$$;

CREATE OR REPLACE FUNCTION public.transition_scrape_uncertainty_review(
    p_actor_user_id UUID,
    p_review_id UUID,
    p_expected_version INTEGER,
    p_action TEXT,
    p_reason TEXT
)
RETURNS TABLE (
    review_id UUID,
    client_request_id UUID,
    status TEXT,
    version INTEGER,
    request_payload_hash TEXT,
    failure_kind TEXT,
    start_period TEXT,
    end_period TEXT,
    force_rescrape BOOLEAN,
    uncertainty_occurred_at TIMESTAMPTZ,
    reason TEXT,
    requested_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    decided_by UUID,
    decided_at TIMESTAMPTZ,
    decision_reason TEXT,
    approval_scope TEXT,
    authoritative BOOLEAN,
    execution_enabled BOOLEAN,
    lock_release_allowed BOOLEAN
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_row public.scrape_uncertainty_review_requests%ROWTYPE;
    v_reason TEXT;
    v_new_status TEXT;
    v_event_type TEXT;
    v_new_version INTEGER;
BEGIN
    PERFORM public._scrape_uncertainty_require_admin(p_actor_user_id);
    v_reason := regexp_replace(btrim(COALESCE(p_reason, '')), '[[:space:]]+', ' ', 'g');
    IF p_action IS NULL
       OR p_action NOT IN ('approve', 'reject', 'revoke')
       OR p_expected_version IS NULL OR p_expected_version < 1
       OR p_reason ~ '[[:cntrl:]]'
       OR char_length(v_reason) NOT BETWEEN 20 AND 500 THEN
        RAISE EXCEPTION 'invalid review transition' USING ERRCODE = '22023';
    END IF;

    PERFORM public._expire_scrape_uncertainty_review_if_needed(p_review_id);
    SELECT * INTO v_row
    FROM public.scrape_uncertainty_review_requests AS r
    WHERE r.review_id = p_review_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'review not found' USING ERRCODE = 'P0002';
    END IF;
    IF p_actor_user_id <> v_row.owner_user_id
       AND v_row.status <> 'pending_review'
       AND v_row.decided_by IS DISTINCT FROM p_actor_user_id THEN
        RAISE EXCEPTION 'review not found' USING ERRCODE = 'P0002';
    END IF;
    IF v_row.version <> p_expected_version THEN
        RAISE EXCEPTION 'review version conflict' USING ERRCODE = '40001';
    END IF;
    IF v_row.status = 'expired' THEN
        RAISE EXCEPTION 'review expired' USING ERRCODE = '55000';
    END IF;

    IF p_action IN ('approve', 'reject') THEN
        IF v_row.status <> 'pending_review' THEN
            RAISE EXCEPTION 'review is not pending' USING ERRCODE = '55000';
        END IF;
        IF p_actor_user_id = v_row.owner_user_id THEN
            RAISE EXCEPTION 'requester cannot approve or reject own review' USING ERRCODE = '42501';
        END IF;
        v_new_status := CASE WHEN p_action = 'approve' THEN 'approved' ELSE 'rejected' END;
        v_event_type := CASE WHEN p_action = 'approve' THEN 'approved' ELSE 'rejected' END;
    ELSE
        IF p_actor_user_id <> v_row.owner_user_id THEN
            RAISE EXCEPTION 'only requester can revoke review' USING ERRCODE = '42501';
        END IF;
        IF v_row.status NOT IN ('pending_review', 'approved') THEN
            RAISE EXCEPTION 'review cannot be revoked from current state' USING ERRCODE = '55000';
        END IF;
        v_new_status := 'revoked';
        v_event_type := 'revoked';
    END IF;

    v_new_version := v_row.version + 1;
    UPDATE public.scrape_uncertainty_review_requests AS r
    SET status = v_new_status,
        version = v_new_version,
        decided_by = p_actor_user_id,
        decided_at = clock_timestamp(),
        decision_reason = v_reason,
        -- Phase 3F approval remains review-only and non-executable.
        approval_scope = 'review_only',
        authoritative = TRUE,
        execution_enabled = FALSE,
        lock_release_allowed = FALSE
    WHERE r.review_id = p_review_id;

    INSERT INTO public.scrape_uncertainty_review_events (
        review_id, event_seq, event_type, actor_user_id,
        from_status, to_status, record_version, request_payload_hash, reason
    ) VALUES (
        v_row.review_id, v_new_version, v_event_type, p_actor_user_id,
        v_row.status, v_new_status, v_new_version, v_row.request_payload_hash, v_reason
    );

    RETURN QUERY
    SELECT
        r.review_id, r.client_request_id, r.status, r.version,
        r.request_payload_hash, r.failure_kind, r.start_period, r.end_period,
        r.force_rescrape, r.uncertainty_occurred_at, r.reason,
        r.requested_at, r.expires_at, r.decided_by, r.decided_at,
        r.decision_reason, r.approval_scope, r.authoritative,
        r.execution_enabled, r.lock_release_allowed
    FROM public.scrape_uncertainty_review_requests AS r
    WHERE r.review_id = p_review_id;
END;
$$;

-- Internal helpers and trigger functions are never callable by API roles.
REVOKE ALL ON FUNCTION public._reject_scrape_uncertainty_event_mutation()
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public._scrape_uncertainty_require_admin(UUID)
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public._scrape_uncertainty_payload_hash(
    UUID, TEXT, TEXT, TEXT, BOOLEAN, TIMESTAMPTZ, TEXT, BOOLEAN, BOOLEAN
)
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public._expire_scrape_uncertainty_review_if_needed(UUID)
    FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION public.create_scrape_uncertainty_review(
    UUID, UUID, TEXT, TEXT, TEXT, BOOLEAN, TIMESTAMPTZ, TEXT, BOOLEAN, BOOLEAN
) FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.get_scrape_uncertainty_review(UUID, UUID)
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.list_scrape_uncertainty_reviews(UUID, TEXT, INTEGER)
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.transition_scrape_uncertainty_review(UUID, UUID, INTEGER, TEXT, TEXT)
    FROM PUBLIC, anon, authenticated, service_role;

GRANT EXECUTE ON FUNCTION public.create_scrape_uncertainty_review(
    UUID, UUID, TEXT, TEXT, TEXT, BOOLEAN, TIMESTAMPTZ, TEXT, BOOLEAN, BOOLEAN
) TO service_role;
GRANT EXECUTE ON FUNCTION public.get_scrape_uncertainty_review(UUID, UUID)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.list_scrape_uncertainty_reviews(UUID, TEXT, INTEGER)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.transition_scrape_uncertainty_review(UUID, UUID, INTEGER, TEXT, TEXT)
    TO service_role;
-- canonical migration 20260720143100 (supabase/migrations/20260720_scrape_execution_reservation.sql)
-- ============================================================
-- Phase 3J: explicit scrape execution authorization and reservation
--
-- Declarative migration only. Applying it to any shared or production
-- environment requires a separate, explicit approval.
--
-- Trust boundary:
-- - an approved uncertainty review is review evidence, never execution authority;
-- - execution authorizations are bootstrap-only rows (there is deliberately no
--   application RPC that creates, updates, or revokes an authorization);
-- - the review owner/requester cannot authorize their own execution;
-- - only service_role may call reservation lifecycle RPCs;
-- - these RPCs reserve, consume, release, or expire a capability. They do not
--   unlock a client, dispatch a worker, or mutate the Phase 3F review ledger.
-- ============================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;

CREATE SEQUENCE IF NOT EXISTS public.scrape_execution_reservation_fencing_seq
    AS BIGINT START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE NO CYCLE;

CREATE TABLE IF NOT EXISTS public.scrape_execution_authorizations (
    authorization_id UUID PRIMARY KEY,
    operation_id UUID NOT NULL UNIQUE,
    job_id UUID NOT NULL UNIQUE,
    review_id UUID NOT NULL REFERENCES public.scrape_uncertainty_review_requests(review_id) ON DELETE RESTRICT,
    review_version INTEGER NOT NULL CHECK (review_version >= 1),
    owner_user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
    authorized_by_user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
    review_payload_hash TEXT NOT NULL CHECK (review_payload_hash ~ '^[0-9a-f]{64}$'),
    execution_request_hash TEXT NOT NULL CHECK (execution_request_hash ~ '^[0-9a-f]{64}$'),
    authorization_binding_hash TEXT NOT NULL CHECK (authorization_binding_hash ~ '^[0-9a-f]{64}$'),
    authorization_version INTEGER NOT NULL DEFAULT 1 CHECK (authorization_version = 1),
    authorization_status TEXT NOT NULL DEFAULT 'authorized' CHECK (authorization_status = 'authorized'),
    authorization_source TEXT NOT NULL DEFAULT 'ci_bootstrap_only' CHECK (authorization_source = 'ci_bootstrap_only'),
    execution_authorized BOOLEAN NOT NULL DEFAULT TRUE CHECK (execution_authorized = TRUE),
    authorized_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    authorization_expires_at TIMESTAMPTZ NOT NULL,
    CHECK (authorized_by_user_id <> owner_user_id),
    CHECK (authorization_expires_at > authorized_at),
    UNIQUE (review_id, review_version)
);

CREATE TABLE IF NOT EXISTS public.scrape_execution_reservations (
    reservation_id UUID PRIMARY KEY,
    authorization_id UUID NOT NULL UNIQUE
        REFERENCES public.scrape_execution_authorizations(authorization_id) ON DELETE RESTRICT,
    operation_id UUID NOT NULL UNIQUE,
    job_id UUID NOT NULL UNIQUE,
    review_id UUID NOT NULL
        REFERENCES public.scrape_uncertainty_review_requests(review_id) ON DELETE RESTRICT,
    review_version INTEGER NOT NULL CHECK (review_version >= 1),
    owner_user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
    execution_request_hash TEXT NOT NULL CHECK (execution_request_hash ~ '^[0-9a-f]{64}$'),
    authorization_binding_hash TEXT NOT NULL CHECK (authorization_binding_hash ~ '^[0-9a-f]{64}$'),
    authorization_version INTEGER NOT NULL CHECK (authorization_version = 1),
    requested_ttl_seconds INTEGER NOT NULL CHECK (requested_ttl_seconds BETWEEN 1 AND 300),
    status TEXT NOT NULL DEFAULT 'reserved' CHECK (
        status IN ('reserved', 'consumed', 'released', 'expired')
    ),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    fencing_token BIGINT NOT NULL UNIQUE CHECK (fencing_token >= 1),
    reserved_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    consume_request_id UUID NULL UNIQUE,
    consume_receipt_hash TEXT NULL UNIQUE CHECK (
        consume_receipt_hash IS NULL OR consume_receipt_hash ~ '^[0-9a-f]{64}$'
    ),
    consumed_at TIMESTAMPTZ NULL,
    release_request_id UUID NULL UNIQUE,
    release_reason TEXT NULL CHECK (
        release_reason IS NULL OR (
            char_length(release_reason) BETWEEN 20 AND 500
            AND release_reason !~ '[[:cntrl:]]'
        )
    ),
    released_at TIMESTAMPTZ NULL,
    expired_at TIMESTAMPTZ NULL,
    CHECK (expires_at > reserved_at),
    CHECK (
        (status = 'reserved'
            AND consume_request_id IS NULL AND consume_receipt_hash IS NULL AND consumed_at IS NULL
            AND release_request_id IS NULL AND release_reason IS NULL AND released_at IS NULL
            AND expired_at IS NULL)
        OR (status = 'consumed'
            AND consume_request_id IS NOT NULL AND consume_receipt_hash IS NOT NULL AND consumed_at IS NOT NULL
            AND release_request_id IS NULL AND release_reason IS NULL AND released_at IS NULL
            AND expired_at IS NULL)
        OR (status = 'released'
            AND consume_request_id IS NULL AND consume_receipt_hash IS NULL AND consumed_at IS NULL
            AND release_request_id IS NOT NULL AND release_reason IS NOT NULL AND released_at IS NOT NULL
            AND expired_at IS NULL)
        OR (status = 'expired'
            AND consume_request_id IS NULL AND consume_receipt_hash IS NULL AND consumed_at IS NULL
            AND release_request_id IS NULL AND release_reason IS NULL AND released_at IS NULL
            AND expired_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_scrape_execution_reservations_status_expiry
    ON public.scrape_execution_reservations (status, expires_at)
    WHERE status = 'reserved';

CREATE TABLE IF NOT EXISTS public.scrape_execution_reservation_events (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reservation_id UUID NOT NULL
        REFERENCES public.scrape_execution_reservations(reservation_id) ON DELETE RESTRICT,
    event_seq INTEGER NOT NULL CHECK (event_seq >= 1),
    event_type TEXT NOT NULL CHECK (event_type IN ('reserved', 'consumed', 'released', 'expired')),
    from_status TEXT NULL CHECK (
        from_status IS NULL OR from_status IN ('reserved', 'consumed', 'released', 'expired')
    ),
    to_status TEXT NOT NULL CHECK (to_status IN ('reserved', 'consumed', 'released', 'expired')),
    record_version INTEGER NOT NULL CHECK (record_version >= 1),
    authorization_binding_hash TEXT NOT NULL CHECK (authorization_binding_hash ~ '^[0-9a-f]{64}$'),
    fencing_token BIGINT NOT NULL CHECK (fencing_token >= 1),
    idempotency_request_id UUID NOT NULL,
    consume_receipt_hash TEXT NULL CHECK (
        consume_receipt_hash IS NULL OR consume_receipt_hash ~ '^[0-9a-f]{64}$'
    ),
    reason TEXT NULL CHECK (
        reason IS NULL OR (
            char_length(reason) BETWEEN 20 AND 500
            AND reason !~ '[[:cntrl:]]'
        )
    ),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (reservation_id, event_seq)
);

ALTER TABLE public.scrape_execution_authorizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scrape_execution_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scrape_execution_reservation_events ENABLE ROW LEVEL SECURITY;

-- No browser policy exists. service_role can inspect server state, but all
-- lifecycle mutations pass through the fixed RPC surface below.
REVOKE ALL ON TABLE public.scrape_execution_authorizations
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON TABLE public.scrape_execution_reservations
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON TABLE public.scrape_execution_reservation_events
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.scrape_execution_reservation_fencing_seq
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.scrape_execution_reservation_events_event_id_seq
    FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON TABLE public.scrape_execution_authorizations TO service_role;
GRANT SELECT ON TABLE public.scrape_execution_reservations TO service_role;
GRANT SELECT ON TABLE public.scrape_execution_reservation_events TO service_role;

CREATE OR REPLACE FUNCTION public._scrape_execution_binding_hash(
    p_operation_id UUID,
    p_job_id UUID,
    p_review_id UUID,
    p_review_version INTEGER,
    p_owner_user_id UUID,
    p_execution_request_hash TEXT
)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
STRICT
SET search_path = public, extensions
AS $$
    SELECT encode(
        extensions.digest(
            convert_to(
                concat_ws(
                    '|',
                    'scrape-execution-binding-v1',
                    p_operation_id::TEXT,
                    p_job_id::TEXT,
                    p_review_id::TEXT,
                    p_review_version::TEXT,
                    p_owner_user_id::TEXT,
                    p_execution_request_hash
                ),
                'UTF8'
            ),
            'sha256'
        ),
        'hex'
    );
$$;

CREATE OR REPLACE FUNCTION public._validate_scrape_execution_authorization_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, extensions
AS $$
DECLARE
    v_review public.scrape_uncertainty_review_requests%ROWTYPE;
    v_binding_hash TEXT;
BEGIN
    IF NEW.authorized_by_user_id = NEW.owner_user_id
       OR NOT EXISTS (
            SELECT 1 FROM public.profiles AS p
            WHERE p.id = NEW.authorized_by_user_id
              AND lower(COALESCE(p.role, '')) = 'admin'
       ) THEN
        RAISE EXCEPTION 'requester cannot self-authorize execution'
            USING ERRCODE = '42501';
    END IF;

    SELECT * INTO v_review
    FROM public.scrape_uncertainty_review_requests AS r
    WHERE r.review_id = NEW.review_id
    FOR SHARE;

    IF NOT FOUND
       OR v_review.status <> 'approved'
       OR v_review.version <> NEW.review_version
       OR v_review.owner_user_id <> NEW.owner_user_id
       OR v_review.request_payload_hash <> NEW.review_payload_hash
       OR v_review.decided_by IS DISTINCT FROM NEW.authorized_by_user_id
       OR v_review.expires_at <= clock_timestamp()
       OR v_review.approval_scope <> 'review_only'
       OR v_review.authoritative IS DISTINCT FROM TRUE
       OR v_review.execution_enabled IS DISTINCT FROM FALSE
       OR v_review.lock_release_allowed IS DISTINCT FROM FALSE THEN
        RAISE EXCEPTION 'approved review binding required for explicit execution authorization'
            USING ERRCODE = '42501';
    END IF;

    IF NEW.authorization_source <> 'ci_bootstrap_only'
       OR NEW.authorization_status <> 'authorized'
       OR NEW.execution_authorized IS DISTINCT FROM TRUE
       OR NEW.authorization_version <> 1
       OR NEW.authorization_expires_at <= clock_timestamp()
       OR NEW.authorization_expires_at > v_review.expires_at
       OR NEW.execution_request_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'invalid explicit execution authorization'
            USING ERRCODE = '22023';
    END IF;

    v_binding_hash := public._scrape_execution_binding_hash(
        NEW.operation_id,
        NEW.job_id,
        NEW.review_id,
        NEW.review_version,
        NEW.owner_user_id,
        NEW.execution_request_hash
    );
    IF v_binding_hash IS NULL THEN
        RAISE EXCEPTION 'invalid explicit execution authorization'
            USING ERRCODE = '22023';
    END IF;
    NEW.authorization_binding_hash := v_binding_hash;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public._reject_scrape_execution_authorization_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RAISE EXCEPTION 'scrape execution authorizations are immutable'
        USING ERRCODE = '55000';
END;
$$;

CREATE OR REPLACE FUNCTION public._reject_scrape_execution_reservation_event_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RAISE EXCEPTION 'scrape execution reservation events are append-only'
        USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_scrape_execution_authorization_validate
    ON public.scrape_execution_authorizations;
CREATE TRIGGER trg_scrape_execution_authorization_validate
    BEFORE INSERT ON public.scrape_execution_authorizations
    FOR EACH ROW EXECUTE FUNCTION public._validate_scrape_execution_authorization_insert();

DROP TRIGGER IF EXISTS trg_scrape_execution_authorizations_immutable
    ON public.scrape_execution_authorizations;
CREATE TRIGGER trg_scrape_execution_authorizations_immutable
    BEFORE UPDATE OR DELETE ON public.scrape_execution_authorizations
    FOR EACH ROW EXECUTE FUNCTION public._reject_scrape_execution_authorization_mutation();

DROP TRIGGER IF EXISTS trg_scrape_execution_reservation_events_immutable
    ON public.scrape_execution_reservation_events;
CREATE TRIGGER trg_scrape_execution_reservation_events_immutable
    BEFORE UPDATE OR DELETE ON public.scrape_execution_reservation_events
    FOR EACH ROW EXECUTE FUNCTION public._reject_scrape_execution_reservation_event_mutation();

CREATE OR REPLACE FUNCTION public._materialize_scrape_execution_reservation_expiry(
    p_reservation_id UUID
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_row public.scrape_execution_reservations%ROWTYPE;
BEGIN
    SELECT * INTO v_row
    FROM public.scrape_execution_reservations AS r
    WHERE r.reservation_id = p_reservation_id
    FOR UPDATE;

    IF NOT FOUND OR v_row.status <> 'reserved' OR v_row.expires_at > clock_timestamp() THEN
        RETURN FALSE;
    END IF;

    UPDATE public.scrape_execution_reservations AS r
    SET status = 'expired',
        version = r.version + 1,
        expired_at = clock_timestamp()
    WHERE r.reservation_id = p_reservation_id
    RETURNING * INTO v_row;

    INSERT INTO public.scrape_execution_reservation_events (
        reservation_id, event_seq, event_type, from_status, to_status,
        record_version, authorization_binding_hash, fencing_token,
        idempotency_request_id, reason
    ) VALUES (
        v_row.reservation_id, v_row.version, 'expired', 'reserved', 'expired',
        v_row.version, v_row.authorization_binding_hash, v_row.fencing_token,
        v_row.reservation_id,
        'Reservation expired before a single-use consume receipt was issued.'
    );
    RETURN TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION public.reserve_scrape_execution(
    p_authorization_id UUID,
    p_reservation_id UUID,
    p_operation_id UUID,
    p_job_id UUID,
    p_review_id UUID,
    p_review_version INTEGER,
    p_owner_user_id UUID,
    p_execution_request_hash TEXT,
    p_expected_authorization_version INTEGER,
    p_ttl_seconds INTEGER
)
RETURNS TABLE (
    reservation_id UUID,
    authorization_id UUID,
    operation_id UUID,
    job_id UUID,
    review_id UUID,
    review_version INTEGER,
    owner_user_id UUID,
    execution_request_hash TEXT,
    status TEXT,
    version INTEGER,
    fencing_token BIGINT,
    reserved_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    consume_receipt_hash TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, extensions
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_authorization public.scrape_execution_authorizations%ROWTYPE;
    v_review public.scrape_uncertainty_review_requests%ROWTYPE;
    v_existing public.scrape_execution_reservations%ROWTYPE;
    v_binding_hash TEXT;
    v_expires_at TIMESTAMPTZ;
    v_fencing_token BIGINT;
BEGIN
    IF p_authorization_id IS NULL OR p_reservation_id IS NULL
       OR p_operation_id IS NULL OR p_job_id IS NULL OR p_review_id IS NULL
       OR p_review_version IS NULL OR p_review_version < 1
       OR p_owner_user_id IS NULL
       OR p_execution_request_hash IS NULL
       OR p_execution_request_hash !~ '^[0-9a-f]{64}$'
       OR p_expected_authorization_version IS NULL OR p_expected_authorization_version < 1
       OR p_ttl_seconds IS NULL OR p_ttl_seconds NOT BETWEEN 1 AND 300 THEN
        RAISE EXCEPTION 'invalid execution reservation request' USING ERRCODE = '22023';
    END IF;

    v_binding_hash := public._scrape_execution_binding_hash(
        p_operation_id, p_job_id, p_review_id, p_review_version,
        p_owner_user_id, p_execution_request_hash
    );

    PERFORM pg_advisory_xact_lock(hashtextextended(p_authorization_id::TEXT, 0));

    SELECT * INTO v_existing
    FROM public.scrape_execution_reservations AS r
    WHERE r.reservation_id = p_reservation_id
    FOR UPDATE;

    IF FOUND THEN
        IF v_existing.authorization_id <> p_authorization_id
           OR v_existing.operation_id <> p_operation_id
           OR v_existing.job_id <> p_job_id
           OR v_existing.review_id <> p_review_id
           OR v_existing.review_version <> p_review_version
           OR v_existing.owner_user_id <> p_owner_user_id
           OR v_existing.execution_request_hash <> p_execution_request_hash
           OR v_existing.authorization_version <> p_expected_authorization_version
           OR v_existing.requested_ttl_seconds <> p_ttl_seconds
           OR v_existing.authorization_binding_hash <> v_binding_hash THEN
            RAISE EXCEPTION 'reservation id payload conflict' USING ERRCODE = '23505';
        END IF;
        RETURN QUERY
        SELECT r.reservation_id, r.authorization_id, r.operation_id, r.job_id,
               r.review_id, r.review_version, r.owner_user_id, r.execution_request_hash,
               r.status, r.version, r.fencing_token, r.reserved_at, r.expires_at,
               r.consume_receipt_hash
        FROM public.scrape_execution_reservations AS r
        WHERE r.reservation_id = p_reservation_id;
        RETURN;
    END IF;

    SELECT * INTO v_authorization
    FROM public.scrape_execution_authorizations AS a
    WHERE a.authorization_id = p_authorization_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'explicit execution authorization not found' USING ERRCODE = 'P0002';
    END IF;
    IF v_authorization.authorization_version <> p_expected_authorization_version THEN
        RAISE EXCEPTION 'authorization version conflict' USING ERRCODE = '40001';
    END IF;
    IF v_authorization.authorization_status <> 'authorized'
       OR v_authorization.execution_authorized IS DISTINCT FROM TRUE
       OR v_authorization.authorization_source <> 'ci_bootstrap_only'
       OR v_authorization.authorization_expires_at <= v_now
       OR v_authorization.operation_id <> p_operation_id
       OR v_authorization.job_id <> p_job_id
       OR v_authorization.review_id <> p_review_id
       OR v_authorization.review_version <> p_review_version
       OR v_authorization.owner_user_id <> p_owner_user_id
       OR v_authorization.authorized_by_user_id = p_owner_user_id
       OR v_authorization.execution_request_hash <> p_execution_request_hash
       OR v_authorization.authorization_binding_hash <> v_binding_hash THEN
        RAISE EXCEPTION 'execution authorization binding rejected' USING ERRCODE = '42501';
    END IF;

    SELECT * INTO v_review
    FROM public.scrape_uncertainty_review_requests AS r
    WHERE r.review_id = p_review_id
    FOR SHARE;
    IF NOT FOUND
       OR v_review.status <> 'approved'
       OR v_review.version <> p_review_version
       OR v_review.owner_user_id <> p_owner_user_id
       OR v_review.request_payload_hash <> v_authorization.review_payload_hash
       OR v_review.decided_by IS DISTINCT FROM v_authorization.authorized_by_user_id
       OR v_review.expires_at <= v_now
       OR v_review.execution_enabled IS DISTINCT FROM FALSE
       OR v_review.lock_release_allowed IS DISTINCT FROM FALSE THEN
        RAISE EXCEPTION 'approved review no longer matches authorization' USING ERRCODE = '42501';
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.scrape_execution_reservations AS r
        WHERE r.authorization_id = p_authorization_id
           OR r.operation_id = p_operation_id
           OR r.job_id = p_job_id
    ) THEN
        RAISE EXCEPTION 'authorization or execution binding already reserved' USING ERRCODE = '23505';
    END IF;

    v_expires_at := LEAST(
        v_now + make_interval(secs => p_ttl_seconds),
        v_authorization.authorization_expires_at,
        v_review.expires_at
    );
    IF v_expires_at <= v_now THEN
        RAISE EXCEPTION 'execution authorization expired' USING ERRCODE = '55000';
    END IF;
    v_fencing_token := nextval('public.scrape_execution_reservation_fencing_seq'::regclass);

    INSERT INTO public.scrape_execution_reservations (
        reservation_id, authorization_id, operation_id, job_id, review_id,
        review_version, owner_user_id, execution_request_hash,
        authorization_binding_hash, authorization_version, requested_ttl_seconds,
        status, version, fencing_token, reserved_at, expires_at
    ) VALUES (
        p_reservation_id, p_authorization_id, p_operation_id, p_job_id, p_review_id,
        p_review_version, p_owner_user_id, p_execution_request_hash,
        v_binding_hash, p_expected_authorization_version, p_ttl_seconds,
        'reserved', 1, v_fencing_token, v_now, v_expires_at
    );

    INSERT INTO public.scrape_execution_reservation_events (
        reservation_id, event_seq, event_type, from_status, to_status,
        record_version, authorization_binding_hash, fencing_token,
        idempotency_request_id, reason
    ) VALUES (
        p_reservation_id, 1, 'reserved', NULL, 'reserved', 1,
        v_binding_hash, v_fencing_token, p_reservation_id,
        'Explicit execution authorization reserved for a single consume attempt.'
    );

    RETURN QUERY
    SELECT r.reservation_id, r.authorization_id, r.operation_id, r.job_id,
           r.review_id, r.review_version, r.owner_user_id, r.execution_request_hash,
           r.status, r.version, r.fencing_token, r.reserved_at, r.expires_at,
           r.consume_receipt_hash
    FROM public.scrape_execution_reservations AS r
    WHERE r.reservation_id = p_reservation_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.consume_scrape_execution_reservation(
    p_reservation_id UUID,
    p_expected_version INTEGER,
    p_consume_request_id UUID,
    p_operation_id UUID,
    p_job_id UUID,
    p_review_id UUID,
    p_review_version INTEGER,
    p_owner_user_id UUID,
    p_execution_request_hash TEXT
)
RETURNS TABLE (
    reservation_id UUID,
    status TEXT,
    version INTEGER,
    fencing_token BIGINT,
    expires_at TIMESTAMPTZ,
    consume_request_id UUID,
    consume_receipt_hash TEXT,
    consumed_at TIMESTAMPTZ
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, extensions
AS $$
DECLARE
    v_row public.scrape_execution_reservations%ROWTYPE;
    v_authorization public.scrape_execution_authorizations%ROWTYPE;
    v_review public.scrape_uncertainty_review_requests%ROWTYPE;
    v_binding_hash TEXT;
    v_receipt TEXT;
BEGIN
    IF p_reservation_id IS NULL OR p_consume_request_id IS NULL
       OR p_expected_version IS NULL OR p_expected_version < 1
       OR p_operation_id IS NULL OR p_job_id IS NULL OR p_review_id IS NULL
       OR p_review_version IS NULL OR p_review_version < 1 OR p_owner_user_id IS NULL
       OR p_execution_request_hash IS NULL
       OR p_execution_request_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'invalid consume request' USING ERRCODE = '22023';
    END IF;

    v_binding_hash := public._scrape_execution_binding_hash(
        p_operation_id, p_job_id, p_review_id, p_review_version,
        p_owner_user_id, p_execution_request_hash
    );
    SELECT * INTO v_row
    FROM public.scrape_execution_reservations AS r
    WHERE r.reservation_id = p_reservation_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'reservation not found' USING ERRCODE = 'P0002';
    END IF;

    IF v_row.operation_id <> p_operation_id
       OR v_row.job_id <> p_job_id
       OR v_row.review_id <> p_review_id
       OR v_row.review_version <> p_review_version
       OR v_row.owner_user_id <> p_owner_user_id
       OR v_row.execution_request_hash <> p_execution_request_hash
       OR v_row.authorization_binding_hash <> v_binding_hash THEN
        RAISE EXCEPTION 'consume payload binding conflict' USING ERRCODE = '23505';
    END IF;

    IF v_row.status = 'consumed' THEN
        IF v_row.consume_request_id <> p_consume_request_id THEN
            RAISE EXCEPTION 'reservation already consumed by another request' USING ERRCODE = '23505';
        END IF;
        RETURN QUERY
        SELECT r.reservation_id, r.status, r.version, r.fencing_token,
               r.expires_at, r.consume_request_id, r.consume_receipt_hash, r.consumed_at
        FROM public.scrape_execution_reservations AS r
        WHERE r.reservation_id = p_reservation_id;
        RETURN;
    END IF;

    IF v_row.status <> 'reserved' THEN
        RAISE EXCEPTION 'reservation is not consumable' USING ERRCODE = '55000';
    END IF;
    IF v_row.version <> p_expected_version THEN
        RAISE EXCEPTION 'reservation version conflict' USING ERRCODE = '40001';
    END IF;

    IF v_row.expires_at <= clock_timestamp() THEN
        PERFORM public._materialize_scrape_execution_reservation_expiry(p_reservation_id);
        RETURN QUERY
        SELECT r.reservation_id, r.status, r.version, r.fencing_token,
               r.expires_at, r.consume_request_id, r.consume_receipt_hash, r.consumed_at
        FROM public.scrape_execution_reservations AS r
        WHERE r.reservation_id = p_reservation_id;
        RETURN;
    END IF;

    SELECT * INTO v_authorization
    FROM public.scrape_execution_authorizations AS a
    WHERE a.authorization_id = v_row.authorization_id
    FOR SHARE;
    SELECT * INTO v_review
    FROM public.scrape_uncertainty_review_requests AS r
    WHERE r.review_id = v_row.review_id
    FOR SHARE;
    IF v_authorization.authorization_expires_at <= clock_timestamp()
       OR v_authorization.authorization_binding_hash <> v_row.authorization_binding_hash
       OR v_review.status <> 'approved'
       OR v_review.version <> v_row.review_version
       OR v_review.owner_user_id <> v_row.owner_user_id
       OR v_review.decided_by IS DISTINCT FROM v_authorization.authorized_by_user_id
       OR v_review.expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'authorization is no longer consumable' USING ERRCODE = '42501';
    END IF;

    v_receipt := encode(
        extensions.digest(
            convert_to(
                concat_ws(
                    '|', 'scrape-consume-receipt-v1', p_reservation_id::TEXT,
                    p_consume_request_id::TEXT, v_row.authorization_binding_hash,
                    v_row.fencing_token::TEXT, gen_random_uuid()::TEXT
                ), 'UTF8'
            ), 'sha256'
        ), 'hex'
    );

    UPDATE public.scrape_execution_reservations AS r
    SET status = 'consumed', version = r.version + 1,
        consume_request_id = p_consume_request_id,
        consume_receipt_hash = v_receipt,
        consumed_at = clock_timestamp()
    WHERE r.reservation_id = p_reservation_id
    RETURNING * INTO v_row;

    INSERT INTO public.scrape_execution_reservation_events (
        reservation_id, event_seq, event_type, from_status, to_status,
        record_version, authorization_binding_hash, fencing_token,
        idempotency_request_id, consume_receipt_hash, reason
    ) VALUES (
        v_row.reservation_id, v_row.version, 'consumed', 'reserved', 'consumed',
        v_row.version, v_row.authorization_binding_hash, v_row.fencing_token,
        p_consume_request_id, v_receipt,
        'Reservation consumed once; receipt confirms execution authority consumption only.'
    );

    RETURN QUERY
    SELECT r.reservation_id, r.status, r.version, r.fencing_token,
           r.expires_at, r.consume_request_id, r.consume_receipt_hash, r.consumed_at
    FROM public.scrape_execution_reservations AS r
    WHERE r.reservation_id = p_reservation_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.release_scrape_execution_reservation(
    p_reservation_id UUID,
    p_expected_version INTEGER,
    p_release_request_id UUID,
    p_operation_id UUID,
    p_job_id UUID,
    p_execution_request_hash TEXT,
    p_reason TEXT
)
RETURNS TABLE (
    reservation_id UUID,
    status TEXT,
    version INTEGER,
    fencing_token BIGINT,
    release_request_id UUID,
    release_reason TEXT,
    released_at TIMESTAMPTZ
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_row public.scrape_execution_reservations%ROWTYPE;
    v_reason TEXT;
BEGIN
    v_reason := regexp_replace(btrim(COALESCE(p_reason, '')), '[[:space:]]+', ' ', 'g');
    IF p_reservation_id IS NULL OR p_release_request_id IS NULL
       OR p_expected_version IS NULL OR p_expected_version < 1
       OR p_operation_id IS NULL OR p_job_id IS NULL
       OR p_execution_request_hash IS NULL OR p_execution_request_hash !~ '^[0-9a-f]{64}$'
       OR p_reason ~ '[[:cntrl:]]' OR char_length(v_reason) NOT BETWEEN 20 AND 500 THEN
        RAISE EXCEPTION 'invalid release request' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_row
    FROM public.scrape_execution_reservations AS r
    WHERE r.reservation_id = p_reservation_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'reservation not found' USING ERRCODE = 'P0002';
    END IF;
    IF v_row.operation_id <> p_operation_id
       OR v_row.job_id <> p_job_id
       OR v_row.execution_request_hash <> p_execution_request_hash THEN
        RAISE EXCEPTION 'release payload binding conflict' USING ERRCODE = '23505';
    END IF;

    IF v_row.status = 'released' THEN
        IF v_row.release_request_id <> p_release_request_id
           OR v_row.release_reason <> v_reason THEN
            RAISE EXCEPTION 'reservation release payload conflict' USING ERRCODE = '23505';
        END IF;
        RETURN QUERY
        SELECT r.reservation_id, r.status, r.version, r.fencing_token,
               r.release_request_id, r.release_reason, r.released_at
        FROM public.scrape_execution_reservations AS r
        WHERE r.reservation_id = p_reservation_id;
        RETURN;
    END IF;
    IF v_row.status <> 'reserved' THEN
        RAISE EXCEPTION 'reservation is not releasable' USING ERRCODE = '55000';
    END IF;
    IF v_row.version <> p_expected_version THEN
        RAISE EXCEPTION 'reservation version conflict' USING ERRCODE = '40001';
    END IF;
    IF v_row.expires_at <= clock_timestamp() THEN
        PERFORM public._materialize_scrape_execution_reservation_expiry(p_reservation_id);
        RETURN QUERY
        SELECT r.reservation_id, r.status, r.version, r.fencing_token,
               r.release_request_id, r.release_reason, r.released_at
        FROM public.scrape_execution_reservations AS r
        WHERE r.reservation_id = p_reservation_id;
        RETURN;
    END IF;

    UPDATE public.scrape_execution_reservations AS r
    SET status = 'released', version = r.version + 1,
        release_request_id = p_release_request_id,
        release_reason = v_reason,
        released_at = clock_timestamp()
    WHERE r.reservation_id = p_reservation_id
    RETURNING * INTO v_row;

    INSERT INTO public.scrape_execution_reservation_events (
        reservation_id, event_seq, event_type, from_status, to_status,
        record_version, authorization_binding_hash, fencing_token,
        idempotency_request_id, reason
    ) VALUES (
        v_row.reservation_id, v_row.version, 'released', 'reserved', 'released',
        v_row.version, v_row.authorization_binding_hash, v_row.fencing_token,
        p_release_request_id, v_reason
    );

    RETURN QUERY
    SELECT r.reservation_id, r.status, r.version, r.fencing_token,
           r.release_request_id, r.release_reason, r.released_at
    FROM public.scrape_execution_reservations AS r
    WHERE r.reservation_id = p_reservation_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.expire_scrape_execution_reservation(
    p_reservation_id UUID,
    p_expected_version INTEGER
)
RETURNS TABLE (
    reservation_id UUID,
    status TEXT,
    version INTEGER,
    fencing_token BIGINT,
    expired_at TIMESTAMPTZ
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_row public.scrape_execution_reservations%ROWTYPE;
BEGIN
    IF p_reservation_id IS NULL OR p_expected_version IS NULL OR p_expected_version < 1 THEN
        RAISE EXCEPTION 'invalid expiry request' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_row
    FROM public.scrape_execution_reservations AS r
    WHERE r.reservation_id = p_reservation_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'reservation not found' USING ERRCODE = 'P0002';
    END IF;
    IF v_row.status = 'expired' THEN
        RETURN QUERY
        SELECT r.reservation_id, r.status, r.version, r.fencing_token, r.expired_at
        FROM public.scrape_execution_reservations AS r
        WHERE r.reservation_id = p_reservation_id;
        RETURN;
    END IF;
    IF v_row.status <> 'reserved' THEN
        RAISE EXCEPTION 'reservation is not expirable' USING ERRCODE = '55000';
    END IF;
    IF v_row.version <> p_expected_version THEN
        RAISE EXCEPTION 'reservation version conflict' USING ERRCODE = '40001';
    END IF;
    IF v_row.expires_at > clock_timestamp() THEN
        RAISE EXCEPTION 'reservation has not expired' USING ERRCODE = '55000';
    END IF;
    PERFORM public._materialize_scrape_execution_reservation_expiry(p_reservation_id);
    RETURN QUERY
    SELECT r.reservation_id, r.status, r.version, r.fencing_token, r.expired_at
    FROM public.scrape_execution_reservations AS r
    WHERE r.reservation_id = p_reservation_id;
END;
$$;

-- Remove PostgreSQL's default PUBLIC EXECUTE from every helper and RPC.
REVOKE ALL ON FUNCTION public._scrape_execution_binding_hash(UUID, UUID, UUID, INTEGER, UUID, TEXT)
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public._validate_scrape_execution_authorization_insert()
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public._reject_scrape_execution_authorization_mutation()
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public._reject_scrape_execution_reservation_event_mutation()
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public._materialize_scrape_execution_reservation_expiry(UUID)
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.reserve_scrape_execution(UUID, UUID, UUID, UUID, UUID, INTEGER, UUID, TEXT, INTEGER, INTEGER)
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.consume_scrape_execution_reservation(UUID, INTEGER, UUID, UUID, UUID, UUID, INTEGER, UUID, TEXT)
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.release_scrape_execution_reservation(UUID, INTEGER, UUID, UUID, UUID, TEXT, TEXT)
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.expire_scrape_execution_reservation(UUID, INTEGER)
    FROM PUBLIC, anon, authenticated, service_role;

GRANT EXECUTE ON FUNCTION public.reserve_scrape_execution(UUID, UUID, UUID, UUID, UUID, INTEGER, UUID, TEXT, INTEGER, INTEGER)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.consume_scrape_execution_reservation(UUID, INTEGER, UUID, UUID, UUID, UUID, INTEGER, UUID, TEXT)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.release_scrape_execution_reservation(UUID, INTEGER, UUID, UUID, UUID, TEXT, TEXT)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.expire_scrape_execution_reservation(UUID, INTEGER)
    TO service_role;
-- canonical migration 20260720143200 (supabase/bootstrap/v1/migrations/20260720143200_security_definer_search_path.sql)
-- Phase 3M fresh-project bootstrap: final fail-closed grants and function
-- search-path normalization after every domain and reconciled migration.

REVOKE ALL ON SCHEMA public FROM PUBLIC, anon, authenticated;
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC, anon, authenticated;

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.profiles FORCE ROW LEVEL SECURITY;
ALTER TABLE public.predictions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.predictions FORCE ROW LEVEL SECURITY;
ALTER TABLE public.bets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bets FORCE ROW LEVEL SECURITY;
ALTER TABLE public.bank_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bank_records FORCE ROW LEVEL SECURITY;
ALTER TABLE public.ocr_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ocr_usage FORCE ROW LEVEL SECURITY;
ALTER TABLE public.purchase_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.purchase_history FORCE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.profiles FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.predictions FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.bets FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.bank_records FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.ocr_usage FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.purchase_history FROM PUBLIC, anon, authenticated;

GRANT SELECT (
    id, email, full_name, role, subscription_tier,
    ocr_monthly_limit, ocr_used_this_month, ocr_reset_date,
    pred_count_remaining, pred_count_reset_at,
    created_at, updated_at
) ON public.profiles TO authenticated;
GRANT UPDATE (full_name) ON public.profiles TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.predictions TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.bets TO authenticated;
GRANT SELECT, INSERT, UPDATE ON public.bank_records TO authenticated;
GRANT SELECT, INSERT ON public.ocr_usage TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.purchase_history TO authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.profiles TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.predictions TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.bets TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.bank_records TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.ocr_usage TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.purchase_history TO service_role;

REVOKE ALL ON FUNCTION public.phase3m_touch_updated_at()
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.phase3m_handle_new_user()
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.reset_pred_count_if_needed(UUID)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.consume_pred_count_batch(UUID, INTEGER)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.consume_pred_count(UUID)
    FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.reset_pred_count_if_needed(UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.consume_pred_count_batch(UUID, INTEGER) TO service_role;
GRANT EXECUTE ON FUNCTION public.consume_pred_count(UUID) TO service_role;

DO $phase3m_harden_security_definer$
DECLARE
    target RECORD;
BEGIN
    FOR target IN
        SELECT p.oid::REGPROCEDURE AS signature
        FROM pg_catalog.pg_proc AS p
        JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.prosecdef
        ORDER BY p.oid::REGPROCEDURE::TEXT
    LOOP
        EXECUTE format(
            'ALTER FUNCTION %s SET search_path = pg_catalog, public, extensions',
            target.signature
        );
        EXECUTE format(
            'REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC, anon, authenticated',
            target.signature
        );
    END LOOP;
END;
$phase3m_harden_security_definer$;
-- canonical migration 20260720143300 (supabase/bootstrap/v1/migrations/20260720143300_ocr_quota_reservation.sql)
-- Phase 3M: atomically reserve one monthly OCR unit before external work.
--
-- The trusted Next.js server supplies a verified profile UUID through the
-- service-role client. Browser roles must never execute this function.

CREATE OR REPLACE FUNCTION public.consume_ocr_quota(p_user_id UUID)
RETURNS TABLE (
    allowed BOOLEAN,
    used_count INTEGER,
    monthly_limit INTEGER,
    reset_at TIMESTAMPTZ
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_now TIMESTAMPTZ := transaction_timestamp();
    v_used INTEGER;
    v_limit INTEGER;
    v_reset_at TIMESTAMPTZ;
    v_month_changed BOOLEAN;
BEGIN
    IF p_user_id IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '22004',
            MESSAGE = 'ocr_quota_user_id_required';
    END IF;

    SELECT
        p.ocr_used_this_month,
        p.ocr_monthly_limit,
        p.ocr_reset_date
    INTO v_used, v_limit, v_reset_at
    FROM public.profiles AS p
    WHERE p.id = p_user_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0002',
            MESSAGE = 'ocr_quota_profile_not_found';
    END IF;

    IF v_used IS NULL OR v_used < 0
       OR v_limit IS NULL OR v_limit < 0
       OR v_reset_at IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '22023',
            MESSAGE = 'ocr_quota_profile_invalid';
    END IF;

    v_month_changed :=
        date_trunc('month', v_reset_at AT TIME ZONE 'UTC')
        < date_trunc('month', v_now AT TIME ZONE 'UTC');

    IF v_month_changed THEN
        v_used := 0;
        v_reset_at := v_now;
    END IF;

    IF v_used >= v_limit THEN
        IF v_month_changed THEN
            UPDATE public.profiles AS p
            SET ocr_used_this_month = v_used,
                ocr_reset_date = v_reset_at
            WHERE p.id = p_user_id;
        END IF;

        RETURN QUERY SELECT FALSE, v_used, v_limit, v_reset_at;
        RETURN;
    END IF;

    v_used := v_used + 1;
    UPDATE public.profiles AS p
    SET ocr_used_this_month = v_used,
        ocr_reset_date = v_reset_at
    WHERE p.id = p_user_id;

    RETURN QUERY SELECT TRUE, v_used, v_limit, v_reset_at;
END;
$$;

REVOKE ALL ON FUNCTION public.consume_ocr_quota(UUID)
    FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.consume_ocr_quota(UUID) TO service_role;
-- canonical migration 20260720143400 (supabase/bootstrap/v1/migrations/20260720143400_admin_role_change.sql)
-- Phase 3M: serialize privileged profile role changes inside PostgreSQL.
--
-- The trusted Next.js server supplies the user id from a verified GoTrue
-- session.  This function revalidates that actor while holding the same
-- transaction boundary used for the target update and immutable audit row.

CREATE TABLE public.admin_role_change_audit (
    request_id UUID PRIMARY KEY,
    actor_user_id UUID NOT NULL,
    target_user_id UUID NOT NULL,
    previous_role TEXT NOT NULL CHECK (previous_role IN ('user', 'admin')),
    new_role TEXT NOT NULL CHECK (new_role IN ('user', 'admin')),
    changed_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp()
);

ALTER TABLE public.admin_role_change_audit ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.admin_role_change_audit FORCE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.admin_role_change_audit
    FROM PUBLIC, anon, authenticated, service_role;

CREATE OR REPLACE FUNCTION public.update_admin_profile_role(
    p_actor_user_id UUID,
    p_target_user_id UUID,
    p_role TEXT,
    p_request_id UUID
)
RETURNS TABLE (
    id UUID,
    role TEXT,
    request_id UUID
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor_role TEXT;
    v_previous_role TEXT;
    v_other_admin_exists BOOLEAN;
BEGIN
    IF p_actor_user_id IS NULL
       OR p_target_user_id IS NULL
       OR p_request_id IS NULL
       OR p_role IS NULL
       OR p_role NOT IN ('user', 'admin') THEN
        RAISE EXCEPTION USING
            ERRCODE = '22023',
            MESSAGE = 'admin_role_change_input_invalid';
    END IF;

    -- Serialize every role transition so two concurrent demotions cannot both
    -- observe another administrator and remove the final Admin together.
    PERFORM pg_catalog.pg_advisory_xact_lock(73003143400::BIGINT);

    SELECT p.role
    INTO v_actor_role
    FROM public.profiles AS p
    WHERE p.id = p_actor_user_id
    FOR UPDATE;

    IF NOT FOUND OR v_actor_role <> 'admin' THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'admin_role_change_actor_not_admin';
    END IF;

    SELECT p.role
    INTO v_previous_role
    FROM public.profiles AS p
    WHERE p.id = p_target_user_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0002',
            MESSAGE = 'admin_role_change_target_not_found';
    END IF;

    IF v_previous_role = 'admin' AND p_role = 'user' THEN
        SELECT EXISTS (
            SELECT 1
            FROM public.profiles AS p
            WHERE p.role = 'admin'
              AND p.id <> p_target_user_id
        )
        INTO v_other_admin_exists;

        IF NOT v_other_admin_exists THEN
            RAISE EXCEPTION USING
                ERRCODE = 'P0001',
                MESSAGE = 'admin_role_change_last_admin_forbidden';
        END IF;
    END IF;

    UPDATE public.profiles AS p
    SET role = p_role
    WHERE p.id = p_target_user_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0002',
            MESSAGE = 'admin_role_change_target_not_found';
    END IF;

    INSERT INTO public.admin_role_change_audit (
        request_id,
        actor_user_id,
        target_user_id,
        previous_role,
        new_role
    ) VALUES (
        p_request_id,
        p_actor_user_id,
        p_target_user_id,
        v_previous_role,
        p_role
    );

    RETURN QUERY SELECT p_target_user_id, p_role, p_request_id;
END;
$$;

REVOKE ALL ON FUNCTION public.update_admin_profile_role(UUID, UUID, TEXT, UUID)
    FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.update_admin_profile_role(UUID, UUID, TEXT, UUID)
    TO service_role;
-- canonical migration 20260802140000 (supabase/migrations/20260802_model_retrain_approval_ledger.sql)
-- ============================================================
-- Model retrain approval ledger (declarative only).
-- Applying this migration is a separate, explicitly approved operation.
-- Approval never creates a job, writes a model artifact, or changes the
-- active-model pointer. Those capabilities require later, separate gates.
-- ============================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;

CREATE TABLE IF NOT EXISTS public.model_retrain_approval_requests (
    approval_id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
    dry_run_id UUID NOT NULL UNIQUE,
    approved_by UUID NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
    approved_at TIMESTAMPTZ NULL,
    approval_status TEXT NOT NULL DEFAULT 'pending' CHECK (
        approval_status IN ('pending', 'approved', 'rejected', 'expired', 'invalidated')
    ),
    approval_comment TEXT NOT NULL DEFAULT '' CHECK (
        char_length(approval_comment) <= 500
        AND approval_comment !~ '[[:cntrl:]]'
    ),
    approved_payload_hash TEXT NOT NULL CHECK (approved_payload_hash ~ '^[0-9a-f]{64}$'),
    requested_by UUID NOT NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    invalidation_reason TEXT NULL CHECK (
        invalidation_reason IS NULL OR invalidation_reason IN (
            'payload-mismatch', 'hash-mismatch', 'expired', 'active-model-changed',
            'feature-contract-changed', 'code-version-changed',
            'manual-invalidation', 'other'
        )
    ),
    execution_policy TEXT NOT NULL CHECK (
        execution_policy IN ('read-only-preview', 'staging-train', 'sandbox-train')
    ),
    allowed_actions TEXT[] NOT NULL,
    dry_run_payload JSONB NOT NULL CHECK (
        jsonb_typeof(dry_run_payload) = 'object'
        AND octet_length(dry_run_payload::TEXT) <= 262144
    ),
    record_version INTEGER NOT NULL DEFAULT 1 CHECK (record_version >= 1),
    authoritative_record BOOLEAN NOT NULL DEFAULT TRUE CHECK (authoritative_record = TRUE),
    execution_enabled BOOLEAN NOT NULL DEFAULT FALSE CHECK (execution_enabled = FALSE),
    job_created BOOLEAN NOT NULL DEFAULT FALSE CHECK (job_created = FALSE),
    CHECK (expires_at > requested_at),
    CHECK (dry_run_payload->>'dry_run_id' = dry_run_id::TEXT),
    CHECK (dry_run_payload->>'created_by' = requested_by::TEXT),
    CHECK (dry_run_payload->>'state' = 'preview-ready'),
    CHECK (
        allowed_actions <@ ARRAY[
            'submit_approved_retrain', 'view_approval_status', 'view_job_status'
        ]::TEXT[]
        AND 'view_approval_status' = ANY (allowed_actions)
        AND cardinality(allowed_actions) =
            CASE WHEN 'submit_approved_retrain' = ANY (allowed_actions) THEN 1 ELSE 0 END
            + CASE WHEN 'view_approval_status' = ANY (allowed_actions) THEN 1 ELSE 0 END
            + CASE WHEN 'view_job_status' = ANY (allowed_actions) THEN 1 ELSE 0 END
    ),
    CHECK (
        (execution_policy = 'read-only-preview'
            AND NOT ('submit_approved_retrain' = ANY (allowed_actions))
            AND NOT ('view_job_status' = ANY (allowed_actions)))
        OR (execution_policy IN ('staging-train', 'sandbox-train')
            AND 'submit_approved_retrain' = ANY (allowed_actions)
            AND 'view_job_status' = ANY (allowed_actions))
    ),
    CHECK (
        (approval_status = 'pending'
            AND approved_by IS NULL AND approved_at IS NULL
            AND invalidation_reason IS NULL)
        OR (approval_status IN ('approved', 'rejected')
            AND approved_by IS NOT NULL AND approved_at IS NOT NULL
            AND approved_by <> requested_by AND invalidation_reason IS NULL)
        OR (approval_status = 'expired' AND invalidation_reason = 'expired')
        OR (approval_status = 'invalidated' AND invalidation_reason IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_model_retrain_approval_requested
    ON public.model_retrain_approval_requests (requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_model_retrain_approval_pending
    ON public.model_retrain_approval_requests (approval_status, expires_at)
    WHERE approval_status = 'pending';

CREATE TABLE IF NOT EXISTS public.model_retrain_approval_events (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    approval_id UUID NOT NULL REFERENCES public.model_retrain_approval_requests(approval_id) ON DELETE RESTRICT,
    event_seq INTEGER NOT NULL CHECK (event_seq >= 1),
    event_type TEXT NOT NULL CHECK (
        event_type IN ('created', 'approved', 'rejected', 'invalidated', 'expired')
    ),
    actor_user_id UUID NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
    from_status TEXT NULL CHECK (
        from_status IS NULL OR from_status IN ('pending', 'approved', 'rejected', 'expired', 'invalidated')
    ),
    to_status TEXT NOT NULL CHECK (
        to_status IN ('pending', 'approved', 'rejected', 'expired', 'invalidated')
    ),
    record_version INTEGER NOT NULL CHECK (record_version >= 1),
    approved_payload_hash TEXT NOT NULL CHECK (approved_payload_hash ~ '^[0-9a-f]{64}$'),
    reason TEXT NULL CHECK (
        reason IS NULL OR (
            char_length(reason) BETWEEN 20 AND 500
            AND reason !~ '[[:cntrl:]]'
        )
    ),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (approval_id, event_seq)
);

ALTER TABLE public.model_retrain_approval_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.model_retrain_approval_events ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.model_retrain_approval_requests
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON TABLE public.model_retrain_approval_events
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.model_retrain_approval_events_event_id_seq
    FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON TABLE public.model_retrain_approval_requests TO service_role;
GRANT SELECT ON TABLE public.model_retrain_approval_events TO service_role;

CREATE OR REPLACE FUNCTION public._model_retrain_require_admin(p_actor_user_id UUID)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF p_actor_user_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM public.profiles AS p
        WHERE p.id = p_actor_user_id
          AND lower(COALESCE(p.role, '')) = 'admin'
    ) THEN
        RAISE EXCEPTION 'admin role required' USING ERRCODE = '42501';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public._reject_model_retrain_approval_event_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RAISE EXCEPTION 'model retrain approval events are append-only' USING ERRCODE = '55000';
END;
$$;

CREATE OR REPLACE FUNCTION public._guard_model_retrain_approval_update()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF ROW(
        NEW.approval_id, NEW.dry_run_id, NEW.approved_payload_hash,
        NEW.requested_by, NEW.requested_at, NEW.expires_at,
        NEW.execution_policy, NEW.allowed_actions, NEW.dry_run_payload
    ) IS DISTINCT FROM ROW(
        OLD.approval_id, OLD.dry_run_id, OLD.approved_payload_hash,
        OLD.requested_by, OLD.requested_at, OLD.expires_at,
        OLD.execution_policy, OLD.allowed_actions, OLD.dry_run_payload
    ) THEN
        RAISE EXCEPTION 'model retrain approval binding is immutable' USING ERRCODE = '55000';
    END IF;
    IF NEW.record_version <> OLD.record_version + 1
       OR NEW.authoritative_record IS DISTINCT FROM TRUE
       OR NEW.execution_enabled IS DISTINCT FROM FALSE
       OR NEW.job_created IS DISTINCT FROM FALSE THEN
        RAISE EXCEPTION 'model retrain approval update violates safety state' USING ERRCODE = '55000';
    END IF;
    IF NOT (
        (OLD.approval_status = 'pending'
            AND NEW.approval_status IN ('approved', 'rejected', 'expired', 'invalidated'))
        OR (OLD.approval_status = 'approved'
            AND NEW.approval_status IN ('expired', 'invalidated'))
    ) THEN
        RAISE EXCEPTION 'model retrain approval transition is invalid' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_model_retrain_approval_update_guard
    ON public.model_retrain_approval_requests;
CREATE TRIGGER trg_model_retrain_approval_update_guard
    BEFORE UPDATE ON public.model_retrain_approval_requests
    FOR EACH ROW EXECUTE FUNCTION public._guard_model_retrain_approval_update();

DROP TRIGGER IF EXISTS trg_model_retrain_approval_events_immutable
    ON public.model_retrain_approval_events;
CREATE TRIGGER trg_model_retrain_approval_events_immutable
    BEFORE UPDATE OR DELETE ON public.model_retrain_approval_events
    FOR EACH ROW EXECUTE FUNCTION public._reject_model_retrain_approval_event_mutation();

CREATE OR REPLACE FUNCTION public._expire_model_retrain_approval_if_needed(p_approval_id UUID)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_row public.model_retrain_approval_requests%ROWTYPE;
    v_version INTEGER;
BEGIN
    SELECT * INTO v_row
    FROM public.model_retrain_approval_requests AS a
    WHERE a.approval_id = p_approval_id
    FOR UPDATE;
    IF NOT FOUND THEN RETURN; END IF;

    IF v_row.approval_status IN ('pending', 'approved')
       AND v_row.expires_at <= clock_timestamp() THEN
        v_version := v_row.record_version + 1;
        UPDATE public.model_retrain_approval_requests AS a
        SET approval_status = 'expired',
            invalidation_reason = 'expired',
            approval_comment = CASE
                WHEN a.approval_comment = '' THEN 'Approval validity expired before any retrain job was created.'
                ELSE a.approval_comment
            END,
            record_version = v_version,
            execution_enabled = FALSE,
            job_created = a.job_created
        WHERE a.approval_id = p_approval_id;

        INSERT INTO public.model_retrain_approval_events (
            approval_id, event_seq, event_type, actor_user_id, from_status,
            to_status, record_version, approved_payload_hash, reason
        ) VALUES (
            v_row.approval_id, v_version, 'expired', NULL, v_row.approval_status,
            'expired', v_version, v_row.approved_payload_hash,
            'Approval validity expired before any retrain job was created.'
        );
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.create_model_retrain_approval(
    p_actor_user_id UUID,
    p_dry_run_id UUID,
    p_approved_payload_hash TEXT,
    p_dry_run_payload JSONB,
    p_execution_policy TEXT,
    p_allowed_actions TEXT[]
)
RETURNS SETOF public.model_retrain_approval_requests
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_existing public.model_retrain_approval_requests%ROWTYPE;
    v_approval_id UUID;
    v_generated_at TIMESTAMPTZ;
BEGIN
    PERFORM public._model_retrain_require_admin(p_actor_user_id);
    IF p_dry_run_id IS NULL
       OR p_approved_payload_hash IS NULL
       OR p_approved_payload_hash !~ '^[0-9a-f]{64}$'
       OR p_dry_run_payload IS NULL
       OR jsonb_typeof(p_dry_run_payload) <> 'object'
       OR octet_length(p_dry_run_payload::TEXT) > 262144
       OR p_dry_run_payload->>'dry_run_id' IS DISTINCT FROM p_dry_run_id::TEXT
       OR p_dry_run_payload->>'created_by' IS DISTINCT FROM p_actor_user_id::TEXT
       OR p_dry_run_payload->>'state' IS DISTINCT FROM 'preview-ready'
       OR p_execution_policy NOT IN ('read-only-preview', 'staging-train', 'sandbox-train')
       OR p_allowed_actions IS NULL
       OR cardinality(p_allowed_actions) < 1
       OR cardinality(p_allowed_actions) > 3
       OR NOT ('view_approval_status' = ANY (p_allowed_actions))
       OR NOT (p_allowed_actions <@ ARRAY[
            'submit_approved_retrain', 'view_approval_status', 'view_job_status'
       ]::TEXT[])
       OR cardinality(p_allowed_actions) <> (
            CASE WHEN 'submit_approved_retrain' = ANY (p_allowed_actions) THEN 1 ELSE 0 END
            + CASE WHEN 'view_approval_status' = ANY (p_allowed_actions) THEN 1 ELSE 0 END
            + CASE WHEN 'view_job_status' = ANY (p_allowed_actions) THEN 1 ELSE 0 END
       )
       OR (p_execution_policy = 'read-only-preview' AND (
            'submit_approved_retrain' = ANY (p_allowed_actions)
            OR 'view_job_status' = ANY (p_allowed_actions)
       ))
       OR (p_execution_policy IN ('staging-train', 'sandbox-train') AND NOT (
            'submit_approved_retrain' = ANY (p_allowed_actions)
            AND 'view_job_status' = ANY (p_allowed_actions)
       )) THEN
        RAISE EXCEPTION 'invalid model retrain approval request' USING ERRCODE = '22023';
    END IF;

    BEGIN
        v_generated_at := (p_dry_run_payload->>'generated_at')::TIMESTAMPTZ;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'invalid model retrain approval request' USING ERRCODE = '22023';
    END;
    IF v_generated_at < v_now - INTERVAL '24 hours'
       OR v_generated_at > v_now + INTERVAL '5 minutes' THEN
        RAISE EXCEPTION 'model retrain preview outside freshness window' USING ERRCODE = '22023';
    END IF;

    PERFORM pg_advisory_xact_lock(hashtextextended(p_dry_run_id::TEXT, 0));
    SELECT * INTO v_existing
    FROM public.model_retrain_approval_requests AS a
    WHERE a.dry_run_id = p_dry_run_id
    FOR UPDATE;

    IF FOUND THEN
        IF v_existing.requested_by IS DISTINCT FROM p_actor_user_id
           OR v_existing.approved_payload_hash IS DISTINCT FROM p_approved_payload_hash
           OR v_existing.dry_run_payload IS DISTINCT FROM p_dry_run_payload
           OR v_existing.execution_policy IS DISTINCT FROM p_execution_policy
           OR v_existing.allowed_actions IS DISTINCT FROM p_allowed_actions THEN
            RAISE EXCEPTION 'dry run approval payload conflict' USING ERRCODE = '23505';
        END IF;
        v_approval_id := v_existing.approval_id;
        PERFORM public._expire_model_retrain_approval_if_needed(v_approval_id);
    ELSE
        v_approval_id := extensions.gen_random_uuid();
        INSERT INTO public.model_retrain_approval_requests (
            approval_id, dry_run_id, approved_payload_hash, requested_by,
            requested_at, expires_at, execution_policy, allowed_actions,
            dry_run_payload, approval_status, record_version,
            authoritative_record, execution_enabled, job_created
        ) VALUES (
            v_approval_id, p_dry_run_id, p_approved_payload_hash, p_actor_user_id,
            v_now, v_now + INTERVAL '30 minutes', p_execution_policy,
            p_allowed_actions, p_dry_run_payload, 'pending', 1, TRUE, FALSE, FALSE
        );
        INSERT INTO public.model_retrain_approval_events (
            approval_id, event_seq, event_type, actor_user_id, from_status,
            to_status, record_version, approved_payload_hash, reason
        ) VALUES (
            v_approval_id, 1, 'created', p_actor_user_id, NULL,
            'pending', 1, p_approved_payload_hash, NULL
        );
    END IF;

    RETURN QUERY SELECT * FROM public.model_retrain_approval_requests AS a
        WHERE a.approval_id = v_approval_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.get_model_retrain_approval(
    p_actor_user_id UUID,
    p_approval_id UUID
)
RETURNS SETOF public.model_retrain_approval_requests
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    PERFORM public._model_retrain_require_admin(p_actor_user_id);
    PERFORM public._expire_model_retrain_approval_if_needed(p_approval_id);
    RETURN QUERY SELECT * FROM public.model_retrain_approval_requests AS a
        WHERE a.approval_id = p_approval_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.transition_model_retrain_approval(
    p_actor_user_id UUID,
    p_approval_id UUID,
    p_expected_version INTEGER,
    p_action TEXT,
    p_reason TEXT
)
RETURNS SETOF public.model_retrain_approval_requests
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_row public.model_retrain_approval_requests%ROWTYPE;
    v_reason TEXT;
    v_status TEXT;
    v_event TEXT;
    v_version INTEGER;
BEGIN
    PERFORM public._model_retrain_require_admin(p_actor_user_id);
    v_reason := regexp_replace(btrim(COALESCE(p_reason, '')), '[[:space:]]+', ' ', 'g');
    IF p_action NOT IN ('approve', 'reject', 'revoke')
       OR p_expected_version IS NULL OR p_expected_version < 1
       OR p_reason ~ '[[:cntrl:]]'
       OR char_length(v_reason) NOT BETWEEN 20 AND 500 THEN
        RAISE EXCEPTION 'invalid model retrain approval transition' USING ERRCODE = '22023';
    END IF;

    PERFORM public._expire_model_retrain_approval_if_needed(p_approval_id);
    SELECT * INTO v_row
    FROM public.model_retrain_approval_requests AS a
    WHERE a.approval_id = p_approval_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'model retrain approval not found' USING ERRCODE = 'P0002';
    END IF;
    IF v_row.record_version <> p_expected_version THEN
        RAISE EXCEPTION 'model retrain approval version conflict' USING ERRCODE = '40001';
    END IF;
    IF v_row.approval_status = 'expired' THEN
        RAISE EXCEPTION 'model retrain approval expired' USING ERRCODE = '55000';
    END IF;

    IF p_action IN ('approve', 'reject') THEN
        IF v_row.approval_status <> 'pending' THEN
            RAISE EXCEPTION 'model retrain approval is not pending' USING ERRCODE = '55000';
        END IF;
        IF p_actor_user_id = v_row.requested_by THEN
            RAISE EXCEPTION 'requester cannot decide own approval' USING ERRCODE = '42501';
        END IF;
        v_status := CASE WHEN p_action = 'approve' THEN 'approved' ELSE 'rejected' END;
        v_event := v_status;
    ELSE
        IF p_actor_user_id <> v_row.requested_by THEN
            RAISE EXCEPTION 'only requester can revoke approval' USING ERRCODE = '42501';
        END IF;
        IF v_row.approval_status NOT IN ('pending', 'approved') THEN
            RAISE EXCEPTION 'model retrain approval cannot be revoked' USING ERRCODE = '55000';
        END IF;
        v_status := 'invalidated';
        v_event := 'invalidated';
    END IF;

    v_version := v_row.record_version + 1;
    UPDATE public.model_retrain_approval_requests AS a
    SET approval_status = v_status,
        approved_by = CASE WHEN p_action = 'revoke' THEN a.approved_by ELSE p_actor_user_id END,
        approved_at = CASE WHEN p_action = 'revoke' THEN a.approved_at ELSE clock_timestamp() END,
        approval_comment = v_reason,
        invalidation_reason = CASE WHEN p_action = 'revoke' THEN 'manual-invalidation' ELSE NULL END,
        record_version = v_version,
        authoritative_record = TRUE,
        execution_enabled = FALSE,
        job_created = a.job_created
    WHERE a.approval_id = p_approval_id;

    INSERT INTO public.model_retrain_approval_events (
        approval_id, event_seq, event_type, actor_user_id, from_status,
        to_status, record_version, approved_payload_hash, reason
    ) VALUES (
        v_row.approval_id, v_version, v_event, p_actor_user_id,
        v_row.approval_status, v_status, v_version, v_row.approved_payload_hash, v_reason
    );

    RETURN QUERY SELECT * FROM public.model_retrain_approval_requests AS a
        WHERE a.approval_id = p_approval_id;
END;
$$;

REVOKE ALL ON FUNCTION public._model_retrain_require_admin(UUID)
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public._reject_model_retrain_approval_event_mutation()
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public._guard_model_retrain_approval_update()
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public._expire_model_retrain_approval_if_needed(UUID)
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.create_model_retrain_approval(UUID, UUID, TEXT, JSONB, TEXT, TEXT[])
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.get_model_retrain_approval(UUID, UUID)
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.transition_model_retrain_approval(UUID, UUID, INTEGER, TEXT, TEXT)
    FROM PUBLIC, anon, authenticated, service_role;

GRANT EXECUTE ON FUNCTION public.create_model_retrain_approval(UUID, UUID, TEXT, JSONB, TEXT, TEXT[])
    TO service_role;
GRANT EXECUTE ON FUNCTION public.get_model_retrain_approval(UUID, UUID)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.transition_model_retrain_approval(UUID, UUID, INTEGER, TEXT, TEXT)
    TO service_role;
-- canonical migration 20260802141000 (supabase/migrations/20260802_model_retrain_job_ledger.sql)
-- ============================================================
-- Approval-bound model retrain job submission ledger.
-- Applying this migration is a separate, explicitly approved operation.
-- A queued record does not start training or write an artifact. A later
-- worker/lease migration must add the independently verified executor.
-- ============================================================

ALTER TABLE public.model_retrain_approval_requests
    DROP CONSTRAINT IF EXISTS model_retrain_approval_requests_job_created_check;

ALTER TABLE public.model_retrain_approval_events
    DROP CONSTRAINT IF EXISTS model_retrain_approval_events_event_type_check;
ALTER TABLE public.model_retrain_approval_events
    ADD CONSTRAINT model_retrain_approval_events_event_type_check CHECK (
        event_type IN ('created', 'approved', 'rejected', 'invalidated', 'expired', 'job-created')
    );

CREATE OR REPLACE FUNCTION public._guard_model_retrain_approval_update()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF ROW(
        NEW.approval_id, NEW.dry_run_id, NEW.approved_payload_hash,
        NEW.requested_by, NEW.requested_at, NEW.expires_at,
        NEW.execution_policy, NEW.allowed_actions, NEW.dry_run_payload
    ) IS DISTINCT FROM ROW(
        OLD.approval_id, OLD.dry_run_id, OLD.approved_payload_hash,
        OLD.requested_by, OLD.requested_at, OLD.expires_at,
        OLD.execution_policy, OLD.allowed_actions, OLD.dry_run_payload
    ) THEN
        RAISE EXCEPTION 'model retrain approval binding is immutable' USING ERRCODE = '55000';
    END IF;
    IF NEW.record_version <> OLD.record_version + 1
       OR NEW.authoritative_record IS DISTINCT FROM TRUE
       OR NEW.execution_enabled IS DISTINCT FROM FALSE
       OR (OLD.job_created IS TRUE AND NEW.job_created IS FALSE) THEN
        RAISE EXCEPTION 'model retrain approval update violates safety state' USING ERRCODE = '55000';
    END IF;

    IF NEW.job_created IS DISTINCT FROM OLD.job_created THEN
        IF NOT (
            OLD.job_created IS FALSE AND NEW.job_created IS TRUE
            AND OLD.approval_status = 'approved' AND NEW.approval_status = 'approved'
            AND ROW(
                NEW.approved_by, NEW.approved_at, NEW.approval_comment,
                NEW.invalidation_reason
            ) IS NOT DISTINCT FROM ROW(
                OLD.approved_by, OLD.approved_at, OLD.approval_comment,
                OLD.invalidation_reason
            )
        ) THEN
            RAISE EXCEPTION 'model retrain job marker transition is invalid' USING ERRCODE = '55000';
        END IF;
    ELSIF NOT (
        (OLD.approval_status = 'pending'
            AND NEW.approval_status IN ('approved', 'rejected', 'expired', 'invalidated'))
        OR (OLD.approval_status = 'approved'
            AND NEW.approval_status IN ('expired', 'invalidated'))
    ) THEN
        RAISE EXCEPTION 'model retrain approval transition is invalid' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TABLE IF NOT EXISTS public.model_retrain_jobs (
    job_id UUID PRIMARY KEY DEFAULT extensions.gen_random_uuid(),
    approval_id UUID NOT NULL UNIQUE
        REFERENCES public.model_retrain_approval_requests(approval_id) ON DELETE RESTRICT,
    dry_run_id UUID NOT NULL UNIQUE,
    approved_payload_hash TEXT NOT NULL CHECK (approved_payload_hash ~ '^[0-9a-f]{64}$'),
    submitted_by UUID NOT NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
    requested_by UUID NOT NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
    approved_by UUID NOT NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
    execution_policy TEXT NOT NULL CHECK (execution_policy IN ('staging-train', 'sandbox-train')),
    job_state TEXT NOT NULL DEFAULT 'queued' CHECK (job_state = 'queued'),
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    record_version INTEGER NOT NULL DEFAULT 1 CHECK (record_version = 1),
    authoritative_record BOOLEAN NOT NULL DEFAULT TRUE CHECK (authoritative_record = TRUE),
    execution_started BOOLEAN NOT NULL DEFAULT FALSE CHECK (execution_started = FALSE),
    artifact_written BOOLEAN NOT NULL DEFAULT FALSE CHECK (artifact_written = FALSE),
    artifact_uri TEXT NULL CHECK (artifact_uri IS NULL),
    artifact_sha256 TEXT NULL CHECK (artifact_sha256 IS NULL),
    CHECK (submitted_by = requested_by),
    CHECK (approved_by <> requested_by)
);

CREATE TABLE IF NOT EXISTS public.model_retrain_job_events (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES public.model_retrain_jobs(job_id) ON DELETE RESTRICT,
    event_seq INTEGER NOT NULL CHECK (event_seq >= 1),
    event_type TEXT NOT NULL CHECK (event_type = 'queued'),
    actor_user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
    approval_id UUID NOT NULL REFERENCES public.model_retrain_approval_requests(approval_id) ON DELETE RESTRICT,
    approved_payload_hash TEXT NOT NULL CHECK (approved_payload_hash ~ '^[0-9a-f]{64}$'),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (job_id, event_seq)
);

CREATE INDEX IF NOT EXISTS idx_model_retrain_jobs_state
    ON public.model_retrain_jobs (job_state, submitted_at);

ALTER TABLE public.model_retrain_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.model_retrain_job_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.model_retrain_jobs
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON TABLE public.model_retrain_job_events
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.model_retrain_job_events_event_id_seq
    FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON TABLE public.model_retrain_jobs TO service_role;
GRANT SELECT ON TABLE public.model_retrain_job_events TO service_role;

CREATE OR REPLACE FUNCTION public._reject_model_retrain_job_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RAISE EXCEPTION 'model retrain queued jobs are immutable until the worker lease contract is installed'
        USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_model_retrain_jobs_immutable ON public.model_retrain_jobs;
CREATE TRIGGER trg_model_retrain_jobs_immutable
    BEFORE UPDATE OR DELETE ON public.model_retrain_jobs
    FOR EACH ROW EXECUTE FUNCTION public._reject_model_retrain_job_mutation();

DROP TRIGGER IF EXISTS trg_model_retrain_job_events_immutable ON public.model_retrain_job_events;
CREATE TRIGGER trg_model_retrain_job_events_immutable
    BEFORE UPDATE OR DELETE ON public.model_retrain_job_events
    FOR EACH ROW EXECUTE FUNCTION public._reject_model_retrain_job_mutation();

CREATE OR REPLACE FUNCTION public.create_model_retrain_job(
    p_actor_user_id UUID,
    p_approval_id UUID,
    p_expected_approval_version INTEGER,
    p_approved_payload_hash TEXT
)
RETURNS SETOF public.model_retrain_jobs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_approval public.model_retrain_approval_requests%ROWTYPE;
    v_existing public.model_retrain_jobs%ROWTYPE;
    v_job_id UUID;
    v_approval_version INTEGER;
BEGIN
    PERFORM public._model_retrain_require_admin(p_actor_user_id);
    IF p_approval_id IS NULL
       OR p_expected_approval_version IS NULL OR p_expected_approval_version < 1
       OR p_approved_payload_hash IS NULL
       OR p_approved_payload_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'invalid model retrain job submission' USING ERRCODE = '22023';
    END IF;

    PERFORM pg_advisory_xact_lock(hashtextextended(p_approval_id::TEXT, 0));
    SELECT * INTO v_existing
    FROM public.model_retrain_jobs AS j
    WHERE j.approval_id = p_approval_id
    FOR UPDATE;
    IF FOUND THEN
        IF v_existing.submitted_by IS DISTINCT FROM p_actor_user_id
           OR v_existing.approved_payload_hash IS DISTINCT FROM p_approved_payload_hash THEN
            RAISE EXCEPTION 'model retrain job submission conflict' USING ERRCODE = '23505';
        END IF;
        RETURN QUERY SELECT * FROM public.model_retrain_jobs AS j
            WHERE j.job_id = v_existing.job_id;
        RETURN;
    END IF;

    PERFORM public._expire_model_retrain_approval_if_needed(p_approval_id);
    SELECT * INTO v_approval
    FROM public.model_retrain_approval_requests AS a
    WHERE a.approval_id = p_approval_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'model retrain approval not found' USING ERRCODE = 'P0002';
    END IF;
    IF v_approval.record_version <> p_expected_approval_version THEN
        RAISE EXCEPTION 'model retrain approval version conflict' USING ERRCODE = '40001';
    END IF;
    IF v_approval.approval_status <> 'approved'
       OR v_approval.expires_at <= clock_timestamp()
       OR v_approval.job_created IS TRUE
       OR v_approval.requested_by IS DISTINCT FROM p_actor_user_id
       OR v_approval.approved_by IS NULL
       OR v_approval.approved_by = v_approval.requested_by
       OR v_approval.approved_payload_hash IS DISTINCT FROM p_approved_payload_hash
       OR v_approval.execution_policy NOT IN ('staging-train', 'sandbox-train')
       OR NOT ('submit_approved_retrain' = ANY (v_approval.allowed_actions))
       OR NOT ('view_job_status' = ANY (v_approval.allowed_actions)) THEN
        RAISE EXCEPTION 'model retrain approval is not eligible for job submission'
            USING ERRCODE = '55000';
    END IF;

    v_job_id := extensions.gen_random_uuid();
    INSERT INTO public.model_retrain_jobs (
        job_id, approval_id, dry_run_id, approved_payload_hash,
        submitted_by, requested_by, approved_by, execution_policy,
        job_state, record_version, authoritative_record,
        execution_started, artifact_written, artifact_uri, artifact_sha256
    ) VALUES (
        v_job_id, v_approval.approval_id, v_approval.dry_run_id,
        v_approval.approved_payload_hash, p_actor_user_id,
        v_approval.requested_by, v_approval.approved_by,
        v_approval.execution_policy, 'queued', 1, TRUE, FALSE, FALSE, NULL, NULL
    );

    INSERT INTO public.model_retrain_job_events (
        job_id, event_seq, event_type, actor_user_id,
        approval_id, approved_payload_hash
    ) VALUES (
        v_job_id, 1, 'queued', p_actor_user_id,
        v_approval.approval_id, v_approval.approved_payload_hash
    );

    v_approval_version := v_approval.record_version + 1;
    UPDATE public.model_retrain_approval_requests AS a
    SET job_created = TRUE,
        record_version = v_approval_version,
        authoritative_record = TRUE,
        execution_enabled = FALSE
    WHERE a.approval_id = p_approval_id;

    INSERT INTO public.model_retrain_approval_events (
        approval_id, event_seq, event_type, actor_user_id, from_status,
        to_status, record_version, approved_payload_hash, reason
    ) VALUES (
        v_approval.approval_id, v_approval_version, 'job-created', p_actor_user_id,
        'approved', 'approved', v_approval_version, v_approval.approved_payload_hash,
        'Approval-bound retrain job was queued without starting execution.'
    );

    RETURN QUERY SELECT * FROM public.model_retrain_jobs AS j
        WHERE j.job_id = v_job_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.get_model_retrain_job(
    p_actor_user_id UUID,
    p_job_id UUID
)
RETURNS SETOF public.model_retrain_jobs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    PERFORM public._model_retrain_require_admin(p_actor_user_id);
    RETURN QUERY SELECT * FROM public.model_retrain_jobs AS j
        WHERE j.job_id = p_job_id;
END;
$$;

REVOKE ALL ON FUNCTION public._reject_model_retrain_job_mutation()
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.create_model_retrain_job(UUID, UUID, INTEGER, TEXT)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.get_model_retrain_job(UUID, UUID)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.create_model_retrain_job(UUID, UUID, INTEGER, TEXT)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.get_model_retrain_job(UUID, UUID)
    TO service_role;
-- canonical migration 20260802142000 (supabase/migrations/20260802_model_retrain_worker_lease.sql)
-- ============================================================
-- Model retrain worker lease and fencing state machine.
-- This migration authorizes no artifact write and has no success transition.
-- A later isolated-writer contract must bind artifact/evaluation evidence.
-- ============================================================

CREATE SEQUENCE IF NOT EXISTS public.model_retrain_job_fencing_seq AS BIGINT START WITH 1;
REVOKE ALL ON SEQUENCE public.model_retrain_job_fencing_seq
    FROM PUBLIC, anon, authenticated, service_role;

ALTER TABLE public.model_retrain_jobs
    DROP CONSTRAINT IF EXISTS model_retrain_jobs_job_state_check,
    DROP CONSTRAINT IF EXISTS model_retrain_jobs_record_version_check,
    DROP CONSTRAINT IF EXISTS model_retrain_jobs_execution_started_check;

ALTER TABLE public.model_retrain_jobs
    ADD COLUMN IF NOT EXISTS worker_id TEXT NULL,
    ADD COLUMN IF NOT EXISTS fencing_token BIGINT NULL,
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS failure_code TEXT NULL;

ALTER TABLE public.model_retrain_jobs
    ADD CONSTRAINT model_retrain_jobs_job_state_check CHECK (
        job_state IN ('queued', 'claimed', 'running', 'failed')
    ),
    ADD CONSTRAINT model_retrain_jobs_record_version_check CHECK (record_version >= 1),
    ADD CONSTRAINT model_retrain_jobs_worker_id_check CHECK (
        worker_id IS NULL OR worker_id ~ '^[a-z0-9][a-z0-9._:-]{2,79}$'
    ),
    ADD CONSTRAINT model_retrain_jobs_fencing_token_check CHECK (
        fencing_token IS NULL OR fencing_token >= 1
    ),
    ADD CONSTRAINT model_retrain_jobs_failure_code_check CHECK (
        failure_code IS NULL OR failure_code IN (
            'worker-error', 'input-invalid', 'training-failed',
            'cancelled-before-write', 'lease-expired-running'
        )
    ),
    ADD CONSTRAINT model_retrain_jobs_worker_state_check CHECK (
        (job_state = 'queued'
            AND worker_id IS NULL AND fencing_token IS NULL
            AND lease_expires_at IS NULL AND claimed_at IS NULL
            AND started_at IS NULL AND finished_at IS NULL
            AND failure_code IS NULL AND execution_started = FALSE)
        OR (job_state = 'claimed'
            AND worker_id IS NOT NULL AND fencing_token IS NOT NULL
            AND lease_expires_at IS NOT NULL AND claimed_at IS NOT NULL
            AND started_at IS NULL AND finished_at IS NULL
            AND failure_code IS NULL AND execution_started = FALSE)
        OR (job_state = 'running'
            AND worker_id IS NOT NULL AND fencing_token IS NOT NULL
            AND lease_expires_at IS NOT NULL AND claimed_at IS NOT NULL
            AND started_at IS NOT NULL AND finished_at IS NULL
            AND failure_code IS NULL AND execution_started = TRUE)
        OR (job_state = 'failed'
            AND worker_id IS NOT NULL AND fencing_token IS NOT NULL
            AND lease_expires_at IS NOT NULL AND claimed_at IS NOT NULL
            AND finished_at IS NOT NULL AND failure_code IS NOT NULL)
    );

ALTER TABLE public.model_retrain_job_events
    DROP CONSTRAINT IF EXISTS model_retrain_job_events_event_type_check;
ALTER TABLE public.model_retrain_job_events
    ALTER COLUMN actor_user_id DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS worker_id TEXT NULL,
    ADD COLUMN IF NOT EXISTS fencing_token BIGINT NULL,
    ADD COLUMN IF NOT EXISTS from_state TEXT NULL,
    ADD COLUMN IF NOT EXISTS to_state TEXT NOT NULL DEFAULT 'queued',
    ADD COLUMN IF NOT EXISTS record_version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS reason TEXT NULL;
ALTER TABLE public.model_retrain_job_events
    ADD CONSTRAINT model_retrain_job_events_event_type_check CHECK (
        event_type IN ('queued', 'claimed', 'heartbeat', 'started', 'failed', 'lease-expired')
    ),
    ADD CONSTRAINT model_retrain_job_events_worker_id_check CHECK (
        worker_id IS NULL OR worker_id ~ '^[a-z0-9][a-z0-9._:-]{2,79}$'
    ),
    ADD CONSTRAINT model_retrain_job_events_fencing_check CHECK (
        fencing_token IS NULL OR fencing_token >= 1
    ),
    ADD CONSTRAINT model_retrain_job_events_state_check CHECK (
        (from_state IS NULL OR from_state IN ('queued', 'claimed', 'running', 'failed'))
        AND to_state IN ('queued', 'claimed', 'running', 'failed')
        AND record_version >= 1
    ),
    ADD CONSTRAINT model_retrain_job_events_actor_check CHECK (
        (event_type = 'queued' AND actor_user_id IS NOT NULL AND worker_id IS NULL AND fencing_token IS NULL)
        OR (event_type <> 'queued' AND actor_user_id IS NULL AND worker_id IS NOT NULL AND fencing_token IS NOT NULL)
    ),
    ADD CONSTRAINT model_retrain_job_events_reason_check CHECK (
        reason IS NULL OR (
            char_length(reason) BETWEEN 10 AND 300
            AND reason !~ '[[:cntrl:]]'
        )
    );

CREATE OR REPLACE FUNCTION public._guard_model_retrain_job_update()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF ROW(
        NEW.job_id, NEW.approval_id, NEW.dry_run_id,
        NEW.approved_payload_hash, NEW.submitted_by, NEW.requested_by,
        NEW.approved_by, NEW.execution_policy, NEW.submitted_at
    ) IS DISTINCT FROM ROW(
        OLD.job_id, OLD.approval_id, OLD.dry_run_id,
        OLD.approved_payload_hash, OLD.submitted_by, OLD.requested_by,
        OLD.approved_by, OLD.execution_policy, OLD.submitted_at
    ) THEN
        RAISE EXCEPTION 'model retrain job binding is immutable' USING ERRCODE = '55000';
    END IF;
    IF NEW.record_version <> OLD.record_version + 1
       OR NEW.authoritative_record IS DISTINCT FROM TRUE
       OR NEW.artifact_written IS DISTINCT FROM FALSE
       OR NEW.artifact_uri IS NOT NULL OR NEW.artifact_sha256 IS NOT NULL THEN
        RAISE EXCEPTION 'model retrain job update violates safety state' USING ERRCODE = '55000';
    END IF;
    IF NOT (
        (OLD.job_state = 'queued' AND NEW.job_state = 'claimed')
        OR (OLD.job_state = 'claimed' AND NEW.job_state IN ('claimed', 'queued', 'running', 'failed'))
        OR (OLD.job_state = 'running' AND NEW.job_state IN ('running', 'failed'))
    ) THEN
        RAISE EXCEPTION 'model retrain job transition is invalid' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_model_retrain_jobs_immutable ON public.model_retrain_jobs;
DROP TRIGGER IF EXISTS trg_model_retrain_jobs_update_guard ON public.model_retrain_jobs;
CREATE TRIGGER trg_model_retrain_jobs_update_guard
    BEFORE UPDATE ON public.model_retrain_jobs
    FOR EACH ROW EXECUTE FUNCTION public._guard_model_retrain_job_update();

CREATE OR REPLACE FUNCTION public._reject_model_retrain_job_delete()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RAISE EXCEPTION 'model retrain jobs cannot be deleted' USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_model_retrain_jobs_delete_guard ON public.model_retrain_jobs;
CREATE TRIGGER trg_model_retrain_jobs_delete_guard
    BEFORE DELETE ON public.model_retrain_jobs
    FOR EACH ROW EXECUTE FUNCTION public._reject_model_retrain_job_delete();

CREATE OR REPLACE FUNCTION public._reject_model_retrain_job_event_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RAISE EXCEPTION 'model retrain job events are append-only' USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_model_retrain_job_events_immutable ON public.model_retrain_job_events;
CREATE TRIGGER trg_model_retrain_job_events_immutable
    BEFORE UPDATE OR DELETE ON public.model_retrain_job_events
    FOR EACH ROW EXECUTE FUNCTION public._reject_model_retrain_job_event_mutation();

CREATE OR REPLACE FUNCTION public._model_retrain_validate_worker(
    p_worker_id TEXT,
    p_ttl_seconds INTEGER
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF p_worker_id IS NULL
       OR p_worker_id !~ '^[a-z0-9][a-z0-9._:-]{2,79}$'
       OR p_ttl_seconds IS NULL OR p_ttl_seconds NOT BETWEEN 30 AND 300 THEN
        RAISE EXCEPTION 'invalid model retrain worker lease request' USING ERRCODE = '22023';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.claim_model_retrain_job(
    p_worker_id TEXT,
    p_job_id UUID,
    p_expected_version INTEGER,
    p_ttl_seconds INTEGER
)
RETURNS SETOF public.model_retrain_jobs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_job public.model_retrain_jobs%ROWTYPE;
    v_approval public.model_retrain_approval_requests%ROWTYPE;
    v_token BIGINT;
    v_lease TIMESTAMPTZ;
BEGIN
    PERFORM public._model_retrain_validate_worker(p_worker_id, p_ttl_seconds);
    IF p_job_id IS NULL OR p_expected_version IS NULL OR p_expected_version < 1 THEN
        RAISE EXCEPTION 'invalid model retrain job claim' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_job FROM public.model_retrain_jobs AS j
    WHERE j.job_id = p_job_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'model retrain job not found' USING ERRCODE = 'P0002';
    END IF;
    IF v_job.record_version <> p_expected_version THEN
        RAISE EXCEPTION 'model retrain job version conflict' USING ERRCODE = '40001';
    END IF;
    IF v_job.job_state <> 'queued' THEN
        RAISE EXCEPTION 'model retrain job is not queued' USING ERRCODE = '55000';
    END IF;
    PERFORM public._expire_model_retrain_approval_if_needed(v_job.approval_id);
    SELECT * INTO v_approval FROM public.model_retrain_approval_requests AS a
    WHERE a.approval_id = v_job.approval_id FOR SHARE;
    IF v_approval.approval_status <> 'approved'
       OR v_approval.expires_at <= v_now
       OR v_approval.job_created IS DISTINCT FROM TRUE
       OR v_approval.approved_payload_hash IS DISTINCT FROM v_job.approved_payload_hash THEN
        RAISE EXCEPTION 'model retrain approval no longer authorizes claim' USING ERRCODE = '42501';
    END IF;
    v_lease := LEAST(v_now + make_interval(secs => p_ttl_seconds), v_approval.expires_at);
    IF v_lease <= v_now THEN
        RAISE EXCEPTION 'model retrain worker lease expired before claim' USING ERRCODE = '55000';
    END IF;
    v_token := nextval('public.model_retrain_job_fencing_seq'::regclass);
    UPDATE public.model_retrain_jobs AS j SET
        job_state = 'claimed', record_version = j.record_version + 1,
        worker_id = p_worker_id, fencing_token = v_token,
        lease_expires_at = v_lease, claimed_at = v_now
    WHERE j.job_id = p_job_id RETURNING * INTO v_job;
    INSERT INTO public.model_retrain_job_events (
        job_id, event_seq, event_type, actor_user_id, worker_id, approval_id,
        approved_payload_hash, fencing_token, from_state, to_state, record_version, reason
    ) VALUES (
        v_job.job_id, v_job.record_version, 'claimed', NULL, p_worker_id,
        v_job.approval_id, v_job.approved_payload_hash, v_token,
        'queued', 'claimed', v_job.record_version, 'Worker acquired a bounded execution lease.'
    );
    RETURN QUERY SELECT * FROM public.model_retrain_jobs AS j WHERE j.job_id = p_job_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.heartbeat_model_retrain_job(
    p_worker_id TEXT,
    p_job_id UUID,
    p_expected_version INTEGER,
    p_fencing_token BIGINT,
    p_ttl_seconds INTEGER
)
RETURNS SETOF public.model_retrain_jobs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_job public.model_retrain_jobs%ROWTYPE;
    v_approval public.model_retrain_approval_requests%ROWTYPE;
    v_lease TIMESTAMPTZ;
BEGIN
    PERFORM public._model_retrain_validate_worker(p_worker_id, p_ttl_seconds);
    SELECT * INTO v_job FROM public.model_retrain_jobs AS j
    WHERE j.job_id = p_job_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'model retrain job not found' USING ERRCODE = 'P0002'; END IF;
    IF v_job.record_version <> p_expected_version THEN
        RAISE EXCEPTION 'model retrain job version conflict' USING ERRCODE = '40001';
    END IF;
    IF v_job.job_state NOT IN ('claimed', 'running')
       OR v_job.worker_id IS DISTINCT FROM p_worker_id
       OR v_job.fencing_token IS DISTINCT FROM p_fencing_token
       OR v_job.lease_expires_at <= v_now THEN
        RAISE EXCEPTION 'stale model retrain worker lease' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_approval FROM public.model_retrain_approval_requests AS a
    WHERE a.approval_id = v_job.approval_id FOR SHARE;
    IF v_approval.approval_status <> 'approved' OR v_approval.expires_at <= v_now THEN
        RAISE EXCEPTION 'model retrain approval no longer authorizes heartbeat' USING ERRCODE = '42501';
    END IF;
    v_lease := LEAST(v_now + make_interval(secs => p_ttl_seconds), v_approval.expires_at);
    UPDATE public.model_retrain_jobs AS j SET
        record_version = j.record_version + 1, lease_expires_at = v_lease
    WHERE j.job_id = p_job_id RETURNING * INTO v_job;
    INSERT INTO public.model_retrain_job_events (
        job_id, event_seq, event_type, actor_user_id, worker_id, approval_id,
        approved_payload_hash, fencing_token, from_state, to_state, record_version, reason
    ) VALUES (
        v_job.job_id, v_job.record_version, 'heartbeat', NULL, p_worker_id,
        v_job.approval_id, v_job.approved_payload_hash, p_fencing_token,
        v_job.job_state, v_job.job_state, v_job.record_version,
        'Worker renewed the bounded execution lease.'
    );
    RETURN QUERY SELECT * FROM public.model_retrain_jobs AS j WHERE j.job_id = p_job_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.start_model_retrain_job(
    p_worker_id TEXT,
    p_job_id UUID,
    p_expected_version INTEGER,
    p_fencing_token BIGINT
)
RETURNS SETOF public.model_retrain_jobs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_job public.model_retrain_jobs%ROWTYPE;
    v_approval public.model_retrain_approval_requests%ROWTYPE;
BEGIN
    IF p_worker_id IS NULL OR p_worker_id !~ '^[a-z0-9][a-z0-9._:-]{2,79}$' THEN
        RAISE EXCEPTION 'invalid model retrain worker' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_job FROM public.model_retrain_jobs AS j
    WHERE j.job_id = p_job_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'model retrain job not found' USING ERRCODE = 'P0002'; END IF;
    IF v_job.record_version <> p_expected_version THEN
        RAISE EXCEPTION 'model retrain job version conflict' USING ERRCODE = '40001';
    END IF;
    IF v_job.job_state <> 'claimed'
       OR v_job.worker_id IS DISTINCT FROM p_worker_id
       OR v_job.fencing_token IS DISTINCT FROM p_fencing_token
       OR v_job.lease_expires_at <= v_now THEN
        RAISE EXCEPTION 'stale model retrain worker cannot start' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_approval FROM public.model_retrain_approval_requests AS a
    WHERE a.approval_id = v_job.approval_id FOR SHARE;
    IF v_approval.approval_status <> 'approved' OR v_approval.expires_at <= v_now THEN
        RAISE EXCEPTION 'model retrain approval no longer authorizes start' USING ERRCODE = '42501';
    END IF;
    UPDATE public.model_retrain_jobs AS j SET
        job_state = 'running', record_version = j.record_version + 1,
        execution_started = TRUE, started_at = v_now
    WHERE j.job_id = p_job_id RETURNING * INTO v_job;
    INSERT INTO public.model_retrain_job_events (
        job_id, event_seq, event_type, actor_user_id, worker_id, approval_id,
        approved_payload_hash, fencing_token, from_state, to_state, record_version, reason
    ) VALUES (
        v_job.job_id, v_job.record_version, 'started', NULL, p_worker_id,
        v_job.approval_id, v_job.approved_payload_hash, p_fencing_token,
        'claimed', 'running', v_job.record_version,
        'Fenced worker began the approved execution attempt.'
    );
    RETURN QUERY SELECT * FROM public.model_retrain_jobs AS j WHERE j.job_id = p_job_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.fail_model_retrain_job(
    p_worker_id TEXT,
    p_job_id UUID,
    p_expected_version INTEGER,
    p_fencing_token BIGINT,
    p_failure_code TEXT
)
RETURNS SETOF public.model_retrain_jobs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_job public.model_retrain_jobs%ROWTYPE;
BEGIN
    SELECT * INTO v_job FROM public.model_retrain_jobs AS j
    WHERE j.job_id = p_job_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'model retrain job not found' USING ERRCODE = 'P0002'; END IF;
    IF v_job.record_version <> p_expected_version THEN
        RAISE EXCEPTION 'model retrain job version conflict' USING ERRCODE = '40001';
    END IF;
    IF p_worker_id IS NULL OR p_worker_id !~ '^[a-z0-9][a-z0-9._:-]{2,79}$'
       OR p_failure_code NOT IN ('worker-error', 'input-invalid', 'training-failed', 'cancelled-before-write')
       OR v_job.job_state NOT IN ('claimed', 'running')
       OR v_job.worker_id IS DISTINCT FROM p_worker_id
       OR v_job.fencing_token IS DISTINCT FROM p_fencing_token
       OR v_job.lease_expires_at <= v_now THEN
        RAISE EXCEPTION 'stale or invalid model retrain failure report' USING ERRCODE = '42501';
    END IF;
    UPDATE public.model_retrain_jobs AS j SET
        job_state = 'failed', record_version = j.record_version + 1,
        finished_at = v_now, failure_code = p_failure_code
    WHERE j.job_id = p_job_id RETURNING * INTO v_job;
    INSERT INTO public.model_retrain_job_events (
        job_id, event_seq, event_type, actor_user_id, worker_id, approval_id,
        approved_payload_hash, fencing_token, from_state, to_state, record_version, reason
    ) VALUES (
        v_job.job_id, v_job.record_version, 'failed', NULL, p_worker_id,
        v_job.approval_id, v_job.approved_payload_hash, p_fencing_token,
        CASE WHEN v_job.execution_started THEN 'running' ELSE 'claimed' END,
        'failed', v_job.record_version, 'Fenced worker reported a bounded failure before artifact registration.'
    );
    RETURN QUERY SELECT * FROM public.model_retrain_jobs AS j WHERE j.job_id = p_job_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.recover_expired_model_retrain_job(
    p_job_id UUID,
    p_expected_version INTEGER
)
RETURNS SETOF public.model_retrain_jobs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_job public.model_retrain_jobs%ROWTYPE;
    v_from TEXT;
    v_token BIGINT;
BEGIN
    SELECT * INTO v_job FROM public.model_retrain_jobs AS j
    WHERE j.job_id = p_job_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'model retrain job not found' USING ERRCODE = 'P0002'; END IF;
    IF v_job.record_version <> p_expected_version THEN
        RAISE EXCEPTION 'model retrain job version conflict' USING ERRCODE = '40001';
    END IF;
    IF v_job.job_state NOT IN ('claimed', 'running') OR v_job.lease_expires_at > v_now THEN
        RAISE EXCEPTION 'model retrain job lease is not recoverable' USING ERRCODE = '55000';
    END IF;
    v_from := v_job.job_state;
    v_token := v_job.fencing_token;
    IF v_from = 'claimed' THEN
        UPDATE public.model_retrain_jobs AS j SET
            job_state = 'queued', record_version = j.record_version + 1,
            worker_id = NULL, fencing_token = NULL, lease_expires_at = NULL,
            claimed_at = NULL
        WHERE j.job_id = p_job_id RETURNING * INTO v_job;
    ELSE
        UPDATE public.model_retrain_jobs AS j SET
            job_state = 'failed', record_version = j.record_version + 1,
            finished_at = v_now, failure_code = 'lease-expired-running'
        WHERE j.job_id = p_job_id RETURNING * INTO v_job;
    END IF;
    INSERT INTO public.model_retrain_job_events (
        job_id, event_seq, event_type, actor_user_id, worker_id, approval_id,
        approved_payload_hash, fencing_token, from_state, to_state, record_version, reason
    ) VALUES (
        v_job.job_id, v_job.record_version, 'lease-expired', NULL, 'system-recovery',
        v_job.approval_id, v_job.approved_payload_hash,
        v_token, v_from, v_job.job_state,
        v_job.record_version, 'Expired worker lease was fenced and recovered without artifact registration.'
    );
    RETURN QUERY SELECT * FROM public.model_retrain_jobs AS j WHERE j.job_id = p_job_id;
END;
$$;

REVOKE ALL ON FUNCTION public._guard_model_retrain_job_update()
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public._reject_model_retrain_job_delete()
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public._reject_model_retrain_job_event_mutation()
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public._model_retrain_validate_worker(TEXT, INTEGER)
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.claim_model_retrain_job(TEXT, UUID, INTEGER, INTEGER)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.heartbeat_model_retrain_job(TEXT, UUID, INTEGER, BIGINT, INTEGER)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.start_model_retrain_job(TEXT, UUID, INTEGER, BIGINT)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.fail_model_retrain_job(TEXT, UUID, INTEGER, BIGINT, TEXT)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.recover_expired_model_retrain_job(UUID, INTEGER)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_model_retrain_job(TEXT, UUID, INTEGER, INTEGER)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.heartbeat_model_retrain_job(TEXT, UUID, INTEGER, BIGINT, INTEGER)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.start_model_retrain_job(TEXT, UUID, INTEGER, BIGINT)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.fail_model_retrain_job(TEXT, UUID, INTEGER, BIGINT, TEXT)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.recover_expired_model_retrain_job(UUID, INTEGER)
    TO service_role;
-- canonical migration 20260802143000 (supabase/migrations/20260802_model_retrain_artifact_registration.sql)
-- ============================================================
-- Approval-bound model artifact registration.
-- Only the live fenced worker may bind an already-uploaded object from the
-- private models bucket. Registration does not evaluate or activate a model.
-- ============================================================

ALTER TABLE public.model_retrain_jobs
    DROP CONSTRAINT IF EXISTS model_retrain_jobs_job_state_check,
    DROP CONSTRAINT IF EXISTS model_retrain_jobs_artifact_written_check,
    DROP CONSTRAINT IF EXISTS model_retrain_jobs_artifact_uri_check,
    DROP CONSTRAINT IF EXISTS model_retrain_jobs_artifact_sha256_check,
    DROP CONSTRAINT IF EXISTS model_retrain_jobs_worker_state_check;

ALTER TABLE public.model_retrain_jobs
    ADD COLUMN IF NOT EXISTS artifact_size_bytes BIGINT NULL,
    ADD COLUMN IF NOT EXISTS artifact_media_type TEXT NULL,
    ADD COLUMN IF NOT EXISTS artifact_registered_at TIMESTAMPTZ NULL;

ALTER TABLE public.model_retrain_jobs
    ADD CONSTRAINT model_retrain_jobs_job_state_check CHECK (
        job_state IN ('queued', 'claimed', 'running', 'artifact-registered', 'failed')
    ),
    ADD CONSTRAINT model_retrain_jobs_artifact_identity_check CHECK (
        (job_state <> 'artifact-registered' AND artifact_written = FALSE
            AND artifact_uri IS NULL AND artifact_sha256 IS NULL
            AND artifact_size_bytes IS NULL AND artifact_media_type IS NULL
            AND artifact_registered_at IS NULL)
        OR (job_state = 'artifact-registered' AND artifact_written = TRUE
            AND artifact_sha256 ~ '^[0-9a-f]{64}$'
            AND artifact_uri IN (
                'models://retrain/' || job_id::TEXT || '/' || artifact_sha256 || '.joblib',
                'models://retrain/' || job_id::TEXT || '/' || artifact_sha256 || '.pkl',
                'models://retrain/' || job_id::TEXT || '/' || artifact_sha256 || '.bin'
            )
            AND artifact_size_bytes BETWEEN 1 AND 104857600
            AND artifact_media_type IN (
                'application/octet-stream',
                'application/x-python-serialized-object'
            )
            AND artifact_registered_at IS NOT NULL)
    ),
    ADD CONSTRAINT model_retrain_jobs_worker_state_check CHECK (
        (job_state = 'queued'
            AND worker_id IS NULL AND fencing_token IS NULL
            AND lease_expires_at IS NULL AND claimed_at IS NULL
            AND started_at IS NULL AND finished_at IS NULL
            AND failure_code IS NULL AND execution_started = FALSE)
        OR (job_state = 'claimed'
            AND worker_id IS NOT NULL AND fencing_token IS NOT NULL
            AND lease_expires_at IS NOT NULL AND claimed_at IS NOT NULL
            AND started_at IS NULL AND finished_at IS NULL
            AND failure_code IS NULL AND execution_started = FALSE)
        OR (job_state = 'running'
            AND worker_id IS NOT NULL AND fencing_token IS NOT NULL
            AND lease_expires_at IS NOT NULL AND claimed_at IS NOT NULL
            AND started_at IS NOT NULL AND finished_at IS NULL
            AND failure_code IS NULL AND execution_started = TRUE)
        OR (job_state = 'artifact-registered'
            AND worker_id IS NOT NULL AND fencing_token IS NOT NULL
            AND lease_expires_at IS NOT NULL AND claimed_at IS NOT NULL
            AND started_at IS NOT NULL AND finished_at IS NOT NULL
            AND failure_code IS NULL AND execution_started = TRUE)
        OR (job_state = 'failed'
            AND worker_id IS NOT NULL AND fencing_token IS NOT NULL
            AND lease_expires_at IS NOT NULL AND claimed_at IS NOT NULL
            AND finished_at IS NOT NULL AND failure_code IS NOT NULL)
    );

ALTER TABLE public.model_retrain_job_events
    DROP CONSTRAINT IF EXISTS model_retrain_job_events_event_type_check,
    DROP CONSTRAINT IF EXISTS model_retrain_job_events_state_check;
ALTER TABLE public.model_retrain_job_events
    ADD CONSTRAINT model_retrain_job_events_event_type_check CHECK (
        event_type IN (
            'queued', 'claimed', 'heartbeat', 'started', 'artifact-registered',
            'failed', 'lease-expired'
        )
    ),
    ADD CONSTRAINT model_retrain_job_events_state_check CHECK (
        (from_state IS NULL OR from_state IN (
            'queued', 'claimed', 'running', 'artifact-registered', 'failed'
        ))
        AND to_state IN ('queued', 'claimed', 'running', 'artifact-registered', 'failed')
        AND record_version >= 1
    );

CREATE TABLE IF NOT EXISTS public.model_retrain_artifacts (
    job_id UUID PRIMARY KEY
        REFERENCES public.model_retrain_jobs(job_id) ON DELETE RESTRICT,
    approval_id UUID NOT NULL
        REFERENCES public.model_retrain_approval_requests(approval_id) ON DELETE RESTRICT,
    approved_payload_hash TEXT NOT NULL CHECK (approved_payload_hash ~ '^[0-9a-f]{64}$'),
    object_name TEXT NOT NULL UNIQUE,
    artifact_uri TEXT NOT NULL UNIQUE CHECK (artifact_uri = 'models://' || object_name),
    artifact_sha256 TEXT NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
    artifact_size_bytes BIGINT NOT NULL CHECK (artifact_size_bytes BETWEEN 1 AND 104857600),
    artifact_media_type TEXT NOT NULL CHECK (artifact_media_type IN (
        'application/octet-stream',
        'application/x-python-serialized-object'
    )),
    worker_id TEXT NOT NULL CHECK (worker_id ~ '^[a-z0-9][a-z0-9._:-]{2,79}$'),
    fencing_token BIGINT NOT NULL CHECK (fencing_token >= 1),
    registered_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    authoritative_record BOOLEAN NOT NULL DEFAULT TRUE CHECK (authoritative_record = TRUE),
    CHECK (object_name IN (
        'retrain/' || job_id::TEXT || '/' || artifact_sha256 || '.joblib',
        'retrain/' || job_id::TEXT || '/' || artifact_sha256 || '.pkl',
        'retrain/' || job_id::TEXT || '/' || artifact_sha256 || '.bin'
    ))
);

ALTER TABLE public.model_retrain_artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.model_retrain_artifacts FORCE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.model_retrain_artifacts
    FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON TABLE public.model_retrain_artifacts TO service_role;

CREATE OR REPLACE FUNCTION public._guard_model_retrain_job_update()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF ROW(
        NEW.job_id, NEW.approval_id, NEW.dry_run_id,
        NEW.approved_payload_hash, NEW.submitted_by, NEW.requested_by,
        NEW.approved_by, NEW.execution_policy, NEW.submitted_at
    ) IS DISTINCT FROM ROW(
        OLD.job_id, OLD.approval_id, OLD.dry_run_id,
        OLD.approved_payload_hash, OLD.submitted_by, OLD.requested_by,
        OLD.approved_by, OLD.execution_policy, OLD.submitted_at
    ) THEN
        RAISE EXCEPTION 'model retrain job binding is immutable' USING ERRCODE = '55000';
    END IF;
    IF NEW.record_version <> OLD.record_version + 1
       OR NEW.authoritative_record IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'model retrain job update violates safety state' USING ERRCODE = '55000';
    END IF;
    IF NOT (
        (OLD.job_state = 'queued' AND NEW.job_state = 'claimed')
        OR (OLD.job_state = 'claimed' AND NEW.job_state IN ('claimed', 'queued', 'running', 'failed'))
        OR (OLD.job_state = 'running' AND NEW.job_state IN ('running', 'artifact-registered', 'failed'))
    ) THEN
        RAISE EXCEPTION 'model retrain job transition is invalid' USING ERRCODE = '55000';
    END IF;
    IF NEW.job_state = 'artifact-registered' THEN
        IF OLD.job_state <> 'running'
           OR OLD.artifact_written IS DISTINCT FROM FALSE
           OR NEW.artifact_written IS DISTINCT FROM TRUE
           OR NEW.artifact_uri IS NULL OR NEW.artifact_sha256 IS NULL
           OR NEW.artifact_size_bytes IS NULL OR NEW.artifact_media_type IS NULL
           OR NEW.artifact_registered_at IS NULL THEN
            RAISE EXCEPTION 'model retrain artifact transition is invalid' USING ERRCODE = '55000';
        END IF;
    ELSIF ROW(
        NEW.artifact_written, NEW.artifact_uri, NEW.artifact_sha256,
        NEW.artifact_size_bytes, NEW.artifact_media_type, NEW.artifact_registered_at
    ) IS DISTINCT FROM ROW(
        OLD.artifact_written, OLD.artifact_uri, OLD.artifact_sha256,
        OLD.artifact_size_bytes, OLD.artifact_media_type, OLD.artifact_registered_at
    ) THEN
        RAISE EXCEPTION 'model retrain artifact identity is immutable' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public._reject_model_retrain_artifact_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RAISE EXCEPTION 'model retrain artifact registrations are immutable' USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_model_retrain_artifacts_immutable ON public.model_retrain_artifacts;
CREATE TRIGGER trg_model_retrain_artifacts_immutable
    BEFORE UPDATE OR DELETE ON public.model_retrain_artifacts
    FOR EACH ROW EXECUTE FUNCTION public._reject_model_retrain_artifact_mutation();

CREATE OR REPLACE FUNCTION public.register_model_retrain_artifact(
    p_worker_id TEXT,
    p_job_id UUID,
    p_expected_version INTEGER,
    p_fencing_token BIGINT,
    p_object_name TEXT,
    p_artifact_sha256 TEXT,
    p_artifact_size_bytes BIGINT,
    p_artifact_media_type TEXT
)
RETURNS SETOF public.model_retrain_jobs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_job public.model_retrain_jobs%ROWTYPE;
    v_approval public.model_retrain_approval_requests%ROWTYPE;
    v_uri TEXT;
BEGIN
    IF p_worker_id IS NULL OR p_worker_id !~ '^[a-z0-9][a-z0-9._:-]{2,79}$'
       OR p_job_id IS NULL
       OR p_expected_version IS NULL OR p_expected_version < 1
       OR p_fencing_token IS NULL OR p_fencing_token < 1
       OR p_artifact_sha256 IS NULL OR p_artifact_sha256 !~ '^[0-9a-f]{64}$'
       OR p_artifact_size_bytes IS NULL OR p_artifact_size_bytes NOT BETWEEN 1 AND 104857600
       OR p_artifact_media_type IS NULL OR p_artifact_media_type NOT IN (
            'application/octet-stream',
            'application/x-python-serialized-object'
       )
       OR p_object_name IS NULL OR p_object_name NOT IN (
            'retrain/' || p_job_id::TEXT || '/' || p_artifact_sha256 || '.joblib',
            'retrain/' || p_job_id::TEXT || '/' || p_artifact_sha256 || '.pkl',
            'retrain/' || p_job_id::TEXT || '/' || p_artifact_sha256 || '.bin'
       ) THEN
        RAISE EXCEPTION 'invalid model retrain artifact registration' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_job FROM public.model_retrain_jobs AS j
    WHERE j.job_id = p_job_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'model retrain job not found' USING ERRCODE = 'P0002';
    END IF;
    IF v_job.record_version <> p_expected_version THEN
        RAISE EXCEPTION 'model retrain job version conflict' USING ERRCODE = '40001';
    END IF;
    IF v_job.job_state <> 'running'
       OR v_job.worker_id IS DISTINCT FROM p_worker_id
       OR v_job.fencing_token IS DISTINCT FROM p_fencing_token
       OR v_job.lease_expires_at <= v_now THEN
        RAISE EXCEPTION 'stale model retrain worker cannot register artifact' USING ERRCODE = '42501';
    END IF;

    SELECT * INTO v_approval FROM public.model_retrain_approval_requests AS a
    WHERE a.approval_id = v_job.approval_id FOR SHARE;
    IF v_approval.approval_status <> 'approved'
       OR v_approval.expires_at <= v_now
       OR v_approval.job_created IS DISTINCT FROM TRUE
       OR v_approval.approved_payload_hash IS DISTINCT FROM v_job.approved_payload_hash THEN
        RAISE EXCEPTION 'model retrain approval no longer authorizes artifact registration'
            USING ERRCODE = '42501';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM storage.buckets AS b
        JOIN storage.objects AS o ON o.bucket_id = b.id
        WHERE b.id = 'models' AND b.public IS FALSE AND o.name = p_object_name
    ) THEN
        RAISE EXCEPTION 'model retrain artifact object is absent from private storage'
            USING ERRCODE = '55000';
    END IF;

    v_uri := 'models://' || p_object_name;
    INSERT INTO public.model_retrain_artifacts (
        job_id, approval_id, approved_payload_hash, object_name, artifact_uri,
        artifact_sha256, artifact_size_bytes, artifact_media_type,
        worker_id, fencing_token, registered_at, authoritative_record
    ) VALUES (
        v_job.job_id, v_job.approval_id, v_job.approved_payload_hash,
        p_object_name, v_uri, p_artifact_sha256, p_artifact_size_bytes,
        p_artifact_media_type, p_worker_id, p_fencing_token, v_now, TRUE
    );

    UPDATE public.model_retrain_jobs AS j SET
        job_state = 'artifact-registered', record_version = j.record_version + 1,
        artifact_written = TRUE, artifact_uri = v_uri,
        artifact_sha256 = p_artifact_sha256,
        artifact_size_bytes = p_artifact_size_bytes,
        artifact_media_type = p_artifact_media_type,
        artifact_registered_at = v_now, finished_at = v_now
    WHERE j.job_id = p_job_id RETURNING * INTO v_job;

    INSERT INTO public.model_retrain_job_events (
        job_id, event_seq, event_type, actor_user_id, worker_id, approval_id,
        approved_payload_hash, fencing_token, from_state, to_state, record_version, reason
    ) VALUES (
        v_job.job_id, v_job.record_version, 'artifact-registered', NULL, p_worker_id,
        v_job.approval_id, v_job.approved_payload_hash, p_fencing_token,
        'running', 'artifact-registered', v_job.record_version,
        'Fenced worker bound an immutable artifact identity from private storage.'
    );
    RETURN QUERY SELECT * FROM public.model_retrain_jobs AS j WHERE j.job_id = p_job_id;
END;
$$;

REVOKE ALL ON FUNCTION public._guard_model_retrain_job_update()
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public._reject_model_retrain_artifact_mutation()
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.register_model_retrain_artifact(
    TEXT, UUID, INTEGER, BIGINT, TEXT, TEXT, BIGINT, TEXT
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.register_model_retrain_artifact(
    TEXT, UUID, INTEGER, BIGINT, TEXT, TEXT, BIGINT, TEXT
) TO service_role;
-- canonical migration 20260802144000 (supabase/migrations/20260802_model_retrain_evaluation_registration.sql)
-- ============================================================
-- Accepted model-evaluation registration.
-- This persists a sanitized verifier report bound to the registered artifact.
-- It deliberately cannot create trusted promotion evidence or activate a model.
-- ============================================================

ALTER TABLE public.model_retrain_jobs
    DROP CONSTRAINT IF EXISTS model_retrain_jobs_job_state_check,
    DROP CONSTRAINT IF EXISTS model_retrain_jobs_artifact_identity_check,
    DROP CONSTRAINT IF EXISTS model_retrain_jobs_worker_state_check;

ALTER TABLE public.model_retrain_jobs
    ADD COLUMN IF NOT EXISTS evaluation_recorded BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS acceptance_passed BOOLEAN NULL,
    ADD COLUMN IF NOT EXISTS evaluation_report_sha256 TEXT NULL,
    ADD COLUMN IF NOT EXISTS evaluator_id TEXT NULL,
    ADD COLUMN IF NOT EXISTS evaluated_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS promotion_eligible BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE public.model_retrain_jobs
    ADD CONSTRAINT model_retrain_jobs_job_state_check CHECK (
        job_state IN (
            'queued', 'claimed', 'running', 'artifact-registered',
            'evaluation-recorded', 'failed'
        )
    ),
    ADD CONSTRAINT model_retrain_jobs_artifact_identity_check CHECK (
        (job_state NOT IN ('artifact-registered', 'evaluation-recorded')
            AND artifact_written = FALSE
            AND artifact_uri IS NULL AND artifact_sha256 IS NULL
            AND artifact_size_bytes IS NULL AND artifact_media_type IS NULL
            AND artifact_registered_at IS NULL)
        OR (job_state IN ('artifact-registered', 'evaluation-recorded')
            AND artifact_written = TRUE
            AND artifact_sha256 ~ '^[0-9a-f]{64}$'
            AND artifact_uri IN (
                'models://retrain/' || job_id::TEXT || '/' || artifact_sha256 || '.joblib',
                'models://retrain/' || job_id::TEXT || '/' || artifact_sha256 || '.pkl',
                'models://retrain/' || job_id::TEXT || '/' || artifact_sha256 || '.bin'
            )
            AND artifact_size_bytes BETWEEN 1 AND 104857600
            AND artifact_media_type IN (
                'application/octet-stream',
                'application/x-python-serialized-object'
            )
            AND artifact_registered_at IS NOT NULL)
    ),
    ADD CONSTRAINT model_retrain_jobs_evaluation_identity_check CHECK (
        (job_state <> 'evaluation-recorded'
            AND evaluation_recorded = FALSE AND acceptance_passed IS NULL
            AND evaluation_report_sha256 IS NULL AND evaluator_id IS NULL
            AND evaluated_at IS NULL AND promotion_eligible = FALSE)
        OR (job_state = 'evaluation-recorded'
            AND evaluation_recorded = TRUE AND acceptance_passed = TRUE
            AND evaluation_report_sha256 ~ '^[0-9a-f]{64}$'
            AND evaluator_id ~ '^[a-z0-9][a-z0-9._:-]{2,79}$'
            AND evaluated_at IS NOT NULL AND promotion_eligible = FALSE)
    ),
    ADD CONSTRAINT model_retrain_jobs_worker_state_check CHECK (
        (job_state = 'queued'
            AND worker_id IS NULL AND fencing_token IS NULL
            AND lease_expires_at IS NULL AND claimed_at IS NULL
            AND started_at IS NULL AND finished_at IS NULL
            AND failure_code IS NULL AND execution_started = FALSE)
        OR (job_state = 'claimed'
            AND worker_id IS NOT NULL AND fencing_token IS NOT NULL
            AND lease_expires_at IS NOT NULL AND claimed_at IS NOT NULL
            AND started_at IS NULL AND finished_at IS NULL
            AND failure_code IS NULL AND execution_started = FALSE)
        OR (job_state = 'running'
            AND worker_id IS NOT NULL AND fencing_token IS NOT NULL
            AND lease_expires_at IS NOT NULL AND claimed_at IS NOT NULL
            AND started_at IS NOT NULL AND finished_at IS NULL
            AND failure_code IS NULL AND execution_started = TRUE)
        OR (job_state IN ('artifact-registered', 'evaluation-recorded')
            AND worker_id IS NOT NULL AND fencing_token IS NOT NULL
            AND lease_expires_at IS NOT NULL AND claimed_at IS NOT NULL
            AND started_at IS NOT NULL AND finished_at IS NOT NULL
            AND failure_code IS NULL AND execution_started = TRUE)
        OR (job_state = 'failed'
            AND worker_id IS NOT NULL AND fencing_token IS NOT NULL
            AND lease_expires_at IS NOT NULL AND claimed_at IS NOT NULL
            AND finished_at IS NOT NULL AND failure_code IS NOT NULL)
    );

ALTER TABLE public.model_retrain_job_events
    DROP CONSTRAINT IF EXISTS model_retrain_job_events_event_type_check,
    DROP CONSTRAINT IF EXISTS model_retrain_job_events_state_check,
    DROP CONSTRAINT IF EXISTS model_retrain_job_events_actor_check;
ALTER TABLE public.model_retrain_job_events
    ADD COLUMN IF NOT EXISTS evaluator_id TEXT NULL;
ALTER TABLE public.model_retrain_job_events
    ADD CONSTRAINT model_retrain_job_events_event_type_check CHECK (
        event_type IN (
            'queued', 'claimed', 'heartbeat', 'started', 'artifact-registered',
            'evaluation-recorded', 'failed', 'lease-expired'
        )
    ),
    ADD CONSTRAINT model_retrain_job_events_state_check CHECK (
        (from_state IS NULL OR from_state IN (
            'queued', 'claimed', 'running', 'artifact-registered',
            'evaluation-recorded', 'failed'
        ))
        AND to_state IN (
            'queued', 'claimed', 'running', 'artifact-registered',
            'evaluation-recorded', 'failed'
        )
        AND record_version >= 1
    ),
    ADD CONSTRAINT model_retrain_job_events_evaluator_check CHECK (
        evaluator_id IS NULL OR evaluator_id ~ '^[a-z0-9][a-z0-9._:-]{2,79}$'
    ),
    ADD CONSTRAINT model_retrain_job_events_actor_check CHECK (
        (event_type = 'queued'
            AND actor_user_id IS NOT NULL AND worker_id IS NULL
            AND fencing_token IS NULL AND evaluator_id IS NULL)
        OR (event_type = 'evaluation-recorded'
            AND actor_user_id IS NULL AND worker_id IS NULL
            AND fencing_token IS NULL AND evaluator_id IS NOT NULL)
        OR (event_type NOT IN ('queued', 'evaluation-recorded')
            AND actor_user_id IS NULL AND worker_id IS NOT NULL
            AND fencing_token IS NOT NULL AND evaluator_id IS NULL)
    );

CREATE TABLE IF NOT EXISTS public.model_retrain_evaluations (
    job_id UUID PRIMARY KEY
        REFERENCES public.model_retrain_jobs(job_id) ON DELETE RESTRICT,
    approval_id UUID NOT NULL
        REFERENCES public.model_retrain_approval_requests(approval_id) ON DELETE RESTRICT,
    approved_payload_hash TEXT NOT NULL CHECK (approved_payload_hash ~ '^[0-9a-f]{64}$'),
    evaluator_id TEXT NOT NULL CHECK (evaluator_id ~ '^[a-z0-9][a-z0-9._:-]{2,79}$'),
    model_id TEXT NOT NULL CHECK (model_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
    candidate_commit_sha TEXT NOT NULL CHECK (candidate_commit_sha ~ '^[0-9a-f]{40}$'),
    artifact_sha256 TEXT NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
    feature_columns_sha256 TEXT NOT NULL CHECK (feature_columns_sha256 ~ '^[0-9a-f]{64}$'),
    observations_sha256 TEXT NOT NULL CHECK (observations_sha256 ~ '^[0-9a-f]{64}$'),
    contract_id TEXT NOT NULL CHECK (contract_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
    contract_sha256 TEXT NOT NULL CHECK (contract_sha256 ~ '^[0-9a-f]{64}$'),
    observed_at TIMESTAMPTZ NOT NULL,
    evaluation_report_sha256 TEXT NOT NULL UNIQUE CHECK (
        evaluation_report_sha256 ~ '^[0-9a-f]{64}$'
    ),
    sanitized_report JSONB NOT NULL CHECK (
        jsonb_typeof(sanitized_report) = 'object'
        AND octet_length(sanitized_report::TEXT) <= 65536
    ),
    acceptance_passed BOOLEAN NOT NULL CHECK (acceptance_passed = TRUE),
    trusted_promotion_evidence BOOLEAN NOT NULL DEFAULT FALSE CHECK (
        trusted_promotion_evidence = FALSE
    ),
    promotion_eligible BOOLEAN NOT NULL DEFAULT FALSE CHECK (promotion_eligible = FALSE),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    authoritative_record BOOLEAN NOT NULL DEFAULT TRUE CHECK (authoritative_record = TRUE)
);

ALTER TABLE public.model_retrain_evaluations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.model_retrain_evaluations FORCE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.model_retrain_evaluations
    FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON TABLE public.model_retrain_evaluations TO service_role;

CREATE OR REPLACE FUNCTION public._guard_model_retrain_job_update()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF ROW(
        NEW.job_id, NEW.approval_id, NEW.dry_run_id,
        NEW.approved_payload_hash, NEW.submitted_by, NEW.requested_by,
        NEW.approved_by, NEW.execution_policy, NEW.submitted_at
    ) IS DISTINCT FROM ROW(
        OLD.job_id, OLD.approval_id, OLD.dry_run_id,
        OLD.approved_payload_hash, OLD.submitted_by, OLD.requested_by,
        OLD.approved_by, OLD.execution_policy, OLD.submitted_at
    ) THEN
        RAISE EXCEPTION 'model retrain job binding is immutable' USING ERRCODE = '55000';
    END IF;
    IF NEW.record_version <> OLD.record_version + 1
       OR NEW.authoritative_record IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'model retrain job update violates safety state' USING ERRCODE = '55000';
    END IF;
    IF NOT (
        (OLD.job_state = 'queued' AND NEW.job_state = 'claimed')
        OR (OLD.job_state = 'claimed' AND NEW.job_state IN ('claimed', 'queued', 'running', 'failed'))
        OR (OLD.job_state = 'running' AND NEW.job_state IN ('running', 'artifact-registered', 'failed'))
        OR (OLD.job_state = 'artifact-registered' AND NEW.job_state = 'evaluation-recorded')
    ) THEN
        RAISE EXCEPTION 'model retrain job transition is invalid' USING ERRCODE = '55000';
    END IF;
    IF NEW.job_state = 'artifact-registered' THEN
        IF OLD.job_state <> 'running'
           OR OLD.artifact_written IS DISTINCT FROM FALSE
           OR NEW.artifact_written IS DISTINCT FROM TRUE
           OR NEW.artifact_uri IS NULL OR NEW.artifact_sha256 IS NULL
           OR NEW.artifact_size_bytes IS NULL OR NEW.artifact_media_type IS NULL
           OR NEW.artifact_registered_at IS NULL THEN
            RAISE EXCEPTION 'model retrain artifact transition is invalid' USING ERRCODE = '55000';
        END IF;
    ELSIF ROW(
        NEW.artifact_written, NEW.artifact_uri, NEW.artifact_sha256,
        NEW.artifact_size_bytes, NEW.artifact_media_type, NEW.artifact_registered_at
    ) IS DISTINCT FROM ROW(
        OLD.artifact_written, OLD.artifact_uri, OLD.artifact_sha256,
        OLD.artifact_size_bytes, OLD.artifact_media_type, OLD.artifact_registered_at
    ) THEN
        RAISE EXCEPTION 'model retrain artifact identity is immutable' USING ERRCODE = '55000';
    END IF;
    IF NEW.job_state = 'evaluation-recorded' THEN
        IF OLD.job_state <> 'artifact-registered'
           OR OLD.evaluation_recorded IS DISTINCT FROM FALSE
           OR NEW.evaluation_recorded IS DISTINCT FROM TRUE
           OR NEW.acceptance_passed IS DISTINCT FROM TRUE
           OR NEW.evaluation_report_sha256 IS NULL
           OR NEW.evaluator_id IS NULL OR NEW.evaluated_at IS NULL
           OR NEW.promotion_eligible IS DISTINCT FROM FALSE THEN
            RAISE EXCEPTION 'model retrain evaluation transition is invalid' USING ERRCODE = '55000';
        END IF;
    ELSIF ROW(
        NEW.evaluation_recorded, NEW.acceptance_passed,
        NEW.evaluation_report_sha256, NEW.evaluator_id,
        NEW.evaluated_at, NEW.promotion_eligible
    ) IS DISTINCT FROM ROW(
        OLD.evaluation_recorded, OLD.acceptance_passed,
        OLD.evaluation_report_sha256, OLD.evaluator_id,
        OLD.evaluated_at, OLD.promotion_eligible
    ) THEN
        RAISE EXCEPTION 'model retrain evaluation identity is immutable' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public._reject_model_retrain_evaluation_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RAISE EXCEPTION 'model retrain evaluations are immutable' USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_model_retrain_evaluations_immutable ON public.model_retrain_evaluations;
CREATE TRIGGER trg_model_retrain_evaluations_immutable
    BEFORE UPDATE OR DELETE ON public.model_retrain_evaluations
    FOR EACH ROW EXECUTE FUNCTION public._reject_model_retrain_evaluation_mutation();

CREATE OR REPLACE FUNCTION public.register_model_retrain_accepted_evaluation(
    p_evaluator_id TEXT,
    p_job_id UUID,
    p_expected_version INTEGER,
    p_sanitized_report JSONB
)
RETURNS SETOF public.model_retrain_jobs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_job public.model_retrain_jobs%ROWTYPE;
    v_approval public.model_retrain_approval_requests%ROWTYPE;
    v_contract JSONB;
    v_evidence JSONB;
    v_checks JSONB;
    v_observed_at TIMESTAMPTZ;
    v_report_sha256 TEXT;
BEGIN
    IF p_evaluator_id IS NULL
       OR p_evaluator_id !~ '^[a-z0-9][a-z0-9._:-]{2,79}$'
       OR p_job_id IS NULL
       OR p_expected_version IS NULL OR p_expected_version < 1
       OR p_sanitized_report IS NULL
       OR jsonb_typeof(p_sanitized_report) <> 'object'
       OR octet_length(p_sanitized_report::TEXT) > 65536 THEN
        RAISE EXCEPTION 'invalid model retrain evaluation registration' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_job FROM public.model_retrain_jobs AS j
    WHERE j.job_id = p_job_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'model retrain job not found' USING ERRCODE = 'P0002';
    END IF;
    IF v_job.record_version <> p_expected_version THEN
        RAISE EXCEPTION 'model retrain job version conflict' USING ERRCODE = '40001';
    END IF;
    IF v_job.job_state <> 'artifact-registered'
       OR v_job.artifact_written IS DISTINCT FROM TRUE
       OR v_job.artifact_sha256 IS NULL THEN
        RAISE EXCEPTION 'model retrain job has no registrable artifact evaluation'
            USING ERRCODE = '55000';
    END IF;

    SELECT * INTO v_approval FROM public.model_retrain_approval_requests AS a
    WHERE a.approval_id = v_job.approval_id FOR SHARE;
    IF v_approval.approval_status <> 'approved'
       OR v_approval.expires_at <= v_now
       OR v_approval.job_created IS DISTINCT FROM TRUE
       OR v_approval.approved_payload_hash IS DISTINCT FROM v_job.approved_payload_hash THEN
        RAISE EXCEPTION 'model retrain approval no longer authorizes evaluation registration'
            USING ERRCODE = '42501';
    END IF;

    IF NOT (p_sanitized_report ?& ARRAY[
        'report_schema', 'schema_version', 'success', 'verdict', 'verdict_reason',
        'accepted', 'acceptance_required', 'evaluated_commit_sha', 'contract',
        'evidence', 'blockers', 'checks', 'failure_codes'
    ]) OR p_sanitized_report - ARRAY[
        'report_schema', 'schema_version', 'success', 'verdict', 'verdict_reason',
        'accepted', 'acceptance_required', 'evaluated_commit_sha', 'contract',
        'evidence', 'blockers', 'checks', 'failure_codes'
    ] <> '{}'::JSONB THEN
        RAISE EXCEPTION 'model acceptance report schema is invalid' USING ERRCODE = '22023';
    END IF;
    IF (p_sanitized_report ->> 'report_schema')
            IS DISTINCT FROM 'model-acceptance-gate-report'
       OR (p_sanitized_report -> 'schema_version') IS DISTINCT FROM '1'::JSONB
       OR (p_sanitized_report -> 'success') IS DISTINCT FROM 'true'::JSONB
       OR (p_sanitized_report ->> 'verdict') IS DISTINCT FROM 'accepted'
       OR (p_sanitized_report ->> 'verdict_reason')
            IS DISTINCT FROM 'all-approved-thresholds-pass'
       OR (p_sanitized_report -> 'accepted') IS DISTINCT FROM 'true'::JSONB
       OR (p_sanitized_report -> 'acceptance_required') IS DISTINCT FROM 'true'::JSONB
       OR (p_sanitized_report -> 'blockers') IS DISTINCT FROM '[]'::JSONB
       OR (p_sanitized_report -> 'failure_codes') IS DISTINCT FROM '[]'::JSONB THEN
        RAISE EXCEPTION 'model acceptance report is not an accepted promotion-mode result'
            USING ERRCODE = '55000';
    END IF;

    v_contract := p_sanitized_report -> 'contract';
    v_evidence := p_sanitized_report -> 'evidence';
    v_checks := p_sanitized_report -> 'checks';
    IF jsonb_typeof(v_contract) IS DISTINCT FROM 'object'
       OR NOT (v_contract ?& ARRAY['contract_id', 'sha256', 'status'])
       OR v_contract - ARRAY['contract_id', 'sha256', 'status'] <> '{}'::JSONB
       OR (v_contract ->> 'status') IS DISTINCT FROM 'approved'
       OR COALESCE(
            (v_contract ->> 'contract_id') ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$',
            FALSE
       ) IS NOT TRUE
       OR COALESCE((v_contract ->> 'sha256') ~ '^[0-9a-f]{64}$', FALSE) IS NOT TRUE THEN
        RAISE EXCEPTION 'model acceptance contract projection is invalid' USING ERRCODE = '22023';
    END IF;
    IF jsonb_typeof(v_evidence) IS DISTINCT FROM 'object'
       OR NOT (v_evidence ?& ARRAY[
            'model_id', 'model_artifact_sha256', 'model_feature_columns_sha256',
            'observations_sha256', 'observed_at'
       ])
       OR v_evidence - ARRAY[
            'model_id', 'model_artifact_sha256', 'model_feature_columns_sha256',
            'observations_sha256', 'observed_at'
       ] <> '{}'::JSONB
       OR COALESCE(
            (v_evidence ->> 'model_id') ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$',
            FALSE
       ) IS NOT TRUE
       OR (v_evidence ->> 'model_artifact_sha256') IS DISTINCT FROM v_job.artifact_sha256
       OR COALESCE(
            (v_evidence ->> 'model_feature_columns_sha256') ~ '^[0-9a-f]{64}$',
            FALSE
       ) IS NOT TRUE
       OR COALESCE(
            (v_evidence ->> 'observations_sha256') ~ '^[0-9a-f]{64}$',
            FALSE
       ) IS NOT TRUE THEN
        RAISE EXCEPTION 'model acceptance evidence projection is invalid' USING ERRCODE = '22023';
    END IF;
    IF (p_sanitized_report ->> 'evaluated_commit_sha')
         IS DISTINCT FROM (v_approval.dry_run_payload ->> 'git_commit')
       OR COALESCE(
            (p_sanitized_report ->> 'evaluated_commit_sha') ~ '^[0-9a-f]{40}$',
            FALSE
       ) IS NOT TRUE THEN
        RAISE EXCEPTION 'model acceptance candidate commit binding is invalid' USING ERRCODE = '55000';
    END IF;
    IF jsonb_typeof(v_checks) IS DISTINCT FROM 'object'
       OR NOT (v_checks ?& ARRAY[
            'contract_schema', 'contract_approved', 'evidence_schema_and_binding',
            'metrics_against_thresholds', 'promotion_policy'
       ])
       OR v_checks - ARRAY[
            'contract_schema', 'contract_approved', 'evidence_schema_and_binding',
            'metrics_against_thresholds', 'promotion_policy'
       ] <> '{}'::JSONB
       OR EXISTS (
            SELECT 1 FROM jsonb_each(v_checks) AS entry
            WHERE entry.value <> 'true'::JSONB
       ) THEN
        RAISE EXCEPTION 'model acceptance checks are invalid' USING ERRCODE = '55000';
    END IF;

    BEGIN
        v_observed_at := (v_evidence ->> 'observed_at')::TIMESTAMPTZ;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'model acceptance observed timestamp is invalid' USING ERRCODE = '22023';
    END;
    IF v_observed_at IS NULL
       OR v_observed_at < v_now - INTERVAL '7 days'
       OR v_observed_at > v_now + INTERVAL '5 minutes' THEN
        RAISE EXCEPTION 'model acceptance report is outside the freshness window'
            USING ERRCODE = '55000';
    END IF;

    v_report_sha256 := encode(
        extensions.digest(convert_to(p_sanitized_report::TEXT, 'UTF8'), 'sha256'),
        'hex'
    );
    INSERT INTO public.model_retrain_evaluations (
        job_id, approval_id, approved_payload_hash, evaluator_id, model_id,
        candidate_commit_sha, artifact_sha256, feature_columns_sha256,
        observations_sha256, contract_id, contract_sha256, observed_at,
        evaluation_report_sha256, sanitized_report, acceptance_passed,
        trusted_promotion_evidence, promotion_eligible, recorded_at,
        authoritative_record
    ) VALUES (
        v_job.job_id, v_job.approval_id, v_job.approved_payload_hash,
        p_evaluator_id, v_evidence ->> 'model_id',
        p_sanitized_report ->> 'evaluated_commit_sha',
        v_evidence ->> 'model_artifact_sha256',
        v_evidence ->> 'model_feature_columns_sha256',
        v_evidence ->> 'observations_sha256',
        v_contract ->> 'contract_id', v_contract ->> 'sha256', v_observed_at,
        v_report_sha256, p_sanitized_report, TRUE, FALSE, FALSE, v_now, TRUE
    );

    UPDATE public.model_retrain_jobs AS j SET
        job_state = 'evaluation-recorded', record_version = j.record_version + 1,
        evaluation_recorded = TRUE, acceptance_passed = TRUE,
        evaluation_report_sha256 = v_report_sha256,
        evaluator_id = p_evaluator_id, evaluated_at = v_now,
        promotion_eligible = FALSE
    WHERE j.job_id = p_job_id RETURNING * INTO v_job;

    INSERT INTO public.model_retrain_job_events (
        job_id, event_seq, event_type, actor_user_id, worker_id, evaluator_id,
        approval_id, approved_payload_hash, fencing_token,
        from_state, to_state, record_version, reason
    ) VALUES (
        v_job.job_id, v_job.record_version, 'evaluation-recorded', NULL, NULL,
        p_evaluator_id, v_job.approval_id, v_job.approved_payload_hash, NULL,
        'artifact-registered', 'evaluation-recorded', v_job.record_version,
        'Accepted evaluation was recorded without creating trusted promotion evidence.'
    );
    RETURN QUERY SELECT * FROM public.model_retrain_jobs AS j WHERE j.job_id = p_job_id;
END;
$$;

REVOKE ALL ON FUNCTION public._guard_model_retrain_job_update()
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public._reject_model_retrain_evaluation_mutation()
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.register_model_retrain_accepted_evaluation(
    TEXT, UUID, INTEGER, JSONB
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.register_model_retrain_accepted_evaluation(
    TEXT, UUID, INTEGER, JSONB
) TO service_role;
-- canonical migration 20260802145000 (supabase/migrations/20260802_model_retrain_execution_bundle.sql)
-- ============================================================
-- Service-only approved retrain execution bundle.
-- This function returns immutable execution inputs only. It does not read a
-- caller-controlled path, start training, write an artifact, or activate a
-- model. The worker must still own the live lease/fencing token.
-- ============================================================

CREATE OR REPLACE FUNCTION public.get_model_retrain_execution_bundle(
    p_worker_id TEXT,
    p_job_id UUID,
    p_expected_version INTEGER,
    p_fencing_token BIGINT,
    p_candidate_commit_sha TEXT,
    p_active_model_id TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, extensions
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_job public.model_retrain_jobs%ROWTYPE;
    v_approval public.model_retrain_approval_requests%ROWTYPE;
    v_payload JSONB;
    v_item JSONB;
    v_selected TEXT[];
    v_removed TEXT[];
    v_train_start TEXT;
    v_train_end TEXT;
    v_validation_start TEXT;
    v_validation_end TEXT;
    v_contract_text TEXT;
    v_contract_sha256 TEXT;
BEGIN
    IF p_worker_id IS NULL
       OR p_worker_id !~ '^[a-z0-9][a-z0-9._:-]{2,79}$'
       OR p_job_id IS NULL
       OR p_expected_version IS NULL OR p_expected_version < 1
       OR p_fencing_token IS NULL OR p_fencing_token < 1
       OR p_candidate_commit_sha IS NULL
       OR p_candidate_commit_sha !~ '^[0-9a-f]{40}$'
       OR p_active_model_id IS NULL
       OR p_active_model_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$' THEN
        RAISE EXCEPTION 'invalid model retrain execution bundle request'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_job
    FROM public.model_retrain_jobs AS j
    WHERE j.job_id = p_job_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'model retrain job not found' USING ERRCODE = 'P0002';
    END IF;
    IF v_job.record_version <> p_expected_version THEN
        RAISE EXCEPTION 'model retrain job version conflict' USING ERRCODE = '40001';
    END IF;
    IF v_job.job_state NOT IN ('claimed', 'running')
       OR v_job.worker_id IS DISTINCT FROM p_worker_id
       OR v_job.fencing_token IS DISTINCT FROM p_fencing_token
       OR v_job.lease_expires_at IS NULL
       OR v_job.lease_expires_at <= v_now THEN
        RAISE EXCEPTION 'stale model retrain worker cannot read execution bundle'
            USING ERRCODE = '42501';
    END IF;

    PERFORM public._expire_model_retrain_approval_if_needed(v_job.approval_id);
    SELECT * INTO v_approval
    FROM public.model_retrain_approval_requests AS a
    WHERE a.approval_id = v_job.approval_id
    FOR SHARE;
    IF NOT FOUND
       OR v_approval.approval_status <> 'approved'
       OR v_approval.expires_at <= v_now
       OR v_approval.job_created IS DISTINCT FROM TRUE
       OR v_approval.approved_payload_hash IS DISTINCT FROM v_job.approved_payload_hash
       OR v_approval.dry_run_id IS DISTINCT FROM v_job.dry_run_id
       OR v_approval.requested_by IS DISTINCT FROM v_job.requested_by
       OR v_approval.approved_by IS DISTINCT FROM v_job.approved_by
       OR v_approval.execution_policy IS DISTINCT FROM v_job.execution_policy
       OR v_approval.execution_policy NOT IN ('staging-train', 'sandbox-train')
       OR NOT ('submit_approved_retrain' = ANY (v_approval.allowed_actions)) THEN
        RAISE EXCEPTION 'model retrain approval no longer authorizes execution bundle'
            USING ERRCODE = '42501';
    END IF;

    v_payload := v_approval.dry_run_payload;
    IF v_payload IS NULL
       OR jsonb_typeof(v_payload) <> 'object'
       OR v_payload->>'state' IS DISTINCT FROM 'preview-ready'
       OR v_payload->>'dry_run_id' IS DISTINCT FROM v_job.dry_run_id::TEXT
       OR v_payload->>'created_by' IS DISTINCT FROM v_job.requested_by::TEXT
       OR v_payload->>'target' IS DISTINCT FROM 'win'
       OR v_payload->>'model_type' IS DISTINCT FROM 'lightgbm'
       OR v_payload->>'git_commit' IS DISTINCT FROM p_candidate_commit_sha
       OR v_payload->>'active_model_id' IS DISTINCT FROM p_active_model_id
       OR COALESCE(v_payload->>'data_snapshot_id', '') !~ '^[0-9a-f]{64}$'
       OR v_payload->>'data_snapshot_id' = repeat('0', 64)
       OR COALESCE(v_payload->>'feature_contract_hash', '') !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(v_payload->'train_period') <> 'object'
       OR jsonb_typeof(v_payload->'validation_period') <> 'object'
       OR jsonb_typeof(v_payload->'selected_features') <> 'array'
       OR jsonb_typeof(v_payload->'removed_features') <> 'array'
       OR jsonb_typeof(v_payload->'safety_checks') <> 'array' THEN
        RAISE EXCEPTION 'approved model retrain payload is not executable'
            USING ERRCODE = '55000';
    END IF;

    v_train_start := v_payload#>>'{train_period,start}';
    v_train_end := v_payload#>>'{train_period,end}';
    v_validation_start := v_payload#>>'{validation_period,start}';
    v_validation_end := v_payload#>>'{validation_period,end}';
    IF COALESCE(v_train_start, '') !~ '^[0-9]{8}$'
       OR COALESCE(v_train_end, '') !~ '^[0-9]{8}$'
       OR COALESCE(v_validation_start, '') !~ '^[0-9]{8}$'
       OR COALESCE(v_validation_end, '') !~ '^[0-9]{8}$' THEN
        RAISE EXCEPTION 'approved model retrain periods are invalid'
            USING ERRCODE = '55000';
    END IF;
    IF to_char(to_date(v_train_start, 'YYYYMMDD'), 'YYYYMMDD') <> v_train_start
       OR to_char(to_date(v_train_end, 'YYYYMMDD'), 'YYYYMMDD') <> v_train_end
       OR to_char(to_date(v_validation_start, 'YYYYMMDD'), 'YYYYMMDD') <> v_validation_start
       OR to_char(to_date(v_validation_end, 'YYYYMMDD'), 'YYYYMMDD') <> v_validation_end
       OR v_train_start > v_train_end
       OR v_train_end >= v_validation_start
       OR v_validation_start > v_validation_end THEN
        RAISE EXCEPTION 'approved model retrain periods are invalid'
            USING ERRCODE = '55000';
    END IF;

    IF jsonb_array_length(v_payload->'selected_features') NOT BETWEEN 1 AND 2048
       OR jsonb_array_length(v_payload->'removed_features') > 2048 THEN
        RAISE EXCEPTION 'approved model retrain feature count is invalid'
            USING ERRCODE = '55000';
    END IF;
    FOR v_item IN SELECT value FROM jsonb_array_elements(v_payload->'selected_features') LOOP
        IF jsonb_typeof(v_item) <> 'string'
           OR (v_item #>> '{}') !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$' THEN
            RAISE EXCEPTION 'approved model retrain selected feature is invalid'
                USING ERRCODE = '55000';
        END IF;
    END LOOP;
    FOR v_item IN SELECT value FROM jsonb_array_elements(v_payload->'removed_features') LOOP
        IF jsonb_typeof(v_item) <> 'string'
           OR (v_item #>> '{}') !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$' THEN
            RAISE EXCEPTION 'approved model retrain removed feature is invalid'
                USING ERRCODE = '55000';
        END IF;
    END LOOP;
    SELECT array_agg(value #>> '{}' ORDER BY ordinal)
    INTO v_selected
    FROM jsonb_array_elements(v_payload->'selected_features')
        WITH ORDINALITY AS selected(value, ordinal);
    SELECT COALESCE(array_agg(value #>> '{}' ORDER BY ordinal), ARRAY[]::TEXT[])
    INTO v_removed
    FROM jsonb_array_elements(v_payload->'removed_features')
        WITH ORDINALITY AS removed(value, ordinal);
    IF cardinality(v_selected) <> (
            SELECT count(DISTINCT feature) FROM unnest(v_selected) AS features(feature)
       )
       OR cardinality(v_removed) <> (
            SELECT count(DISTINCT feature) FROM unnest(v_removed) AS features(feature)
       )
       OR NOT (v_removed <@ v_selected)
       OR cardinality(v_selected) - cardinality(v_removed) < 1 THEN
        RAISE EXCEPTION 'approved model retrain feature binding is invalid'
            USING ERRCODE = '55000';
    END IF;
    IF COALESCE(v_payload->>'feature_count', '') !~ '^[0-9]{1,4}$' THEN
        RAISE EXCEPTION 'approved model retrain feature binding is invalid'
            USING ERRCODE = '55000';
    END IF;
    IF (v_payload->>'feature_count')::INTEGER
            <> cardinality(v_selected) - cardinality(v_removed) THEN
        RAISE EXCEPTION 'approved model retrain feature binding is invalid'
            USING ERRCODE = '55000';
    END IF;

    FOR v_item IN SELECT value FROM jsonb_array_elements(v_payload->'safety_checks') LOOP
        IF jsonb_typeof(v_item) <> 'object'
           OR COALESCE(v_item->>'key', '') !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
           OR v_item->>'status' IS DISTINCT FROM 'pass' THEN
            RAISE EXCEPTION 'approved model retrain safety checks are not all passing'
                USING ERRCODE = '55000';
        END IF;
    END LOOP;
    IF NOT (v_payload->'safety_checks' @> '[{"key":"future_field_exclusion","status":"pass"}]'::JSONB)
       OR NOT (v_payload->'safety_checks' @> '[{"key":"out_of_time_split","status":"pass"}]'::JSONB)
       OR NOT (v_payload->'safety_checks' @> '[{"key":"active_model_immutable","status":"pass"}]'::JSONB)
       OR NOT (v_payload->'safety_checks' @> '[{"key":"production_write_blocked","status":"pass"}]'::JSONB)
       OR NOT (v_payload->'safety_checks' @> '[{"key":"path_input_rejected","status":"pass"}]'::JSONB)
       OR NOT (v_payload->'safety_checks' @> '[{"key":"data_snapshot_bound","status":"pass"}]'::JSONB)
       OR NOT (v_payload->'safety_checks' @> '[{"key":"candidate_commit_bound","status":"pass"}]'::JSONB) THEN
        RAISE EXCEPTION 'approved model retrain required safety check is missing'
            USING ERRCODE = '55000';
    END IF;

    v_contract_text := '{"model_type":' || to_jsonb(v_payload->>'model_type')::TEXT
        || ',"removed_features":[' || COALESCE((
            SELECT string_agg(to_jsonb(feature)::TEXT, ',' ORDER BY feature)
            FROM unnest(v_removed) AS removed_features(feature)
        ), '') || '],"selected_features":[' || COALESCE((
            SELECT string_agg(to_jsonb(feature)::TEXT, ',' ORDER BY feature)
            FROM unnest(v_selected) AS selected_features(feature)
        ), '') || '],"target":' || to_jsonb(v_payload->>'target')::TEXT || '}';
    v_contract_sha256 := encode(
        extensions.digest(convert_to(v_contract_text, 'UTF8'), 'sha256'),
        'hex'
    );
    IF v_contract_sha256 IS DISTINCT FROM v_payload->>'feature_contract_hash' THEN
        RAISE EXCEPTION 'approved model retrain feature contract hash mismatch'
            USING ERRCODE = '55000';
    END IF;

    RETURN jsonb_build_object(
        'schema_version', 1,
        'job_id', v_job.job_id,
        'approval_id', v_job.approval_id,
        'dry_run_id', v_job.dry_run_id,
        'approved_payload_hash', v_job.approved_payload_hash,
        'worker_id', v_job.worker_id,
        'fencing_token', v_job.fencing_token,
        'record_version', v_job.record_version,
        'lease_expires_at', v_job.lease_expires_at,
        'execution_policy', v_job.execution_policy,
        'target', 'win',
        'model_type', 'lightgbm',
        'train_period', jsonb_build_object('start', v_train_start, 'end', v_train_end),
        'validation_period', jsonb_build_object(
            'start', v_validation_start,
            'end', v_validation_end
        ),
        'selected_features', to_jsonb(v_selected),
        'removed_features', to_jsonb(v_removed),
        'data_snapshot_sha256', v_payload->>'data_snapshot_id',
        'feature_contract_sha256', v_contract_sha256,
        'candidate_commit_sha', p_candidate_commit_sha,
        'active_model_id', p_active_model_id,
        'training_parameters', jsonb_build_object(
            'force_sync', FALSE,
            'test_size', 0.2,
            'cv_folds', 5,
            'use_optuna', FALSE
        )
    );
END;
$$;

REVOKE ALL ON FUNCTION public.get_model_retrain_execution_bundle(
    TEXT, UUID, INTEGER, BIGINT, TEXT, TEXT
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.get_model_retrain_execution_bundle(
    TEXT, UUID, INTEGER, BIGINT, TEXT, TEXT
) TO service_role;
-- canonical migration 20260802146000 (supabase/migrations/20260802_model_retrain_orphan_reconciliation.sql)
-- ============================================================
-- Service-only expired-lease and orphan-artifact reconciliation boundary.
-- Only terminal failed jobs without an immutable artifact registration can
-- expose a cleanup candidate. Storage deletion remains outside PostgreSQL,
-- and every bounded scan is recorded after the observed outcome is rechecked.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.model_retrain_orphan_reconciliation_runs (
    run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reconciler_id TEXT NOT NULL CHECK (
        reconciler_id ~ '^[a-z0-9][a-z0-9._:-]{2,79}$'
    ),
    min_age_seconds INTEGER NOT NULL CHECK (
        min_age_seconds BETWEEN 3600 AND 604800
    ),
    requested_limit INTEGER NOT NULL CHECK (requested_limit BETWEEN 1 AND 50),
    candidate_count INTEGER NOT NULL CHECK (candidate_count BETWEEN 0 AND 50),
    deleted_count INTEGER NOT NULL CHECK (deleted_count BETWEEN 0 AND 50),
    not_found_count INTEGER NOT NULL CHECK (not_found_count BETWEEN 0 AND 50),
    failed_count INTEGER NOT NULL CHECK (failed_count BETWEEN 0 AND 50),
    observations JSONB NOT NULL CHECK (
        jsonb_typeof(observations) = 'array'
        AND jsonb_array_length(observations) = candidate_count
        AND octet_length(observations::TEXT) <= 65536
    ),
    successful BOOLEAN NOT NULL CHECK (successful = (failed_count = 0)),
    observed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    authoritative_record BOOLEAN NOT NULL DEFAULT TRUE CHECK (
        authoritative_record = TRUE
    ),
    CHECK (candidate_count = deleted_count + not_found_count + failed_count)
);

ALTER TABLE public.model_retrain_orphan_reconciliation_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.model_retrain_orphan_reconciliation_runs FORCE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.model_retrain_orphan_reconciliation_runs
    FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON TABLE public.model_retrain_orphan_reconciliation_runs TO service_role;

CREATE OR REPLACE FUNCTION public._reject_model_retrain_orphan_run_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RAISE EXCEPTION 'model retrain orphan reconciliation runs are immutable'
        USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_model_retrain_orphan_runs_immutable
    ON public.model_retrain_orphan_reconciliation_runs;
CREATE TRIGGER trg_model_retrain_orphan_runs_immutable
    BEFORE UPDATE OR DELETE ON public.model_retrain_orphan_reconciliation_runs
    FOR EACH ROW EXECUTE FUNCTION public._reject_model_retrain_orphan_run_mutation();

CREATE OR REPLACE FUNCTION public.list_expired_model_retrain_job_candidates(
    p_reconciler_id TEXT,
    p_limit INTEGER
)
RETURNS TABLE (
    job_id UUID,
    record_version INTEGER,
    job_state TEXT,
    lease_expires_at TIMESTAMPTZ
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF p_reconciler_id IS NULL
       OR p_reconciler_id !~ '^[a-z0-9][a-z0-9._:-]{2,79}$'
       OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 50 THEN
        RAISE EXCEPTION 'invalid model retrain expired lease scan'
            USING ERRCODE = '22023';
    END IF;
    RETURN QUERY
    SELECT j.job_id, j.record_version, j.job_state, j.lease_expires_at
    FROM public.model_retrain_jobs AS j
    WHERE j.job_state IN ('claimed', 'running')
      AND j.lease_expires_at <= clock_timestamp()
    ORDER BY j.lease_expires_at, j.job_id
    LIMIT p_limit;
END;
$$;

CREATE OR REPLACE FUNCTION public.list_model_retrain_orphan_candidates(
    p_reconciler_id TEXT,
    p_min_age_seconds INTEGER,
    p_limit INTEGER
)
RETURNS TABLE (
    job_id UUID,
    record_version INTEGER,
    failure_code TEXT,
    object_name TEXT,
    artifact_sha256 TEXT,
    storage_created_at TIMESTAMPTZ,
    cleanup_token TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF p_reconciler_id IS NULL
       OR p_reconciler_id !~ '^[a-z0-9][a-z0-9._:-]{2,79}$'
       OR p_min_age_seconds IS NULL
       OR p_min_age_seconds NOT BETWEEN 3600 AND 604800
       OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 50 THEN
        RAISE EXCEPTION 'invalid model retrain orphan scan' USING ERRCODE = '22023';
    END IF;
    RETURN QUERY
    WITH eligible_objects AS (
        SELECT
            o.name,
            o.created_at,
            substring(
                o.name FROM '^retrain/([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})/'
            )::UUID AS parsed_job_id,
            split_part(split_part(o.name, '/', 3), '.', 1) AS parsed_sha256
        FROM storage.objects AS o
        WHERE o.bucket_id = 'models'
          AND o.name ~ '^retrain/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/[0-9a-f]{64}\.joblib$'
          AND o.created_at <= clock_timestamp() - make_interval(secs => p_min_age_seconds)
    )
    SELECT
        j.job_id,
        j.record_version,
        j.failure_code,
        o.name AS object_name,
        o.parsed_sha256 AS artifact_sha256,
        o.created_at AS storage_created_at,
        encode(
            extensions.digest(
                convert_to(
                    j.job_id::TEXT || '|' || o.name || '|' || o.parsed_sha256
                    || '|' || to_char(o.created_at AT TIME ZONE 'UTC',
                        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                    'UTF8'
                ),
                'sha256'
            ),
            'hex'
        ) AS cleanup_token
    FROM eligible_objects AS o
    JOIN public.model_retrain_jobs AS j ON j.job_id = o.parsed_job_id
    WHERE j.job_state = 'failed'
      AND j.artifact_written = FALSE
      AND j.artifact_uri IS NULL
      AND j.artifact_sha256 IS NULL
      AND j.finished_at IS NOT NULL
      AND j.finished_at <= clock_timestamp() - make_interval(secs => p_min_age_seconds)
      AND NOT EXISTS (
          SELECT 1 FROM public.model_retrain_artifacts AS a
          WHERE a.job_id = j.job_id OR a.object_name = o.name
      )
    ORDER BY o.created_at, o.name
    LIMIT p_limit;
END;
$$;

CREATE OR REPLACE FUNCTION public.record_model_retrain_orphan_reconciliation(
    p_reconciler_id TEXT,
    p_min_age_seconds INTEGER,
    p_limit INTEGER,
    p_observations JSONB
)
RETURNS SETOF public.model_retrain_orphan_reconciliation_runs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_item JSONB;
    v_job_id UUID;
    v_object_name TEXT;
    v_sha256 TEXT;
    v_storage_created_at TIMESTAMPTZ;
    v_expected_token TEXT;
    v_outcome TEXT;
    v_candidate_count INTEGER;
    v_deleted_count INTEGER := 0;
    v_not_found_count INTEGER := 0;
    v_failed_count INTEGER := 0;
    v_run public.model_retrain_orphan_reconciliation_runs%ROWTYPE;
BEGIN
    IF p_reconciler_id IS NULL
       OR p_reconciler_id !~ '^[a-z0-9][a-z0-9._:-]{2,79}$'
       OR p_min_age_seconds IS NULL
       OR p_min_age_seconds NOT BETWEEN 3600 AND 604800
       OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 50
       OR p_observations IS NULL
       OR jsonb_typeof(p_observations) <> 'array'
       OR jsonb_array_length(p_observations) > p_limit
       OR octet_length(p_observations::TEXT) > 65536 THEN
        RAISE EXCEPTION 'invalid model retrain orphan reconciliation report'
            USING ERRCODE = '22023';
    END IF;
    v_candidate_count := jsonb_array_length(p_observations);

    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements(p_observations) AS item
        GROUP BY item->>'object_name', item->>'cleanup_token'
        HAVING count(*) <> 1
    ) THEN
        RAISE EXCEPTION 'model retrain orphan observations contain duplicates'
            USING ERRCODE = '22023';
    END IF;

    IF v_candidate_count = 0 AND EXISTS (
        SELECT 1 FROM public.list_model_retrain_orphan_candidates(
            p_reconciler_id, p_min_age_seconds, 1
        )
    ) THEN
        RAISE EXCEPTION 'model retrain orphan reconciliation omitted candidates'
            USING ERRCODE = '55000';
    END IF;

    FOR v_item IN SELECT value FROM jsonb_array_elements(p_observations)
    LOOP
        IF jsonb_typeof(v_item) <> 'object'
           OR NOT (v_item ?& ARRAY[
                'job_id', 'object_name', 'artifact_sha256',
                'storage_created_at', 'cleanup_token', 'outcome'
           ])
           OR v_item - ARRAY[
                'job_id', 'object_name', 'artifact_sha256',
                'storage_created_at', 'cleanup_token', 'outcome'
           ] <> '{}'::JSONB THEN
            RAISE EXCEPTION 'model retrain orphan observation schema is invalid'
                USING ERRCODE = '22023';
        END IF;
        BEGIN
            v_job_id := (v_item->>'job_id')::UUID;
            v_storage_created_at := (v_item->>'storage_created_at')::TIMESTAMPTZ;
        EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'model retrain orphan observation identity is invalid'
                USING ERRCODE = '22023';
        END;
        v_object_name := v_item->>'object_name';
        v_sha256 := v_item->>'artifact_sha256';
        v_outcome := v_item->>'outcome';
        v_expected_token := encode(
            extensions.digest(
                convert_to(
                    v_job_id::TEXT || '|' || v_object_name || '|' || v_sha256
                    || '|' || to_char(v_storage_created_at AT TIME ZONE 'UTC',
                        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                    'UTF8'
                ),
                'sha256'
            ),
            'hex'
        );
        IF v_sha256 !~ '^[0-9a-f]{64}$'
           OR v_object_name IS DISTINCT FROM
                'retrain/' || v_job_id::TEXT || '/' || v_sha256 || '.joblib'
           OR (v_item->>'cleanup_token') IS DISTINCT FROM v_expected_token
           OR v_outcome NOT IN ('deleted', 'not-found', 'delete-failed')
           OR v_storage_created_at > v_now - make_interval(secs => p_min_age_seconds)
           OR NOT EXISTS (
                SELECT 1 FROM public.model_retrain_jobs AS j
                WHERE j.job_id = v_job_id
                  AND j.job_state = 'failed'
                  AND j.artifact_written = FALSE
                  AND j.artifact_uri IS NULL AND j.artifact_sha256 IS NULL
                  AND j.finished_at <= v_now - make_interval(secs => p_min_age_seconds)
           )
           OR EXISTS (
                SELECT 1 FROM public.model_retrain_artifacts AS a
                WHERE a.job_id = v_job_id OR a.object_name = v_object_name
           ) THEN
            RAISE EXCEPTION 'model retrain orphan observation binding is invalid'
                USING ERRCODE = '55000';
        END IF;

        IF v_outcome IN ('deleted', 'not-found') THEN
            IF EXISTS (
                SELECT 1 FROM storage.objects AS o
                WHERE o.bucket_id = 'models' AND o.name = v_object_name
            ) THEN
                RAISE EXCEPTION 'model retrain orphan object still exists'
                    USING ERRCODE = '55000';
            END IF;
            IF v_outcome = 'deleted' THEN
                v_deleted_count := v_deleted_count + 1;
            ELSE
                v_not_found_count := v_not_found_count + 1;
            END IF;
        ELSE
            IF NOT EXISTS (
                SELECT 1 FROM storage.objects AS o
                WHERE o.bucket_id = 'models' AND o.name = v_object_name
                  AND o.created_at = v_storage_created_at
            ) THEN
                RAISE EXCEPTION 'model retrain failed cleanup object is unavailable'
                    USING ERRCODE = '55000';
            END IF;
            v_failed_count := v_failed_count + 1;
        END IF;
    END LOOP;

    INSERT INTO public.model_retrain_orphan_reconciliation_runs (
        reconciler_id, min_age_seconds, requested_limit,
        candidate_count, deleted_count, not_found_count, failed_count,
        observations, successful, observed_at, authoritative_record
    ) VALUES (
        p_reconciler_id, p_min_age_seconds, p_limit,
        v_candidate_count, v_deleted_count, v_not_found_count, v_failed_count,
        p_observations, v_failed_count = 0, v_now, TRUE
    ) RETURNING * INTO v_run;
    RETURN QUERY SELECT v_run.*;
END;
$$;

REVOKE ALL ON FUNCTION public._reject_model_retrain_orphan_run_mutation()
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.list_expired_model_retrain_job_candidates(TEXT, INTEGER)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.list_model_retrain_orphan_candidates(TEXT, INTEGER, INTEGER)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.record_model_retrain_orphan_reconciliation(
    TEXT, INTEGER, INTEGER, JSONB
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.list_expired_model_retrain_job_candidates(TEXT, INTEGER)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.list_model_retrain_orphan_candidates(TEXT, INTEGER, INTEGER)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.record_model_retrain_orphan_reconciliation(
    TEXT, INTEGER, INTEGER, JSONB
) TO service_role;
-- canonical migration 20260802147000 (supabase/migrations/20260802_model_retrain_dispatch_queue.sql)
-- ============================================================
-- Service-only bounded dispatcher projection.
-- This is a read-only preliminary queue view. Claim/execution still requires
-- the existing CAS lease, fence, approval recheck, and execution bundle.
-- ============================================================

CREATE OR REPLACE FUNCTION public.list_dispatchable_model_retrain_jobs(
    p_dispatcher_id TEXT,
    p_execution_policy TEXT,
    p_candidate_commit_sha TEXT,
    p_active_model_id TEXT,
    p_limit INTEGER
)
RETURNS TABLE (
    job_id UUID,
    record_version INTEGER,
    execution_policy TEXT,
    data_snapshot_sha256 TEXT,
    candidate_commit_sha TEXT,
    active_model_id TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF p_dispatcher_id IS NULL
       OR p_dispatcher_id !~ '^[a-z0-9][a-z0-9._:-]{2,49}$'
       OR p_execution_policy NOT IN ('staging-train', 'sandbox-train')
       OR p_candidate_commit_sha IS NULL
       OR p_candidate_commit_sha !~ '^[0-9a-f]{40}$'
       OR p_candidate_commit_sha = repeat('0', 40)
       OR p_active_model_id IS NULL
       OR p_active_model_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
       OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 5 THEN
        RAISE EXCEPTION 'invalid model retrain dispatch scan' USING ERRCODE = '22023';
    END IF;

    RETURN QUERY
    SELECT
        j.job_id,
        j.record_version,
        j.execution_policy,
        a.dry_run_payload->>'data_snapshot_id' AS data_snapshot_sha256,
        a.dry_run_payload->>'git_commit' AS candidate_commit_sha,
        a.dry_run_payload->>'active_model_id' AS active_model_id
    FROM public.model_retrain_jobs AS j
    JOIN public.model_retrain_approval_requests AS a
      ON a.approval_id = j.approval_id
    WHERE j.job_state = 'queued'
      AND j.record_version >= 1
      AND j.execution_started = FALSE
      AND j.artifact_written = FALSE
      AND j.worker_id IS NULL AND j.fencing_token IS NULL
      AND j.lease_expires_at IS NULL
      AND j.execution_policy = p_execution_policy
      AND j.submitted_by = j.requested_by
      AND a.approval_status = 'approved'
      AND a.expires_at > clock_timestamp()
      AND a.job_created = TRUE
      AND a.execution_enabled = FALSE
      AND a.approved_payload_hash = j.approved_payload_hash
      AND a.execution_policy = j.execution_policy
      AND a.dry_run_payload->>'state' = 'preview-ready'
      AND a.dry_run_payload->>'target' = 'win'
      AND a.dry_run_payload->>'model_type' = 'lightgbm'
      AND a.dry_run_payload->>'git_commit' = p_candidate_commit_sha
      AND a.dry_run_payload->>'active_model_id' = p_active_model_id
      AND COALESCE(
          (a.dry_run_payload->>'data_snapshot_id') ~ '^[0-9a-f]{64}$',
          FALSE
      ) = TRUE
      AND a.dry_run_payload->>'data_snapshot_id' <> repeat('0', 64)
      AND a.dry_run_payload->'safety_checks' @>
          '[{"key":"future_field_exclusion","status":"pass"},
            {"key":"out_of_time_split","status":"pass"},
            {"key":"active_model_immutable","status":"pass"},
            {"key":"production_write_blocked","status":"pass"},
            {"key":"path_input_rejected","status":"pass"},
            {"key":"data_snapshot_bound","status":"pass"},
            {"key":"candidate_commit_bound","status":"pass"}]'::JSONB
    ORDER BY j.submitted_at, j.job_id
    LIMIT p_limit;
END;
$$;

REVOKE ALL ON FUNCTION public.list_dispatchable_model_retrain_jobs(
    TEXT, TEXT, TEXT, TEXT, INTEGER
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.list_dispatchable_model_retrain_jobs(
    TEXT, TEXT, TEXT, TEXT, INTEGER
) TO service_role;
-- canonical migration 20260802148000 (supabase/migrations/20260720_scrape_operational_outbox.sql)
-- Phase 3N: shared operational scrape outbox.
--
-- This migration is intentionally separate from the immutable Phase 3M
-- bootstrap manifest.  Apply it through the reviewed migration promotion
-- path after the Phase 3M bootstrap fingerprint has been verified.

CREATE SEQUENCE IF NOT EXISTS public.scrape_operational_worker_fencing_seq
    AS BIGINT START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE NO CYCLE;

CREATE TABLE IF NOT EXISTS public.scrape_operational_jobs (
    job_id UUID PRIMARY KEY,
    operation_id UUID NOT NULL UNIQUE,
    reservation_id UUID NOT NULL UNIQUE
        REFERENCES public.scrape_execution_reservations(reservation_id) ON DELETE RESTRICT,
    owner_user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    request_payload JSONB NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE CHECK (idempotency_key ~ '^[0-9a-f]{64}$'),
    reservation_fencing_token BIGINT NOT NULL CHECK (reservation_fencing_token >= 1),
    consume_receipt_hash TEXT NOT NULL UNIQUE CHECK (consume_receipt_hash ~ '^[0-9a-f]{64}$'),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'error')),
    progress JSONB NOT NULL DEFAULT '{}'::JSONB CHECK (jsonb_typeof(progress) = 'object'),
    result JSONB NULL CHECK (result IS NULL OR jsonb_typeof(result) = 'object'),
    error TEXT NULL CHECK (
        error IS NULL OR (char_length(error) BETWEEN 1 AND 500 AND error !~ '[[:cntrl:]]')
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (
        (status IN ('queued', 'running') AND result IS NULL AND error IS NULL)
        OR (status = 'completed' AND result IS NOT NULL AND error IS NULL)
        OR (status = 'error' AND result IS NULL AND error IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS scrape_operational_one_active_job_per_owner
    ON public.scrape_operational_jobs(owner_user_id)
    WHERE status IN ('queued', 'running');

CREATE TABLE IF NOT EXISTS public.scrape_operational_outbox (
    job_id UUID PRIMARY KEY
        REFERENCES public.scrape_operational_jobs(job_id) ON DELETE RESTRICT,
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'claimed', 'acknowledged', 'blocked')),
    worker_owner TEXT NULL CHECK (
        worker_owner IS NULL OR worker_owner ~ '^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$'
    ),
    lease_expires_at TIMESTAMPTZ NULL,
    fencing_token BIGINT NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 20),
    effect_receipt_hash TEXT NULL UNIQUE CHECK (
        effect_receipt_hash IS NULL OR effect_receipt_hash ~ '^[0-9a-f]{64}$'
    ),
    settlement_reason TEXT NULL CHECK (
        settlement_reason IS NULL OR (
            char_length(settlement_reason) BETWEEN 1 AND 500
            AND settlement_reason !~ '[[:cntrl:]]'
        )
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (
        (state = 'pending' AND worker_owner IS NULL AND lease_expires_at IS NULL
            AND effect_receipt_hash IS NULL AND settlement_reason IS NULL)
        OR (state = 'claimed' AND worker_owner IS NOT NULL AND lease_expires_at IS NOT NULL
            AND fencing_token >= 1 AND effect_receipt_hash IS NULL AND settlement_reason IS NULL)
        OR (state = 'acknowledged' AND worker_owner IS NULL AND lease_expires_at IS NULL
            AND effect_receipt_hash IS NOT NULL AND settlement_reason = 'effect-confirmed')
        OR (state = 'blocked' AND worker_owner IS NULL AND lease_expires_at IS NULL
            AND effect_receipt_hash IS NULL AND settlement_reason IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS scrape_operational_outbox_claimable
    ON public.scrape_operational_outbox(state, lease_expires_at, created_at)
    WHERE state IN ('pending', 'claimed');

ALTER TABLE public.scrape_operational_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scrape_operational_outbox ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.scrape_operational_jobs
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON TABLE public.scrape_operational_outbox
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.scrape_operational_worker_fencing_seq
    FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON TABLE public.scrape_operational_jobs TO service_role;
GRANT SELECT ON TABLE public.scrape_operational_outbox TO service_role;

CREATE OR REPLACE FUNCTION public.phase3n_operational_runtime_health()
RETURNS TABLE (ready BOOLEAN, schema_version INTEGER)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT
        to_regclass('public.scrape_operational_jobs') IS NOT NULL
        AND to_regclass('public.scrape_operational_outbox') IS NOT NULL,
        1;
$$;

CREATE OR REPLACE FUNCTION public.enqueue_scrape_operational_job(
    p_authorization_id UUID,
    p_reservation_id UUID,
    p_operation_id UUID,
    p_job_id UUID,
    p_review_id UUID,
    p_review_version INTEGER,
    p_owner_user_id UUID,
    p_execution_request_hash TEXT,
    p_expected_authorization_version INTEGER,
    p_consume_request_id UUID,
    p_request_payload JSONB,
    p_idempotency_key TEXT
)
RETURNS TABLE (
    mutation_code TEXT, reason TEXT, job JSONB,
    job_id UUID, operation_id UUID, owner_user_id UUID,
    request_hash TEXT, request_payload JSONB, idempotency_key TEXT,
    worker_owner TEXT, fencing_token BIGINT,
    lease_expires_at_epoch BIGINT, attempt_count INTEGER
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, extensions
AS $$
DECLARE
    v_res RECORD;
    v_consume RECORD;
    v_existing public.scrape_operational_jobs%ROWTYPE;
    v_expected_key TEXT;
BEGIN
    IF p_authorization_id IS NULL OR p_reservation_id IS NULL
       OR p_operation_id IS NULL OR p_job_id IS NULL OR p_review_id IS NULL
       OR p_review_version IS NULL OR p_review_version < 1
       OR p_owner_user_id IS NULL OR p_consume_request_id IS NULL
       OR p_expected_authorization_version IS NULL OR p_expected_authorization_version < 1
       OR p_execution_request_hash IS NULL OR p_execution_request_hash !~ '^[0-9a-f]{64}$'
       OR p_idempotency_key IS NULL OR p_idempotency_key !~ '^[0-9a-f]{64}$'
       OR p_request_payload IS NULL OR jsonb_typeof(p_request_payload) <> 'object'
       OR NOT (p_request_payload ?& ARRAY['start_date','end_date','force_rescrape','dry_run'])
       OR (p_request_payload - ARRAY['start_date','end_date','force_rescrape','dry_run']) <> '{}'::JSONB
       OR jsonb_typeof(p_request_payload->'start_date') <> 'string'
       OR jsonb_typeof(p_request_payload->'end_date') <> 'string'
       OR jsonb_typeof(p_request_payload->'force_rescrape') <> 'boolean'
       OR jsonb_typeof(p_request_payload->'dry_run') <> 'boolean' THEN
        RAISE EXCEPTION 'invalid operational enqueue request' USING ERRCODE = '22023';
    END IF;

    v_expected_key := encode(
        extensions.digest(
            convert_to(
                concat_ws('|', 'scrape-operational-effect-v1',
                    p_operation_id::TEXT, p_job_id::TEXT, p_execution_request_hash),
                'UTF8'
            ),
            'sha256'
        ),
        'hex'
    );
    IF v_expected_key <> p_idempotency_key THEN
        RAISE EXCEPTION 'operational idempotency key mismatch' USING ERRCODE = '23505';
    END IF;

    PERFORM pg_advisory_xact_lock(hashtextextended(p_job_id::TEXT, 0));

    SELECT * INTO v_existing
    FROM public.scrape_operational_jobs AS j
    WHERE j.job_id = p_job_id
    FOR UPDATE;
    IF FOUND THEN
        IF v_existing.operation_id <> p_operation_id
           OR v_existing.owner_user_id <> p_owner_user_id
           OR v_existing.request_hash <> p_execution_request_hash
           OR v_existing.request_payload <> p_request_payload
           OR v_existing.idempotency_key <> p_idempotency_key
           OR v_existing.reservation_id <> p_reservation_id THEN
            RETURN QUERY SELECT 'conflict', 'job-id-binding-conflict', NULL::JSONB,
                NULL::UUID, NULL::UUID, NULL::UUID, NULL::TEXT, NULL::JSONB,
                NULL::TEXT, NULL::TEXT, NULL::BIGINT, NULL::BIGINT, NULL::INTEGER;
            RETURN;
        END IF;
        RETURN QUERY SELECT 'duplicate', NULL::TEXT, to_jsonb(v_existing),
            NULL::UUID, NULL::UUID, NULL::UUID, NULL::TEXT, NULL::JSONB,
            NULL::TEXT, NULL::TEXT, NULL::BIGINT, NULL::BIGINT, NULL::INTEGER;
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.scrape_operational_jobs AS j
        WHERE j.owner_user_id = p_owner_user_id AND j.status IN ('queued', 'running')
    ) THEN
        RETURN QUERY SELECT 'conflict', 'owner-active-job', NULL::JSONB,
            NULL::UUID, NULL::UUID, NULL::UUID, NULL::TEXT, NULL::JSONB,
            NULL::TEXT, NULL::TEXT, NULL::BIGINT, NULL::BIGINT, NULL::INTEGER;
        RETURN;
    END IF;

    SELECT * INTO v_res FROM public.reserve_scrape_execution(
        p_authorization_id, p_reservation_id, p_operation_id, p_job_id,
        p_review_id, p_review_version, p_owner_user_id,
        p_execution_request_hash, p_expected_authorization_version, 300
    );
    SELECT * INTO v_consume FROM public.consume_scrape_execution_reservation(
        p_reservation_id, v_res.version, p_consume_request_id,
        p_operation_id, p_job_id, p_review_id, p_review_version,
        p_owner_user_id, p_execution_request_hash
    );
    IF v_consume.status <> 'consumed'
       OR v_consume.consume_receipt_hash IS NULL
       OR v_consume.fencing_token <> v_res.fencing_token THEN
        RAISE EXCEPTION 'execution reservation was not consumed' USING ERRCODE = '55000';
    END IF;

    INSERT INTO public.scrape_operational_jobs (
        job_id, operation_id, reservation_id, owner_user_id,
        request_hash, request_payload, idempotency_key,
        reservation_fencing_token, consume_receipt_hash, status
    ) VALUES (
        p_job_id, p_operation_id, p_reservation_id, p_owner_user_id,
        p_execution_request_hash, p_request_payload, p_idempotency_key,
        v_consume.fencing_token, v_consume.consume_receipt_hash, 'queued'
    ) RETURNING * INTO v_existing;

    INSERT INTO public.scrape_operational_outbox(job_id, state)
    VALUES (p_job_id, 'pending');

    RETURN QUERY SELECT 'applied', NULL::TEXT, to_jsonb(v_existing),
        NULL::UUID, NULL::UUID, NULL::UUID, NULL::TEXT, NULL::JSONB,
        NULL::TEXT, NULL::TEXT, NULL::BIGINT, NULL::BIGINT, NULL::INTEGER;
END;
$$;

CREATE OR REPLACE FUNCTION public.claim_scrape_operational_outbox(
    p_worker_owner TEXT,
    p_lease_seconds INTEGER,
    p_max_attempts INTEGER
)
RETURNS TABLE (
    mutation_code TEXT, reason TEXT, job JSONB,
    job_id UUID, operation_id UUID, owner_user_id UUID,
    request_hash TEXT, request_payload JSONB, idempotency_key TEXT,
    worker_owner TEXT, fencing_token BIGINT,
    lease_expires_at_epoch BIGINT, attempt_count INTEGER
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_job public.scrape_operational_jobs%ROWTYPE;
    v_outbox public.scrape_operational_outbox%ROWTYPE;
    v_now TIMESTAMPTZ := clock_timestamp();
BEGIN
    IF p_worker_owner IS NULL
       OR p_worker_owner !~ '^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$'
       OR p_lease_seconds IS NULL OR p_lease_seconds NOT BETWEEN 5 AND 300
       OR p_max_attempts IS NULL OR p_max_attempts NOT BETWEEN 1 AND 20 THEN
        RAISE EXCEPTION 'invalid operational claim request' USING ERRCODE = '22023';
    END IF;

    -- Materialize one exhausted crash loop before looking for claimable work.
    -- This terminal transition releases the per-owner active-job lock instead
    -- of leaving an unclaimable running row forever.
    SELECT o.* INTO v_outbox
    FROM public.scrape_operational_outbox AS o
    WHERE o.state = 'claimed' AND o.lease_expires_at <= v_now
      AND o.attempt_count >= p_max_attempts
    ORDER BY o.created_at, o.job_id
    FOR UPDATE SKIP LOCKED
    LIMIT 1;
    IF FOUND THEN
        UPDATE public.scrape_operational_outbox AS o
        SET state = 'blocked', worker_owner = NULL, lease_expires_at = NULL,
            settlement_reason = 'max-attempts-exhausted', version = o.version + 1,
            updated_at = v_now
        WHERE o.job_id = v_outbox.job_id;
        UPDATE public.scrape_operational_jobs AS j
        SET status = 'error', result = NULL, error = 'max-attempts-exhausted',
            updated_at = v_now
        WHERE j.job_id = v_outbox.job_id;
    END IF;

    SELECT o.* INTO v_outbox
    FROM public.scrape_operational_outbox AS o
    WHERE (
        o.state = 'pending'
        OR (o.state = 'claimed' AND o.lease_expires_at <= v_now)
    ) AND o.attempt_count < p_max_attempts
    ORDER BY o.created_at, o.job_id
    FOR UPDATE SKIP LOCKED
    LIMIT 1;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'not_found', NULL::TEXT, NULL::JSONB,
            NULL::UUID, NULL::UUID, NULL::UUID, NULL::TEXT, NULL::JSONB,
            NULL::TEXT, NULL::TEXT, NULL::BIGINT, NULL::BIGINT, NULL::INTEGER;
        RETURN;
    END IF;

    UPDATE public.scrape_operational_outbox AS o
    SET state = 'claimed', worker_owner = p_worker_owner,
        lease_expires_at = v_now + make_interval(secs => p_lease_seconds),
        fencing_token = nextval('public.scrape_operational_worker_fencing_seq'::regclass),
        version = o.version + 1, attempt_count = o.attempt_count + 1,
        updated_at = v_now
    WHERE o.job_id = v_outbox.job_id
    RETURNING * INTO v_outbox;

    UPDATE public.scrape_operational_jobs AS j
    SET status = 'running', updated_at = v_now
    WHERE j.job_id = v_outbox.job_id
    RETURNING * INTO v_job;

    RETURN QUERY SELECT 'applied', NULL::TEXT, NULL::JSONB,
        v_job.job_id, v_job.operation_id, v_job.owner_user_id,
        v_job.request_hash, v_job.request_payload, v_job.idempotency_key,
        v_outbox.worker_owner, v_outbox.fencing_token,
        floor(extract(epoch FROM v_outbox.lease_expires_at))::BIGINT,
        v_outbox.attempt_count;
END;
$$;

CREATE OR REPLACE FUNCTION public.heartbeat_scrape_operational_outbox(
    p_job_id UUID,
    p_worker_owner TEXT,
    p_fencing_token BIGINT,
    p_lease_seconds INTEGER
)
RETURNS TABLE (
    mutation_code TEXT, reason TEXT, job JSONB,
    job_id UUID, operation_id UUID, owner_user_id UUID,
    request_hash TEXT, request_payload JSONB, idempotency_key TEXT,
    worker_owner TEXT, fencing_token BIGINT,
    lease_expires_at_epoch BIGINT, attempt_count INTEGER
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_row public.scrape_operational_outbox%ROWTYPE;
    v_now TIMESTAMPTZ := clock_timestamp();
BEGIN
    IF p_job_id IS NULL OR p_worker_owner IS NULL OR p_fencing_token IS NULL
       OR p_fencing_token < 1 OR p_lease_seconds NOT BETWEEN 5 AND 300 THEN
        RAISE EXCEPTION 'invalid operational heartbeat request' USING ERRCODE = '22023';
    END IF;
    UPDATE public.scrape_operational_outbox AS o
    SET lease_expires_at = v_now + make_interval(secs => p_lease_seconds),
        version = o.version + 1, updated_at = v_now
    WHERE o.job_id = p_job_id AND o.state = 'claimed'
      AND o.worker_owner = p_worker_owner AND o.fencing_token = p_fencing_token
      AND o.lease_expires_at > v_now
    RETURNING * INTO v_row;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'conflict', 'lease-lost', NULL::JSONB,
            NULL::UUID, NULL::UUID, NULL::UUID, NULL::TEXT, NULL::JSONB,
            NULL::TEXT, NULL::TEXT, NULL::BIGINT, NULL::BIGINT, NULL::INTEGER;
        RETURN;
    END IF;
    RETURN QUERY SELECT 'applied', NULL::TEXT, NULL::JSONB,
        NULL::UUID, NULL::UUID, NULL::UUID, NULL::TEXT, NULL::JSONB,
        NULL::TEXT, NULL::TEXT, v_row.fencing_token,
        floor(extract(epoch FROM v_row.lease_expires_at))::BIGINT,
        v_row.attempt_count;
END;
$$;

CREATE OR REPLACE FUNCTION public.settle_scrape_operational_outbox(
    p_job_id UUID,
    p_worker_owner TEXT,
    p_fencing_token BIGINT,
    p_outcome TEXT,
    p_result JSONB,
    p_error TEXT,
    p_effect_receipt_hash TEXT
)
RETURNS TABLE (
    mutation_code TEXT, reason TEXT, job JSONB,
    job_id UUID, operation_id UUID, owner_user_id UUID,
    request_hash TEXT, request_payload JSONB, idempotency_key TEXT,
    worker_owner TEXT, fencing_token BIGINT,
    lease_expires_at_epoch BIGINT, attempt_count INTEGER
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_outbox public.scrape_operational_outbox%ROWTYPE;
    v_now TIMESTAMPTZ := clock_timestamp();
    v_error TEXT := regexp_replace(btrim(COALESCE(p_error, '')), '[[:space:]]+', ' ', 'g');
BEGIN
    IF p_job_id IS NULL OR p_worker_owner IS NULL OR p_fencing_token IS NULL
       OR p_fencing_token < 1 OR p_outcome NOT IN ('completed', 'error') THEN
        RAISE EXCEPTION 'invalid operational settlement request' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_outbox FROM public.scrape_operational_outbox AS o
    WHERE o.job_id = p_job_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'not_found', NULL::TEXT, NULL::JSONB,
            NULL::UUID, NULL::UUID, NULL::UUID, NULL::TEXT, NULL::JSONB,
            NULL::TEXT, NULL::TEXT, NULL::BIGINT, NULL::BIGINT, NULL::INTEGER;
        RETURN;
    END IF;
    IF v_outbox.state = 'acknowledged' THEN
        RETURN QUERY SELECT
            CASE WHEN v_outbox.effect_receipt_hash = p_effect_receipt_hash THEN 'duplicate' ELSE 'conflict' END,
            CASE WHEN v_outbox.effect_receipt_hash = p_effect_receipt_hash THEN NULL::TEXT ELSE 'effect-receipt-conflict' END,
            NULL::JSONB, NULL::UUID, NULL::UUID, NULL::UUID, NULL::TEXT,
            NULL::JSONB, NULL::TEXT, NULL::TEXT, NULL::BIGINT, NULL::BIGINT, NULL::INTEGER;
        RETURN;
    END IF;
    IF v_outbox.state <> 'claimed' OR v_outbox.worker_owner <> p_worker_owner
       OR v_outbox.fencing_token <> p_fencing_token OR v_outbox.lease_expires_at <= v_now THEN
        RETURN QUERY SELECT 'conflict', 'stale-worker-fence', NULL::JSONB,
            NULL::UUID, NULL::UUID, NULL::UUID, NULL::TEXT, NULL::JSONB,
            NULL::TEXT, NULL::TEXT, NULL::BIGINT, NULL::BIGINT, NULL::INTEGER;
        RETURN;
    END IF;

    IF p_outcome = 'completed' THEN
        IF p_result IS NULL OR jsonb_typeof(p_result) <> 'object'
           OR p_effect_receipt_hash IS NULL OR p_effect_receipt_hash !~ '^[0-9a-f]{64}$'
           OR p_error IS NOT NULL THEN
            RAISE EXCEPTION 'invalid completed settlement' USING ERRCODE = '22023';
        END IF;
        UPDATE public.scrape_operational_outbox AS o
        SET state = 'acknowledged', worker_owner = NULL, lease_expires_at = NULL,
            effect_receipt_hash = p_effect_receipt_hash,
            settlement_reason = 'effect-confirmed', version = o.version + 1,
            updated_at = v_now
        WHERE o.job_id = p_job_id;
        UPDATE public.scrape_operational_jobs AS j
        SET status = 'completed', result = p_result, error = NULL, updated_at = v_now
        WHERE j.job_id = p_job_id;
    ELSE
        IF p_result IS NOT NULL OR p_effect_receipt_hash IS NOT NULL
           OR char_length(v_error) NOT BETWEEN 1 AND 500 OR p_error ~ '[[:cntrl:]]' THEN
            RAISE EXCEPTION 'invalid error settlement' USING ERRCODE = '22023';
        END IF;
        UPDATE public.scrape_operational_outbox AS o
        SET state = 'blocked', worker_owner = NULL, lease_expires_at = NULL,
            settlement_reason = v_error, version = o.version + 1, updated_at = v_now
        WHERE o.job_id = p_job_id;
        UPDATE public.scrape_operational_jobs AS j
        SET status = 'error', result = NULL, error = v_error, updated_at = v_now
        WHERE j.job_id = p_job_id;
    END IF;
    RETURN QUERY SELECT 'applied', NULL::TEXT, NULL::JSONB,
        NULL::UUID, NULL::UUID, NULL::UUID, NULL::TEXT, NULL::JSONB,
        NULL::TEXT, NULL::TEXT, NULL::BIGINT, NULL::BIGINT, NULL::INTEGER;
END;
$$;

REVOKE ALL ON FUNCTION public.phase3n_operational_runtime_health()
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.enqueue_scrape_operational_job(UUID, UUID, UUID, UUID, UUID, INTEGER, UUID, TEXT, INTEGER, UUID, JSONB, TEXT)
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.claim_scrape_operational_outbox(TEXT, INTEGER, INTEGER)
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.heartbeat_scrape_operational_outbox(UUID, TEXT, BIGINT, INTEGER)
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.settle_scrape_operational_outbox(UUID, TEXT, BIGINT, TEXT, JSONB, TEXT, TEXT)
    FROM PUBLIC, anon, authenticated, service_role;

GRANT EXECUTE ON FUNCTION public.phase3n_operational_runtime_health() TO service_role;
GRANT EXECUTE ON FUNCTION public.enqueue_scrape_operational_job(UUID, UUID, UUID, UUID, UUID, INTEGER, UUID, TEXT, INTEGER, UUID, JSONB, TEXT)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.claim_scrape_operational_outbox(TEXT, INTEGER, INTEGER)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.heartbeat_scrape_operational_outbox(UUID, TEXT, BIGINT, INTEGER)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.settle_scrape_operational_outbox(UUID, TEXT, BIGINT, TEXT, JSONB, TEXT, TEXT)
    TO service_role;
-- canonical migration 20260802149000 (supabase/migrations/20260802_phase3n_observation_ha.sql)
-- Phase 3N: immutable model observations and shared HA maintenance jobs.
--
-- This migration is append-only. It does not alter the first nineteen
-- canonical migrations, enable a scheduler, or grant browser write access.
-- All timestamps representing receipt/recording are PostgreSQL server times.

CREATE SEQUENCE IF NOT EXISTS public.phase3n_ha_fencing_seq AS BIGINT;

CREATE TABLE IF NOT EXISTS public.phase3n_model_manifests (
    manifest_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key TEXT NOT NULL UNIQUE
        CHECK (char_length(idempotency_key) BETWEEN 16 AND 200 AND idempotency_key !~ '[[:cntrl:]]'),
    model_id TEXT NOT NULL CHECK (model_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
    model_version TEXT NOT NULL CHECK (model_version ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
    model_artifact_sha256 TEXT NOT NULL CHECK (model_artifact_sha256 ~ '^[0-9a-f]{64}$'),
    candidate_commit_sha TEXT NOT NULL CHECK (candidate_commit_sha ~ '^[0-9a-f]{40}$'),
    feature_manifest_sha256 TEXT NOT NULL CHECK (feature_manifest_sha256 ~ '^[0-9a-f]{64}$'),
    model_feature_columns TEXT[] NOT NULL CHECK (cardinality(model_feature_columns) BETWEEN 1 AND 2048),
    training_data_ended_at TIMESTAMPTZ NOT NULL,
    expanding_window_checks_passed BOOLEAN NOT NULL CHECK (expanding_window_checks_passed),
    source_environment TEXT NOT NULL CHECK (source_environment IN ('staging', 'production')),
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    registered_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT phase3n_model_manifest_identity UNIQUE (
        model_artifact_sha256, candidate_commit_sha, feature_manifest_sha256, source_environment
    )
);

CREATE TABLE IF NOT EXISTS public.phase3n_prediction_observations (
    observation_id UUID PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE
        CHECK (char_length(idempotency_key) BETWEEN 16 AND 200 AND idempotency_key !~ '[[:cntrl:]]'),
    manifest_id UUID NOT NULL REFERENCES public.phase3n_model_manifests(manifest_id),
    race_id TEXT NOT NULL CHECK (char_length(race_id) BETWEEN 6 AND 64 AND race_id !~ '[[:cntrl:]]'),
    horse_id TEXT NOT NULL CHECK (char_length(horse_id) BETWEEN 1 AND 128 AND horse_id !~ '[[:cntrl:]]'),
    horse_number INTEGER NOT NULL CHECK (horse_number BETWEEN 1 AND 99),
    race_date DATE NOT NULL,
    prediction_at TIMESTAMPTZ NOT NULL,
    data_observed_at TIMESTAMPTZ NOT NULL,
    data_cutoff_at TIMESTAMPTZ NOT NULL,
    feature_values_sha256 TEXT NOT NULL CHECK (feature_values_sha256 ~ '^[0-9a-f]{64}$'),
    predicted_value DOUBLE PRECISION NOT NULL CHECK (
        predicted_value <> 'NaN'::DOUBLE PRECISION
        AND predicted_value NOT IN ('Infinity'::DOUBLE PRECISION, '-Infinity'::DOUBLE PRECISION)
    ),
    predicted_probability DOUBLE PRECISION NOT NULL
        CHECK (predicted_probability BETWEEN 0 AND 1),
    predicted_rank INTEGER NOT NULL CHECK (predicted_rank BETWEEN 1 AND 99),
    odds_at_prediction NUMERIC(12, 4) NULL CHECK (odds_at_prediction IS NULL OR odds_at_prediction > 0),
    recommendation TEXT NOT NULL CHECK (recommendation IN ('bet', 'pass', 'unavailable')),
    qualifying_bet BOOLEAN NOT NULL,
    wager_amount NUMERIC(14, 2) NOT NULL CHECK (
        wager_amount >= 0 AND wager_amount <> 'NaN'::NUMERIC AND wager_amount <> 'Infinity'::NUMERIC
    ),
    baseline_wager_amount NUMERIC(14, 2) NOT NULL CHECK (
        baseline_wager_amount >= 0 AND baseline_wager_amount <> 'NaN'::NUMERIC
        AND baseline_wager_amount <> 'Infinity'::NUMERIC
    ),
    latency_ms NUMERIC(12, 3) NOT NULL CHECK (
        latency_ms >= 0 AND latency_ms <> 'NaN'::NUMERIC AND latency_ms <> 'Infinity'::NUMERIC
    ),
    source_environment TEXT NOT NULL CHECK (source_environment IN ('staging', 'production')),
    leakage_violation_count INTEGER NOT NULL DEFAULT 0 CHECK (leakage_violation_count = 0),
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT phase3n_prediction_observation_identity UNIQUE (
        manifest_id, race_id, horse_id, prediction_at
    ),
    CONSTRAINT phase3n_prediction_temporal_order CHECK (
        data_observed_at <= data_cutoff_at
        AND data_cutoff_at <= prediction_at
        AND prediction_at = recorded_at
    ),
    CONSTRAINT phase3n_prediction_bet_shape CHECK (
        (qualifying_bet AND recommendation = 'bet' AND wager_amount > 0)
        OR (NOT qualifying_bet AND recommendation <> 'bet' AND wager_amount = 0)
    )
);

CREATE TABLE IF NOT EXISTS public.phase3n_result_observation_events (
    result_event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key TEXT NOT NULL UNIQUE
        CHECK (char_length(idempotency_key) BETWEEN 16 AND 200 AND idempotency_key !~ '[[:cntrl:]]'),
    observation_id UUID NOT NULL UNIQUE REFERENCES public.phase3n_prediction_observations(observation_id),
    settled_at TIMESTAMPTZ NOT NULL,
    y_true INTEGER NOT NULL CHECK (y_true IN (0, 1)),
    finish_order INTEGER NULL CHECK (finish_order IS NULL OR finish_order BETWEEN 1 AND 99),
    bet_outcome TEXT NOT NULL CHECK (bet_outcome IN ('won', 'lost', 'void', 'not-bet')),
    return_amount NUMERIC(14, 2) NOT NULL CHECK (
        return_amount >= 0 AND return_amount <> 'NaN'::NUMERIC AND return_amount <> 'Infinity'::NUMERIC
    ),
    baseline_return_amount NUMERIC(14, 2) NOT NULL CHECK (
        baseline_return_amount >= 0 AND baseline_return_amount <> 'NaN'::NUMERIC
        AND baseline_return_amount <> 'Infinity'::NUMERIC
    ),
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS public.phase3n_observation_ingest_attempts (
    attempt_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_kind TEXT NOT NULL CHECK (event_kind IN ('model-manifest', 'prediction', 'result')),
    idempotency_key TEXT NOT NULL CHECK (char_length(idempotency_key) BETWEEN 16 AND 200),
    outcome TEXT NOT NULL CHECK (outcome IN ('inserted', 'duplicate', 'conflict', 'rejected')),
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    failure_code TEXT NULL CHECK (
        failure_code IS NULL OR failure_code ~ '^[a-z0-9][a-z0-9-]{0,99}$'
    ),
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS phase3n_prediction_observations_report_idx
    ON public.phase3n_prediction_observations(source_environment, race_date, prediction_at);
CREATE INDEX IF NOT EXISTS phase3n_result_observation_events_settled_idx
    ON public.phase3n_result_observation_events(settled_at);
CREATE INDEX IF NOT EXISTS phase3n_observation_attempts_report_idx
    ON public.phase3n_observation_ingest_attempts(attempted_at, event_kind, outcome);

CREATE TABLE IF NOT EXISTS public.phase3n_ha_jobs (
    job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key TEXT NOT NULL UNIQUE
        CHECK (char_length(idempotency_key) BETWEEN 16 AND 200 AND idempotency_key !~ '[[:cntrl:]]'),
    job_kind TEXT NOT NULL CHECK (job_kind IN ('observation-result-reconcile', 'cache-rebuild', 'ha-contract-test')),
    request_payload JSONB NOT NULL CHECK (jsonb_typeof(request_payload) = 'object'),
    request_sha256 TEXT NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'leased', 'completed', 'failed')),
    worker_owner TEXT NULL CHECK (worker_owner IS NULL OR char_length(worker_owner) BETWEEN 3 AND 128),
    fencing_token BIGINT NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
    lease_expires_at TIMESTAMPTZ NULL,
    effect_key TEXT NULL CHECK (effect_key IS NULL OR char_length(effect_key) BETWEEN 16 AND 200),
    effect_sha256 TEXT NULL CHECK (effect_sha256 IS NULL OR effect_sha256 ~ '^[0-9a-f]{64}$'),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 20),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    completed_at TIMESTAMPTZ NULL,
    CONSTRAINT phase3n_ha_job_state_shape CHECK (
        (state = 'pending' AND worker_owner IS NULL AND lease_expires_at IS NULL
            AND effect_key IS NULL AND effect_sha256 IS NULL AND completed_at IS NULL)
        OR (state = 'leased' AND worker_owner IS NOT NULL AND lease_expires_at IS NOT NULL
            AND fencing_token >= 1 AND effect_key IS NULL AND effect_sha256 IS NULL AND completed_at IS NULL)
        OR (state = 'completed' AND worker_owner IS NULL AND lease_expires_at IS NULL
            AND effect_key IS NOT NULL AND effect_sha256 IS NOT NULL AND completed_at IS NOT NULL)
        OR (state = 'failed' AND worker_owner IS NULL AND lease_expires_at IS NULL
            AND effect_key IS NULL AND effect_sha256 IS NULL AND completed_at IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS public.phase3n_ha_effects (
    effect_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL UNIQUE REFERENCES public.phase3n_ha_jobs(job_id),
    effect_key TEXT NOT NULL UNIQUE CHECK (char_length(effect_key) BETWEEN 16 AND 200),
    fencing_token BIGINT NOT NULL CHECK (fencing_token >= 1),
    effect_sha256 TEXT NOT NULL CHECK (effect_sha256 ~ '^[0-9a-f]{64}$'),
    applied_by TEXT NOT NULL CHECK (char_length(applied_by) BETWEEN 3 AND 128),
    applied_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS public.phase3n_ha_events (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES public.phase3n_ha_jobs(job_id),
    event_type TEXT NOT NULL CHECK (
        event_type IN ('enqueued', 'duplicate-enqueue', 'claimed', 'reclaimed', 'heartbeat',
                       'effect-applied', 'stale-fence-rejected', 'duplicate-effect-rejected', 'failed')
    ),
    worker_owner TEXT NULL CHECK (worker_owner IS NULL OR char_length(worker_owner) BETWEEN 3 AND 128),
    fencing_token BIGINT NOT NULL CHECK (fencing_token >= 0),
    detail_code TEXT NOT NULL CHECK (detail_code ~ '^[a-z0-9][a-z0-9-]{0,99}$'),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS phase3n_ha_jobs_claim_idx
    ON public.phase3n_ha_jobs(state, lease_expires_at, created_at);
CREATE INDEX IF NOT EXISTS phase3n_ha_events_timeline_idx
    ON public.phase3n_ha_events(job_id, event_id);

CREATE OR REPLACE FUNCTION public._phase3n_reject_immutable_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    RAISE EXCEPTION 'phase3n-append-only-mutation-forbidden' USING ERRCODE = '55000';
END;
$$;

DO $phase3n_immutable_triggers$
DECLARE
    v_table TEXT;
BEGIN
    FOREACH v_table IN ARRAY ARRAY[
        'phase3n_model_manifests',
        'phase3n_prediction_observations',
        'phase3n_result_observation_events',
        'phase3n_observation_ingest_attempts',
        'phase3n_ha_effects',
        'phase3n_ha_events'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS phase3n_reject_mutation ON public.%I', v_table);
        EXECUTE format(
            'CREATE TRIGGER phase3n_reject_mutation BEFORE UPDATE OR DELETE ON public.%I '
            'FOR EACH ROW EXECUTE FUNCTION public._phase3n_reject_immutable_mutation()',
            v_table
        );
    END LOOP;
END
$phase3n_immutable_triggers$;

CREATE OR REPLACE FUNCTION public.register_phase3n_model_manifest(p_manifest JSONB)
RETURNS TABLE(mutation_code TEXT, returned_manifest_id UUID, registered_at TIMESTAMPTZ)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_id UUID;
    v_existing public.phase3n_model_manifests%ROWTYPE;
    v_columns TEXT[];
    v_payload_sha TEXT;
    v_idempotency TEXT;
BEGIN
    IF p_manifest IS NULL OR jsonb_typeof(p_manifest) <> 'object' THEN
        RAISE EXCEPTION 'phase3n-model-manifest-invalid' USING ERRCODE = '22023';
    END IF;
    v_idempotency := p_manifest->>'idempotency_key';
    v_payload_sha := p_manifest->>'payload_sha256';
    SELECT array_agg(value ORDER BY ordinality) INTO v_columns
    FROM jsonb_array_elements_text(p_manifest->'model_feature_columns') WITH ORDINALITY AS c(value, ordinality);
    IF v_idempotency IS NULL OR v_payload_sha !~ '^[0-9a-f]{64}$'
       OR v_columns IS NULL OR cardinality(v_columns) NOT BETWEEN 1 AND 2048
       OR (
           SELECT count(*) <> count(DISTINCT column_name)
           FROM unnest(v_columns) AS feature_columns(column_name)
       )
       OR EXISTS (
           SELECT 1 FROM unnest(v_columns) AS c(column_name)
           WHERE c.column_name = ANY (ARRAY[
               'time_seconds','finish_time','last_3f','last_3f_time','last_3f_rank',
               'last_3f_rank_normalized','corner_1','corner_2','corner_3','corner_4',
               'corner_positions','corner_positions_list','corner_position_avg',
               'corner_position_variance','last_corner_position','position_change','margin',
               'prize_money','finish','finish_position','actual_finish','win','place','return_tables'
           ])
       ) THEN
        RAISE EXCEPTION 'phase3n-model-manifest-invalid' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_existing FROM public.phase3n_model_manifests
    WHERE idempotency_key = v_idempotency;
    IF FOUND THEN
        INSERT INTO public.phase3n_observation_ingest_attempts(
            event_kind, idempotency_key, outcome, payload_sha256, failure_code
        ) VALUES (
            'model-manifest', v_idempotency,
            CASE WHEN v_existing.payload_sha256 = v_payload_sha THEN 'duplicate' ELSE 'conflict' END,
            v_payload_sha,
            CASE WHEN v_existing.payload_sha256 = v_payload_sha THEN NULL ELSE 'payload-binding-conflict' END
        );
        RETURN QUERY SELECT
            CASE WHEN v_existing.payload_sha256 = v_payload_sha THEN 'duplicate' ELSE 'conflict' END,
            v_existing.manifest_id, v_existing.registered_at;
        RETURN;
    END IF;
    v_id := gen_random_uuid();
    INSERT INTO public.phase3n_model_manifests(
        manifest_id, idempotency_key, model_id, model_version, model_artifact_sha256,
        candidate_commit_sha, feature_manifest_sha256, model_feature_columns,
        training_data_ended_at, expanding_window_checks_passed, source_environment,
        payload_sha256, registered_at
    ) VALUES (
        v_id, v_idempotency, p_manifest->>'model_id', p_manifest->>'model_version',
        p_manifest->>'model_artifact_sha256', p_manifest->>'candidate_commit_sha',
        p_manifest->>'feature_manifest_sha256', v_columns,
        (p_manifest->>'training_data_ended_at')::TIMESTAMPTZ,
        (p_manifest->>'expanding_window_checks_passed')::BOOLEAN,
        p_manifest->>'source_environment', v_payload_sha, v_now
    );
    INSERT INTO public.phase3n_observation_ingest_attempts(
        event_kind, idempotency_key, outcome, payload_sha256
    ) VALUES ('model-manifest', v_idempotency, 'inserted', v_payload_sha);
    RETURN QUERY SELECT 'inserted', v_id, v_now;
END;
$$;

CREATE OR REPLACE FUNCTION public.record_phase3n_prediction_observation(p_observation JSONB)
RETURNS TABLE(mutation_code TEXT, returned_observation_id UUID, prediction_at TIMESTAMPTZ)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_id UUID;
    v_existing public.phase3n_prediction_observations%ROWTYPE;
    v_payload_sha TEXT := p_observation->>'payload_sha256';
    v_idempotency TEXT := p_observation->>'idempotency_key';
    v_manifest public.phase3n_model_manifests%ROWTYPE;
    v_data_observed TIMESTAMPTZ;
    v_data_cutoff TIMESTAMPTZ;
BEGIN
    IF p_observation IS NULL OR jsonb_typeof(p_observation) <> 'object'
       OR v_payload_sha !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'phase3n-prediction-observation-invalid' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_existing FROM public.phase3n_prediction_observations
    WHERE idempotency_key = v_idempotency;
    IF FOUND THEN
        INSERT INTO public.phase3n_observation_ingest_attempts(
            event_kind, idempotency_key, outcome, payload_sha256, failure_code
        ) VALUES (
            'prediction', v_idempotency,
            CASE WHEN v_existing.payload_sha256 = v_payload_sha THEN 'duplicate' ELSE 'conflict' END,
            v_payload_sha,
            CASE WHEN v_existing.payload_sha256 = v_payload_sha THEN NULL ELSE 'payload-binding-conflict' END
        );
        RETURN QUERY SELECT
            CASE WHEN v_existing.payload_sha256 = v_payload_sha THEN 'duplicate' ELSE 'conflict' END,
            v_existing.observation_id, v_existing.prediction_at;
        RETURN;
    END IF;
    SELECT * INTO v_manifest FROM public.phase3n_model_manifests
    WHERE manifest_id = (p_observation->>'manifest_id')::UUID;
    IF NOT FOUND OR v_manifest.source_environment <> p_observation->>'source_environment' THEN
        RAISE EXCEPTION 'phase3n-model-manifest-binding-invalid' USING ERRCODE = '23503';
    END IF;
    v_data_observed := (p_observation->>'data_observed_at')::TIMESTAMPTZ;
    v_data_cutoff := (p_observation->>'data_cutoff_at')::TIMESTAMPTZ;
    IF v_data_observed > v_data_cutoff OR v_data_cutoff > v_now
       OR v_manifest.training_data_ended_at >= v_now
       OR COALESCE((p_observation->>'leakage_violation_count')::INTEGER, -1) <> 0 THEN
        INSERT INTO public.phase3n_observation_ingest_attempts(
            event_kind, idempotency_key, outcome, payload_sha256, failure_code
        ) VALUES ('prediction', v_idempotency, 'rejected', v_payload_sha, 'temporal-or-leakage-invalid');
        RETURN QUERY SELECT 'rejected', NULL::UUID, v_now;
        RETURN;
    END IF;
    v_id := (p_observation->>'observation_id')::UUID;
    INSERT INTO public.phase3n_prediction_observations(
        observation_id, idempotency_key, manifest_id, race_id, horse_id, horse_number,
        race_date, prediction_at, data_observed_at, data_cutoff_at, feature_values_sha256,
        predicted_value, predicted_probability, predicted_rank, odds_at_prediction,
        recommendation, qualifying_bet, wager_amount, baseline_wager_amount, latency_ms,
        source_environment, leakage_violation_count, payload_sha256, recorded_at
    ) VALUES (
        v_id, v_idempotency, v_manifest.manifest_id, p_observation->>'race_id',
        p_observation->>'horse_id', (p_observation->>'horse_number')::INTEGER,
        (p_observation->>'race_date')::DATE, v_now, v_data_observed, v_data_cutoff,
        p_observation->>'feature_values_sha256', (p_observation->>'predicted_value')::DOUBLE PRECISION,
        (p_observation->>'predicted_probability')::DOUBLE PRECISION,
        (p_observation->>'predicted_rank')::INTEGER,
        NULLIF(p_observation->>'odds_at_prediction', '')::NUMERIC,
        p_observation->>'recommendation', (p_observation->>'qualifying_bet')::BOOLEAN,
        (p_observation->>'wager_amount')::NUMERIC,
        (p_observation->>'baseline_wager_amount')::NUMERIC,
        (p_observation->>'latency_ms')::NUMERIC, p_observation->>'source_environment',
        (p_observation->>'leakage_violation_count')::INTEGER, v_payload_sha, v_now
    );
    INSERT INTO public.phase3n_observation_ingest_attempts(
        event_kind, idempotency_key, outcome, payload_sha256
    ) VALUES ('prediction', v_idempotency, 'inserted', v_payload_sha);
    RETURN QUERY SELECT 'inserted', v_id, v_now;
END;
$$;

CREATE OR REPLACE FUNCTION public.record_phase3n_result_observation(p_result JSONB)
RETURNS TABLE(mutation_code TEXT, returned_result_event_id UUID, recorded_at TIMESTAMPTZ)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_existing public.phase3n_result_observation_events%ROWTYPE;
    v_prediction public.phase3n_prediction_observations%ROWTYPE;
    v_payload_sha TEXT := p_result->>'payload_sha256';
    v_idempotency TEXT := p_result->>'idempotency_key';
    v_id UUID;
BEGIN
    IF p_result IS NULL OR jsonb_typeof(p_result) <> 'object'
       OR v_payload_sha !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'phase3n-result-observation-invalid' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_existing FROM public.phase3n_result_observation_events
    WHERE idempotency_key = v_idempotency;
    IF FOUND THEN
        INSERT INTO public.phase3n_observation_ingest_attempts(
            event_kind, idempotency_key, outcome, payload_sha256, failure_code
        ) VALUES (
            'result', v_idempotency,
            CASE WHEN v_existing.payload_sha256 = v_payload_sha THEN 'duplicate' ELSE 'conflict' END,
            v_payload_sha,
            CASE WHEN v_existing.payload_sha256 = v_payload_sha THEN NULL ELSE 'payload-binding-conflict' END
        );
        RETURN QUERY SELECT
            CASE WHEN v_existing.payload_sha256 = v_payload_sha THEN 'duplicate' ELSE 'conflict' END,
            v_existing.result_event_id, v_existing.recorded_at;
        RETURN;
    END IF;
    SELECT * INTO v_prediction FROM public.phase3n_prediction_observations
    WHERE observation_id = (p_result->>'observation_id')::UUID;
    IF NOT FOUND OR (p_result->>'settled_at')::TIMESTAMPTZ < v_prediction.prediction_at
       OR (p_result->>'settled_at')::TIMESTAMPTZ > v_now THEN
        INSERT INTO public.phase3n_observation_ingest_attempts(
            event_kind, idempotency_key, outcome, payload_sha256, failure_code
        ) VALUES ('result', v_idempotency, 'rejected', v_payload_sha, 'settlement-time-invalid');
        RETURN QUERY SELECT 'rejected', NULL::UUID, v_now;
        RETURN;
    END IF;
    INSERT INTO public.phase3n_result_observation_events(
        idempotency_key, observation_id, settled_at, y_true, finish_order, bet_outcome,
        return_amount, baseline_return_amount, payload_sha256, recorded_at
    ) VALUES (
        v_idempotency, v_prediction.observation_id, (p_result->>'settled_at')::TIMESTAMPTZ,
        (p_result->>'y_true')::INTEGER, NULLIF(p_result->>'finish_order', '')::INTEGER,
        p_result->>'bet_outcome', (p_result->>'return_amount')::NUMERIC,
        (p_result->>'baseline_return_amount')::NUMERIC, v_payload_sha, v_now
    ) RETURNING result_event_id INTO v_id;
    INSERT INTO public.phase3n_observation_ingest_attempts(
        event_kind, idempotency_key, outcome, payload_sha256
    ) VALUES ('result', v_idempotency, 'inserted', v_payload_sha);
    RETURN QUERY SELECT 'inserted', v_id, v_now;
END;
$$;

CREATE OR REPLACE FUNCTION public.enqueue_phase3n_ha_job(
    p_idempotency_key TEXT, p_job_kind TEXT, p_request_payload JSONB, p_request_sha256 TEXT
)
RETURNS TABLE(mutation_code TEXT, returned_job_id UUID, fencing_token BIGINT, lease_expires_at TIMESTAMPTZ)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_job public.phase3n_ha_jobs%ROWTYPE;
BEGIN
    SELECT * INTO v_job FROM public.phase3n_ha_jobs WHERE idempotency_key = p_idempotency_key;
    IF FOUND THEN
        INSERT INTO public.phase3n_ha_events(job_id, event_type, fencing_token, detail_code)
        VALUES (v_job.job_id, 'duplicate-enqueue', v_job.fencing_token, 'idempotent-replay');
        RETURN QUERY SELECT
            CASE WHEN v_job.request_sha256 = p_request_sha256 AND v_job.job_kind = p_job_kind
                 THEN 'duplicate' ELSE 'conflict' END,
            v_job.job_id, v_job.fencing_token, v_job.lease_expires_at;
        RETURN;
    END IF;
    INSERT INTO public.phase3n_ha_jobs(
        idempotency_key, job_kind, request_payload, request_sha256
    ) VALUES (p_idempotency_key, p_job_kind, p_request_payload, p_request_sha256)
    RETURNING * INTO v_job;
    INSERT INTO public.phase3n_ha_events(job_id, event_type, fencing_token, detail_code)
    VALUES (v_job.job_id, 'enqueued', 0, 'new-job');
    RETURN QUERY SELECT 'inserted', v_job.job_id, 0::BIGINT, NULL::TIMESTAMPTZ;
END;
$$;

CREATE OR REPLACE FUNCTION public.claim_phase3n_ha_job(
    p_worker_owner TEXT, p_lease_seconds INTEGER, p_job_kind TEXT DEFAULT NULL
)
RETURNS TABLE(
    mutation_code TEXT, returned_job_id UUID, returned_job_kind TEXT,
    request_payload JSONB, worker_owner TEXT, fencing_token BIGINT,
    lease_expires_at TIMESTAMPTZ, attempt_count INTEGER
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_job public.phase3n_ha_jobs%ROWTYPE;
    v_reclaimed BOOLEAN;
BEGIN
    IF p_worker_owner IS NULL OR char_length(p_worker_owner) NOT BETWEEN 3 AND 128
       OR p_lease_seconds NOT BETWEEN 2 AND 300 THEN
        RAISE EXCEPTION 'phase3n-ha-claim-invalid' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_job FROM public.phase3n_ha_jobs AS j
    WHERE (j.state = 'pending' OR (j.state = 'leased' AND j.lease_expires_at <= v_now))
      AND (p_job_kind IS NULL OR j.job_kind = p_job_kind)
      AND j.attempt_count < 20
    ORDER BY j.created_at, j.job_id
    FOR UPDATE SKIP LOCKED LIMIT 1;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'not-found', NULL::UUID, NULL::TEXT, NULL::JSONB,
            NULL::TEXT, NULL::BIGINT, NULL::TIMESTAMPTZ, NULL::INTEGER;
        RETURN;
    END IF;
    v_reclaimed := v_job.state = 'leased';
    UPDATE public.phase3n_ha_jobs AS j
    SET state = 'leased', worker_owner = p_worker_owner,
        fencing_token = nextval('public.phase3n_ha_fencing_seq'::regclass),
        lease_expires_at = v_now + make_interval(secs => p_lease_seconds),
        attempt_count = j.attempt_count + 1, updated_at = v_now
    WHERE j.job_id = v_job.job_id RETURNING * INTO v_job;
    INSERT INTO public.phase3n_ha_events(job_id, event_type, worker_owner, fencing_token, detail_code)
    VALUES (
        v_job.job_id, CASE WHEN v_reclaimed THEN 'reclaimed' ELSE 'claimed' END,
        v_job.worker_owner, v_job.fencing_token,
        CASE WHEN v_reclaimed THEN 'expired-lease-takeover' ELSE 'initial-lease' END
    );
    RETURN QUERY SELECT 'applied', v_job.job_id, v_job.job_kind, v_job.request_payload,
        v_job.worker_owner, v_job.fencing_token, v_job.lease_expires_at, v_job.attempt_count;
END;
$$;

CREATE OR REPLACE FUNCTION public.heartbeat_phase3n_ha_job(
    p_job_id UUID, p_worker_owner TEXT, p_fencing_token BIGINT, p_lease_seconds INTEGER
)
RETURNS TABLE(mutation_code TEXT, lease_expires_at TIMESTAMPTZ)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_expiry TIMESTAMPTZ;
BEGIN
    UPDATE public.phase3n_ha_jobs AS j
    SET lease_expires_at = v_now + make_interval(secs => p_lease_seconds), updated_at = v_now
    WHERE j.job_id = p_job_id AND j.state = 'leased' AND j.worker_owner = p_worker_owner
      AND j.fencing_token = p_fencing_token AND j.lease_expires_at > v_now
    RETURNING j.lease_expires_at INTO v_expiry;
    IF v_expiry IS NULL THEN
        INSERT INTO public.phase3n_ha_events(job_id, event_type, worker_owner, fencing_token, detail_code)
        VALUES (p_job_id, 'stale-fence-rejected', p_worker_owner, p_fencing_token, 'heartbeat-rejected');
        RETURN QUERY SELECT 'conflict', NULL::TIMESTAMPTZ;
        RETURN;
    END IF;
    INSERT INTO public.phase3n_ha_events(job_id, event_type, worker_owner, fencing_token, detail_code)
    VALUES (p_job_id, 'heartbeat', p_worker_owner, p_fencing_token, 'lease-renewed');
    RETURN QUERY SELECT 'applied', v_expiry;
END;
$$;

CREATE OR REPLACE FUNCTION public.apply_phase3n_ha_effect(
    p_job_id UUID, p_worker_owner TEXT, p_fencing_token BIGINT,
    p_effect_key TEXT, p_effect_sha256 TEXT
)
RETURNS TABLE(mutation_code TEXT, returned_effect_id UUID, accepted_fencing_token BIGINT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_job public.phase3n_ha_jobs%ROWTYPE;
    v_effect public.phase3n_ha_effects%ROWTYPE;
BEGIN
    SELECT * INTO v_job FROM public.phase3n_ha_jobs WHERE job_id = p_job_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'not-found', NULL::UUID, NULL::BIGINT;
        RETURN;
    END IF;
    IF v_job.state <> 'leased' OR v_job.worker_owner <> p_worker_owner
       OR v_job.fencing_token <> p_fencing_token OR v_job.lease_expires_at <= v_now THEN
        INSERT INTO public.phase3n_ha_events(job_id, event_type, worker_owner, fencing_token, detail_code)
        VALUES (p_job_id, 'stale-fence-rejected', p_worker_owner, p_fencing_token, 'effect-rejected');
        RETURN QUERY SELECT 'conflict', NULL::UUID, v_job.fencing_token;
        RETURN;
    END IF;
    SELECT * INTO v_effect FROM public.phase3n_ha_effects
    WHERE job_id = p_job_id OR effect_key = p_effect_key;
    IF FOUND THEN
        INSERT INTO public.phase3n_ha_events(job_id, event_type, worker_owner, fencing_token, detail_code)
        VALUES (p_job_id, 'duplicate-effect-rejected', p_worker_owner, p_fencing_token, 'effect-idempotency-hit');
        RETURN QUERY SELECT 'duplicate', v_effect.effect_id, v_effect.fencing_token;
        RETURN;
    END IF;
    INSERT INTO public.phase3n_ha_effects(job_id, effect_key, fencing_token, effect_sha256, applied_by)
    VALUES (p_job_id, p_effect_key, p_fencing_token, p_effect_sha256, p_worker_owner)
    RETURNING * INTO v_effect;
    UPDATE public.phase3n_ha_jobs AS j
    SET state = 'completed', worker_owner = NULL, lease_expires_at = NULL,
        effect_key = p_effect_key, effect_sha256 = p_effect_sha256,
        completed_at = v_now, updated_at = v_now
    WHERE j.job_id = p_job_id;
    INSERT INTO public.phase3n_ha_events(job_id, event_type, worker_owner, fencing_token, detail_code)
    VALUES (p_job_id, 'effect-applied', p_worker_owner, p_fencing_token, 'effect-committed');
    RETURN QUERY SELECT 'applied', v_effect.effect_id, v_effect.fencing_token;
END;
$$;

ALTER TABLE public.phase3n_model_manifests ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.phase3n_prediction_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.phase3n_result_observation_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.phase3n_observation_ingest_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.phase3n_ha_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.phase3n_ha_effects ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.phase3n_ha_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.phase3n_model_manifests FORCE ROW LEVEL SECURITY;
ALTER TABLE public.phase3n_prediction_observations FORCE ROW LEVEL SECURITY;
ALTER TABLE public.phase3n_result_observation_events FORCE ROW LEVEL SECURITY;
ALTER TABLE public.phase3n_observation_ingest_attempts FORCE ROW LEVEL SECURITY;
ALTER TABLE public.phase3n_ha_jobs FORCE ROW LEVEL SECURITY;
ALTER TABLE public.phase3n_ha_effects FORCE ROW LEVEL SECURITY;
ALTER TABLE public.phase3n_ha_events FORCE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.phase3n_model_manifests, public.phase3n_prediction_observations,
    public.phase3n_result_observation_events, public.phase3n_observation_ingest_attempts,
    public.phase3n_ha_jobs, public.phase3n_ha_effects, public.phase3n_ha_events
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.phase3n_ha_fencing_seq,
    public.phase3n_observation_ingest_attempts_attempt_id_seq,
    public.phase3n_ha_events_event_id_seq
    FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON TABLE public.phase3n_model_manifests, public.phase3n_prediction_observations,
    public.phase3n_result_observation_events, public.phase3n_observation_ingest_attempts,
    public.phase3n_ha_jobs, public.phase3n_ha_effects, public.phase3n_ha_events
    TO service_role;

REVOKE ALL ON FUNCTION public._phase3n_reject_immutable_mutation() FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.register_phase3n_model_manifest(JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.record_phase3n_prediction_observation(JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.record_phase3n_result_observation(JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.enqueue_phase3n_ha_job(TEXT, TEXT, JSONB, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.claim_phase3n_ha_job(TEXT, INTEGER, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.heartbeat_phase3n_ha_job(UUID, TEXT, BIGINT, INTEGER) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.apply_phase3n_ha_effect(UUID, TEXT, BIGINT, TEXT, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.register_phase3n_model_manifest(JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_phase3n_prediction_observation(JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_phase3n_result_observation(JSONB) TO service_role;
GRANT EXECUTE ON FUNCTION public.enqueue_phase3n_ha_job(TEXT, TEXT, JSONB, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.claim_phase3n_ha_job(TEXT, INTEGER, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.heartbeat_phase3n_ha_job(UUID, TEXT, BIGINT, INTEGER) TO service_role;
GRANT EXECUTE ON FUNCTION public.apply_phase3n_ha_effect(UUID, TEXT, BIGINT, TEXT, TEXT) TO service_role;
CREATE SCHEMA phase3m_internal AUTHORIZATION postgres;
REVOKE ALL ON SCHEMA phase3m_internal FROM PUBLIC, anon, authenticated, service_role;
CREATE TABLE phase3m_internal.bootstrap_history (
    ordinal INTEGER PRIMARY KEY CHECK (ordinal > 0),
    version TEXT NOT NULL UNIQUE CHECK (version ~ '^[0-9]{14}$'),
    path TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    migration_sha256 TEXT NOT NULL CHECK (migration_sha256 ~ '^[0-9a-f]{64}$'),
    chain_digest TEXT NOT NULL CHECK (chain_digest ~ '^[0-9a-f]{64}$'),
    bootstrap_id TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    expected_commit_sha TEXT NOT NULL CHECK (expected_commit_sha ~ '^[0-9a-f]{40}$'),
    applied_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
REVOKE ALL ON TABLE phase3m_internal.bootstrap_history FROM PUBLIC, anon, authenticated, service_role;
INSERT INTO phase3m_internal.bootstrap_history (
    ordinal, version, path, source, migration_sha256,
    chain_digest, bootstrap_id, manifest_sha256, expected_commit_sha
)
VALUES
(1, '20260720141000', 'supabase/bootstrap/v1/migrations/20260720141000_core_identity_finance.sql', 'phase3m-bootstrap', '04fb2a2ea7bd76ac7390b0d1226ce56415490f6f954799ec90e8a82bc9da55a3', '1a92dcb456887df6351bc0cc525c4f9f558a560755c2761e28e0a3f9284216d4', 'phase3m-supabase-bootstrap-v1', '99be317b411468f107ad8ff1351230966ec05624d72579be6834eee61af9e557', '86a2d314a641160e852d3597396aadcd03e81347'),
(2, '20260720141100', 'supabase/bootstrap/v1/migrations/20260720141100_prediction_quota.sql', 'phase3m-bootstrap', '35689b733902bcbfa4096e124c5319453bb29e22b0bf25a3d2039676dae63aec', '1a92dcb456887df6351bc0cc525c4f9f558a560755c2761e28e0a3f9284216d4', 'phase3m-supabase-bootstrap-v1', '99be317b411468f107ad8ff1351230966ec05624d72579be6834eee61af9e557', '86a2d314a641160e852d3597396aadcd03e81347'),
(3, '20260720141200', 'supabase/bootstrap/v1/migrations/20260720141200_purchase_history.sql', 'phase3m-bootstrap', 'cec5fdc2b62556a0e9cdf9917124ce0421e8297cf16bb1cc633a54deb0296033', '1a92dcb456887df6351bc0cc525c4f9f558a560755c2761e28e0a3f9284216d4', 'phase3m-supabase-bootstrap-v1', '99be317b411468f107ad8ff1351230966ec05624d72579be6834eee61af9e557', '86a2d314a641160e852d3597396aadcd03e81347'),
(4, '20260720142000', 'supabase/bootstrap/v1/migrations/20260720142000_race_domain.sql', 'phase3m-bootstrap', '4d1043b31d1699d7033ca1ac1bf038a0c82341ebbf63560e1d2fd27b60210f60', '1a92dcb456887df6351bc0cc525c4f9f558a560755c2761e28e0a3f9284216d4', 'phase3m-supabase-bootstrap-v1', '99be317b411468f107ad8ff1351230966ec05624d72579be6834eee61af9e557', '86a2d314a641160e852d3597396aadcd03e81347'),
(5, '20260720142100', 'supabase/bootstrap/v1/migrations/20260720142100_normalized_ultimate_domain.sql', 'phase3m-bootstrap', 'd8f2e758a0a53308ddb9fac717f67a21755fab0516115dcbec037b502b0d183d', '1a92dcb456887df6351bc0cc525c4f9f558a560755c2761e28e0a3f9284216d4', 'phase3m-supabase-bootstrap-v1', '99be317b411468f107ad8ff1351230966ec05624d72579be6834eee61af9e557', '86a2d314a641160e852d3597396aadcd03e81347'),
(6, '20260720142200', 'supabase/bootstrap/v1/migrations/20260720142200_server_ml_storage.sql', 'phase3m-bootstrap', 'b9b6c4888263f6ec202ac1e86bf9916206a656e5e1904a39b97b3881dec9a613', '1a92dcb456887df6351bc0cc525c4f9f558a560755c2761e28e0a3f9284216d4', 'phase3m-supabase-bootstrap-v1', '99be317b411468f107ad8ff1351230966ec05624d72579be6834eee61af9e557', '86a2d314a641160e852d3597396aadcd03e81347'),
(7, '20260720143000', 'supabase/migrations/20260718_scrape_uncertainty_review_ledger.sql', 'reconciled-existing', '6abdb1ab1fa8bee0a50834c25080682fc00f1275e6cb2959b77e1ab2c9f9e2af', '1a92dcb456887df6351bc0cc525c4f9f558a560755c2761e28e0a3f9284216d4', 'phase3m-supabase-bootstrap-v1', '99be317b411468f107ad8ff1351230966ec05624d72579be6834eee61af9e557', '86a2d314a641160e852d3597396aadcd03e81347'),
(8, '20260720143100', 'supabase/migrations/20260720_scrape_execution_reservation.sql', 'reconciled-existing', 'cf626a0976afe529d53cecdc3bea73f01ffdb8253516026f02aa40397d9f9c24', '1a92dcb456887df6351bc0cc525c4f9f558a560755c2761e28e0a3f9284216d4', 'phase3m-supabase-bootstrap-v1', '99be317b411468f107ad8ff1351230966ec05624d72579be6834eee61af9e557', '86a2d314a641160e852d3597396aadcd03e81347'),
(9, '20260720143200', 'supabase/bootstrap/v1/migrations/20260720143200_security_definer_search_path.sql', 'phase3m-bootstrap', 'fb150136686ceae39b170237516a08cda1089490b3d694429f30a8a63ca433e4', '1a92dcb456887df6351bc0cc525c4f9f558a560755c2761e28e0a3f9284216d4', 'phase3m-supabase-bootstrap-v1', '99be317b411468f107ad8ff1351230966ec05624d72579be6834eee61af9e557', '86a2d314a641160e852d3597396aadcd03e81347'),
(10, '20260720143300', 'supabase/bootstrap/v1/migrations/20260720143300_ocr_quota_reservation.sql', 'phase3m-bootstrap', '1fa0ecd23eb587520ce5b47b99c32cf14584686b591045ef11909be301004d12', '1a92dcb456887df6351bc0cc525c4f9f558a560755c2761e28e0a3f9284216d4', 'phase3m-supabase-bootstrap-v1', '99be317b411468f107ad8ff1351230966ec05624d72579be6834eee61af9e557', '86a2d314a641160e852d3597396aadcd03e81347'),
(11, '20260720143400', 'supabase/bootstrap/v1/migrations/20260720143400_admin_role_change.sql', 'phase3m-bootstrap', '7a4b5d918e66eea968097af1a02c9b1cd349b7b8dfa6baa6687c66ff8d80d6fd', '1a92dcb456887df6351bc0cc525c4f9f558a560755c2761e28e0a3f9284216d4', 'phase3m-supabase-bootstrap-v1', '99be317b411468f107ad8ff1351230966ec05624d72579be6834eee61af9e557', '86a2d314a641160e852d3597396aadcd03e81347'),
(12, '20260802140000', 'supabase/migrations/20260802_model_retrain_approval_ledger.sql', 'reconciled-existing', '67569184f079ae62f263384c54dc1e3f74493cdb2e9422aa8e5098ab7637c435', '1a92dcb456887df6351bc0cc525c4f9f558a560755c2761e28e0a3f9284216d4', 'phase3m-supabase-bootstrap-v1', '99be317b411468f107ad8ff1351230966ec05624d72579be6834eee61af9e557', '86a2d314a641160e852d3597396aadcd03e81347'),
(13, '20260802141000', 'supabase/migrations/20260802_model_retrain_job_ledger.sql', 'reconciled-existing', 'd98ab49665e6cbe41ed5d57998e167319c1f2e1e23dede6283755f31cce2b6a9', '1a92dcb456887df6351bc0cc525c4f9f558a560755c2761e28e0a3f9284216d4', 'phase3m-supabase-bootstrap-v1', '99be317b411468f107ad8ff1351230966ec05624d72579be6834eee61af9e557', '86a2d314a641160e852d3597396aadcd03e81347'),
(14, '20260802142000', 'supabase/migrations/20260802_model_retrain_worker_lease.sql', 'reconciled-existing', '25d1acad0af02e99acbf08ff521d1a427bc8e450f75a554a3b0a6740b79a6abd', '1a92dcb456887df6351bc0cc525c4f9f558a560755c2761e28e0a3f9284216d4', 'phase3m-supabase-bootstrap-v1', '99be317b411468f107ad8ff1351230966ec05624d72579be6834eee61af9e557', '86a2d314a641160e852d3597396aadcd03e81347'),
(15, '20260802143000', 'supabase/migrations/20260802_model_retrain_artifact_registration.sql', 'reconciled-existing', '10d7beb209ebe03bbe7ca23873ac0a31191a48fce8009229021226b9b553df80', '1a92dcb456887df6351bc0cc525c4f9f558a560755c2761e28e0a3f9284216d4', 'phase3m-supabase-bootstrap-v1', '99be317b411468f107ad8ff1351230966ec05624d72579be6834eee61af9e557', '86a2d314a641160e852d3597396aadcd03e81347'),
(16, '20260802144000', 'supabase/migrations/20260802_model_retrain_evaluation_registration.sql', 'reconciled-existing', 'fcfe4764c09278aa51b3dde655c220aaeffe735f352d5c4f1c778778da2c5ab7', '1a92dcb456887df6351bc0cc525c4f9f558a560755c2761e28e0a3f9284216d4', 'phase3m-supabase-bootstrap-v1', '99be317b411468f107ad8ff1351230966ec05624d72579be6834eee61af9e557', '86a2d314a641160e852d3597396aadcd03e81347'),
(17, '20260802145000', 'supabase/migrations/20260802_model_retrain_execution_bundle.sql', 'reconciled-existing', 'ec002098276d85524762c8ed50eed30a199f0042e75d2fb7a9b5ecd1f71a01f9', '1a92dcb456887df6351bc0cc525c4f9f558a560755c2761e28e0a3f9284216d4', 'phase3m-supabase-bootstrap-v1', '99be317b411468f107ad8ff1351230966ec05624d72579be6834eee61af9e557', '86a2d314a641160e852d3597396aadcd03e81347'),
(18, '20260802146000', 'supabase/migrations/20260802_model_retrain_orphan_reconciliation.sql', 'reconciled-existing', '29223b892cf56ea9bc2472acaff9748ebc60e04411e1df37976665f48a6393c8', '1a92dcb456887df6351bc0cc525c4f9f558a560755c2761e28e0a3f9284216d4', 'phase3m-supabase-bootstrap-v1', '99be317b411468f107ad8ff1351230966ec05624d72579be6834eee61af9e557', '86a2d314a641160e852d3597396aadcd03e81347'),
(19, '20260802147000', 'supabase/migrations/20260802_model_retrain_dispatch_queue.sql', 'reconciled-existing', '502f9cd3bfcc66881755c524c31e49bbdf7dc310ce445948965439034b4f3ed7', '1a92dcb456887df6351bc0cc525c4f9f558a560755c2761e28e0a3f9284216d4', 'phase3m-supabase-bootstrap-v1', '99be317b411468f107ad8ff1351230966ec05624d72579be6834eee61af9e557', '86a2d314a641160e852d3597396aadcd03e81347'),
(20, '20260802148000', 'supabase/migrations/20260720_scrape_operational_outbox.sql', 'phase3n-ha-extension', '97798c132fe27b722cc22e151a785ea793157ea962d69f35b6ace9e05c29bf1f', '1a92dcb456887df6351bc0cc525c4f9f558a560755c2761e28e0a3f9284216d4', 'phase3m-supabase-bootstrap-v1', '99be317b411468f107ad8ff1351230966ec05624d72579be6834eee61af9e557', '86a2d314a641160e852d3597396aadcd03e81347'),
(21, '20260802149000', 'supabase/migrations/20260802_phase3n_observation_ha.sql', 'phase3n-ha-extension', '952ca8c07909ff61a0f54e403ee5a4d0bdd4eecea679b6bbaf568a58690e46c2', '1a92dcb456887df6351bc0cc525c4f9f558a560755c2761e28e0a3f9284216d4', 'phase3m-supabase-bootstrap-v1', '99be317b411468f107ad8ff1351230966ec05624d72579be6834eee61af9e557', '86a2d314a641160e852d3597396aadcd03e81347');
-- Copy only reviewed legacy rows into canonical tables.
INSERT INTO public.profiles (
    id, email, full_name, role, subscription_tier,
    stripe_customer_id, stripe_subscription_id,
    ocr_monthly_limit, ocr_used_this_month, ocr_reset_date,
    pred_count_remaining, pred_count_reset_at, created_at, updated_at
)
SELECT id, email, full_name, role, subscription_tier,
       stripe_customer_id, stripe_subscription_id,
       COALESCE(ocr_monthly_limit, 10), COALESCE(ocr_used_this_month, 0),
       COALESCE(ocr_reset_date, now()), COALESCE(pred_count_remaining, 10),
       COALESCE(pred_count_reset_at, date_trunc('month', now()) + interval '1 month'),
       COALESCE(created_at, now()), COALESCE(updated_at, created_at, now())
FROM phase3n_legacy_20260816.profiles;

INSERT INTO public.purchase_history (
    id, user_id, race_id, purchase_date, season, venue, bet_type, combinations,
    strategy_type, purchase_count, unit_price, total_cost, expected_value,
    expected_return, actual_return, is_hit, recovery_rate, created_at
)
SELECT id, user_id, race_id, purchase_date, season, venue, bet_type, combinations,
       strategy_type, purchase_count, unit_price, total_cost, expected_value,
       expected_return, COALESCE(actual_return, 0), COALESCE(is_hit, false),
       COALESCE(recovery_rate, 0), COALESCE(created_at, now())
FROM phase3n_legacy_20260816.purchase_history;

INSERT INTO public.races (
    race_id, race_name, venue, date, kaisai_date, post_time, race_class, distance,
    track_type, surface, course_direction, weather, field_condition, kai, day,
    num_horses, horse_count, prize_money, market_entropy, top3_probability,
    source, user_id, created_at, updated_at
)
SELECT race_id, race_name, venue, date, kaisai_date, post_time, race_class, distance,
       track_type, surface, course_direction, weather, field_condition, kai, day,
       num_horses, horse_count, prize_money, market_entropy, top3_probability,
       source, CASE WHEN EXISTS (
           SELECT 1 FROM phase3n_legacy_20260816.profiles AS p WHERE p.id = source_rows.user_id
       ) THEN source_rows.user_id ELSE NULL END,
       COALESCE(source_rows.created_at, now()), COALESCE(source_rows.updated_at, source_rows.created_at, now())
FROM phase3n_legacy_20260816.races AS source_rows;

INSERT INTO public.race_results (
    id, race_id, finish_position, bracket_number, horse_number, horse_name, sex,
    age, jockey_weight, jockey_name, trainer_name, owner_name, finish_time, odds,
    popularity, margin, corner_positions, last_3f_time, horse_weight,
    weight_change, prize_money, user_id, created_at
)
SELECT id, race_id, finish_position, bracket_number, horse_number, horse_name, sex,
       age, jockey_weight, jockey_name, trainer_name, owner_name, finish_time, odds,
       popularity, margin, corner_positions, last_3f_time, horse_weight,
       weight_change, prize_money::BIGINT,
       CASE WHEN EXISTS (
           SELECT 1 FROM phase3n_legacy_20260816.profiles AS p WHERE p.id = source_rows.user_id
       ) THEN source_rows.user_id ELSE NULL END,
       COALESCE(source_rows.created_at, now())
FROM phase3n_legacy_20260816.race_results AS source_rows;

INSERT INTO public.race_payouts (
    id, race_id, bet_type, combination, payout, popularity, user_id, created_at
)
SELECT id, race_id, bet_type, combination, payout, popularity,
       CASE WHEN EXISTS (
           SELECT 1 FROM phase3n_legacy_20260816.profiles AS p WHERE p.id = source_rows.user_id
       ) THEN source_rows.user_id ELSE NULL END,
       COALESCE(source_rows.created_at, now())
FROM phase3n_legacy_20260816.race_payouts AS source_rows;

INSERT INTO public.races_ultimate (race_id, data, created_at, updated_at)
SELECT race_id, (data #>> '{}')::JSONB,
       COALESCE(created_at, now()), COALESCE(created_at, now())
FROM phase3n_legacy_20260816.races_ultimate;

INSERT INTO public.race_results_ultimate (
    id, race_id, horse_number, data, created_at, updated_at
)
SELECT id, race_id,
       COALESCE(
           NULLIF(btrim(((data #>> '{}')::JSONB) ->> 'horse_number'), ''),
           NULLIF(btrim(((data #>> '{}')::JSONB) ->> 'horse_num'), '')
       ),
       (data #>> '{}')::JSONB,
       COALESCE(created_at, now()), COALESCE(created_at, now())
FROM phase3n_legacy_20260816.race_results_ultimate;
SELECT setval(
    'public.race_results_ultimate_id_seq',
    (SELECT max(id) FROM public.race_results_ultimate),
    true
);

INSERT INTO public.model_metadata (
    model_id, user_id, storage_path, metadata, created_at, updated_at
)
SELECT model_id, 'shared', storage_path, (metadata #>> '{}')::JSONB,
       COALESCE(created_at, now()), COALESCE(created_at, now())
FROM phase3n_legacy_20260816.model_metadata;

INSERT INTO public.horse_pedigree (
    horse_id, sire, dam, damsire, created_at, updated_at
)
SELECT horse_id, sire, dam, damsire,
       COALESCE(created_at, now()), COALESCE(created_at, now())
FROM phase3n_legacy_20260816.horse_pedigree;

DO $phase3n_legacy_postcondition$
BEGIN
    IF (SELECT count(*) FROM phase3n_legacy_20260816.bank_records) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-count-mismatch:bank_records';
    END IF;
    IF (SELECT count(*) FROM phase3n_legacy_20260816.bets) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-count-mismatch:bets';
    END IF;
    IF (SELECT count(*) FROM phase3n_legacy_20260816.entries) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-count-mismatch:entries';
    END IF;
    IF (SELECT count(*) FROM phase3n_legacy_20260816.horse_details) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-count-mismatch:horse_details';
    END IF;
    IF (SELECT count(*) FROM phase3n_legacy_20260816.horse_pedigree) <> 1805 THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-count-mismatch:horse_pedigree';
    END IF;
    IF (SELECT count(*) FROM public.horse_pedigree) <> 1805 THEN
        RAISE EXCEPTION 'phase3n-legacy-migrated-row-count-mismatch:horse_pedigree';
    END IF;
    IF (SELECT count(*) FROM phase3n_legacy_20260816.jockey_details) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-count-mismatch:jockey_details';
    END IF;
    IF (SELECT count(*) FROM phase3n_legacy_20260816.ml_models) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-count-mismatch:ml_models';
    END IF;
    IF (SELECT count(*) FROM phase3n_legacy_20260816.model_metadata) <> 73 THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-count-mismatch:model_metadata';
    END IF;
    IF (SELECT count(*) FROM public.model_metadata) <> 73 THEN
        RAISE EXCEPTION 'phase3n-legacy-migrated-row-count-mismatch:model_metadata';
    END IF;
    IF (SELECT count(*) FROM phase3n_legacy_20260816.ocr_usage) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-count-mismatch:ocr_usage';
    END IF;
    IF (SELECT count(*) FROM phase3n_legacy_20260816.past_performances) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-count-mismatch:past_performances';
    END IF;
    IF (SELECT count(*) FROM phase3n_legacy_20260816.payouts) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-count-mismatch:payouts';
    END IF;
    IF (SELECT count(*) FROM phase3n_legacy_20260816.predictions) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-count-mismatch:predictions';
    END IF;
    IF (SELECT count(*) FROM phase3n_legacy_20260816.profiles) <> 3 THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-count-mismatch:profiles';
    END IF;
    IF (SELECT count(*) FROM public.profiles) <> 3 THEN
        RAISE EXCEPTION 'phase3n-legacy-migrated-row-count-mismatch:profiles';
    END IF;
    IF (SELECT count(*) FROM phase3n_legacy_20260816.purchase_history) <> 4 THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-count-mismatch:purchase_history';
    END IF;
    IF (SELECT count(*) FROM public.purchase_history) <> 4 THEN
        RAISE EXCEPTION 'phase3n-legacy-migrated-row-count-mismatch:purchase_history';
    END IF;
    IF (SELECT count(*) FROM phase3n_legacy_20260816.race_lap_times) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-count-mismatch:race_lap_times';
    END IF;
    IF (SELECT count(*) FROM phase3n_legacy_20260816.race_odds) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-count-mismatch:race_odds';
    END IF;
    IF (SELECT count(*) FROM phase3n_legacy_20260816.race_payouts) <> 5847 THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-count-mismatch:race_payouts';
    END IF;
    IF (SELECT count(*) FROM public.race_payouts) <> 5847 THEN
        RAISE EXCEPTION 'phase3n-legacy-migrated-row-count-mismatch:race_payouts';
    END IF;
    IF (SELECT count(*) FROM phase3n_legacy_20260816.race_results) <> 9588 THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-count-mismatch:race_results';
    END IF;
    IF (SELECT count(*) FROM public.race_results) <> 9588 THEN
        RAISE EXCEPTION 'phase3n-legacy-migrated-row-count-mismatch:race_results';
    END IF;
    IF (SELECT count(*) FROM phase3n_legacy_20260816.race_results_ultimate) <> 719 THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-count-mismatch:race_results_ultimate';
    END IF;
    IF (SELECT count(*) FROM public.race_results_ultimate) <> 719 THEN
        RAISE EXCEPTION 'phase3n-legacy-migrated-row-count-mismatch:race_results_ultimate';
    END IF;
    IF (SELECT count(*) FROM phase3n_legacy_20260816.races) <> 288 THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-count-mismatch:races';
    END IF;
    IF (SELECT count(*) FROM public.races) <> 288 THEN
        RAISE EXCEPTION 'phase3n-legacy-migrated-row-count-mismatch:races';
    END IF;
    IF (SELECT count(*) FROM phase3n_legacy_20260816.races_ultimate) <> 72 THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-count-mismatch:races_ultimate';
    END IF;
    IF (SELECT count(*) FROM public.races_ultimate) <> 72 THEN
        RAISE EXCEPTION 'phase3n-legacy-migrated-row-count-mismatch:races_ultimate';
    END IF;
    IF (SELECT count(*) FROM phase3n_legacy_20260816.results) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-count-mismatch:results';
    END IF;
    IF (SELECT count(*) FROM phase3n_legacy_20260816.trainer_details) <> 0 THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-count-mismatch:trainer_details';
    END IF;
    IF (SELECT count(*) FROM phase3n_legacy_20260816.users) <> 1 THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-count-mismatch:users';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT horse_id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM phase3n_legacy_20260816.horse_pedigree AS t
        ) AS source_rows
    )
       IS DISTINCT FROM '9074ba1d4d4b89c52619cc770ec99a579416b2b9ce818fc855a9cb08d6e2d644' THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-digest-mismatch:horse_pedigree';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT model_id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM phase3n_legacy_20260816.model_metadata AS t
        ) AS source_rows
    )
       IS DISTINCT FROM 'ad6ecf1c56bc921f7b1f20ca8efcb44fed309f547947a8ac46b3f989a8084359' THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-digest-mismatch:model_metadata';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM phase3n_legacy_20260816.profiles AS t
        ) AS source_rows
    )
       IS DISTINCT FROM '67b0f8b62d310af3b6fa9d293335f2faa91cb5b5075e097af594a9d864bcaef1' THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-digest-mismatch:profiles';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM phase3n_legacy_20260816.purchase_history AS t
        ) AS source_rows
    )
       IS DISTINCT FROM 'c7bf6e3b4ee949eb4c569c660285fa64cf1308467b9075d927142cff27244e93' THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-digest-mismatch:purchase_history';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM phase3n_legacy_20260816.race_payouts AS t
        ) AS source_rows
    )
       IS DISTINCT FROM 'a4ea839ff5bae8c1776edec41df7e647532bf027ea5c4da0463adda595ea460d' THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-digest-mismatch:race_payouts';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM phase3n_legacy_20260816.race_results AS t
        ) AS source_rows
    )
       IS DISTINCT FROM 'eb48e6dab0d2dd6514fe807dedf18fac494a9c1f33c0aa4b540c66f2588ad019' THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-digest-mismatch:race_results';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM phase3n_legacy_20260816.race_results_ultimate AS t
        ) AS source_rows
    )
       IS DISTINCT FROM '112d562f8bf89e8e771b23d8b4f80aa8b41f9a798cde01fdb41df21af6af42f2' THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-digest-mismatch:race_results_ultimate';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT race_id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM phase3n_legacy_20260816.races AS t
        ) AS source_rows
    )
       IS DISTINCT FROM '96aa605a704d04c938b48c43982e55c41c73f0fe6ac4d7c79cb063f8cc48a258' THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-digest-mismatch:races';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT race_id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM phase3n_legacy_20260816.races_ultimate AS t
        ) AS source_rows
    )
       IS DISTINCT FROM 'a27b2555c7ef8b1f7f5dd7784dd6f1d7b4115737c2225788f1f272ce985671da' THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-digest-mismatch:races_ultimate';
    END IF;
    IF (
        SELECT encode(
            extensions.digest(
                convert_to(COALESCE(string_agg(row_hash, '|' ORDER BY row_key), ''), 'UTF8'),
                'sha256'
            ),
            'hex'
        )
        FROM (
            SELECT id::TEXT AS row_key,
                   encode(
                       extensions.digest(convert_to(to_jsonb(t)::TEXT, 'UTF8'), 'sha256'),
                       'hex'
                   ) AS row_hash
            FROM phase3n_legacy_20260816.users AS t
        ) AS source_rows
    )
       IS DISTINCT FROM '04b79d98eebf1bd137cdbcae02e950ff6e13dd0d116e1417b3ddbd5e5a9aceaf' THEN
        RAISE EXCEPTION 'phase3n-legacy-archive-row-digest-mismatch:users';
    END IF;
    IF (SELECT count(*) FROM public.race_payouts WHERE user_id IS NULL)
       <> 5847
       OR EXISTS (
           SELECT 1 FROM public.race_payouts AS canonical
           LEFT JOIN public.profiles AS p ON p.id = canonical.user_id
           WHERE canonical.user_id IS NOT NULL AND p.id IS NULL
       ) THEN
        RAISE EXCEPTION 'phase3n-legacy-canonical-user-reference-invalid:race_payouts';
    END IF;
    IF (SELECT count(*) FROM public.race_results WHERE user_id IS NULL)
       <> 9588
       OR EXISTS (
           SELECT 1 FROM public.race_results AS canonical
           LEFT JOIN public.profiles AS p ON p.id = canonical.user_id
           WHERE canonical.user_id IS NOT NULL AND p.id IS NULL
       ) THEN
        RAISE EXCEPTION 'phase3n-legacy-canonical-user-reference-invalid:race_results';
    END IF;
    IF (SELECT count(*) FROM public.races WHERE user_id IS NULL)
       <> 288
       OR EXISTS (
           SELECT 1 FROM public.races AS canonical
           LEFT JOIN public.profiles AS p ON p.id = canonical.user_id
           WHERE canonical.user_id IS NOT NULL AND p.id IS NULL
       ) THEN
        RAISE EXCEPTION 'phase3n-legacy-canonical-user-reference-invalid:races';
    END IF;
    IF (SELECT count(*) FROM phase3m_internal.bootstrap_history) <> 21 THEN
        RAISE EXCEPTION 'phase3n-legacy-bootstrap-history-incomplete';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = 'phase3n_prediction_observations'
    ) THEN
        RAISE EXCEPTION 'phase3n-legacy-phase3n-postcondition-missing';
    END IF;
END
$phase3n_legacy_postcondition$;
COMMIT;
