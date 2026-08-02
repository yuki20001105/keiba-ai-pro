import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { DELETE } from '@/app/api/models/[id]/route'

const originalFetch = global.fetch

function request(): Request {
  return new Request('http://localhost/api/models/candidate', {
    method: 'DELETE',
    headers: { Authorization: 'Bearer test-token' },
  })
}

describe('legacy model deletion route guard', () => {
  beforeEach(() => {
    global.fetch = vi.fn()
    delete process.env.MODEL_DELETION_LOCAL_ENABLED
  })

  afterEach(() => {
    global.fetch = originalFetch
    delete process.env.APP_ENV
    delete process.env.MODEL_DELETION_LOCAL_ENABLED
  })

  it.each(['staging', 'production', 'prod', '', 'unknown'])(
    'never forwards deletion in deployed or unknown environment %s',
    async environment => {
      process.env.APP_ENV = environment
      process.env.MODEL_DELETION_LOCAL_ENABLED = 'true'

      const response = await DELETE(request() as never, { params: Promise.resolve({ id: 'candidate' }) })
      const body = await response.json()

      expect(response.status).toBe(409)
      expect(response.headers.get('Cache-Control')).toBe('no-store')
      expect(body.code).toBe('separate-model-retirement-approval-required')
      expect(global.fetch).not.toHaveBeenCalled()
    },
  )

  it('does not forward local deletion without exact opt-in', async () => {
    process.env.APP_ENV = 'local'

    const response = await DELETE(request() as never, { params: Promise.resolve({ id: 'candidate' }) })

    expect(response.status).toBe(409)
    expect(global.fetch).not.toHaveBeenCalled()
  })

  it('keeps explicit local/test compatibility behind the FastAPI Admin boundary', async () => {
    process.env.APP_ENV = 'test'
    process.env.MODEL_DELETION_LOCAL_ENABLED = 'TRUE'
    vi.mocked(global.fetch).mockResolvedValue(new Response(JSON.stringify({
      success: true,
      deleted: ['candidate.joblib'],
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))

    const response = await DELETE(request() as never, { params: Promise.resolve({ id: 'candidate' }) })

    expect(response.status).toBe(200)
    expect(global.fetch).toHaveBeenCalledOnce()
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/models/candidate'),
      expect.objectContaining({ method: 'DELETE' }),
    )
  })

  it('keeps the normal deletion UI disabled until retirement approval exists', () => {
    const source = readFileSync(path.join(process.cwd(), 'src/app/train/page.tsx'), 'utf8')

    expect(source).toContain('title="モデル削除には別の永続的な廃止承認が必要です"')
    expect(source).toMatch(/onClick=\{\(\) => handleDeleteModel\(m\.model_id\)\}\s+disabled/)
  })
})
