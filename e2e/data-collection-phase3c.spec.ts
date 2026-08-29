import { expect, Page, test } from '@playwright/test'
import { mockSupabaseIdentity, setSupabaseTestSession } from './helpers/mock-api'

const SUPABASE_ORIGIN = 'http://127.0.0.1:54321'

type Scenario =
  | { kind: 'ok'; body: Record<string, unknown> }
  | { kind: 'status'; status: number; body: Record<string, unknown> }
  | { kind: 'malformed'; body: Record<string, unknown> }

async function setupAuthorizedPage(page: Page, baseURL: string) {
  await setSupabaseTestSession(page, {
    role: 'admin',
    tier: 'premium',
    appBaseUrl: baseURL,
    supabaseUrl: SUPABASE_ORIGIN,
  })
  await mockSupabaseIdentity(page, { authenticated: true, role: 'admin', tier: 'premium' })

  await page.route('**/api/scrape/health**', route =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'healthy' }) })
  )
  await page.route('**/api/data-stats**', route =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ total_races: 1, total_horses: 1, latest_date: '2026-07-01' }) })
  )
  await page.route('**/api/scrape/history**', route =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ jobs: [] }) })
  )
}

function successPayload(target: string, maxTargets = 10, unique = 2) {
  const makeSample = (url: string, bucket: 'result_page' | 'race_detail' | 'pedigree') => ({
    url,
    url_type: bucket,
    race_id: bucket === 'pedigree' ? null : '202601010101',
    horse_id: bucket === 'pedigree' ? '2018101234' : null,
    reason: 'true-missing',
    column: bucket === 'pedigree' ? 'sire' : 'finish_position',
    priority: 'P0',
    source: 'db',
    recommended_next_action: 'targeted refetch dry-run',
  })

  return {
    dry_run: true,
    read_only: true,
    execution_enabled: false,
    plan: {
      target,
      verdict: unique === 0 ? 'pass' : 'warn',
      verdict_reason: 'targeted-refetch-dry-run',
      p0_total_count: 10,
      refetch_candidate_count: unique,
      unique_url_count: unique,
      race_result_url_count: unique > 0 ? 1 : 0,
      race_detail_url_count: unique > 1 ? 1 : 0,
      horse_detail_url_count: 0,
      pedigree_url_count: unique > 2 ? 1 : 0,
      excluded_schema_review_count: 0,
      excluded_domain_allowed_count: 0,
      excluded_metadata_repair_count: 0,
      excluded_cache_available_count: 0,
      reparse_candidate_count: 0,
      estimated_http_request_count: unique,
      estimated_runtime_seconds: unique,
      sample_urls: {
        result_page: unique > 0 ? [makeSample('https://db.netkeiba.com/race/202601010101/', 'result_page')] : [],
        race_detail: unique > 1 ? [makeSample('https://db.netkeiba.com/race/202601010102/', 'race_detail')] : [],
        horse_detail: [],
        pedigree: unique > 2 ? [makeSample('https://db.netkeiba.com/horse/ped/2018101234/', 'pedigree')] : [],
      },
      recommended_next_actions: unique === 0 ? [] : ['next'],
      safety_flags: {
        read_only: true,
        no_db_write: true,
        no_http_access: true,
        no_scrape_execute: true,
        no_upsert: true,
        no_force_refresh_execute: true,
      },
    },
  }
}

