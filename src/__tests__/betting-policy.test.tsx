import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BettingRecommendationPanel } from '@/components/BettingRecommendationPanel'
import { allocateDayBudget, BETTING_POLICY_VERSION, isCurrentBettingCache, perRaceCeiling, safeRecommendationForInput, selectedTickets } from '@/lib/betting-policy'
import type { PredictionInputDiagnostics, Recommendation, RecommendedTicket } from '@/lib/race-analysis-types'
import { verifiedPredictionInput } from './helpers/verified-prediction-input'

afterEach(() => { cleanup(); vi.useRealTimers() })

function ticket(combination: string, stake: number, ev: number, betType = '単勝'): RecommendedTicket {
  return { bet_type: betType, combination, horse_numbers: [Number(combination)], stake, probability: 0.3,
    odds: 5, expected_value: ev + 0.1, conservative_expected_value: ev }
}
function recommendation(tickets: RecommendedTicket[]): Recommendation {
  return { action: '購入候補', decision: 'buy', reason: '余裕のある期待倍率',
    purchase_count: tickets.length, unit_price: 0, total_cost: tickets.reduce((sum, t) => sum + t.stake, 0), expected_return: 0,
    policy_version: BETTING_POLICY_VERSION, probability_basis: 'calibrated', input_verified: true, tickets,
    bankroll: 10000, risk_mode: 'balanced', budget: 300, per_race_limit: 300, max_points: 3,
    evaluated_at: new Date().toISOString(), valid_until: new Date(Date.now() + 300000).toISOString() }
}

describe('daily betting ceiling', () => {
  it('prioritizes conservative value and only reduces existing 100-yen stakes', () => {
    const input = { low: recommendation([ticket('1', 300, 1.1)]), high: recommendation([ticket('2', 300, 1.4)]) }
    const result = allocateDayBudget(input, 450)
    expect(result.high?.tickets?.[0].stake).toBe(300)
    expect(result.low?.tickets?.[0].stake).toBe(100)
    expect(Object.values(result).reduce((sum, rec) => sum + rec!.total_cost, 0)).toBe(400)
    expect(input.low.tickets![0].stake).toBe(300)
  })
  it('marks budget overflow as a zero-cost skip and never increases stakes', () => {
    const result = allocateDayBudget({ low: recommendation([ticket('1', 100, 1.1)]), high: recommendation([ticket('2', 200, 1.4)]) }, 200)
    expect(result.low).toMatchObject({ decision: 'skip', reason: 'この日の予算上限', tickets: [], total_cost: 0 })
    expect(result.high?.tickets?.[0].stake).toBe(200)
  })
  it('respects per-race ceilings across multiple selected tickets', () => {
    const rec = recommendation([ticket('1', 300, 1.3), ticket('2', 300, 1.2)])
    expect(allocateDayBudget({ race: rec }, 1000).race?.total_cost).toBe(300)
  })
  it('never treats legacy, uncalibrated, or invalid proposals as purchases', () => {
    const rec = recommendation([ticket('1', 300, 1.4)])
    for (const invalid of [{ ...rec, policy_version: undefined }, { ...rec, probability_basis: 'uncalibrated_score' as const },
      { ...rec, tickets: [ticket('1', Number.NaN, 1.4)] }]) {
      expect(allocateDayBudget({ race: invalid }, 1000).race?.total_cost).toBe(0)
    }
  })
  it('cache settings and policy must match; the per-race ceiling uses 100-yen units', () => {
    const rec = recommendation([ticket('1', 100, 1.4)])
    expect(isCurrentBettingCache(rec, 10000, 'balanced', verifiedPredictionInput)).toBe(true)
    expect(isCurrentBettingCache(rec, 20000, 'balanced')).toBe(false)
    expect(isCurrentBettingCache(rec, 10000, 'aggressive')).toBe(false)
    expect(isCurrentBettingCache({ ...rec, policy_version: undefined }, 10000, 'balanced')).toBe(false)
    expect(perRaceCeiling(10000, 'balanced')).toBe(300)
  })
  it('requires an expiry and expires at the earlier deadline with a five-minute ceiling', () => {
    const now = Date.now()
    const rec = { ...recommendation([ticket('1', 100, 1.4)]), evaluated_at: new Date(now).toISOString(), valid_until: new Date(now + 30000).toISOString() }
    expect(selectedTickets(rec, now + 29999)).toHaveLength(1)
    expect(selectedTickets(rec, now + 30000)).toHaveLength(0)
    expect(selectedTickets({ ...rec, valid_until: undefined }, now)).toHaveLength(0)
    expect(selectedTickets({ ...rec, valid_until: new Date(now + 600000).toISOString() }, now + 300000)).toHaveLength(0)
    expect(isCurrentBettingCache({ ...rec, valid_until: new Date(now - 1).toISOString() }, 10000, 'balanced')).toBe(false)
  })
})

