import { renderHook, act } from '@testing-library/react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

const { mockedAuthFetch } = vi.hoisted(() => ({ mockedAuthFetch: vi.fn() }))

vi.mock('@/lib/auth-fetch', () => ({ authFetch: mockedAuthFetch }))

import { useBatchScrape } from '@/hooks/useBatchScrape'

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('useBatchScrape', () => {
  const nativeSetTimeout = globalThis.setTimeout

  beforeEach(() => {
    vi.spyOn(globalThis, 'setTimeout').mockImplementation(((handler: TimerHandler) => {
      return nativeSetTimeout(handler as any, 1)
    }) as typeof setTimeout)
    mockedAuthFetch.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
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

    const { result } = renderHook(() => useBatchScrape())
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

    const { result } = renderHook(() => useBatchScrape())
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

    const { result } = renderHook(() => useBatchScrape())
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

    const { result } = renderHook(() => useBatchScrape())
    const firstPromise = result.current.start('2026-01', '2026-01', false)
    await act(async () => {
      try {
        await firstPromise
      } catch {
        // expected
      }
    })

    const second = result.current.start('2026-01', '2026-01', false)
    await act(async () => {
      await Promise.resolve()
    })
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

    const { result } = renderHook(() => useBatchScrape())
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

    const { result } = renderHook(() => useBatchScrape())
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
        return jsonResponse({ status: 'error', error: '取得が中止されました' })
      }
      throw new Error(`unexpected request: ${url}`)
    })

    const { result } = renderHook(() => useBatchScrape())
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
    expect((rejected as Error).message).toBe('取得が中止されました')
    expect(result.current.status).toBe('error')
  })
})
