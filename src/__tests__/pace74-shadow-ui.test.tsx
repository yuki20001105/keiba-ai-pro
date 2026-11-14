import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ModelComparison, RacePredictResult } from '@/lib/race-analysis-types'
import { referenceExpectationResult, referenceRace, REFERENCE_NOW } from './helpers/reference-expectation'
import { freshPrediction } from './helpers/prediction-freshness'
import { savedPredictionForDisplay, writePredictionCache } from '@/lib/prediction-freshness'

const { fetchMock } = vi.hoisted(() => ({ fetchMock: vi.fn() }))
vi.mock('@/lib/auth-fetch', () => ({ authFetch: fetchMock }))
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ isPremium: false, userId: 'shadow-owner', loading: false }) }))
vi.mock('@/components/Logo', () => ({ Logo: () => null }))
vi.mock('@/components/Toast', () => ({ Toast: () => null }))
vi.mock('@/hooks/useJobPoller', () => ({ useJobPoller: () => ({}) }))
import { RacePredictionPanel } from '@/components/RacePredictionPanel'
import PredictBatchPage from '@/app/predict-batch/page'

function comparisonResult(): RacePredictResult & { model_comparison: ModelComparison } {
  const result = referenceExpectationResult()
  return {
    ...result,
    model_comparison: {
      schema: 'pace74-shadow-comparison-v1', status: 'compared',
      candidate_model_id: 'pace74-c09-minleaf240', primary_model_id: result.model_id!,
      reference_only: true, purchase_eligible: false, captured_at: new Date(REFERENCE_NOW).toISOString(),
      input_revision: 'same-input', input_aligned: true, candidate_feature_count: 74,
      odds_observed_at: null, odds_verified: false, observation_kind: 'historical',
      comparison_id: 'comparison-fixture', recorded: true, issue_codes: [],
      promotion: { status: 'blocked', eligible: false, reasons: ['prospective_evaluation_pending'],
        acceptance_contract_id: 'model-acceptance-v1' },
      predictions: [
        { horse_number: 1, horse_name: '参考馬A', primary_probability: .13, primary_rank: 1,
          candidate_probability: .6, candidate_rank: 1, odds: 5.7, expected_value: 3.42 },
        { horse_number: 2, horse_name: '参考馬B', primary_probability: .87, primary_rank: 2,
          candidate_probability: .4, candidate_rank: 2, odds: 1.2, expected_value: .48 },
      ],
    },
  }
}

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
    writePredictionCache(referenceRace.race_id, {
      ...result, freshness: { ...freshPrediction, resolved_model_id: 'shared-test-model',
        generated_at: new Date(REFERENCE_NOW).toISOString() },
      recommendation: result.recommendation ? { ...result.recommendation, betting_strategy: 'fixed-two-v1' } : null,
      performance_allocation: { day_budget: 1000, allocated_at: new Date(REFERENCE_NOW).toISOString() },
    }, REFERENCE_NOW, 'shadow-owner')
    render(<PredictBatchPage />)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'レース一覧を取得' })) })
    fireEvent.click(screen.getByRole('button', { name: /参考試算レース/ }))
  }
}

describe.each(['detail', 'batch'])('%s pace74 reference comparison', surface => {
  it('displays paired probabilities and reference EV without changing primary ranks, stakes or the input', async () => {
    const result = comparisonResult()
    const before = JSON.stringify(result)
    await mount(surface, result)
    const panel = within(screen.getByRole('region', { name: '新旧モデル比較（参考専用）' }))
    expect(panel.getByText('60.0%')).toBeVisible()
    expect(panel.getByText('13.0%')).toBeVisible()
    expect(panel.getByText('3.42')).toBeVisible()
    expect(panel.getByText(/購入判断は現行モデルのまま/)).toBeVisible()
    expect(panel.getByText(/正式切替未承認/)).toBeVisible()
    expect(panel.getByText(/過去レースの比較/)).toBeVisible()
    expect(panel.queryByRole('button')).not.toBeInTheDocument()
    expect(panel.queryByRole('spinbutton')).not.toBeInTheDocument()
    const primary = within(screen.getByRole('table', { name: '予測一覧' }))
    expect(primary.getAllByRole('row')[1]).toHaveTextContent('参考馬A')
    expect(primary.queryByText('60.0%')).not.toBeInTheDocument()
    expect(screen.getByText('合計 ¥0')).toBeInTheDocument()
    expect(JSON.stringify(result)).toBe(before)
  })
})

describe('reference evidence boundaries', () => {
  it('shows missing package and recording failures without hiding primary predictions', () => {
    const result = comparisonResult()
    result.model_comparison.status = 'unavailable'
    result.model_comparison.predictions = []
    result.model_comparison.recorded = false
    result.model_comparison.issue_codes = ['shadow_package_missing']
    render(<RacePredictionPanel result={result} />)
    const panel = within(screen.getByRole('region', { name: '新旧モデル比較（参考専用）' }))
    expect(panel.getByText(/比較を利用できません/)).toBeVisible()
    expect(panel.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.getByRole('table', { name: '予測一覧' })).toBeVisible()
    cleanup()
    const unrecorded = comparisonResult()
    unrecorded.model_comparison.recorded = false
    render(<RacePredictionPanel result={unrecorded} />)
    expect(within(screen.getByRole('region', { name: '新旧モデル比較（参考専用）' }))
      .getByText(/比較台帳への保存に失敗/)).toBeVisible()
  })

  it('never presents zero or missing odds as a usable reference expected value', () => {
    const result = comparisonResult()
    result.model_comparison.predictions[0].odds = 0
    result.model_comparison.predictions[0].expected_value = 100
    result.model_comparison.predictions[1].odds = null
    result.model_comparison.predictions[1].expected_value = null
    render(<RacePredictionPanel result={result} />)
    const rows = within(screen.getByRole('table', { name: '新旧勝率の比較（購入対象外）' })).getAllByRole('row')
    expect(within(rows[1]).getAllByText('—')).toHaveLength(2)
    expect(within(rows[2]).getAllByText('—')).toHaveLength(2)
    expect(within(rows[1]).queryByText('100.00')).not.toBeInTheDocument()
  })

  it('rejects mixed-model, partial-cohort or unaligned comparisons rather than showing paired probabilities', () => {
    for (const scenario of ['model', 'cohort', 'input'] as const) {
      const result = comparisonResult()
      if (scenario === 'model') result.model_comparison.primary_model_id = 'another-model'
      if (scenario === 'cohort') result.model_comparison.predictions.pop()
      if (scenario === 'input') result.model_comparison.input_aligned = false
      render(<RacePredictionPanel result={result} />)
      const panel = within(screen.getByRole('region', { name: '新旧モデル比較（参考専用）' }))
      expect(panel.queryByRole('table')).not.toBeInTheDocument()
      expect(panel.getByText(/再予測してください/)).toBeVisible()
      cleanup()
    }
  })

  it('keeps stale comparison references visibly historical instead of presenting them as current paired predictions', () => {
    const result = savedPredictionForDisplay(comparisonResult(), 'stale')
    render(<RacePredictionPanel result={result} />)
    expect(screen.queryByRole('table', { name: '新旧勝率の比較（購入対象外）' })).not.toBeInTheDocument()
    expect(screen.getByText(/新モデル比較は再予測後に表示/)).toBeVisible()
  })
})
