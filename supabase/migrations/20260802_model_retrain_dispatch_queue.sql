-- ============================================================
-- Service-only bounded dispatcher projection.
-- This is a read-only preliminary queue view. Claim/execution still requires
-- the existing CAS lease, fence, approval recheck, and execution bundle.
-- ============================================================

CREATE OR REPLACE FUNCTION public.list_dispatchable_model_retrain_jobs(
    p_dispatcher_id TEXT,
    p_execution_policy TEXT,
    p_candidate_commit_sha TEXT,
    p_active_model_id TEXT,
    p_limit INTEGER
)
RETURNS TABLE (
    job_id UUID,
    record_version INTEGER,
    execution_policy TEXT,
    data_snapshot_sha256 TEXT,
    candidate_commit_sha TEXT,
    active_model_id TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF p_dispatcher_id IS NULL
       OR p_dispatcher_id !~ '^[a-z0-9][a-z0-9._:-]{2,49}$'
       OR p_execution_policy NOT IN ('staging-train', 'sandbox-train')
       OR p_candidate_commit_sha IS NULL
       OR p_candidate_commit_sha !~ '^[0-9a-f]{40}$'
       OR p_candidate_commit_sha = repeat('0', 40)
       OR p_active_model_id IS NULL
       OR p_active_model_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
       OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 5 THEN
        RAISE EXCEPTION 'invalid model retrain dispatch scan' USING ERRCODE = '22023';
    END IF;

    RETURN QUERY
    SELECT
        j.job_id,
        j.record_version,
        j.execution_policy,
        a.dry_run_payload->>'data_snapshot_id' AS data_snapshot_sha256,
        a.dry_run_payload->>'git_commit' AS candidate_commit_sha,
        a.dry_run_payload->>'active_model_id' AS active_model_id
    FROM public.model_retrain_jobs AS j
    JOIN public.model_retrain_approval_requests AS a
      ON a.approval_id = j.approval_id
    WHERE j.job_state = 'queued'
      AND j.record_version >= 1
      AND j.execution_started = FALSE
      AND j.artifact_written = FALSE
      AND j.worker_id IS NULL AND j.fencing_token IS NULL
      AND j.lease_expires_at IS NULL
      AND j.execution_policy = p_execution_policy
      AND j.submitted_by = j.requested_by
      AND a.approval_status = 'approved'
      AND a.expires_at > clock_timestamp()
      AND a.job_created = TRUE
      AND a.execution_enabled = FALSE
      AND a.approved_payload_hash = j.approved_payload_hash
      AND a.execution_policy = j.execution_policy
      AND a.dry_run_payload->>'state' = 'preview-ready'
      AND a.dry_run_payload->>'target' = 'win'
      AND a.dry_run_payload->>'model_type' = 'lightgbm'
      AND a.dry_run_payload->>'git_commit' = p_candidate_commit_sha
      AND a.dry_run_payload->>'active_model_id' = p_active_model_id
      AND COALESCE(
          (a.dry_run_payload->>'data_snapshot_id') ~ '^[0-9a-f]{64}$',
          FALSE
      ) = TRUE
      AND a.dry_run_payload->>'data_snapshot_id' <> repeat('0', 64)
      AND a.dry_run_payload->'safety_checks' @>
          '[{"key":"future_field_exclusion","status":"pass"},
            {"key":"out_of_time_split","status":"pass"},
            {"key":"active_model_immutable","status":"pass"},
            {"key":"production_write_blocked","status":"pass"},
            {"key":"path_input_rejected","status":"pass"},
            {"key":"data_snapshot_bound","status":"pass"},
            {"key":"candidate_commit_bound","status":"pass"}]'::JSONB
    ORDER BY j.submitted_at, j.job_id
    LIMIT p_limit;
END;
$$;

REVOKE ALL ON FUNCTION public.list_dispatchable_model_retrain_jobs(
    TEXT, TEXT, TEXT, TEXT, INTEGER
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.list_dispatchable_model_retrain_jobs(
    TEXT, TEXT, TEXT, TEXT, INTEGER
) TO service_role;
