-- ============================================================
-- Approval-bound model artifact registration.
-- Only the live fenced worker may bind an already-uploaded object from the
-- private models bucket. Registration does not evaluate or activate a model.
-- ============================================================

ALTER TABLE public.model_retrain_jobs
    DROP CONSTRAINT IF EXISTS model_retrain_jobs_job_state_check,
    DROP CONSTRAINT IF EXISTS model_retrain_jobs_artifact_written_check,
    DROP CONSTRAINT IF EXISTS model_retrain_jobs_artifact_uri_check,
    DROP CONSTRAINT IF EXISTS model_retrain_jobs_artifact_sha256_check,
    DROP CONSTRAINT IF EXISTS model_retrain_jobs_worker_state_check;

ALTER TABLE public.model_retrain_jobs
    ADD COLUMN IF NOT EXISTS artifact_size_bytes BIGINT NULL,
    ADD COLUMN IF NOT EXISTS artifact_media_type TEXT NULL,
    ADD COLUMN IF NOT EXISTS artifact_registered_at TIMESTAMPTZ NULL;

ALTER TABLE public.model_retrain_jobs
    ADD CONSTRAINT model_retrain_jobs_job_state_check CHECK (
        job_state IN ('queued', 'claimed', 'running', 'artifact-registered', 'failed')
    ),
    ADD CONSTRAINT model_retrain_jobs_artifact_identity_check CHECK (
        (job_state <> 'artifact-registered' AND artifact_written = FALSE
            AND artifact_uri IS NULL AND artifact_sha256 IS NULL
            AND artifact_size_bytes IS NULL AND artifact_media_type IS NULL
            AND artifact_registered_at IS NULL)
        OR (job_state = 'artifact-registered' AND artifact_written = TRUE
            AND artifact_sha256 ~ '^[0-9a-f]{64}$'
            AND artifact_uri IN (
                'models://retrain/' || job_id::TEXT || '/' || artifact_sha256 || '.joblib',
                'models://retrain/' || job_id::TEXT || '/' || artifact_sha256 || '.pkl',
                'models://retrain/' || job_id::TEXT || '/' || artifact_sha256 || '.bin'
            )
            AND artifact_size_bytes BETWEEN 1 AND 104857600
            AND artifact_media_type IN (
                'application/octet-stream',
                'application/x-python-serialized-object'
            )
            AND artifact_registered_at IS NOT NULL)
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
        OR (job_state = 'artifact-registered'
            AND worker_id IS NOT NULL AND fencing_token IS NOT NULL
            AND lease_expires_at IS NOT NULL AND claimed_at IS NOT NULL
            AND started_at IS NOT NULL AND finished_at IS NOT NULL
            AND failure_code IS NULL AND execution_started = TRUE)
        OR (job_state = 'failed'
            AND worker_id IS NOT NULL AND fencing_token IS NOT NULL
            AND lease_expires_at IS NOT NULL AND claimed_at IS NOT NULL
            AND finished_at IS NOT NULL AND failure_code IS NOT NULL)
    );

ALTER TABLE public.model_retrain_job_events
    DROP CONSTRAINT IF EXISTS model_retrain_job_events_event_type_check,
    DROP CONSTRAINT IF EXISTS model_retrain_job_events_state_check;
ALTER TABLE public.model_retrain_job_events
    ADD CONSTRAINT model_retrain_job_events_event_type_check CHECK (
        event_type IN (
            'queued', 'claimed', 'heartbeat', 'started', 'artifact-registered',
            'failed', 'lease-expired'
        )
    ),
    ADD CONSTRAINT model_retrain_job_events_state_check CHECK (
        (from_state IS NULL OR from_state IN (
            'queued', 'claimed', 'running', 'artifact-registered', 'failed'
        ))
        AND to_state IN ('queued', 'claimed', 'running', 'artifact-registered', 'failed')
        AND record_version >= 1
    );

