import type { PredictionInputDiagnostics } from '@/lib/race-analysis-types'

/** Artificial evidence only; the active production adapter does not grant it. */
export const verifiedPredictionInput: PredictionInputDiagnostics = {
  schema: 'prediction-input-contract-v1', status: 'aligned', semantic_parity_verified: true,
  expected_count: 3, actual_count: 3, missing_columns: [], restored_columns: [],
  blocked_columns: [], all_missing_columns: [], source_hash_matches: true,
  history_available: true, issue_codes: [],
}
