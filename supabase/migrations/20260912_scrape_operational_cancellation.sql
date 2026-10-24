-- Owner-scoped cooperative cancellation for operational scrape jobs.
--
-- Internal job states intentionally remain queued/running/completed/error so
-- this migration is additive and compatible with the existing constraints.
-- A running row with cancel_requested_at is projected as `cancelling`; an
-- error row whose bounded reason is `cancelled-by-owner` is projected as
-- `cancelled` by the application adapter.

ALTER TABLE public.scrape_operational_jobs
    ADD COLUMN IF NOT EXISTS cancel_requested_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS cancel_requested_by UUID NULL
        REFERENCES public.profiles(id) ON DELETE RESTRICT;

ALTER TABLE public.scrape_operational_outbox
    ADD COLUMN IF NOT EXISTS cancel_requested_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS cancel_requested_by UUID NULL
        REFERENCES public.profiles(id) ON DELETE RESTRICT;

CREATE OR REPLACE FUNCTION public.request_cancel_scrape_operational_job(
    p_job_id UUID,
    p_owner_user_id UUID
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
    IF p_job_id IS NULL OR p_owner_user_id IS NULL THEN
        RAISE EXCEPTION 'invalid operational cancel request' USING ERRCODE = '22023';
    END IF;

    -- Match claim/settle lock ordering (outbox, then job) so a cancellation
    -- racing completion cannot deadlock with the worker settlement RPC.
    SELECT o.* INTO v_outbox
    FROM public.scrape_operational_outbox AS o
    WHERE o.job_id = p_job_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'not_found', NULL::TEXT, NULL::JSONB,
            NULL::UUID, NULL::UUID, NULL::UUID, NULL::TEXT, NULL::JSONB,
            NULL::TEXT, NULL::TEXT, NULL::BIGINT, NULL::BIGINT, NULL::INTEGER;
        RETURN;
    END IF;

    SELECT j.* INTO v_job
    FROM public.scrape_operational_jobs AS j
    WHERE j.job_id = p_job_id AND j.owner_user_id = p_owner_user_id
    FOR UPDATE;
    IF NOT FOUND THEN
        -- Do not reveal whether another owner's job exists.
        RETURN QUERY SELECT 'not_found', NULL::TEXT, NULL::JSONB,
            NULL::UUID, NULL::UUID, NULL::UUID, NULL::TEXT, NULL::JSONB,
            NULL::TEXT, NULL::TEXT, NULL::BIGINT, NULL::BIGINT, NULL::INTEGER;
        RETURN;
    END IF;

    IF v_job.status = 'error' AND v_job.error = 'cancelled-by-owner' THEN
        RETURN QUERY SELECT 'duplicate', NULL::TEXT, to_jsonb(v_job),
            NULL::UUID, NULL::UUID, NULL::UUID, NULL::TEXT, NULL::JSONB,
            NULL::TEXT, NULL::TEXT, NULL::BIGINT, NULL::BIGINT, NULL::INTEGER;
        RETURN;
    END IF;
    IF v_job.status IN ('completed', 'error') THEN
        RETURN QUERY SELECT 'conflict', 'job-terminal', to_jsonb(v_job),
            NULL::UUID, NULL::UUID, NULL::UUID, NULL::TEXT, NULL::JSONB,
            NULL::TEXT, NULL::TEXT, NULL::BIGINT, NULL::BIGINT, NULL::INTEGER;
        RETURN;
    END IF;
    IF v_outbox.cancel_requested_at IS NOT NULL THEN
        RETURN QUERY SELECT 'duplicate', NULL::TEXT, to_jsonb(v_job),
            NULL::UUID, NULL::UUID, NULL::UUID, NULL::TEXT, NULL::JSONB,
            NULL::TEXT, NULL::TEXT, NULL::BIGINT, NULL::BIGINT, NULL::INTEGER;
        RETURN;
    END IF;

    IF v_job.status = 'queued' AND v_outbox.state = 'pending' THEN
        UPDATE public.scrape_operational_outbox AS o
        SET state = 'blocked', settlement_reason = 'cancelled-by-owner',
            cancel_requested_at = v_now, cancel_requested_by = p_owner_user_id,
            version = o.version + 1, updated_at = v_now
        WHERE o.job_id = p_job_id;
        UPDATE public.scrape_operational_jobs AS j
        SET status = 'error', result = NULL, error = 'cancelled-by-owner',
            cancel_requested_at = v_now, cancel_requested_by = p_owner_user_id,
            updated_at = v_now
        WHERE j.job_id = p_job_id
        RETURNING * INTO v_job;
    ELSIF v_job.status = 'running' AND v_outbox.state = 'claimed' THEN
        UPDATE public.scrape_operational_outbox AS o
        SET cancel_requested_at = v_now, cancel_requested_by = p_owner_user_id,
            version = o.version + 1, updated_at = v_now
        WHERE o.job_id = p_job_id;
        UPDATE public.scrape_operational_jobs AS j
        SET cancel_requested_at = v_now, cancel_requested_by = p_owner_user_id,
            updated_at = v_now
        WHERE j.job_id = p_job_id
        RETURNING * INTO v_job;
    ELSE
        RETURN QUERY SELECT 'conflict', 'job-state-conflict', NULL::JSONB,
            NULL::UUID, NULL::UUID, NULL::UUID, NULL::TEXT, NULL::JSONB,
            NULL::TEXT, NULL::TEXT, NULL::BIGINT, NULL::BIGINT, NULL::INTEGER;
        RETURN;
    END IF;

    RETURN QUERY SELECT 'applied', NULL::TEXT, to_jsonb(v_job),
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

    -- A cancellation marker survives a worker crash. Once its lease expires,
    -- settle it instead of reclaiming and repeating the effect.
    SELECT o.* INTO v_outbox
    FROM public.scrape_operational_outbox AS o
    WHERE o.state = 'claimed' AND o.cancel_requested_at IS NOT NULL
      AND o.lease_expires_at <= v_now
    ORDER BY o.created_at, o.job_id
    FOR UPDATE SKIP LOCKED
    LIMIT 1;
    IF FOUND THEN
        UPDATE public.scrape_operational_outbox AS o
        SET state = 'blocked', worker_owner = NULL, lease_expires_at = NULL,
            settlement_reason = 'cancelled-by-owner', version = o.version + 1,
            updated_at = v_now
        WHERE o.job_id = v_outbox.job_id;
        UPDATE public.scrape_operational_jobs AS j
        SET status = 'error', result = NULL, error = 'cancelled-by-owner',
            updated_at = v_now
        WHERE j.job_id = v_outbox.job_id;
    END IF;

    SELECT o.* INTO v_outbox
    FROM public.scrape_operational_outbox AS o
    WHERE o.state = 'claimed' AND o.cancel_requested_at IS NULL
      AND o.lease_expires_at <= v_now AND o.attempt_count >= p_max_attempts
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
    ) AND o.cancel_requested_at IS NULL AND o.attempt_count < p_max_attempts
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
    RETURN QUERY SELECT 'applied',
        CASE WHEN v_row.cancel_requested_at IS NULL THEN NULL::TEXT ELSE 'cancel-requested' END,
        NULL::JSONB, NULL::UUID, NULL::UUID, NULL::UUID, NULL::TEXT, NULL::JSONB,
        NULL::TEXT, NULL::TEXT, v_row.fencing_token,
        floor(extract(epoch FROM v_row.lease_expires_at))::BIGINT,
        v_row.attempt_count;
END;
$$;

REVOKE ALL ON FUNCTION public.request_cancel_scrape_operational_job(UUID, UUID)
    FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.request_cancel_scrape_operational_job(UUID, UUID)
    TO service_role;
