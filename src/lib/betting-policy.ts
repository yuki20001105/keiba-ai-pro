import type { PredictionInputDiagnostics, Recommendation, RecommendedTicket } from '@/lib/race-analysis-types'
import { predictionInputVerified } from '@/lib/prediction-input-contract'

// v1 cached tickets predate the fail-closed input compatibility requirement.
export const BETTING_POLICY_VERSION = 'value-selection-v2'
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
  return Array.isArray(rec.tickets) ? rec.tickets.filter(isValidTicket) : []
}

export function isCurrentBettingCache(
  rec: Recommendation | null | undefined, bankroll: number, mode: RiskMode,
  diagnostics?: PredictionInputDiagnostics | null,
): boolean {
  if (rec?.policy_version !== BETTING_POLICY_VERSION || rec.bankroll !== bankroll || rec.risk_mode !== mode
    || bettingRecommendationExpired(rec)) return false
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
  const candidates = Object.entries(recommendations).flatMap(([raceId, rec]) =>
    selectedTickets(rec, nowMs).map((ticket, index) => ({ raceId, ticket, index })))
    .sort((a, b) => b.ticket.conservative_expected_value - a.ticket.conservative_expected_value
      || a.raceId.localeCompare(b.raceId) || a.index - b.index)
  let remaining = roundBetBudget(dayBudget)
  const raceSpent: Record<string, number> = {}
  for (const { raceId, ticket } of candidates) {
    const rec = recommendations[raceId]!
    const ceiling = roundBetBudget(Math.min(rec.per_race_limit ?? rec.budget ?? 0, rec.budget ?? rec.per_race_limit ?? 0))
    const stake = roundBetBudget(Math.min(ticket.stake, remaining, ceiling - (raceSpent[raceId] ?? 0)))
    if (stake < 100) continue
    ;(selections[raceId] ??= []).push({ ...ticket, stake })
    raceSpent[raceId] = (raceSpent[raceId] ?? 0) + stake
    remaining -= stake
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
