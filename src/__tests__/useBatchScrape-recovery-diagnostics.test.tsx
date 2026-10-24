import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { fetchMock } = vi.hoisted(() => ({ fetchMock: vi.fn() }))
vi.mock('@/lib/auth-fetch', () => ({ authFetch: fetchMock }))
import { BATCH_SCRAPE_STORAGE_KEY_PREFIX, useBatchScrape } from '@/hooks/useBatchScrape'

const OWNER = 'recovery-owner'
const KEY = `${BATCH_SCRAPE_STORAGE_KEY_PREFIX}:${OWNER}`
const JOB = '11111111-1111-4111-8111-111111111111'
const OPERATION = '22222222-2222-4222-8222-222222222222'
const ORIGINAL = JSON.stringify({
  version: 1, ownerUserId: OWNER, jobId: JOB, operationId: OPERATION,
  startDate: '20160101', endDate: '20260930', forceRescrape: false,
  createdAt: '2026-10-12T07:00:00Z', origin: 'submitted', raceScope: 'all',
})
const FAST = { ownerUserId: OWNER, pollIntervalMs: 1, maxPollAttempts: 4, maxConsecutiveStatusFailures: 1 }
const json = (payload: unknown, status = 200) => Response.json(payload, { status })
const posts = () => fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')

describe('batch recovery diagnostics retain the original request', () => {
  beforeEach(() => { localStorage.clear(); fetchMock.mockReset() })

  it.each([
    ['not_found', { job_id: JOB, status: 'not_found' }, 200, true, '受付記録'],
    ['service_unavailable', { detail: 'service unavailable' }, 502, false, '確認サービス'],
    ['invalid_response', null, 200, false, '応答形式'],
    ['invalid_response', { job_id: JOB, status: 'unknown' }, 200, false, '応答形式'],
    ['job_mismatch', { job_id: OPERATION, status: 'completed', result: { races_collected: 10 } }, 200, false, '一致しません'],
  ] as const)('separates %s without auto POST or releasing the lock', async (issue, payload, status, canResubmit, message) => {
    localStorage.setItem(KEY, ORIGINAL)
    fetchMock.mockResolvedValue(json(payload, status))
    const hook = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(hook.result.current.failureKind).toBe('monitoring'))
    expect(hook.result.current.monitoringIssue).toBe(issue)
    expect(hook.result.current.error).toContain(message)
    expect(hook.result.current.canResubmit).toBe(canResubmit)
    expect(hook.result.current.isExecutionLocked).toBe(true)
    expect(hook.result.current.jobId).toBe(JOB)
    expect(hook.result.current.raceScope).toBe('all')
    expect(hook.result.current.requestPeriod).toEqual({ jobId: JOB, startPeriod: '2016-01', endPeriod: '2026-09' })
    expect(localStorage.getItem(KEY)).toBe(ORIGINAL)
    expect(posts()).toHaveLength(0)
    expect(fetchMock).toHaveBeenCalledWith(`/api/scrape/status/${JOB}`, expect.objectContaining({ cache: 'no-store' }))
  })

  it('separates transport failure from a confirmed missing receipt', async () => {
    localStorage.setItem(KEY, ORIGINAL)
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    const hook = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(hook.result.current.monitoringIssue).toBe('network'))
    expect(hook.result.current.canResubmit).toBe(false)
    expect(hook.result.current.error).toContain('通信が途切れました')
    expect(localStorage.getItem(KEY)).toBe(ORIGINAL)
    expect(posts()).toHaveLength(0)
  })

  it.each([401, 403, 502])('does not leave an earlier resend offer enabled after preflight HTTP %s', async status => {
    localStorage.setItem(KEY, ORIGINAL)
    fetchMock.mockResolvedValue(json({ job_id: JOB, status: 'not_found' }))
    const hook = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(hook.result.current.canResubmit).toBe(true))
    fetchMock.mockResolvedValue(json({ detail: 'unavailable' }, status))
    const confirm = vi.fn(() => true)
    await act(async () => { await hook.result.current.resubmit(confirm).catch(() => {}) })
    expect(hook.result.current.canResubmit).toBe(false)
    expect(hook.result.current.failureKind).toBe(status === 502 ? 'monitoring' : 'authentication')
    expect(hook.result.current.monitoringIssue).toBe(status === 502 ? 'service_unavailable' : null)
    expect(hook.result.current.isExecutionLocked).toBe(true)
    expect(localStorage.getItem(KEY)).toBe(ORIGINAL)
    expect(confirm).not.toHaveBeenCalled()
    expect(posts()).toHaveLength(0)
  })

  it('clears the monitoring diagnosis only when the original receipt is recovered', async () => {
    localStorage.setItem(KEY, ORIGINAL)
    fetchMock.mockResolvedValue(json({ job_id: JOB, status: 'not_found' }))
    const hook = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(hook.result.current.monitoringIssue).toBe('not_found'))
    fetchMock.mockResolvedValue(json({ job_id: JOB, status: 'completed', result: { races_collected: 0 } }))
    await act(async () => { await hook.result.current.reconnect() })
    expect(hook.result.current.status).toBe('completed')
    expect(hook.result.current.monitoringIssue).toBeNull()
    expect(hook.result.current.error).toBeNull()
    expect(hook.result.current.isExecutionLocked).toBe(false)
    expect(localStorage.getItem(KEY)).toBeNull()
    expect(posts()).toHaveLength(0)
  })

  it('recognizes the explicit owner-cancellation reason in older terminal receipts', async () => {
    localStorage.setItem(KEY, ORIGINAL)
    fetchMock.mockResolvedValue(json({ job_id: JOB, status: 'error', error: 'cancelled-by-owner' }))
    const hook = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(hook.result.current.status).toBe('cancelled'))
    expect(hook.result.current.failureKind).toBe('cancelled')
    expect(hook.result.current.error).toBeNull()
    expect(hook.result.current.canRetry).toBe(false)
    expect(hook.result.current.isExecutionLocked).toBe(false)
    expect(localStorage.getItem(KEY)).toBeNull()
    expect(posts()).toHaveLength(0)
  })
})
