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
