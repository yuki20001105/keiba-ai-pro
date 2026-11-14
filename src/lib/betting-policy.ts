import type { PredictionInputDiagnostics, Recommendation, RecommendedTicket } from '@/lib/race-analysis-types'
import { predictionInputVerified } from '@/lib/prediction-input-contract'

// v1 cached tickets predate the fail-closed input compatibility requirement.
export const BETTING_POLICY_VERSION = 'value-selection-v4'
export const FIXED_TWO_STRATEGY = 'fixed-two-v1' as const
export type BettingStrategy = 'value' | typeof FIXED_TWO_STRATEGY

export function safeRecommendationForStrategy(rec: Recommendation | null | undefined,
  strategy: BettingStrategy): Recommendation | null {
  if (!rec) return null
  if ((rec.betting_strategy ?? 'value') === strategy) return rec
  return { ...rec, betting_strategy: strategy, decision: 'skip', action: '見送り',
    reason: '買い目方式が異なります。選択した方式で再予測してください。',
    reason_codes: ['betting_strategy_mismatch'], tickets: [], purchase_count: 0, unit_price: 0,
    total_cost: 0, expected_return: 0 }
}
export type RiskMode = 'conservative' | 'balanced' | 'aggressive'
export const RISK_MODES = {
  conservative: { label: '控えめ', percentage: 0.02 },
  balanced: { label: '標準', percentage: 0.035 },
  aggressive: { label: '積極', percentage: 0.05 },
} as const

export function roundBetBudget(value: number): number {
  return Number.isFinite(value) ? Math.max(0, Math.floor(value / 100) * 100) : 0
}

export function perRaceCeiling(bankroll: number, mode: RiskMode): number {
  return roundBetBudget(bankroll * RISK_MODES[mode].percentage)
}

function isValidTicket(ticket: RecommendedTicket): boolean {
  if (!ticket || typeof ticket !== 'object') return false
  return Boolean(ticket.bet_type && ticket.combination)
    && Number.isFinite(ticket.stake) && ticket.stake >= 100 && ticket.stake % 100 === 0
    && Number.isFinite(ticket.probability) && ticket.probability > 0 && ticket.probability <= 1
    && Number.isFinite(ticket.odds) && ticket.odds > 1
    && Number.isFinite(ticket.expected_value) && ticket.expected_value > 1
    && Number.isFinite(ticket.conservative_expected_value) && ticket.conservative_expected_value > 1
}

/** Only the server's current calibrated policy can supply purchase candidates. */
export function bettingRecommendationExpired(rec: Recommendation | null | undefined, nowMs = Date.now()): boolean {
  if (rec?.decision !== 'buy' && rec?.decision !== 'priority') return false
  const evaluatedAt = Date.parse(rec.evaluated_at ?? '')
  const validUntil = Date.parse(rec.valid_until ?? '')
  return !Number.isFinite(evaluatedAt) || !Number.isFinite(validUntil)
    || evaluatedAt > nowMs || validUntil <= evaluatedAt
    || nowMs >= Math.min(validUntil, evaluatedAt + 5 * 60 * 1000)
}

export function selectedTickets(rec: Recommendation | null | undefined, nowMs = Date.now()): RecommendedTicket[] {
  if (rec?.policy_version !== BETTING_POLICY_VERSION || rec.probability_basis !== 'calibrated'
    || rec.input_verified !== true
    || (rec.decision !== 'buy' && rec.decision !== 'priority') || bettingRecommendationExpired(rec, nowMs)) return []
  const tickets = Array.isArray(rec.tickets) ? rec.tickets.filter(isValidTicket) : []
  if (rec.betting_strategy === 'fixed-two-v1') {
    if (tickets.some(t => !Array.isArray(t.horse_numbers))) return []
    const wide = tickets.filter(t => t.bet_type === 'ワイド')
    const tris = tickets.filter(t => t.bet_type === '三連単')
    if (tickets.length !== 3 || wide.length !== 1 || tris.length !== 2
      || tickets.some(t => t.stake !== tickets[0].stake) || tickets[0].stake > 500
      || wide[0].horse_numbers.length !== 2 || tris.some(t => t.horse_numbers.length !== 3)
      || new Set(tris[0].horse_numbers).size !== 3
      || tris[0].horse_numbers[1] !== tris[1].horse_numbers[1]
      || tris[0].horse_numbers[0] !== tris[1].horse_numbers[2]
      || tris[0].horse_numbers[2] !== tris[1].horse_numbers[0]
      || !wide[0].horse_numbers.includes(tris[0].horse_numbers[1])
      || !wide[0].horse_numbers.some(n => [tris[0].horse_numbers[0], tris[0].horse_numbers[2]].includes(n))) return []
  }
  return tickets
}

