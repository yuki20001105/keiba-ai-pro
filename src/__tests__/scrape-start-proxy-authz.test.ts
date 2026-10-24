import { NextRequest } from 'next/server'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { verifyRequestAuthMock, fetchMock } = vi.hoisted(() => ({
  verifyRequestAuthMock: vi.fn(),
  fetchMock: vi.fn(),
}))
vi.mock('@/lib/server-auth', () => ({ verifyRequestAuth: verifyRequestAuthMock }))
import { POST } from '@/app/api/scrape/route'

const jobId = '11111111-1111-4111-8111-111111111111'
const operationId = '22222222-2222-4222-8222-222222222222'
const payload = {
  start_date: '20160101', end_date: '20260930', force_rescrape: false,
  server_batch: true, job_id: jobId, operation_id: operationId,
}
function request(body: unknown = payload) {
  return new NextRequest('http://localhost/api/scrape', {
    method: 'POST', headers: { Authorization: 'Bearer unverified-browser-value' },
    body: JSON.stringify(body),
  })
}

describe('durable scrape submission proxy', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('fetch', fetchMock)
    verifyRequestAuthMock.mockResolvedValue({ ok: true, context: { token: 'verified-token' } })
  })
  afterEach(() => vi.unstubAllGlobals())

  it.each([401, 403, 503])('blocks submission when current authorization fails with %s', async status => {
    verifyRequestAuthMock.mockResolvedValueOnce({ ok: false, status, detail: 'Access denied' })
    const response = await POST(request())
    expect(response.status).toBe(status)
    expect(response.headers.get('Cache-Control')).toBe('no-store')
    expect(fetchMock).not.toHaveBeenCalled()
    expect(verifyRequestAuthMock).toHaveBeenCalledWith(expect.anything(), { requireAdmin: true })
  })

  it('preserves the preallocated parent identity and upstream accepted status', async () => {
    const accepted = { job_id: jobId, operation_id: operationId, status: 'queued' }
    fetchMock.mockResolvedValueOnce(Response.json(accepted, { status: 202 }))
    const response = await POST(request())
    expect(response.status).toBe(202)
    expect(await response.json()).toEqual(accepted)
    expect(response.headers.get('Cache-Control')).toBe('no-store')
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toMatch(/\/api\/scrape\/start$/)
    expect(init.headers).toEqual({ 'Content-Type': 'application/json', Authorization: 'Bearer verified-token' })
    expect(JSON.parse(init.body)).toEqual(payload)
    expect(init.cache).toBe('no-store')
  })

  it('preserves ownership and idempotency conflicts without retrying', async () => {
    fetchMock.mockResolvedValueOnce(Response.json({ detail: 'owner-active-job' }, { status: 409 }))
    const response = await POST(request())
    expect(response.status).toBe(409)
    expect(await response.json()).toEqual({ detail: 'owner-active-job' })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it.each([null, [], 'invalid'])('rejects non-object JSON (%j)', async value => {
    expect((await POST(request(value))).status).toBe(400)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('rejects malformed JSON before backend submission', async () => {
    const req = new NextRequest('http://localhost/api/scrape', { method: 'POST', body: '{' })
    expect((await POST(req)).status).toBe(400)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('does not retry or claim rejection after an ambiguous transport failure', async () => {
    fetchMock.mockRejectedValueOnce(new Error('timeout'))
    const response = await POST(request())
    expect(response.status).toBe(502)
    expect((await response.json()).detail).toContain('check the submitted job status')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('fails closed on an invalid response instead of reporting completion', async () => {
    fetchMock.mockResolvedValueOnce(new Response('<html>proxy error</html>'))
    expect((await POST(request())).status).toBe(502)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
