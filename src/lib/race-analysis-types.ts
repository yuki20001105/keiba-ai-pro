// レース分析ページ専用の型定義
// NOTE: 共有の PredictResult (lib/types.ts) とは別物。こちらはより詳細な /api/analyze-race レスポンスに対応。

export type Prediction = {
  horse_number: number
  horse_name: string
  jockey_name: string
  trainer_name: string
  sex: string
  age: number | null
  horse_weight: number | null
  odds: number | null
  popularity: number | null
  win_probability: number
  p_raw: number
  p_norm: number
  expected_value: number | null
  predicted_rank: number
  explanation?: PredictionExplanation | null
}

export type FeatureContribution = {
  feature: string
  label: string
  description: string
  value: string | number | boolean | null
  contribution: number
  impact_pct: number
  direction: 'positive' | 'negative'
}

export type PredictionExplanation = {
  method: 'tree_shap'
  base_value: number
  features: FeatureContribution[]
}

export type RaceInfo = {
  race_id: string
  race_name: string
  venue: string
  date: string
  distance: number
  track_type: string
  num_horses: number
  weather?: string
  field_condition?: string
}

export type RecommendedTicket = {
  bet_type: string
  combination: string
  horse_numbers: number[]
  stake: number
  probability: number
  odds: number
  expected_value: number
  conservative_expected_value: number
}

export type Recommendation = {
  action: string
  reason: string
  purchase_count: number
  unit_price: number
  total_cost: number
  expected_return: number
  decision?: 'skip' | 'buy' | 'priority'
  reason_codes?: string[]
  policy_version?: string
  probability_basis?: 'calibrated' | 'uncalibrated_score'
  input_verified?: boolean
  tickets?: RecommendedTicket[]
  budget?: number
  per_race_limit?: number
  max_points?: number
  risk_mode?: 'conservative' | 'balanced' | 'aggressive'
  bankroll?: number
  evaluated_at?: string
  valid_until?: string | null
}

export type RacePredictResult = {
  success: boolean
  freshness?: import('@/lib/prediction-freshness').PredictionFreshness | null
  reference_expectation?: ReferenceExpectation | null
  reference_bets?: ReferenceBets | null
  performance_snapshot_id?: string | null
  performance_recording_error?: string | null
  performance_allocation?: { day_budget: number; allocated_at: string }
  model_id?: string | null
  input_diagnostics?: PredictionInputDiagnostics | null
  explanation_method?: 'tree_shap' | null
  race_info: RaceInfo
  predictions: Prediction[]
  recommendation: Recommendation | null
  best_bet_type: string | null
  pro_evaluation: {
    recommended_action?: string
    probability_basis?: Recommendation['probability_basis']
    confidence_level?: string
    reason?: string
    race_level?: string
    confidence?: number
  } | null
  bet_types?: Record<string, { combination: string }[]>
}

/** Display-only calculation; never grants a purchase or performance authority. */
export type ReferenceExpectation = {
  schema: 'calibrated-win-reference-v1'
  basis: 'calibrated_probability_displayed_odds'
  purchase_eligible: false
  capture_verified: false
  odds_freshness_verified: false
  computed_at: string
  valid_until: string
  model_id: string
  race_id: string
  items: { horse_number: number; probability: number; odds: number; expected_value: number }[]
}

export type ReferenceBets = {
  schema: 'rank-reference-bets-v1'
  basis: 'ranking_only'
  purchase_eligible: false
  monetary_amounts_assigned: false
  tickets: { bet_type: string; combination: string; horse_numbers: number[] }[]
}

export type PredictionInputDiagnostics = {
  schema: 'prediction-input-contract-v1'
  scope?: string
  feature_pipeline?: string
  reference_only?: boolean
  live_capture_hash_bound?: boolean
  current_outcomes_consumed?: boolean
  missing_value_rate?: number
  status: 'aligned' | 'degraded' | 'unverified'
  semantic_parity_verified: boolean
  expected_count: number
  actual_count: number
  missing_columns: string[]
  restored_columns: string[]
  blocked_columns: string[]
  all_missing_columns: string[]
  source_hash_matches: boolean | null
  history_available: boolean | null
  issue_codes: string[]
}

export type FeatureData = {
  feature_columns: string[]
  records: Record<string, unknown>[]
  horse_count: number
  feature_count: number
}

export type CacheEntry = {
  predictResult: RacePredictResult
  featData: FeatureData | null
  cachedAt: number
  ownerMatches?: boolean
}
