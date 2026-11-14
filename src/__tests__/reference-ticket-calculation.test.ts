import { describe, expect, it } from 'vitest'
import { referenceSimulationResult, REFERENCE_NOW } from './helpers/reference-expectation'
import type { RacePredictResult } from '@/lib/race-analysis-types'

const modulePath = '@/lib/reference-ticket-calculation'
async function calculator() {
  const result = await import(modulePath).catch(() => null)
  expect(result, 'Missing display-only ticket calculator').not.toBeNull()
  return result!
}

const probabilities = new Map([[1, .4], [2, .3], [3, .2], [4, .1]])

describe('hypothetical ticket arithmetic', () => {
  it('uses exact ordered probabilities rather than multiplying single-win odds or independent wins', async () => {
    const { referenceTicketProbability } = await calculator()
    expect(referenceTicketProbability(probabilities, '単勝', [1])).toBeCloseTo(.4)
    expect(referenceTicketProbability(probabilities, '馬単', [1, 2])).toBeCloseTo(.2)
    expect(referenceTicketProbability(probabilities, '三連単', [1, 2, 3])).toBeCloseTo(2 / 15)
    expect(referenceTicketProbability(probabilities, '三連単', [2, 1, 3])).toBeCloseTo(4 / 35)
    expect(referenceTicketProbability(probabilities, '馬連', [1, 2])).toBeCloseTo(13 / 35)
  })

  it('counts unordered and top-three wide events once over the full field', async () => {
    const { referenceTicketProbability } = await calculator()
    const equal = new Map([[1, .25], [2, .25], [3, .25], [4, .25]])
    expect(referenceTicketProbability(equal, 'ワイド', [1, 2])).toBeCloseTo(.5)
    expect(referenceTicketProbability(equal, '三連複', [1, 2, 3])).toBeCloseTo(.25)
    let sum = 0
    for (const a of [1, 2, 3, 4]) for (const b of [1, 2, 3, 4]) for (const c of [1, 2, 3, 4]) {
      if (a !== b && b !== c && a !== c) sum += referenceTicketProbability(probabilities, '三連単', [a, b, c])
    }
    expect(sum).toBeCloseTo(1)
  })

  it('does not invent joint probabilities for singular or invalid distributions and members', async () => {
    const { referenceTicketProbability } = await calculator()
    expect(referenceTicketProbability(new Map([[1, 1], [2, 0], [3, 0]]), '三連単', [1, 2, 3])).toBeNull()
    expect(referenceTicketProbability(probabilities, '馬単', [1, 1])).toBeNull()
    expect(referenceTicketProbability(probabilities, '単勝', [9])).toBeNull()
    expect(referenceTicketProbability(new Map([[1, .1], [2, .2]]), '単勝', [1])).toBeNull()
  })

  it.each([null, 0, -2, Number.NaN, Number.POSITIVE_INFINITY, 1e308])('keeps missing/invalid/overflow quotes unpriced: %s', async odds => {
    const { calculateReferenceTicket } = await calculator()
    expect(calculateReferenceTicket(.13, odds)).toMatchObject({ payout: null, expectedMultiplier: null })
  })

  it('separates hit payout from probability-weighted return and withholds incomplete aggregate EV', async () => {
    const { calculateReferenceTicket, aggregateReferenceTickets } = await calculator()
    expect(calculateReferenceTicket(.13, 9)).toMatchObject({ payout: 900, expectedMultiplier: 1.17, expectedReturn: 117 })
    expect(calculateReferenceTicket(null, 9)).toMatchObject({ payout: 900, expectedMultiplier: null })
    const priced = [calculateReferenceTicket(.1, 12), calculateReferenceTicket(.2, 8)]
    expect(aggregateReferenceTickets(priced)).toMatchObject({ totalCost: 200, pricedPoints: 2, expectedMultiplier: 1.4, expectedReturn: 280 })
    expect(aggregateReferenceTickets([priced[0], calculateReferenceTicket(.2, null)]))
      .toMatchObject({ totalCost: 200, pricedPoints: 1, expectedMultiplier: null, expectedReturn: null })
  })

  it('binds the unpriced simulation probabilities to the complete race/model/cohort but not purchase authority', async () => {
    const { referenceSimulationProbabilities } = await calculator()
    const result = referenceSimulationResult()
    expect(referenceSimulationProbabilities(result, REFERENCE_NOW)?.get(1)).toBeCloseTo(.13)
    expect(referenceSimulationProbabilities(result, REFERENCE_NOW + 86400000)?.get(1)).toBeCloseTo(.13)
    expect(referenceSimulationProbabilities(result, REFERENCE_NOW, 'other-race')).toBeNull()
    expect(referenceSimulationProbabilities({ ...result, model_id: 'other-model' }, REFERENCE_NOW)).toBeNull()
    result.predictions = result.predictions.map(p => ({ ...p, p_norm: p.horse_number === 1 ? .14 : .86 }))
    expect(referenceSimulationProbabilities(result, REFERENCE_NOW)).toBeNull()
  })

  it('rejects absent/contradictory evidence instead of turning a normalized score into a proven probability', async () => {
    const { referenceSimulationProbabilities } = await calculator()
    for (const problem of ['absent', 'outcomes', 'context', 'uncalibrated', 'amount', 'cohort']) {
      const result = referenceSimulationResult()
      if (problem === 'absent') delete result.reference_simulation
      if (problem === 'outcomes') result.input_diagnostics!.current_outcomes_consumed = true
      if (problem === 'context') result.input_diagnostics!.reference_only = true
      if (problem === 'uncalibrated') result.recommendation!.probability_basis = 'uncalibrated_score'
      if (problem === 'amount') result.recommendation!.total_cost = 100
      if (problem === 'cohort') result.reference_simulation!.items.pop()
      expect(referenceSimulationProbabilities(result, REFERENCE_NOW)).toBeNull()
    }
  })

  it('fails closed rather than crashing for malformed cached model and race identifiers', async () => {
    const { referenceSimulationProbabilities } = await calculator()
    const result = referenceSimulationResult()
    for (const malformed of [
      { ...result, model_id: 42 },
      { ...result, race_info: { ...result.race_info, race_id: 42 } },
    ]) {
      expect(referenceSimulationProbabilities(malformed as unknown as RacePredictResult, REFERENCE_NOW)).toBeNull()
    }
  })

  it.each([
    { mode: 'simulation', referenceOnly: undefined },
    { mode: 'simulation', referenceOnly: false },
    { mode: 'verified', referenceOnly: undefined },
    { mode: 'verified', referenceOnly: false },
  ])('rejects an unadmitted context cohort in $mode mode with reference_only=$referenceOnly', async ({ mode, referenceOnly }) => {
    const { referenceSimulationProbabilities } = await calculator()
    const result = referenceSimulationResult()
    Object.assign(result.input_diagnostics!, {
      feature_pipeline: 'local-db-context-v1', expected_count: 80, actual_count: 80, reference_only: referenceOnly,
      ...(mode === 'verified' ? { status: 'aligned', live_capture_hash_bound: true, issue_codes: [] } : {}),
    })
    result.recommendation!.input_verified = mode === 'verified'
    expect(referenceSimulationProbabilities(result, REFERENCE_NOW)).toBeNull()
  })
})
