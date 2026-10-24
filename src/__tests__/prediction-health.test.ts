import { afterEach, describe, expect, it, vi } from 'vitest'
import { GET } from '@/app/api/health/route'

describe('prediction runtime handshake', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('forwards fresh worker capabilities without caching', async () => {
    const body = { status: 'ok', prediction_runtime: { version: 'bounded-cpu-v1', max_workers: 4 } }
    const fetchMock = vi.fn().mockResolvedValue(Response.json(body))
    vi.stubGlobal('fetch', fetchMock)
    const result = await GET()
    expect(await result.json()).toEqual(body)
    expect(result.headers.get('Cache-Control')).toBe('no-store')
    expect(fetchMock.mock.calls[0][1].cache).toBe('no-store')
  })

  it('marks an unavailable runtime offline without caching', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
    const result = await GET()
    expect(result.status).toBe(503)
    expect(await result.json()).toEqual({ status: 'offline' })
    expect(result.headers.get('Cache-Control')).toBe('no-store')
  })
})
