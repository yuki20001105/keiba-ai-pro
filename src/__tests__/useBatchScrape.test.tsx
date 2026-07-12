import { renderHook, act, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { mockedAuthFetch } = vi.hoisted(() => ({ mockedAuthFetch: vi.fn() }))

vi.mock('@/lib/auth-fetch', () => ({ authFetch: mockedAuthFetch }))

import { BatchScrapeError, useBatchScrape } from '@/hooks/useBatchScrape'

const FAST_OPTIONS = {
  pollIntervalMs: 1,
  maxPollAttempts: 10,
  maxConsecutiveStatusFailures: 2,
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(r => {
    resolve = r
  })
  return { promise, resolve }
}

async function sleep(ms = 5) {
  await act(async () => {
    await new Promise(resolve => setTimeout(resolve, ms))
  })
}

async function renderBatchHook(options = FAST_OPTIONS) {
  const hook = renderHook(() => useBatchScrape(options))
  await waitFor(() => {
    expect(hook.result.current).toBeTruthy()
  })
  return hook
}

describe('useBatchScrape', () => {
  beforeEach(() => {
    mockedAuthFetch.mockReset()
  })

  it('observes queued and running before completed', async () => {
    const statusDeferred: Array<ReturnType<typeof deferred<Response>>> = []

    mockedAuthFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/scrape' && init?.method === 'POST') {
        return jsonResponse({ job_id: 'job-1' })
      }
      if (url === '/api/scrape/status/job-1') {
        const d = deferred<Response>()
        statusDeferred.push(d)
        return d.promise
      }
      throw new Error(`unexpected request: ${url}`)
    })

    const { result } = await renderBatchHook()
    const promise = result.current.start('2026-01', '2026-01', false)
    const handled = promise.catch(error => error)

    await sleep()
    expect(result.current.status).toBe('queued')

    await act(async () => {
      statusDeferred.shift()?.resolve(jsonResponse({ status: 'queued', progress: { done: 0, total: 10, message: 'queued' } }))
    })
    await sleep()
    expect(result.current.status).toBe('queued')

    await act(async () => {
      statusDeferred.shift()?.resolve(jsonResponse({ status: 'running', progress: { done: 3, total: 10, message: 'running' } }))
    })
    await sleep()
    expect(result.current.status).toBe('running')

    await act(async () => {
      statusDeferred.shift()?.resolve(jsonResponse({ status: 'completed', result: { races_collected: 4 } }))
    })

    let resolved: any
    await act(async () => {
      resolved = await promise
    })

    expect(resolved).toMatchObject({ races_collected: 4 })
    expect(result.current.status).toBe('completed')
  })

  it('keeps status non-completed and result null during second-month running', async () => {
    let postCount = 0
    const febStatusDeferred: Array<ReturnType<typeof deferred<Response>>> = []

    mockedAuthFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/scrape' && init?.method === 'POST') {
        postCount += 1
        return jsonResponse({ job_id: postCount === 1 ? 'job-jan' : 'job-feb' })
      }
      if (url === '/api/scrape/status/job-jan') {
        return jsonResponse({ status: 'completed', result: { races_collected: 2 } })
      }
      if (url === '/api/scrape/status/job-feb') {
        const d = deferred<Response>()
        febStatusDeferred.push(d)
        return d.promise
      }
      throw new Error(`unexpected request: ${url}`)
    })

    const { result } = await renderBatchHook()
    const promise = result.current.start('2026-01', '2026-02', false)

    await waitFor(() => {
      expect(febStatusDeferred.length).toBeGreaterThan(0)
    })

    await act(async () => {
      febStatusDeferred.shift()?.resolve(jsonResponse({ status: 'running', progress: { done: 1, total: 10, message: 'running feb' } }))
    })
    await sleep()
    expect(result.current.status).toBe('running')
    expect(result.current.status).not.toBe('completed')
    expect(result.current.result).toBeNull()

    await waitFor(() => {
      expect(febStatusDeferred.length).toBeGreaterThan(0)
    })
    await act(async () => {
      febStatusDeferred.shift()?.resolve(jsonResponse({ status: 'completed', result: { races_collected: 3 } }))
    })

    let resolved: any
    await act(async () => {
      resolved = await promise
    })

    expect(resolved).toMatchObject({ races_collected: 5 })
    expect(result.current.status).toBe('completed')
  })

  it('blocks concurrent start atomically and keeps first run state intact', async () => {
    const statusDeferred: Array<ReturnType<typeof deferred<Response>>> = []
    let postCount = 0

    mockedAuthFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/scrape' && init?.method === 'POST') {
        postCount += 1
        return jsonResponse({ job_id: 'job-concurrent' })
      }
      if (url === '/api/scrape/status/job-concurrent') {
        const d = deferred<Response>()
        statusDeferred.push(d)
        return d.promise
      }
      throw new Error(`unexpected request: ${url}`)
    })

    const { result } = await renderBatchHook()
    const first = result.current.start('2026-01', '2026-01', false)
    await sleep()

    const second = result.current.start('2026-01', '2026-01', false).catch(error => error)
    const secondError = await second
    expect(secondError).toBeInstanceOf(BatchScrapeError)
    expect((secondError as BatchScrapeError).kind).toBe('busy')
    expect(postCount).toBe(1)
    expect(result.current.status).not.toBe('error')

    await act(async () => {
      statusDeferred.shift()?.resolve(jsonResponse({ status: 'running', progress: { done: 4, total: 10, message: 'running' } }))
    })
    await sleep()
    expect(result.current.status).toBe('running')

    await act(async () => {
      statusDeferred.shift()?.resolve(jsonResponse({ status: 'completed', result: { races_collected: 2 } }))
    })
    await act(async () => {
      await first
    })

    expect(result.current.status).toBe('completed')
    expect(result.current.result?.races_collected).toBe(2)
  })

  it('marks execution failure as retry-safe', async () => {
    mockedAuthFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/scrape' && init?.method === 'POST') return jsonResponse({ job_id: 'job-exec' })
      if (url === '/api/scrape/status/job-exec') return jsonResponse({ status: 'error', error: 'backend failed' })
      throw new Error(`unexpected request: ${url}`)
    })

    const { result } = await renderBatchHook()
    const promise = result.current.start('2026-01', '2026-01', false)
    const handled = promise.catch(error => error)
    let rejected: any
    await act(async () => {
      rejected = await handled
    })

    expect(rejected).toBeInstanceOf(BatchScrapeError)
    expect((rejected as BatchScrapeError).kind).toBe('execution')
    expect((rejected as BatchScrapeError).safeToRetry).toBe(true)
    expect(result.current.failureKind).toBe('execution')
    expect(result.current.canRetry).toBe(true)
  })

  it('marks monitoring failure as not retry-safe and locks further start', async () => {
    mockedAuthFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/scrape' && init?.method === 'POST') return jsonResponse({ job_id: 'job-nf' })
      if (url === '/api/scrape/status/job-nf') return jsonResponse({ status: 'not_found' })
      throw new Error(`unexpected request: ${url}`)
    })

    const { result } = await renderBatchHook({ ...FAST_OPTIONS, maxConsecutiveStatusFailures: 2 })
    const first = result.current.start('2026-01', '2026-01', false)
    let firstError: any
    await act(async () => {
      try {
        await first
      } catch (error) {
        firstError = error
      }
    })

    expect(firstError).toBeInstanceOf(BatchScrapeError)
    expect((firstError as BatchScrapeError).kind).toBe('monitoring')
    expect((firstError as BatchScrapeError).safeToRetry).toBe(false)
    expect(result.current.failureKind).toBe('monitoring')
    expect(result.current.canRetry).toBe(false)
    expect(result.current.isExecutionLocked).toBe(true)

    const callsAfterFailure = mockedAuthFetch.mock.calls.length
    const second = result.current.start('2026-01', '2026-01', false).catch(error => error)
    const secondError = await second
    expect(secondError).toBeInstanceOf(BatchScrapeError)
    expect((secondError as BatchScrapeError).kind).toBe('busy')
    expect(mockedAuthFetch.mock.calls.length).toBe(callsAfterFailure)
  })

  it('client abort uses monitoring-stop semantics without backend error mock', async () => {
    const statusDeferred: Array<ReturnType<typeof deferred<Response>>> = []

    mockedAuthFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/scrape' && init?.method === 'POST') return jsonResponse({ job_id: 'job-abort' })
      if (url === '/api/scrape/status/job-abort') {
        const d = deferred<Response>()
        statusDeferred.push(d)
        return d.promise
      }
      throw new Error(`unexpected request: ${url}`)
    })

    const { result } = await renderBatchHook()
    const promise = result.current.start('2026-01', '2026-01', false)
    const handled = promise.catch(error => error)

    await sleep()
    expect(result.current.status).toBe('queued')

    await act(async () => {
      result.current.abort()
    })

    await act(async () => {
      statusDeferred.shift()?.resolve(jsonResponse({ status: 'running', progress: { done: 1, total: 10, message: 'running' } }))
    })

    let rejected: any
    await act(async () => {
      rejected = await handled
    })

    expect(rejected).toBeInstanceOf(BatchScrapeError)
    expect((rejected as BatchScrapeError).kind).toBe('client_stop')
    expect((rejected as BatchScrapeError).safeToRetry).toBe(false)
    expect(result.current.failureKind).toBe('client_stop')
    expect(result.current.canRetry).toBe(false)
    expect(result.current.error).toContain('開始済みのサーバージョブは継続している可能性があります')
  })

  it('rejects invalid period format without authFetch call', async () => {
    const { result } = await renderBatchHook()
    const promise = result.current.start('2026-13', '2026-01', false)
    let rejected: any
    await act(async () => {
      try {
        await promise
      } catch (error) {
        rejected = error
      }
    })

    expect(rejected).toBeInstanceOf(BatchScrapeError)
    expect((rejected as BatchScrapeError).kind).toBe('validation')
    expect(mockedAuthFetch).not.toHaveBeenCalled()
    expect(result.current.canRetry).toBe(false)
  })

  it('rejects non-2xx start as start_rejected and retry-safe', async () => {
    mockedAuthFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/scrape' && init?.method === 'POST') {
        return jsonResponse({ detail: 'rejected by backend' }, 400)
      }
      throw new Error(`unexpected request: ${url}`)
    })

    const { result } = await renderBatchHook()
    const promise = result.current.start('2026-01', '2026-01', false)
    let rejected: any
    await act(async () => {
      try {
        await promise
      } catch (error) {
        rejected = error
      }
    })

    expect(rejected).toBeInstanceOf(BatchScrapeError)
    expect((rejected as BatchScrapeError).kind).toBe('start_rejected')
    expect((rejected as BatchScrapeError).safeToRetry).toBe(true)
    expect(result.current.canRetry).toBe(true)
  })
})
