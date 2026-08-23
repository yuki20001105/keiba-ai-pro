import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PUT } from '@/app/api/models/[id]/activate/route'

const originalFetch = global.fetch

function request(): Request {
  return new Request('http://localhost/api/models/candidate/activate', {
    method: 'PUT',
    headers: { Authorization: 'Bearer test-token' },
  })
}

describe('legacy model activation route guard', () => {
  beforeEach(() => {
    global.fetch = vi.fn()
    delete process.env.MODEL_ACTIVATION_LOCAL_ENABLED
  })

  afterEach(() => {
    global.fetch = originalFetch
    delete process.env.APP_ENV
    delete process.env.MODEL_ACTIVATION_LOCAL_ENABLED
  })

  it.each(['staging', 'production', 'prod', '', 'unknown'])(
    'never forwards activation in deployed or unknown environment %s',
    async environment => {
      process.env.APP_ENV = environment
      process.env.MODEL_ACTIVATION_LOCAL_ENABLED = 'true'

      const response = await PUT(request() as never, { params: Promise.resolve({ id: 'candidate' }) })
      const body = await response.json()

      expect(response.status).toBe(409)
      expect(response.headers.get('Cache-Control')).toBe('no-store')
      expect(body.code).toBe('separate-activation-approval-required')
      expect(global.fetch).not.toHaveBeenCalled()
    },
  )

  it('does not forward local activation without exact opt-in', async () => {
    process.env.APP_ENV = 'local'
    const response = await PUT(request() as never, { params: Promise.resolve({ id: 'candidate' }) })
    expect(response.status).toBe(409)
    expect(global.fetch).not.toHaveBeenCalled()
  })

  it('keeps explicit local/test compatibility behind the FastAPI Admin boundary', async () => {
    process.env.APP_ENV = 'test'
    process.env.MODEL_ACTIVATION_LOCAL_ENABLED = 'TRUE'
    vi.mocked(global.fetch).mockResolvedValue(new Response(JSON.stringify({
      success: true,
      active_model_id: 'candidate',
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))

    const response = await PUT(request() as never, { params: Promise.resolve({ id: 'candidate' }) })

    expect(response.status).toBe(200)
    expect(global.fetch).toHaveBeenCalledOnce()
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/models/candidate/activate'),
      expect.objectContaining({
        method: 'PUT',
        headers: { Authorization: 'Bearer test-token' },
      }),
    )
  })
})
