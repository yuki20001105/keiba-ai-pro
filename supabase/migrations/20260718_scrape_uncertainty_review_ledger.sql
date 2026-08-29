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
