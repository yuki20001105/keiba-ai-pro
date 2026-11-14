import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { readModelPerformance, readMonthlyPerformance } from '@/lib/model-roi'
import DashboardPage from '@/app/dashboard/page'

const { fetchMock } = vi.hoisted(() => ({ fetchMock: vi.fn() }))
vi.mock('@/lib/auth-fetch', () => ({ authFetch: fetchMock }))
vi.mock('@/components/Logo', () => ({ Logo: () => null }))

const totals = (cost = 0, returned = 0, count = 0) => ({
  total_cost: cost, total_return: returned, profit: returned - cost,
  roi: cost ? Math.round(returned / cost * 1000) / 10 : null, race_count: count,
})
const data = {
  schema: 'model-cumulative-roi-v2', mode: 'paper', source: 'unseen-simulation', origin: 'all',
  date_from: null, date_to: null, point_granularity: 'day', includes_full_filtered_set: true,
  years: ['2025', '2026'], origin_counts: { historical: 3 },
  models: [
    { model_id: 'model-A', display_name: 'モデルA', color: '#60a5fa', model_sha256: 'a'.repeat(64), cutoff_date: '2026-09-27',
      ...totals(300, 600, 2), points: [{ date: '2026-10-10', race_id: '2026-10-10', post_time: null, ...totals(300, 600, 2) }] },
    { model_id: 'model-B', display_name: 'モデルB', color: '#4ade80', model_sha256: 'b'.repeat(64), cutoff_date: '2026-09-27',
      ...totals(2000, 3000, 1), points: [{ date: '2026-11-01', race_id: '2026-11-01', post_time: null, ...totals(2000, 3000, 1) }] },
  ],
}
const months = (year: number) => ({
  schema: 'model-monthly-roi-v1', mode: 'paper', source: 'unseen-simulation', origin: 'all',
  date_from: null, date_to: null, year,
  models: data.models.map((model, index) => ({
    model_id: model.model_id, display_name: model.display_name, color: model.color,
    months: Array.from({ length: 12 }, (_, month) => ({ month: month + 1,
      ...(year === 2026 && month === 9 + index ? totals(model.total_cost, model.total_return, model.race_count) : totals()) })),
  })),
})
const scope = (origin: string | null) => origin === 'historical_validation'
  ? { origin, source: 'historical-validation-simulation', evaluation_scope: 'non-training-retrospective' }
  : origin === 'combined'
    ? { origin, source: 'combined-reference-simulation', evaluation_scope: 'non-training-combined' }
  : { origin: origin ?? 'all', source: 'unseen-simulation' }

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class {
    constructor(private callback: ResizeObserverCallback) {}
    observe(target: Element) { this.callback([{ target, contentRect: { width: 600, height: 240 } } as ResizeObserverEntry], this as unknown as ResizeObserver) }
    unobserve() {}
    disconnect() {}
  })
  fetchMock.mockReset()
  fetchMock.mockImplementation(async (url: string) => {
    const query = new URL(url, 'http://localhost').searchParams
    return Response.json(query.has('year') ? { ...months(Number(query.get('year'))), ...scope(query.get('origin')) } : {
      records: [], summary: { pending_races: 0 }, total_count: 3, model_performance: { ...data, ...scope(query.get('origin')) },
    })
  })
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals() })

it('accepts daily cumulative counts without inventing zero-percent pending performance', () => {
  expect(readModelPerformance(data, 'paper', '', '', 'all')).not.toBeNull()
  const pending = { ...data.models[0], ...totals(), points: [] }
  expect(readModelPerformance({ ...data, models: [pending] }, 'paper', '', '', 'all')).not.toBeNull()
  expect(readModelPerformance(data, 'paper', '', '', 'pre_race')).toBeNull()
  expect(readModelPerformance({ ...data, source: 'recorded' }, 'paper', '', '', 'all')).toBeNull()
})

it('keeps retrospective and truly unseen response scopes distinct', () => {
  const retrospective = { ...data, ...scope('historical_validation') }
  expect(readModelPerformance(retrospective, 'paper', '', '', 'historical_validation')).not.toBeNull()
  expect(readModelPerformance(retrospective, 'paper', '', '', 'all')).toBeNull()
  expect(readModelPerformance({ ...data, origin: 'historical_validation' }, 'paper', '', '', 'historical_validation')).toBeNull()
  const monthly = { ...months(2024), ...scope('historical_validation') }
  expect(readMonthlyPerformance(monthly, 2024, '', '', 'historical_validation')).not.toBeNull()
  expect(readMonthlyPerformance(monthly, 2024, '', '', 'all')).toBeNull()
})

