import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
const { mockedAuthFetch } = vi.hoisted(() => ({ mockedAuthFetch: vi.fn() }))
vi.mock('@/lib/auth-fetch', () => ({ authFetch: mockedAuthFetch }))
import { BATCH_SCRAPE_MAX_POLL_DURATION_MS, BATCH_SCRAPE_STORAGE_KEY_PREFIX, BatchScrapeError, useBatchScrape } from '@/hooks/useBatchScrape'

const OWNER = 'owner-a'
const KEY = `${BATCH_SCRAPE_STORAGE_KEY_PREFIX}:${OWNER}`
const JOB = '11111111-1111-4111-8111-111111111111'
const OPERATION = '22222222-2222-4222-8222-222222222222'
const FAST = { ownerUserId: OWNER, pollIntervalMs: 1, maxPollAttempts: 8, maxConsecutiveStatusFailures: 2 }
const json = (payload: unknown, status = 200) => new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
const stored = (changes = {}) => ({
  version: 1, ownerUserId: OWNER, jobId: JOB, operationId: OPERATION,
  startDate: '20260101', endDate: '20260228', forceRescrape: false, createdAt: '2026-09-13T12:00:00Z', ...changes,
})
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(r => { resolve = r })
  return { promise, resolve }
}
function postCalls() {
  return mockedAuthFetch.mock.calls.filter(([url, init]) => url === '/api/scrape' && init?.method === 'POST')
}
function setupStatus(reply: (jobId: string, count: number) => Response | Promise<Response>) {
  let polls = 0
  mockedAuthFetch.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url === '/api/scrape' && init?.method === 'POST') {
      const body = JSON.parse(String(init.body))
      return json({ job_id: body.job_id, status: 'queued' })
    }
    if (url.startsWith('/api/scrape/status/')) return reply(url.split('/').pop()!, ++polls)
    throw new Error('unexpected request: ' + url)
  })
}
async function startAndCatch(hook: ReturnType<typeof renderHook<ReturnType<typeof useBatchScrape>, unknown>>, start = '2026-01', end = '2026-02') {
  let answer: unknown
  await act(async () => { answer = await hook.result.current.start(start, end, false).catch(e => e) })
  return answer
}

