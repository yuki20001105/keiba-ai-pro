export type RetrainDryRunState = 'preview-ready' | 'preview-warn' | 'preview-fail'
export type RetrainDryRunSafetyCheckStatus = 'pass' | 'warn' | 'fail'
export type RetrainApprovalStatus = 'pending' | 'approved' | 'rejected' | 'expired' | 'invalidated'
export type RetrainApprovalExecutionPolicy = 'read-only-preview' | 'staging-train' | 'sandbox-train'
export type RetrainApprovalAllowedAction = 'submit_approved_retrain' | 'view_approval_status' | 'view_job_status'
export type RetrainApprovalInvalidationReason =
  | 'payload-mismatch'
  | 'hash-mismatch'
  | 'expired'
  | 'active-model-changed'
  | 'feature-contract-changed'
  | 'code-version-changed'
  | 'manual-invalidation'
  | 'other'

export type RetrainDryRunPeriod = {
  start: string | null
  end: string | null
}

export type RetrainDryRunExpectedOutput = string

export type RetrainDryRunSafetyCheck = {
  key: string
  status: RetrainDryRunSafetyCheckStatus
  note: string
}

export type RetrainDryRunPreview = {
  target: string
  model_type: string
  train_period: RetrainDryRunPeriod
  validation_period: RetrainDryRunPeriod
  feature_count: number
  selected_features: string[]
  removed_features: string[]
  expected_outputs: RetrainDryRunExpectedOutput[]
  estimated_runtime: {
    unit: string
    min: number
    max: number
    note: string
  }
  safety_checks: RetrainDryRunSafetyCheck[]
  source_model_id?: string | null
  active_model_id?: string | null
  feature_contract_hash?: string
  data_snapshot_id?: string
  code_version?: string
  git_commit?: string
  created_by?: string
  state?: RetrainDryRunState
  warnings?: string[]
  notes?: string[]
}

export type RetrainDryRunPayload = RetrainDryRunPreview & {
  dry_run_id: string
  generated_at: string
  source_model_id: string | null
  active_model_id: string | null
  feature_contract_hash: string
  data_snapshot_id: string
  code_version: string
  git_commit: string
  created_by: string
  state: RetrainDryRunState
}

export type RetrainDryRunRequest = {
  action?: 'retrain_dry_run' | string
  target?: string
  model_type?: string
  train_period?: RetrainDryRunPeriod | null
  validation_period?: RetrainDryRunPeriod | null
  selected_features?: string[]
  removed_features?: string[]
  source_model_id?: string | null
  active_model_id?: string | null
  feature_contract_hash?: string
  data_snapshot_id?: string
  code_version?: string
  git_commit?: string
  created_by?: string
  state?: RetrainDryRunState
  [key: string]: unknown
}

export type RetrainApprovalRecord = {
  approval_id: string
  dry_run_id: string
  approved_by: string | null
  approved_at: string | null
  approval_status: RetrainApprovalStatus
  approval_comment: string
  approved_payload_hash: string
  requested_by: string
  requested_at: string
  expires_at: string
  invalidation_reason: RetrainApprovalInvalidationReason | null
  execution_policy: RetrainApprovalExecutionPolicy
  allowed_actions: RetrainApprovalAllowedAction[]
}

export type ApprovedRetrainJobPreconditions = {
  approval_status: RetrainApprovalStatus
  payload_hash_matches: boolean
  approval_not_expired: boolean
  active_model_unchanged: boolean
  feature_contract_unchanged: boolean
  code_version_unchanged: boolean
  admin_allowed: boolean
  production_write_blocked: boolean
  artifact_write_allowed: boolean
}

export type ApprovedRetrainJobSubmitRequest = {
  approval_id: string
  dry_run_id: string
  requested_by: string
  execution_policy: RetrainApprovalExecutionPolicy
  allowed_actions: RetrainApprovalAllowedAction[]
}

export type ApprovedRetrainJobSubmitResult = {
  success: boolean
  state: 'pass' | 'warn' | 'fail'
  code: string
  job_id: string | null
  approval_id: string
  dry_run_id: string
  started_at: string | null
  message: string
  preconditions: ApprovedRetrainJobPreconditions
}

export type ActiveModelSwitchApprovalStatus = RetrainApprovalStatus

export type ActiveModelSwitchApprovalRecord = {
  approval_id: string
  requested_by: string
  requested_at: string
  approved_by: string | null
  approved_at: string | null
  approval_status: ActiveModelSwitchApprovalStatus
  approval_comment: string
  target_model_id: string
  target_feature_contract_hash: string
  invalidation_reason: RetrainApprovalInvalidationReason | null
  expires_at: string
}
