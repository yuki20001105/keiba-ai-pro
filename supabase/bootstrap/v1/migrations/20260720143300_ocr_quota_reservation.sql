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
