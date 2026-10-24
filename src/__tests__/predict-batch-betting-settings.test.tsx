import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BETTING_POLICY_VERSION } from '@/lib/betting-policy'
import { verifiedPredictionInput } from './helpers/verified-prediction-input'
import { freshPrediction } from './helpers/prediction-freshness'

const { fetchMock } = vi.hoisted(() => ({ fetchMock: vi.fn() }))
vi.mock('@/lib/auth-fetch', () => ({ authFetch: fetchMock }))
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ isPremium: false, userId: 'test-owner' }) }))
vi.mock('@/components/Logo', () => ({ Logo: () => null }))
vi.mock('@/components/Toast', () => ({ Toast: () => null }))
vi.mock('@/components/RaceExplanationPanel', () => ({ RaceExplanationPanel: () => null }))
vi.mock('@/hooks/useJobPoller', () => ({ useJobPoller: () => ({}) }))
import PredictBatchPage from '@/app/predict-batch/page'

const race = { race_id: '202606040101', race_name: 'テストレース', venue: '中山', venue_code: '06', race_no: 1, distance: 1200, track_type: '芝', num_horses: 12 }
const rec = { policy_version: BETTING_POLICY_VERSION, probability_basis: 'calibrated', input_verified: true, action: '購入候補', decision: 'buy', reason: '余裕のある期待倍率',
  bankroll: 10000, risk_mode: 'balanced', budget: 300, per_race_limit: 300, purchase_count: 1, unit_price: 300, total_cost: 300, expected_return: 450,
  evaluated_at: new Date().toISOString(), valid_until: new Date(Date.now() + 300000).toISOString(),
  tickets: [{ bet_type: '単勝', combination: '2', horse_numbers: [2], stake: 300, odds: 5, probability: 0.3, expected_value: 1.5, conservative_expected_value: 1.3 }] }
beforeEach(() => {
  localStorage.clear()
  fetchMock.mockReset()
  fetchMock.mockImplementation(async (url: string) => {
    if (url === '/api/health') return Response.json({ status: 'ok', prediction_runtime: { version: 'bounded-cpu-v1', max_workers: 4 } })
    if (url.startsWith('/api/prediction-freshness')) return Response.json(freshPrediction)
    if (url === '/api/models?ultimate=true') return Response.json({ models: [] })
    if (url.startsWith('/api/races/by-date')) return Response.json({ races: [race] })
    throw new Error(`Unexpected request: ${url}`)
  })
})
afterEach(() => { cleanup(); vi.useRealTimers() })
function cache(recommendation: unknown) {
  localStorage.setItem(`ra-cache:${race.race_id}`, JSON.stringify({ owner_user_id: 'test-owner', data: { model_id: 'current', freshness: freshPrediction, race_info: { date: '20200101' }, input_diagnostics: verifiedPredictionInput, predictions: [], recommendation }, cachedAt: Date.now() }))
}

