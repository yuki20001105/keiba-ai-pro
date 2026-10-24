import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
const { fetchMock } = vi.hoisted(() => ({ fetchMock: vi.fn() }))
vi.mock('@/lib/auth-fetch', () => ({ authFetch: fetchMock }))
import { BATCH_SCRAPE_STORAGE_KEY_PREFIX, useBatchScrape } from '@/hooks/useBatchScrape'
import type { ScrapeRaceScope } from '@/lib/scrape-race-scope'

const OWNER = 'owner-a'
const KEY = `${BATCH_SCRAPE_STORAGE_KEY_PREFIX}:${OWNER}`
const JOB = '11111111-1111-4111-8111-111111111111'
const OPERATION = '22222222-2222-4222-8222-222222222222'
const FAST = { ownerUserId: OWNER, pollIntervalMs: 1, maxPollAttempts: 4, maxConsecutiveStatusFailures: 1 }
const json = (payload: unknown, status = 200) => Response.json(payload, { status })
const CAPABILITIES = { scrape_capabilities: { race_scopes: ['all', 'jra'], jra_month_reuse: true } }
const stored = (changes = {}) => ({ version: 1, ownerUserId: OWNER, jobId: JOB, operationId: OPERATION,
  startDate: '20160101', endDate: '20260930', forceRescrape: false, createdAt: '2026-10-12T07:00:00Z',
  origin: 'submitted', ...changes })
const posts = () => fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')

describe('durable acquisition race scope', () => {
  beforeEach(() => { localStorage.clear(); fetchMock.mockReset() })

  it.each(['jra', 'all'] as const)('pins %s before submission and publishes the accepted scope', async raceScope => {
    const accepted = vi.fn()
    fetchMock.mockImplementation(async (url: string, init: RequestInit = {}) => {
      if (url === '/api/health') return json(CAPABILITIES)
      if (init.method === 'POST') {
        const body = JSON.parse(String(init.body))
        expect(body.race_scope).toBe(raceScope)
        expect(JSON.parse(localStorage.getItem(KEY)!).raceScope).toBe(raceScope)
        return json({ job_id: body.job_id, status: 'queued' })
      }
      return json({ job_id: url.split('/').pop(), status: 'completed', result: { races_collected: 0 } })
    })
    const hook = renderHook(() => useBatchScrape({ ...FAST, onJobAccepted: accepted }))
    await act(async () => { await hook.result.current.start('2016-01', '2026-09', false, raceScope) })
    expect(accepted).toHaveBeenCalledWith(expect.objectContaining({ raceScope }))
    expect(posts()).toHaveLength(1)
  })

  it.each([undefined, 'jra', 'all'] as const)('resends only the original body including its unchanged scope %s', async raceScope => {
    const original = stored(raceScope === undefined ? {} : { raceScope })
    const raw = JSON.stringify(original)
    localStorage.setItem(KEY, raw)
    let accepted = false
    fetchMock.mockImplementation(async (url: string, init: RequestInit = {}) => {
      if (url === '/api/health') return json(CAPABILITIES)
      if (init.method === 'POST') {
        const body = JSON.parse(String(init.body))
        expect(body).toEqual({ start_date: '20160101', end_date: '20260930', force_rescrape: false,
          server_batch: true, job_id: JOB, operation_id: OPERATION,
          ...(raceScope === undefined ? {} : { race_scope: raceScope }) })
        expect(localStorage.getItem(KEY)).toBe(raw)
        accepted = true
        return json({ job_id: JOB, status: 'queued' })
      }
      return json({ job_id: JOB, status: accepted ? 'completed' : 'not_found',
        ...(accepted ? { result: { races_collected: 0 } } : {}) })
    })
    const hook = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(hook.result.current.failureKind).toBe('monitoring'))
    expect(hook.result.current.raceScope).toBe(raceScope ?? 'all')
    expect(hook.result.current.requestPeriod).toEqual({ jobId: JOB, startPeriod: '2016-01', endPeriod: '2026-09' })
    expect(posts()).toHaveLength(0)
    await act(async () => { await hook.result.current.resubmit(() => true) })
    expect(posts()).toHaveLength(1)
  })

  it('keeps the explicit jra scope identical across a failed first POST and manual resend', async () => {
    let resending = false
    fetchMock.mockImplementation(async (url: string, init: RequestInit = {}) => {
      if (url === '/api/health') return json(CAPABILITIES)
      const saved = JSON.parse(localStorage.getItem(KEY)!)
      if (init.method === 'POST') return resending ? json({ job_id: saved.jobId, status: 'queued' }) : json({}, 502)
      return json({ job_id: saved.jobId, status: resending ? 'completed' : 'not_found',
        ...(resending ? { result: { races_collected: 0 } } : {}) })
    })
    const hook = renderHook(() => useBatchScrape(FAST))
    await act(async () => { await hook.result.current.start('2016-01', '2026-09', false, 'jra').catch(() => {}) })
    const first = posts()[0][1].body
    await act(async () => { await hook.result.current.resubmit(() => { resending = true; return true }) })
    expect(posts()[1][1].body).toBe(first)
    expect(JSON.parse(String(first)).race_scope).toBe('jra')
  })

  it.each([null, 'nar', 'JRA', true])('does not silently reinterpret malformed saved scope %s', async raceScope => {
    const raw = JSON.stringify(stored({ raceScope }))
    localStorage.setItem(KEY, raw)
    const hook = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(hook.result.current.isExecutionLocked).toBe(true))
    expect(localStorage.getItem(KEY)).toBe(raw)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('rejects an invalid new scope before reserving or submitting a job', async () => {
    const hook = renderHook(() => useBatchScrape(FAST))
    let error: unknown
    await act(async () => { error = await hook.result.current.start('2016-01', '2026-09', false,
      'nar' as ScrapeRaceScope).catch(value => value) })
    expect(error).toMatchObject({ kind: 'validation' })
    expect(localStorage.getItem(KEY)).toBeNull()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it.each([null, {}, { scrape_capabilities: { race_scopes: ['all'] } },
    { scrape_capabilities: { race_scopes: ['all', 'jra'], jra_month_reuse: false } }])
  ('does not send or reserve a new jra job on an unsupported server %s', async payload => {
    fetchMock.mockResolvedValue(json(payload))
    const hook = renderHook(() => useBatchScrape(FAST))
    let error: unknown
    await act(async () => { error = await hook.result.current.start('2016-01', '2026-09', false, 'jra')
      .catch(value => value) })
    expect(error).toMatchObject({ kind: 'start_rejected', safeToRetry: false })
    expect(localStorage.getItem(KEY)).toBeNull()
    expect(posts()).toHaveLength(0)
    expect(fetchMock).toHaveBeenCalledWith('/api/health', expect.objectContaining({ cache: 'no-store' }))
  })

  it('preserves a saved jra request without resending it to an old server', async () => {
    const raw = JSON.stringify(stored({ raceScope: 'jra' }))
    localStorage.setItem(KEY, raw)
    fetchMock.mockImplementation((url: string) => Promise.resolve(url === '/api/health'
      ? json({ status: 'healthy' }) : json({ job_id: JOB, status: 'not_found' })))
    const hook = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(hook.result.current.failureKind).toBe('monitoring'))
    const confirmation = vi.fn(() => true)
    await act(async () => { await hook.result.current.resubmit(confirmation).catch(() => {}) })
    expect(localStorage.getItem(KEY)).toBe(raw)
    expect(hook.result.current.isExecutionLocked).toBe(true)
    expect(confirmation).not.toHaveBeenCalled()
    expect(posts()).toHaveLength(0)
  })
})
