import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { POST } from '@/app/api/ml/train/start/route'

const originalFetch = global.fetch

function request(): Request {
  return new Request('http://localhost/api/ml/train/start', {
    method: 'POST',
    headers: {
      Authorization: 'Bearer test-token',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ target: 'win' }),
  })
}

describe('legacy direct model training route guard', () => {
  beforeEach(() => {
    global.fetch = vi.fn()
    delete process.env.MODEL_TRAINING_LOCAL_ENABLED
  })

  afterEach(() => {
    global.fetch = originalFetch
    delete process.env.APP_ENV
    delete process.env.MODEL_TRAINING_LOCAL_ENABLED
  })

  it.each(['staging', 'production', 'prod', '', 'unknown'])(
    'never forwards direct training in deployed or unknown environment %s',
    async environment => {
      process.env.APP_ENV = environment
      process.env.MODEL_TRAINING_LOCAL_ENABLED = 'true'

      const response = await POST(request() as never)
      const body = await response.json()

      expect(response.status).toBe(409)
      expect(response.headers.get('Cache-Control')).toBe('no-store')
      expect(body.code).toBe('approval-bound-model-training-required')
      expect(global.fetch).not.toHaveBeenCalled()
    },
  )

  it('does not forward local training without exact opt-in', async () => {
    process.env.APP_ENV = 'local'

    const response = await POST(request() as never)

    expect(response.status).toBe(409)
    expect(global.fetch).not.toHaveBeenCalled()
  })

  it('keeps explicit local/test compatibility behind the FastAPI Premium boundary', async () => {
    process.env.APP_ENV = 'test'
    process.env.MODEL_TRAINING_LOCAL_ENABLED = 'TRUE'
    vi.mocked(global.fetch).mockResolvedValue(new Response(JSON.stringify({
      job_id: 'local-job',
      status: 'queued',
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))

    const response = await POST(request() as never)

    expect(response.status).toBe(200)
    expect(global.fetch).toHaveBeenCalledOnce()
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/train/start'),
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ Authorization: 'Bearer test-token' }),
      }),
    )
  })

  it('keeps the normal training UI disabled until the durable runner exists', () => {
    const source = readFileSync(path.join(process.cwd(), 'src/app/train/page.tsx'), 'utf8')

    expect(source).toContain('title="モデル学習には永続的な承認と承認済みジョブ実行基盤が必要です"')
    expect(source).toContain("'承認済みジョブ実行基盤を準備中'")
    expect(source).toMatch(/<button\s+onClick=\{handleTrain\}\s+disabled/)
  })
})
