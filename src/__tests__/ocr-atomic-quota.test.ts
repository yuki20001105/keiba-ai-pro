import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

const mockVerifyRequestAuth = vi.fn()
const mockCreateSupabaseServiceClient = vi.fn()
const mockVisionTextDetection = vi.fn()

vi.mock('@/lib/server-auth', () => ({
  verifyRequestAuth: (...args: unknown[]) => mockVerifyRequestAuth(...args),
  createSupabaseServiceClient: (...args: unknown[]) => mockCreateSupabaseServiceClient(...args),
}))

vi.mock('@google-cloud/vision', () => ({
  default: {
    ImageAnnotatorClient: class {
      textDetection = (...args: unknown[]) => mockVisionTextDetection(...args)
    },
  },
}))

const RESET_AT = '2026-07-01T00:00:00.000Z'
const PNG_SIGNATURE = new Uint8Array([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
])

function makePngFile(name = 'ticket.png') {
  return new File([PNG_SIGNATURE], name, { type: 'image/png' })
}

function makeRequestFromForm(form: FormData) {
  return {
    headers: new Headers({ Authorization: 'Bearer token' }),
    formData: async () => form,
  }
}

function makeRequest(userId?: string, image: File = makePngFile()) {
  const form = new FormData()
  form.set('image', image)
  if (userId) form.set('userId', userId)
  return makeRequestFromForm(form)
}

function allowedQuota(usedCount = 1, monthlyLimit = 10) {
  return {
    data: [{
      allowed: true,
      used_count: usedCount,
      monthly_limit: monthlyLimit,
      reset_at: RESET_AT,
    }],
    error: null,
  }
}

function deniedQuota(monthlyLimit = 10) {
  return {
    data: [{
      allowed: false,
      used_count: monthlyLimit,
      monthly_limit: monthlyLimit,
      reset_at: RESET_AT,
    }],
    error: null,
  }
}

