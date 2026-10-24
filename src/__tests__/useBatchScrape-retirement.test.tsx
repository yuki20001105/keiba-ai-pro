import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { fetchMock } = vi.hoisted(() => ({ fetchMock: vi.fn() }))
vi.mock('@/lib/auth-fetch', () => ({ authFetch: fetchMock }))
import { BATCH_SCRAPE_RETIREMENT_KEY_PREFIX, BATCH_SCRAPE_STORAGE_KEY_PREFIX, useBatchScrape } from '@/hooks/useBatchScrape'
import { retirementCanonicalPayload } from '@/lib/scrape-retirement'
import type { ScrapeRaceScope } from '@/lib/scrape-race-scope'

const OWNER = 'retirement-owner'
const KEY = `${BATCH_SCRAPE_STORAGE_KEY_PREFIX}:${OWNER}`
const JOB = '11111111-1111-4111-8111-111111111111'
const OPERATION = '22222222-2222-4222-8222-222222222222'
const ARCHIVE = `${BATCH_SCRAPE_RETIREMENT_KEY_PREFIX}:${OWNER}:${JOB}`
const FAST = { ownerUserId: OWNER, pollIntervalMs: 1, maxPollAttempts: 4, maxConsecutiveStatusFailures: 1 }
const original = (raceScope: ScrapeRaceScope | undefined = 'all', origin = 'submitted') => ({
  version: 1, ownerUserId: OWNER, jobId: JOB, operationId: OPERATION,
  startDate: '20160101', endDate: '20260930', forceRescrape: false,
  createdAt: '2026-10-12T07:00:00Z', origin,
  ...(raceScope === undefined ? {} : { raceScope }),
})
const json = (payload: unknown, status = 200) => Response.json(payload, { status })
const missing = () => json({ job_id: JOB, status: 'not_found', retirement_supported: true })
const receipt = (raceScope: ScrapeRaceScope | undefined = 'all') => ({
  job_id: JOB, operation_id: OPERATION, status: 'cancelled', retired: true,
  replacement_allowed: true, retirement_supported: true,
  request_payload: retirementCanonicalPayload(original(raceScope)),
})
const retirePosts = () => fetchMock.mock.calls.filter(([url, init]) => url === `/api/scrape/retire/${JOB}` && init?.method === 'POST')
const scrapePosts = () => fetchMock.mock.calls.filter(([url, init]) => url === '/api/scrape' && init?.method === 'POST')
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(r => { resolve = r })
  return { promise, resolve }
}