it('defaults to combined evaluation and live records and scopes both graphs together', async () => {
  render(<DashboardPage />)
  await screen.findByRole('img', { name: 'モデル別月別回収率グラフ' })
  expect(screen.getByLabelText('予測の記録区分')).toHaveValue('combined')
  expect(fetchMock.mock.calls.filter(([url]) => String(url).startsWith('/api/performance?'))
    .every(([url]) => new URL(String(url), 'http://localhost').searchParams.get('origin') === 'combined')).toBe(true)
  fireEvent.change(screen.getByLabelText('予測の記録区分'), { target: { value: 'all' } })
  await screen.findByText(/学習・校正・評価期間外のみ/)
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes('origin=all'))).toBe(true)
})

it('shows evaluated KPI data instead of defaulting to an unsettled model and explains each cohort', async () => {
  const pending = { ...data.models[0], model_id: 'pending-model', display_name: '未精算モデル',
    ...totals(), points: [], status_counts: { settled: 0, pending: 1, payout_pending: 0, review: 0, skipped: 0, other: 0 } }
  const evaluated = { ...data.models[1],
    status_counts: { settled: 1, pending: 0, payout_pending: 2, review: 1, skipped: 0, other: 0 },
    cohorts: {
      evaluation: { ...totals(2000, 3000, 1), record_count: 2, pending_races: 1 },
      historical: { ...totals(), record_count: 0, pending_races: 0 },
      pre_race: { ...totals(), record_count: 2, pending_races: 2 },
    },
  }
  fetchMock.mockImplementation(async (url: string) => {
    const query = new URL(url, 'http://localhost').searchParams
    return Response.json(query.has('year') ? { ...months(Number(query.get('year'))), ...scope(query.get('origin')) } : {
      records: [], summary: {},
      training_evaluation_imports: [{ model_id: 'model-B', accepted_races: 1, excluded_races: 2 }],
      model_performance: { ...data, ...scope(query.get('origin')), models: [pending, evaluated, data.models[0]] },
    })
  })
  render(<DashboardPage />)
  await screen.findByRole('checkbox', { name: /未精算モデル/ })
  const kpis = within(screen.getByRole('region', { name: '確定分の成績' }))
  expect(kpis.getByText('150.0%')).toBeInTheDocument()
  expect(screen.getByLabelText('KPIモデル')).toHaveValue('model-B')
  const breakdown = within(screen.getByRole('region', { name: '成績の内訳' }))
  expect(breakdown.getByText('学習時TEST評価')).toBeInTheDocument()
  expect(breakdown.getByText('発走前に保存した運用予測')).toBeInTheDocument()
  expect(breakdown.getByText(/払戻待ち 2/)).toBeInTheDocument()
  expect(screen.getByText(/2レースは、オッズ・頭数/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('checkbox', { name: /モデルB/ }))
  expect(screen.getByLabelText('KPIモデル')).toHaveValue('model-A')
  expect(kpis.getByText('200.0%')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('KPIモデル'), { target: { value: 'pending-model' } })
  expect(kpis.getByText('未算出')).toBeInTheDocument()
  expect(breakdown.getByText(/結果待ち 1/)).toBeInTheDocument()
})

it('uses one explicit KPI model, shares visibility and keeps persisted colors across sorts', async () => {
  render(<DashboardPage />)
  await screen.findByRole('checkbox', { name: /モデルA/ })
  await screen.findByRole('img', { name: 'モデル別月別回収率グラフ' })
  const kpis = within(screen.getByRole('region', { name: '確定分の成績' }))
  expect(kpis.getByText('¥300')).toBeInTheDocument()
  expect(kpis.getByText('200.0%')).toBeInTheDocument()
  expect(kpis.queryByText('¥2,300')).not.toBeInTheDocument()
  const color = () => screen.getByRole('checkbox', { name: /モデルA/ }).closest('label')?.querySelector('span[aria-hidden]')?.getAttribute('style')
  const before = color()
  fireEvent.change(screen.getByLabelText('モデルの並び順'), { target: { value: 'profit' } })
  expect(screen.getAllByRole('checkbox')[0]).toHaveAccessibleName(/モデルB/)
  expect(color()).toBe(before)
  expect(kpis.getByText('200.0%')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('KPIモデル'), { target: { value: 'model-B' } })
  expect(kpis.getByText('150.0%')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('checkbox', { name: /モデルB/ }))
  expect(kpis.getByText('200.0%')).toBeInTheDocument()
  expect(screen.getByText('表示中：1モデル')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '全解除' }))
  expect(kpis.getByText('未算出')).toBeInTheDocument()
  expect(screen.queryByRole('img', { name: 'モデル別累積回収率グラフ' })).not.toBeInTheDocument()
  expect(screen.queryByRole('img', { name: 'モデル別月別回収率グラフ' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '全モデル表示' }))
  expect(screen.getByText('表示中：2モデル')).toBeInTheDocument()
})

