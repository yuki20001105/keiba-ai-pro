import { NextRequest } from 'next/server'
import { beforeEach, describe, expect, test, vi } from 'vitest'

const verifyRequestAuthMock = vi.fn()
vi.mock('@/lib/server-auth', () => ({
  verifyRequestAuth: (...args: unknown[]) => verifyRequestAuthMock(...args),
}))

const fetchMock = vi.fn()
const JOB_ID = '11111111-1111-4111-8111-111111111111'
const VERIFIED_TOKEN = ['verified', 'admin', 'token'].join('-')

function request(path: string) {
  return new NextRequest(`http://localhost${path}`, {
    headers: { Authorization: 'Bearer browser-token' },
  })
}

describe('scrape job status/history proxy authorization', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    verifyRequestAuthMock.mockResolvedValue({
      ok: true,
      context: { token: VERIFIED_TOKEN },
    })
    vi.stubGlobal('fetch', fetchMock)
  })

  test('requires verified Admin before status backend access', async () => {
    verifyRequestAuthMock.mockResolvedValueOnce({ ok: false, status: 403, detail: 'Admin role required' })
    const { GET } = await import('@/app/api/scrape/status/[jobId]/route')
    const response = await GET(request(`/api/scrape/status/${JOB_ID}`), {
      params: Promise.resolve({ jobId: JOB_ID }),
    })

    expect(response.status).toBe(403)
    expect(response.headers.get('Cache-Control')).toBe('no-store')
    expect(fetchMock).not.toHaveBeenCalled()
    expect(verifyRequestAuthMock).toHaveBeenCalledWith(expect.anything(), { requireAdmin: true })
  })

  test('rejects shortened or malformed job ids without backend access', async () => {
    const { GET } = await import('@/app/api/scrape/status/[jobId]/route')
    for (const jobId of ['11111111', 'not-a-uuid', `${JOB_ID}/extra`]) {
      const response = await GET(request(`/api/scrape/status/${encodeURIComponent(jobId)}`), {
        params: Promise.resolve({ jobId }),
      })
      expect(response.status).toBe(400)
    }
    expect(fetchMock).not.toHaveBeenCalled()
  })

  test('forwards a canonical UUID with only the verified bearer token', async () => {
    const backendPayload = {
      job_id: JOB_ID,
      status: 'running',
      request_payload: {
        start_date: '20200101',
        end_date: '20200131',
        dry_run: false,
        force_rescrape: false,
      },
      created_at: '2026-09-11T17:27:04Z',
      updated_at: '2026-09-11T17:28:04Z',
    }
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(backendPayload), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    const { GET } = await import('@/app/api/scrape/status/[jobId]/route')
    const response = await GET(request(`/api/scrape/status/${JOB_ID}`), {
      params: Promise.resolve({ jobId: JOB_ID }),
    })

    expect(response.status).toBe(200)
    expect(await response.json()).toEqual(backendPayload)
    expect(response.headers.get('Cache-Control')).toBe('no-store')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toMatch(new RegExp(`/api/scrape/status/${JOB_ID}$`))
    expect(init.headers).toEqual({ Authorization: `Bearer ${VERIFIED_TOKEN}` })
    expect(init.cache).toBe('no-store')
  })

  test('requires Admin and validates history limit before backend access', async () => {
    const { GET } = await import('@/app/api/scrape/history/route')
    verifyRequestAuthMock.mockResolvedValueOnce({ ok: false, status: 401, detail: 'Authentication required' })
    let response = await GET(request('/api/scrape/history'))
    expect(response.status).toBe(401)
    expect(fetchMock).not.toHaveBeenCalled()

    for (const limit of ['0', '101', '1.5', 'abc']) {
      response = await GET(request(`/api/scrape/history?limit=${limit}`))
      expect(response.status).toBe(400)
    }
    expect(fetchMock).not.toHaveBeenCalled()
  })

  test('forwards valid history request and propagates backend status safely', async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'temporarily unavailable' }), {
      status: 503,
      headers: { 'Content-Type': 'application/json' },
    }))
    const { GET } = await import('@/app/api/scrape/history/route')
    const response = await GET(request('/api/scrape/history?limit=25'))

    expect(response.status).toBe(503)
    expect(response.headers.get('Cache-Control')).toBe('no-store')
    expect(await response.json()).toEqual({ detail: 'temporarily unavailable' })
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toMatch(/\/api\/scrape\/history\?limit=25$/)
    expect(init.headers).toEqual({ Authorization: `Bearer ${VERIFIED_TOKEN}` })
  })

  test('fails closed when the backend response is not JSON', async () => {
    fetchMock.mockResolvedValueOnce(new Response('not-json', { status: 200 }))
    const { GET } = await import('@/app/api/scrape/history/route')
    const response = await GET(request('/api/scrape/history'))
    expect(response.status).toBe(502)
    expect(await response.json()).toEqual({ detail: 'Scrape history service returned an invalid response' })
  })

  test('requires a short-lived Admin verification grant before cancellation backend access', async () => {
    verifyRequestAuthMock.mockResolvedValueOnce({
      ok: false,
      status: 403,
      detail: 'Admin mode verification required',
    })
    const { POST } = await import('@/app/api/scrape/cancel/[jobId]/route')
    const response = await POST(request(`/api/scrape/cancel/${JOB_ID}`), {
      params: Promise.resolve({ jobId: JOB_ID }),
    })

    expect(response.status).toBe(403)
    expect(response.headers.get('Cache-Control')).toBe('no-store')
    expect(fetchMock).not.toHaveBeenCalled()
    expect(verifyRequestAuthMock).toHaveBeenCalledWith(expect.anything(), { requireAdminMode: true })
  })

  test('rejects a malformed cancellation job id before backend access', async () => {
    const { POST } = await import('@/app/api/scrape/cancel/[jobId]/route')
    const response = await POST(request('/api/scrape/cancel/not-a-job'), {
      params: Promise.resolve({ jobId: 'not-a-job' }),
    })

    expect(response.status).toBe(400)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  test('forwards cancellation with only the verified bearer token and preserves accepted status', async () => {
    const backendPayload = {
      job_id: JOB_ID,
      status: 'cancelling',
      cancel_requested_at: '2026-09-12T08:00:00Z',
      duplicate: false,
    }
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(backendPayload), {
      status: 202,
      headers: { 'Content-Type': 'application/json' },
    }))
    const { POST } = await import('@/app/api/scrape/cancel/[jobId]/route')
    const response = await POST(request(`/api/scrape/cancel/${JOB_ID}`), {
      params: Promise.resolve({ jobId: JOB_ID }),
    })

    expect(response.status).toBe(202)
    expect(response.headers.get('Cache-Control')).toBe('no-store')
    expect(await response.json()).toEqual(backendPayload)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toMatch(new RegExp(`/api/scrape/cancel/${JOB_ID}$`))
    expect(init).toMatchObject({
      method: 'POST',
      headers: { Authorization: `Bearer ${VERIFIED_TOKEN}` },
      cache: 'no-store',
    })
    expect(init.body).toBeUndefined()
  })

  test('propagates cancellation conflicts and fails closed on invalid backend JSON', async () => {
    const { POST } = await import('@/app/api/scrape/cancel/[jobId]/route')
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'job-already-terminal' }), {
      status: 409,
      headers: { 'Content-Type': 'application/json' },
    }))
    let response = await POST(request(`/api/scrape/cancel/${JOB_ID}`), {
      params: Promise.resolve({ jobId: JOB_ID }),
    })
    expect(response.status).toBe(409)
    expect(await response.json()).toEqual({ detail: 'job-already-terminal' })

    fetchMock.mockResolvedValueOnce(new Response('not-json', { status: 200 }))
    response = await POST(request(`/api/scrape/cancel/${JOB_ID}`), {
      params: Promise.resolve({ jobId: JOB_ID }),
    })
    expect(response.status).toBe(502)
    expect(await response.json()).toEqual({
      detail: 'Scrape cancellation service returned an invalid response',
    })
  })
})
