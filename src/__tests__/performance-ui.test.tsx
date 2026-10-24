import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Recommendation } from '@/lib/race-analysis-types'
import { BETTING_POLICY_VERSION } from '@/lib/betting-policy'
import { savePerformanceRecord, performanceDateLabel, performanceStatusLabel } from '@/lib/performance-records'
import { BettingRecommendationPanel } from '@/components/BettingRecommendationPanel'
import { verifiedPredictionInput } from './helpers/verified-prediction-input'

const { fetchMock } = vi.hoisted(() => ({ fetchMock: vi.fn() }))
vi.mock('@/lib/auth-fetch', () => ({ authFetch: fetchMock }))
vi.mock('@/components/Logo', () => ({ Logo: () => null }))
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
beforeEach(() => { fetchMock.mockReset() })
afterEach(cleanup)

describe('performance recording', () => {
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
  it('keeps pending investments out of settled indicators and uses canonical race metadata', async () => {
    fetchMock.mockResolvedValue(Response.json({ records: [record, { ...record, id: 'pending', status: 'pending', total_cost: 700, actual_return: null, profit: null }], summary, total_count: 2 }))
    render(<DashboardPage />)
    await screen.findAllByText('テストレース')
    const cards = within(screen.getByRole('region', { name: '確定分の成績' }))
    expect(cards.getByText('¥300')).toBeInTheDocument()
    expect(cards.getByText('¥600')).toBeInTheDocument()
    expect(cards.getByText('+¥300')).toBeInTheDocument()
    expect(cards.getByText('200.0%')).toBeInTheDocument()
    expect(cards.getByText('未確定 1レース（¥700・内訳未取得）')).toBeInTheDocument()
    expect(screen.getAllByText('2026/10/10 · 東京')).toHaveLength(2)
    expect(screen.queryByText(/2026\/06\/04/)).not.toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'AI参考' })).toHaveAttribute('aria-selected', 'true')
  })
  it('loads actual records separately and treats settled zero returns as losses across refreshes', async () => {
    fetchMock.mockImplementation(async (url: string) => Response.json({ records: [{ ...record, mode: url.includes('mode=actual') ? 'actual' : 'paper', actual_return: 0, profit: -300 }], summary: { ...summary, settled_return: 0, profit: -300, roi: 0, pending_races: 0 }, total_count: 1 }))
    render(<DashboardPage />)
    await screen.findByText('外れ')
    fireEvent.click(screen.getByRole('tab', { name: '購入済み' }))
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.includes('mode=actual'))).toBe(true))
    await screen.findByText('外れ')
    expect(within(screen.getByRole('region', { name: '確定分の成績' })).getByText('−¥300')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '更新' }))
    await screen.findByText('外れ')
  })
  it('preserves unknown outcomes and unknown dates instead of deriving them from race IDs', () => {
    expect(performanceDateLabel(undefined)).toBe('日付未確認')
    expect(performanceDateLabel('2026-10-10')).toBe('2026/10/10')
    expect(performanceStatusLabel({ status: 'pending', actual_return: 0 })).toBe('結果待ち')
    expect(performanceStatusLabel({ status: 'settled', actual_return: 0 })).toBe('外れ')
    expect(performanceStatusLabel({ status: 'payout_pending', actual_return: null })).toBe('払戻待ち')
  })
})
