import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BETTING_POLICY_VERSION } from '@/lib/betting-policy'
import type { RacePredictResult, Recommendation } from '@/lib/race-analysis-types'
import { verifiedPredictionInput } from './helpers/verified-prediction-input'
import { freshPrediction } from './helpers/prediction-freshness'

const { fetchMock } = vi.hoisted(() => ({ fetchMock: vi.fn() }))
vi.mock('@/lib/auth-fetch', () => ({ authFetch: fetchMock }))
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ isPremium: false, userId: 'test-owner' }) }))
vi.mock('@/components/Logo', () => ({ Logo: () => null }))
vi.mock('@/components/Toast', () => ({ Toast: () => null }))
vi.mock('@/components/RaceExplanationPanel', () => ({ RaceExplanationPanel: () => null }))
vi.mock('@/hooks/useJobPoller', () => ({ useJobPoller: () => ({}) }))
import { RacePredictionPanel } from '@/components/RacePredictionPanel'
import PredictBatchPage from '@/app/predict-batch/page'

const NOW = Date.parse('2026-10-11T01:00:00Z')
const race = { race_id: '202605040301', race_name: 'EV鮮度レース', venue: '東京', venue_code: '05', race_no: 1,
  date: '20261011', distance: 1600, track_type: '芝', num_horses: 12 }
function result(decision: Recommendation['decision'], lifetime: number): RacePredictResult {
  return {
    success: true, input_diagnostics: verifiedPredictionInput, race_info: race, best_bet_type: null, pro_evaluation: null,
    predictions: [{ horse_number: 1, horse_name: 'テスト馬', jockey_name: '騎手', trainer_name: '調教師', sex: '牡',
      age: 3, horse_weight: 480, odds: 7, popularity: 1, win_probability: 0.25, p_raw: 0.42, p_norm: 0.25,
      expected_value: 1.75, predicted_rank: 1 }],
    recommendation: { policy_version: BETTING_POLICY_VERSION, probability_basis: 'calibrated', input_verified: true,
      decision, action: decision === 'buy' ? '購入候補' : '見送り', reason: '条件を確認済み',
      bankroll: 10000, risk_mode: 'balanced', budget: 300, per_race_limit: 300,
      purchase_count: decision === 'buy' ? 1 : 0, unit_price: decision === 'buy' ? 100 : 0,
      total_cost: decision === 'buy' ? 100 : 0, expected_return: 0,
      evaluated_at: new Date(NOW).toISOString(), valid_until: new Date(NOW + lifetime).toISOString(),
      tickets: decision === 'buy' ? [{ bet_type: '単勝', combination: '1', horse_numbers: [1], stake: 100,
        odds: 7, probability: 0.25, expected_value: 1.75, conservative_expected_value: 1.5 }] : [],
    },
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date(NOW))
  localStorage.clear()
  fetchMock.mockReset()
  fetchMock.mockImplementation(async (url: string) => {
    if (url === '/api/models?ultimate=true') return Response.json({ models: [] })
    if (url.startsWith('/api/prediction-freshness')) return Response.json(freshPrediction)
    if (url.startsWith('/api/races/by-date')) return Response.json({ races: [race] })
    throw new Error(`Unexpected request: ${url}`)
  })
})
afterEach(() => { cleanup(); vi.useRealTimers() })

async function mount(surface: string, prediction: RacePredictResult) {
  if (surface === 'detail') {
    render(<RacePredictionPanel result={prediction} />)
  } else {
    localStorage.setItem(`ra-cache:${race.race_id}`, JSON.stringify({ owner_user_id: 'test-owner', data: {
      ...prediction, performance_allocation: { day_budget: 1000, allocated_at: new Date(NOW).toISOString() },
      freshness: { ...freshPrediction, generated_at: new Date(NOW).toISOString() }, model_id: 'current',
    }, cachedAt: NOW }))
    render(<PredictBatchPage />)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'レース一覧を取得' })) })
    fireEvent.click(screen.getByRole('button', { name: /EV鮮度レース/ }))
  }
}
function assertRankingRetained() {
  expect(screen.getByText('勝率')).toBeInTheDocument()
  expect(screen.getAllByText('25.0%').length).toBeGreaterThan(0)
  expect(screen.getByText('42.0')).toBeInTheDocument()
  expect(screen.getByText('テスト馬')).toBeInTheDocument()
    expect(fetchMock.mock.calls.every(([url]) => url === '/api/models?ultimate=true' || url.startsWith('/api/races/by-date') || url.startsWith('/api/prediction-freshness'))).toBe(true)
}
function assertExpiredCell(surface: string) {
  const cell = surface === 'detail' ? screen.getByText('期待値').nextElementSibling
    : screen.getByRole('columnheader', { name: '期待値' }).closest('table')!.querySelector('tbody tr')!.children[8]
  expect(cell).toHaveTextContent('—')
  expect(screen.queryByText('1.75')).not.toBeInTheDocument()
}

describe.each(['detail', 'batch'])('%s prediction EV freshness only', surface => {
  it.each(['buy', 'skip'] as const)('hides expired EV while preserving calibrated win probabilities and scores (%s)', async decision => {
    const prediction = result(decision, 2000)
    const before = JSON.stringify(prediction)
    await mount(surface, prediction)
    expect(screen.getByText('1.75')).toBeInTheDocument()
    act(() => { vi.advanceTimersByTime(1999) })
    expect(screen.getByText('1.75')).toBeInTheDocument()
    act(() => { vi.advanceTimersByTime(1) })
    assertExpiredCell(surface)
    assertRankingRetained()
    expect(JSON.stringify(prediction)).toBe(before)
  })
  it('uses the existing five-minute ceiling even if valid_until is later', async () => {
    await mount(surface, result('skip', 600000))
    expect(screen.getByText('1.75')).toBeInTheDocument()
    act(() => { vi.advanceTimersByTime(299999) })
    expect(screen.getByText('1.75')).toBeInTheDocument()
    act(() => { vi.advanceTimersByTime(1) })
    assertExpiredCell(surface)
    assertRankingRetained()
  })
  it.each(['elapsed', 'missing'] as const)('does not treat an already stale or missing expiry as a current EV (%s)', async expiry => {
    const prediction = result('skip', -1)
    if (expiry === 'missing') prediction.recommendation!.valid_until = undefined
    await mount(surface, prediction)
    assertExpiredCell(surface)
    assertRankingRetained()
  })
})