test.describe('Phase3C Targeted Refetch Planning (read-only)', () => {
  let unexpectedExternalRequests: string[] = []
  let unexpectedAppApiRequests: string[] = []
  let targetedPostCount = 0
  let blockedWriteCount = 0

  test.beforeEach(async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    unexpectedExternalRequests = []
    unexpectedAppApiRequests = []
    targetedPostCount = 0
    blockedWriteCount = 0

    await page.route('**/*', route => {
      const url = route.request().url()
      if (url.startsWith(baseURL)) return route.fallback()
      if (url.startsWith('about:') || url.startsWith('blob:') || url.startsWith('data:')) return route.fallback()

      if (url.startsWith(SUPABASE_ORIGIN)) {
        const pathname = new URL(url).pathname
        if (pathname === '/auth/v1/user' || pathname === '/rest/v1/profiles') {
          return route.fallback()
        }
      }

      unexpectedExternalRequests.push(url)
      return route.abort('blockedbyclient')
    })

    await page.route('**/api/**', async route => {
      const req = route.request()
      const url = req.url()
      if (!url.startsWith(`${baseURL}/api/`)) return route.fallback()

      const path = url.replace(`${baseURL}`, '')
      if (path.startsWith('/api/scrape/targeted-refetch-plan')) {
        return route.fallback()
      }

      if (
        (path === '/api/scrape' && req.method() === 'POST') ||
        (path.startsWith('/api/scrape/refresh-plan') && req.method() === 'PUT') ||
        (path.startsWith('/api/scrape/p0-repair-plan') && req.method() === 'PUT') ||
        path.startsWith('/api/scrape/repair/') ||
        path.startsWith('/api/scrape/live-validation')
      ) {
        blockedWriteCount += 1
        return route.fulfill({ status: 599, contentType: 'application/json', body: JSON.stringify({ detail: 'write endpoint must not be called' }) })
      }

      unexpectedAppApiRequests.push(path)
      return route.fulfill({ status: 599, contentType: 'application/json', body: JSON.stringify({ detail: 'unexpected unmocked api' }) })
    })
  })

  test.afterEach(async () => {
    expect(unexpectedExternalRequests).toEqual([])
    expect(unexpectedAppApiRequests).toEqual([])
    expect(blockedWriteCount).toBe(0)
  })

  async function mountScenario(page: Page, scenario: Scenario, expectedTarget?: string) {
    await page.route('**/api/scrape/targeted-refetch-plan', async route => {
      const req = route.request()
      if (req.method() !== 'POST') {
        return route.fulfill({ status: 405, contentType: 'application/json', body: JSON.stringify({ error: 'method not allowed' }) })
      }

      targetedPostCount += 1
      const body = (req.postDataJSON() as Record<string, unknown>) || {}
      expect(body.target).toBe(expectedTarget ?? 'all')
      expect(typeof body.max_targets).toBe('number')

      if (scenario.kind === 'status') {
        return route.fulfill({ status: scenario.status, contentType: 'application/json', body: JSON.stringify(scenario.body) })
      }

      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(scenario.body) })
    })
  }

  test('navigation and nonzero plan success', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await setupAuthorizedPage(page, baseURL)
    await mountScenario(page, { kind: 'ok', body: successPayload('all', 10, 2) }, 'all')

    await page.goto('/data-collection')
    const link = page.getByRole('link', { name: 'Targeted Refetch Plan' }).first()
    await expect(link).toBeVisible()
    await expect(link).toHaveAttribute('href', '/data-collection/targeted-refetch-plan')

    await page.goto('/data-collection/targeted-refetch-plan')
    await expect(page).toHaveURL(/\/data-collection\/targeted-refetch-plan/)

    await page.getByRole('button', { name: 'Generate Read-only Plan' }).click()

    await expect(page.getByTestId('phase3c-status')).toContainText('success')
    await expect(page.getByTestId('phase3c-plan-summary')).toContainText('refetch_candidate_count: 2')
    await expect(page.getByTestId('phase3c-safety-flags')).toContainText('no_db_write: true')
    await expect(page.getByText('read-only / HTTPなし / DB writeなし')).toBeVisible()
    expect(targetedPostCount).toBe(1)
  })

  test('zero candidate success', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await setupAuthorizedPage(page, baseURL)
    await mountScenario(page, { kind: 'ok', body: successPayload('race', 5, 0) }, 'race')

    await page.goto('/data-collection/targeted-refetch-plan')
    await page.getByTestId('phase3c-target-select').selectOption('race')
    await page.getByTestId('phase3c-max-targets-input').fill('5')
    await page.getByTestId('phase3c-generate-button').click()

    await expect(page.getByTestId('phase3c-status')).toContainText('success')
    await expect(page.getByTestId('phase3c-plan-summary')).toContainText('refetch_candidate_count: 0')
    await expect(page.getByTestId('phase3c-next-actions')).toContainText('候補0件のため追加アクションはありません（正常完了）。')
    expect(targetedPostCount).toBe(1)
  })

  test('loading中は二重submit防止', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await setupAuthorizedPage(page, baseURL)

    let resolver: () => void = () => {}
    await page.route('**/api/scrape/targeted-refetch-plan', async route => {
      targetedPostCount += 1
      await new Promise<void>(resolve => {
        resolver = resolve
      })
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(successPayload('all', 10, 1)) })
    })

    await page.goto('/data-collection/targeted-refetch-plan')
    const button = page.getByTestId('phase3c-generate-button')
    await button.click()
    await expect(button).toBeDisabled()
    await button.click({ force: true })

    resolver()
    await expect(page.getByTestId('phase3c-status')).toContainText('success')
    expect(targetedPostCount).toBe(1)
  })

  test('malformed payload is fail-closed', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await setupAuthorizedPage(page, baseURL)

    const malformed = successPayload('all', 10, 1)
    ;(malformed.plan.safety_flags as any).no_http_access = false
    await mountScenario(page, { kind: 'malformed', body: malformed })

    await page.goto('/data-collection/targeted-refetch-plan')
    await page.getByRole('button', { name: 'Generate Read-only Plan' }).click()

    await expect(page.getByTestId('phase3c-status')).toContainText('error')
    await expect(page.getByTestId('phase3c-plan-summary')).toHaveCount(0)
  })

  for (const status of [401, 403, 503]) {
    test(`handles ${status} response`, async ({ page, baseURL }) => {
      if (!baseURL) throw new Error('Playwright baseURL is required')
      await setupAuthorizedPage(page, baseURL)
      await mountScenario(page, { kind: 'status', status, body: { detail: `detail ${status}` } })

      await page.goto('/data-collection/targeted-refetch-plan')
      await page.getByRole('button', { name: 'Generate Read-only Plan' }).click()

      await expect(page.getByTestId('phase3c-status')).toContainText('error')
      await expect(page.getByTestId('phase3c-error')).toBeVisible()
      await expect(page.getByTestId('phase3c-error')).toContainText(`detail ${status}`)
    })
  }

  test('invalid max_targets is blocked on client and sends zero request', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await setupAuthorizedPage(page, baseURL)
    await mountScenario(page, { kind: 'ok', body: successPayload('all', 10, 1) }, 'all')

    await page.goto('/data-collection/targeted-refetch-plan')
    const button = page.getByTestId('phase3c-generate-button')
    await page.getByTestId('phase3c-max-targets-input').fill('0')

    await expect(button).toBeDisabled()
    await expect(page.getByTestId('phase3c-input-error')).toContainText('max_targets must be an integer between 1 and 50')
    expect(targetedPostCount).toBe(0)
  })

  test('target/max_targets request body and no execute routes called', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await setupAuthorizedPage(page, baseURL)
    await mountScenario(page, { kind: 'ok', body: successPayload('pedigree', 3, 1) }, 'pedigree')

    await page.goto('/data-collection/targeted-refetch-plan')
    await page.getByTestId('phase3c-target-select').selectOption('pedigree')
    await page.getByTestId('phase3c-max-targets-input').fill('3')
    await page.getByTestId('phase3c-generate-button').click()

    await expect(page.getByTestId('phase3c-status')).toContainText('success')
    expect(targetedPostCount).toBe(1)
    expect(blockedWriteCount).toBe(0)
  })
})
