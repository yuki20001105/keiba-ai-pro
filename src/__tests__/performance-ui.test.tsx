import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Recommendation } from '@/lib/race-analysis-types'
import { BETTING_POLICY_VERSION } from '@/lib/betting-policy'
import { savePerformanceRecord, performanceDateLabel, performanceStatusLabel } from '@/lib/performance-records'
import { BettingRecommendationPanel } from '@/components/BettingRecommendationPanel'
import { ModelRoiTooltip } from '@/components/ModelRoiPerformance'
import { modelRoiChartRows, readModelPerformance } from '@/lib/model-roi'
import { verifiedPredictionInput } from './helpers/verified-prediction-input'

const { fetchMock } = vi.hoisted(() => ({ fetchMock: vi.fn() }))
vi.mock('@/lib/auth-fetch', () => ({ authFetch: fetchMock }))
vi.mock('@/components/Logo', () => ({ Logo: () => null }))
vi.mock('@/components/ResearchBettingPerformance', () => ({
  ResearchBettingPerformance: () => <section aria-label="101特徴量モデル・買い目比較（研究）" />,
}))
import DashboardPage from '@/app/dashboard/page'

const recommendation = (): Recommendation => ({
  policy_version: BETTING_POLICY_VERSION, probability_basis: 'calibrated', input_verified: true, decision: 'buy', action: '購入候補', reason: '条件を確認済み',
  evaluated_at: new Date().toISOString(), valid_until: new Date(Date.now() + 300000).toISOString(),
  purchase_count: 2, unit_price: 0, total_cost: 300, expected_return: 450,
  tickets: [
    { bet_type: '単勝', combination: '2', horse_numbers: [2], stake: 100, odds: 5, probability: 0.3, expected_value: 1.5, conservative_expected_value: 1.3 },
    { bet_type: 'ワイド', combination: '3-4', horse_numbers: [3, 4], stake: 200, odds: 4, probability: 0.4, expected_value: 1.6, conservative_expected_value: 1.4 },
  ],
})
const record = {
  id: 'record-1', race_id: '202606040101', race_info: { date: '20261010', race_name: 'テストレース', venue: '東京' },
  model_id: 'test-model', policy_version: BETTING_POLICY_VERSION, decision: 'buy', reason: '条件確認済み', mode: 'paper',
  tickets: [{ bet_type: '単勝', combination: '2', horse_numbers: [2], stake: 300 }],
  total_cost: 300, actual_return: 600, profit: 300, status: 'settled', created_at: '2026-10-10T00:00:00Z',
}
const modelPerformance = {
  schema: 'model-cumulative-roi-v2', mode: 'paper', date_from: null, date_to: null,
  source: 'unseen-simulation', origin: 'all', point_granularity: 'day', years: ['2026'],
  includes_full_filtered_set: true,
  models: [{ model_id: 'model-A', total_cost: 300, total_return: 600, profit: 300, roi: 200, race_count: 1,
    points: [{ date: '2026-10-10', race_id: '202606040101', post_time: '15:00',
      total_cost: 300, total_return: 600, profit: 300, roi: 200, race_count: 1 }] },
  { model_id: 'model-B', total_cost: 100, total_return: 0, profit: -100, roi: 0, race_count: 1,
    points: [{ date: '2026-10-11', race_id: '202606040201', post_time: '15:00',
      total_cost: 100, total_return: 0, profit: -100, roi: 0, race_count: 1 }] }].map((model, index) => ({
        ...model, color: index ? '#4ade80' : '#60a5fa', display_name: model.model_id, cutoff_date: '2026-09-27',
      })),
}
const retrospectivePerformance = {
  ...modelPerformance, source: 'combined-reference-simulation', origin: 'combined',
  evaluation_scope: 'non-training-combined',
}
beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('ResizeObserver', class {
    constructor(private callback: ResizeObserverCallback) {}
    observe(target: Element) {
      this.callback([{ target, contentRect: { width: 800, height: 360 } } as ResizeObserverEntry], this as unknown as ResizeObserver)
    }
    unobserve() {}
    disconnect() {}
  })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('performance recording', () => {
  it('shows the expanded-feature research section only in AI reference, not actual purchases', async () => {
    fetchMock.mockImplementation(async () => Response.json({ records: [], summary: {} }))
    render(<DashboardPage />)
    expect(screen.getByRole('region', { name: '101特徴量モデル・買い目比較（研究）' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: '購入済み' }))
    expect(screen.queryByRole('region', { name: '101特徴量モデル・買い目比較（研究）' })).not.toBeInTheDocument()
    await screen.findByText('まだ成績がありません')
  })
  it('persists exact allocated stakes without declaring a paper record purchased', async () => {
    fetchMock.mockResolvedValue(Response.json({ record, existing: false }))
    await savePerformanceRecord('snapshot-1', recommendation())
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/performance/records')
    expect(JSON.parse(init.body)).toEqual({ snapshot_id: 'snapshot-1', mode: 'paper', tickets: [
      { bet_type: '単勝', combination: '2', horse_numbers: [2], stake: 100 },
      { bet_type: 'ワイド', combination: '3-4', horse_numbers: [3, 4], stake: 200 },
    ] })
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/purchase')).toBe(false)
  })
  it('records uncalibrated predictions only as zero-ticket paper decisions', async () => {
    fetchMock.mockResolvedValue(Response.json({ record, existing: false }))
    await savePerformanceRecord('snapshot-1', { ...recommendation(), decision: 'skip', probability_basis: 'uncalibrated_score' })
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).tickets).toEqual([])
    await expect(savePerformanceRecord('snapshot-1', { ...recommendation(), decision: 'skip' }, 'actual')).rejects.toThrow('有効な買い目')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
  it('never claims successful recording without a durable record ID', async () => {
    fetchMock.mockResolvedValue(Response.json({ success: true }))
    await expect(savePerformanceRecord('snapshot-1', recommendation())).rejects.toThrow('保存を確認できません')
    await expect(savePerformanceRecord(null, recommendation())).rejects.toThrow('予測を再実行')
  })
  it('requires two explicit clicks to record actual purchase confirmation', async () => {
    fetchMock.mockResolvedValue(Response.json({ record: { ...record, mode: 'actual' }, existing: false }))
    render(<BettingRecommendationPanel recommendation={recommendation()} diagnostics={verifiedPredictionInput} snapshotId="snapshot-1" />)
    fireEvent.click(screen.getByRole('button', { name: '購入済みとして記録' }))
    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.getByText(/購入は行いません/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '確認して記録' }))
    await screen.findByText('購入済みとして記録しました。')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({ mode: 'actual', confirmed: true })
    expect(screen.queryByRole('button', { name: '購入済みとして記録' })).not.toBeInTheDocument()
  })
  it('explains immutable duplicate records rather than implying tickets were updated', async () => {
    fetchMock.mockResolvedValue(Response.json({ record: { ...record, tickets: recommendation().tickets }, existing: true }))
    render(<BettingRecommendationPanel recommendation={recommendation()} diagnostics={verifiedPredictionInput} snapshotId="snapshot-1" />)
    fireEvent.click(screen.getByRole('button', { name: '購入済みとして記録' }))
    fireEvent.click(screen.getByRole('button', { name: '確認して記録' }))
    await screen.findByText('このレースは記録済みです。最初の買い目を保持しています。')
  })
  it('does not claim actual recording succeeded when an immutable record contains other stakes', async () => {
    fetchMock.mockResolvedValue(Response.json({ record, existing: true }))
    render(<BettingRecommendationPanel recommendation={recommendation()} diagnostics={verifiedPredictionInput} snapshotId="snapshot-1" />)
    fireEvent.click(screen.getByRole('button', { name: '購入済みとして記録' }))
    fireEvent.click(screen.getByRole('button', { name: '確認して記録' }))
    await screen.findByRole('alert')
    expect(screen.getByRole('alert')).toHaveTextContent('別の買い目が記録されています')
    expect(screen.queryByText('購入済みとして記録しました。')).not.toBeInTheDocument()
  })
})

