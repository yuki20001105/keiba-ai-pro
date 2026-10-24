import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { JobPollerOptions } from '@/hooks/useJobPoller'

const { fetchMock, poller } = vi.hoisted(() => ({
  fetchMock: vi.fn(),
  poller: { options: null as JobPollerOptions | null },
}))
vi.mock('@/lib/auth-fetch', () => ({ authFetch: fetchMock }))
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ isPremium: false }) }))
vi.mock('@/components/Logo', () => ({ Logo: () => null }))
vi.mock('@/components/Toast', () => ({ Toast: () => null }))
vi.mock('@/components/RaceExplanationPanel', () => ({ RaceExplanationPanel: () => null }))
vi.mock('@/hooks/useJobPoller', () => ({
  useJobPoller: (options: JobPollerOptions) => { poller.options = options; return {} },
}))

import PredictBatchPage from '@/app/predict-batch/page'
import { scrapeCompletionOutcome } from '@/lib/scrape-completion-outcome'

const JOB = '11111111-1111-4111-8111-111111111111'
const RACE = {
  race_id: '202606040101', race_name: 'テストレース', venue: '中山', venue_code: '06',
  race_no: 1, distance: 1200, track_type: 'ダート', num_horses: 12,
}
let raceResponse: unknown
let pendingRaceResponse: Promise<Response> | null

beforeEach(() => {
  localStorage.clear()
  fetchMock.mockReset()
  poller.options = null
  raceResponse = { races: [] }
  pendingRaceResponse = null
  fetchMock.mockImplementation(async (url: string) => {
    if (url === '/api/models?ultimate=true') return Response.json({ models: [] })
    if (url.startsWith('/api/races/by-date')) return pendingRaceResponse ?? Response.json(raceResponse)
    if (url === '/api/scrape') return Response.json({ job_id: JOB, status: 'queued' })
    throw new Error(`Unexpected request: ${url}`)
  })
})
afterEach(cleanup)

async function startFromMissing() {
  render(<PredictBatchPage />)
  fireEvent.click(screen.getByRole('button', { name: 'レース一覧を取得' }))
  fireEvent.click(await screen.findByRole('button', { name: 'この日付をスクレイプして取得' }))
  await waitFor(() => expect(poller.options?.jobId).toBe(JOB))
}

async function complete(result: unknown) {
  await act(async () => { await poller.options?.onCompleted?.({ job_id: JOB, status: 'completed', result }) })
}

