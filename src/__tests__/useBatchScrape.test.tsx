import { renderHook, act } from '@testing-library/react'
import { describe, it, expect, beforeEach, vi } from 'vitest'

const { mockedAuthFetch } = vi.hoisted(() => ({ mockedAuthFetch: vi.fn() }))

vi.mock('@/lib/auth-fetch', () => ({ authFetch: mockedAuthFetch }))

import { useBatchScrape } from '@/hooks/useBatchScrape'

const FAST_OPTIONS = {
  pollIntervalMs: 0,
  maxPollAttempts: 10,
  maxConsecutiveStatusFailures: 3,
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

async function flushMicrotasks(times = 2) {
  for (let i = 0; i < times; i += 1) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

describe('useBatchScrape', () => {
  beforeEach(() => {
    mockedAuthFetch.mockReset()
  })

  it('idle -> queued -> running -> completed', async () => {
    let pollCount = 0
    mockedAuthFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/scrape' && init?.method === 'POST') return jsonResponse({ job_id: 'job-1' })
      if (url === '/api/scrape/status/job-1') {
        pollCount += 1
        if (pollCount === 1) return jsonResponse({ status: 'queued', progress: { done: 0, total: 10, message: 'queued' } })
        if (pollCount === 2) return jsonResponse({ status: 'running', progress: { done: 5, total: 10, message: 'running' } })
        return jsonResponse({ status: 'completed', result: { races_collected: 4 } })
      }
      throw new Error(`unexpected request: ${url}`)
    })

    const { result } = renderHook(() => useBatchScrape(FAST_OPTIONS))
    const promise = result.current.start('2026-01', '2026-01', false)
    let resolved: any
    await act(async () => {
      resolved = await promise
    })
    expect(resolved).toMatchObject({ races_collected: 4 })
    expect(result.current.status).toBe('completed')
    expect(result.current.jobId).toBe('job-1')
  })

  it('completed with zero races', async () => {
    mockedAuthFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/scrape' && init?.method === 'POST') return jsonResponse({ job_id: 'job-zero' })
      if (url === '/api/scrape/status/job-zero') return jsonResponse({ status: 'completed', result: { races_collected: 0 } })
      throw new Error(`unexpected request: ${url}`)
    })

    const { result } = renderHook(() => useBatchScrape(FAST_OPTIONS))
    const promise = result.current.start('2026-01', '2026-01', false)
    let resolved: any
    await act(async () => {
      resolved = await promise
    })
    expect(resolved).toMatchObject({ races_collected: 0 })
    expect(result.current.status).toBe('completed')
    expect(result.current.result?.races_collected).toBe(0)
  })

  it('backend error -> error', async () => {
    mockedAuthFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/scrape' && init?.method === 'POST') return jsonResponse({ job_id: 'job-err' })
      if (url === '/api/scrape/status/job-err') return jsonResponse({ status: 'error', error: 'backend failed' })
      throw new Error(`unexpected request: ${url}`)
    })

    const { result } = renderHook(() => useBatchScrape(FAST_OPTIONS))
    const promise = result.current.start('2026-01', '2026-01', false)
    let rejected: any
    await act(async () => {
      try {
        await promise
      } catch (error) {
        rejected = error
      }
    })
    expect(rejected).toBeInstanceOf(Error)
    expect((rejected as Error).message).toBe('backend failed')
    expect(result.current.status).toBe('error')
    expect(result.current.error).toBe('backend failed')
  })

  it('re-run resets previous error/result/jobId', async () => {
    let run = 0
    let secondPollCount = 0
    mockedAuthFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/scrape' && init?.method === 'POST') {
        run += 1
        return jsonResponse({ job_id: run === 1 ? 'job-first' : 'job-second' })
      }
      if (url === '/api/scrape/status/job-first') return jsonResponse({ status: 'error', error: 'first failed' })
      if (url === '/api/scrape/status/job-second') {
        secondPollCount += 1
        if (secondPollCount === 1) {
          return jsonResponse({ status: 'running', progress: { done: 1, total: 10, message: 'running' } })
        }
        return jsonResponse({ status: 'completed', result: { races_collected: 1 } })
      }
      throw new Error(`unexpected request: ${url}`)
    })

    const { result } = renderHook(() => useBatchScrape(FAST_OPTIONS))
    const firstPromise = result.current.start('2026-01', '2026-01', false)
    await act(async () => {
      try {
        await firstPromise
      } catch {
        // expected
      }
    })

    const second = result.current.start('2026-01', '2026-01', false)
    await flushMicrotasks()
    expect(result.current.error).toBeNull()
    expect(result.current.result).toBeNull()
    let secondResolved: any
    await act(async () => {
      secondResolved = await second
    })
    expect(secondResolved).toMatchObject({ races_collected: 1 })
    expect(result.current.jobId).toBe('job-second')
  })

  it('multiple months do not complete globally at intermediate completion', async () => {
    let postCount = 0
    let febPollCount = 0
    mockedAuthFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/scrape' && init?.method === 'POST') {
        postCount += 1
        return jsonResponse({ job_id: postCount === 1 ? 'job-jan' : 'job-feb' })
      }
      if (url === '/api/scrape/status/job-jan') return jsonResponse({ status: 'completed', result: { races_collected: 2 } })
      if (url === '/api/scrape/status/job-feb') {
        febPollCount += 1
        if (febPollCount === 1) return jsonResponse({ status: 'running', progress: { done: 1, total: 10, message: 'running' } })
        return jsonResponse({ status: 'completed', result: { races_collected: 3 } })
      }
      if (url.startsWith('/api/scrape/status/')) {
        return jsonResponse({ status: 'error', error: 'unexpected status url' })
      }
      throw new Error(`unexpected request: ${url}`)
    })

    const { result } = renderHook(() => useBatchScrape(FAST_OPTIONS))
    const promise = result.current.start('2026-01', '2026-02', false)
    let resolved: any
    await act(async () => {
      resolved = await promise
    })
    expect(resolved).toMatchObject({ races_collected: 5 })
    expect(result.current.status).toBe('completed')
  })

  it('unknown nonterminal is not treated as completed until terminal completed arrives', async () => {
    let pollCount = 0
    mockedAuthFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/scrape' && init?.method === 'POST') return jsonResponse({ job_id: 'job-unknown' })
      if (url === '/api/scrape/status/job-unknown') {
        pollCount += 1
        if (pollCount === 1) return jsonResponse({ status: 'mystery', progress: { done: 0, total: 10, message: 'mystery' } })
        return jsonResponse({ status: 'completed', result: { races_collected: 2 } })
      }
      if (url.startsWith('/api/scrape/status/')) {
        return jsonResponse({ status: 'error', error: 'unexpected status url' })
      }
      throw new Error(`unexpected request: ${url}`)
    })

    const { result } = renderHook(() => useBatchScrape(FAST_OPTIONS))
    const promise = result.current.start('2026-01', '2026-01', false)
    let resolved: any
    await act(async () => {
      resolved = await promise
    })
    expect(resolved).toMatchObject({ races_collected: 2 })
    expect(result.current.status).toBe('completed')
  })

  it('abort is not treated as completed', async () => {
    mockedAuthFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/scrape' && init?.method === 'POST') return jsonResponse({ job_id: 'job-abort' })
      if (url === '/api/scrape/status/job-abort') {
        return jsonResponse({ status: 'error', error: '取得を中断しました' })
      }
      throw new Error(`unexpected request: ${url}`)
    })

    const { result } = renderHook(() => useBatchScrape(FAST_OPTIONS))
    const promise = result.current.start('2026-01', '2026-01', false)
    const handled = promise.catch(error => error)
    await act(async () => {
      result.current.abort()
    })

    let rejected: any
    await act(async () => {
      rejected = await handled
    })
    expect(rejected).toBeInstanceOf(Error)
    expect((rejected as Error).message).toBe('取得を中断しました')
    expect(result.current.status).toBe('error')
  })

  it('rejects invalid period format without authFetch calls', async () => {
    const { result } = renderHook(() => useBatchScrape(FAST_OPTIONS))
    const promise = result.current.start('2026-13', '2026-01', false)
    let rejected: any
    await act(async () => {
      try {
        await promise
      } catch (error) {
        rejected = error
      }
    })

    expect(rejected).toBeInstanceOf(Error)
    expect((rejected as Error).message).toContain('期間指定が不正')
    expect(mockedAuthFetch).not.toHaveBeenCalled()
    expect(result.current.status).toBe('error')
  })

  it('rejects start > end without authFetch calls', async () => {
    const { result } = renderHook(() => useBatchScrape(FAST_OPTIONS))
    const promise = result.current.start('2026-02', '2026-01', false)
    let rejected: any
    await act(async () => {
      try {
        await promise
      } catch (error) {
        rejected = error
      }
    })

    expect(rejected).toBeInstanceOf(Error)
    expect((rejected as Error).message).toContain('開始年月')
    expect(mockedAuthFetch).not.toHaveBeenCalled()
    expect(result.current.status).toBe('error')
  })

  it('fails when start response job_id is empty', async () => {
    mockedAuthFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/scrape' && init?.method === 'POST') return jsonResponse({ job_id: '' })
      throw new Error(`unexpected request: ${url}`)
    })

    const { result } = renderHook(() => useBatchScrape(FAST_OPTIONS))
    const promise = result.current.start('2026-01', '2026-01', false)
    let rejected: any
    await act(async () => {
      try {
        await promise
      } catch (error) {
        rejected = error
      }
    })

    expect(rejected).toBeInstanceOf(Error)
    expect((rejected as Error).message).toContain('job_id')
    expect(result.current.status).toBe('error')
  })

  it('stops polling after consecutive not_found failures', async () => {
    mockedAuthFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/scrape' && init?.method === 'POST') return jsonResponse({ job_id: 'job-nf' })
      if (url === '/api/scrape/status/job-nf') return jsonResponse({ status: 'not_found' })
      throw new Error(`unexpected request: ${url}`)
    })

    const { result } = renderHook(() => useBatchScrape({ ...FAST_OPTIONS, maxConsecutiveStatusFailures: 2 }))
    const promise = result.current.start('2026-01', '2026-01', false)
    let rejected: any
    await act(async () => {
      try {
        await promise
      } catch (error) {
        rejected = error
      }
    })

    expect(rejected).toBeInstanceOf(Error)
    expect((rejected as Error).message).toContain('ジョブが見つかりません')
    expect(result.current.status).toBe('error')
  })

  it('stops polling after max attempts on unknown status', async () => {
    mockedAuthFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/scrape' && init?.method === 'POST') return jsonResponse({ job_id: 'job-unknown-loop' })
      if (url === '/api/scrape/status/job-unknown-loop') return jsonResponse({ status: 'mystery', progress: { done: 0, total: 10 } })
      throw new Error(`unexpected request: ${url}`)
    })

    const { result } = renderHook(() => useBatchScrape({ ...FAST_OPTIONS, maxPollAttempts: 2 }))
    const promise = result.current.start('2026-01', '2026-01', false)
    let rejected: any
    await act(async () => {
      try {
        await promise
      } catch (error) {
        rejected = error
      }
    })

    expect(rejected).toBeInstanceOf(Error)
    expect((rejected as Error).message).toContain('上限回数')
    expect(result.current.status).toBe('error')
    expect(result.current.status).not.toBe('completed')
  })
})
