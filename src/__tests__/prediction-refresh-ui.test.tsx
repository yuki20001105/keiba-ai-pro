import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RacePredictResult } from '@/lib/race-analysis-types'
import { BETTING_POLICY_VERSION } from '@/lib/betting-policy'
import { writePredictionCache } from '@/lib/prediction-freshness'
import { freshPrediction } from './helpers/prediction-freshness'
import { verifiedPredictionInput } from './helpers/verified-prediction-input'

const { fetchMock } = vi.hoisted(() => ({ fetchMock: vi.fn() }))
vi.mock('@/lib/auth-fetch', () => ({ authFetch: fetchMock }))
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ isPremium: false, loading: false, userId: 'owner' }) }))
vi.mock('next/navigation', () => ({ useSearchParams: () => new URLSearchParams('date=20261012') }))
vi.mock('@/components/Logo', () => ({ Logo: () => null }))
vi.mock('@/components/Toast', () => ({ Toast: () => null }))
vi.mock('@/components/RaceExplanationPanel', () => ({ RaceExplanationPanel: () => null }))
vi.mock('@/components/RacePredictionPanel', () => ({ RacePredictionPanel: ({ result }: { result: RacePredictResult }) => <p data-testid="detail-result">{result.predictions[0]?.horse_name} {result.performance_snapshot_id ?? '記録権限なし'}</p> }))
vi.mock('@/hooks/useJobPoller', () => ({ useJobPoller: () => ({}) }))
import PredictBatchPage from '@/app/predict-batch/page'
import RaceAnalysisPage from '@/app/race-analysis/page'

const race = { race_id: '202605040301', race_name: '保存検証レース', venue: '東京', venue_code: '05', race_no: 1,
  date: '20261012', distance: 1600, track_type: '芝', num_horses: 12 }
function prediction(name = '保存順位の馬'): RacePredictResult {
  return { success: true, model_id: 'current', freshness: { ...freshPrediction, generated_at: new Date().toISOString() },
    race_info: race, performance_snapshot_id: 'new-snapshot', input_diagnostics: verifiedPredictionInput,
    best_bet_type: null, pro_evaluation: null,
    predictions: [{ horse_number: 1, horse_name: name, jockey_name: '', trainer_name: '', sex: '', age: 4, horse_weight: 480,
      odds: 5, popularity: 1, win_probability: 0.3, p_raw: 0.3, p_norm: 0.3, predicted_rank: 1, expected_value: 1.5 }],
    recommendation: { policy_version: BETTING_POLICY_VERSION, probability_basis: 'calibrated', input_verified: true,
      decision: 'buy', action: '購入候補', reason: '条件検証済み', bankroll: 10000, risk_mode: 'balanced', budget: 300, per_race_limit: 300,
      purchase_count: 1, unit_price: 100, total_cost: 100, expected_return: 150,
      evaluated_at: new Date().toISOString(), valid_until: new Date(Date.now() + 300000).toISOString(),
      tickets: [{ bet_type: '単勝', combination: '1', horse_numbers: [1], stake: 100, odds: 5, probability: 0.3, expected_value: 1.5, conservative_expected_value: 1.3 }] },
  }
}
let descriptor: unknown
let descriptorStatus: number
beforeEach(() => {
  localStorage.clear(); fetchMock.mockReset(); descriptor = freshPrediction; descriptorStatus = 200
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url === '/api/models?ultimate=true') return Response.json({ models: [] })
    if (url === '/api/health') return Response.json({ prediction_runtime: { version: 'bounded-cpu-v1', max_workers: 4 } })
    if (url.startsWith('/api/races/by-date')) return Response.json({ races: [race] })
    if (url.startsWith('/api/prediction-freshness')) return Response.json(descriptor, { status: descriptorStatus })
    if (url === '/api/analyze-race') return Response.json(prediction('再計算後の馬'))
    if (url === '/api/performance/records') return Response.json({ record: { id: JSON.parse(String(init?.body)).snapshot_id }, existing: true, selection_matches: false })
    throw new Error(`Unexpected request: ${url}`)
  })
})
afterEach(cleanup)
async function mount(surface: string, saved: RacePredictResult | null = prediction()) {
  if (saved) writePredictionCache(race.race_id, saved, Date.now(), 'owner')
  render(surface === 'batch' ? <PredictBatchPage /> : <RaceAnalysisPage />)
  if (surface === 'batch') fireEvent.click(screen.getByRole('button', { name: 'レース一覧を取得' }))
  if (surface === 'batch') await screen.findByRole('checkbox')
  else await screen.findByText('保存検証レース')
  if (surface === 'detail') fireEvent.click(screen.getByRole('button', { name: /保存検証レース/ }))
  if (saved) {
    if (surface === 'batch') { await screen.findByText(/合計 ¥/); fireEvent.click(screen.getByRole('button', { name: /保存検証レース/ })) }
    await screen.findByTestId('prediction-freshness-notice')
  }
}
async function recompute(surface: string) {
  if (surface === 'batch') fireEvent.click(screen.getByRole('checkbox'))
  fireEvent.click(screen.getByRole('button', { name: /最新データで再予測/ }))
}

