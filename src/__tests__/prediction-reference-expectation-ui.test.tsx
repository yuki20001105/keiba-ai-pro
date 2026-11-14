import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { REFERENCE_EXPECTATION_NOTE } from '@/lib/prediction-expectation-display'
import type { RacePredictResult } from '@/lib/race-analysis-types'
import { referenceExpectationResult, referenceSimulationResult, referenceRace, REFERENCE_NOW } from './helpers/reference-expectation'
import { freshPrediction } from './helpers/prediction-freshness'
import { writePredictionCache } from '@/lib/prediction-freshness'

const { fetchMock } = vi.hoisted(() => ({ fetchMock: vi.fn() }))
vi.mock('@/lib/auth-fetch', () => ({ authFetch: fetchMock }))
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ isPremium: false, userId: 'display-owner', loading: false }) }))
vi.mock('@/components/Logo', () => ({ Logo: () => null }))
vi.mock('@/components/Toast', () => ({ Toast: () => null }))
vi.mock('@/components/RaceExplanationPanel', () => ({ RaceExplanationPanel: () => null }))
vi.mock('@/hooks/useJobPoller', () => ({ useJobPoller: () => ({}) }))
import { RacePredictionPanel } from '@/components/RacePredictionPanel'
import PredictBatchPage from '@/app/predict-batch/page'

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date(REFERENCE_NOW))
  localStorage.clear()
  fetchMock.mockReset()
  fetchMock.mockImplementation(async (url: string) => {
    if (url === '/api/models?ultimate=true') return Response.json({ models: [] })
    if (url.startsWith('/api/races/by-date')) return Response.json({ races: [referenceRace] })
    if (url === '/api/prediction-freshness') return Response.json({ ...freshPrediction, resolved_model_id: 'shared-test-model' })
    throw new Error(`Unexpected request: ${url}`)
  })
})
afterEach(() => { cleanup(); vi.useRealTimers() })

async function mount(surface: string, result: RacePredictResult) {
  if (surface === 'detail') render(<RacePredictionPanel result={result} />)
  else {
    // Test-local freshness/owner proof only. Do not weaken capture or
    // probability validation flags to bypass the production restore guards.
    writePredictionCache(referenceRace.race_id, {
      ...result, freshness: { ...freshPrediction, resolved_model_id: 'shared-test-model',
        generated_at: new Date(REFERENCE_NOW).toISOString() },
      recommendation: result.recommendation ? { ...result.recommendation, betting_strategy: 'fixed-two-v1' } : null,
      performance_allocation: { day_budget: 1000, allocated_at: new Date(REFERENCE_NOW).toISOString() },
    }, REFERENCE_NOW, 'display-owner')
    render(<PredictBatchPage />)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'レース一覧を取得' })) })
    fireEvent.click(screen.getByRole('button', { name: /参考試算レース/ }))
  }
}

