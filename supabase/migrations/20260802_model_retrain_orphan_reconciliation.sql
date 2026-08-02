-- ============================================================
-- Service-only expired-lease and orphan-artifact reconciliation boundary.
-- Only terminal failed jobs without an immutable artifact registration can
-- expose a cleanup candidate. Storage deletion remains outside PostgreSQL,
-- and every bounded scan is recorded after the observed outcome is rechecked.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.model_retrain_orphan_reconciliation_runs (
    run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reconciler_id TEXT NOT NULL CHECK (
        reconciler_id ~ '^[a-z0-9][a-z0-9._:-]{2,79}$'
    ),
    min_age_seconds INTEGER NOT NULL CHECK (
        min_age_seconds BETWEEN 3600 AND 604800
    ),
    requested_limit INTEGER NOT NULL CHECK (requested_limit BETWEEN 1 AND 50),
    candidate_count INTEGER NOT NULL CHECK (candidate_count BETWEEN 0 AND 50),
    deleted_count INTEGER NOT NULL CHECK (deleted_count BETWEEN 0 AND 50),
    not_found_count INTEGER NOT NULL CHECK (not_found_count BETWEEN 0 AND 50),
    failed_count INTEGER NOT NULL CHECK (failed_count BETWEEN 0 AND 50),
    observations JSONB NOT NULL CHECK (
        jsonb_typeof(observations) = 'array'
        AND jsonb_array_length(observations) = candidate_count
        AND octet_length(observations::TEXT) <= 65536
    ),
    successful BOOLEAN NOT NULL CHECK (successful = (failed_count = 0)),
    observed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    authoritative_record BOOLEAN NOT NULL DEFAULT TRUE CHECK (
        authoritative_record = TRUE
    ),
    CHECK (candidate_count = deleted_count + not_found_count + failed_count)
);

ALTER TABLE public.model_retrain_orphan_reconciliation_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.model_retrain_orphan_reconciliation_runs FORCE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.model_retrain_orphan_reconciliation_runs
    FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON TABLE public.model_retrain_orphan_reconciliation_runs TO service_role;

CREATE OR REPLACE FUNCTION public._reject_model_retrain_orphan_run_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RAISE EXCEPTION 'model retrain orphan reconciliation runs are immutable'
        USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_model_retrain_orphan_runs_immutable
    ON public.model_retrain_orphan_reconciliation_runs;
CREATE TRIGGER trg_model_retrain_orphan_runs_immutable
    BEFORE UPDATE OR DELETE ON public.model_retrain_orphan_reconciliation_runs
    FOR EACH ROW EXECUTE FUNCTION public._reject_model_retrain_orphan_run_mutation();