describe('batch betting settings', () => {
  it('names the active reference default and explains the restriction before any prediction', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (url === '/api/models?ultimate=true') return Response.json({ models: [
        { model_id: 'reference-nll', target: 'win', is_active: true,
          feature_pipeline: 'local-win-nll-reference-v1', display_name: '全履歴・勝率NLL（参考運用）' },
        { model_id: 'preserved-speed', target: 'speed_deviation', is_active: false },
      ] })
      throw new Error(`Unexpected request: ${url}`)
    })
    render(<PredictBatchPage />)
    expect(await screen.findByRole('option', { name: '既定：全履歴・勝率NLL（参考運用）' })).toBeInTheDocument()
    expect(screen.getByTestId('selected-model-reference-notice')).toHaveTextContent('購入判断には使用しません')
    fireEvent.change(screen.getByLabelText('使用モデル'), { target: { value: 'preserved-speed' } })
    expect(screen.queryByTestId('selected-model-reference-notice')).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('使用モデル'), { target: { value: 'reference-nll' } })
    expect(screen.getByTestId('selected-model-reference-notice')).toHaveTextContent('発走前オッズ・未知期間は未検証')
    expect(fetchMock.mock.calls.every(([url]) => url === '/api/models?ultimate=true')).toBe(true)
  })

  it('invalidates stake recommendations on bankroll changes without automatically predicting again', async () => {
    cache(rec)
    render(<PredictBatchPage />)
    fireEvent.click(screen.getByRole('button', { name: 'レース一覧を取得' }))
    await screen.findByText('合計 ¥300 / 予算 ¥1,000')
    fireEvent.click(screen.getByRole('button', { name: /賭け設定/ }))
    expect(screen.getByText('1レース上限: ¥300')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('総資金'), { target: { value: '20000' } })
    expect(screen.getByText('買い目は再判定が必要です。予測を再実行してください。')).toBeInTheDocument()
    expect(screen.queryByText('合計 ¥300 / 予算 ¥1,000')).not.toBeInTheDocument()
    expect(localStorage.getItem(`ra-cache:${race.race_id}`)).not.toBeNull()
    expect(screen.getByLabelText('この日の予算')).toHaveValue(2000)
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/analyze-race')).toBe(false)
  })
  it('rejects legacy cached recommendations and tells the user to reevaluate', async () => {
    cache({ ...rec, policy_version: undefined })
    render(<PredictBatchPage />)
    fireEvent.click(screen.getByRole('button', { name: 'レース一覧を取得' }))
    await waitFor(() => expect(screen.getByText('買い目は再判定が必要です。予測を再実行してください。')).toBeInTheDocument())
    expect(screen.queryByText(/合計 ¥300/)).not.toBeInTheDocument()
  })
  it.each([undefined, null, { status: 'aligned' }, { ...verifiedPredictionInput, semantic_parity_verified: false }])(
    'does not restore a cached purchase with unknown or contradictory input evidence: %o', async diagnostics => {
      localStorage.setItem(`ra-cache:${race.race_id}`, JSON.stringify({ data: {
        input_diagnostics: diagnostics, race_info: { date: '20200101' }, predictions: [], recommendation: rec,
      }, cachedAt: Date.now() }))
      render(<PredictBatchPage />)
      fireEvent.click(screen.getByRole('button', { name: 'レース一覧を取得' }))
      await screen.findByText('買い目は再判定が必要です。予測を再実行してください。')
      expect(screen.queryByText(/合計 ¥300/)).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: '購入済みとして記録' })).not.toBeInTheDocument()
      expect(fetchMock.mock.calls.some(([url]) => url === '/api/analyze-race')).toBe(false)
    },
  )
  it('retains zero-ticket reference cache without another prediction', async () => {
    cache({ ...rec, decision: 'skip', input_verified: false, tickets: [], total_cost: 0, purchase_count: 0,
      unit_price: 0, expected_return: 0, reason: '入力互換性未確認' })
    render(<PredictBatchPage />)
    fireEvent.click(screen.getByRole('button', { name: 'レース一覧を取得' }))
    await screen.findByText('合計 ¥0 / 予算 ¥1,000')
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/analyze-race')).toBe(false)
    expect(screen.queryByRole('button', { name: '購入済みとして記録' })).not.toBeInTheDocument()
  })
  it('does not restore a different model when an explicit model is selected', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (url === '/api/health') return Response.json({ status: 'ok', prediction_runtime: { version: 'bounded-cpu-v1', max_workers: 4 } })
      if (url === '/api/models?ultimate=true') return Response.json({ models: [{ model_id: 'model_b' }] })
      if (url.startsWith('/api/races/by-date')) return Response.json({ races: [race] })
      throw new Error(`Unexpected request: ${url}`)
    })
    render(<PredictBatchPage />)
    await waitFor(() => expect(screen.getByLabelText('使用モデル').querySelector('option[value="model_b"]')).not.toBeNull())
    fireEvent.change(screen.getByLabelText('使用モデル'), { target: { value: 'model_b' } })
    localStorage.setItem(`ra-cache:${race.race_id}`, JSON.stringify({ data: {
      model_id: 'model_a', race_info: { date: '20200101' }, predictions: [], recommendation: rec,
    }, cachedAt: Date.now() }))
    fireEvent.click(screen.getByRole('button', { name: 'レース一覧を取得' }))
    await screen.findByText('テストレース')
    expect(screen.queryByText(/合計 ¥300/)).not.toBeInTheDocument()
    expect(screen.getByText('買い目は再判定が必要です。予測を再実行してください。')).toBeInTheDocument()
  })
  it('records only the final day allocation, including zero-ticket skips, once after prediction', async () => {
    const highRace = { ...race, race_id: '202606040102', race_name: '高期待レース', race_no: 2 }
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.startsWith('/api/prediction-freshness')) return Response.json(freshPrediction)
      if (url === '/api/health') return Response.json({ status: 'ok', prediction_runtime: { version: 'bounded-cpu-v1', max_workers: 4 } })
      if (url === '/api/models?ultimate=true') return Response.json({ models: [] })
      if (url.startsWith('/api/races/by-date')) return Response.json({ races: [race, highRace] })
      if (url === '/api/analyze-race') {
        const id = JSON.parse(String(init?.body)).race_id
        return Response.json({ performance_snapshot_id: id, input_diagnostics: verifiedPredictionInput, race_info: { date: '20261010' }, predictions: [],
          recommendation: { ...rec, tickets: rec.tickets.map(ticket => ({ ...ticket, conservative_expected_value: id === highRace.race_id ? 1.8 : 1.3 })) } })
      }
      if (url === '/api/performance/records') return Response.json({ record: { id: JSON.parse(String(init?.body)).snapshot_id }, existing: false })
      throw new Error(`Unexpected request: ${url}`)
    })
    render(<PredictBatchPage />)
    fireEvent.click(screen.getByRole('button', { name: 'レース一覧を取得' }))
    await screen.findByText('高期待レース')
    fireEvent.click(screen.getByRole('button', { name: /賭け設定/ }))
    fireEvent.change(screen.getByLabelText('この日の予算'), { target: { value: '100' } })
    screen.getAllByRole('checkbox').forEach(checkbox => fireEvent.click(checkbox))
    fireEvent.click(screen.getByRole('button', { name: '選択 2 レースを一括予測' }))
    await screen.findByText(/AI参考として記録しました/)
    const writes = fetchMock.mock.calls.filter(([url]) => url === '/api/performance/records').map(([, init]) => JSON.parse(init.body))
    expect(writes).toHaveLength(2)
    expect(writes.find(write => write.snapshot_id === race.race_id)).toMatchObject({ mode: 'paper', tickets: [] })
    expect(writes.find(write => write.snapshot_id === highRace.race_id).tickets).toEqual([{ bet_type: '単勝', combination: '2', horse_numbers: [2], stake: 100 }])
    expect(JSON.parse(localStorage.getItem(`ra-cache:test-owner::${race.race_id}`)!).data.recommendation.tickets).toEqual([])
    expect(JSON.parse(localStorage.getItem(`ra-cache:test-owner::${highRace.race_id}`)!).data.recommendation.tickets[0].stake).toBe(100)
    expect(JSON.parse(localStorage.getItem(`ra-cache:test-owner::${highRace.race_id}`)!).data.performance_allocation.day_budget).toBe(100)
  })
  it('does not reallocate expired high-value selection to a previously skipped race', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-10-10T01:00:00Z'))
    const highRace = { ...race, race_id: '202606040102', race_name: '高期待レース', race_no: 2 }
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.startsWith('/api/prediction-freshness')) return Response.json(freshPrediction)
      if (url === '/api/health') return Response.json({ status: 'ok', prediction_runtime: { version: 'bounded-cpu-v1', max_workers: 4 } })
      if (url === '/api/models?ultimate=true') return Response.json({ models: [] })
      if (url.startsWith('/api/races/by-date')) return Response.json({ races: [race, highRace] })
      if (url === '/api/analyze-race') {
        const id = JSON.parse(String(init?.body)).race_id
        return Response.json({ performance_snapshot_id: id, model_id: 'current',
          freshness: { ...freshPrediction, generated_at: new Date().toISOString() },
          input_diagnostics: verifiedPredictionInput, race_info: { date: '20261010' }, predictions: [], recommendation: {
          ...rec, evaluated_at: new Date().toISOString(), valid_until: new Date(Date.now() + (id === highRace.race_id ? 1000 : 100000)).toISOString(),
          tickets: rec.tickets.map(ticket => ({ ...ticket, conservative_expected_value: id === highRace.race_id ? 1.8 : 1.3 })),
        } })
      }
      if (url === '/api/performance/records') return Response.json({ record: { id: JSON.parse(String(init?.body)).snapshot_id }, existing: false })
      throw new Error(`Unexpected request: ${url}`)
    })
    render(<PredictBatchPage />)
    fireEvent.click(screen.getByRole('button', { name: 'レース一覧を取得' }))
    await screen.findByText('高期待レース')
    fireEvent.click(screen.getByRole('button', { name: /賭け設定/ }))
    fireEvent.change(screen.getByLabelText('この日の予算'), { target: { value: '100' } })
    screen.getAllByRole('checkbox').forEach(checkbox => fireEvent.click(checkbox))
    fireEvent.click(screen.getByRole('button', { name: '選択 2 レースを一括予測' }))
    await screen.findByText(/AI参考として記録しました/)
    expect(screen.getByText('合計 ¥100 / 予算 ¥100')).toBeInTheDocument()
    vi.setSystemTime(new Date('2026-10-10T01:00:02Z'))
    fireEvent(window, new Event('focus'))
    expect(screen.getByText('合計 ¥0 / 予算 ¥100')).toBeInTheDocument()
    expect(screen.getByText('この日の予算上限')).toBeInTheDocument()
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/performance/records')).toHaveLength(2)
    expect(screen.queryByRole('button', { name: '購入済みとして記録' })).not.toBeInTheDocument()
  })
  it('does not write performance records while restoring cached predictions', async () => {
    cache({ ...rec, performance_snapshot_id: 'old-snapshot' })
    render(<PredictBatchPage />)
    fireEvent.click(screen.getByRole('button', { name: 'レース一覧を取得' }))
    await screen.findByText('合計 ¥300 / 予算 ¥1,000')
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/performance/records')).toBe(false)
  })
  it('keeps an unverified new response as a reference and records only a zero-ticket paper pass', async () => {
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.startsWith('/api/prediction-freshness')) return Response.json(freshPrediction)
      if (url === '/api/health') return Response.json({ status: 'ok', prediction_runtime: { version: 'bounded-cpu-v1', max_workers: 4 } })
      if (url === '/api/models?ultimate=true') return Response.json({ models: [] })
      if (url.startsWith('/api/races/by-date')) return Response.json({ races: [race] })
      if (url === '/api/analyze-race') return Response.json({ performance_snapshot_id: 'reference-snapshot',
        input_diagnostics: { ...verifiedPredictionInput, semantic_parity_verified: false },
        race_info: { date: '20261010' }, predictions: [], recommendation: rec })
      if (url === '/api/performance/records') return Response.json({ record: { id: JSON.parse(String(init?.body)).snapshot_id }, existing: false })
      throw new Error(`Unexpected request: ${url}`)
    })
    render(<PredictBatchPage />)
    fireEvent.click(screen.getByRole('button', { name: 'レース一覧を取得' }))
    await screen.findByText('テストレース')
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: '選択 1 レースを一括予測' }))
    await screen.findByText(/AI参考として記録しました/)
    expect(screen.getByText('合計 ¥0 / 予算 ¥1,000')).toBeInTheDocument()
    if (!screen.queryByText('入力互換性未確認 · 参考予測')) fireEvent.click(screen.getByRole('button', { name: /テストレース/ }))
    await screen.findByText('入力互換性未確認 · 参考予測')
    expect(screen.queryByRole('button', { name: '購入済みとして記録' })).not.toBeInTheDocument()
    const write = fetchMock.mock.calls.find(([url]) => url === '/api/performance/records')!
    expect(JSON.parse(write[1].body)).toMatchObject({ mode: 'paper', tickets: [] })
    expect(JSON.parse(localStorage.getItem(`ra-cache:test-owner::${race.race_id}`)!).data.recommendation).toMatchObject({
      decision: 'skip', input_verified: false, tickets: [], total_cost: 0,
    })
  })
})
