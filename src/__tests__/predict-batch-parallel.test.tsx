import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BETTING_POLICY_VERSION } from '@/lib/betting-policy'
import { verifiedPredictionInput } from './helpers/verified-prediction-input'
import { freshPrediction } from './helpers/prediction-freshness'

const { fetchMock } = vi.hoisted(() => ({ fetchMock: vi.fn() }))
vi.mock('@/lib/auth-fetch', () => ({ authFetch: fetchMock }))
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ isPremium: true }) }))
vi.mock('@/components/Logo', () => ({ Logo: () => null }))
vi.mock('@/components/Toast', () => ({ Toast: () => null }))
vi.mock('@/components/RaceExplanationPanel', () => ({ RaceExplanationPanel: () => null }))
vi.mock('@/hooks/useJobPoller', () => ({ useJobPoller: () => ({}) }))
import PredictBatchPage from '@/app/predict-batch/page'

const races = Array.from({ length: 6 }, (_, index) => ({
  race_id: `2026050401${String(index + 1).padStart(2, '0')}`, race_name: `テスト${index + 1}`, venue: '東京', venue_code: '05',
  race_no: index + 1, distance: 1600, track_type: '芝', num_horses: 12,
}))
function deferred() {
  let resolve!: (value: Response) => void
  let reject!: (error: Error) => void
  const promise = new Promise<Response>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
function response(index: number) {
  return Response.json({ success: true, performance_snapshot_id: `snapshot-${index}`, model_id: 'current',
    input_diagnostics: verifiedPredictionInput,
    race_info: { date: '20261010', race_name: `テスト${index + 1}` }, predictions: [], recommendation: {
      policy_version: BETTING_POLICY_VERSION, probability_basis: 'calibrated', input_verified: true, action: '購入候補', decision: 'buy',
      reason: '条件確認済み', bankroll: 10000, risk_mode: 'balanced', budget: 300, per_race_limit: 300,
      purchase_count: 1, unit_price: 100, total_cost: 100, expected_return: 150,
      evaluated_at: new Date().toISOString(), valid_until: new Date(Date.now() + 300000).toISOString(),
      tickets: [{ bet_type: '単勝', combination: '2', horse_numbers: [2], stake: 100, odds: 5,
        probability: 0.3, expected_value: 1.5, conservative_expected_value: 1.3 }],
    } })
}
let pending: ReturnType<typeof deferred>[]
let started: string[]
let healthPayload: unknown
let healthFailure: boolean
let pendingHealth: ReturnType<typeof deferred> | undefined
beforeEach(() => {
  localStorage.clear()
  pending = races.map(() => deferred())
  started = []
  healthPayload = { status: 'ok', prediction_runtime: { version: 'bounded-cpu-v1', max_workers: 4 } }
  healthFailure = false
  pendingHealth = undefined
  fetchMock.mockReset()
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.startsWith('/api/prediction-freshness')) return Response.json(freshPrediction)
    if (url === '/api/models?ultimate=true') return Response.json({ models: [] })
    if (url.startsWith('/api/races/by-date')) return Response.json({ races })
    if (url === '/api/health') {
      if (pendingHealth) return pendingHealth.promise
      if (healthFailure) throw new Error('offline')
      return Response.json(healthPayload)
    }
    if (url === '/api/analyze-race') {
      const id = JSON.parse(String(init?.body)).race_id
      started.push(id)
      return pending[races.findIndex(race => race.race_id === id)].promise
    }
    if (url === '/api/performance/records') return Response.json({ record: { id: JSON.parse(String(init?.body)).snapshot_id }, existing: false })
    throw new Error(`Unexpected request: ${url}`)
  })
})
afterEach(() => { cleanup(); vi.useRealTimers() })
async function start(capacity = 4) {
  const view = render(<PredictBatchPage />)
  fireEvent.click(screen.getByRole('button', { name: 'レース一覧を取得' }))
  await screen.findByText('テスト6')
  fireEvent.click(screen.getByRole('button', { name: '全選択' }))
  fireEvent.click(screen.getByRole('button', { name: '選択 6 レースを一括予測' }))
  await waitFor(() => expect(started).toHaveLength(capacity))
  return view
}

