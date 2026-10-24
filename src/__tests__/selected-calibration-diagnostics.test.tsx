import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SelectedCalibrationDiagnostics } from '@/components/SelectedCalibrationDiagnostics'
import { performanceRoiLabel, readSelectionDiagnostics } from '@/lib/performance-records'
import { emptyCalibrationMetrics, selectedCalibrationFixture } from './helpers/selected-calibration'

const { fetchMock } = vi.hoisted(() => ({ fetchMock: vi.fn() }))
vi.mock('@/lib/auth-fetch', () => ({ authFetch: fetchMock }))
vi.mock('@/components/Logo', () => ({ Logo: () => null }))
import DashboardPage from '@/app/dashboard/page'

afterEach(cleanup)
beforeEach(() => { fetchMock.mockReset() })

describe('observational selected-ticket display contract', () => {
  it('binds mode/date scope to the complete server-filtered cohort, not displayed rows', () => {
    const value = selectedCalibrationFixture('actual')
    value.scope.date_from = '2026-10-10'
    expect(readSelectionDiagnostics(value, 'actual', '2026-10-10')).toEqual(value)
    expect(readSelectionDiagnostics(value, 'actual', '20261010')).toEqual(value)
    expect(readSelectionDiagnostics(value, 'paper', '2026-10-10')).toBeNull()
    expect(readSelectionDiagnostics(value, 'actual')).toBeNull()
    value.scope.includes_full_filtered_set = false as true
    expect(readSelectionDiagnostics(value, 'actual', '2026-10-10')).toBeNull()
  })

  it('does not treat an absent backend field, malformed numbers or permission flags as observations', () => {
    expect(readSelectionDiagnostics(undefined, 'paper')).toBeNull()
    const value = selectedCalibrationFixture()
    expect(readSelectionDiagnostics({ ...value, purchase_authority: true }, 'paper')).toBeNull()
    value.groups[0].mean_probability = NaN
    expect(readSelectionDiagnostics(value, 'paper')).toBeNull()
  })

  it('rejects changed bands and duplicate model/policy/bet-type groups rather than mixing them', () => {
    const value = selectedCalibrationFixture()
    value.groups.push({ ...value.groups[0] })
    expect(readSelectionDiagnostics(value, 'paper')).toBeNull()
    value.groups.pop()
    value.groups[0].odds_bands[0].upper = 5
    expect(readSelectionDiagnostics(value, 'paper')).toBeNull()
  })

  it('keeps unknown ROI distinct from a genuinely settled 0.0% ROI', () => {
    expect(performanceRoiLabel(null)).toBe('未算出')
    expect(performanceRoiLabel(undefined)).toBe('未算出')
    expect(performanceRoiLabel(NaN)).toBe('未算出')
    expect(performanceRoiLabel(0)).toBe('0.0%')
  })

  it('displays separate groups and unestimated bands without granting validation or purchase authority', () => {
    const value = selectedCalibrationFixture()
    value.groups.push({ ...value.groups[0], model_id: 'frozen-model-B', policy_version: 'other-fixed-rule', bet_type: 'ワイド' })
    render(<SelectedCalibrationDiagnostics diagnostics={value} />)
    fireEvent.click(screen.getByText('モデル・選定ルール・オッズ帯を見る'))
    expect(screen.getByText('モデル: frozen-model-A')).toBeInTheDocument()
    expect(screen.getByText('モデル: frozen-model-B')).toBeInTheDocument()
    expect(screen.getAllByText('予測的中率').length).toBeGreaterThan(1)
    expect(screen.getAllByText('+25.0pt').length).toBeGreaterThan(0)
    expect(screen.getAllByText('30以上')).toHaveLength(2)
    expect(screen.getAllByText('未算出').length).toBeGreaterThan(0)
    expect(screen.getByText(/未知期間の検証や購入の許可ではありません/)).toBeInTheDocument()
    expect(screen.getByText(/複数比較の補正はなく/)).toBeInTheDocument()
    expect(screen.queryByText('検証済み')).not.toBeInTheDocument()
    expect(screen.queryByText(/購入推奨|回収率118/)).not.toBeInTheDocument()
  })

  it('preserves one-date point estimates while explicitly withholding confidence intervals', () => {
    const value = selectedCalibrationFixture()
    Object.assign(value.groups[0], { date_count: 1, interval_status: 'insufficient_distinct_dates',
      mean_probability_ci95: null, hit_rate_ci95: null, bias_ci95: null, bootstrap_valid_samples: 0 })
    render(<SelectedCalibrationDiagnostics diagnostics={value} />)
    expect(screen.getByText('観測日が少ないため区間未算出')).toBeInTheDocument()
    expect(screen.getAllByText('50.0%').length).toBeGreaterThan(0)
    expect(screen.queryByText('合格')).not.toBeInTheDocument()
  })

  it('does not fill absent observations with zero predicted or hit rates', () => {
    const value = selectedCalibrationFixture()
    value.groups[0] = { ...value.groups[0], ...emptyCalibrationMetrics() }
    render(<SelectedCalibrationDiagnostics diagnostics={value} />)
    expect(screen.queryByText('0.0%')).not.toBeInTheDocument()
    expect(screen.getAllByText('未算出').length).toBeGreaterThan(0)
  })
})

