import { beforeEach, describe, expect, test, vi } from 'vitest'

const verifyRequestAuthMock = vi.fn()
vi.mock('@/lib/server-auth', () => ({
  verifyRequestAuth: (...args: unknown[]) => verifyRequestAuthMock(...args),
}))

const fetchMock = vi.fn()

const requestBody = {
  target: 'all',
  url_type: 'all',
  max_urls: 1,
  confirm_live_fetch: true,
}

function makeRequest(body: unknown) {
  return new Request('http://localhost/api/scrape/live-validation', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: 'Bearer browser-token' },
    body: JSON.stringify(body),
  })
}

function validBackendResponse() {
  return {
    live_validation: true,
    bounded: true,
    external_http: true,
    read_only: true,
    execution_enabled: false,
    result: {
      target: 'all',
      url_type: 'all',
      max_urls_applied: 1,
      attempted_url_count: 1,
      http_success_count: 1,
      http_error_count: 0,
      parse_success_count: 1,
      parse_error_count: 0,
      would_fix_count: 1,
      would_not_fix_count: 0,
      no_downgrade_count: 0,
      repairable_count: 1,
      elapsed_seconds: 1.2,
      estimated_full_refetch_runtime_seconds: 4,
      excluded_schema_review_count: 0,
      excluded_domain_allowed_count: 0,
      excluded_metadata_repair_count: 0,
      excluded_cache_available_count: 0,
      sample_results: [{
        url: 'https://db.netkeiba.com/race/202601010101/',
        url_type: 'result_page',
        race_id: '202601010101',
        horse_id: '2021100001',
        http_status: 200,
        parse_status: 'parse_success',
        missing_fields_before: ['finish_position'],
        fields_found_after: ['finish_position'],
        would_fix_columns: ['finish_position'],
        action: 'would-fix',
        reason: 'true-missing',
        recommended_next_action: 'review bounded evidence',
      }],
      recommended_next_actions: ['review bounded evidence'],
      rate_limit_policy: {
        max_urls: 1,
        max_supported_urls: 10,
        min_interval_sec: 1,
        max_retries: 1,
        retry_base_sec: 0,
        retry_jitter_sec: 0,
        retry_after_enabled: false,
        max_retry_after_sec: 0,
        per_request_timeout_sec: 12,
        total_timeout_sec: 75,
        max_body_bytes: 1048576,
        circuit_breaker: { threshold: 3, cooldown_sec: 30 },
        parallelism: 1,
        fetch_pipeline_used: true,
      },
      safety_flags: {
        small_live_validation_only: true,
        max_urls_limited: true,
        no_db_write: true,
        no_upsert: true,
        no_repair_execute: true,
        no_production_table_write: true,
        no_force_refresh_execute: true,
        no_bulk_refetch: true,
        redirects_disabled: true,
        bounded_response_body: true,
        bounded_total_runtime: true,
      },
      verdict: 'pass',
      verdict_reason: 'small-live-validation',
    },
  }
}

describe('Phase 3D Next live-validation proxy', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    verifyRequestAuthMock.mockResolvedValue({
      ok: true,
      context: { token: ['verified', 'admin', 'token'].join('-') },
    })
    vi.stubGlobal('fetch', fetchMock)
  })

  test('requires verified Admin before touching the backend', async () => {
    verifyRequestAuthMock.mockResolvedValueOnce({ ok: false, status: 403, detail: 'Admin role required' })
    const { POST } = await import('@/app/api/scrape/live-validation/route')
    const response = await POST(makeRequest(requestBody) as any)
    expect(response.status).toBe(403)
    expect(fetchMock).not.toHaveBeenCalled()
    expect(verifyRequestAuthMock).toHaveBeenCalledWith(expect.anything(), { requireAdmin: true })
    expect(response.headers.get('Cache-Control')).toBe('no-store')
  })

  test('rejects malformed/unknown/unconfirmed bodies without backend access', async () => {
    const { POST } = await import('@/app/api/scrape/live-validation/route')
    for (const body of [
      { ...requestBody, max_urls: 4 },
      { ...requestBody, confirm_live_fetch: false },
      { ...requestBody, url: 'https://evil.test' },
      { ...requestBody, fixture_json: 'fixture.json' },
    ]) {
      const response = await POST(makeRequest(body) as any)
      expect(response.status).toBe(400)
    }
    expect(fetchMock).not.toHaveBeenCalled()
  })

  test('forwards only the sanitized request and verified bearer token', async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(validBackendResponse()), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    const { POST } = await import('@/app/api/scrape/live-validation/route')
    const response = await POST(makeRequest(requestBody) as any)

    expect(response.status).toBe(200)
    expect(response.headers.get('Cache-Control')).toBe('no-store')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toMatch(/\/api\/scrape\/live-validation$/)
    expect(init.method).toBe('POST')
    expect(init.headers.Authorization).toBe('Bearer verified-admin-token')
    expect(JSON.parse(init.body)).toEqual(requestBody)
    expect(JSON.stringify(await response.json())).not.toContain('internal_path')
  })

  test('propagates backend status/detail without leaking unsafe path text', async () => {
    const { POST } = await import('@/app/api/scrape/live-validation/route')
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'validator is busy' }), {
      status: 409,
      headers: { 'Content-Type': 'application/json' },
    }))
    let response = await POST(makeRequest(requestBody) as any)
    expect(response.status).toBe(409)
    expect(await response.json()).toEqual({ detail: 'validator is busy' })

    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'failed at C:\\secret\\report.json' }), {
      status: 502,
      headers: { 'Content-Type': 'application/json' },
    }))
    response = await POST(makeRequest(requestBody) as any)
    expect(response.status).toBe(502)
    expect(JSON.stringify(await response.json())).not.toContain('secret')

    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'failed at /etc/passwd' }), {
      status: 502,
      headers: { 'Content-Type': 'application/json' },
    }))
    response = await POST(makeRequest(requestBody) as any)
    expect(response.status).toBe(502)
    expect(JSON.stringify(await response.json())).not.toContain('/etc/passwd')
  })

  test('fails closed for malformed success response or unavailable backend', async () => {
    const { POST } = await import('@/app/api/scrape/live-validation/route')
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ ...validBackendResponse(), bounded: false }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    let response = await POST(makeRequest(requestBody) as any)
    expect(response.status).toBe(502)

    const falseGreen = validBackendResponse()
    falseGreen.result.sample_results[0].http_status = 503
    falseGreen.result.sample_results[0].parse_status = 'http_error'
    falseGreen.result.sample_results[0].would_fix_columns = []
    falseGreen.result.sample_results[0].action = 'http_error'
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(falseGreen), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    response = await POST(makeRequest(requestBody) as any)
    expect(response.status).toBe(502)

    fetchMock.mockRejectedValueOnce(new Error('connection refused C:\\internal'))
    response = await POST(makeRequest(requestBody) as any)
    expect(response.status).toBe(503)
    expect(await response.json()).toEqual({ detail: 'live validation backend unavailable' })
  })
})