CREATE TABLE IF NOT EXISTS public.model_retrain_artifacts (
    job_id UUID PRIMARY KEY
        REFERENCES public.model_retrain_jobs(job_id) ON DELETE RESTRICT,
    approval_id UUID NOT NULL
        REFERENCES public.model_retrain_approval_requests(approval_id) ON DELETE RESTRICT,
    approved_payload_hash TEXT NOT NULL CHECK (approved_payload_hash ~ '^[0-9a-f]{64}$'),
    object_name TEXT NOT NULL UNIQUE,
    artifact_uri TEXT NOT NULL UNIQUE CHECK (artifact_uri = 'models://' || object_name),
    artifact_sha256 TEXT NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
    artifact_size_bytes BIGINT NOT NULL CHECK (artifact_size_bytes BETWEEN 1 AND 104857600),
    artifact_media_type TEXT NOT NULL CHECK (artifact_media_type IN (
        'application/octet-stream',
        'application/x-python-serialized-object'
    )),
    worker_id TEXT NOT NULL CHECK (worker_id ~ '^[a-z0-9][a-z0-9._:-]{2,79}$'),
    fencing_token BIGINT NOT NULL CHECK (fencing_token >= 1),
    registered_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    authoritative_record BOOLEAN NOT NULL DEFAULT TRUE CHECK (authoritative_record = TRUE),
    CHECK (object_name IN (
        'retrain/' || job_id::TEXT || '/' || artifact_sha256 || '.joblib',
        'retrain/' || job_id::TEXT || '/' || artifact_sha256 || '.pkl',
        'retrain/' || job_id::TEXT || '/' || artifact_sha256 || '.bin'
    ))
);

ALTER TABLE public.model_retrain_artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.model_retrain_artifacts FORCE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.model_retrain_artifacts
    FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON TABLE public.model_retrain_artifacts TO service_role;

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
       OR NEW.authoritative_record IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'model retrain job update violates safety state' USING ERRCODE = '55000';
    END IF;
    IF NOT (
        (OLD.job_state = 'queued' AND NEW.job_state = 'claimed')
        OR (OLD.job_state = 'claimed' AND NEW.job_state IN ('claimed', 'queued', 'running', 'failed'))
        OR (OLD.job_state = 'running' AND NEW.job_state IN ('running', 'artifact-registered', 'failed'))
    ) THEN
        RAISE EXCEPTION 'model retrain job transition is invalid' USING ERRCODE = '55000';
    END IF;
    IF NEW.job_state = 'artifact-registered' THEN
        IF OLD.job_state <> 'running'
           OR OLD.artifact_written IS DISTINCT FROM FALSE
           OR NEW.artifact_written IS DISTINCT FROM TRUE
           OR NEW.artifact_uri IS NULL OR NEW.artifact_sha256 IS NULL
           OR NEW.artifact_size_bytes IS NULL OR NEW.artifact_media_type IS NULL
           OR NEW.artifact_registered_at IS NULL THEN
            RAISE EXCEPTION 'model retrain artifact transition is invalid' USING ERRCODE = '55000';
        END IF;
    ELSIF ROW(
        NEW.artifact_written, NEW.artifact_uri, NEW.artifact_sha256,
        NEW.artifact_size_bytes, NEW.artifact_media_type, NEW.artifact_registered_at
    ) IS DISTINCT FROM ROW(
        OLD.artifact_written, OLD.artifact_uri, OLD.artifact_sha256,
        OLD.artifact_size_bytes, OLD.artifact_media_type, OLD.artifact_registered_at
    ) THEN
        RAISE EXCEPTION 'model retrain artifact identity is immutable' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public._reject_model_retrain_artifact_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RAISE EXCEPTION 'model retrain artifact registrations are immutable' USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_model_retrain_artifacts_immutable ON public.model_retrain_artifacts;
CREATE TRIGGER trg_model_retrain_artifacts_immutable
    BEFORE UPDATE OR DELETE ON public.model_retrain_artifacts
    FOR EACH ROW EXECUTE FUNCTION public._reject_model_retrain_artifact_mutation();

