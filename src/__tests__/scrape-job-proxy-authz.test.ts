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
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ job_id: JOB_ID, status: 'running' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    const { GET } = await import('@/app/api/scrape/status/[jobId]/route')
    const response = await GET(request(`/api/scrape/status/${JOB_ID}`), {
      params: Promise.resolve({ jobId: JOB_ID }),
    })

    expect(response.status).toBe(200)
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
})