describe.each(['detail', 'batch'])('%s reference expectation display only', surface => {
  it('uses the same fixed three-point plan on the main surface, including cached predictions, without allocating or recording it', async () => {
    const result = referenceExpectationResult()
    result.predictions = [[1, .36, 2], [2, .22, 4], [3, .16, 8], [4, .12, 15], [5, .09, 30], [6, .05, 60]]
      .map(([horse_number, p_norm, odds], i) => ({
        ...result.predictions[0], horse_number, p_norm, odds, predicted_rank: i + 1,
      }))
    result.race_info.num_horses = 6
    const before = JSON.stringify(result)
    await mount(surface, result)
    const book = screen.getByRole('group', { name: '②の買い目' })
    expect(book).toBeVisible()
    expect(within(book).getByText('1-4')).toBeInTheDocument()
    expect(within(book).getByText('5→1→4')).toBeInTheDocument()
    expect(within(book).getByText('4→1→5')).toBeInTheDocument()
    expect(screen.getByText('合計 ¥0')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /購入済み|記録/ })).not.toBeInTheDocument()
    expect(JSON.stringify(result)).toBe(before)
    expect(fetchMock.mock.calls.every(([url]) => url === '/api/models?ultimate=true'
      || url === '/api/prediction-freshness' || url.startsWith('/api/races/by-date'))).toBe(true)
  })

  it('hides the duplicate reference bet trial while actual purchase stays zero', async () => {
    const result = referenceSimulationResult()
    const numbers = [10, ...Array.from({ length: 17 }, (_, i) => i + 1).filter(n => n !== 10)]
    result.predictions = numbers.map((number, i) => ({
      ...result.predictions[0], horse_number: number, horse_name: `未価格馬${number}`,
      predicted_rank: i + 1, p_norm: i === 0 ? .4 : .6 / 16,
    }))
    result.race_info.num_horses = 17
    result.reference_simulation!.items = result.predictions.map(p => ({ horse_number: p.horse_number, probability: p.p_norm }))
    result.reference_bets = { schema: 'rank-reference-bets-v1', basis: 'ranking_only',
      purchase_eligible: false, monetary_amounts_assigned: false,
      tickets: [{ bet_type: '単勝', combination: '10', horse_numbers: [10] }] }
    const before = JSON.stringify(result)
    await mount(surface, result)
    expect(screen.queryByText('参考試算')).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '参考買い目' })).not.toBeInTheDocument()
    expect(screen.getByText('合計 ¥0')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '購入済みとして記録' })).not.toBeInTheDocument()
    expect(JSON.stringify(result)).toBe(before)
    fireEvent.click(screen.getByRole('button', { name: '三連単' }))
    const positions = screen.getByRole('group', { name: '三連単の着順候補' })
    expect(within(positions).getByText('10')).toBeInTheDocument()
    expect(within(positions).getAllByText('1・2・3・4')).toHaveLength(2)
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument()
    expect(screen.getByText('合計 ¥0')).toBeInTheDocument()
    expect(JSON.stringify(result)).toBe(before)
  })

  it('shows a compact number-only trifecta pattern from table marks without granting purchase authority', async () => {
    const result = referenceExpectationResult()
    const template = result.predictions[0]
    result.predictions = [[1, 2.3], [7, 2.5], [3, 10], [11, 11.7], [17, 8], [6, 32.8], [8, 100]]
      .map(([number, odds], index) => ({
        ...template, horse_number: number, horse_name: `候補馬${number}`, predicted_rank: index + 1, odds,
        p_norm: index === 4 ? 0.9 : 0.1 / 6, win_probability: index === 4 ? 0.9 : 0.1 / 6,
      }))
    result.race_info.num_horses = result.predictions.length
    const before = JSON.stringify(result)
    await mount(surface, result)
    fireEvent.change(within(screen.getByRole('table', { name: '予測一覧' })).getByRole('combobox', { name: '7番の印を編集' }), { target: { value: '◎' } })
    fireEvent.click(screen.getByRole('button', { name: '三連単' }))
    fireEvent.change(screen.getByRole('combobox', { name: '三連単の買い方' }), { target: { value: 'narrow-second' } })
    const positions = screen.getByRole('group', { name: '三連単の着順候補' })
    expect(within(positions).getByText('7')).toBeInTheDocument()
    expect(within(positions).getByText('1・3')).toBeInTheDocument()
    expect(within(positions).getByText('1・3・6・11')).toBeInTheDocument()
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument()
    expect(screen.getByText('合計 ¥0')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '購入済みとして記録' })).not.toBeInTheDocument()
    expect(JSON.stringify(result)).toBe(before)
    expect(fetchMock.mock.calls.every(([url]) => url === '/api/models?ultimate=true'
      || url === '/api/prediction-freshness' || url.startsWith('/api/races/by-date'))).toBe(true)
  })

  it.each(['local-db-shared-v1', 'local-db-shared-optimized-v1'] as const)(
    'shows a neutral labelled trial, leaving reference probabilities, skip and zero stakes intact: %s', async profile => {
    const result = referenceExpectationResult(profile)
    const before = JSON.stringify(result)
    await mount(surface, result)
    expect(screen.getAllByText('参考期待値').length).toBeGreaterThan(0)
    expect(screen.getAllByText('0.74').length).toBeGreaterThan(0)
    expect(screen.queryByText(REFERENCE_EXPECTATION_NOTE)).not.toBeInTheDocument()
    expect(screen.getAllByText('参考比率').length).toBeGreaterThan(0)
    expect(screen.queryByText('勝率')).not.toBeInTheDocument()
    expect(screen.getAllByText('13.0%').length).toBeGreaterThan(0)
    expect(screen.getByText('合計 ¥0')).toBeInTheDocument()
    expect(screen.getAllByText('見送り').length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: '購入済みとして記録' })).not.toBeInTheDocument()
    expect(JSON.stringify(result)).toBe(before)
    expect(fetchMock.mock.calls.every(([url]) => url === '/api/models?ultimate=true'
      || url === '/api/prediction-freshness' || url.startsWith('/api/races/by-date'))).toBe(true)
  })

  it('keeps the hypothetical quote after five minutes without re-predicting or enabling purchase', async () => {
    const result = referenceExpectationResult()
    const before = JSON.stringify(result)
    await mount(surface, result)
    expect(screen.getAllByText('0.74').length).toBeGreaterThan(0)
    act(() => { vi.advanceTimersByTime(300000) })
    expect(screen.getAllByText('0.74').length).toBeGreaterThan(0)
    expect(screen.queryByText(REFERENCE_EXPECTATION_NOTE)).not.toBeInTheDocument()
    expect(screen.getAllByText('参考期待値').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/参考馬A/).length).toBeGreaterThan(0)
    expect(screen.getAllByText('13.0%').length).toBeGreaterThan(0)
    expect(screen.getByText('合計 ¥0')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '購入済みとして記録' })).not.toBeInTheDocument()
    expect(JSON.stringify(result)).toBe(before)
    expect(fetchMock.mock.calls.every(([url]) => url === '/api/models?ultimate=true'
      || url === '/api/prediction-freshness' || url.startsWith('/api/races/by-date'))).toBe(true)
  })

  it.each(['absent', 'uncalibrated', 'input missing'] as const)('keeps the old safe blank for %s evidence', async reason => {
    const result = referenceExpectationResult()
    if (reason === 'absent') delete result.reference_expectation
    else if (reason === 'uncalibrated') result.recommendation!.probability_basis = 'uncalibrated_score'
    else result.input_diagnostics!.missing_columns = ['feature']
    await mount(surface, result)
    expect(screen.queryByText('0.74')).not.toBeInTheDocument()
    expect(screen.getAllByText(/参考馬A/).length).toBeGreaterThan(0)
    expect(screen.getByText('合計 ¥0')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '購入済みとして記録' })).not.toBeInTheDocument()
  })
})
