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