describe('OCR atomic quota reservation', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    mockVerifyRequestAuth.mockResolvedValue({
      ok: true,
      context: {
        user: { id: '30000000-0000-4000-8000-000000000001' },
        token: 'token',
        profile: { role: 'user', subscription_tier: 'free' },
      },
    })
    mockVisionTextDetection.mockResolvedValue([{
      textAnnotations: [{ description: '1R 単勝 1番 100円 2.0倍' }],
    }])
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  test.each([
    ['unsupported MIME type', () => new File([PNG_SIGNATURE], 'ticket.txt', { type: 'text/plain' })],
    ['empty file', () => new File([], 'ticket.png', { type: 'image/png' })],
    ['MIME/extension mismatch', () => new File([PNG_SIGNATURE], 'ticket.jpg', { type: 'image/png' })],
    ['invalid image signature', () => new File(['not-an-image'], 'ticket.png', { type: 'image/png' })],
    ['unsafe filename', () => new File([PNG_SIGNATURE], 'ticket\u0000.png', { type: 'image/png' })],
  ])('rejects %s locally without reserving quota', async (_label, makeImage) => {
    const rpc = vi.fn().mockResolvedValue(allowedQuota())
    mockCreateSupabaseServiceClient.mockReturnValue({ rpc, from: vi.fn() })

    const { POST } = await import('@/app/api/ocr/route')
    const response = await POST(makeRequest(undefined, makeImage()) as never)

    expect(response.status).toBe(400)
    expect(mockCreateSupabaseServiceClient).not.toHaveBeenCalled()
    expect(rpc).not.toHaveBeenCalled()
    expect(mockVisionTextDetection).not.toHaveBeenCalled()
  })

  test('rejects an oversized image before reading it or reserving quota', async () => {
    const image = makePngFile()
    const read = vi.spyOn(image, 'arrayBuffer')
    Object.defineProperty(image, 'size', { value: 10 * 1024 * 1024 + 1 })
    const rpc = vi.fn().mockResolvedValue(allowedQuota())
    mockCreateSupabaseServiceClient.mockReturnValue({ rpc, from: vi.fn() })

    const { POST } = await import('@/app/api/ocr/route')
    const response = await POST(makeRequest(undefined, image) as never)

    expect(response.status).toBe(400)
    expect(read).not.toHaveBeenCalled()
    expect(mockCreateSupabaseServiceClient).not.toHaveBeenCalled()
    expect(rpc).not.toHaveBeenCalled()
    expect(mockVisionTextDetection).not.toHaveBeenCalled()
  })

  test('rejects duplicate image fields without reserving quota', async () => {
    const form = new FormData()
    form.append('image', makePngFile('first.png'))
    form.append('image', makePngFile('second.png'))
    const rpc = vi.fn().mockResolvedValue(allowedQuota())
    mockCreateSupabaseServiceClient.mockReturnValue({ rpc, from: vi.fn() })

    const { POST } = await import('@/app/api/ocr/route')
    const response = await POST(makeRequestFromForm(form) as never)

    expect(response.status).toBe(400)
    expect(mockCreateSupabaseServiceClient).not.toHaveBeenCalled()
    expect(rpc).not.toHaveBeenCalled()
    expect(mockVisionTextDetection).not.toHaveBeenCalled()
  })

  test('does not reserve quota when multipart parsing fails', async () => {
    const rpc = vi.fn().mockResolvedValue(allowedQuota())
    mockCreateSupabaseServiceClient.mockReturnValue({ rpc, from: vi.fn() })

    const { POST } = await import('@/app/api/ocr/route')
    const response = await POST({
      headers: new Headers({ Authorization: 'Bearer token' }),
      formData: vi.fn().mockRejectedValue(new Error('multipart parser detail')),
    } as never)

    expect(response.status).toBe(400)
    expect(await response.json()).toEqual({ error: 'Invalid multipart form data' })
    expect(mockCreateSupabaseServiceClient).not.toHaveBeenCalled()
    expect(rpc).not.toHaveBeenCalled()
    expect(mockVisionTextDetection).not.toHaveBeenCalled()
  })

  test('does not reserve quota when local image-buffer preparation fails', async () => {
    const image = makePngFile()
    vi.spyOn(image, 'arrayBuffer').mockRejectedValue(new Error('buffer detail'))
    const rpc = vi.fn().mockResolvedValue(allowedQuota())
    mockCreateSupabaseServiceClient.mockReturnValue({ rpc, from: vi.fn() })

    const { POST } = await import('@/app/api/ocr/route')
    const response = await POST(makeRequest(undefined, image) as never)

    expect(response.status).toBe(400)
    expect(await response.json()).toEqual({ error: 'Image file could not be read' })
    expect(mockCreateSupabaseServiceClient).not.toHaveBeenCalled()
    expect(rpc).not.toHaveBeenCalled()
    expect(mockVisionTextDetection).not.toHaveBeenCalled()
  })

  test('two concurrent requests reserve through the RPC and only the allowed request reaches Vision', async () => {
    const rpc = vi.fn()
      .mockResolvedValueOnce(allowedQuota(10, 10))
      .mockResolvedValueOnce(deniedQuota(10))
    const insert = vi.fn().mockResolvedValue({ error: null })
    const from = vi.fn().mockReturnValue({ insert })
    mockCreateSupabaseServiceClient.mockReturnValue({ rpc, from })

    const { POST } = await import('@/app/api/ocr/route')
    const responses = await Promise.all([
      POST(makeRequest() as never),
      POST(makeRequest() as never),
    ])

    expect(responses.map((response) => response.status).sort()).toEqual([200, 429])
    expect(rpc).toHaveBeenCalledTimes(2)
    expect(rpc).toHaveBeenNthCalledWith(1, 'consume_ocr_quota', {
      p_user_id: '30000000-0000-4000-8000-000000000001',
    })
    expect(mockVisionTextDetection).toHaveBeenCalledTimes(1)
    expect(rpc.mock.invocationCallOrder[0]).toBeLessThan(mockVisionTextDetection.mock.invocationCallOrder[0])
    expect(from).toHaveBeenCalledTimes(1)
    expect(from).toHaveBeenCalledWith('ocr_usage')
    expect(insert).toHaveBeenCalledTimes(1)
  })

  test.each([
    ['RPC error', { data: null, error: { message: 'unavailable' } }],
    ['missing row', { data: [], error: null }],
    ['multiple rows', { data: [allowedQuota().data[0], allowedQuota(2).data[0]], error: null }],
    ['non-boolean allowed', { data: [{ allowed: 1, used_count: 1, monthly_limit: 10, reset_at: RESET_AT }], error: null }],
    ['non-integer count', { data: [{ allowed: true, used_count: 1.5, monthly_limit: 10, reset_at: RESET_AT }], error: null }],
    ['invalid reset timestamp', { data: [{ allowed: true, used_count: 1, monthly_limit: 10, reset_at: 'invalid' }], error: null }],
    ['non-RFC3339 reset timestamp', { data: [{ allowed: true, used_count: 1, monthly_limit: 10, reset_at: '07/01/2026' }], error: null }],
    ['inconsistent denial', { data: [{ allowed: false, used_count: 9, monthly_limit: 10, reset_at: RESET_AT }], error: null }],
    ['unexpected field', { data: [{ ...allowedQuota().data[0], internal: 'not-part-of-contract' }], error: null }],
  ])('fails closed with 503 for %s before Vision', async (_label, rpcResult) => {
    const rpc = vi.fn().mockResolvedValue(rpcResult)
    mockCreateSupabaseServiceClient.mockReturnValue({ rpc, from: vi.fn() })

    const { POST } = await import('@/app/api/ocr/route')
    const response = await POST(makeRequest() as never)

    expect(response.status).toBe(503)
    expect(mockVisionTextDetection).not.toHaveBeenCalled()
  })

  test('maps a thrown quota transport failure to 503 before Vision', async () => {
    const rpc = vi.fn().mockRejectedValue(new Error('network unavailable'))
    mockCreateSupabaseServiceClient.mockReturnValue({ rpc, from: vi.fn() })

    const { POST } = await import('@/app/api/ocr/route')
    const response = await POST(makeRequest() as never)

    expect(response.status).toBe(503)
    expect(mockVisionTextDetection).not.toHaveBeenCalled()
  })

  test('uses the verified user ID and never trusts a mismatched form userId', async () => {
    const rpc = vi.fn().mockResolvedValue(allowedQuota())
    mockCreateSupabaseServiceClient.mockReturnValue({ rpc, from: vi.fn() })

    const { POST } = await import('@/app/api/ocr/route')
    const response = await POST(makeRequest('other-user') as never)

    expect(response.status).toBe(403)
    expect(rpc).not.toHaveBeenCalled()
    expect(mockVisionTextDetection).not.toHaveBeenCalled()
  })

  test('does not perform a second profile increment after a successful reservation', async () => {
    const rpc = vi.fn().mockResolvedValue(allowedQuota())
    const insert = vi.fn().mockResolvedValue({ error: null })
    const from = vi.fn().mockReturnValue({ insert })
    mockCreateSupabaseServiceClient.mockReturnValue({ rpc, from })

    const { POST } = await import('@/app/api/ocr/route')
    const response = await POST(makeRequest() as never)

    expect(response.status).toBe(200)
    expect(from).toHaveBeenCalledWith('ocr_usage')
    expect(from).not.toHaveBeenCalledWith('profiles')
  })

  test('keeps the reservation after a Vision attempt fails and returns no provider detail', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const rpc = vi.fn().mockResolvedValue(allowedQuota())
    const from = vi.fn()
    mockCreateSupabaseServiceClient.mockReturnValue({ rpc, from })
    mockVisionTextDetection.mockRejectedValue(new Error('provider-secret-detail'))

    const { POST } = await import('@/app/api/ocr/route')
    const response = await POST(makeRequest() as never)
    const body = await response.json()

    expect(response.status).toBe(500)
    expect(body).toEqual({ error: 'OCR processing failed' })
    expect(JSON.stringify(body)).not.toContain('provider-secret-detail')
    expect(consoleError).toHaveBeenCalledOnce()
    expect(consoleError).toHaveBeenCalledWith('OCR_UNHANDLED_FAILURE')
    expect(JSON.stringify(consoleError.mock.calls)).not.toContain('provider-secret-detail')
    expect(rpc).toHaveBeenCalledTimes(1)
    expect(mockVisionTextDetection).toHaveBeenCalledTimes(1)
    expect(rpc.mock.invocationCallOrder[0]).toBeLessThan(mockVisionTextDetection.mock.invocationCallOrder[0])
    expect(from).not.toHaveBeenCalled()
  })

  test('keeps the reservation when Vision returns no text', async () => {
    const rpc = vi.fn().mockResolvedValue(allowedQuota())
    const from = vi.fn()
    mockCreateSupabaseServiceClient.mockReturnValue({ rpc, from })
    mockVisionTextDetection.mockResolvedValue([{ textAnnotations: [] }])

    const { POST } = await import('@/app/api/ocr/route')
    const response = await POST(makeRequest() as never)

    expect(response.status).toBe(400)
    expect(rpc).toHaveBeenCalledTimes(1)
    expect(mockVisionTextDetection).toHaveBeenCalledTimes(1)
    expect(from).not.toHaveBeenCalled()
  })

  test('keeps the reservation when usage-history persistence fails after Vision', async () => {
    const rpc = vi.fn().mockResolvedValue(allowedQuota())
    const insert = vi.fn().mockResolvedValue({ error: { message: 'database-secret-detail' } })
    const from = vi.fn().mockReturnValue({ insert })
    mockCreateSupabaseServiceClient.mockReturnValue({ rpc, from })

    const { POST } = await import('@/app/api/ocr/route')
    const response = await POST(makeRequest() as never)
    const body = await response.json()

    expect(response.status).toBe(503)
    expect(body).toEqual({ error: 'OCR usage could not be recorded' })
    expect(JSON.stringify(body)).not.toContain('database-secret-detail')
    expect(rpc).toHaveBeenCalledTimes(1)
    expect(mockVisionTextDetection).toHaveBeenCalledTimes(1)
    expect(insert).toHaveBeenCalledTimes(1)
  })

  test('maps an internal pre-reservation failure to a fixed response without quota use', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const rpc = vi.fn().mockResolvedValue(allowedQuota())
    mockCreateSupabaseServiceClient.mockImplementation(() => {
      throw new Error('internal-secret-detail')
    })

    const { POST } = await import('@/app/api/ocr/route')
    const response = await POST(makeRequest() as never)
    const body = await response.json()

    expect(response.status).toBe(500)
    expect(body).toEqual({ error: 'OCR processing failed' })
    expect(JSON.stringify(body)).not.toContain('internal-secret-detail')
    expect(consoleError).toHaveBeenCalledOnce()
    expect(consoleError).toHaveBeenCalledWith('OCR_UNHANDLED_FAILURE')
    expect(JSON.stringify(consoleError.mock.calls)).not.toContain('internal-secret-detail')
    expect(rpc).not.toHaveBeenCalled()
    expect(mockVisionTextDetection).not.toHaveBeenCalled()
  })
})
