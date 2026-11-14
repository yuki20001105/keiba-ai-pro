import type { RacePredictResult } from '@/lib/race-analysis-types'
import { BETTING_POLICY_VERSION } from '@/lib/betting-policy'
import { predictionInputVerified, sharedInputFormulaMatchedCaptureUnverified } from '@/lib/prediction-input-contract'
import { predictionExpectationDisplay } from '@/lib/prediction-expectation-display'

export const TRIAL_STAKE = 100
export const RANKING_TRIAL_NOTE = '単勝は校正済み確率、組合せは全出走馬の勝率から順位を順に割り当てる仮のモデル（Plackett–Luce）で試算します。組合せの的中率は未検証です。仮オッズは保存・購入・成績・予算に反映しません。'

const cardinalities: Record<string, number> = { '単勝': 1, 'ワイド': 2, '馬連': 2, '馬単': 2, '三連複': 3, '三連単': 3 }
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)

function utcTime(value: unknown): number {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(value)
    ? Date.parse(value) : Number.NaN
}

function completeProbabilities(result: RacePredictResult | null | undefined, expectedRaceId?: string): Map<number, number> | null {
  const diagnostics = result?.input_diagnostics
  if (!result?.success || typeof result.model_id !== 'string' || !result.model_id.trim()
    || typeof result.race_info?.race_id !== 'string' || !result.race_info.race_id
    || expectedRaceId !== undefined && expectedRaceId !== result.race_info.race_id
    || diagnostics?.feature_pipeline === 'local-db-context-v1' || diagnostics?.reference_only === true
    || diagnostics?.current_outcomes_consumed !== false
    || !finite(diagnostics.missing_value_rate) || diagnostics.missing_value_rate < 0 || diagnostics.missing_value_rate > .2
    || !Array.isArray(result.predictions) || result.predictions.length < 2 || result.predictions.length > 18
    || result.race_info.num_horses !== result.predictions.length) return null
  const probabilities = new Map<number, number>()
  for (const horse of result.predictions) {
    if (!horse || !Number.isInteger(horse.horse_number) || horse.horse_number < 1 || horse.horse_number > 18
      || probabilities.has(horse.horse_number) || !finite(horse.p_norm) || horse.p_norm < 0 || horse.p_norm > 1) return null
    probabilities.set(horse.horse_number, horse.p_norm)
  }
  return Math.abs([...probabilities.values()].reduce((a, b) => a + b, 0) - 1) <= 1e-5 ? probabilities : null
}

export function referenceSimulationProbabilities(
  result: RacePredictResult | null | undefined, nowMs = Date.now(), expectedRaceId?: string,
): ReadonlyMap<number, number> | null {
  if (!finite(nowMs)) return null
  const probabilities = completeProbabilities(result, expectedRaceId)
  if (!probabilities || !result) return null
  const rec = result.recommendation
  if (predictionInputVerified(result.input_diagnostics) && rec?.input_verified === true
    && rec.probability_basis === 'calibrated') return probabilities

  const reference = result.reference_simulation
  if (!reference) {
    const old = predictionExpectationDisplay(result, rec, nowMs, expectedRaceId)
    return old.mode === 'reference' && result.reference_expectation?.items.length === probabilities.size ? probabilities : null
  }
  if (!sharedInputFormulaMatchedCaptureUnverified(result.input_diagnostics)
    || rec?.policy_version !== BETTING_POLICY_VERSION || rec.probability_basis !== 'calibrated'
    || rec.input_verified !== false || rec.decision !== 'skip'
    || rec.purchase_count !== 0 || rec.unit_price !== 0 || rec.total_cost !== 0 || rec.expected_return !== 0
    || !Array.isArray(rec.tickets) || rec.tickets.length !== 0
    || reference.schema !== 'calibrated-win-simulation-v1' || reference.basis !== 'calibrated_win_probabilities'
    || reference.purchase_eligible !== false || reference.capture_verified !== false || reference.odds_freshness_verified !== false
    || reference.model_id !== result.model_id || reference.race_id !== result.race_info.race_id
    || !Array.isArray(reference.items) || reference.items.length !== probabilities.size) return null
  const computed = utcTime(reference.computed_at)
  const until = utcTime(reference.valid_until)
  if (!finite(computed) || !finite(until) || computed > nowMs || until <= computed || until > computed + 300000) return null
  const seen = new Set<number>()
  for (const item of reference.items) {
    if (!item || seen.has(item.horse_number) || !finite(item.probability)
      || probabilities.get(item.horse_number) !== item.probability) return null
    seen.add(item.horse_number)
  }
  return probabilities
}

export function referenceTicketProbability(
  probabilities: ReadonlyMap<number, number> | null, kind: string, members: readonly number[],
): number | null {
  if (!probabilities || cardinalities[kind] !== members.length || new Set(members).size !== members.length
    || members.some(n => !Number.isInteger(n) || !probabilities.has(n))) return null
  const values = [...probabilities.values()]
  if (values.some(p => !finite(p) || p < 0 || p > 1) || Math.abs(values.reduce((a, b) => a + b, 0) - 1) > 1e-5) return null
  if (kind === '単勝') return probabilities.get(members[0])!
  const places = kind === 'ワイド' ? 3 : members.length
  if (values.filter(p => p > 0).length < places) return null

  // Sequential sampling without replacement, not independent win events.
  const ordered = (order: readonly number[]) => {
    let remaining = values.reduce((a, b) => a + b, 0)
    let chance = 1
    for (const n of order) {
      const p = probabilities.get(n)!
      if (p === 0) return 0
      if (remaining <= 0) return Number.NaN
      chance *= p / remaining
      remaining -= p
    }
    return chance
  }
  let chance = 0
  if (kind === '馬単' || kind === '三連単') chance = ordered(members)
  else {
    const pool = kind === 'ワイド' ? [...probabilities.keys()] : [...members]
    for (const a of pool) for (const b of pool) {
      if (a === b) continue
      if (places === 2) chance += ordered([a, b])
      else for (const c of pool) {
        if (a === c || b === c || members.some(n => ![a, b, c].includes(n))) continue
        chance += ordered([a, b, c])
      }
    }
  }
  return finite(chance) && chance >= 0 && chance <= 1 + 1e-5 ? Math.min(1, chance) : null
}

export type ReferenceTicketCalculation = {
  payout: number | null
  expectedMultiplier: number | null
  expectedReturn: number | null
}

export function calculateReferenceTicket(probability: number | null, odds: number | null): ReferenceTicketCalculation {
  const payout = finite(odds) && odds >= 1 && finite(odds * TRIAL_STAKE) ? odds * TRIAL_STAKE : null
  const validProbability = finite(probability) && probability >= 0 && probability <= 1
  return {
    payout,
    expectedMultiplier: payout !== null && validProbability ? probability * odds! : null,
    expectedReturn: payout !== null && validProbability ? probability * payout : null,
  }
}

export function aggregateReferenceTickets(tickets: readonly ReferenceTicketCalculation[]) {
  const totalCost = tickets.length * TRIAL_STAKE
  const totalReturn = tickets.reduce((sum, ticket) => sum + (ticket.expectedReturn ?? 0), 0)
  const complete = tickets.length > 0 && tickets.every(ticket => ticket.payout !== null && ticket.expectedReturn !== null) && finite(totalReturn)
  return {
    totalCost, pricedPoints: tickets.filter(ticket => ticket.payout !== null).length,
    expectedReturn: complete ? totalReturn : null,
    expectedMultiplier: complete ? totalReturn / totalCost : null,
  }
}