describe('performance diagnostics loading and selection scope', () => {
  const summary = { settled_cost: 100, settled_return: 0, profit: -100, roi: 0, settled_races: 1,
    hit_races: 0, pending_races: 4, pending_cost: 400, skipped_races: 3 }
  const response = (diagnostics = selectedCalibrationFixture(), overrideSummary = summary) => Response.json({
    records: [], total_count: diagnostics.scope.record_count, summary: overrideSummary, selection_diagnostics: diagnostics,
  })

  it('shows old-backend diagnostics as unreflected, never a zero-sample success', async () => {
    fetchMock.mockResolvedValue(Response.json({ records: [], summary: { ...summary, roi: null } }))
    render(<DashboardPage />)
    await screen.findByText('集計機能未反映／データ未取得')
    expect(screen.getByText('未算出')).toBeInTheDocument()
    expect(screen.queryByText('対象 0買い目 · 0レース · 0日')).not.toBeInTheDocument()
    expect(screen.getByText('未確定 4レース（¥400・内訳未取得）')).toBeInTheDocument()
  })

  it('separates pending, missing payout, review, skip and zero return from eligible diagnostics', async () => {
    fetchMock.mockResolvedValue(response())
    render(<DashboardPage />)
    await screen.findByText('対象 4買い目 · 3レース · 3日')
    const cards = within(screen.getByRole('region', { name: '確定分の成績' }))
    expect(cards.getByText('0.0%')).toBeInTheDocument()
    expect(cards.getByText('結果待ち 2レース')).toBeInTheDocument()
    expect(cards.getByText('払戻待ち 1レース')).toBeInTheDocument()
    expect(cards.getByText('要確認 1レース')).toBeInTheDocument()
    expect(cards.getByText('見送り 3レース')).toBeInTheDocument()
    expect(screen.getByText('診断対象外 7記録 / 3買い目')).toBeInTheDocument()
  })

  it('does not let late paper responses replace actual diagnostics or records', async () => {
    let resolvePaper!: (value: Response) => void
    fetchMock.mockImplementation((url: string) => url.includes('mode=actual') ? Promise.resolve(response(selectedCalibrationFixture('actual')))
      : new Promise<Response>(resolve => { resolvePaper = resolve }))
    render(<DashboardPage />)
    fireEvent.click(screen.getByRole('tab', { name: '購入済み' }))
    await screen.findByText(/購入済みの記録を選定時の確率/)
    resolvePaper(response())
    await waitFor(() => expect(screen.queryByText(/AI参考の記録を選定時の確率/)).not.toBeInTheDocument())
    expect(screen.getByText(/購入済みの記録を選定時の確率/)).toBeInTheDocument()
  })

  it('does not show an out-of-filter cohort returned by the backend', async () => {
    fetchMock.mockResolvedValue(response())
    render(<DashboardPage />)
    await screen.findByText('対象 4買い目 · 3レース · 3日')
    fireEvent.change(screen.getByLabelText('開始日'), { target: { value: '2026-10-10' } })
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.includes('date_from=20261010'))).toBe(true))
    await screen.findByText('集計機能未反映／データ未取得')
    expect(screen.queryByText('対象 4買い目 · 3レース · 3日')).not.toBeInTheDocument()
  })
})
