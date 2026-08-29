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
