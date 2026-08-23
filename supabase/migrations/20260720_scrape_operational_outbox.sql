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
