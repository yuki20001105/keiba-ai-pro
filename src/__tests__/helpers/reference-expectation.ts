import { BETTING_POLICY_VERSION } from '@/lib/betting-policy'
import type { RacePredictResult } from '@/lib/race-analysis-types'

export const REFERENCE_NOW = Date.parse('2026-10-11T01:00:00Z')
export const referenceRace = { race_id: '202605040301', race_name: '参考試算レース', venue: '東京', venue_code: '05', race_no: 1,
  date: '20261011', distance: 1600, track_type: '芝', num_horses: 2 }

/** Artificial display evidence only; never a live input or purchase receipt. */
export function referenceExpectationResult(profile: 'local-db-shared-v1' | 'local-db-shared-optimized-v1' = 'local-db-shared-v1'): RacePredictResult {
  const featureCount = profile === 'local-db-shared-optimized-v1' ? 46 : 47
  return {
    success: true, model_id: 'shared-test-model', race_info: { ...referenceRace }, best_bet_type: null, pro_evaluation: null,
    input_diagnostics: {
      schema: 'prediction-input-contract-v1', feature_pipeline: profile, status: 'unverified',
      expected_count: featureCount, actual_count: featureCount, semantic_parity_verified: true, missing_columns: [], restored_columns: [],
      blocked_columns: [], all_missing_columns: [], source_hash_matches: true, history_available: true,
      live_capture_hash_bound: false, current_outcomes_consumed: false, missing_value_rate: 0.1,
      issue_codes: ['pre_race_db_capture_unverified'],
    },
    predictions: [
      { horse_number: 1, horse_name: '参考馬A', jockey_name: '騎手A', trainer_name: '調教師A', sex: '牡', age: 3,
        horse_weight: 480, popularity: 1, odds: 5.7, win_probability: 0.13, p_norm: 0.13, p_raw: 0.8,
        expected_value: null, predicted_rank: 1 },
      { horse_number: 2, horse_name: '参考馬B', jockey_name: '騎手B', trainer_name: '調教師B', sex: '牡', age: 3,
        horse_weight: 480, popularity: 2, odds: 1.2, win_probability: 0.87, p_norm: 0.87, p_raw: 0.7,
        expected_value: null, predicted_rank: 2 },
    ],
    recommendation: {
      policy_version: BETTING_POLICY_VERSION, probability_basis: 'calibrated', input_verified: false,
      decision: 'skip', action: '見送り', reason: '発走前の取得証跡が未確認', reason_codes: ['input_contract_unverified'],
      tickets: [], purchase_count: 0, unit_price: 0, total_cost: 0, expected_return: 0,
      bankroll: 10000, risk_mode: 'balanced', budget: 300, per_race_limit: 300,
      evaluated_at: new Date(REFERENCE_NOW).toISOString(), valid_until: new Date(REFERENCE_NOW + 300000).toISOString(),
    },
    reference_expectation: {
      schema: 'calibrated-win-reference-v1', basis: 'calibrated_probability_displayed_odds',
      purchase_eligible: false, capture_verified: false, odds_freshness_verified: false,
      model_id: 'shared-test-model', race_id: referenceRace.race_id,
      computed_at: new Date(REFERENCE_NOW).toISOString(), valid_until: new Date(REFERENCE_NOW + 300000).toISOString(),
      items: [{ horse_number: 1, probability: 0.13, odds: 5.7, expected_value: 0.13 * 5.7 },
        { horse_number: 2, probability: 0.87, odds: 1.2, expected_value: 0.87 * 1.2 }],
    },
  }
}
