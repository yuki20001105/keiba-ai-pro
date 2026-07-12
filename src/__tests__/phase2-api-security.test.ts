import { beforeEach, describe, expect, test, vi } from 'vitest'

const mockVerifyRequestAuth = vi.fn()
const mockCreateSupabaseServiceClient = vi.fn()

vi.mock('@/lib/server-auth', () => ({
  verifyRequestAuth: (...args: unknown[]) => mockVerifyRequestAuth(...args),
  createSupabaseServiceClient: (...args: unknown[]) => mockCreateSupabaseServiceClient(...args),
}))

const mockCreateClient = vi.fn()
vi.mock('@supabase/supabase-js', () => ({
  createClient: (...args: unknown[]) => mockCreateClient(...args),
}))

const mockStripeCheckoutCreate = vi.fn()
const mockStripePortalCreate = vi.fn()
const mockStripeCustomersCreate = vi.fn()
vi.mock('@/lib/stripe', () => ({
  stripe: {
    checkout: { sessions: { create: (...args: unknown[]) => mockStripeCheckoutCreate(...args) } },
    billingPortal: { sessions: { create: (...args: unknown[]) => mockStripePortalCreate(...args) } },
    customers: { create: (...args: unknown[]) => mockStripeCustomersCreate(...args) },
  },
  PLANS: {
    PREMIUM: { priceId: 'price_premium' },
  },
}))

const mockVisionTextDetection = vi.fn()
vi.mock('@google-cloud/vision', () => ({
  default: {
    ImageAnnotatorClient: class {
      textDetection = (...args: unknown[]) => mockVisionTextDetection(...args)
    },
  },
}))

function makeJsonRequest(url: string, body: unknown, headers?: Record<string, string>): Request {
  return new Request(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(headers || {}),
    },
    body: JSON.stringify(body),
  })
}

function makeFormRequest(url: string, form: FormData, headers?: Record<string, string>): Request {
  return new Request(url, {
    method: 'POST',
    headers: headers || {},
    body: form,
  })
}

function makeOcrRequest(form: FormData, headers?: Record<string, string>) {
  return {
    headers: new Headers(headers || {}),
    formData: async () => form,
  }
}

