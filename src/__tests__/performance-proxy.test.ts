import { NextRequest } from 'next/server'
import { beforeEach, describe, expect, test, vi } from 'vitest'

const verify = vi.fn()
vi.mock('@/lib/server-auth', () => ({ verifyRequestAuth: (...args: unknown[]) => verify(...args) }))
const fetchMock = vi.fn()
const request = (path: string, body?: unknown) => new NextRequest(`http://localhost${path}`, {
  headers: { Authorization: 'Bearer unverified', 'Content-Type': 'application/json' },
  ...(body === undefined ? {} : { method: 'POST', body: JSON.stringify(body) }),
})
const selection = { snapshot_id: 'owner-snapshot', mode: 'paper', tickets: [{ bet_type: '単勝', combination: '5', horse_numbers: [5], stake: 100 }] }

describe('personal performance proxies', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    verify.mockResolvedValue({ ok: true, context: { token: 'verified-token' } })
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ records: [], summary: {} }), { status: 200 }))
  })

  test('requires verified auth before any backend access', async () => {
    verify.mockResolvedValue({ ok: false, status: 401, detail: 'Authentication required' })
    const { GET } = await import('@/app/api/performance/route')
    const { POST } = await import('@/app/api/performance/records/route')
    for (const res of [await GET(request('/api/performance')), await POST(request('/api/performance/records', selection))]) {
      expect(res.status).toBe(401)
      expect(res.headers.get('Cache-Control')).toBe('no-store')
    }
    expect(fetchMock).not.toHaveBeenCalled()
  })

  test('forwards verified token and only allowed query fields without caching', async () => {
    const { GET } = await import('@/app/api/performance/route')
    const res = await GET(request('/api/performance?mode=actual&limit=25&date_from=20260101&date_to=20261010&owner=victim'))
    expect(res.status).toBe(200)
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toMatch(/\/api\/performance\?mode=actual&limit=25&date_from=20260101&date_to=20261010$/)
    expect(init.headers.Authorization).toBe('Bearer verified-token')
    expect(init.cache).toBe('no-store')
  })

  test('rejects malformed filters before backend', async () => {
    const { GET } = await import('@/app/api/performance/route')
    for (const query of ['mode=all', 'limit=201', 'limit=0', 'limit=1.5', 'date_from=20260230', 'date_from=20260101&date_to=20250101', 'date_from=']) {
      expect((await GET(request(`/api/performance?${query}`))).status).toBe(400)
    }
    expect(fetchMock).not.toHaveBeenCalled()
  })

  test('actual records require explicit purchase confirmation', async () => {
    const { POST } = await import('@/app/api/performance/records/route')
    expect((await POST(request('/api/performance/records', { ...selection, mode: 'actual' }))).status).toBe(400)
    expect(fetchMock).not.toHaveBeenCalled()
    expect((await POST(request('/api/performance/records', { ...selection, mode: 'actual', confirmed: true }))).status).toBe(200)
    const [, init] = fetchMock.mock.calls[0]
    expect(JSON.parse(init.body).confirmed).toBe(true)
    expect(init.headers.Authorization).toBe('Bearer verified-token')
  })

  test('rejects metadata, owner, payout injection and coerced stakes', async () => {
    const { POST } = await import('@/app/api/performance/records/route')
    const bodies = [
      { ...selection, owner: 'victim' }, { ...selection, actual_return: 5000 },
      { ...selection, tickets: [{ ...selection.tickets[0], probability: 1 }] },
      { ...selection, tickets: [{ ...selection.tickets[0], stake: '100' }] },
      { ...selection, tickets: [{ ...selection.tickets[0], stake: 150 }] },
    ]
    for (const body of bodies) expect((await POST(request('/api/performance/records', body))).status).toBe(400)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  test('backend rejection stays rejected and ambiguous non-JSON responses never claim saved', async () => {
    const { POST } = await import('@/app/api/performance/records/route')
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ detail: '期限切れ' }), { status: 400 }))
    expect((await POST(request('/api/performance/records', selection))).status).toBe(400)
    fetchMock.mockResolvedValueOnce(new Response('not-json', { status: 200 }))
    const res = await POST(request('/api/performance/records', selection))
    expect(res.status).toBe(502)
    expect(res.headers.get('Cache-Control')).toBe('no-store')
  })
})
