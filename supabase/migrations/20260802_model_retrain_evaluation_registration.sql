-- ============================================================
-- Accepted model-evaluation registration.
-- This persists a sanitized verifier report bound to the registered artifact.
-- It deliberately cannot create trusted promotion evidence or activate a model.
-- ============================================================

ALTER TABLE public.model_retrain_jobs
    DROP CONSTRAINT IF EXISTS model_retrain_jobs_job_state_check,
    DROP CONSTRAINT IF EXISTS model_retrain_jobs_artifact_identity_check,
    DROP CONSTRAINT IF EXISTS model_retrain_jobs_worker_state_check;

ALTER TABLE public.model_retrain_jobs
    ADD COLUMN IF NOT EXISTS evaluation_recorded BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS acceptance_passed BOOLEAN NULL,
    ADD COLUMN IF NOT EXISTS evaluation_report_sha256 TEXT NULL,
    ADD COLUMN IF NOT EXISTS evaluator_id TEXT NULL,
    ADD COLUMN IF NOT EXISTS evaluated_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS promotion_eligible BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE public.model_retrain_jobs
    ADD CONSTRAINT model_retrain_jobs_job_state_check CHECK (
        job_state IN (
            'queued', 'claimed', 'running', 'artifact-registered',
            'evaluation-recorded', 'failed'
        )
    ),
    ADD CONSTRAINT model_retrain_jobs_artifact_identity_check CHECK (
        (job_state NOT IN ('artifact-registered', 'evaluation-recorded')
            AND artifact_written = FALSE
            AND artifact_uri IS NULL AND artifact_sha256 IS NULL
            AND artifact_size_bytes IS NULL AND artifact_media_type IS NULL
            AND artifact_registered_at IS NULL)
        OR (job_state IN ('artifact-registered', 'evaluation-recorded')
            AND artifact_written = TRUE
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
    ADD CONSTRAINT model_retrain_jobs_evaluation_identity_check CHECK (
        (job_state <> 'evaluation-recorded'
            AND evaluation_recorded = FALSE AND acceptance_passed IS NULL
            AND evaluation_report_sha256 IS NULL AND evaluator_id IS NULL
            AND evaluated_at IS NULL AND promotion_eligible = FALSE)
        OR (job_state = 'evaluation-recorded'
            AND evaluation_recorded = TRUE AND acceptance_passed = TRUE
            AND evaluation_report_sha256 ~ '^[0-9a-f]{64}$'
            AND evaluator_id ~ '^[a-z0-9][a-z0-9._:-]{2,79}$'
            AND evaluated_at IS NOT NULL AND promotion_eligible = FALSE)
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
        OR (job_state IN ('artifact-registered', 'evaluation-recorded')
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
    DROP CONSTRAINT IF EXISTS model_retrain_job_events_state_check,
    DROP CONSTRAINT IF EXISTS model_retrain_job_events_actor_check;
ALTER TABLE public.model_retrain_job_events
    ADD COLUMN IF NOT EXISTS evaluator_id TEXT NULL;
ALTER TABLE public.model_retrain_job_events
    ADD CONSTRAINT model_retrain_job_events_event_type_check CHECK (
        event_type IN (
            'queued', 'claimed', 'heartbeat', 'started', 'artifact-registered',
            'evaluation-recorded', 'failed', 'lease-expired'
        )
    ),
    ADD CONSTRAINT model_retrain_job_events_state_check CHECK (
        (from_state IS NULL OR from_state IN (
            'queued', 'claimed', 'running', 'artifact-registered',
            'evaluation-recorded', 'failed'
        ))
        AND to_state IN (
            'queued', 'claimed', 'running', 'artifact-registered',
            'evaluation-recorded', 'failed'
        )
        AND record_version >= 1
    ),
    ADD CONSTRAINT model_retrain_job_events_evaluator_check CHECK (
        evaluator_id IS NULL OR evaluator_id ~ '^[a-z0-9][a-z0-9._:-]{2,79}$'
    ),
    ADD CONSTRAINT model_retrain_job_events_actor_check CHECK (
        (event_type = 'queued'
            AND actor_user_id IS NOT NULL AND worker_id IS NULL
            AND fencing_token IS NULL AND evaluator_id IS NULL)
        OR (event_type = 'evaluation-recorded'
            AND actor_user_id IS NULL AND worker_id IS NULL
            AND fencing_token IS NULL AND evaluator_id IS NOT NULL)
        OR (event_type NOT IN ('queued', 'evaluation-recorded')
            AND actor_user_id IS NULL AND worker_id IS NOT NULL
            AND fencing_token IS NOT NULL AND evaluator_id IS NULL)
    );

CREATE TABLE IF NOT EXISTS public.model_retrain_evaluations (
    job_id UUID PRIMARY KEY
        REFERENCES public.model_retrain_jobs(job_id) ON DELETE RESTRICT,
    approval_id UUID NOT NULL
        REFERENCES public.model_retrain_approval_requests(approval_id) ON DELETE RESTRICT,
    approved_payload_hash TEXT NOT NULL CHECK (approved_payload_hash ~ '^[0-9a-f]{64}$'),
    evaluator_id TEXT NOT NULL CHECK (evaluator_id ~ '^[a-z0-9][a-z0-9._:-]{2,79}$'),
    model_id TEXT NOT NULL CHECK (model_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
    candidate_commit_sha TEXT NOT NULL CHECK (candidate_commit_sha ~ '^[0-9a-f]{40}$'),
    artifact_sha256 TEXT NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
    feature_columns_sha256 TEXT NOT NULL CHECK (feature_columns_sha256 ~ '^[0-9a-f]{64}$'),
    observations_sha256 TEXT NOT NULL CHECK (observations_sha256 ~ '^[0-9a-f]{64}$'),
    contract_id TEXT NOT NULL CHECK (contract_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
    contract_sha256 TEXT NOT NULL CHECK (contract_sha256 ~ '^[0-9a-f]{64}$'),
    observed_at TIMESTAMPTZ NOT NULL,
    evaluation_report_sha256 TEXT NOT NULL UNIQUE CHECK (
        evaluation_report_sha256 ~ '^[0-9a-f]{64}$'
    ),
    sanitized_report JSONB NOT NULL CHECK (
        jsonb_typeof(sanitized_report) = 'object'
        AND octet_length(sanitized_report::TEXT) <= 65536
    ),
    acceptance_passed BOOLEAN NOT NULL CHECK (acceptance_passed = TRUE),
    trusted_promotion_evidence BOOLEAN NOT NULL DEFAULT FALSE CHECK (
        trusted_promotion_evidence = FALSE
    ),
    promotion_eligible BOOLEAN NOT NULL DEFAULT FALSE CHECK (promotion_eligible = FALSE),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    authoritative_record BOOLEAN NOT NULL DEFAULT TRUE CHECK (authoritative_record = TRUE)
);

ALTER TABLE public.model_retrain_evaluations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.model_retrain_evaluations FORCE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.model_retrain_evaluations
    FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON TABLE public.model_retrain_evaluations TO service_role;

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
        OR (OLD.job_state = 'artifact-registered' AND NEW.job_state = 'evaluation-recorded')
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
    IF NEW.job_state = 'evaluation-recorded' THEN
        IF OLD.job_state <> 'artifact-registered'
           OR OLD.evaluation_recorded IS DISTINCT FROM FALSE
           OR NEW.evaluation_recorded IS DISTINCT FROM TRUE
           OR NEW.acceptance_passed IS DISTINCT FROM TRUE
           OR NEW.evaluation_report_sha256 IS NULL
           OR NEW.evaluator_id IS NULL OR NEW.evaluated_at IS NULL
           OR NEW.promotion_eligible IS DISTINCT FROM FALSE THEN
            RAISE EXCEPTION 'model retrain evaluation transition is invalid' USING ERRCODE = '55000';
        END IF;
    ELSIF ROW(
        NEW.evaluation_recorded, NEW.acceptance_passed,
        NEW.evaluation_report_sha256, NEW.evaluator_id,
        NEW.evaluated_at, NEW.promotion_eligible
    ) IS DISTINCT FROM ROW(
        OLD.evaluation_recorded, OLD.acceptance_passed,
        OLD.evaluation_report_sha256, OLD.evaluator_id,
        OLD.evaluated_at, OLD.promotion_eligible
    ) THEN
        RAISE EXCEPTION 'model retrain evaluation identity is immutable' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public._reject_model_retrain_evaluation_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RAISE EXCEPTION 'model retrain evaluations are immutable' USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_model_retrain_evaluations_immutable ON public.model_retrain_evaluations;
CREATE TRIGGER trg_model_retrain_evaluations_immutable
    BEFORE UPDATE OR DELETE ON public.model_retrain_evaluations
    FOR EACH ROW EXECUTE FUNCTION public._reject_model_retrain_evaluation_mutation();

CREATE OR REPLACE FUNCTION public.register_model_retrain_accepted_evaluation(
    p_evaluator_id TEXT,
    p_job_id UUID,
    p_expected_version INTEGER,
    p_sanitized_report JSONB
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
    v_contract JSONB;
    v_evidence JSONB;
    v_checks JSONB;
    v_observed_at TIMESTAMPTZ;
    v_report_sha256 TEXT;
BEGIN
    IF p_evaluator_id IS NULL
       OR p_evaluator_id !~ '^[a-z0-9][a-z0-9._:-]{2,79}$'
       OR p_job_id IS NULL
       OR p_expected_version IS NULL OR p_expected_version < 1
       OR p_sanitized_report IS NULL
       OR jsonb_typeof(p_sanitized_report) <> 'object'
       OR octet_length(p_sanitized_report::TEXT) > 65536 THEN
        RAISE EXCEPTION 'invalid model retrain evaluation registration' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_job FROM public.model_retrain_jobs AS j
    WHERE j.job_id = p_job_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'model retrain job not found' USING ERRCODE = 'P0002';
    END IF;
    IF v_job.record_version <> p_expected_version THEN
        RAISE EXCEPTION 'model retrain job version conflict' USING ERRCODE = '40001';
    END IF;
    IF v_job.job_state <> 'artifact-registered'
       OR v_job.artifact_written IS DISTINCT FROM TRUE
       OR v_job.artifact_sha256 IS NULL THEN
        RAISE EXCEPTION 'model retrain job has no registrable artifact evaluation'
            USING ERRCODE = '55000';
    END IF;

    SELECT * INTO v_approval FROM public.model_retrain_approval_requests AS a
    WHERE a.approval_id = v_job.approval_id FOR SHARE;
    IF v_approval.approval_status <> 'approved'
       OR v_approval.expires_at <= v_now
       OR v_approval.job_created IS DISTINCT FROM TRUE
       OR v_approval.approved_payload_hash IS DISTINCT FROM v_job.approved_payload_hash THEN
        RAISE EXCEPTION 'model retrain approval no longer authorizes evaluation registration'
            USING ERRCODE = '42501';
    END IF;

    IF NOT (p_sanitized_report ?& ARRAY[
        'report_schema', 'schema_version', 'success', 'verdict', 'verdict_reason',
        'accepted', 'acceptance_required', 'evaluated_commit_sha', 'contract',
        'evidence', 'blockers', 'checks', 'failure_codes'
    ]) OR p_sanitized_report - ARRAY[
        'report_schema', 'schema_version', 'success', 'verdict', 'verdict_reason',
        'accepted', 'acceptance_required', 'evaluated_commit_sha', 'contract',
        'evidence', 'blockers', 'checks', 'failure_codes'
    ] <> '{}'::JSONB THEN
        RAISE EXCEPTION 'model acceptance report schema is invalid' USING ERRCODE = '22023';
    END IF;
    IF (p_sanitized_report ->> 'report_schema')
            IS DISTINCT FROM 'model-acceptance-gate-report'
       OR (p_sanitized_report -> 'schema_version') IS DISTINCT FROM '1'::JSONB
       OR (p_sanitized_report -> 'success') IS DISTINCT FROM 'true'::JSONB
       OR (p_sanitized_report ->> 'verdict') IS DISTINCT FROM 'accepted'
       OR (p_sanitized_report ->> 'verdict_reason')
            IS DISTINCT FROM 'all-approved-thresholds-pass'
       OR (p_sanitized_report -> 'accepted') IS DISTINCT FROM 'true'::JSONB
       OR (p_sanitized_report -> 'acceptance_required') IS DISTINCT FROM 'true'::JSONB
       OR (p_sanitized_report -> 'blockers') IS DISTINCT FROM '[]'::JSONB
       OR (p_sanitized_report -> 'failure_codes') IS DISTINCT FROM '[]'::JSONB THEN
        RAISE EXCEPTION 'model acceptance report is not an accepted promotion-mode result'
            USING ERRCODE = '55000';
    END IF;

    v_contract := p_sanitized_report -> 'contract';
    v_evidence := p_sanitized_report -> 'evidence';
    v_checks := p_sanitized_report -> 'checks';
    IF jsonb_typeof(v_contract) IS DISTINCT FROM 'object'
       OR NOT (v_contract ?& ARRAY['contract_id', 'sha256', 'status'])
       OR v_contract - ARRAY['contract_id', 'sha256', 'status'] <> '{}'::JSONB
       OR (v_contract ->> 'status') IS DISTINCT FROM 'approved'
       OR COALESCE(
            (v_contract ->> 'contract_id') ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$',
            FALSE
       ) IS NOT TRUE
       OR COALESCE((v_contract ->> 'sha256') ~ '^[0-9a-f]{64}$', FALSE) IS NOT TRUE THEN
        RAISE EXCEPTION 'model acceptance contract projection is invalid' USING ERRCODE = '22023';
    END IF;
    IF jsonb_typeof(v_evidence) IS DISTINCT FROM 'object'
       OR NOT (v_evidence ?& ARRAY[
            'model_id', 'model_artifact_sha256', 'model_feature_columns_sha256',
            'observations_sha256', 'observed_at'
       ])
       OR v_evidence - ARRAY[
            'model_id', 'model_artifact_sha256', 'model_feature_columns_sha256',
            'observations_sha256', 'observed_at'
       ] <> '{}'::JSONB
       OR COALESCE(
            (v_evidence ->> 'model_id') ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$',
            FALSE
       ) IS NOT TRUE
       OR (v_evidence ->> 'model_artifact_sha256') IS DISTINCT FROM v_job.artifact_sha256
       OR COALESCE(
            (v_evidence ->> 'model_feature_columns_sha256') ~ '^[0-9a-f]{64}$',
            FALSE
       ) IS NOT TRUE
       OR COALESCE(
            (v_evidence ->> 'observations_sha256') ~ '^[0-9a-f]{64}$',
            FALSE
       ) IS NOT TRUE THEN
        RAISE EXCEPTION 'model acceptance evidence projection is invalid' USING ERRCODE = '22023';
    END IF;
    IF (p_sanitized_report ->> 'evaluated_commit_sha')
         IS DISTINCT FROM (v_approval.dry_run_payload ->> 'git_commit')
       OR COALESCE(
            (p_sanitized_report ->> 'evaluated_commit_sha') ~ '^[0-9a-f]{40}$',
            FALSE
       ) IS NOT TRUE THEN
        RAISE EXCEPTION 'model acceptance candidate commit binding is invalid' USING ERRCODE = '55000';
    END IF;
    IF jsonb_typeof(v_checks) IS DISTINCT FROM 'object'
       OR NOT (v_checks ?& ARRAY[
            'contract_schema', 'contract_approved', 'evidence_schema_and_binding',
            'metrics_against_thresholds', 'promotion_policy'
       ])
       OR v_checks - ARRAY[
            'contract_schema', 'contract_approved', 'evidence_schema_and_binding',
            'metrics_against_thresholds', 'promotion_policy'
       ] <> '{}'::JSONB
       OR EXISTS (
            SELECT 1 FROM jsonb_each(v_checks) AS entry
            WHERE entry.value <> 'true'::JSONB
       ) THEN
        RAISE EXCEPTION 'model acceptance checks are invalid' USING ERRCODE = '55000';
    END IF;

    BEGIN
        v_observed_at := (v_evidence ->> 'observed_at')::TIMESTAMPTZ;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'model acceptance observed timestamp is invalid' USING ERRCODE = '22023';
    END;
    IF v_observed_at IS NULL
       OR v_observed_at < v_now - INTERVAL '7 days'
       OR v_observed_at > v_now + INTERVAL '5 minutes' THEN
        RAISE EXCEPTION 'model acceptance report is outside the freshness window'
            USING ERRCODE = '55000';
    END IF;

    v_report_sha256 := encode(
        extensions.digest(convert_to(p_sanitized_report::TEXT, 'UTF8'), 'sha256'),
        'hex'
    );
    INSERT INTO public.model_retrain_evaluations (
        job_id, approval_id, approved_payload_hash, evaluator_id, model_id,
        candidate_commit_sha, artifact_sha256, feature_columns_sha256,
        observations_sha256, contract_id, contract_sha256, observed_at,
        evaluation_report_sha256, sanitized_report, acceptance_passed,
        trusted_promotion_evidence, promotion_eligible, recorded_at,
        authoritative_record
    ) VALUES (
        v_job.job_id, v_job.approval_id, v_job.approved_payload_hash,
        p_evaluator_id, v_evidence ->> 'model_id',
        p_sanitized_report ->> 'evaluated_commit_sha',
        v_evidence ->> 'model_artifact_sha256',
        v_evidence ->> 'model_feature_columns_sha256',
        v_evidence ->> 'observations_sha256',
        v_contract ->> 'contract_id', v_contract ->> 'sha256', v_observed_at,
        v_report_sha256, p_sanitized_report, TRUE, FALSE, FALSE, v_now, TRUE
    );

    UPDATE public.model_retrain_jobs AS j SET
        job_state = 'evaluation-recorded', record_version = j.record_version + 1,
        evaluation_recorded = TRUE, acceptance_passed = TRUE,
        evaluation_report_sha256 = v_report_sha256,
        evaluator_id = p_evaluator_id, evaluated_at = v_now,
        promotion_eligible = FALSE
    WHERE j.job_id = p_job_id RETURNING * INTO v_job;

    INSERT INTO public.model_retrain_job_events (
        job_id, event_seq, event_type, actor_user_id, worker_id, evaluator_id,
        approval_id, approved_payload_hash, fencing_token,
        from_state, to_state, record_version, reason
    ) VALUES (
        v_job.job_id, v_job.record_version, 'evaluation-recorded', NULL, NULL,
        p_evaluator_id, v_job.approval_id, v_job.approved_payload_hash, NULL,
        'artifact-registered', 'evaluation-recorded', v_job.record_version,
        'Accepted evaluation was recorded without creating trusted promotion evidence.'
    );
    RETURN QUERY SELECT * FROM public.model_retrain_jobs AS j WHERE j.job_id = p_job_id;
END;
$$;

REVOKE ALL ON FUNCTION public._guard_model_retrain_job_update()
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public._reject_model_retrain_evaluation_mutation()
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.register_model_retrain_accepted_evaluation(
    TEXT, UUID, INTEGER, JSONB
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.register_model_retrain_accepted_evaluation(
    TEXT, UUID, INTEGER, JSONB
) TO service_role;
