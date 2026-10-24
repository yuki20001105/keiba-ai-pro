import { describe, expect, it } from 'vitest'
import { predictionExpectationDisplay } from '@/lib/prediction-expectation-display'
import { predictionInputVerified } from '@/lib/prediction-input-contract'
import { allocateDayBudget, selectedTickets } from '@/lib/betting-policy'
import type { RacePredictResult } from '@/lib/race-analysis-types'
import { referenceExpectationResult, REFERENCE_NOW } from './helpers/reference-expectation'
import { verifiedPredictionInput } from './helpers/verified-prediction-input'

describe('independent reference expectation display contract', () => {
  it.each([false, true])('does not borrow display or purchase EV authority for a context candidate: %s', referenceOnly => {
    const result = referenceExpectationResult()
    Object.assign(result.input_diagnostics!, { feature_pipeline: 'local-db-context-v1',
      expected_count: 80, actual_count: 80, reference_only: referenceOnly })
    const before = JSON.stringify(result)
    expect(predictionExpectationDisplay(result, result.recommendation, REFERENCE_NOW).mode).toBe('unavailable')
    expect(JSON.stringify(result)).toBe(before)
  })
  it.each(['local-db-shared-v1', 'local-db-shared-optimized-v1'] as const)(
    'displays the proven server trial without granting or mutating any authority: %s', profile => {
    const result = referenceExpectationResult(profile)
    const before = JSON.stringify(result)
    const display = predictionExpectationDisplay(result, result.recommendation, REFERENCE_NOW, result.race_info.race_id)
    expect(display.mode).toBe('reference')
    expect(display.label).toBe('参考期待値')
    expect(display.values.get(1)).toBeCloseTo(0.741)
    expect(predictionInputVerified(result.input_diagnostics)).toBe(false)
    expect(selectedTickets(result.recommendation, REFERENCE_NOW)).toEqual([])
    expect(allocateDayBudget({ race: result.recommendation }, 1000, REFERENCE_NOW).race).toMatchObject({
      decision: 'skip', input_verified: false, tickets: [], total_cost: 0, expected_return: 0,
    })
    expect(JSON.stringify(result)).toBe(before)
  })

  it('allows a safe partial item set while a missing-odds horse remains blank', () => {
    const result = referenceExpectationResult()
    result.predictions[1].odds = null
    result.reference_expectation!.items.pop()
    const display = predictionExpectationDisplay(result, result.recommendation, REFERENCE_NOW)
    expect(display.mode).toBe('reference')
    expect(display.values.get(1)).toBeCloseTo(0.741)
    expect(display.values.has(2)).toBe(false)
  })

  it('accepts explicit UTC offsets and a failed capture as display-only evidence', () => {
    const result = referenceExpectationResult()
    result.reference_expectation!.computed_at = '2026-10-11T01:00:00.000000+00:00'
    result.reference_expectation!.valid_until = '2026-10-11T01:05:00.000000+00:00'
    result.input_diagnostics!.issue_codes = ['pre_race_db_capture_failed']
    expect(predictionExpectationDisplay(result, result.recommendation, REFERENCE_NOW).mode).toBe('reference')
  })

  const failures: { name: string; change: (result: RacePredictResult) => void }[] = [
    { name: 'old cache with no field', change: r => { delete r.reference_expectation } },
    { name: 'null contract', change: r => { r.reference_expectation = null } },
    { name: 'unknown schema', change: r => { r.reference_expectation!.schema = 'unknown' as never } },
    { name: 'unknown basis', change: r => { r.reference_expectation!.basis = 'ranking_only' as never } },
    { name: 'purchase authority', change: r => { r.reference_expectation!.purchase_eligible = true as never } },
    { name: 'capture verified contradiction', change: r => { r.reference_expectation!.capture_verified = true as never } },
    { name: 'odds verified contradiction', change: r => { r.reference_expectation!.odds_freshness_verified = true as never } },
    { name: 'mismatched model', change: r => { r.reference_expectation!.model_id = 'other' } },
    { name: 'absent model', change: r => { delete r.model_id } },
    { name: 'mismatched race', change: r => { r.reference_expectation!.race_id = 'other' } },
    { name: 'failed prediction', change: r => { r.success = false } },
    { name: 'wrong pipeline', change: r => { r.input_diagnostics!.feature_pipeline = 'other' } },
    { name: 'wrong feature count', change: r => { r.input_diagnostics!.actual_count = r.input_diagnostics!.expected_count === 47 ? 46 : 47 } },
    { name: 'equal counts but profile mismatch', change: r => {
      const count = r.input_diagnostics!.expected_count === 47 ? 46 : 47
      r.input_diagnostics!.actual_count = count; r.input_diagnostics!.expected_count = count
    } },
    { name: 'other profile with original counts', change: r => {
      r.input_diagnostics!.feature_pipeline = r.input_diagnostics!.feature_pipeline === 'local-db-shared-v1'
        ? 'local-db-shared-optimized-v1' : 'local-db-shared-v1'
    } },
    { name: 'outcomes consumed', change: r => { r.input_diagnostics!.current_outcomes_consumed = true } },
    { name: 'outcomes status absent', change: r => { delete r.input_diagnostics!.current_outcomes_consumed } },
    { name: 'missing rate unknown', change: r => { delete r.input_diagnostics!.missing_value_rate } },
    { name: 'too much missing', change: r => { r.input_diagnostics!.missing_value_rate = 0.20001 } },
    { name: 'missing column', change: r => { r.input_diagnostics!.missing_columns = ['unknown'] } },
    { name: 'blocked column', change: r => { r.input_diagnostics!.blocked_columns = ['finish'] } },
    { name: 'source mismatch', change: r => { r.input_diagnostics!.source_hash_matches = false } },
    { name: 'other issue', change: r => { r.input_diagnostics!.issue_codes.push('target_not_after_training_evaluation_period') } },
    { name: 'uncalibrated', change: r => { r.recommendation!.probability_basis = 'uncalibrated_score' } },
    { name: 'verified input contradiction', change: r => { r.recommendation!.input_verified = true } },
    { name: 'buy decision', change: r => { r.recommendation!.decision = 'buy' } },
    { name: 'purchase count', change: r => { r.recommendation!.purchase_count = 1 } },
    { name: 'stake', change: r => { r.recommendation!.total_cost = 100 } },
    { name: 'unit stake', change: r => { r.recommendation!.unit_price = 100 } },
    { name: 'return', change: r => { r.recommendation!.expected_return = 100 } },
    { name: 'ticket present', change: r => { r.recommendation!.tickets = [{} as never] } },
    { name: 'future computation', change: r => { r.reference_expectation!.computed_at = new Date(REFERENCE_NOW + 1).toISOString() } },
    { name: 'missing expiry', change: r => { r.reference_expectation!.valid_until = '' } },
    { name: 'expiry not after computation', change: r => { r.reference_expectation!.valid_until = r.reference_expectation!.computed_at } },
    { name: 'over five-minute trial', change: r => { r.reference_expectation!.valid_until = new Date(REFERENCE_NOW + 300001).toISOString() } },
    { name: 'local timestamp', change: r => { r.reference_expectation!.computed_at = '2026-10-11T01:00:00' } },
    { name: 'empty items', change: r => { r.reference_expectation!.items = [] } },
    { name: 'duplicated item', change: r => { r.reference_expectation!.items.push({ ...r.reference_expectation!.items[0] }) } },
    { name: 'unknown horse', change: r => { r.reference_expectation!.items[0].horse_number = 3 } },
    { name: 'probability mismatch', change: r => { r.reference_expectation!.items[0].probability = 0.14 } },
    { name: 'odds mismatch', change: r => { r.reference_expectation!.items[0].odds = 6 } },
    { name: 'product mismatch', change: r => { r.reference_expectation!.items[0].expected_value = 1.741 } },
    { name: 'string odds', change: r => { r.reference_expectation!.items[0].odds = '5.7' as never } },
    { name: 'boolean probability', change: r => { r.reference_expectation!.items[0].probability = true as never } },
    { name: 'invalid odds', change: r => { r.reference_expectation!.items[0].odds = 0 } },
    { name: 'missing referenced odds', change: r => { r.predictions[0].odds = null } },
    { name: 'duplicated prediction', change: r => { r.predictions[1].horse_number = 1 } },
    { name: 'horse number above JRA maximum', change: r => { r.predictions[1].horse_number = 19 } },
    { name: 'incomplete runner set', change: r => { r.race_info.num_horses = 3 } },
    { name: 'fractional runner count', change: r => { r.race_info.num_horses = 2.5 } },
    { name: 'runner count not numeric', change: r => { r.race_info.num_horses = '2' as never } },
    { name: 'runner count above JRA maximum', change: r => { r.race_info.num_horses = 19 } },
    { name: 'bad probability distribution', change: r => { r.predictions[1].p_norm = 0.8 } },
    { name: 'nonfinite probability', change: r => { r.predictions[1].p_norm = Number.NaN } },
    { name: 'too few predictions', change: r => { r.predictions.pop() } },
  ]
  it.each(failures.flatMap(failure => (['local-db-shared-v1', 'local-db-shared-optimized-v1'] as const)
    .map(profile => ({ ...failure, profile }))))('fails closed for $profile / $name', ({ change, profile }) => {
    const result = referenceExpectationResult(profile)
    change(result)
    expect(predictionExpectationDisplay(result, result.recommendation, REFERENCE_NOW).mode).toBe('unavailable')
  })

  it('requires the visible batch race to agree with the bound result', () => {
    const result = referenceExpectationResult()
    expect(predictionExpectationDisplay(result, result.recommendation, REFERENCE_NOW, 'other').mode).toBe('unavailable')
  })

  it('expires the trial exactly at its display deadline, without relying on skip offer expiry', () => {
    const result = referenceExpectationResult()
    result.recommendation!.valid_until = null
    expect(predictionExpectationDisplay(result, result.recommendation, REFERENCE_NOW + 299999).mode).toBe('reference')
    expect(predictionExpectationDisplay(result, result.recommendation, REFERENCE_NOW + 300000).mode).toBe('unavailable')
  })

  it('prefers official EV and never falls back from an expired official EV', () => {
    const result = referenceExpectationResult()
    result.input_diagnostics = verifiedPredictionInput
    result.recommendation!.input_verified = true
    result.predictions[0].expected_value = 1.5
    expect(predictionExpectationDisplay(result, result.recommendation, REFERENCE_NOW)).toMatchObject({ mode: 'official', label: '期待値' })
    expect(predictionExpectationDisplay(result, result.recommendation, REFERENCE_NOW).values.get(1)).toBe(1.5)
    result.recommendation!.valid_until = new Date(REFERENCE_NOW + 1000).toISOString()
    expect(predictionExpectationDisplay(result, result.recommendation, REFERENCE_NOW + 1000).mode).toBe('unavailable')
  })

  it.each(['local-db-shared-v1', 'local-db-shared-optimized-v1'] as const)(
    'never mistakes capture-unbound aligned %s inputs for official EV', profile => {
    const result = referenceExpectationResult(profile)
    result.input_diagnostics!.status = 'aligned'
    result.input_diagnostics!.issue_codes = []
    result.input_diagnostics!.live_capture_hash_bound = true
    result.recommendation!.input_verified = true
    result.predictions[0].expected_value = 1.5
    expect(predictionExpectationDisplay(result, result.recommendation, REFERENCE_NOW).mode).toBe('official')
    result.input_diagnostics!.live_capture_hash_bound = false
    expect(predictionExpectationDisplay(result, result.recommendation, REFERENCE_NOW).mode).toBe('unavailable')
    expect(predictionInputVerified(result.input_diagnostics)).toBe(false)
  })
})