describe('parallel batch prediction UI', () => {
  it('falls back to one-at-a-time for an old server and displays the update notice', async () => {
    healthPayload = { status: 'ok' }
    await start(1)
    expect(screen.getByText('サーバー更新待ち · 1件ずつ')).toBeInTheDocument()
    expect(screen.getByTestId('prediction-live-progress')).toHaveTextContent('最大1件')
    await act(async () => { pending[0].resolve(response(0)) })
    await waitFor(() => expect(started).toHaveLength(2))
    await act(async () => { pending.forEach((item, index) => item.resolve(response(index))) })
    await screen.findByText(/AI参考として記録しました/)
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/performance/records')).toHaveLength(6)
  })

  it('uses the server RAM-limited capacity instead of assuming four workers', async () => {
    healthPayload = { status: 'ok', prediction_runtime: { version: 'bounded-cpu-v1', max_workers: 2 } }
    await start(2)
    expect(screen.getByTestId('prediction-live-progress')).toHaveTextContent('最大2件')
    expect(screen.queryByText('サーバー更新待ち · 1件ずつ')).not.toBeInTheDocument()
  })

  it('falls back safely when the health check is offline', async () => {
    healthFailure = true
    await start(1)
    expect(screen.getByText('サーバー更新待ち · 1件ずつ')).toBeInTheDocument()
    expect(started).toHaveLength(1)
  })

  it('aborts the initial health check and never starts races from a late capability response', async () => {
    pendingHealth = deferred()
    const view = render(<PredictBatchPage />)
    fireEvent.click(screen.getByRole('button', { name: 'レース一覧を取得' }))
    await screen.findByText('テスト6')
    fireEvent.click(screen.getByRole('button', { name: '全選択' }))
    fireEvent.click(screen.getByRole('button', { name: '選択 6 レースを一括予測' }))
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url === '/api/health')).toBe(true))
    expect(screen.getByTestId('prediction-live-progress')).toHaveTextContent('実行環境を確認中')
    const healthSignal = fetchMock.mock.calls.find(([url]) => url === '/api/health')![1].signal as AbortSignal
    view.unmount()
    expect(healthSignal.aborted).toBe(true)
    await act(async () => { pendingHealth!.resolve(Response.json(healthPayload)) })
    expect(started).toHaveLength(0)
    expect(localStorage.length).toBe(0)
  })

  it('fills rolling slots, retains explanation requests, and freezes/records only after all predictions finish', async () => {
    await start()
    expect(screen.getByTestId('prediction-live-progress')).toHaveTextContent('並列リクエスト 4件（最大4件）')
    expect(screen.getByTestId('prediction-live-progress')).toHaveTextContent('速度を計測中')
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/analyze-race').every(([, init]) => JSON.parse(init.body).include_explanation === true)).toBe(true)
    await act(async () => { pending[1].resolve(response(1)) })
    await waitFor(() => expect(started).toHaveLength(5))
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/performance/records')).toBe(false)
    expect(JSON.parse(localStorage.getItem(`ra-cache:${races[1].race_id}`)!).data.performance_snapshot_id).toBeNull()
    await act(async () => { pending[4].resolve(response(4)) })
    await waitFor(() => expect(started).toHaveLength(6))
    await act(async () => { pending.forEach((item, index) => item.resolve(response(index))) })
    await screen.findByText(/AI参考として記録しました/)
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/performance/records')).toHaveLength(6)
    expect(screen.getByTestId('prediction-live-progress')).toHaveTextContent('予測完了')
    for (const race of races) expect(JSON.parse(localStorage.getItem(`ra-cache:${race.race_id}`)!).data.performance_allocation).toBeDefined()
    fireEvent(window, new Event('focus'))
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/performance/records')).toHaveLength(6)
  })

  it('continues after an individual failed race without recording that failure', async () => {
    await start()
    await act(async () => { pending[0].reject(new Error('このレースの予測に失敗')) })
    await waitFor(() => expect(started).toHaveLength(5))
    await act(async () => { pending[1].resolve(response(1)) })
    await waitFor(() => expect(started).toHaveLength(6))
    await act(async () => { pending.slice(2).forEach((item, index) => item.resolve(response(index + 2))) })
    await screen.findByText(/AI参考として記録しました/)
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/performance/records')).toHaveLength(5)
    expect(screen.getByTestId('prediction-live-progress')).toHaveTextContent('処理 6/6')
  })

  it('shows the Japanese quota message instead of only an HTTP code', async () => {
    await start()
    await act(async () => {
      pending.forEach(item => item.resolve(Response.json({ detail: { error: 'pred_limit_exceeded', message: '月間予測回数の上限に達しました。' } }, { status: 429 })))
    })
    await screen.findByText('予測完了')
    fireEvent.click(screen.getByRole('button', { name: /テスト1/ }))
    expect(screen.getByText('月間予測回数の上限に達しました。')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/performance/records')).toBe(false)
  })

  it('rejects a duplicate button action without adding more prediction requests', async () => {
    await start()
    const button = screen.getByRole('button', { name: /予測中\.\.\./ })
    fireEvent.click(button)
    fireEvent.click(button)
    expect(started).toHaveLength(4)
  })

  it('aborts on unmount and never caches or records late responses', async () => {
    const view = await start()
    const signals = fetchMock.mock.calls.filter(([url]) => url === '/api/analyze-race').map(([, init]) => init.signal as AbortSignal)
    view.unmount()
    expect(signals.every(signal => signal.aborted)).toBe(true)
    await act(async () => { pending.forEach((item, index) => item.resolve(response(index))) })
    expect(started).toHaveLength(4)
    expect(localStorage.length).toBe(0)
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/performance/records')).toBe(false)
  })

  it.each(['2026-10-10T00:00:00Z', '2026-10-11T00:00:00Z'])(
    'invalidates an old generation on a date change even when responses ignore the abort signal (clock %s)', async now => {
    // Date-only fakes keep the queue/React timers real, and cover the calendar
    // rollover that made the former fixed 10/11 change become a no-op.
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(now))
    await start()
    const dateInput = screen.getByLabelText('日付') as HTMLInputElement
    const initialDate = dateInput.value
    const nextDate = initialDate === '2026-10-11' ? '2026-10-12' : '2026-10-11'
    const signals = fetchMock.mock.calls.filter(([url]) => url === '/api/analyze-race').map(([, init]) => init.signal as AbortSignal)
    expect(signals).toHaveLength(4)
    expect(nextDate).not.toBe(initialDate)
    fireEvent.change(dateInput, { target: { value: nextDate } })
    expect(dateInput).toHaveValue(nextDate)
    // Do not release the abort-ignoring responses until invalidation has run.
    await waitFor(() => expect(signals.every(signal => signal.aborted)).toBe(true))
    await act(async () => { pending.forEach((item, index) => item.resolve(response(index))) })
    expect(started).toHaveLength(4)
    expect(localStorage.length).toBe(0)
    expect(screen.queryByText('③ 予測結果・買い目')).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/performance/records')).toBe(false)
    },
  )
})
