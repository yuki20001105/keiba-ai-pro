import { NextRequest } from 'next/server'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const verifyRequestAuthMock = vi.hoisted(() => vi.fn())

vi.mock('@/lib/backend-url', () => ({
  ML_API_URL: 'https://ml.example.test',
}))
vi.mock('@/lib/server-auth', () => ({
  verifyRequestAuth: (...args: unknown[]) => verifyRequestAuthMock(...args),
}))

import { GET as GET_CAPABILITY } from '@/app/api/ml/train/capability/route'
import { POST as POST_START } from '@/app/api/ml/train/start/route'
import { GET as GET_STATUS } from '@/app/api/ml/train/status/[job_id]/route'

const originalFetch = global.fetch
const originalAppEnv = process.env.APP_ENV
const originalTrainingFlag = process.env.MODEL_TRAINING_LOCAL_ENABLED
const JOB_ID = '11111111-1111-4111-8111-111111111111'

function request(path: string, method = 'GET'): NextRequest {
  return new NextRequest(`http://localhost${path}`, {
    method,
    headers: {
      Authorization: 'Bearer browser-supplied-token',
      ...(method === 'POST' ? { 'Content-Type': 'application/json' } : {}),
    },
    ...(method === 'POST' ? { body: JSON.stringify({ target: 'win' }) } : {}),
  })
}

function restoreEnvironment(name: string, value: string | undefined) {
  if (value === undefined) delete process.env[name]
  else process.env[name] = value
}

describe('local model training upstream boundary', () => {
  beforeEach(() => {
    process.env.APP_ENV = 'development'
    process.env.MODEL_TRAINING_LOCAL_ENABLED = 'true'
    global.fetch = vi.fn()
    verifyRequestAuthMock.mockReset()
  })

  afterEach(() => {
    global.fetch = originalFetch
    restoreEnvironment('APP_ENV', originalAppEnv)
    restoreEnvironment('MODEL_TRAINING_LOCAL_ENABLED', originalTrainingFlag)
  })

  it('never forwards start, capability, or status to a non-loopback API', async () => {
    const start = await POST_START(request('/api/ml/train/start', 'POST'))
    const capability = await GET_CAPABILITY(request('/api/ml/train/capability'))
    const status = await GET_STATUS(request(`/api/ml/train/status/${JOB_ID}`), {
      params: Promise.resolve({ job_id: JOB_ID }),
    })

    expect(start.status).toBe(409)
    expect(await start.json()).toMatchObject({ code: 'local-training-upstream-required' })
    expect(capability.status).toBe(409)
    expect(await capability.json()).toEqual({ enabled: false, reason: 'backend-not-local' })
    expect(status.status).toBe(409)
    expect(await status.json()).toEqual({ detail: 'Local model training API is not loopback' })
    expect(verifyRequestAuthMock).not.toHaveBeenCalled()
    expect(global.fetch).not.toHaveBeenCalled()
  })
})
