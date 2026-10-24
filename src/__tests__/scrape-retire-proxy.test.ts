import { NextRequest } from 'next/server'
import { beforeEach, describe, expect, it, vi } from 'vitest'
const { auth, fetchMock } = vi.hoisted(() => ({ auth: vi.fn(), fetchMock: vi.fn() }))
vi.mock('@/lib/server-auth', () => ({ verifyRequestAuth: auth }))
const JOB = '11111111-1111-4111-8111-111111111111'
const OP = '22222222-2222-4222-8222-222222222222'
const body = { operation_id: OP, start_date: '20160101', end_date: '20260930', force_rescrape: false, server_batch: true, race_scope: 'all' }
const request = (payload: unknown = body) => new NextRequest(`http://localhost/api/scrape/retire/${JOB}`, {
  method: 'POST', body: JSON.stringify(payload), headers: { Authorization: 'Bearer browser-token', 'Content-Type': 'application/json' },
})
describe('saved scrape retirement proxy', () => {
  beforeEach(() => { vi.resetModules(); vi.clearAllMocks(); vi.stubGlobal('fetch', fetchMock)
    auth.mockResolvedValue({ ok: true, context: { token: 'verified-admin-token' } }) })
  it.each([401, 403, 503])('fails closed on authorization HTTP %s', async status => {
    auth.mockResolvedValue({ ok: false, status, detail: 'authorization unavailable' })
    const { POST } = await import('@/app/api/scrape/retire/[jobId]/route')
    const r = await POST(request(), { params: Promise.resolve({ jobId: JOB }) })
    expect(r.status).toBe(status); expect(fetchMock).not.toHaveBeenCalled()
    expect(auth).toHaveBeenCalledWith(expect.anything(), { requireAdminMode: true })
    expect(r.headers.get('Cache-Control')).toBe('no-store')
  })
  it.each([null, {}, { ...body, owner_user_id: 'another-owner' }, { ...body, server_batch: false },
    { ...body, force_rescrape: 'false' }, { ...body, operation_id: 'short' }, { ...body, start_date: '20260230' },
    { ...body, start_date: '20270101' }, { ...body, race_scope: 'unapproved' },
  ])('rejects a malformed or expanded saved request without upstream access %#', async payload => {
    const { POST } = await import('@/app/api/scrape/retire/[jobId]/route')
    expect((await POST(request(payload), { params: Promise.resolve({ jobId: JOB }) })).status).toBe(400)
    expect(fetchMock).not.toHaveBeenCalled()
  })
  it('rejects a partial path ID and invalid JSON', async () => {
    const { POST } = await import('@/app/api/scrape/retire/[jobId]/route')
    expect((await POST(request(), { params: Promise.resolve({ jobId: 'short' }) })).status).toBe(400)
    expect((await POST(new NextRequest('http://localhost/api/scrape/retire/'+JOB, { method: 'POST', body: '{' }),
      { params: Promise.resolve({ jobId: JOB }) })).status).toBe(400)
    expect(fetchMock).not.toHaveBeenCalled()
  })
  it.each([200, 409, 503])('forwards only the verified identity and preserves upstream status %s', async status => {
    const receipt = status === 200 ? { job_id: JOB, operation_id: OP, status: 'cancelled', retired: true, replacement_allowed: true }
      : { detail: 'job-state-conflict' }
    fetchMock.mockResolvedValue(Response.json(receipt, { status }))
    const { POST } = await import('@/app/api/scrape/retire/[jobId]/route')
    const r = await POST(request(), { params: Promise.resolve({ jobId: JOB }) })
    expect(r.status).toBe(status); expect(await r.json()).toEqual(receipt)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toMatch(new RegExp(`/api/scrape/retire/${JOB}$`))
    expect(init.headers).toEqual({ 'Content-Type': 'application/json', Authorization: 'Bearer verified-admin-token' })
    expect(JSON.parse(init.body)).toEqual(body); expect(init.cache).toBe('no-store')
  })
  it('preserves legacy omitted all-scope binding rather than rewriting it', async () => {
    const legacy = { operation_id: OP, start_date: '20160101', end_date: '20260930', force_rescrape: false, server_batch: true }
    fetchMock.mockResolvedValue(Response.json({ job_id: JOB }))
    const { POST } = await import('@/app/api/scrape/retire/[jobId]/route')
    await POST(request(legacy), { params: Promise.resolve({ jobId: JOB }) })
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(legacy)
  })
  it.each(['invalid_json', 'transport'])('never turns %s into release proof', async mode => {
    if (mode === 'transport') fetchMock.mockRejectedValue(new Error('offline'))
    else fetchMock.mockResolvedValue(new Response('bad-json', { status: 200 }))
    const { POST } = await import('@/app/api/scrape/retire/[jobId]/route')
    expect((await POST(request(), { params: Promise.resolve({ jobId: JOB }) })).status).toBe(502)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