it('fetches the selected year only and paginates missing months without calling them zero', async () => {
  render(<DashboardPage />)
  await screen.findByRole('img', { name: 'モデル別月別回収率グラフ' })
  const region = screen.getByRole('region', { name: '月別回収率' })
  fireEvent.click(within(region).getByRole('button', { name: 'データ一覧' }))
  expect(within(region).queryByText('10月')).not.toBeInTheDocument()
  expect(within(region).getAllByRole('row')).toHaveLength(13)
  expect(within(region).queryByText('0.0%')).not.toBeInTheDocument()
  fireEvent.click(within(region).getByRole('button', { name: '後半（7〜12月）' }))
  const row = within(region).getAllByRole('row').find(element => element.textContent?.includes('10月モデルA'))
  expect(row).toBeDefined()
  expect(within(row!).getByText('¥300')).toBeInTheDocument()
  expect(within(row!).getByText('¥600')).toBeInTheDocument()
  fireEvent.change(within(region).getByLabelText('表示年'), { target: { value: '2025' } })
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes('year=2025'))).toBe(true))
  await waitFor(() => expect(within(region).queryByText('200.0%')).not.toBeInTheDocument())
  expect(within(region).queryByText('0.0%')).not.toBeInTheDocument()
  expect(fetchMock.mock.calls.filter(([url]) => String(url).startsWith('/api/performance?'))
    .every(([url]) => String(url).includes('source=simulation'))).toBe(true)
})

it('keeps the replacement KPI model after hiding the original and sorting three models', async () => {
  const third = { ...data.models[1], model_id: 'model-C', display_name: 'モデルC', color: '#a78bfa',
    ...totals(10000, 12500, 1),
    points: [{ date: '2026-11-02', race_id: '2026-11-02', post_time: null, ...totals(10000, 12500, 1) }] }
  fetchMock.mockImplementation(async (url: string) => {
    const query = new URL(url, 'http://localhost').searchParams
    const year = Number(query.get('year'))
    return Response.json(query.has('year') ? {
      ...months(year), ...scope(query.get('origin')), models: [...months(year).models, { model_id: third.model_id, display_name: third.display_name,
        color: third.color, months: Array.from({ length: 12 }, (_, index) => ({ month: index + 1, ...totals() })) }],
    } : { records: [], summary: { pending_races: 0 }, model_performance: { ...data, ...scope(query.get('origin')), models: [...data.models, third] } })
  })
  render(<DashboardPage />)
  await screen.findByRole('checkbox', { name: /モデルC/ })
  fireEvent.click(screen.getByRole('checkbox', { name: /モデルA/ }))
  const kpis = within(screen.getByRole('region', { name: '確定分の成績' }))
  expect(kpis.getByText('150.0%')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('モデルの並び順'), { target: { value: 'profit' } })
  expect(kpis.getByText('150.0%')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('checkbox', { name: /モデルA/ }))
  expect(kpis.getByText('150.0%')).toBeInTheDocument()
})

it('refreshes future prediction records even when all previous races are settled', async () => {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
  render(<DashboardPage />)
  await screen.findByRole('img', { name: 'モデル別月別回収率グラフ' })
  const overviewCalls = () => fetchMock.mock.calls.filter(([url]) => !new URL(String(url), 'http://localhost').searchParams.has('year')).length
  const before = overviewCalls()
  await act(async () => { vi.advanceTimersByTime(30_000) })
  expect(overviewCalls()).toBe(before + 1)
})
