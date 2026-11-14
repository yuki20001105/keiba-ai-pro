import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ResearchBettingPerformance } from '@/components/ResearchBettingPerformance'
import { readResearchPerformance } from '@/lib/research-performance'

const { fetchMock } = vi.hoisted(() => ({ fetchMock: vi.fn() }))
vi.mock('@/lib/auth-fetch', () => ({ authFetch: fetchMock }))
vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  LineChart: ({ data }: { data: unknown }) => <div data-testid="research-chart">{JSON.stringify(data)}</div>,
  Line: () => null, XAxis: () => null, YAxis: () => null, CartesianGrid: () => null,
  Tooltip: () => null, Legend: () => null, ReferenceLine: () => null,
}))
const stats = { opportunities: 3, bet_races: 1, hit_races: 1, investment: 1500, payout: 3000, profit: 1500, roi: 200 }
const baseline = { ...stats, payout: 1500, profit: 0, roi: 100 }
function report(policy = 'fixed_two__all', scope = 'retrospective') {
  const date = scope === 'development' ? '2020-01-05' : '2026-01-04'
  return {
    schema: 'betting-research-performance-v1', study_id: 'search101-20261107-v1',
    feature_count: 101, reference_only: true, purchase_eligible: false, recommended_method: null,
    historical_support: false, scope, policy, date_from: null, date_to: null, excluded_races: 2,
    periods: { development: ['2020-01-05', '2022-05-21'], retrospective: ['2023-01-05', '2026-10-17'] },
    policies: ['single_ai', 'single_favorite', 'wide_ai', 'wide_axis_h3', 'trio_ai', 'fixed_two', 'trifecta_ai'].flatMap(name =>
      ['all', 'market_top3'].map(gate => ({ id: `${name}__${gate}`, label: `${name} ${gate}`, development: stats, retrospective: stats, baseline }))),
    points: [{ date, ...stats, baseline_roi: 100 }],
    monthly: [{ month: date.slice(0, 7), ...stats, baseline_roi: 100 }],
  }
}
beforeEach(() => {
  fetchMock.mockReset()
  fetchMock.mockImplementation(async (url: string) => {
    const query = new URL(url, 'http://localhost').searchParams
    return Response.json({ ...report(query.get('policy') ?? undefined, query.get('scope') ?? undefined),
      date_from: query.get('date_from') ? '2026-01-01' : null,
      date_to: query.get('date_to') ? '2026-01-31' : null })
  })
})
afterEach(cleanup)

describe('read-only research performance', () => {
  it('shows policy rejection, matched-model curve and monthly virtual totals without purchases', async () => {
    render(<ResearchBettingPerformance dateFrom="" dateTo="" />)
    await screen.findByText('採用条件を満たす買い目なし：運用への切替は見送り')
    const kpis = screen.getByRole('region', { name: '研究の仮想成績' })
    expect(within(kpis).getByText('200.0%')).toBeInTheDocument()
    expect(within(kpis).getByText('¥1,500')).toBeInTheDocument()
    expect(screen.getByTestId('research-chart')).toHaveTextContent('"baseline_roi":100')
    expect(screen.getByRole('table', { name: '研究の月別成績' })).toHaveTextContent('2026-01')
    expect(screen.getByRole('table', { name: '研究買い目14方式の比較' })).toBeInTheDocument()
    expect(fetchMock.mock.calls.every(([url, init]) => String(url).startsWith('/api/performance/research?') && !init.method)).toBe(true)
  })
  it('applies date/scope/policy controls to charts and totals, not to the frozen selection decision', async () => {
    const view = render(<ResearchBettingPerformance dateFrom="" dateTo="" />)
    await screen.findByTestId('research-chart')
    fireEvent.change(screen.getByLabelText('研究の買い目'), { target: { value: 'single_ai__all' } })
    await waitFor(() => expect(fetchMock.mock.lastCall?.[0]).toContain('policy=single_ai__all'))
    await screen.findByTestId('research-chart')
    view.rerender(<ResearchBettingPerformance dateFrom="2026-01-01" dateTo="2026-01-31" />)
    await waitFor(() => expect(fetchMock.mock.lastCall?.[0]).toContain('date_from=20260101&date_to=20260131'))
    await screen.findByTestId('research-chart')
    expect(screen.getByText('採用条件を満たす買い目なし：運用への切替は見送り')).toBeInTheDocument()
    view.rerender(<ResearchBettingPerformance dateFrom="" dateTo="" />)
    await screen.findByTestId('research-chart')
    fireEvent.change(screen.getByLabelText('研究の評価区分'), { target: { value: 'development' } })
    await waitFor(() => expect(fetchMock.mock.lastCall?.[0]).toContain('scope=development'))
    await waitFor(() => expect(screen.getByTestId('research-chart')).toHaveTextContent('2020-01-05'))
  })
  it('does not reuse earlier charts after a failed or unsafe study response', async () => {
    const view = render(<ResearchBettingPerformance dateFrom="" dateTo="" />)
    await screen.findByTestId('research-chart')
    fetchMock.mockResolvedValue(Response.json({ ...report(), purchase_eligible: true }))
    view.rerender(<ResearchBettingPerformance dateFrom="2026-01-01" dateTo="" />)
    await screen.findByText(/検証済みの研究成績を確認できませんでした。/)
    expect(screen.queryByTestId('research-chart')).not.toBeInTheDocument()
    expect(readResearchPerformance({ ...report(), purchase_eligible: true }, 'fixed_two__all', 'retrospective', '', '')).toBeNull()
    expect(readResearchPerformance({ ...report(), scope: 'development' }, 'fixed_two__all', 'retrospective', '', '')).toBeNull()
    expect(readResearchPerformance({ ...report(), points: [] }, 'fixed_two__all', 'retrospective', '', '')).toBeNull()
  })
})