export function isCurrentBettingCache(
  rec: Recommendation | null | undefined, bankroll: number, mode: RiskMode,
  diagnostics?: PredictionInputDiagnostics | null,
  strategy?: BettingStrategy,
): boolean {
  if (rec?.policy_version !== BETTING_POLICY_VERSION || rec.bankroll !== bankroll || rec.risk_mode !== mode
    || bettingRecommendationExpired(rec)) return false
  if (strategy && (rec.betting_strategy ?? 'value') !== strategy) return false
  if (rec.decision === 'buy' || rec.decision === 'priority') {
    return predictionInputVerified(diagnostics) && selectedTickets(rec).length > 0
  }
  // Keep zero-ticket reference rankings without causing another prediction.
  // They never confer a purchase or an actual-recording capability.
  return rec.decision === 'skip' && Array.isArray(rec.tickets) && rec.tickets.length === 0
    && rec.total_cost === 0 && rec.purchase_count === 0
}

/** A contradictory response may retain its ranking, never its buying capability. */
export function safeRecommendationForInput(
  rec: Recommendation | null | undefined, diagnostics?: PredictionInputDiagnostics | null,
): Recommendation | null | undefined {
  if (!rec || (rec.decision !== 'buy' && rec.decision !== 'priority')
    || (rec.input_verified === true && predictionInputVerified(diagnostics))) return rec
  return { ...rec, decision: 'skip', action: '見送り', input_verified: false,
    reason: '入力互換性未確認のため買い目には使用しません。',
    reason_codes: [...new Set([...(Array.isArray(rec.reason_codes) ? rec.reason_codes : []), 'input_contract_unverified'])],
    tickets: [], purchase_count: 0, unit_price: 0, total_cost: 0, expected_return: 0 }
}

/** Allocate the day's ceiling without increasing any server-selected stake. */
export function allocateDayBudget(
  recommendations: Record<string, Recommendation | null | undefined>,
  dayBudget: number,
  nowMs = Date.now(),
): Record<string, Recommendation | null | undefined> {
  const allocated: Record<string, Recommendation | null | undefined> = Object.fromEntries(
    Object.entries(recommendations).map(([raceId, rec]) => [raceId, rec ? {
      ...rec, tickets: [], purchase_count: 0, unit_price: 0, total_cost: 0, expected_return: 0,
    } : rec]),
  )
  const selections: Record<string, RecommendedTicket[]> = {}
  const candidates = Object.entries(recommendations).flatMap(([raceId, rec]) => {
    const tickets = selectedTickets(rec, nowMs)
    return rec?.betting_strategy === 'fixed-two-v1' && tickets.length
      ? [{ raceId, ticket: { ...tickets[0], conservative_expected_value:
        tickets.reduce((sum, t) => sum + t.conservative_expected_value, 0) / 3 }, index: -1 }]
      : tickets.map((ticket, index) => ({ raceId, ticket, index }))
  })
    .sort((a, b) => b.ticket.conservative_expected_value - a.ticket.conservative_expected_value
      || a.raceId.localeCompare(b.raceId) || a.index - b.index)
  let remaining = roundBetBudget(dayBudget)
  const raceSpent: Record<string, number> = {}
  for (const { raceId, ticket, index } of candidates) {
    const rec = recommendations[raceId]!
    const ceiling = roundBetBudget(Math.min(rec.per_race_limit ?? rec.budget ?? 0, rec.budget ?? rec.per_race_limit ?? 0))
    const stake = roundBetBudget(Math.min(ticket.stake, index === -1 ? remaining / 3 : remaining,
      index === -1 ? ceiling / 3 : ceiling - (raceSpent[raceId] ?? 0)))
    if (stake < 100) continue
    if (index === -1) {
      selections[raceId] = selectedTickets(rec, nowMs).map(t => ({ ...t, stake }))
      raceSpent[raceId] = stake * 3
      remaining -= stake * 3
    } else {
      ;(selections[raceId] ??= []).push({ ...ticket, stake })
      raceSpent[raceId] = (raceSpent[raceId] ?? 0) + stake
      remaining -= stake
    }
  }
  for (const [raceId, rec] of Object.entries(recommendations)) {
    if (selectedTickets(rec, nowMs).length === 0) continue
    const tickets = selections[raceId] ?? []
    const totalCost = tickets.reduce((sum, ticket) => sum + ticket.stake, 0)
    allocated[raceId] = {
      ...rec!, tickets, purchase_count: tickets.length, total_cost: totalCost,
      unit_price: tickets.length && tickets.every(t => t.stake === tickets[0].stake) ? tickets[0].stake : 0,
      expected_return: tickets.reduce((sum, ticket) => sum + ticket.stake * ticket.expected_value, 0),
      ...(tickets.length === 0 ? { decision: 'skip' as const, action: '見送り', reason: 'この日の予算上限', reason_codes: ['day_budget_limit'] } : {}),
    }
  }
  return allocated
}
