import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const authFetchMock = vi.hoisted(() => vi.fn())

vi.mock('@/lib/auth-fetch', () => ({
  authFetch: authFetchMock,
}))

import { ACTIVE_SCRAPE_JOB_KEY, useBatchScrape } from '@/hooks/useBatchScrape'

function response(body: object, ok = true): Response {
  return {
    ok,
    status: ok ? 200 : 500,
    json: async () => body,
  } as Response
}

beforeEach(() => {
  localStorage.clear()
  authFetchMock.mockReset()
})

describe('useBatchScrape', () => {
  it('submits a multi-month range as one backend job', async () => {
    authFetchMock
      .mockResolvedValueOnce(response({ job_id: 'job-range-1' }))
      .mockResolvedValueOnce(response({
        status: 'completed',
        result: { races_collected: 42, elapsed_time: 12 },
      }))

    const { result } = renderHook(() => useBatchScrape())
    let completed: Awaited<ReturnType<typeof result.current.start>> | undefined
    await act(async () => {
      completed = await result.current.start('2025-01', '2025-02', false)
    })

    expect(authFetchMock).toHaveBeenCalledTimes(2)
    const [, init] = authFetchMock.mock.calls[0]
    expect(authFetchMock.mock.calls[0][0]).toBe('/api/scrape')
    expect(JSON.parse(String(init?.body))).toEqual({
      start_date: '20250101',
      end_date: '20250228',
      force_rescrape: false,
      dry_run: false,
    })
    expect(completed?.stats.total_months).toBe(2)
    expect(completed?.races_collected).toBe(42)
    expect(localStorage.getItem(ACTIVE_SCRAPE_JOB_KEY)).toBeNull()
  })

  it('reconnects to the stored job after a page refresh', async () => {
    localStorage.setItem(ACTIVE_SCRAPE_JOB_KEY, JSON.stringify({
      jobId: 'job-resume-1',
      startPeriod: '2026-01',
      endPeriod: '2026-03',
      forceRescrape: false,
      startedAt: Date.now(),
    }))
    authFetchMock.mockResolvedValueOnce(response({
      status: 'completed',
      result: { races_collected: 9, elapsed_time: 3 },
    }))

    const { result } = renderHook(() => useBatchScrape())

    await waitFor(() => expect(result.current.result?.races_collected).toBe(9))
    expect(authFetchMock).toHaveBeenCalledWith('/api/scrape/status/job-resume-1')
    expect(result.current.result?.stats.total_months).toBe(3)
    expect(localStorage.getItem(ACTIVE_SCRAPE_JOB_KEY)).toBeNull()
  })
})
