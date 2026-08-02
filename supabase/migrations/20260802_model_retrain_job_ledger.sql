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
