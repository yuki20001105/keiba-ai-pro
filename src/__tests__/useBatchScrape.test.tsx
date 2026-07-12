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
    expect(result.current.canRetry).toBe(false)
  })

  it('accepts completed with races_collected=0 as normal completion', async () => {
    mockedAuthFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/scrape' && init?.method === 'POST') return jsonResponse({ job_id: 'job-zero' })
      if (url === '/api/scrape/status/job-zero') return jsonResponse({ status: 'completed', result: { races_collected: 0 } })
      throw new Error(`unexpected request: ${url}`)
    })

    const { result } = await renderBatchHook()
    let resolved: any
    await act(async () => {
      resolved = await result.current.start('2026-01', '2026-01', false)
    })

    expect(resolved.races_collected).toBe(0)
    expect(result.current.status).toBe('completed')
    expect(result.current.result?.races_collected).toBe(0)
    expect(result.current.canRetry).toBe(false)
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

  it('rejects malformed completed result payloads as monitoring uncertainty', async () => {
    const invalidValues: unknown[] = [
      null,
      undefined,
      '',
      false,
      true,
      '8',
      Number.NaN,
      Number.POSITIVE_INFINITY,
      -1,
      1.2,
    ]

    for (const value of invalidValues) {
      mockedAuthFetch.mockReset()
      mockedAuthFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url === '/api/scrape' && init?.method === 'POST') return jsonResponse({ job_id: 'job-malformed' })
        if (url === '/api/scrape/status/job-malformed') {
          return jsonResponse({ status: 'completed', result: { races_collected: value } })
        }
        throw new Error(`unexpected request: ${url}`)
      })

      const { result } = await renderBatchHook({ ...FAST_OPTIONS, maxPollAttempts: 2 })
      const rejected = await result.current.start('2026-01', '2026-01', false).catch(error => error)

      expect(rejected).toBeInstanceOf(BatchScrapeError)
      expect((rejected as BatchScrapeError).kind).toBe('monitoring')
      expect((rejected as BatchScrapeError).safeToRetry).toBe(false)
      await waitFor(() => {
        expect(result.current.status).toBe('error')
      })
      expect(result.current.result).toBeNull()
      expect(result.current.error).toContain('完了応答の形式を確認できないため、サーバージョブの状態確認が必要')
    }
  })

  it('rejects completed payload when result is missing or null', async () => {
    const payloads = [
      { status: 'completed' },
      { status: 'completed', result: null },
    ]

    for (const payload of payloads) {
      mockedAuthFetch.mockReset()
      mockedAuthFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url === '/api/scrape' && init?.method === 'POST') return jsonResponse({ job_id: 'job-completed-invalid' })
        if (url === '/api/scrape/status/job-completed-invalid') return jsonResponse(payload)
        throw new Error(`unexpected request: ${url}`)
      })

      const { result } = await renderBatchHook({ ...FAST_OPTIONS, maxPollAttempts: 2 })
      const rejected = await result.current.start('2026-01', '2026-01', false).catch(error => error)
      expect((rejected as BatchScrapeError).kind).toBe('monitoring')
      await waitFor(() => {
        expect(result.current.status).toBe('error')
      })
      expect(result.current.result).toBeNull()
    }
  })

  it('resets error/result/jobId on rerun start', async () => {
    let run = 0
    const secondStatusDeferred: Array<ReturnType<typeof deferred<Response>>> = []
    mockedAuthFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/scrape' && init?.method === 'POST') {
        run += 1
        return jsonResponse({ job_id: run === 1 ? 'job-first' : 'job-second' })
      }
      if (url === '/api/scrape/status/job-first') return jsonResponse({ status: 'error', error: 'backend failed' })
      if (url === '/api/scrape/status/job-second') {
        const d = deferred<Response>()
        secondStatusDeferred.push(d)
        return d.promise
      }
      throw new Error(`unexpected request: ${url}`)
    })

    const { result } = await renderBatchHook()
    await result.current.start('2026-01', '2026-01', false).catch(() => undefined)

    await waitFor(() => {
      expect(result.current.status).toBe('error')
    })
    expect(result.current.error).toContain('backend failed')
    expect(result.current.jobId).toBe('job-first')
    expect(result.current.result).toBeNull()

    const secondPromise = result.current.start('2026-01', '2026-01', false)
    await sleep()
    expect(result.current.error).toBeNull()
    expect(result.current.result).toBeNull()
    expect(result.current.jobId).toBe('job-second')

    await act(async () => {
      secondStatusDeferred.shift()?.resolve(jsonResponse({ status: 'completed', result: { races_collected: 2 } }))
    })

    await act(async () => {
      await secondPromise
    })
    expect(result.current.status).toBe('completed')
  })

  it('rejects empty job_id as monitoring uncertainty', async () => {
    mockedAuthFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/scrape' && init?.method === 'POST') return jsonResponse({ job_id: '' })
      throw new Error(`unexpected request: ${url}`)
    })

    const { result } = await renderBatchHook()
    const rejected = await result.current.start('2026-01', '2026-01', false).catch(error => error)
    expect(rejected).toBeInstanceOf(BatchScrapeError)
    expect((rejected as BatchScrapeError).kind).toBe('monitoring')
    expect(result.current.canRetry).toBe(false)
  })

  it('unknown status finishes within maxPollAttempts as monitoring uncertainty', async () => {
    mockedAuthFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/scrape' && init?.method === 'POST') return jsonResponse({ job_id: 'job-unknown' })
      if (url === '/api/scrape/status/job-unknown') return jsonResponse({ status: 'mystery' })
      throw new Error(`unexpected request: ${url}`)
    })

    const { result } = await renderBatchHook({ ...FAST_OPTIONS, maxPollAttempts: 3 })
    const rejected = await result.current.start('2026-01', '2026-01', false).catch(error => error)
    expect(rejected).toBeInstanceOf(BatchScrapeError)
    expect((rejected as BatchScrapeError).kind).toBe('monitoring')
    expect((rejected as BatchScrapeError).safeToRetry).toBe(false)
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

    await act(async () => {
      statusDeferred.shift()?.resolve(jsonResponse({ status: 'completed', result: { races_collected: 2 } }))
    })
    await act(async () => {
      await first
    })

    expect(result.current.status).toBe('completed')
    expect(result.current.result?.races_collected).toBe(2)
  })

  it('rejects synchronous re-start right after monitoring failure without extra POST', async () => {
    let postCount = 0
    mockedAuthFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/scrape' && init?.method === 'POST') {
        postCount += 1
        return jsonResponse({ job_id: 'job-monitoring' })
      }
      if (url === '/api/scrape/status/job-monitoring') return jsonResponse({ status: 'not_found' })
      throw new Error(`unexpected request: ${url}`)
    })

    const { result } = await renderBatchHook({ ...FAST_OPTIONS, maxConsecutiveStatusFailures: 1 })
    const firstError = await result.current.start('2026-01', '2026-01', false).catch(error => error)

    expect((firstError as BatchScrapeError).kind).toBe('monitoring')

    const secondError = await result.current.start('2026-01', '2026-01', false).catch(error => error)
    expect(secondError).toBeInstanceOf(BatchScrapeError)
    expect((secondError as BatchScrapeError).kind).toBe('busy')
    expect(postCount).toBe(1)
  })

  it('marks execution failure as retry-safe', async () => {
    mockedAuthFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/scrape' && init?.method === 'POST') return jsonResponse({ job_id: 'job-exec' })
      if (url === '/api/scrape/status/job-exec') return jsonResponse({ status: 'error', error: 'backend failed' })
      throw new Error(`unexpected request: ${url}`)
    })

    const { result } = await renderBatchHook()
    const rejected = await result.current.start('2026-01', '2026-01', false).catch(error => error)

    expect(rejected).toBeInstanceOf(BatchScrapeError)
    expect((rejected as BatchScrapeError).kind).toBe('execution')
    expect((rejected as BatchScrapeError).safeToRetry).toBe(true)
    await waitFor(() => {
      expect(result.current.failureKind).toBe('execution')
      expect(result.current.canRetry).toBe(true)
    })
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
    const handled = result.current.start('2026-01', '2026-01', false).catch(error => error)

    await sleep()
    await act(async () => {
      result.current.abort()
    })

    await act(async () => {
      statusDeferred.shift()?.resolve(jsonResponse({ status: 'running', progress: { done: 1, total: 10, message: 'running' } }))
    })

    const rejected = await handled
    expect(rejected).toBeInstanceOf(BatchScrapeError)
    expect((rejected as BatchScrapeError).kind).toBe('client_stop')
    expect(result.current.canRetry).toBe(false)
    expect(result.current.isExecutionLocked).toBe(true)
  })

  it('rejects start>end without authFetch call', async () => {
    const { result } = await renderBatchHook()
    const rejected = await result.current.start('2026-02', '2026-01', false).catch(error => error)

    expect(rejected).toBeInstanceOf(BatchScrapeError)
    expect((rejected as BatchScrapeError).kind).toBe('validation')
    expect(mockedAuthFetch).not.toHaveBeenCalled()
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
    const rejected = await result.current.start('2026-01', '2026-01', false).catch(error => error)

    expect(rejected).toBeInstanceOf(BatchScrapeError)
    expect((rejected as BatchScrapeError).kind).toBe('start_rejected')
    expect((rejected as BatchScrapeError).safeToRetry).toBe(true)
    await waitFor(() => {
      expect(result.current.canRetry).toBe(true)
    })
  })
})