describe('prediction acquisition outcomes', () => {
  it('keeps failed-completed reason and job ID through the automatic and manual DB reload', async () => {
    await startFromMissing()
    await complete({ success: false, failure_reason: '取得元から本日の一覧を確認できません。', failure_code: 'race_list_unavailable' })
    expect(screen.getByTestId('scrape-completion-outcome')).toHaveTextContent('取得元から本日の一覧を確認できません。')
    expect(screen.getByRole('button', { name: '再試行' })).toBeEnabled()
    expect(screen.queryByText('この日のデータはまだ取得されていません。')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'レース一覧を取得' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '再試行' })).toBeEnabled())
    expect(screen.getByTestId('scrape-completion-outcome')).toHaveTextContent('取得元から本日の一覧を確認できません。')
    expect(poller.options?.jobId).toBe(JOB)
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/scrape')).toHaveLength(1)
  })

  it('shows confirmed success with the loaded races and retains the job ID', async () => {
    await startFromMissing()
    raceResponse = { races: [RACE] }
    await complete({ success: true, races_collected: 1, fetch_summary: { quality_audit: { quality_complete: true } } })
    expect(screen.getByTestId('scrape-completion-outcome')).toHaveTextContent('レース情報を取得しました。')
    expect(screen.getByText('テストレース')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '再試行' })).not.toBeInTheDocument()
    expect(poller.options?.jobId).toBe(JOB)
  })

  it('keeps partially acquired races selectable alongside the quality warning', async () => {
    await startFromMissing()
    raceResponse = { races: [RACE] }
    await complete({ success: false, message: '収集は完了しましたが、1日を修復キューへ登録しました', fetch_summary: { quality_audit: { quality_complete: false } } })
    expect(screen.getByTestId('scrape-completion-outcome')).toHaveTextContent('取得できなかったデータがあります。')
    expect(screen.getByText('取得済みのレースは下の一覧から利用できます。')).toBeInTheDocument()
    expect(screen.getByRole('checkbox')).toBeEnabled()
    fireEvent.click(screen.getByRole('checkbox'))
    expect(screen.getByRole('checkbox')).toBeChecked()
    expect(screen.queryByText(/修復キュー/)).not.toBeInTheDocument()
  })

  it('does not claim success if the completed job still leaves no usable races', async () => {
    await startFromMissing()
    await complete({ success: true, races_collected: 0 })
    expect(screen.getByTestId('scrape-completion-outcome')).toHaveTextContent('この日のレースを取得できませんでした。')
    expect(screen.getByRole('button', { name: '再試行' })).toBeEnabled()
  })

  it('clears the previous outcome only when a new acquisition starts', async () => {
    await startFromMissing()
    await complete({ success: false, failure_reason: '取得元の応答待ちです。' })
    fireEvent.click(screen.getByRole('button', { name: '再試行' }))
    expect(screen.queryByTestId('scrape-completion-outcome')).not.toBeInTheDocument()
    await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => url === '/api/scrape')).toHaveLength(2))
    expect(screen.getByRole('button', { name: 'レース一覧を取得' })).toBeDisabled()
  })

  it('clears the outcome on date change and ignores an old completion reload still in flight', async () => {
    await startFromMissing()
    let resolveRead!: (response: Response) => void
    pendingRaceResponse = new Promise(resolve => { resolveRead = resolve })
    let completion: unknown
    act(() => {
      completion = poller.options?.onCompleted?.({ job_id: JOB, status: 'completed', result: { success: false, failure_reason: '古い日付の取得失敗' } })
    })
    expect(screen.getByTestId('scrape-completion-outcome')).toHaveTextContent('古い日付の取得失敗')
    fireEvent.change(screen.getByLabelText('日付'), { target: { value: '2026-09-20' } })
    expect(screen.queryByTestId('scrape-completion-outcome')).not.toBeInTheDocument()
    expect(poller.options?.jobId).toBeNull()
    await act(async () => { resolveRead(Response.json({ races: [RACE] })); await completion })
    expect(screen.queryByText('テストレース')).not.toBeInTheDocument()
    expect(screen.queryByTestId('scrape-completion-outcome')).not.toBeInTheDocument()
    expect(screen.queryByText('この日のデータはまだ取得されていません。')).not.toBeInTheDocument()
  })

  it('shows DB response errors as read failures rather than data missing', async () => {
    raceResponse = { races: [], error: 'database is locked' }
    render(<PredictBatchPage />)
    fireEvent.click(screen.getByRole('button', { name: 'レース一覧を取得' }))
    expect(await screen.findByText(/保存データを読み込めませんでした/)).toBeInTheDocument()
    expect(screen.queryByText('この日のデータはまだ取得されていません。')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'この日付をスクレイプして取得' })).not.toBeInTheDocument()
  })

  it('does not display successful acquisition when the completion DB reload fails', async () => {
    await startFromMissing()
    raceResponse = { races: [], error: 'database is locked' }
    await complete({ success: true, races_collected: 1 })
    expect(screen.getByText(/保存データを読み込めませんでした/)).toBeInTheDocument()
    expect(screen.queryByText('レース情報を取得しました。')).not.toBeInTheDocument()
    expect(poller.options?.jobId).toBe(JOB)
  })
})

describe('legacy completion payloads', () => {
  it('honors a failed quality audit even when success is absent or incorrectly true', () => {
    for (const success of [undefined, true]) {
      expect(scrapeCompletionOutcome({ result: { success, fetch_summary: { quality_audit: { quality_complete: false } } } }, 1).kind).toBe('warning')
    }
  })
  it('explains legacy recent-day empty results in Japanese', () => {
    const outcome = scrapeCompletionOutcome({ result: { success: false, errors: ['20260919: recent_race_list_empty_recheck'] } }, 0)
    expect(outcome.message).toContain('この日のレース情報はまだ確認できません')
    expect(outcome.message).not.toContain('recent_race_list')
  })
  it('distinguishes a verified historical no-race day from an unsuccessful empty result', () => {
    expect(scrapeCompletionOutcome({ result: { success: true, verified_no_race_dates: 1 } }, 0)).toEqual({ kind: 'success', message: 'この日の開催はありません（確認済み）。' })
    expect(scrapeCompletionOutcome({ result: { success: false, verified_no_race_dates: 1 } }, 0).kind).toBe('warning')
  })
})
