import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

const verifyRequestAuthMock = vi.fn()

vi.mock('@/lib/server-auth', () => ({
  verifyRequestAuth: (...args: unknown[]) => verifyRequestAuthMock(...args),
}))

const spawnMock = vi.fn()
function childProcessMockFactory() {
  return {
  __esModule: true,
  spawn: (...args: unknown[]) => spawnMock(...args),
  default: {
    spawn: (...args: unknown[]) => spawnMock(...args),
  },
  }
}
vi.mock('child_process', childProcessMockFactory)
vi.mock('node:child_process', childProcessMockFactory)

const fsReadFileMock = vi.fn()
const fsUnlinkMock = vi.fn()
const fsMkdirMock = vi.fn()
const fsRmMock = vi.fn()
const fsStatMock = vi.fn()
function fsPromisesMockFactory() {
  return {
    __esModule: true,
    readFile: (...args: unknown[]) => fsReadFileMock(...args),
    unlink: (...args: unknown[]) => fsUnlinkMock(...args),
    mkdtemp: (...args: unknown[]) => fsMkdirMock(...args),
    rm: (...args: unknown[]) => fsRmMock(...args),
    stat: (...args: unknown[]) => fsStatMock(...args),
    default: {
      readFile: (...args: unknown[]) => fsReadFileMock(...args),
      unlink: (...args: unknown[]) => fsUnlinkMock(...args),
      mkdtemp: (...args: unknown[]) => fsMkdirMock(...args),
      rm: (...args: unknown[]) => fsRmMock(...args),
      stat: (...args: unknown[]) => fsStatMock(...args),
    },
  }
}
vi.mock('fs/promises', fsPromisesMockFactory)
vi.mock('node:fs/promises', fsPromisesMockFactory)

class MockStream {
  private listeners: Record<string, Array<(data: Buffer) => void>> = {}

  on(event: string, cb: (data: Buffer) => void) {
    this.listeners[event] = this.listeners[event] || []
    this.listeners[event].push(cb)
    return this
  }

  emit(event: string, data: string) {
    const list = this.listeners[event] || []
    for (const cb of list) cb(Buffer.from(data))
  }
}

class MockChild {
  stdout = new MockStream()
  stderr = new MockStream()
  private closeListeners: Array<(code: number | null) => void> = []
  private errorListeners: Array<(err: Error) => void> = []
  kill = vi.fn()

  on(event: string, cb: (arg: any) => void) {
    if (event === 'close') this.closeListeners.push(cb)
    if (event === 'error') this.errorListeners.push(cb)
    return this
  }

  emitClose(code: number | null) {
    for (const cb of this.closeListeners) cb(code)
  }

  emitError(err: Error) {
    for (const cb of this.errorListeners) cb(err)
  }
}

function makeRequest(body: unknown, headers?: Record<string, string>) {
  return new Request('http://localhost/api/scrape/targeted-refetch-plan', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(headers || {}),
    },
    body: JSON.stringify(body),
  })
}

function makeRawRequest(rawBody: string, headers?: Record<string, string>) {
  return new Request('http://localhost/api/scrape/targeted-refetch-plan', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(headers || {}),
    },
    body: rawBody,
  })
}

function validReport(target = 'all') {
  return {
    target,
    verdict: 'warn',
    verdict_reason: 'targeted-refetch-dry-run',
    p0_total_count: 20,
    refetch_candidate_count: 3,
    unique_url_count: 3,
    race_result_url_count: 1,
    race_detail_url_count: 1,
    horse_detail_url_count: 0,
    pedigree_url_count: 1,
    excluded_schema_review_count: 0,
    excluded_domain_allowed_count: 0,
    excluded_metadata_repair_count: 0,
    excluded_cache_available_count: 2,
    reparse_candidate_count: 2,
    estimated_http_request_count: 3,
    estimated_runtime_seconds: 4.2,
    sample_urls: {
      result_page: [
        {
          url: 'https://db.netkeiba.com/race/202601010101/',
          url_type: 'result_page',
          race_id: '202601010101',
          horse_id: null,
          reason: 'true-missing',
          column: 'finish_position',
          priority: 'P0',
          source: 'db',
          recommended_next_action: 'targeted refetch dry-run',
        },
      ],
      race_detail: [
        {
          url: 'https://db.netkeiba.com/race/202601010102/',
          url_type: 'race_detail',
          race_id: '202601010102',
          horse_id: null,
          reason: 'consistency:race_without_horse_data',
          column: '(check)',
          priority: 'P0',
          source: 'db',
          recommended_next_action: 'targeted refetch dry-run',
        },
      ],
      horse_detail: [],
      pedigree: [
        {
          url: 'https://db.netkeiba.com/horse/ped/2018101234/',
          url_type: 'pedigree',
          race_id: null,
          horse_id: '2018101234',
          reason: 'true-missing',
          column: 'sire',
          priority: 'P1',
          source: 'db',
          recommended_next_action: 'targeted refetch dry-run',
        },
      ],
    },
    recommended_next_actions: ['a', 'b'],
    safety_flags: {
      read_only: true,
      no_db_write: true,
      no_http_access: true,
      no_scrape_execute: true,
      no_upsert: true,
      no_force_refresh_execute: true,
    },
  }
}

