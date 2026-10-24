import type { PredictionInputDiagnostics } from '@/lib/race-analysis-types'

/** Fixed served profiles; unknown or research-only subsets confer no authority. */
export function sharedFeatureProfileCount(pipeline: unknown): 46 | 47 | 80 | null {
  if (pipeline === 'local-db-shared-v1') return 47
  if (pipeline === 'local-db-shared-optimized-v1') return 46
  if (pipeline === 'local-db-context-v1') return 80
  return null
}

/** Display-only parity evidence; never qualifies inputs for EV or purchases. */
export function sharedInputFormulaMatchedCaptureUnverified(value: PredictionInputDiagnostics | null | undefined): boolean {
  const expectedCount = sharedFeatureProfileCount(value?.feature_pipeline)
  return value?.schema === 'prediction-input-contract-v1'
    && expectedCount !== null
    && value.status === 'unverified'
    && value.expected_count === expectedCount
    && Number.isInteger(value.actual_count) && value.actual_count === value.expected_count
    && Array.isArray(value.missing_columns) && value.missing_columns.length === 0
    && Array.isArray(value.blocked_columns) && value.blocked_columns.length === 0
    && value.source_hash_matches === true && value.semantic_parity_verified === true
    && value.history_available === true && value.live_capture_hash_bound === false
    && Array.isArray(value.issue_codes) && value.issue_codes.length > 0
    && value.issue_codes.every(code => code === 'pre_race_db_capture_unverified' || code === 'pre_race_db_capture_failed')
}

/** Same fail-closed evidence contract as the server; does not grant validation. */
export function predictionInputVerified(value: PredictionInputDiagnostics | null | undefined): boolean {
  const sharedCount = sharedFeatureProfileCount(value?.feature_pipeline)
  const profileVerified = value?.feature_pipeline === undefined
    || sharedCount !== null && value.expected_count === sharedCount && value.live_capture_hash_bound === true
  return value?.schema === 'prediction-input-contract-v1'
    && value.reference_only !== true
    && profileVerified
    && value.status === 'aligned'
    && Number.isInteger(value.expected_count) && value.expected_count > 0
    && Number.isInteger(value.actual_count) && value.actual_count === value.expected_count
    && Array.isArray(value.missing_columns) && value.missing_columns.length === 0
    && Array.isArray(value.blocked_columns) && value.blocked_columns.length === 0
    && Array.isArray(value.issue_codes) && value.issue_codes.length === 0
    && value.source_hash_matches === true && value.history_available === true
    && value.semantic_parity_verified === true
}