describe('simple performance dashboard', () => {
  const summary = { settled_cost: 300, settled_return: 600, profit: 300, roi: 200, settled_races: 1, hit_races: 1, pending_races: 1, pending_cost: 700, skipped_races: 2 }
  it('shows only settled model ROI and KPIs in paper mode, not race or skip cards', async () => {
    fetchMock.mockResolvedValue(Response.json({ records: [record, { ...record, id: 'pending', status: 'pending', total_cost: 700, actual_return: null, profit: null }], summary, total_count: 2, model_performance: retrospectivePerformance }))
    render(<DashboardPage />)
    await screen.findByRole('checkbox', { name: /model-A/ })
    const cards = within(screen.getByRole('region', { name: '確定分の成績' }))
    expect(cards.getByText('¥300')).toBeInTheDocument()
    expect(cards.getByText('¥600')).toBeInTheDocument()
    expect(cards.getByText('+¥300')).toBeInTheDocument()
    expect(cards.getByText('200.0%')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'モデル別累積回収率グラフ' })).toBeInTheDocument()
    expect(screen.getByText('100% 損益分岐')).toBeInTheDocument()
    expect(screen.queryByText('テストレース')).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'レース別の成績' })).not.toBeInTheDocument()
    expect(screen.queryByText('以前の購入記録')).not.toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'AI参考' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.click(screen.getByRole('checkbox', { name: /model-B/ }))
    expect(cards.getByText('¥300')).toBeInTheDocument()
    expect(cards.getByText('200.0%')).toBeInTheDocument()
    expect(screen.getByText('表示中：1モデル')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('checkbox', { name: /model-A/ }))
    expect(within(screen.getByRole('region', { name: 'モデル別回収率推移' })).getByText('表示モデルを選択してください。')).toBeInTheDocument()
    expect(cards.getByText('未算出')).toBeInTheDocument()
  })
  it('loads actual records separately and treats settled zero returns as losses across refreshes', async () => {
    fetchMock.mockImplementation(async (url: string) => Response.json({ records: [{ ...record, mode: url.includes('mode=actual') ? 'actual' : 'paper', actual_return: 0, profit: -300 }], summary: { ...summary, settled_return: 0, profit: -300, roi: 0, pending_races: 0 }, total_count: 1 }))
    render(<DashboardPage />)
    fireEvent.click(screen.getByRole('tab', { name: '購入済み' }))
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.includes('mode=actual'))).toBe(true))
    await screen.findByText('外れ')
    expect(within(screen.getByRole('region', { name: '確定分の成績' })).getByText('−¥300')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '更新' }))
    await screen.findByText('外れ')
  })
  it('does not make a truncated record page look like a complete model curve', async () => {
    fetchMock.mockResolvedValue(Response.json({ records: [record], summary, total_count: 101 }))
    render(<DashboardPage />)
    await screen.findByText(/モデル別集計を取得できません/)
    expect(screen.queryByRole('img', { name: 'モデル別累積回収率グラフ' })).not.toBeInTheDocument()
    expect(screen.queryByText('200.0%')).not.toBeInTheDocument()
  })
  it('distinguishes no settled investments from a zero-percent losing model', async () => {
    fetchMock.mockResolvedValue(Response.json({ records: [], summary, total_count: 0,
      model_performance: { ...retrospectivePerformance, models: [] } }))
    render(<DashboardPage />)
    await screen.findByText('投資・払戻が確定したAI参考の記録がありません。')
    expect(screen.getByText('未算出')).toBeInTheDocument()
    expect(screen.queryByText('0.0%')).not.toBeInTheDocument()
  })
  it('recomputes the date scope for thirty days and resets it for all history', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const query = new URL(url, 'http://localhost').searchParams
      const date = (key: string) => query.get(key)?.replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3') ?? null
      return Response.json({ records: [], summary: { ...summary, pending_races: 0 }, total_count: 0,
        model_performance: { ...retrospectivePerformance, models: [], date_from: date('date_from'), date_to: date('date_to') } })
    })
    render(<DashboardPage />)
    await screen.findByText('投資・払戻が確定したAI参考の記録がありません。')
    fireEvent.click(screen.getByRole('button', { name: '直近30日' }))
    const from = (screen.getByLabelText('開始日') as HTMLInputElement).value
    const to = (screen.getByLabelText('終了日') as HTMLInputElement).value
    expect((Date.parse(to) - Date.parse(from)) / 86400000).toBe(29)
    await screen.findByText('投資・払戻が確定したAI参考の記録がありません。')
    fireEvent.click(screen.getByRole('button', { name: '全期間' }))
    expect(screen.getByLabelText('開始日')).toHaveValue('')
    expect(screen.getByLabelText('終了日')).toHaveValue('')
    await screen.findByText('投資・払戻が確定したAI参考の記録がありません。')
  })
  it('preserves unknown outcomes and unknown dates instead of deriving them from race IDs', () => {
    expect(performanceDateLabel(undefined)).toBe('日付未確認')
    expect(performanceDateLabel('2026-10-10')).toBe('2026/10/10')
    expect(performanceStatusLabel({ status: 'pending', actual_return: 0 })).toBe('結果待ち')
    expect(performanceStatusLabel({ status: 'settled', actual_return: 0 })).toBe('外れ')
    expect(performanceStatusLabel({ status: 'payout_pending', actual_return: null })).toBe('払戻待ち')
  })

  describe('independent model chart observations', () => {
    it('does not carry a later morning race into a single-digit earlier hour', () => {
      const models = modelPerformance.models.map((model, index) => ({ ...model,
        points: model.points.map(point => ({ ...point, date: '2026-10-10', post_time: index ? '10:20' : '9:50' })) }))
      const rows = modelRoiChartRows(models)
      expect(rows.map(row => row.post_time)).toEqual(['09:50', '10:20'])
      expect(rows[0].values.has('model-B')).toBe(false)
      expect(rows[1].values.get('model-A')?.roi).toBe(200)
    })
    it('carries only past observations forward and keeps confirmed zero-return tooltip values', () => {
      const models = modelPerformance.models
      const rows = modelRoiChartRows([...models].reverse())
      expect(rows[0].values.has('model-B')).toBe(false)
      expect(rows[1].values.get('model-A')?.roi).toBe(200)
      expect(rows[1].values.get('model-B')?.roi).toBe(0)
      render(<ModelRoiTooltip row={rows[1]} models={models} />)
      expect(screen.getByText('model-B · 0.0%')).toBeInTheDocument()
      expect(screen.getByText('累計投資 ¥100 · 累計払戻 ¥0')).toBeInTheDocument()
      expect(screen.getByText('収支 −¥100 · 1レース')).toBeInTheDocument()
    })
    it('rejects a response outside the requested period or mode rather than reusing its curve', () => {
      expect(readModelPerformance(modelPerformance, 'actual', '', '')).toBeNull()
      expect(readModelPerformance(modelPerformance, 'paper', '2026-10-11', '')).toBeNull()
      expect(readModelPerformance({ ...modelPerformance, includes_full_filtered_set: false }, 'paper', '', '')).toBeNull()
    })
  })
})
