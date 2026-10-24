import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { REFERENCE_EXPECTATION_CAVEAT, REFERENCE_EXPECTATION_NOTE } from '@/lib/prediction-expectation-display'
import type { RacePredictResult } from '@/lib/race-analysis-types'
import { referenceExpectationResult, referenceRace, REFERENCE_NOW } from './helpers/reference-expectation'
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
      performance_allocation: { day_budget: 1000, allocated_at: new Date(REFERENCE_NOW).toISOString() },
    }, REFERENCE_NOW, 'display-owner')
    render(<PredictBatchPage />)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'レース一覧を取得' })) })
    fireEvent.click(screen.getByRole('button', { name: /参考試算レース/ }))
  }
}

describe.each(['detail', 'batch'])('%s reference expectation display only', surface => {
  it.each(['local-db-shared-v1', 'local-db-shared-optimized-v1'] as const)(
    'shows a neutral labelled trial, leaving reference probabilities, skip and zero stakes intact: %s', async profile => {
    const result = referenceExpectationResult(profile)
    const before = JSON.stringify(result)
    await mount(surface, result)
    expect(screen.getAllByText('参考期待値').length).toBeGreaterThan(0)
    expect(screen.getByText('0.74')).toHaveClass('text-[#888]')
    expect(screen.getByText(REFERENCE_EXPECTATION_NOTE)).toBeInTheDocument()
    expect(screen.getByText(REFERENCE_EXPECTATION_NOTE)).toHaveAttribute('title', REFERENCE_EXPECTATION_CAVEAT)
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

  it('hides the number when the trial expires without re-predicting or discarding rankings', async () => {
    await mount(surface, referenceExpectationResult())
    expect(screen.getByText('0.74')).toBeInTheDocument()
    act(() => { vi.advanceTimersByTime(300000) })
    expect(screen.queryByText('0.74')).not.toBeInTheDocument()
    expect(screen.queryByText(REFERENCE_EXPECTATION_NOTE)).not.toBeInTheDocument()
    expect(screen.getByText('参考馬A')).toBeInTheDocument()
    expect(screen.getAllByText('13.0%').length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: '購入済みとして記録' })).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.every(([url]) => url === '/api/models?ultimate=true'
      || url === '/api/prediction-freshness' || url.startsWith('/api/races/by-date'))).toBe(true)
  })

  it.each(['absent', 'uncalibrated', 'input missing'] as const)('keeps the old safe blank for %s evidence', async reason => {
    const result = referenceExpectationResult()
    if (reason === 'absent') delete result.reference_expectation
    else if (reason === 'uncalibrated') result.recommendation!.probability_basis = 'uncalibrated_score'
    else result.input_diagnostics!.missing_columns = ['feature']
    await mount(surface, result)
    expect(screen.queryByText('参考期待値')).not.toBeInTheDocument()
    expect(screen.queryByText('0.74')).not.toBeInTheDocument()
    expect(screen.queryByText(REFERENCE_EXPECTATION_NOTE)).not.toBeInTheDocument()
    expect(screen.getByText('参考馬A')).toBeInTheDocument()
    expect(screen.getByText('合計 ¥0')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '購入済みとして記録' })).not.toBeInTheDocument()
  })
})