describe('Phase2 API security regressions', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    vi.unstubAllGlobals()
    process.env.NEXT_PUBLIC_SUPABASE_URL = 'http://127.0.0.1:54321'
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = 'anon'
    process.env.SUPABASE_SERVICE_ROLE_KEY = 'service'
    process.env.STRIPE_PRICE_ID_PREMIUM = 'price_premium'
  })

  test('netkeiba/race unauthenticated returns 401', async () => {
    mockVerifyRequestAuth.mockResolvedValue({ ok: false, status: 401, detail: 'Authentication required' })
    const { POST } = await import('@/app/api/netkeiba/race/route')
    const res = await POST(makeJsonRequest('http://localhost/api/netkeiba/race', { raceId: '202601010101' }) as any)
    expect(res.status).toBe(401)
  })

  test('netkeiba/race non-admin returns 403', async () => {
    mockVerifyRequestAuth.mockResolvedValue({ ok: false, status: 403, detail: 'Admin role required' })
    const { POST } = await import('@/app/api/netkeiba/race/route')
    const res = await POST(makeJsonRequest('http://localhost/api/netkeiba/race', { raceId: '202601010101' }) as any)
    expect(res.status).toBe(403)
  })

  test('netkeiba/race spoofed userId is rejected', async () => {
    mockVerifyRequestAuth.mockResolvedValue({
      ok: true,
      context: { user: { id: 'verified-user' }, token: 'tok', profile: { role: 'admin', subscription_tier: 'premium' } },
    })
    mockCreateClient.mockReturnValue({ from: vi.fn() })

    const { POST } = await import('@/app/api/netkeiba/race/route')
    const res = await POST(
      makeJsonRequest('http://localhost/api/netkeiba/race', { raceId: '202601010101', userId: 'spoof-user' }) as any
    )
    expect(res.status).toBe(403)
  })

  test('netkeiba/race admin success binds writes to verified user', async () => {
    mockVerifyRequestAuth.mockResolvedValue({
      ok: true,
      context: { user: { id: 'verified-admin' }, token: 'tok', profile: { role: 'admin', subscription_tier: 'premium' } },
    })

    const upsert = vi.fn().mockResolvedValue({ error: null })
    const insert = vi.fn().mockResolvedValue({ error: null })
    mockCreateClient.mockReturnValue({
      from: (table: string) => {
        if (table === 'races') return { upsert }
        return { insert }
      },
    })

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        success: true,
        race_info: { race_name: 'R', distance: 1200, track_type: '芝', weather: '晴', field_condition: '良', venue: '東京' },
        results: [{ finish_position: '1', bracket_number: '1', horse_number: '1', horse_name: 'A', sex_age: '牡3', jockey_weight: '55', jockey_name: 'J', finish_time: '1:34.5', odds: '2.2', popularity: '1' }],
        payouts: [{ type: '単勝', numbers: '1', amount: '220円' }],
      }),
    }) as any)

    const { POST } = await import('@/app/api/netkeiba/race/route')
    const res = await POST(
      makeJsonRequest('http://localhost/api/netkeiba/race', { raceId: '202601010101', userId: 'spoofed' }) as any
    )
    expect(res.status).toBe(403)

    const res2 = await POST(
      makeJsonRequest('http://localhost/api/netkeiba/race', { raceId: '202601010101' }) as any
    )
    expect(res2.status).toBe(200)
    expect(upsert).toHaveBeenCalled()
    const upsertArg = upsert.mock.calls[0][0] as Record<string, unknown>
    expect(upsertArg.user_id).toBe('verified-admin')
  })

  test('stripe checkout rejects userId mismatch and unknown priceId', async () => {
    mockVerifyRequestAuth.mockResolvedValue({
      ok: true,
      context: { user: { id: 'verified-user' }, token: 'tok', profile: { role: 'user', subscription_tier: 'premium' } },
    })
    mockCreateSupabaseServiceClient.mockReturnValue({
      from: () => ({ select: () => ({ eq: () => ({ single: async () => ({ data: { stripe_customer_id: 'cus_1', email: 'u@example.com' }, error: null }) }) }) }),
    })

    const { POST } = await import('@/app/api/stripe/create-checkout/route')

    const mismatch = await POST(
      makeJsonRequest('http://localhost/api/stripe/create-checkout', { userId: 'other-user', priceId: 'price_premium' }) as any
    )
    expect(mismatch.status).toBe(403)

    const badPrice = await POST(
      makeJsonRequest('http://localhost/api/stripe/create-checkout', { priceId: 'price_not_allowed' }) as any
    )
    expect(badPrice.status).toBe(400)
  })

  test('stripe portal rejects other customer portal request', async () => {
    mockVerifyRequestAuth.mockResolvedValue({
      ok: true,
      context: { user: { id: 'verified-user' }, token: 'tok', profile: { role: 'user', subscription_tier: 'premium' } },
    })
    mockCreateSupabaseServiceClient.mockReturnValue({
      from: () => ({ select: () => ({ eq: () => ({ single: async () => ({ data: { stripe_customer_id: 'cus_owner' }, error: null }) }) }) }),
    })

    const { POST } = await import('@/app/api/stripe/portal/route')
    const res = await POST(makeJsonRequest('http://localhost/api/stripe/portal', { customerId: 'cus_other' }) as any)
    expect(res.status).toBe(403)
    expect(mockStripePortalCreate).not.toHaveBeenCalled()
  })

  test('ocr unauthenticated returns 401 and does not call Vision', async () => {
    mockVerifyRequestAuth.mockResolvedValue({ ok: false, status: 401, detail: 'Authentication required' })
    const { POST } = await import('@/app/api/ocr/route')

    const form = new FormData()
    form.set('image', new File(['x'], 'ticket.png', { type: 'image/png' }))
    const res = await POST(makeOcrRequest(form) as any)

    expect(res.status).toBe(401)
    expect(mockVisionTextDetection).not.toHaveBeenCalled()
  })

  test('ocr rejects mismatched userId and quota exceed before Vision', async () => {
    mockVerifyRequestAuth.mockResolvedValue({
      ok: true,
      context: { user: { id: 'verified-user' }, token: 'tok', profile: { role: 'user', subscription_tier: 'free' } },
    })

    const single = vi.fn()
      .mockResolvedValueOnce({ data: { ocr_monthly_limit: 10, ocr_used_this_month: 10, ocr_reset_date: new Date().toISOString() }, error: null })
    mockCreateSupabaseServiceClient.mockReturnValue({
      from: () => ({
        select: () => ({ eq: () => ({ single }) }),
      }),
    })

    const { POST } = await import('@/app/api/ocr/route')

    const mismatchForm = new FormData()
    mismatchForm.set('image', new File(['x'], 'ticket.png', { type: 'image/png' }))
    mismatchForm.set('userId', 'other-user')
    const mismatch = await POST(makeOcrRequest(mismatchForm, { Authorization: 'Bearer x' }) as any)
    expect(mismatch.status).toBe(403)
    expect(mockVisionTextDetection).not.toHaveBeenCalled()

    const quotaForm = new FormData()
    quotaForm.set('image', new File(['x'], 'ticket.png', { type: 'image/png' }))
    const quota = await POST(makeOcrRequest(quotaForm, { Authorization: 'Bearer x' }) as any)
    expect(quota.status).toBe(429)
    expect(mockVisionTextDetection).not.toHaveBeenCalled()
  })

  test('stripe checkout and portal unauthenticated return 401', async () => {
    mockVerifyRequestAuth.mockResolvedValue({ ok: false, status: 401, detail: 'Authentication required' })

    const checkout = await import('@/app/api/stripe/create-checkout/route')
    const portal = await import('@/app/api/stripe/portal/route')

    const checkoutRes = await checkout.POST(
      makeJsonRequest('http://localhost/api/stripe/create-checkout', { priceId: 'price_premium' }) as any
    )
    const portalRes = await portal.POST(
      makeJsonRequest('http://localhost/api/stripe/portal', {}) as any
    )

    expect(checkoutRes.status).toBe(401)
    expect(portalRes.status).toBe(401)
  })

  test('stripe routes return 503 when service role client is unavailable', async () => {
    mockVerifyRequestAuth.mockResolvedValue({
      ok: true,
      context: { user: { id: 'verified-user' }, token: 'tok', profile: { role: 'user', subscription_tier: 'premium' } },
    })
    mockCreateSupabaseServiceClient.mockReturnValue(null)

    const checkout = await import('@/app/api/stripe/create-checkout/route')
    const portal = await import('@/app/api/stripe/portal/route')

    const checkoutRes = await checkout.POST(
      makeJsonRequest('http://localhost/api/stripe/create-checkout', { priceId: 'price_premium' }) as any
    )
    const portalRes = await portal.POST(
      makeJsonRequest('http://localhost/api/stripe/portal', {}) as any
    )

    expect(checkoutRes.status).toBe(503)
    expect(portalRes.status).toBe(503)
  })

  test('netkeiba/race unauthenticated does not call scrape service or service-role DB', async () => {
    mockVerifyRequestAuth.mockResolvedValue({ ok: false, status: 401, detail: 'Authentication required' })
    const fetchSpy = vi.fn()
    vi.stubGlobal('fetch', fetchSpy as any)

    const { POST } = await import('@/app/api/netkeiba/race/route')
    const res = await POST(makeJsonRequest('http://localhost/api/netkeiba/race', { raceId: '202601010101' }) as any)

    expect(res.status).toBe(401)
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(mockCreateClient).not.toHaveBeenCalled()
  })

  test('profiling status proxy forwards Bearer token and preserves backend detail/status', async () => {
    mockVerifyRequestAuth.mockResolvedValue({
      ok: true,
      context: { user: { id: 'admin' }, token: 'forwarded-token', profile: { role: 'admin', subscription_tier: 'premium' } },
    })
    const fetchSpy = vi.fn().mockResolvedValue({
      ok: false,
      status: 429,
      json: async () => ({ detail: 'E2E-ERROR-429' }),
    })
    vi.stubGlobal('fetch', fetchSpy as any)

    const { GET } = await import('@/app/api/profiling/status/[job_id]/route')
    const req = new Request('http://localhost/api/profiling/status/job-1', { method: 'GET' })
    const res = await GET(req as any, { params: Promise.resolve({ job_id: 'job-1' }) })
    const body = await res.json()

    expect(res.status).toBe(429)
    expect(body.detail).toBe('E2E-ERROR-429')
    const fetchOptions = fetchSpy.mock.calls[0][1] as Record<string, any>
    expect(fetchOptions.headers.Authorization).toBe('Bearer forwarded-token')
  })

  test('realtime-odds refresh proxy forwards Bearer token and preserves backend detail/status', async () => {
    mockVerifyRequestAuth.mockResolvedValue({
      ok: true,
      context: { user: { id: 'admin' }, token: 'forwarded-token', profile: { role: 'admin', subscription_tier: 'premium' } },
    })
    const fetchSpy = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({ detail: 'E2E-ERROR-503' }),
    })
    vi.stubGlobal('fetch', fetchSpy as any)

    const { POST } = await import('@/app/api/realtime-odds/refresh/route')
    const res = await POST(makeJsonRequest('http://localhost/api/realtime-odds/refresh', { race_ids: ['202601010101'] }) as any)
    const body = await res.json()

    expect(res.status).toBe(503)
    expect(body.detail).toBe('E2E-ERROR-503')
    const fetchOptions = fetchSpy.mock.calls[0][1] as Record<string, any>
    expect(fetchOptions.headers.Authorization).toBe('Bearer forwarded-token')
  })
})
