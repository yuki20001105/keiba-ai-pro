import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { fetchMock } = vi.hoisted(() => ({ fetchMock: vi.fn() }))
vi.mock('@/lib/auth-fetch', () => ({ authFetch: fetchMock }))
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ userId: 'owner-a' }) }))
vi.mock('@/components/Toast', () => ({ Toast: () => null }))
vi.mock('@/components/Logo', () => ({ Logo: () => null }))

import DataCollectionPage from '@/app/data-collection/page'
import { BATCH_SCRAPE_STORAGE_KEY_PREFIX } from '@/hooks/useBatchScrape'
import { UNCERTAINTY_STORAGE_KEY } from '@/lib/scrape-uncertainty-approval'

const JOB = '11111111-1111-4111-8111-111111111111'
const OPERATION = '22222222-2222-4222-8222-222222222222'
const KEY = `${BATCH_SCRAPE_STORAGE_KEY_PREFIX}:owner-a`
const json = (value: unknown, status = 200) => Response.json(value, { status })
function stored() {
  return { version: 1, ownerUserId: 'owner-a', jobId: JOB, operationId: OPERATION,
    startDate: '20160101', endDate: '20260930', forceRescrape: false,
    createdAt: '2026-09-13T12:00:00Z', origin: 'submitted' }
}
function parent(jobId = JOB, status = 'running') {
  return {
    job_id: jobId, status, created_at: '2026-09-13T12:00:00Z',
    request_payload: { start_date: '20160101', end_date: '20260930', force_rescrape: false, server_batch: true },
    progress: { done: 1000, total: 129000, current_month: '2016-02', saved_races: 62, saved_horses: 603, message: '取得中' },
  }
}

describe('data collection server batch integration', () => {
  beforeEach(() => {
    localStorage.clear()
    fetchMock.mockReset()
    vi.stubEnv('NEXT_PUBLIC_E2E_BATCH_POLL_INTERVAL_MS', '20')
    vi.spyOn(window, 'confirm').mockReturnValue(true)
  })
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllEnvs() })

  function serveJob(getJob: () => ReturnType<typeof parent> | null, handleMutation?: (url: string, init: RequestInit) => Response) {
    fetchMock.mockImplementation(async (url: string, init: RequestInit = {}) => {
      if (init.method === 'POST' && handleMutation) return handleMutation(url, init)
      if (url === '/api/scrape/health') return json({ status: 'healthy', metrics: { active_jobs: 0 }, runtime: { status: 'healthy' } })
      if (url.startsWith('/api/data-stats')) return json({ total_races: 50000, total_horses: 500000 })
      if (url.startsWith('/api/scrape/history')) {
        const job = getJob()
        return json({ jobs: job ? [job] : [], count: job ? 1 : 0 })
      }
      if (url.startsWith('/api/scrape/status/')) return json(getJob() || { job_id: url.split('/').pop(), status: 'not_found' })
      throw new Error('unexpected request: ' + url)
    })
  }

  it('submits all 129 months once and keeps start disabled while the server parent runs', async () => {
    let job: ReturnType<typeof parent> | null = null
    serveJob(() => job, (url, init) => {
      expect(url).toBe('/api/scrape')
      const body = JSON.parse(String(init.body))
      expect(body).toMatchObject({ server_batch: true, start_date: '20160101', end_date: '20260930' })
      expect(JSON.parse(localStorage.getItem(KEY)!).jobId).toBe(body.job_id)
      job = parent(body.job_id)
      return json(job)
    })
    const view = render(<DataCollectionPage />)
    await waitFor(() => expect(screen.getByTestId('execute-button')).toBeEnabled())
    fireEvent.change(screen.getByTestId('start-period-input'), { target: { value: '2016-01' } })
    fireEvent.change(screen.getByTestId('end-period-input'), { target: { value: '2026-09' } })
    fireEvent.click(screen.getByTestId('execute-button'))
    await waitFor(() => expect(screen.getByTestId('active-scrape-job')).toHaveTextContent('サーバーで取得中'))
    expect(screen.getByTestId('execute-button')).toBeDisabled()
    expect(screen.getByTestId('dry-run-button')).toBeDisabled()
    expect(screen.getByTestId('batch-status-panel')).toHaveTextContent('2016-02')
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
    view.unmount()
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) })
    expect(localStorage.getItem(KEY)).not.toBeNull()
    expect(localStorage.getItem(UNCERTAINTY_STORAGE_KEY)).toBeNull()
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
  })

  it('reopens the saved parent, shows progress and stops that exact parent without restarting', async () => {
    localStorage.setItem(KEY, JSON.stringify(stored()))
    let job = parent()
    serveJob(() => job, (url, init) => {
      expect(url).toBe(`/api/scrape/cancel/${JOB}`)
      expect(init.method).toBe('POST')
      job = parent(JOB, 'cancelled')
      return json(job)
    })
    render(<DataCollectionPage />)
    await waitFor(() => expect(screen.getByTestId('batch-status-panel')).toHaveTextContent('2016-02'))
    expect(screen.getByTestId('active-scrape-job')).toHaveTextContent('2016/01/01～2026/09/30')
    fireEvent.click(screen.getByTestId('cancel-active-job-button'))
    await waitFor(() => expect(screen.getByTestId('batch-status-panel')).toHaveTextContent('取得を停止しました'))
    expect(fetchMock.mock.calls.filter(([url, init]) => url === '/api/scrape' && init?.method === 'POST')).toHaveLength(0)
    expect(fetchMock.mock.calls.filter(([url]) => url === `/api/scrape/cancel/${JOB}`)).toHaveLength(1)
    expect(localStorage.getItem(KEY)).toBeNull()
  })

  it('restores a parent from owner-filtered history when this browser has no saved ID', async () => {
    serveJob(() => parent())
    const view = render(<DataCollectionPage />)
    await waitFor(() => expect(screen.getByTestId('batch-status-panel')).toHaveTextContent('2016-02'))
    expect(screen.getByTestId('cancel-active-job-button')).toBeEnabled()
    expect(JSON.parse(localStorage.getItem(KEY)!)).toMatchObject({ jobId: JOB, ownerUserId: 'owner-a', origin: 'history' })
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
    view.unmount()
    expect(localStorage.getItem(UNCERTAINTY_STORAGE_KEY)).toBeNull()
  })

  it('keeps a legacy lock if the rechecked terminal response belongs to another job', async () => {
    const lock = {
      version: 1, failureKind: 'monitoring', jobId: JOB, occurredAt: '2026-09-13T12:48:11.000Z',
      request: { startPeriod: '2016-01', endPeriod: '2026-09', forceRescrape: false },
    }
    const raw = JSON.stringify(lock)
    localStorage.setItem(UNCERTAINTY_STORAGE_KEY, raw)
    serveJob(() => null)
    const original = fetchMock.getMockImplementation()!
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === `/api/scrape/status/${JOB}`) {
        return json({ job_id: OPERATION, status: 'completed', result: { races_collected: 5 } })
      }
      return original(url, init)
    })
    render(<DataCollectionPage />)
    await waitFor(() => expect(screen.getByTestId('reconcile-status-button')).toBeEnabled())
    fireEvent.click(screen.getByTestId('reconcile-status-button'))
    await waitFor(() => expect(screen.getByTestId('uncertainty-panel')).toHaveTextContent('状態応答形式が不正'))
    expect(localStorage.getItem(UNCERTAINTY_STORAGE_KEY)).toBe(raw)
    expect(screen.getByTestId('execute-button')).toBeDisabled()
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
  })
})