describe('shared recommendation panel', () => {
  it('renders mixed ticket types and exact individual stakes', () => {
    render(<BettingRecommendationPanel recommendation={recommendation([ticket('2', 100, 1.3), ticket('3-4', 200, 1.2, 'ワイド')])} diagnostics={verifiedPredictionInput} />)
    expect(screen.getByText('購入候補')).toBeInTheDocument()
    expect(screen.getByText('単勝')).toBeInTheDocument()
    expect(screen.getByText('ワイド')).toBeInTheDocument()
    expect(screen.getByText('¥100')).toBeInTheDocument()
    expect(screen.getByText('¥200')).toBeInTheDocument()
    expect(screen.getByText('合計 ¥300')).toBeInTheDocument()
  })
  it('shows the supplied skip reason and zero cost for uncalibrated scores', () => {
    render(<BettingRecommendationPanel recommendation={{ ...recommendation([ticket('2', 100, 1.3)]),
      action: '見送り', decision: 'skip', probability_basis: 'uncalibrated_score', reason: '勝率の検証待ち' }} />)
    expect(screen.getByText('見送り')).toBeInTheDocument()
    expect(screen.getByText('勝率の検証待ち')).toBeInTheDocument()
    expect(screen.getByText('合計 ¥0')).toBeInTheDocument()
    expect(screen.queryByText('単勝')).not.toBeInTheDocument()
  })
  it('rejects stale cached recommendations without recreating legacy ticket choices', () => {
    render(<BettingRecommendationPanel recommendation={{ ...recommendation([ticket('2', 100, 1.3)]), policy_version: undefined }} />)
    expect(screen.getByText('再判定が必要です')).toBeInTheDocument()
    expect(screen.getByText('合計 ¥0')).toBeInTheDocument()
    expect(screen.queryByText('単勝')).not.toBeInTheDocument()
  })
  it('invalidates pre-input-audit v1 tickets even within their expiry', () => {
    const old = { ...recommendation([ticket('2', 100, 1.3)]), policy_version: 'value-selection-v1' }
    expect(selectedTickets(old)).toHaveLength(0)
    expect(isCurrentBettingCache(old, 10000, 'balanced')).toBe(false)
    render(<BettingRecommendationPanel recommendation={old} snapshotId="old-snapshot" />)
    expect(screen.getByText('再判定が必要です')).toBeInTheDocument()
    expect(screen.getByText('合計 ¥0')).toBeInTheDocument()
    expect(screen.queryByText('購入済みとして記録')).not.toBeInTheDocument()
  })
  it('removes expired tickets while the page stays open without another prediction', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-10T01:00:00Z'))
    const rec = { ...recommendation([ticket('2', 100, 1.3)]), valid_until: new Date(Date.now() + 1000).toISOString() }
    render(<BettingRecommendationPanel recommendation={rec} diagnostics={verifiedPredictionInput} />)
    expect(screen.getByText('単勝')).toBeInTheDocument()
    act(() => { vi.advanceTimersByTime(1000) })
    expect(screen.getByText('再判定が必要です')).toBeInTheDocument()
    expect(screen.getByText('合計 ¥0')).toBeInTheDocument()
    expect(screen.queryByText('単勝')).not.toBeInTheDocument()
  })
  it.each([undefined, false, 'true'])('requires the explicit boolean server input verification flag: %s', flag => {
    const rec = { ...recommendation([ticket('2', 100, 1.3)]), input_verified: flag } as Recommendation
    expect(selectedTickets(rec)).toEqual([])
    expect(isCurrentBettingCache(rec, 10000, 'balanced', verifiedPredictionInput)).toBe(false)
    render(<BettingRecommendationPanel recommendation={rec} diagnostics={verifiedPredictionInput} snapshotId="unverified" />)
    expect(screen.getByText('合計 ¥0')).toBeInTheDocument()
    expect(screen.queryByText('単勝')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '購入済みとして記録' })).not.toBeInTheDocument()
  })
  it.each([
    undefined, null, { status: 'aligned' },
    { ...verifiedPredictionInput, semantic_parity_verified: false },
    { ...verifiedPredictionInput, actual_count: 4 },
    { ...verifiedPredictionInput, missing_columns: ['missing'] },
    { ...verifiedPredictionInput, blocked_columns: ['finish_time'] },
    { ...verifiedPredictionInput, source_hash_matches: false },
    { ...verifiedPredictionInput, history_available: false },
  ])('does not display or cache purchases from missing, malformed, or contradictory evidence: %o', value => {
    const diagnostics = value as PredictionInputDiagnostics | null | undefined
    const rec = recommendation([ticket('2', 100, 1.3)])
    expect(isCurrentBettingCache(rec, 10000, 'balanced', diagnostics)).toBe(false)
    const safe = safeRecommendationForInput(rec, diagnostics)
    expect(safe).toMatchObject({ decision: 'skip', input_verified: false, tickets: [], total_cost: 0 })
    expect(allocateDayBudget({ invalid: safe }, 1000).invalid?.total_cost).toBe(0)
    expect(rec.tickets).toHaveLength(1)
    render(<BettingRecommendationPanel recommendation={rec} diagnostics={diagnostics} snapshotId="unverified" />)
    expect(screen.getByText('参考予測 · 見送り')).toBeInTheDocument()
    expect(screen.getByText('合計 ¥0')).toBeInTheDocument()
    expect(screen.queryByText('単勝')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '購入済みとして記録' })).not.toBeInTheDocument()
  })
  it('keeps zero-ticket reference caches without granting purchase eligibility', () => {
    const rec: Recommendation = { ...recommendation([]), decision: 'skip', input_verified: false, total_cost: 0, purchase_count: 0 }
    expect(isCurrentBettingCache(rec, 10000, 'balanced')).toBe(true)
    expect(isCurrentBettingCache({ ...rec, tickets: [ticket('2', 100, 1.3)] }, 10000, 'balanced')).toBe(false)
    expect(isCurrentBettingCache({ ...rec, policy_version: 'value-selection-v1' }, 10000, 'balanced')).toBe(false)
    expect(selectedTickets(rec)).toEqual([])
  })
  it('fails closed for a malformed null ticket rather than crashing the panel', () => {
    const rec = { ...recommendation([]), tickets: [null] } as unknown as Recommendation
    expect(selectedTickets(rec)).toEqual([])
  })
})