CREATE OR REPLACE FUNCTION public.list_expired_model_retrain_job_candidates(
    p_reconciler_id TEXT,
    p_limit INTEGER
)
RETURNS TABLE (
    job_id UUID,
    record_version INTEGER,
    job_state TEXT,
    lease_expires_at TIMESTAMPTZ
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF p_reconciler_id IS NULL
       OR p_reconciler_id !~ '^[a-z0-9][a-z0-9._:-]{2,79}$'
       OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 50 THEN
        RAISE EXCEPTION 'invalid model retrain expired lease scan'
            USING ERRCODE = '22023';
    END IF;
    RETURN QUERY
    SELECT j.job_id, j.record_version, j.job_state, j.lease_expires_at
    FROM public.model_retrain_jobs AS j
    WHERE j.job_state IN ('claimed', 'running')
      AND j.lease_expires_at <= clock_timestamp()
    ORDER BY j.lease_expires_at, j.job_id
    LIMIT p_limit;
END;
$$;

CREATE OR REPLACE FUNCTION public.list_model_retrain_orphan_candidates(
    p_reconciler_id TEXT,
    p_min_age_seconds INTEGER,
    p_limit INTEGER
)
RETURNS TABLE (
    job_id UUID,
    record_version INTEGER,
    failure_code TEXT,
    object_name TEXT,
    artifact_sha256 TEXT,
    storage_created_at TIMESTAMPTZ,
    cleanup_token TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF p_reconciler_id IS NULL
       OR p_reconciler_id !~ '^[a-z0-9][a-z0-9._:-]{2,79}$'
       OR p_min_age_seconds IS NULL
       OR p_min_age_seconds NOT BETWEEN 3600 AND 604800
       OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 50 THEN
        RAISE EXCEPTION 'invalid model retrain orphan scan' USING ERRCODE = '22023';
    END IF;
    RETURN QUERY
    WITH eligible_objects AS (
        SELECT
            o.name,
            o.created_at,
            substring(
                o.name FROM '^retrain/([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})/'
            )::UUID AS parsed_job_id,
            split_part(split_part(o.name, '/', 3), '.', 1) AS parsed_sha256
        FROM storage.objects AS o
        WHERE o.bucket_id = 'models'
          AND o.name ~ '^retrain/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/[0-9a-f]{64}\.joblib$'
          AND o.created_at <= clock_timestamp() - make_interval(secs => p_min_age_seconds)
    )
    SELECT
        j.job_id,
        j.record_version,
        j.failure_code,
        o.name AS object_name,
        o.parsed_sha256 AS artifact_sha256,
        o.created_at AS storage_created_at,
        encode(
            extensions.digest(
                convert_to(
                    j.job_id::TEXT || '|' || o.name || '|' || o.parsed_sha256
                    || '|' || to_char(o.created_at AT TIME ZONE 'UTC',
                        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                    'UTF8'
                ),
                'sha256'
            ),
            'hex'
        ) AS cleanup_token
    FROM eligible_objects AS o
    JOIN public.model_retrain_jobs AS j ON j.job_id = o.parsed_job_id
    WHERE j.job_state = 'failed'
      AND j.artifact_written = FALSE
      AND j.artifact_uri IS NULL
      AND j.artifact_sha256 IS NULL
      AND j.finished_at IS NOT NULL
      AND j.finished_at <= clock_timestamp() - make_interval(secs => p_min_age_seconds)
      AND NOT EXISTS (
          SELECT 1 FROM public.model_retrain_artifacts AS a
          WHERE a.job_id = j.job_id OR a.object_name = o.name
      )
    ORDER BY o.created_at, o.name
    LIMIT p_limit;
END;
$$;

CREATE OR REPLACE FUNCTION public.record_model_retrain_orphan_reconciliation(
    p_reconciler_id TEXT,
    p_min_age_seconds INTEGER,
    p_limit INTEGER,
    p_observations JSONB
)
RETURNS SETOF public.model_retrain_orphan_reconciliation_runs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_now TIMESTAMPTZ := clock_timestamp();
    v_item JSONB;
    v_job_id UUID;
    v_object_name TEXT;
    v_sha256 TEXT;
    v_storage_created_at TIMESTAMPTZ;
    v_expected_token TEXT;
    v_outcome TEXT;
    v_candidate_count INTEGER;
    v_deleted_count INTEGER := 0;
    v_not_found_count INTEGER := 0;
    v_failed_count INTEGER := 0;
    v_run public.model_retrain_orphan_reconciliation_runs%ROWTYPE;
BEGIN
    IF p_reconciler_id IS NULL
       OR p_reconciler_id !~ '^[a-z0-9][a-z0-9._:-]{2,79}$'
       OR p_min_age_seconds IS NULL
       OR p_min_age_seconds NOT BETWEEN 3600 AND 604800
       OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 50
       OR p_observations IS NULL
       OR jsonb_typeof(p_observations) <> 'array'
       OR jsonb_array_length(p_observations) > p_limit
       OR octet_length(p_observations::TEXT) > 65536 THEN
        RAISE EXCEPTION 'invalid model retrain orphan reconciliation report'
            USING ERRCODE = '22023';
    END IF;
    v_candidate_count := jsonb_array_length(p_observations);

    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements(p_observations) AS item
        GROUP BY item->>'object_name', item->>'cleanup_token'
        HAVING count(*) <> 1
    ) THEN
        RAISE EXCEPTION 'model retrain orphan observations contain duplicates'
            USING ERRCODE = '22023';
    END IF;

    IF v_candidate_count = 0 AND EXISTS (
        SELECT 1 FROM public.list_model_retrain_orphan_candidates(
            p_reconciler_id, p_min_age_seconds, 1
        )
    ) THEN
        RAISE EXCEPTION 'model retrain orphan reconciliation omitted candidates'
            USING ERRCODE = '55000';
    END IF;

    FOR v_item IN SELECT value FROM jsonb_array_elements(p_observations)
    LOOP
        IF jsonb_typeof(v_item) <> 'object'
           OR NOT (v_item ?& ARRAY[
                'job_id', 'object_name', 'artifact_sha256',
                'storage_created_at', 'cleanup_token', 'outcome'
           ])
           OR v_item - ARRAY[
                'job_id', 'object_name', 'artifact_sha256',
                'storage_created_at', 'cleanup_token', 'outcome'
           ] <> '{}'::JSONB THEN
            RAISE EXCEPTION 'model retrain orphan observation schema is invalid'
                USING ERRCODE = '22023';
        END IF;
        BEGIN
            v_job_id := (v_item->>'job_id')::UUID;
            v_storage_created_at := (v_item->>'storage_created_at')::TIMESTAMPTZ;
        EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'model retrain orphan observation identity is invalid'
                USING ERRCODE = '22023';
        END;
        v_object_name := v_item->>'object_name';
        v_sha256 := v_item->>'artifact_sha256';
        v_outcome := v_item->>'outcome';
        v_expected_token := encode(
            extensions.digest(
                convert_to(
                    v_job_id::TEXT || '|' || v_object_name || '|' || v_sha256
                    || '|' || to_char(v_storage_created_at AT TIME ZONE 'UTC',
                        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                    'UTF8'
                ),
                'sha256'
            ),
            'hex'
        );
        IF v_sha256 !~ '^[0-9a-f]{64}$'
           OR v_object_name IS DISTINCT FROM
                'retrain/' || v_job_id::TEXT || '/' || v_sha256 || '.joblib'
           OR (v_item->>'cleanup_token') IS DISTINCT FROM v_expected_token
           OR v_outcome NOT IN ('deleted', 'not-found', 'delete-failed')
           OR v_storage_created_at > v_now - make_interval(secs => p_min_age_seconds)
           OR NOT EXISTS (
                SELECT 1 FROM public.model_retrain_jobs AS j
                WHERE j.job_id = v_job_id
                  AND j.job_state = 'failed'
                  AND j.artifact_written = FALSE
                  AND j.artifact_uri IS NULL AND j.artifact_sha256 IS NULL
                  AND j.finished_at <= v_now - make_interval(secs => p_min_age_seconds)
           )
           OR EXISTS (
                SELECT 1 FROM public.model_retrain_artifacts AS a
                WHERE a.job_id = v_job_id OR a.object_name = v_object_name
           ) THEN
            RAISE EXCEPTION 'model retrain orphan observation binding is invalid'
                USING ERRCODE = '55000';
        END IF;

        IF v_outcome IN ('deleted', 'not-found') THEN
            IF EXISTS (
                SELECT 1 FROM storage.objects AS o
                WHERE o.bucket_id = 'models' AND o.name = v_object_name
            ) THEN
                RAISE EXCEPTION 'model retrain orphan object still exists'
                    USING ERRCODE = '55000';
            END IF;
            IF v_outcome = 'deleted' THEN
                v_deleted_count := v_deleted_count + 1;
            ELSE
                v_not_found_count := v_not_found_count + 1;
            END IF;
        ELSE
            IF NOT EXISTS (
                SELECT 1 FROM storage.objects AS o
                WHERE o.bucket_id = 'models' AND o.name = v_object_name
                  AND o.created_at = v_storage_created_at
            ) THEN
                RAISE EXCEPTION 'model retrain failed cleanup object is unavailable'
                    USING ERRCODE = '55000';
            END IF;
            v_failed_count := v_failed_count + 1;
        END IF;
    END LOOP;

    INSERT INTO public.model_retrain_orphan_reconciliation_runs (
        reconciler_id, min_age_seconds, requested_limit,
        candidate_count, deleted_count, not_found_count, failed_count,
        observations, successful, observed_at, authoritative_record
    ) VALUES (
        p_reconciler_id, p_min_age_seconds, p_limit,
        v_candidate_count, v_deleted_count, v_not_found_count, v_failed_count,
        p_observations, v_failed_count = 0, v_now, TRUE
    ) RETURNING * INTO v_run;
    RETURN QUERY SELECT v_run.*;
END;
$$;

REVOKE ALL ON FUNCTION public._reject_model_retrain_orphan_run_mutation()
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.list_expired_model_retrain_job_candidates(TEXT, INTEGER)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.list_model_retrain_orphan_candidates(TEXT, INTEGER, INTEGER)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.record_model_retrain_orphan_reconciliation(
    TEXT, INTEGER, INTEGER, JSONB
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.list_expired_model_retrain_job_candidates(TEXT, INTEGER)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.list_model_retrain_orphan_candidates(TEXT, INTEGER, INTEGER)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.record_model_retrain_orphan_reconciliation(
    TEXT, INTEGER, INTEGER, JSONB
) TO service_role;