function realPlannerShapeReport(target = 'all') {
  return {
    ...validReport(target),
    timestamp: '2026-07-18T02:00:00Z',
    p0_plan_total_count: 12,
    audit_p0_true_missing_count: 4,
    rate_limit_policy: 'conservative',
    cache_diagnosis_note: 'cache-only review',
    input_audit: 'C:\\Users\\test\\AppData\\Local\\Temp\\planner\\audit.json',
    input_p0_plan: 'C:\\Users\\test\\AppData\\Local\\Temp\\planner\\p0_plan.json',
    input_cache_diagnosis: 'C:\\Users\\test\\AppData\\Local\\Temp\\planner\\cache.json',
  }
}

async function waitForSpawnCalls(expectedCalls: number): Promise<void> {
  for (let i = 0; i < 20; i += 1) {
    if (spawnMock.mock.calls.length >= expectedCalls) return
    await Promise.resolve()
  }
}

describe('targeted-refetch-plan route', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    verifyRequestAuthMock.mockResolvedValue({ ok: true, context: {} })
    fsMkdirMock.mockResolvedValue('C:/tmp/targeted-refetch-plan-abc')
    fsUnlinkMock.mockResolvedValue(undefined)
    fsRmMock.mockResolvedValue(undefined)
    fsStatMock.mockResolvedValue({ size: 512 })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  test('returns 401/403/503 from verifyRequestAuth', async () => {
    const { POST } = await import('@/app/api/scrape/targeted-refetch-plan/route')

    verifyRequestAuthMock.mockResolvedValueOnce({ ok: false, status: 401, detail: 'Authentication required' })
    let res = await POST(makeRequest({ target: 'all', max_targets: 10 }) as any)
    expect(res.status).toBe(401)

    verifyRequestAuthMock.mockResolvedValueOnce({ ok: false, status: 403, detail: 'Premium or admin role required' })
    res = await POST(makeRequest({ target: 'all', max_targets: 10 }) as any)
    expect(res.status).toBe(403)

    verifyRequestAuthMock.mockResolvedValueOnce({ ok: false, status: 503, detail: 'Authorization backend unavailable' })
    res = await POST(makeRequest({ target: 'all', max_targets: 10 }) as any)
    expect(res.status).toBe(503)
  })

  test('rejects unknown/path input and max_targets boundaries', async () => {
    const { POST } = await import('@/app/api/scrape/targeted-refetch-plan/route')

    let res = await POST(makeRequest({ foo: 1 }) as any)
    expect(res.status).toBe(400)

    res = await POST(makeRequest({ target: '../all', max_targets: 10 }) as any)
    expect(res.status).toBe(400)

    res = await POST(makeRequest({ target: 'all', max_targets: 0 }) as any)
    expect(res.status).toBe(400)

    res = await POST(makeRequest({ target: 'all', max_targets: 51 }) as any)
    expect(res.status).toBe(400)

    res = await POST(makeRequest({ target: 'all', max_targets: 1 }) as any)
    expect(res.status).not.toBe(400)
  })

  test('rejects malformed JSON and empty body without spawning planner', async () => {
    const { POST } = await import('@/app/api/scrape/targeted-refetch-plan/route')

    let res = await POST(makeRawRequest('{') as any)
    expect(res.status).toBe(400)

    res = await POST(makeRawRequest('') as any)
    expect(res.status).toBe(400)

    expect(spawnMock).toHaveBeenCalledTimes(0)
  })

  test('rejects null/array/primitive JSON bodies without spawning planner', async () => {
    const { POST } = await import('@/app/api/scrape/targeted-refetch-plan/route')

    let res = await POST(makeRequest(null) as any)
    expect(res.status).toBe(400)
    res = await POST(makeRequest([1, 2]) as any)
    expect(res.status).toBe(400)
    res = await POST(makeRequest(1) as any)
    expect(res.status).toBe(400)

    expect(spawnMock).toHaveBeenCalledTimes(0)
  })

  test('spawn uses shell=false and validates response contract', async () => {
    const child = new MockChild()
    spawnMock.mockImplementation(() => child)
    fsReadFileMock.mockResolvedValue(JSON.stringify(realPlannerShapeReport('race')))

    const { POST } = await import('@/app/api/scrape/targeted-refetch-plan/route')
    const promise = POST(makeRequest({ target: 'race', max_targets: 10 }) as any)
    await waitForSpawnCalls(1)
    child.emitClose(0)
    const res = await promise

    expect(spawnMock).toHaveBeenCalled()
    const spawnOptions = spawnMock.mock.calls[0][2] as Record<string, unknown>
    expect(spawnOptions.shell).toBe(false)

    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body.dry_run).toBe(true)
    expect(body.read_only).toBe(true)
    expect(body.execution_enabled).toBe(false)
    expect((body.plan as any).target).toBe('race')
    expect((body.plan as any).input_audit).toBeUndefined()
    expect((body.plan as any).input_p0_plan).toBeUndefined()
    expect((body.plan as any).input_cache_diagnosis).toBeUndefined()
    expect((body.plan as any).timestamp).toBeUndefined()
    expect((body.plan as any).rate_limit_policy).toBeUndefined()
    expect((body.plan as any).output).toBeUndefined()
    expect(JSON.stringify(body)).not.toContain('C:\\Users')
    expect(JSON.stringify(body)).not.toContain('AppData\\Local\\Temp')
  })

  test('returns 502 for malformed numeric values / target mismatch / missing safety flag', async () => {
    const { POST } = await import('@/app/api/scrape/targeted-refetch-plan/route')

    const child = new MockChild()
    spawnMock.mockImplementation(() => child)

    fsReadFileMock.mockResolvedValueOnce(JSON.stringify({ ...validReport(), p0_total_count: -1 }))
    let promise = POST(makeRequest({ target: 'all', max_targets: 10 }) as any)
    await waitForSpawnCalls(1)
    child.emitClose(0)
    let res = await promise
    expect(res.status).toBe(502)

    const child2 = new MockChild()
    spawnMock.mockImplementationOnce(() => child2)
    fsReadFileMock.mockResolvedValueOnce(JSON.stringify({ ...validReport('race') }))
    promise = POST(makeRequest({ target: 'all', max_targets: 10 }) as any)
    await waitForSpawnCalls(2)
    child2.emitClose(0)
    res = await promise
    expect(res.status).toBe(502)

    const brokenFlags = validReport('all')
    ;(brokenFlags.safety_flags as any).no_http_access = false
    const child3 = new MockChild()
    spawnMock.mockImplementationOnce(() => child3)
    fsReadFileMock.mockResolvedValueOnce(JSON.stringify(brokenFlags))
    promise = POST(makeRequest({ target: 'all', max_targets: 10 }) as any)
    await waitForSpawnCalls(3)
    child3.emitClose(0)
    res = await promise
    expect(res.status).toBe(502)
  })

  test('enforces sample URL limit', async () => {
    const report = validReport('all')
    report.sample_urls.result_page.push({ ...report.sample_urls.result_page[0], url: 'https://db.netkeiba.com/race/202601010199/' })

    const child = new MockChild()
    spawnMock.mockImplementation(() => child)
    fsReadFileMock.mockResolvedValue(JSON.stringify(report))

    const { POST } = await import('@/app/api/scrape/targeted-refetch-plan/route')
    const promise = POST(makeRequest({ target: 'all', max_targets: 1 }) as any)
    await waitForSpawnCalls(1)
    child.emitClose(0)
    const res = await promise
    expect(res.status).toBe(502)
  })

  test('cleanup on planner error and timeout', async () => {
    const { POST } = await import('@/app/api/scrape/targeted-refetch-plan/route')

    const child = new MockChild()
    spawnMock.mockImplementationOnce(() => child)
    const p1 = POST(makeRequest({ target: 'all', max_targets: 10 }) as any)
    await waitForSpawnCalls(1)
    child.emitClose(1)
    const r1 = await p1
    expect(r1.status).toBe(500)
    expect(fsUnlinkMock).toHaveBeenCalled()
    expect(fsRmMock).toHaveBeenCalledWith('C:/tmp/targeted-refetch-plan-abc', {
      recursive: true,
      force: true,
    })

    vi.useFakeTimers()
    const child2 = new MockChild()
    spawnMock.mockImplementationOnce(() => child2)
    const p2 = POST(makeRequest({ target: 'all', max_targets: 10 }) as any)
    await waitForSpawnCalls(2)
    await vi.advanceTimersByTimeAsync(120_001)
    child2.emitClose(null)
    const r2 = await p2
    expect(r2.status).toBe(502)
    expect(child2.kill).toHaveBeenCalled()
    expect(fsUnlinkMock).toHaveBeenCalled()
    expect(fsRmMock).toHaveBeenCalledWith('C:/tmp/targeted-refetch-plan-abc', {
      recursive: true,
      force: true,
    })
  })

  test('does not leak server filesystem path in error response', async () => {
    const child = new MockChild()
    spawnMock.mockImplementation(() => child)
    fsReadFileMock.mockResolvedValue('{not-json')

    const { POST } = await import('@/app/api/scrape/targeted-refetch-plan/route')
    const promise = POST(makeRequest({ target: 'all', max_targets: 10 }) as any)
    await waitForSpawnCalls(1)
    child.emitClose(0)
    const res = await promise
    const body = await res.json()

    expect(res.status).toBe(502)
    expect(JSON.stringify(body)).not.toContain('C:/')
    expect(JSON.stringify(body)).not.toContain('\\')
    expect(fsRmMock).toHaveBeenCalledWith('C:/tmp/targeted-refetch-plan-abc', {
      recursive: true,
      force: true,
    })
  })

  test('cleanup runs on spawn error path', async () => {
    const child = new MockChild()
    spawnMock.mockImplementation(() => child)

    const { POST } = await import('@/app/api/scrape/targeted-refetch-plan/route')
    const promise = POST(makeRequest({ target: 'all', max_targets: 10 }) as any)
    await waitForSpawnCalls(1)
    child.emitError(new Error('spawn failed'))
    const res = await promise

    expect(res.status).toBe(500)
    expect(fsRmMock).toHaveBeenCalledWith('C:/tmp/targeted-refetch-plan-abc', {
      recursive: true,
      force: true,
    })
  })

  test('rejects oversized or empty report before readFile', async () => {
    const { POST } = await import('@/app/api/scrape/targeted-refetch-plan/route')

    const child = new MockChild()
    spawnMock.mockImplementationOnce(() => child)
    fsStatMock.mockResolvedValueOnce({ size: 2 * 1024 * 1024 + 1 })

    const p1 = POST(makeRequest({ target: 'all', max_targets: 10 }) as any)
    await waitForSpawnCalls(1)
    child.emitClose(0)
    const r1 = await p1
    expect(r1.status).toBe(502)
    expect(fsReadFileMock).not.toHaveBeenCalled()

    const child2 = new MockChild()
    spawnMock.mockImplementationOnce(() => child2)
    fsStatMock.mockResolvedValueOnce({ size: 0 })

    const p2 = POST(makeRequest({ target: 'all', max_targets: 10 }) as any)
    await waitForSpawnCalls(2)
    child2.emitClose(0)
    const r2 = await p2
    expect(r2.status).toBe(502)
    expect(fsRmMock).toHaveBeenCalledWith('C:/tmp/targeted-refetch-plan-abc', {
      recursive: true,
      force: true,
    })
  })

  test('rejects report containing surfaced filesystem path attacks', async () => {
    const child = new MockChild()
    spawnMock.mockImplementation(() => child)
    const reportWindows = validReport('all')
    ;(reportWindows.sample_urls.result_page[0] as any).reason = 'path=C:\\secret\\data.json'
    fsReadFileMock.mockResolvedValueOnce(JSON.stringify(reportWindows))

    const { POST } = await import('@/app/api/scrape/targeted-refetch-plan/route')
    let promise = POST(makeRequest({ target: 'all', max_targets: 10 }) as any)
    await waitForSpawnCalls(1)
    child.emitClose(0)
    let res = await promise
    expect(res.status).toBe(502)

    const child2 = new MockChild()
    spawnMock.mockImplementationOnce(() => child2)
    const reportUnix = validReport('all')
    ;(reportUnix.sample_urls.result_page[0] as any).source = 'source=/etc/passwd'
    fsReadFileMock.mockResolvedValueOnce(JSON.stringify(reportUnix))
    promise = POST(makeRequest({ target: 'all', max_targets: 10 }) as any)
    await waitForSpawnCalls(2)
    child2.emitClose(0)
    res = await promise
    expect(res.status).toBe(502)

    const child3 = new MockChild()
    spawnMock.mockImplementationOnce(() => child3)
    const reportFileUri = validReport('all')
    ;(reportFileUri.sample_urls.result_page[0] as any).source = 'value=file:///tmp/source'
    fsReadFileMock.mockResolvedValueOnce(JSON.stringify(reportFileUri))
    promise = POST(makeRequest({ target: 'all', max_targets: 10 }) as any)
    await waitForSpawnCalls(3)
    child3.emitClose(0)
    res = await promise
    expect(res.status).toBe(502)

    const child4 = new MockChild()
    spawnMock.mockImplementationOnce(() => child4)
    const reportUnc = validReport('all')
    ;(reportUnc.sample_urls.result_page[0] as any).source = 'note=\\\\server\\share\\file'
    fsReadFileMock.mockResolvedValueOnce(JSON.stringify(reportUnc))
    promise = POST(makeRequest({ target: 'all', max_targets: 10 }) as any)
    await waitForSpawnCalls(4)
    child4.emitClose(0)
    res = await promise
    expect(res.status).toBe(502)

    const child5 = new MockChild()
    spawnMock.mockImplementationOnce(() => child5)
    const reportHome = validReport('all')
    ;(reportHome.sample_urls.result_page[0] as any).recommended_next_action = 'path=~/secret'
    fsReadFileMock.mockResolvedValueOnce(JSON.stringify(reportHome))
    promise = POST(makeRequest({ target: 'all', max_targets: 10 }) as any)
    await waitForSpawnCalls(5)
    child5.emitClose(0)
    res = await promise
    expect(res.status).toBe(502)

    const child6 = new MockChild()
    spawnMock.mockImplementationOnce(() => child6)
    const reportTraversal = validReport('all')
    ;(reportTraversal.sample_urls.result_page[0] as any).recommended_next_action = '../relative-secret'
    fsReadFileMock.mockResolvedValueOnce(JSON.stringify(reportTraversal))
    promise = POST(makeRequest({ target: 'all', max_targets: 10 }) as any)
    await waitForSpawnCalls(6)
    child6.emitClose(0)
    res = await promise
    expect(res.status).toBe(502)

    const child7 = new MockChild()
    spawnMock.mockImplementationOnce(() => child7)
    const reportParenWindows = validReport('all')
    ;(reportParenWindows.sample_urls.result_page[0] as any).reason = '(C:\\secret\\data.json)'
    fsReadFileMock.mockResolvedValueOnce(JSON.stringify(reportParenWindows))
    promise = POST(makeRequest({ target: 'all', max_targets: 10 }) as any)
    await waitForSpawnCalls(7)
    child7.emitClose(0)
    res = await promise
    expect(res.status).toBe(502)

    const child8 = new MockChild()
    spawnMock.mockImplementationOnce(() => child8)
    const safeText = validReport('all')
    ;(safeText.sample_urls.result_page[0] as any).recommended_next_action = 'date/race_id 分割で段階実行を検討'
    ;(safeText.sample_urls.result_page[0] as any).reason = 'race detail/result page'
    ;(safeText.sample_urls.result_page[0] as any).source = 'targeted refetch dry-run'
    fsReadFileMock.mockResolvedValueOnce(JSON.stringify(safeText))
    promise = POST(makeRequest({ target: 'all', max_targets: 10 }) as any)
    await waitForSpawnCalls(8)
    child8.emitClose(0)
    res = await promise
    expect(res.status).toBe(200)
  })

  test('enforces single-flight and blocks second concurrent planner request', async () => {
    const child = new MockChild()
    spawnMock.mockImplementation(() => child)
    fsReadFileMock.mockResolvedValue(JSON.stringify(validReport('all')))

    const { POST } = await import('@/app/api/scrape/targeted-refetch-plan/route')
    const firstPromise = POST(makeRequest({ target: 'all', max_targets: 10 }) as any)
    await waitForSpawnCalls(1)

    const second = await POST(makeRequest({ target: 'all', max_targets: 10 }) as any)
    expect(second.status).toBe(429)
    expect(spawnMock).toHaveBeenCalledTimes(1)

    child.emitClose(0)
    const first = await firstPromise
    expect(first.status).toBe(200)
  })
})