describe('operation-bound retirement of an unaccepted saved batch', () => {
  beforeEach(() => { localStorage.clear(); fetchMock.mockReset() })

  it.each(['all', 'jra', undefined] as const)('archives original %s scope, tombstones only that tuple and opens an editable draft', async raceScope => {
    // Explicit undefined must remain absent, rather than the helper default.
    const saved = raceScope === undefined ? { ...original('all'), raceScope: undefined } : original(raceScope)
    const raw = JSON.stringify(saved)
    localStorage.setItem(KEY, raw)
    fetchMock.mockImplementation(async (url: string) => url.includes('/retire/') ? json(receipt(raceScope)) : missing())
    const hook = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(hook.result.current.canRetire).toBe(true))
    const confirm = vi.fn(() => true)
    let draft: unknown
    await act(async () => { draft = await hook.result.current.retire(confirm) })
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(draft).toEqual({ startPeriod: '2016-01', endPeriod: '2026-09', raceScope: raceScope ?? 'all' })
    expect(hook.result.current.retiredRequestDraft).toEqual(draft)
    expect(hook.result.current.status).toBe('idle')
    expect(hook.result.current.jobId).toBeNull()
    expect(hook.result.current.isExecutionLocked).toBe(false)
    expect(hook.result.current.canRetire).toBe(false)
    expect(localStorage.getItem(KEY)).toBeNull()
    expect(JSON.parse(localStorage.getItem(ARCHIVE)!)).toMatchObject({ ownerUserId: OWNER, request: JSON.parse(raw),
      receipt: { job_id: JOB, operation_id: OPERATION, retired: true } })
    expect(retirePosts()).toHaveLength(1)
    expect(JSON.parse(String(retirePosts()[0][1].body))).toEqual({
      operation_id: OPERATION, start_date: '20160101', end_date: '20260930', force_rescrape: false, server_batch: true,
      ...(raceScope === undefined ? {} : { race_scope: raceScope }),
    })
    expect(scrapePosts()).toHaveLength(0)
  })

  it('does not offer retirement until the backend advertises the guarded operation', async () => {
    localStorage.setItem(KEY, JSON.stringify(original()))
    fetchMock.mockResolvedValue(json({ job_id: JOB, status: 'not_found' }))
    const hook = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(hook.result.current.canResubmit).toBe(true))
    expect(hook.result.current.canRetire).toBe(false)
    const confirm = vi.fn(() => true)
    await act(async () => { expect(await hook.result.current.retire(confirm)).toBeNull() })
    expect(confirm).not.toHaveBeenCalled()
    expect(retirePosts()).toHaveLength(0)
    expect(localStorage.getItem(KEY)).not.toBeNull()
  })

  it('does not hide a saved request by merely reloading repeatedly', async () => {
    const raw = JSON.stringify(original())
    localStorage.setItem(KEY, raw)
    fetchMock.mockImplementation(() => Promise.resolve(missing()))
    for (let reload = 0; reload < 3; reload++) {
      const hook = renderHook(() => useBatchScrape(FAST))
      await waitFor(() => expect(hook.result.current.canRetire).toBe(true))
      expect(hook.result.current.isExecutionLocked).toBe(true)
      expect(localStorage.getItem(KEY)).toBe(raw)
      hook.unmount()
    }
    expect(retirePosts()).toHaveLength(0)
    expect(scrapePosts()).toHaveLength(0)
  })

  it('uses one immediate supported missing-ledger read for reopening, without waiting ten polls', async () => {
    localStorage.setItem(KEY, JSON.stringify(original()))
    fetchMock.mockResolvedValue(missing())
    const hook = renderHook(() => useBatchScrape({ ownerUserId: OWNER, pollIntervalMs: 3000 }))
    await waitFor(() => expect(hook.result.current.canRetire).toBe(true))
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem(KEY)).not.toBeNull()
  })

  it('labels reopening as observation, not a newly queued acquisition', async () => {
    localStorage.setItem(KEY, JSON.stringify(original()))
    const pending = deferred<Response>()
    fetchMock.mockReturnValue(pending.promise)
    const hook = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(hook.result.current.loading).toBe(true))
    expect(hook.result.current.observationPhase).toBe('monitoring')
    expect(scrapePosts()).toHaveLength(0)
    await act(async () => { pending.resolve(missing()) })
  })

  it('requires explicit confirmation and preserves the exact original after declining', async () => {
    const raw = JSON.stringify(original())
    localStorage.setItem(KEY, raw)
    fetchMock.mockImplementation(() => Promise.resolve(missing()))
    const hook = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(hook.result.current.canRetire).toBe(true))
    const confirm = vi.fn(() => false)
    await act(async () => { expect(await hook.result.current.retire(confirm)).toBeNull() })
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem(KEY)).toBe(raw)
    expect(localStorage.getItem(ARCHIVE)).toBeNull()
    expect(hook.result.current.isExecutionLocked).toBe(true)
    expect(retirePosts()).toHaveLength(0)
  })

  it.each([401, 403, 409, 502])('keeps identity and lock on retirement HTTP %s', async status => {
    const raw = JSON.stringify(original())
    localStorage.setItem(KEY, raw)
    fetchMock.mockImplementation(async (url: string) => url.includes('/retire/') ? json({ detail: 'not allowed' }, status) : missing())
    const hook = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(hook.result.current.canRetire).toBe(true))
    await act(async () => { await hook.result.current.retire(() => true).catch(() => {}) })
    expect(localStorage.getItem(KEY)).toBe(raw)
    expect(localStorage.getItem(ARCHIVE)).toBeNull()
    expect(hook.result.current.isExecutionLocked).toBe(true)
    expect(retirePosts()).toHaveLength(1)
    expect(scrapePosts()).toHaveLength(0)
  })

  it.each(['queued', 'running', 'completed'])('does not retire when preflight detects the original %s job', async status => {
    const raw = JSON.stringify(original())
    localStorage.setItem(KEY, raw)
    fetchMock.mockResolvedValue(missing())
    const hook = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(hook.result.current.canRetire).toBe(true))
    fetchMock.mockResolvedValue(json({ job_id: JOB, status }))
    const confirm = vi.fn(() => true)
    await act(async () => { await hook.result.current.retire(confirm).catch(() => {}) })
    expect(localStorage.getItem(KEY)).toBe(raw)
    expect(hook.result.current.isExecutionLocked).toBe(true)
    expect(confirm).not.toHaveBeenCalled()
    expect(retirePosts()).toHaveLength(0)
  })

  it.each([{ operation_id: JOB }, { replacement_allowed: false }, { status: 'running' },
    { request_payload: { ...retirementCanonicalPayload(original()), race_scope: 'jra' } }])
  ('rejects a mismatched retirement receipt %s without changing local storage', async change => {
    const raw = JSON.stringify(original())
    localStorage.setItem(KEY, raw)
    fetchMock.mockImplementation(async (url: string) => url.includes('/retire/') ? json({ ...receipt(), ...change }) : missing())
    const hook = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(hook.result.current.canRetire).toBe(true))
    await act(async () => { await hook.result.current.retire(() => true).catch(() => {}) })
    expect(localStorage.getItem(KEY)).toBe(raw)
    expect(localStorage.getItem(ARCHIVE)).toBeNull()
    expect(hook.result.current.isExecutionLocked).toBe(true)
  })

  it('recovers a committed retirement after the POST response was lost, using GET and backup only', async () => {
    const raw = JSON.stringify(original())
    localStorage.setItem(KEY, raw)
    let committed = false
    fetchMock.mockImplementation(async (url: string) => {
      if (url.includes('/retire/')) { committed = true; throw new TypeError('response lost') }
      return committed ? json(receipt()) : missing()
    })
    const hook = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(hook.result.current.canRetire).toBe(true))
    await act(async () => { await hook.result.current.retire(() => true).catch(() => {}) })
    expect(localStorage.getItem(KEY)).toBe(raw)
    expect(hook.result.current.isExecutionLocked).toBe(true)
    hook.unmount()
    const reopened = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(reopened.result.current.retiredRequestDraft).not.toBeNull())
    expect(reopened.result.current.status).toBe('idle')
    expect(reopened.result.current.isExecutionLocked).toBe(false)
    expect(localStorage.getItem(KEY)).toBeNull()
    expect(JSON.parse(localStorage.getItem(ARCHIVE)!).request).toEqual(JSON.parse(raw))
    expect(retirePosts()).toHaveLength(1)
    expect(scrapePosts()).toHaveLength(0)
  })

  it('keeps the original until its retirement archive can be saved', async () => {
    const raw = JSON.stringify(original())
    localStorage.setItem(KEY, raw)
    fetchMock.mockImplementation(async (url: string) => url.includes('/retire/') ? json(receipt()) : missing())
    const hook = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(hook.result.current.canRetire).toBe(true))
    const write = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
    try {
      await act(async () => { await hook.result.current.retire(() => true).catch(() => {}) })
      expect(localStorage.getItem(KEY)).toBe(raw)
      expect(hook.result.current.isExecutionLocked).toBe(true)
    } finally { write.mockRestore() }
  })

  it('never deletes another tab\'s different operation with the same job ID', async () => {
    localStorage.setItem(KEY, JSON.stringify(original()))
    fetchMock.mockResolvedValue(missing())
    const hook = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(hook.result.current.canRetire).toBe(true))
    const changed = JSON.stringify({ ...original(), operationId: JOB })
    fetchMock.mockImplementation(async (url: string) => {
      if (url.includes('/retire/')) { localStorage.setItem(KEY, changed); return json(receipt()) }
      return missing()
    })
    await act(async () => { await hook.result.current.retire(() => true).catch(() => {}) })
    expect(localStorage.getItem(KEY)).toBe(changed)
    expect(localStorage.getItem(ARCHIVE)).toBeNull()
    expect(hook.result.current.isExecutionLocked).toBe(true)
  })

  it('does not retire a history-adopted job or another owner\'s original', async () => {
    localStorage.setItem(KEY, JSON.stringify(original('all', 'history')))
    fetchMock.mockResolvedValue(missing())
    const history = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(history.result.current.monitoringIssue).toBe('not_found'))
    expect(history.result.current.canRetire).toBe(false)
    const confirm = vi.fn(() => true)
    await act(async () => { expect(await history.result.current.retire(confirm)).toBeNull() })
    history.unmount()
    const other = renderHook(() => useBatchScrape({ ...FAST, ownerUserId: 'other-owner' }))
    await act(async () => { expect(await other.result.current.retire(confirm)).toBeNull() })
    expect(confirm).not.toHaveBeenCalled()
    expect(retirePosts()).toHaveLength(0)
    expect(localStorage.getItem(KEY)).not.toBeNull()
  })

  it('serializes a different tab\'s resend behind retirement without holding the lock during monitoring', async () => {
    const descriptor = Object.getOwnPropertyDescriptor(navigator, 'locks')
    let tail = Promise.resolve<unknown>(undefined)
    const request = vi.fn((name: string, action: () => unknown) => {
      expect(name).toBe(KEY)
      const current = tail.then(action)
      tail = current.catch(() => undefined)
      return current
    })
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request } })
    try {
      localStorage.setItem(KEY, JSON.stringify(original()))
      const retireResponse = deferred<Response>()
      fetchMock.mockImplementation(async (url: string) => url.includes('/retire/') ? retireResponse.promise : missing())
      const first = renderHook(() => useBatchScrape(FAST))
      const second = renderHook(() => useBatchScrape(FAST))
      await waitFor(() => expect(first.result.current.canRetire && second.result.current.canRetire).toBe(true))
      let retiring!: Promise<unknown>
      let resending!: Promise<unknown>
      act(() => { retiring = first.result.current.retire(() => true) })
      await waitFor(() => expect(retirePosts()).toHaveLength(1))
      act(() => { resending = second.result.current.resubmit(() => true).catch(error => error) })
      await act(async () => { retireResponse.resolve(json(receipt())); await retiring })
      let rejected: unknown
      await act(async () => { rejected = await resending })
      expect(rejected).toMatchObject({ kind: 'monitoring', monitoringIssue: 'job_mismatch' })
      expect(localStorage.getItem(KEY)).toBeNull()
      expect(localStorage.getItem(ARCHIVE)).not.toBeNull()
      expect(scrapePosts()).toHaveLength(0)
      expect(request).toHaveBeenCalledTimes(2)
    } finally {
      if (descriptor) Object.defineProperty(navigator, 'locks', descriptor)
      else Reflect.deleteProperty(navigator, 'locks')
    }
  })

  it('does not modify the prior owner\'s saved request if logout occurs while retirement is pending', async () => {
    const raw = JSON.stringify(original())
    localStorage.setItem(KEY, raw)
    const pending = deferred<Response>()
    fetchMock.mockImplementation(async (url: string) => url.includes('/retire/') ? pending.promise : missing())
    const hook = renderHook(({ ownerUserId }) => useBatchScrape({ ...FAST, ownerUserId }), { initialProps: { ownerUserId: OWNER } })
    await waitFor(() => expect(hook.result.current.canRetire).toBe(true))
    let retiring!: Promise<unknown>
    act(() => { retiring = hook.result.current.retire(() => true).catch(error => error) })
    await waitFor(() => expect(retirePosts()).toHaveLength(1))
    hook.rerender({ ownerUserId: 'other-owner' })
    let rejected: unknown
    await act(async () => { pending.resolve(json(receipt())); rejected = await retiring })
    expect(rejected).toMatchObject({ kind: 'client_stop' })
    expect(localStorage.getItem(KEY)).toBe(raw)
    expect(localStorage.getItem(ARCHIVE)).toBeNull()
    expect(hook.result.current.status).toBe('idle')
    expect(hook.result.current.jobId).toBeNull()
    expect(scrapePosts()).toHaveLength(0)
  })

  it('keeps waiting for delayed acceptance after the initial submission even if retirement is supported', async () => {
    let polls = 0
    fetchMock.mockImplementation(async (url: string) => {
      const saved = JSON.parse(localStorage.getItem(KEY)!)
      if (url === '/api/scrape') return json({ job_id: saved.jobId, status: 'queued' })
      return ++polls === 1 ? json({ job_id: saved.jobId, status: 'not_found', retirement_supported: true })
        : json({ job_id: saved.jobId, status: 'completed', result: { races_collected: 2 } })
    })
    const hook = renderHook(() => useBatchScrape({ ...FAST, maxConsecutiveStatusFailures: 3 }))
    await act(async () => { await hook.result.current.start('2016-01', '2026-09', false) })
    expect(hook.result.current.status).toBe('completed')
    expect(hook.result.current.result?.races_collected).toBe(2)
    expect(polls).toBe(2)
    expect(retirePosts()).toHaveLength(0)
    expect(scrapePosts()).toHaveLength(1)
  })

  it('does not replace a corrupt or different retirement archive', async () => {
    const raw = JSON.stringify(original())
    localStorage.setItem(KEY, raw)
    localStorage.setItem(ARCHIVE, '{invalid archived receipt')
    fetchMock.mockImplementation(async (url: string) => url.includes('/retire/') ? json(receipt()) : missing())
    const hook = renderHook(() => useBatchScrape(FAST))
    await waitFor(() => expect(hook.result.current.canRetire).toBe(true))
    await act(async () => { await hook.result.current.retire(() => true).catch(() => {}) })
    expect(localStorage.getItem(KEY)).toBe(raw)
    expect(localStorage.getItem(ARCHIVE)).toBe('{invalid archived receipt')
    expect(hook.result.current.isExecutionLocked).toBe(true)
  })
})
