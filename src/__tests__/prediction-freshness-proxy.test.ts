import { NextRequest } from 'next/server'
import { beforeEach, describe, expect, it, vi } from 'vitest'
const { auth, fetchMock } = vi.hoisted(() => ({ auth: vi.fn(), fetchMock: vi.fn() }))
vi.mock('@/lib/server-auth', () => ({ verifyRequestAuth: auth }))
const request = (query = '') => new NextRequest(`http://localhost/api/prediction-freshness${query}`,
  { headers: { Authorization: 'Bearer browser-token' } })

describe('prediction freshness proxy', () => {
  beforeEach(() => {
    vi.clearAllMocks(); vi.stubGlobal('fetch', fetchMock)
    auth.mockResolvedValue({ ok: true, context: { token: 'verified-token' } })
  })
  it.each([401, 403, 503])('fails closed on auth %s', async status => {
    auth.mockResolvedValue({ ok: false, status, detail: 'Authentication unavailable' })
    const { GET } = await import('@/app/api/prediction-freshness/route')
    const response = await GET(request())
    expect(response.status).toBe(status); expect(fetchMock).not.toHaveBeenCalled()
    expect(response.headers.get('Cache-Control')).toBe('no-store')
  })
  it.each(['?model_id=../private', '?model_id=*', '?model_id=', '?model_id=a&model_id=b', '?model_id='+ 'x'.repeat(201)])(
    'rejects invalid model input %s', async query => {
      const { GET } = await import('@/app/api/prediction-freshness/route')
      expect((await GET(request(query))).status).toBe(400)
      expect(fetchMock).not.toHaveBeenCalled()
    })
  it.each([200, 404, 503])('forwards verified token and preserves upstream %s', async status => {
    const body = status === 200 ? { schema_version: 'prediction-freshness-v1', capabilities: { saved_db_recompute: true } }
      : { detail: 'model unavailable' }
    fetchMock.mockResolvedValue(Response.json(body, { status }))
    const { GET } = await import('@/app/api/prediction-freshness/route')
    const response = await GET(request('?model_id=model_safe_2026'))
    expect(response.status).toBe(status); expect(await response.json()).toEqual(body)
    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/api\/prediction-freshness\?model_id=model_safe_2026$/),
      expect.objectContaining({ cache: 'no-store', headers: { Authorization: 'Bearer verified-token' } }))
    expect(response.headers.get('Cache-Control')).toBe('no-store')
  })
  it.each(['invalid', 'transport'])('does not invent freshness on %s failure', async mode => {
    if (mode === 'transport') fetchMock.mockRejectedValue(new Error('secret-service-url'))
    else fetchMock.mockResolvedValue(new Response('invalid-json'))
    const { GET } = await import('@/app/api/prediction-freshness/route')
    const response = await GET(request())
    expect(response.status).toBe(502)
    expect(JSON.stringify(await response.json())).not.toContain('secret-service-url')
  })
})
