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