CREATE OR REPLACE FUNCTION public.register_model_retrain_artifact(
    p_worker_id TEXT,
    p_job_id UUID,
    p_expected_version INTEGER,
    p_fencing_token BIGINT,
    p_object_name TEXT,
    p_artifact_sha256 TEXT,
    p_artifact_size_bytes BIGINT,
    p_artifact_media_type TEXT
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
    v_uri TEXT;
BEGIN
    IF p_worker_id IS NULL OR p_worker_id !~ '^[a-z0-9][a-z0-9._:-]{2,79}$'
       OR p_job_id IS NULL
       OR p_expected_version IS NULL OR p_expected_version < 1
       OR p_fencing_token IS NULL OR p_fencing_token < 1
       OR p_artifact_sha256 IS NULL OR p_artifact_sha256 !~ '^[0-9a-f]{64}$'
       OR p_artifact_size_bytes IS NULL OR p_artifact_size_bytes NOT BETWEEN 1 AND 104857600
       OR p_artifact_media_type IS NULL OR p_artifact_media_type NOT IN (
            'application/octet-stream',
            'application/x-python-serialized-object'
       )
       OR p_object_name IS NULL OR p_object_name NOT IN (
            'retrain/' || p_job_id::TEXT || '/' || p_artifact_sha256 || '.joblib',
            'retrain/' || p_job_id::TEXT || '/' || p_artifact_sha256 || '.pkl',
            'retrain/' || p_job_id::TEXT || '/' || p_artifact_sha256 || '.bin'
       ) THEN
        RAISE EXCEPTION 'invalid model retrain artifact registration' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_job FROM public.model_retrain_jobs AS j
    WHERE j.job_id = p_job_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'model retrain job not found' USING ERRCODE = 'P0002';
    END IF;
    IF v_job.record_version <> p_expected_version THEN
        RAISE EXCEPTION 'model retrain job version conflict' USING ERRCODE = '40001';
    END IF;
    IF v_job.job_state <> 'running'
       OR v_job.worker_id IS DISTINCT FROM p_worker_id
       OR v_job.fencing_token IS DISTINCT FROM p_fencing_token
       OR v_job.lease_expires_at <= v_now THEN
        RAISE EXCEPTION 'stale model retrain worker cannot register artifact' USING ERRCODE = '42501';
    END IF;

    SELECT * INTO v_approval FROM public.model_retrain_approval_requests AS a
    WHERE a.approval_id = v_job.approval_id FOR SHARE;
    IF v_approval.approval_status <> 'approved'
       OR v_approval.expires_at <= v_now
       OR v_approval.job_created IS DISTINCT FROM TRUE
       OR v_approval.approved_payload_hash IS DISTINCT FROM v_job.approved_payload_hash THEN
        RAISE EXCEPTION 'model retrain approval no longer authorizes artifact registration'
            USING ERRCODE = '42501';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM storage.buckets AS b
        JOIN storage.objects AS o ON o.bucket_id = b.id
        WHERE b.id = 'models' AND b.public IS FALSE AND o.name = p_object_name
    ) THEN
        RAISE EXCEPTION 'model retrain artifact object is absent from private storage'
            USING ERRCODE = '55000';
    END IF;

    v_uri := 'models://' || p_object_name;
    INSERT INTO public.model_retrain_artifacts (
        job_id, approval_id, approved_payload_hash, object_name, artifact_uri,
        artifact_sha256, artifact_size_bytes, artifact_media_type,
        worker_id, fencing_token, registered_at, authoritative_record
    ) VALUES (
        v_job.job_id, v_job.approval_id, v_job.approved_payload_hash,
        p_object_name, v_uri, p_artifact_sha256, p_artifact_size_bytes,
        p_artifact_media_type, p_worker_id, p_fencing_token, v_now, TRUE
    );

    UPDATE public.model_retrain_jobs AS j SET
        job_state = 'artifact-registered', record_version = j.record_version + 1,
        artifact_written = TRUE, artifact_uri = v_uri,
        artifact_sha256 = p_artifact_sha256,
        artifact_size_bytes = p_artifact_size_bytes,
        artifact_media_type = p_artifact_media_type,
        artifact_registered_at = v_now, finished_at = v_now
    WHERE j.job_id = p_job_id RETURNING * INTO v_job;

    INSERT INTO public.model_retrain_job_events (
        job_id, event_seq, event_type, actor_user_id, worker_id, approval_id,
        approved_payload_hash, fencing_token, from_state, to_state, record_version, reason
    ) VALUES (
        v_job.job_id, v_job.record_version, 'artifact-registered', NULL, p_worker_id,
        v_job.approval_id, v_job.approved_payload_hash, p_fencing_token,
        'running', 'artifact-registered', v_job.record_version,
        'Fenced worker bound an immutable artifact identity from private storage.'
    );
    RETURN QUERY SELECT * FROM public.model_retrain_jobs AS j WHERE j.job_id = p_job_id;
END;
$$;

REVOKE ALL ON FUNCTION public._guard_model_retrain_job_update()
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public._reject_model_retrain_artifact_mutation()
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.register_model_retrain_artifact(
    TEXT, UUID, INTEGER, BIGINT, TEXT, TEXT, BIGINT, TEXT
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.register_model_retrain_artifact(
    TEXT, UUID, INTEGER, BIGINT, TEXT, TEXT, BIGINT, TEXT
) TO service_role;
