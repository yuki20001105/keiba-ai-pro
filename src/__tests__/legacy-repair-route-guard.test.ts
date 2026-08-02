import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { POST as repair } from '@/app/api/scrape/repair/[race_id]/route'
import { POST as rescrape } from '@/app/api/scrape/rescrape-incomplete/route'

const originalFetch = global.fetch

function request(pathname: string): Request {
  return new Request(`http://localhost${pathname}`, {
    method: 'POST',
    headers: { Authorization: 'Bearer test-token' },
  })
}

async function callRepair(): Promise<Response> {
  return repair(
    request('/api/scrape/repair/202601010101') as never,
    { params: Promise.resolve({ race_id: '202601010101' }) },
  )
}

async function callRescrape(): Promise<Response> {
  return rescrape(request('/api/scrape/rescrape-incomplete?limit=10') as never)
}

describe('legacy repair and rescrape proxy guards', () => {
  beforeEach(() => {
    global.fetch = vi.fn()
    delete process.env.PHASE3N_ALLOW_LEGACY_SCRAPE_WRITES
  })

  afterEach(() => {
    global.fetch = originalFetch
    delete process.env.APP_ENV
    delete process.env.PHASE3N_ALLOW_LEGACY_SCRAPE_WRITES
  })

  it.each(['staging', 'stage', 'production', 'prod', 'prd', 'live', '', 'unknown'])(
    'blocks both proxies before network access in environment %s even with opt-in',
    async environment => {
      process.env.APP_ENV = environment
      process.env.PHASE3N_ALLOW_LEGACY_SCRAPE_WRITES = 'true'

      const [repairResponse, rescrapeResponse] = await Promise.all([callRepair(), callRescrape()])
      expect(repairResponse.status).toBe(503)
      expect(rescrapeResponse.status).toBe(503)
      expect((await repairResponse.json()).code).toBe('legacy-repair-disabled')
      expect((await rescrapeResponse.json()).code).toBe('legacy-rescrape-disabled')
      expect(global.fetch).not.toHaveBeenCalled()
    },
  )

  it('blocks local mode unless the write opt-in is explicit', async () => {
    process.env.APP_ENV = 'local'
    const [repairResponse, rescrapeResponse] = await Promise.all([callRepair(), callRescrape()])
    expect(repairResponse.status).toBe(503)
    expect(rescrapeResponse.status).toBe(503)
    expect(global.fetch).not.toHaveBeenCalled()
  })

  it.each(['1', 'true', 'TRUE', 'yes', 'on'])(
    'preserves explicit local compatibility for value %s behind FastAPI Admin auth',
    async flag => {
      process.env.APP_ENV = 'test'
      process.env.PHASE3N_ALLOW_LEGACY_SCRAPE_WRITES = flag
      vi.mocked(global.fetch).mockImplementation(async url => new Response(JSON.stringify({
        success: true,
        forwarded_to: String(url),
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))

      const [repairResponse, rescrapeResponse] = await Promise.all([callRepair(), callRescrape()])
      expect(repairResponse.status).toBe(200)
      expect(rescrapeResponse.status).toBe(200)
      expect(global.fetch).toHaveBeenCalledTimes(2)
      for (const call of vi.mocked(global.fetch).mock.calls) {
        expect(call[1]).toEqual(expect.objectContaining({
          method: 'POST',
          headers: { Authorization: 'Bearer test-token' },
        }))
      }
    },
  )
})
