import { NextRequest } from 'next/server'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const verifyRequestAuthMock = vi.hoisted(() => vi.fn())
vi.mock('@/lib/server-auth', () => ({
  verifyRequestAuth: (...args: unknown[]) => verifyRequestAuthMock(...args),
}))

import { PUT } from '@/app/api/models/[id]/activate/route'

const originalFetch = global.fetch
const originalAppEnv = process.env.APP_ENV
const originalActivationFlag = process.env.MODEL_ACTIVATION_LOCAL_ENABLED
const VERIFIED_TOKEN = 'verified-admin-token'

function request(host = 'localhost'): NextRequest {
  return new NextRequest(`http://${host}/api/models/candidate/activate`, {
    method: 'PUT',
    headers: { Authorization: 'Bearer browser-supplied-token' },
  })
}

function restoreEnvironment(name: string, value: string | undefined) {
  if (value === undefined) delete process.env[name]
  else process.env[name] = value
}

describe('legacy model activation route guard', () => {
  beforeEach(() => {
    global.fetch = vi.fn()
    verifyRequestAuthMock.mockReset()
    verifyRequestAuthMock.mockResolvedValue({
      ok: true,
      context: { token: VERIFIED_TOKEN },
    })
    delete process.env.MODEL_ACTIVATION_LOCAL_ENABLED
  })

  afterEach(() => {
    global.fetch = originalFetch
    restoreEnvironment('APP_ENV', originalAppEnv)
    restoreEnvironment('MODEL_ACTIVATION_LOCAL_ENABLED', originalActivationFlag)
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
      expect(verifyRequestAuthMock).not.toHaveBeenCalled()
      expect(global.fetch).not.toHaveBeenCalled()
    },
  )

  it('does not forward local activation without exact opt-in', async () => {
    process.env.APP_ENV = 'local'
    const response = await PUT(request() as never, { params: Promise.resolve({ id: 'candidate' }) })
    expect(response.status).toBe(409)
    expect(verifyRequestAuthMock).not.toHaveBeenCalled()
    expect(global.fetch).not.toHaveBeenCalled()
  })

  it('rejects non-loopback activation before authentication', async () => {
    process.env.APP_ENV = 'development'
    process.env.MODEL_ACTIVATION_LOCAL_ENABLED = 'true'

    const response = await PUT(request('example.test'), { params: Promise.resolve({ id: 'candidate' }) })

    expect(response.status).toBe(403)
    expect(await response.json()).toEqual({ detail: 'Local access required' })
    expect(verifyRequestAuthMock).not.toHaveBeenCalled()
    expect(global.fetch).not.toHaveBeenCalled()
  })

  it('requires current-password Admin mode', async () => {
    process.env.APP_ENV = 'development'
    process.env.MODEL_ACTIVATION_LOCAL_ENABLED = 'true'
    verifyRequestAuthMock.mockResolvedValueOnce({
      ok: false,
      status: 403,
      detail: 'Admin mode verification required',
    })

    const response = await PUT(request(), { params: Promise.resolve({ id: 'candidate' }) })

    expect(response.status).toBe(403)
    expect(verifyRequestAuthMock).toHaveBeenCalledWith(expect.anything(), { requireAdminMode: true })
    expect(global.fetch).not.toHaveBeenCalled()
  })

  it('forwards only the verified Admin token in explicit local mode', async () => {
    process.env.APP_ENV = 'test'
    process.env.MODEL_ACTIVATION_LOCAL_ENABLED = 'TRUE'
    vi.mocked(global.fetch).mockResolvedValue(new Response(JSON.stringify({
      success: true,
      active_model_id: 'candidate',
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))

    const response = await PUT(request(), { params: Promise.resolve({ id: 'candidate' }) })

    expect(response.status).toBe(200)
    expect(verifyRequestAuthMock).toHaveBeenCalledWith(expect.anything(), { requireAdminMode: true })
    expect(global.fetch).toHaveBeenCalledOnce()
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/models/candidate/activate'),
      expect.objectContaining({
        method: 'PUT',
        headers: { Authorization: `Bearer ${VERIFIED_TOKEN}` },
        cache: 'no-store',
        redirect: 'error',
      }),
    )
    expect(JSON.stringify(vi.mocked(global.fetch).mock.calls[0])).not.toContain('browser-supplied-token')
  })

  it('rejects malformed model IDs without forwarding', async () => {
    process.env.APP_ENV = 'test'
    process.env.MODEL_ACTIVATION_LOCAL_ENABLED = 'true'

    const response = await PUT(request(), { params: Promise.resolve({ id: '../candidate' }) })

    expect(response.status).toBe(400)
    expect(global.fetch).not.toHaveBeenCalled()
  })
})
