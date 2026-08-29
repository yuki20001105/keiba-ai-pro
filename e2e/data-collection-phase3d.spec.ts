import { expect, Page, test } from '@playwright/test'
import { mockSupabaseIdentity, setSupabaseTestSession } from './helpers/mock-api'

const SUPABASE_ORIGIN = 'http://127.0.0.1:54321'

async function authorizeAdmin(page: Page, baseURL: string) {
  await setSupabaseTestSession(page, {
    role: 'admin',
    tier: 'premium',
    appBaseUrl: baseURL,
    supabaseUrl: SUPABASE_ORIGIN,
  })
  await mockSupabaseIdentity(page, { authenticated: true, role: 'admin', tier: 'premium' })
  await page.route('**/api/scrape/health**', route => route.fulfill({ status: 200, json: { status: 'healthy' } }))
  await page.route('**/api/data-stats**', route => route.fulfill({ status: 200, json: { total_races: 1, total_horses: 1, latest_date: '2026-07-01' } }))
  await page.route('**/api/scrape/history**', route => route.fulfill({ status: 200, json: { jobs: [] } }))
}

function livePayload(maxUrls = 1, mode: 'pass' | 'zero' | 'partial' = 'pass') {
  const attempted = mode === 'zero' ? 0 : mode === 'partial' ? 2 : 1
  const httpErrors = mode === 'partial' ? 1 : 0
  const parseErrors = mode === 'partial' ? 1 : 0
  const samples = [
    {
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
    },
    {
      url: 'https://db.netkeiba.com/horse/ped/2021100002/',
      url_type: 'pedigree',
      race_id: null,
      horse_id: '2021100002',
      http_status: 503,
      parse_status: 'http_error',
      missing_fields_before: ['sire'],
      fields_found_after: [],
      would_fix_columns: [],
      action: 'http_error',
      reason: 'true-missing',
      recommended_next_action: 'review bounded evidence',
    },
  ].slice(0, attempted)

  return {
    live_validation: true,
    bounded: true,
    external_http: true,
    read_only: true,
    execution_enabled: false,
    result: {
      target: 'all',
      url_type: 'all',
      max_urls_applied: maxUrls,
      attempted_url_count: attempted,
      http_success_count: attempted - httpErrors,
      http_error_count: httpErrors,
      parse_success_count: attempted - parseErrors,
      parse_error_count: parseErrors,
      would_fix_count: attempted > 0 ? 1 : 0,
      would_not_fix_count: attempted > 0 ? attempted - 1 : 0,
      no_downgrade_count: 0,
      repairable_count: attempted > 0 ? 1 : 0,
      elapsed_seconds: attempted,
      estimated_full_refetch_runtime_seconds: attempted * 2,
      excluded_schema_review_count: 0,
      excluded_domain_allowed_count: 0,
      excluded_metadata_repair_count: 0,
      excluded_cache_available_count: 0,
      sample_results: samples,
      recommended_next_actions: attempted > 0 ? ['review bounded evidence'] : ['no eligible target'],
      rate_limit_policy: {
        max_urls: maxUrls,
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
      verdict: mode === 'zero' ? 'warn' : 'pass',
      verdict_reason: 'small-live-validation',
    },
  }
}

test.describe('Phase 3D bounded live validation', () => {
  let livePostCount = 0
  let forbiddenWriteCount = 0
  let unexpectedApi: string[] = []
  let unexpectedExternal: string[] = []

  test.beforeEach(async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    livePostCount = 0
    forbiddenWriteCount = 0
    unexpectedApi = []
    unexpectedExternal = []

    await page.route('**/*', route => {
      const url = route.request().url()
      if (url.startsWith(baseURL) || url.startsWith('about:') || url.startsWith('blob:') || url.startsWith('data:')) return route.fallback()
      if (url.startsWith(SUPABASE_ORIGIN)) {
        const pathname = new URL(url).pathname
        if (pathname === '/auth/v1/user' || pathname === '/rest/v1/profiles') return route.fallback()
      }
      unexpectedExternal.push(url)
      return route.abort('blockedbyclient')
    })

    await page.route('**/api/**', route => {
      const request = route.request()
      const url = request.url()
      if (!url.startsWith(`${baseURL}/api/`)) return route.fallback()
      const path = url.slice(baseURL.length)
      if (path.startsWith('/api/scrape/live-validation')) return route.fallback()
      if (
        (path === '/api/scrape' && request.method() === 'POST') ||
        path.startsWith('/api/scrape/repair/') ||
        path.startsWith('/api/scrape/refresh-plan') ||
        path.startsWith('/api/scrape/p0-repair-plan') ||
        path.startsWith('/api/scrape/targeted-refetch-plan')
      ) {
        forbiddenWriteCount += 1
        return route.fulfill({ status: 599, json: { detail: 'forbidden endpoint called' } })
      }
      unexpectedApi.push(path)
      return route.fulfill({ status: 599, json: { detail: 'unexpected unmocked API' } })
    })
  })

  test.afterEach(() => {
    expect(forbiddenWriteCount).toBe(0)
    expect(unexpectedApi).toEqual([])
    expect(unexpectedExternal).toEqual([])
  })

  async function mockLive(page: Page, options: { status?: number; body?: Record<string, unknown>; delay?: Promise<void> } = {}) {
    await page.route('**/api/scrape/live-validation', async route => {
      livePostCount += 1
      if (options.delay) await options.delay
      return route.fulfill({
        status: options.status ?? 200,
        contentType: 'application/json',
        body: JSON.stringify(options.body ?? livePayload()),
      })
    })
  }

  async function openAndConfirm(page: Page, baseURL: string) {
    await authorizeAdmin(page, baseURL)
    await page.goto('/data-collection/live-validation')
    await expect(page.getByTestId('phase3d-safety-notice')).toContainText('DB repair / upsert / production table write')
    await page.getByTestId('phase3d-confirm').check()
  }

  test('navigation is explicit and no request runs before confirmation', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await authorizeAdmin(page, baseURL)
    await mockLive(page)
    await page.goto('/data-collection')
    const link = page.getByTestId('phase3d-header-link')
    await expect(link).toHaveAttribute('href', '/data-collection/live-validation')
    await link.click()
    await expect(page.getByTestId('phase3d-run')).toBeDisabled()
    expect(livePostCount).toBe(0)
  })

  test('confirmed success sends only the exact bounded body', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await openAndConfirm(page, baseURL)
    await page.route('**/api/scrape/live-validation', async route => {
      livePostCount += 1
      expect(route.request().method()).toBe('POST')
      expect(route.request().postDataJSON()).toEqual({ target: 'all', url_type: 'all', max_urls: 1, confirm_live_fetch: true })
      return route.fulfill({ status: 200, json: livePayload() })
    })
    await page.getByTestId('phase3d-run').click()
    await expect(page.getByTestId('phase3d-state')).toContainText('pass')
    await expect(page.getByTestId('phase3d-summary')).toContainText('would fix 1')
    await expect(page.getByTestId('phase3d-runtime-policy')).toContainText('no_db_write=true')
    expect(livePostCount).toBe(1)
  })

  test('loading disables controls and blocks duplicate submission', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    let release!: () => void
    const delay = new Promise<void>(resolve => { release = resolve })
    await openAndConfirm(page, baseURL)
    await mockLive(page, { delay })
    const button = page.getByTestId('phase3d-run')
    await button.click()
    await expect(button).toBeDisabled()
    await button.click({ force: true })
    expect(livePostCount).toBe(1)
    release()
    await expect(page.getByTestId('phase3d-state')).toContainText('pass')
  })

  test('zero eligible targets is a warn result, not a fabricated validation', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await openAndConfirm(page, baseURL)
    await mockLive(page, { body: livePayload(1, 'zero') })
    await page.getByTestId('phase3d-run').click()
    await expect(page.getByTestId('phase3d-state')).toContainText('warn')
    await expect(page.getByTestId('phase3d-zero-targets')).toContainText('対象URLは0件')
  })

  test('valid partial result remains visibly distinct from API success', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await openAndConfirm(page, baseURL)
    await page.getByTestId('phase3d-max-urls').fill('2')
    await mockLive(page, { body: livePayload(2, 'partial') })
    await page.getByTestId('phase3d-run').click()
    await expect(page.getByTestId('phase3d-state')).toContainText('partial')
    await expect(page.getByTestId('phase3d-partial')).toContainText('別判定')
    await expect(page.getByTestId('phase3d-summary')).toContainText('HTTP error 1')
  })

  test('invalid max_urls never sends a request', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await openAndConfirm(page, baseURL)
    await mockLive(page)
    for (const value of ['0', '4', '1.5']) {
      await page.getByTestId('phase3d-max-urls').fill(value)
      await expect(page.getByTestId('phase3d-run')).toBeDisabled()
    }
    expect(livePostCount).toBe(0)
  })

  for (const status of [401, 403, 503]) {
    test(`surfaces fail-closed auth/backend status ${status}`, async ({ page, baseURL }) => {
      if (!baseURL) throw new Error('Playwright baseURL is required')
      await openAndConfirm(page, baseURL)
      await mockLive(page, { status, body: { detail: `blocked ${status}` } })
      await page.getByTestId('phase3d-run').click()
      await expect(page.getByTestId('phase3d-state')).toContainText('error')
      await expect(page.getByTestId('phase3d-error')).toContainText(`blocked ${status}`)
    })
  }

  test('busy response is distinct and does not retry automatically', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await openAndConfirm(page, baseURL)
    await mockLive(page, { status: 429, body: { detail: 'cooldown active' } })
    await page.getByTestId('phase3d-run').click()
    await expect(page.getByTestId('phase3d-state')).toContainText('busy')
    expect(livePostCount).toBe(1)
  })

  test('malformed success payload fails closed with no result cards', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await openAndConfirm(page, baseURL)
    const malformed = livePayload()
    malformed.result.safety_flags.no_db_write = false
    await mockLive(page, { body: malformed })
    await page.getByTestId('phase3d-run').click()
    await expect(page.getByTestId('phase3d-state')).toContainText('error')
    await expect(page.getByTestId('phase3d-summary')).toHaveCount(0)
  })

  test('false-green sample and aggregate contradiction fails closed', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await openAndConfirm(page, baseURL)
    const contradictory = livePayload()
    contradictory.result.sample_results[0].http_status = 503
    contradictory.result.sample_results[0].parse_status = 'http_error'
    contradictory.result.sample_results[0].would_fix_columns = []
    contradictory.result.sample_results[0].action = 'http_error'
    await mockLive(page, { body: contradictory })
    await page.getByTestId('phase3d-run').click()
    await expect(page.getByTestId('phase3d-state')).toContainText('error')
    await expect(page.getByTestId('phase3d-summary')).toHaveCount(0)
    expect(livePostCount).toBe(1)
  })
})