describe('useBatchScrape server-owned period', () => {
  beforeEach(() => { mockedAuthFetch.mockReset(); localStorage.clear() })

  it('does not end healthy multi-day monitoring at 24 hours', () => {
    expect(BATCH_SCRAPE_MAX_POLL_DURATION_MS).toBe(Number.POSITIVE_INFINITY)
  })

  it('persists owner/job/operation identity before exactly one full-period POST', async () => {
    mockedAuthFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === '/api/scrape') {
        const body = JSON.parse(String(init?.body))
        const saved = JSON.parse(localStorage.getItem(KEY)!)
        expect(saved).toMatchObject({ ownerUserId: OWNER, jobId: body.job_id, operationId: body.operation_id })
        expect(body).toMatchObject({ server_batch: true, start_date: '20160101', end_date: '20260930', force_rescrape: false })
        expect(body.job_id).toMatch(/^[a-f0-9-]{36}$/)
        expect(body.operation_id).not.toBe(body.job_id)
        return json({ job_id: body.job_id, status: 'queued' })
      }
      return json({ job_id: url.split('/').pop(), status: 'completed', result: { races_collected: 4 } })
    })
    const hook = renderHook(() => useBatchScrape(FAST))
    const result = await startAndCatch(hook, '2016-01', '2026-09')
    expect(result).toMatchObject({ races_collected: 4, stats: { total_months: 129 } })
    expect(postCalls()).toHaveLength(1)
    expect(localStorage.getItem(KEY)).toBeNull()
    expect(hook.result.current.status).toBe('completed')
  })

  it('shows whole-parent progress without finishing after a single month', async () => {
    const last = deferred<Response>()
    let id = ''
    setupStatus((jobId, count) => {
      id = jobId
      return count === 1 ? json({ job_id: id, status: 'running', progress: {
        done: 1000, total: 2000, current_month: '2026-02', completed_months: 1, total_months: 2,
        saved_races: 7, saved_horses: 82, existing_races_skipped: 279, no_race_dates: 14, message: '取得中',
      } }) : last.promise
    })
    const hook = renderHook(() => useBatchScrape(FAST))
    let promise!: Promise<unknown>
    act(() => { promise = hook.result.current.start('2026-01', '2026-02', false) })
    await waitFor(() => expect(hook.result.current.progress.current).toBe(50))
    expect(hook.result.current.progress).toMatchObject({ newSavedRaces: 7, newSavedHorses: 82, existingRacesSkipped: 279, verifiedNoRaceDates: 14 })
    expect(hook.result.current.progress.message).toContain('2026-02')
    expect(hook.result.current.result).toBeNull()
    expect(postCalls()).toHaveLength(1)
    await act(async () => { last.resolve(json({ job_id: id, status: 'completed', result: { races_collected: 12, saved_horses: 130 } })); await promise })
    expect(hook.result.current.result).toMatchObject({ races_collected: 12, saved_horses: 130 })
  })

  it('publishes exact parent period after acceptance', async () => {
    const finish = deferred<Response>()
    const onJobAccepted = vi.fn()
    let id = ''
    setupStatus(jobId => { id = jobId; return finish.promise })
    const hook = renderHook(() => useBatchScrape({ ...FAST, onJobAccepted }))
    let promise!: Promise<unknown>
    act(() => { promise = hook.result.current.start('2026-01', '2026-02', false) })
    await waitFor(() => expect(onJobAccepted).toHaveBeenCalledTimes(1))
    expect(onJobAccepted).toHaveBeenCalledWith(expect.objectContaining({ jobId: id, startDate: '20260101', endDate: '20260228' }))
    await act(async () => { finish.resolve(json({ job_id: id, status: 'completed', result: { races_collected: 0 } })); await promise })
    expect(hook.result.current.result?.races_collected).toBe(0)
  })

  it.each(['lost-response', 'missing-id', 'wrong-id', 'proxy-502'])('recovers %s with saved ID and no additional POST', async mode => {
    mockedAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/scrape') {
        if (mode === 'lost-response') throw new TypeError('Failed to fetch')
        if (mode === 'proxy-502') return json({ error: 'upstream unknown' }, 502)
        return json(mode === 'wrong-id' ? { job_id: JOB } : {})
      }
      const saved = JSON.parse(localStorage.getItem(KEY)!)
      expect(url).toBe('/api/scrape/status/' + saved.jobId)
      return json({ job_id: saved.jobId, status: 'completed', result: { races_collected: 2 } })
    })
    const hook = renderHook(() => useBatchScrape(FAST))
    expect(await startAndCatch(hook)).toMatchObject({ races_collected: 2 })
    expect(postCalls()).toHaveLength(1)
  })

  it('retains the preallocated ID when acceptance cannot be resolved', async () => {
    mockedAuthFetch.mockImplementation(async (url: string) => url === '/api/scrape'
      ? json({})
      : json({ status: 'not_found' }, 404))
    const hook = renderHook(() => useBatchScrape(FAST))
    const failure = await startAndCatch(hook)
    expect(failure).toBeInstanceOf(BatchScrapeError)
    expect(failure).toMatchObject({ kind: 'monitoring', safeToRetry: false, jobId: hook.result.current.jobId })
    expect(JSON.parse(localStorage.getItem(KEY)!).jobId).toBe(hook.result.current.jobId)
    expect(hook.result.current.isExecutionLocked).toBe(true)
    expect(await startAndCatch(hook)).toMatchObject({ kind: 'busy' })
    expect(postCalls()).toHaveLength(1)
  })

  it('reopens an accepted job using status GET only', async () => {
    localStorage.setItem(KEY, JSON.stringify(stored()))
    setupStatus(jobId => json({ job_id: jobId, status: 'completed', result: { races_collected: 5 } }))
    const hook = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(hook.result.current.status).toBe('completed'))
    expect(hook.result.current.result?.races_collected).toBe(5)
    expect(postCalls()).toHaveLength(0)
    expect(localStorage.getItem(KEY)).toBeNull()
  })

  it('recovers an owner-history parent on a different browser without submitting', async () => {
    setupStatus(jobId => json({ job_id: jobId, status: 'completed', result: { races_collected: 5 } }))
    const hook = renderHook(() => useBatchScrape(FAST))
    await act(async () => {
      await hook.result.current.reconnect({ jobId: JOB, status: 'running', startDate: '20260101', endDate: '20260228', acceptedAt: '2026-09-13T12:00:00Z' })
    })
    expect(hook.result.current.status).toBe('completed')
    expect(postCalls()).toHaveLength(0)
  })

  it('unmount aborts observation, preserves ID, and does not cancel or enqueue more work', async () => {
    const pending = deferred<Response>()
    let id = ''
    setupStatus(jobId => { id = jobId; return pending.promise })
    const hook = renderHook(() => useBatchScrape(FAST))
    let promise!: Promise<unknown>
    act(() => { promise = hook.result.current.start('2026-01', '2026-02', false).catch(e => e) })
    await waitFor(() => expect(id).not.toBe(''))
    hook.unmount()
    pending.resolve(json({ job_id: id, status: 'running' }))
    expect(await promise).toMatchObject({ kind: 'client_stop', jobId: id })
    expect(JSON.parse(localStorage.getItem(KEY)!).jobId).toBe(id)
    expect(postCalls()).toHaveLength(1)
    expect(mockedAuthFetch.mock.calls.some(([url]) => String(url).includes('/cancel/'))).toBe(false)
    setupStatus(jobId => json({ job_id: jobId, status: 'completed', result: { races_collected: 8 } }))
    const reopened = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(reopened.result.current.status).toBe('completed'))
    expect(postCalls()).toHaveLength(1)
  })

  it.each([401, 403])('keeps accepted job on status HTTP %s and only reconnects after authentication', async status => {
    setupStatus(() => json({ detail: 'expired' }, status))
    const hook = renderHook(() => useBatchScrape(FAST))
    const failure = await startAndCatch(hook)
    expect(failure).toMatchObject({ kind: 'authentication', safeToRetry: false })
    const id = hook.result.current.jobId!
    expect(JSON.parse(localStorage.getItem(KEY)!).jobId).toBe(id)
    setupStatus(jobId => json({ job_id: jobId, status: 'completed', result: { races_collected: 7 } }))
    await act(async () => { await hook.result.current.reconnect() })
    expect(hook.result.current.status).toBe('completed')
    expect(postCalls()).toHaveLength(1)
  })

  it.each([401, 403])('does not permanently lock a never-accepted submission rejected with HTTP %s', async status => {
    mockedAuthFetch.mockResolvedValue(json({ detail: 'denied' }, status))
    const hook = renderHook(() => useBatchScrape(FAST))
    expect(await startAndCatch(hook)).toMatchObject({ kind: 'authentication', safeToRetry: true })
    expect(localStorage.getItem(KEY)).toBeNull()
    expect(hook.result.current.isExecutionLocked).toBe(false)
    expect(hook.result.current.jobId).toBeNull()
    expect(mockedAuthFetch).toHaveBeenCalledTimes(1)
  })

  it('does not lose an accepted ID on temporary auth session/network failure', async () => {
    setupStatus(() => { throw Object.assign(new Error('session unavailable'), { code: 'AUTH_SESSION_UNAVAILABLE' }) })
    const hook = renderHook(() => useBatchScrape(FAST))
    expect(await startAndCatch(hook)).toMatchObject({ kind: 'monitoring', safeToRetry: false })
    expect(localStorage.getItem(KEY)).not.toBeNull()
  })

  it.each([null, undefined, '', false, '8', -1, 1.2])('rejects malformed completion races_collected=%s without clearing ID', async value => {
    setupStatus(jobId => json({ job_id: jobId, status: 'completed', result: { races_collected: value } }))
    const hook = renderHook(() => useBatchScrape(FAST))
    expect(await startAndCatch(hook)).toMatchObject({ kind: 'monitoring', safeToRetry: false })
    expect(hook.result.current.result).toBeNull()
    expect(localStorage.getItem(KEY)).not.toBeNull()
    expect(hook.result.current.error).toContain('完了結果')
  })

  it.each([{ status: 'completed', result: { races_collected: 9 } }, { job_id: JOB, status: 'completed', result: { races_collected: 9 } }])('does not unlock a status response without the exact saved ID', async payload => {
    setupStatus(() => json(payload))
    const hook = renderHook(() => useBatchScrape(FAST))
    expect(await startAndCatch(hook)).toMatchObject({ kind: 'monitoring' })
    expect(localStorage.getItem(KEY)).not.toBeNull()
  })

  it('rejects unknown status within bounded test monitoring attempts', async () => {
    setupStatus(jobId => json({ job_id: jobId, status: 'mystery' }))
    const hook = renderHook(() => useBatchScrape(FAST))
    expect(await startAndCatch(hook)).toMatchObject({ kind: 'monitoring', safeToRetry: false })
  })

  it('blocks concurrent starts atomically and preserves the first request', async () => {
    const last = deferred<Response>()
    let id = ''
    setupStatus(jobId => { id = jobId; return last.promise })
    const hook = renderHook(() => useBatchScrape(FAST))
    let first!: Promise<unknown>
    act(() => { first = hook.result.current.start('2026-01', '2026-02', false) })
    expect(await startAndCatch(hook)).toMatchObject({ kind: 'busy' })
    expect(postCalls()).toHaveLength(1)
    await act(async () => { last.resolve(json({ job_id: id, status: 'completed', result: { races_collected: 2 } })); await first })
    expect(hook.result.current.result?.races_collected).toBe(2)
  })

  it('preserves another owner record and never observes it', async () => {
    localStorage.setItem(KEY, JSON.stringify(stored()))
    const hook = renderHook(() => useBatchScrape({ ...FAST, ownerUserId: 'owner-b' }))
    expect(hook.result.current.jobId).toBeNull()
    expect(mockedAuthFetch).not.toHaveBeenCalled()
    expect(localStorage.getItem(KEY)).not.toBeNull()
  })

  it.each([{ jobId: '../other' }, { operationId: 'bad-id' }, { ownerUserId: 'someone-else' }, { startDate: '20260231' }, { endDate: '20250101' }])('fails closed on corrupted owner-scoped storage: %s', async change => {
    const raw = JSON.stringify(stored(change))
    localStorage.setItem(KEY, raw)
    const hook = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(hook.result.current.isExecutionLocked).toBe(true))
    expect(mockedAuthFetch).not.toHaveBeenCalled()
    expect(localStorage.getItem(KEY)).toBe(raw)
  })

  it('does not submit if durable storage is unavailable', async () => {
    const hook = renderHook(() => useBatchScrape(FAST))
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
    try {
      expect(await startAndCatch(hook)).toMatchObject({ kind: 'start_rejected', safeToRetry: false })
      expect(mockedAuthFetch).not.toHaveBeenCalled()
    } finally { spy.mockRestore() }
  })

  it.each([['2026-02', '2026-01'], ['2026-13', '2026-01'], ['invalid', '2026-01']])('rejects invalid dates %s through %s before POST', async (start, end) => {
    const hook = renderHook(() => useBatchScrape(FAST))
    expect(await startAndCatch(hook, start, end)).toMatchObject({ kind: 'validation', safeToRetry: false })
    expect(mockedAuthFetch).not.toHaveBeenCalled()
  })

  it('allows a fresh request after definite validation rejection, not an uncertain failure', async () => {
    mockedAuthFetch.mockResolvedValue(json({ detail: 'invalid date' }, 400))
    const hook = renderHook(() => useBatchScrape(FAST))
    expect(await startAndCatch(hook)).toMatchObject({ kind: 'start_rejected', safeToRetry: true })
    expect(localStorage.getItem(KEY)).toBeNull()
    setupStatus(jobId => json({ job_id: jobId, status: 'completed', result: { races_collected: 2 } }))
    expect(await startAndCatch(hook)).toMatchObject({ races_collected: 2 })
    expect(postCalls()).toHaveLength(2)
  })

  it('recognizes another owner job conflict without a blind retry', async () => {
    mockedAuthFetch.mockResolvedValue(json({ detail: 'owner-active-job' }, 409))
    const hook = renderHook(() => useBatchScrape(FAST))
    expect(await startAndCatch(hook)).toMatchObject({ kind: 'busy', safeToRetry: false })
    expect(localStorage.getItem(KEY)).toBeNull()
    expect(postCalls()).toHaveLength(1)
  })

  it('keeps cancelling pending until server parent confirms cancellation', async () => {
    const last = deferred<Response>()
    let id = ''
    setupStatus((jobId, count) => {
      id = jobId
      return count === 1 ? json({ job_id: id, status: 'cancelling' }) : last.promise
    })
    const hook = renderHook(() => useBatchScrape(FAST))
    let promise!: Promise<unknown>
    act(() => { promise = hook.result.current.start('2026-01', '2026-02', false).catch(e => e) })
    await waitFor(() => expect(hook.result.current.status).toBe('cancelling'))
    expect(hook.result.current.loading).toBe(true)
    expect(localStorage.getItem(KEY)).not.toBeNull()
    await act(async () => { last.resolve(json({ job_id: id, status: 'cancelled' })); await promise })
    expect(hook.result.current.status).toBe('cancelled')
    expect(hook.result.current.error).toBeNull()
    expect(localStorage.getItem(KEY)).toBeNull()
    expect(postCalls()).toHaveLength(1)
  })

  it('does not pretend cancellation won when the parent completed concurrently', async () => {
    const last = deferred<Response>()
    let id = ''
    setupStatus(jobId => { id = jobId; return last.promise })
    const hook = renderHook(() => useBatchScrape(FAST))
    let promise!: Promise<unknown>
    act(() => { promise = hook.result.current.start('2026-01', '2026-02', false) })
    await waitFor(() => expect(id).not.toBe(''))
    act(() => hook.result.current.requestBatchStop())
    await act(async () => { last.resolve(json({ job_id: id, status: 'completed', result: { races_collected: 3 } })); await promise })
    expect(hook.result.current.status).toBe('completed')
    expect(postCalls()).toHaveLength(1)
  })

  it('marks a durable server error as retry-safe and clears only its own ID', async () => {
    setupStatus(jobId => json({ job_id: jobId, status: 'error', error: 'backend failed' }))
    const hook = renderHook(() => useBatchScrape(FAST))
    expect(await startAndCatch(hook)).toMatchObject({ kind: 'execution', safeToRetry: true })
    expect(hook.result.current.error).toBe('backend failed')
    expect(localStorage.getItem(KEY)).toBeNull()
  })

  it('refuses to clear a different pending parent during legacy lock reconciliation', async () => {
    setupStatus(() => json({ status: 'not_found' }, 404))
    const hook = renderHook(() => useBatchScrape(FAST))
    await startAndCatch(hook)
    act(() => { expect(hook.result.current.clearExecutionLockAfterReconciliation(JOB)).toBe(false) })
    expect(localStorage.getItem(KEY)).not.toBeNull()
  })

  it('manually resends only the same saved identity after an authoritative not_found and explicit confirmation', async () => {
    let resending = false
    mockedAuthFetch.mockImplementation(async (url: string) => {
      const id = JSON.parse(localStorage.getItem(KEY)!).jobId
      if (url === '/api/scrape') return resending ? json({ job_id: id, status: 'queued' }) : json({}, 502)
      return resending ? json({ job_id: id, status: 'completed', result: { races_collected: 3 } }) : json({ job_id: id, status: 'not_found' })
    })
    const hook = renderHook(() => useBatchScrape(FAST))
    await startAndCatch(hook)
    expect(hook.result.current.canResubmit).toBe(true)
    const before = JSON.parse(String(postCalls()[0][1].body))
    const confirm = vi.fn(() => { resending = true; return true })
    await act(async () => { await hook.result.current.resubmit(confirm) })
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(postCalls()).toHaveLength(2)
    expect(JSON.parse(String(postCalls()[1][1].body))).toEqual(before)
    expect(hook.result.current.status).toBe('completed')
  })

  it.each([401, 403, 503, 200])('does not resend if the preflight HTTP %s is unauthorized, unavailable, or malformed', async code => {
    localStorage.setItem(KEY, JSON.stringify(stored({ origin: 'submitted' })))
    setupStatus(jobId => json({ job_id: jobId, status: 'not_found' }))
    const hook = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(hook.result.current.failureKind).toBe('monitoring'))
    mockedAuthFetch.mockResolvedValue(json({ detail: 'unverified' }, code))
    const confirm = vi.fn(() => true)
    await act(async () => { await hook.result.current.resubmit(confirm).catch(() => {}) })
    expect(confirm).not.toHaveBeenCalled()
    expect(postCalls()).toHaveLength(0)
    expect(localStorage.getItem(KEY)).not.toBeNull()
  })

  it.each([401, 403, 400, 422, 409])('preserves the original identity and lock if a manual resend POST is rejected with HTTP %s', async code => {
    const original = JSON.stringify(stored({ origin: 'submitted' }))
    localStorage.setItem(KEY, original)
    setupStatus(jobId => json({ job_id: jobId, status: 'not_found' }))
    const hook = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(hook.result.current.failureKind).toBe('monitoring'))
    mockedAuthFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === '/api/scrape' && init?.method === 'POST') {
        expect(JSON.parse(String(init.body))).toMatchObject({ job_id: JOB, operation_id: OPERATION })
        return json({ detail: code === 409 ? 'owner-active-job' : 'rejected' }, code)
      }
      return json({ job_id: JOB, status: 'not_found' })
    })
    let failure: unknown
    await act(async () => { failure = await hook.result.current.resubmit(() => true).catch(error => error) })
    expect(failure).toMatchObject({ safeToRetry: false, jobId: JOB })
    expect(hook.result.current.jobId).toBe(JOB)
    expect(hook.result.current.isExecutionLocked).toBe(true)
    expect(hook.result.current.canRetry).toBe(false)
    expect(localStorage.getItem(KEY)).toBe(original)
    expect(postCalls()).toHaveLength(1)
    expect(await startAndCatch(hook)).toMatchObject({ kind: 'busy' })
    expect(postCalls()).toHaveLength(1)
  })

  it('never resends a history-adopted record with no original operation identity', async () => {
    localStorage.setItem(KEY, JSON.stringify(stored({ origin: 'history' })))
    setupStatus(jobId => json({ job_id: jobId, status: 'not_found' }))
    const hook = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(hook.result.current.failureKind).toBe('monitoring'))
    const confirm = vi.fn(() => true)
    await act(async () => { await hook.result.current.resubmit(confirm) })
    expect(hook.result.current.canResubmit).toBe(false)
    expect(confirm).not.toHaveBeenCalled()
    expect(postCalls()).toHaveLength(0)
  })

  it('requires a fresh confirmation and blocks double-clicked resubmission', async () => {
    localStorage.setItem(KEY, JSON.stringify(stored({ origin: 'submitted' })))
    setupStatus(jobId => json({ job_id: jobId, status: 'not_found' }))
    const hook = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(hook.result.current.failureKind).toBe('monitoring'))
    const preflight = deferred<Response>()
    mockedAuthFetch.mockImplementation(() => preflight.promise)
    const confirm = vi.fn(() => false)
    let first!: Promise<unknown>
    act(() => { first = hook.result.current.resubmit(confirm) })
    await act(async () => { expect(await hook.result.current.resubmit(confirm)).toBeNull() })
    await act(async () => { preflight.resolve(json({ job_id: JOB, status: 'not_found' })); await first })
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(postCalls()).toHaveLength(0)
    expect(localStorage.getItem(KEY)).not.toBeNull()
  })

  it('logs out by detaching monitoring without submitting or deleting the prior owner record', async () => {
    localStorage.setItem(KEY, JSON.stringify(stored()))
    const pending = deferred<Response>()
    setupStatus(() => pending.promise)
    const hook = renderHook(({ ownerUserId }) => useBatchScrape({ ...FAST, ownerUserId }), { initialProps: { ownerUserId: OWNER as string | null } })
    await waitFor(() => expect(hook.result.current.jobId).toBe(JOB))
    hook.rerender({ ownerUserId: null })
    await act(async () => { pending.resolve(json({ job_id: JOB, status: 'completed', result: { races_collected: 5 } })) })
    expect(hook.result.current.jobId).toBeNull()
    expect(hook.result.current.result).toBeNull()
    expect(localStorage.getItem(KEY)).not.toBeNull()
    expect(postCalls()).toHaveLength(0)
  })

  it('ignores stale owner callbacks without changing the new account state', async () => {
    const hook = renderHook(({ ownerUserId }) => useBatchScrape({ ...FAST, ownerUserId }), { initialProps: { ownerUserId: OWNER } })
    const oldReconnect = hook.result.current.reconnect
    const oldResubmit = hook.result.current.resubmit
    const oldStart = hook.result.current.start
    hook.rerender({ ownerUserId: 'owner-b' })
    const raw = JSON.stringify(stored({ origin: 'submitted' }))
    localStorage.setItem(KEY, raw)
    await act(async () => {
      expect(await oldReconnect()).toBeNull()
      expect(await oldResubmit(() => true)).toBeNull()
      expect(await oldStart('2026-01', '2026-02', false).catch(e => e)).toMatchObject({ kind: 'authentication' })
    })
    expect(hook.result.current.status).toBe('idle')
    expect(hook.result.current.jobId).toBeNull()
    expect(hook.result.current.loading).toBe(false)
    expect(hook.result.current.isExecutionLocked).toBe(false)
    expect(localStorage.getItem(KEY)).toBe(raw)
    expect(mockedAuthFetch).not.toHaveBeenCalled()
  })
})
