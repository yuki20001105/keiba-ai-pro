import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useScrape } from '@/hooks/useScrape'

const { authFetch } = vi.hoisted(() => ({ authFetch: vi.fn() }))
vi.mock('@/lib/auth-fetch', () => ({ authFetch }))
const capabilities = {
  safety_policy_version: 'normal-acquisition-safe-v1',
  minimum_provider_interval_seconds: 3,
  halt_http400: true,
  preserve_saved_values: true,
  max_concurrent_http_requests: 1,
  prediction_card_policy_version: 'prediction-card-cached-v1',
}
beforeEach(() => { authFetch.mockReset() })
afterEach(cleanup)

describe('prediction-list acquisition', () => {
  it('requests cached-only cards after the running API confirms the safe fast path', async () => {
    authFetch.mockImplementation(async (url: string) => url === '/api/scrape/health'
      ? Response.json({ scrape_capabilities: capabilities })
      : Response.json({ job_id: 'cards-job' }))
    const { result } = renderHook(() => useScrape())
    await act(async () => {
      await result.current.startScrape({ startDate: '20261107', endDate: '20261107', horseDetails: 'cached_only' })
    })
    const start = authFetch.mock.calls.find(([url]) => url === '/api/scrape')!
    expect(authFetch.mock.calls[0][0]).toBe('/api/scrape/health')
    expect(JSON.parse(start[1].body)).toMatchObject({ horse_details: 'cached_only', force_rescrape: false })
    expect(result.current.jobId).toBe('cards-job')
  })

  it('does not silently start the slow job on an old API', async () => {
    authFetch.mockResolvedValue(Response.json({ scrape_capabilities: { ...capabilities, prediction_card_policy_version: undefined } }))
    const { result } = renderHook(() => useScrape())
    await act(async () => {
      await result.current.startScrape({ startDate: '20261107', endDate: '20261107', horseDetails: 'cached_only' })
    })
    expect(result.current.status).toBe('error')
    expect(result.current.message).toMatch(/再起動/)
    expect(authFetch.mock.calls.some(([url]) => url === '/api/scrape')).toBe(false)
  })

  it('keeps full acquisition requests unchanged', async () => {
    authFetch.mockResolvedValue(Response.json({ job_id: 'full-job' }))
    const { result } = renderHook(() => useScrape())
    await act(async () => {
      await result.current.startScrape({ startDate: '20240101', endDate: '20240101' })
    })
    expect(authFetch).toHaveBeenCalledTimes(1)
    expect(JSON.parse(authFetch.mock.calls[0][1].body)).toEqual({
      start_date: '20240101', end_date: '20240101', force_rescrape: false,
    })
  })

  it('never submits an old date after reset while the capability request is pending', async () => {
    let resolve!: (value: Response) => void
    authFetch.mockImplementation(() => new Promise<Response>(yes => { resolve = yes }))
    const { result } = renderHook(() => useScrape())
    let started!: Promise<string | null>
    act(() => {
      started = result.current.startScrape({ startDate: '20261107', endDate: '20261107', horseDetails: 'cached_only' })
    })
    act(() => result.current.reset())
    await act(async () => {
      resolve(Response.json({ scrape_capabilities: capabilities }))
      await started
    })
    expect(authFetch.mock.calls.some(([url]) => url === '/api/scrape')).toBe(false)
    expect(result.current.status).toBe('idle')
  })
})