describe.each(['batch', 'detail'])('%s latest saved-data prediction', surface => {
  it('restores verified display-only results without running inference or writing records', async () => {
    await mount(surface)
    expect(screen.getByTestId('prediction-freshness-notice')).toHaveTextContent('保存予測 · データ・モデルの更新なし')
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/analyze-race' || url === '/api/performance/records')).toBe(false)
    expect(screen.queryByRole('button', { name: '購入済みとして記録' })).not.toBeInTheDocument()
  })
  it.each(['data_revision', 'model_runtime_revision', 'resolved_model_id'] as const)('shows changed %s as old output without purchase/EV authority', async field => {
    descriptor = { ...freshPrediction, [field]: 'new-revision' }
    await mount(surface)
    expect(screen.getByTestId('prediction-freshness-notice')).toHaveTextContent('保存予測 · 最新データで再予測してください')
    expect(screen.getByText(/保存順位の馬/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '購入済みとして記録' })).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/analyze-race' || url === '/api/performance/records')).toBe(false)
  })
  it('checks freshness when returning from acquisition, without automatically rerunning', async () => {
    await mount(surface)
    descriptor = { ...freshPrediction, data_revision: 'newly-acquired-history' }
    fireEvent.focus(window)
    await waitFor(() => expect(screen.getByTestId('prediction-freshness-notice')).toHaveTextContent('最新データで再予測してください'))
    expect(screen.getByText(/保存順位の馬/)).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/analyze-race' || url === '/api/performance/records')).toBe(false)
  })
  it('recomputes only on explicit click using DB-only bypass, retaining the first recorded selection', async () => {
    await mount(surface)
    localStorage.setItem('sealed-paper-record', 'first-selection')
    await recompute(surface)
    await screen.findByText(/再計算後の馬/)
    const posts = fetchMock.mock.calls.filter(([url]) => url === '/api/analyze-race')
    expect(posts).toHaveLength(1)
    expect(JSON.parse(posts[0][1].body)).toMatchObject({ race_id: race.race_id, force_recompute: true, data_refresh: 'saved_db' })
    expect(localStorage.getItem('sealed-paper-record')).toBe('first-selection')
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/scrape' || url === '/api/train')).toBe(false)
    await waitFor(() => expect(screen.queryByRole('button', { name: '購入済みとして記録' })).not.toBeInTheDocument())
  })
  it.each(['capability-missing', 'offline', 'unknown-schema'])('does not POST to an unsupported API: %s', async failure => {
    await mount(surface)
    descriptor = failure === 'capability-missing' ? { ...freshPrediction, capabilities: {} }
      : failure === 'unknown-schema' ? { ...freshPrediction, schema_version: 'other' } : { error: 'unavailable' }
    descriptorStatus = failure === 'offline' ? 503 : 200
    await recompute(surface)
    await screen.findByText(/APIの更新・接続状態を確認してください/)
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/analyze-race' || url === '/api/performance/records')).toBe(false)
    expect(screen.getByText(/保存順位の馬/)).toBeInTheDocument()
  })
})
