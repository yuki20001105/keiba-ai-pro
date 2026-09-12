import { NextRequest } from 'next/server'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { isLoopbackUrl } from '@/lib/legacy-local-policy'

const verifyRequestAuthMock = vi.hoisted(() => vi.fn())
vi.mock('@/lib/server-auth', () => ({
  verifyRequestAuth: (...args: unknown[]) => verifyRequestAuthMock(...args),
}))

import { GET as GET_CAPABILITY } from '@/app/api/ml/train/capability/route'
import { POST } from '@/app/api/ml/train/start/route'
import { GET as GET_STATUS } from '@/app/api/ml/train/status/[job_id]/route'

const originalFetch = global.fetch
const originalAppEnv = process.env.APP_ENV
const originalTrainingFlag = process.env.MODEL_TRAINING_LOCAL_ENABLED
const VERIFIED_TOKEN = 'verified-admin-token'
const JOB_ID = '11111111-1111-4111-8111-111111111111'

function startRequest(
  host = 'localhost',
  body: unknown = { target: 'win' },
): NextRequest {
  return new NextRequest(`http://${host}/api/ml/train/start`, {
    method: 'POST',
    headers: {
      Authorization: 'Bearer browser-supplied-token',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  })
}

function capabilityRequest(host = 'localhost'): NextRequest {
  return new NextRequest(`http://${host}/api/ml/train/capability`, {
    headers: { Authorization: 'Bearer browser-supplied-token' },
  })
}

function statusRequest(jobId = JOB_ID): NextRequest {
  return new NextRequest(`http://localhost/api/ml/train/status/${jobId}`, {
    headers: { Authorization: 'Bearer browser-supplied-token' },
  })
}

function restoreEnvironment(name: string, value: string | undefined) {
  if (value === undefined) delete process.env[name]
  else process.env[name] = value
}

describe('local Admin model training route guard', () => {
  beforeEach(() => {
    global.fetch = vi.fn()
    verifyRequestAuthMock.mockReset()
    verifyRequestAuthMock.mockResolvedValue({
      ok: true,
      context: { token: VERIFIED_TOKEN },
    })
    delete process.env.MODEL_TRAINING_LOCAL_ENABLED
  })

  afterEach(() => {
    global.fetch = originalFetch
    restoreEnvironment('APP_ENV', originalAppEnv)
    restoreEnvironment('MODEL_TRAINING_LOCAL_ENABLED', originalTrainingFlag)
  })

  it.each(['staging', 'production', 'prod', '', 'unknown'])(
    'never enables direct training in deployed or unknown environment %s',
    async environment => {
      process.env.APP_ENV = environment
      process.env.MODEL_TRAINING_LOCAL_ENABLED = 'true'

      const startResponse = await POST(startRequest())
      const capabilityResponse = await GET_CAPABILITY(capabilityRequest())

      expect(startResponse.status).toBe(409)
      expect(startResponse.headers.get('Cache-Control')).toBe('no-store')
      expect((await startResponse.json()).code).toBe('approval-bound-model-training-required')
      expect(capabilityResponse.status).toBe(409)
      expect(await capabilityResponse.json()).toEqual({
        enabled: false,
        reason: 'local-training-disabled',
      })
      expect(verifyRequestAuthMock).not.toHaveBeenCalled()
      expect(global.fetch).not.toHaveBeenCalled()
    },
  )

  it('does not enable local training without the exact opt-in flag', async () => {
    process.env.APP_ENV = 'development'
    process.env.MODEL_TRAINING_LOCAL_ENABLED = '1'

    const response = await POST(startRequest())

    expect(response.status).toBe(409)
    expect(verifyRequestAuthMock).not.toHaveBeenCalled()
    expect(global.fetch).not.toHaveBeenCalled()
  })

  it.each([
    'http://localhost:8000',
    'https://127.0.0.1:8443/',
    'http://[::1]:8000',
  ])('accepts the loopback API URL %s', value => {
    expect(isLoopbackUrl(value)).toBe(true)
  })

  it.each([
    'https://ml.example.test',
    'http://127.0.0.1.example.test:8000',
    'http://0.0.0.0:8000',
    'ftp://127.0.0.1/model',
    'http://user:password@127.0.0.1:8000',
    'http://127.0.0.1:8000/base',
  ])('rejects the non-canonical local API URL %s', value => {
    expect(isLoopbackUrl(value)).toBe(false)
  })

  it('rejects a non-loopback request even when the local flag is enabled', async () => {
    process.env.APP_ENV = 'development'
    process.env.MODEL_TRAINING_LOCAL_ENABLED = 'true'

    const response = await POST(startRequest('example.test'))

    expect(response.status).toBe(403)
    expect(await response.json()).toEqual({ detail: 'Local access required' })
    expect(verifyRequestAuthMock).not.toHaveBeenCalled()
    expect(global.fetch).not.toHaveBeenCalled()
  })

  it('requires the current-password Admin mode grant before starting a job', async () => {
    process.env.APP_ENV = 'development'
    process.env.MODEL_TRAINING_LOCAL_ENABLED = 'true'
    verifyRequestAuthMock.mockResolvedValueOnce({
      ok: false,
      status: 403,
      detail: 'Admin mode verification required',
    })

    const response = await POST(startRequest())

    expect(response.status).toBe(403)
    expect(await response.json()).toEqual({ detail: 'Admin mode verification required' })
    expect(verifyRequestAuthMock).toHaveBeenCalledWith(expect.anything(), { requireAdminMode: true })
    expect(global.fetch).not.toHaveBeenCalled()
  })

  it('checks backend capability with only the verified Admin token', async () => {
    process.env.APP_ENV = 'development'
    process.env.MODEL_TRAINING_LOCAL_ENABLED = 'TRUE'
    vi.mocked(global.fetch).mockResolvedValueOnce(new Response(JSON.stringify({
      enabled: true,
      reason: null,
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))

    const response = await GET_CAPABILITY(capabilityRequest())

    expect(response.status).toBe(200)
    expect(await response.json()).toEqual({ enabled: true, reason: null })
    expect(verifyRequestAuthMock).toHaveBeenCalledWith(expect.anything(), { requireAdminMode: true })
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/train/capability'),
      expect.objectContaining({
        headers: { Authorization: `Bearer ${VERIFIED_TOKEN}` },
        cache: 'no-store',
        redirect: 'error',
      }),
    )
  })

  it('starts local training with only the verified Admin token', async () => {
    process.env.APP_ENV = 'test'
    process.env.MODEL_TRAINING_LOCAL_ENABLED = 'TRUE'
    vi.mocked(global.fetch).mockResolvedValueOnce(new Response(JSON.stringify({
      job_id: JOB_ID,
      status: 'queued',
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))

    const response = await POST(startRequest())

    expect(response.status).toBe(200)
    expect(verifyRequestAuthMock).toHaveBeenCalledWith(expect.anything(), { requireAdminMode: true })
    expect(global.fetch).toHaveBeenCalledOnce()
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/train/start'),
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ Authorization: `Bearer ${VERIFIED_TOKEN}` }),
        redirect: 'error',
      }),
    )
    expect(JSON.stringify(vi.mocked(global.fetch).mock.calls[0])).not.toContain('browser-supplied-token')
  })

  it('allowlists training input and always disables the synchronous writer mode', async () => {
    process.env.APP_ENV = 'development'
    process.env.MODEL_TRAINING_LOCAL_ENABLED = 'true'
    vi.mocked(global.fetch).mockResolvedValueOnce(new Response(JSON.stringify({
      job_id: JOB_ID,
      status: 'queued',
    }), {
      status: 202,
      headers: { 'Content-Type': 'application/json' },
    }))

    const response = await POST(startRequest('localhost', {
      target: 'win',
      model_type: 'lightgbm',
      test_size: 0.2,
      force_sync: true,
      writer_path: 'direct',
    }))

    expect(response.status).toBe(202)
    expect(global.fetch).toHaveBeenCalledOnce()
    const [, init] = vi.mocked(global.fetch).mock.calls[0]
    expect(JSON.parse(String(init?.body))).toEqual({
      force_sync: false,
      target: 'win',
      model_type: 'lightgbm',
      test_size: 0.2,
    })
  })

  it('preserves the active-job conflict for safe UI reconnection without retrying start', async () => {
    process.env.APP_ENV = 'development'
    process.env.MODEL_TRAINING_LOCAL_ENABLED = 'true'
    const conflict = {
      detail: {
        code: 'train-job-active',
        message: '別のモデル作成が実行中です',
        job_id: JOB_ID,
      },
    }
    vi.mocked(global.fetch).mockResolvedValueOnce(new Response(JSON.stringify(conflict), {
      status: 409,
      headers: { 'Content-Type': 'application/json' },
    }))

    const response = await POST(startRequest())

    expect(response.status).toBe(409)
    expect(response.headers.get('Cache-Control')).toBe('no-store')
    expect(await response.json()).toEqual(conflict)
    expect(global.fetch).toHaveBeenCalledOnce()
  })

  it('fails closed after one backend start attempt and never retries an uncertain request', async () => {
    process.env.APP_ENV = 'development'
    process.env.MODEL_TRAINING_LOCAL_ENABLED = 'true'
    vi.mocked(global.fetch).mockRejectedValueOnce(new Error('response lost'))

    const response = await POST(startRequest())

    expect(response.status).toBe(502)
    expect(await response.json()).toEqual({ detail: 'Model training service unavailable' })
    expect(global.fetch).toHaveBeenCalledOnce()
  })

  it('rejects malformed input and invalid backend JSON without starting a job', async () => {
    process.env.APP_ENV = 'development'
    process.env.MODEL_TRAINING_LOCAL_ENABLED = 'true'
    const malformed = new NextRequest('http://localhost/api/ml/train/start', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer browser-supplied-token',
        'Content-Type': 'application/json',
      },
      body: '{',
    })

    let response = await POST(malformed)
    expect(response.status).toBe(400)
    expect(global.fetch).not.toHaveBeenCalled()

    vi.mocked(global.fetch).mockResolvedValueOnce(new Response('not-json', { status: 200 }))
    response = await POST(startRequest())
    expect(response.status).toBe(502)
    expect(await response.json()).toEqual({
      detail: 'Model training service returned an invalid response',
    })
  })

  it('requires current-password Admin mode before reading or recovering local job status', async () => {
    process.env.APP_ENV = 'development'
    process.env.MODEL_TRAINING_LOCAL_ENABLED = 'true'
    verifyRequestAuthMock.mockResolvedValueOnce({
      ok: false,
      status: 403,
      detail: 'Admin mode verification required',
    })

    const response = await GET_STATUS(statusRequest(), {
      params: Promise.resolve({ job_id: JOB_ID }),
    })

    expect(response.status).toBe(403)
    expect(response.headers.get('Cache-Control')).toBe('no-store')
    expect(verifyRequestAuthMock).toHaveBeenCalledWith(expect.anything(), { requireAdminMode: true })
    expect(global.fetch).not.toHaveBeenCalled()
  })

  it('polls a canonical local job UUID with the verified Admin mode grant', async () => {
    process.env.APP_ENV = 'development'
    process.env.MODEL_TRAINING_LOCAL_ENABLED = 'true'
    vi.mocked(global.fetch).mockResolvedValueOnce(new Response(JSON.stringify({
      job_id: JOB_ID,
      status: 'running',
      progress: '学習中...',
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))

    const response = await GET_STATUS(statusRequest(), {
      params: Promise.resolve({ job_id: JOB_ID }),
    })

    expect(response.status).toBe(200)
    expect(response.headers.get('Cache-Control')).toBe('no-store')
    expect(verifyRequestAuthMock).toHaveBeenCalledWith(expect.anything(), { requireAdminMode: true })
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining(`/api/train/status/${JOB_ID}`),
      expect.objectContaining({
        headers: { Authorization: `Bearer ${VERIFIED_TOKEN}` },
        cache: 'no-store',
        redirect: 'error',
      }),
    )
    expect(JSON.stringify(vi.mocked(global.fetch).mock.calls[0])).not.toContain('browser-supplied-token')
  })

  it.each([
    {
      state: 'preparing',
      payload: { job_id: JOB_ID, status: 'queued', progress: '準備中', pct: 0, result: null, error: null },
    },
    {
      state: 'running',
      payload: { job_id: JOB_ID, status: 'running', progress: '学習中', pct: 50, result: null, error: null },
    },
    {
      state: 'artifact-registered',
      payload: {
        job_id: JOB_ID,
        status: 'completed',
        progress: '完了',
        pct: 100,
        result: { model_id: 'candidate-model', metrics: { auc: 0.75 } },
        error: null,
      },
    },
    {
      state: 'failed',
      payload: {
        job_id: JOB_ID,
        status: 'error',
        progress: '中断',
        pct: 100,
        result: null,
        error: 'lease-expired-running',
      },
    },
    {
      state: 'not-found-or-not-owned',
      payload: {
        job_id: JOB_ID,
        status: 'not_found',
        progress: '',
        pct: 0,
        result: null,
        error: '学習ジョブが見つかりません',
      },
    },
  ])('preserves the local-admin-train $state status projection', async ({ payload }) => {
    process.env.APP_ENV = 'development'
    process.env.MODEL_TRAINING_LOCAL_ENABLED = 'true'
    vi.mocked(global.fetch).mockResolvedValueOnce(new Response(JSON.stringify(payload), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))

    const response = await GET_STATUS(statusRequest(), {
      params: Promise.resolve({ job_id: JOB_ID }),
    })

    expect(response.status).toBe(200)
    expect(await response.json()).toEqual(payload)
    expect(global.fetch).toHaveBeenCalledOnce()
  })

  it('rejects malformed job IDs and invalid status responses', async () => {
    process.env.APP_ENV = 'development'
    process.env.MODEL_TRAINING_LOCAL_ENABLED = 'true'

    let response = await GET_STATUS(statusRequest('not-a-job'), {
      params: Promise.resolve({ job_id: 'not-a-job' }),
    })
    expect(response.status).toBe(400)
    expect(global.fetch).not.toHaveBeenCalled()

    vi.mocked(global.fetch).mockResolvedValueOnce(new Response('not-json', { status: 200 }))
    response = await GET_STATUS(statusRequest(), {
      params: Promise.resolve({ job_id: JOB_ID }),
    })
    expect(response.status).toBe(502)
    expect(await response.json()).toEqual({
      detail: 'Model training status service returned an invalid response',
    })
  })

  it('uses capability gating instead of a permanently disabled training button', () => {
    const source = readFileSync(path.join(process.cwd(), 'src/app/train/page.tsx'), 'utf8')

    expect(source).toContain("authFetch('/api/ml/train/capability'")
    expect(source).not.toContain('承認済みジョブ実行基盤を準備中')
    expect(source).not.toMatch(/<button\s+onClick=\{handleTrain\}\s+disabled(?:\s|>)/)
  })

  it('forces the local training flag and loopback binds after loading launcher configuration', () => {
    const source = readFileSync(path.join(process.cwd(), 'scripts/start-local-app.ps1'), 'utf8')
    const finalDotEnvImport = source.lastIndexOf('Import-DotEnv -Path')
    const appEnvironment = source.indexOf("$env:APP_ENV = 'development'")
    const trainingFlag = source.indexOf("$env:MODEL_TRAINING_LOCAL_ENABLED = 'true'")
    const activationFlag = source.indexOf("$env:MODEL_ACTIVATION_LOCAL_ENABLED = 'true'")

    expect(finalDotEnvImport).toBeGreaterThanOrEqual(0)
    expect(appEnvironment).toBeGreaterThan(finalDotEnvImport)
    expect(trainingFlag).toBeGreaterThan(appEnvironment)
    expect(activationFlag).toBeGreaterThan(trainingFlag)
    expect(source).toContain("$env:API_HOST = '127.0.0.1'")
    expect(source).toContain("$env:HOSTNAME = '127.0.0.1'")
    expect(source).toContain('npm run dev -- --hostname 127.0.0.1')
  })
})
