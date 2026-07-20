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
