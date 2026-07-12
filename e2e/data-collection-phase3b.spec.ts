import { expect, Page, test } from '@playwright/test'
import { mockSupabaseIdentity, setSupabaseTestSession } from './helpers/mock-api'

type BatchScenario = {
  jobId: string
  polls: Array<Record<string, unknown>>
}

const SUPABASE_ORIGIN = 'http://127.0.0.1:54321'

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
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ total_races: 123, total_horses: 456, latest_date: '2026-06-01' }),
    })
  )

  await page.route('**/api/scrape/history**', route =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ jobs: [] }) })
  )
}

async function mockBatchWorkflow(page: Page, scenarios: BatchScenario[], options?: { dryRunStatus?: Record<string, unknown> }) {
  let postIndex = 0
  const pollCount: Record<string, number> = {}

  await page.route('**/api/scrape', async route => {
    if (route.request().method() !== 'POST') return route.fallback()

    const body = route.request().postDataJSON() as Record<string, unknown>
    if (body?.dry_run === true) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ job_id: 'dry-job' }),
      })
    }

    const scenario = scenarios[postIndex]
    postIndex += 1
    if (!scenario) {
      return route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'missing scenario' }),
      })
    }

    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job_id: scenario.jobId }) })
  })

  await page.route('**/api/scrape/status/**', route => {
    const url = route.request().url()
    const jobId = url.split('/').pop() || ''

    if (jobId === 'dry-job') {
      const payload = options?.dryRunStatus ?? { status: 'running', progress: { done: 0, total: 1, message: 'dry-running' } }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(payload) })
    }

    const scenario = scenarios.find(s => s.jobId === jobId)
    if (!scenario) {
      return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ status: 'not_found' }) })
    }

    pollCount[jobId] = (pollCount[jobId] || 0) + 1
    const idx = Math.min(pollCount[jobId] - 1, scenario.polls.length - 1)
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(scenario.polls[idx]) })
  })
}

async function setSingleMonthRange(page: Page, month: string) {
  await page.getByTestId('start-period-input').fill(month)
  await page.getByTestId('end-period-input').fill(month)
}

test.describe('Phase3B Data Collection workflow', () => {
  let unexpectedExternalRequests: string[] = []
  let unexpectedAppApiRequests: string[] = []

  test.beforeEach(async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    unexpectedExternalRequests = []
    unexpectedAppApiRequests = []

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

    await page.route('**/api/**', route => {
      const url = route.request().url()
      if (!url.startsWith(`${baseURL}/api/`)) return route.fallback()
      unexpectedAppApiRequests.push(url)
      return route.fulfill({ status: 599, contentType: 'application/json', body: JSON.stringify({ detail: 'unexpected unmocked api' }) })
    })
  })

  test.afterEach(async () => {
    expect(unexpectedExternalRequests).toEqual([])
    expect(unexpectedAppApiRequests).toEqual([])
  })

  test('queued/running/multi-month-running do not show completed or quality bridge until terminal completed', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    await setupAuthorizedPage(page, baseURL)
    await mockBatchWorkflow(page, [
      { jobId: 'job-jan', polls: [{ status: 'completed', result: { races_collected: 2 } }] },
      {
        jobId: 'job-feb',
        polls: [
          { status: 'running', progress: { done: 4, total: 10, message: 'running feb' } },
          { status: 'completed', result: { races_collected: 3 } },
        ],
      },
    ])

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await page.getByTestId('start-period-input').fill('2026-01')
    await page.getByTestId('end-period-input').fill('2026-02')
    await page.getByTestId('execute-button').click()

    const statusPanel = page.getByTestId('batch-status-panel')
    await expect(statusPanel).toContainText('開始待ち')
    await expect(statusPanel).not.toContainText('取得完了')
    await expect(page.getByTestId('quality-bridge-card')).toHaveCount(0)

    await expect(statusPanel).toContainText('取得実行中')
    await expect(statusPanel).not.toContainText('取得完了')
    await expect(page.getByTestId('quality-bridge-card')).toHaveCount(0)

    await expect(statusPanel).toContainText('取得完了')
    await expect(page.getByTestId('quality-bridge-card')).toBeVisible()
  })

  test('busy operation locks form inputs and buttons', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    await setupAuthorizedPage(page, baseURL)
    await mockBatchWorkflow(page, [
      {
        jobId: 'job-busy',
        polls: [
          { status: 'running', progress: { done: 1, total: 10, message: 'running' } },
          { status: 'completed', result: { races_collected: 1 } },
        ],
      },
    ])

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await page.getByTestId('execute-button').click()

    await expect(page.getByTestId('start-period-input')).toBeDisabled()
    await expect(page.getByTestId('end-period-input')).toBeDisabled()
    await expect(page.getByTestId('force-rescrape-input')).toBeDisabled()
    await expect(page.getByTestId('dry-run-button')).toBeDisabled()
    await expect(page.getByTestId('execute-button')).toBeDisabled()
  })

  test('retry uses exactly the same request body for retry-safe error', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    const postBodies: Array<Record<string, unknown>> = []
    let postCount = 0

    await setupAuthorizedPage(page, baseURL)

    await page.route('**/api/scrape', async route => {
      if (route.request().method() !== 'POST') return route.fallback()
      const body = (route.request().postDataJSON() as Record<string, unknown>) || {}
      if (body.dry_run === true) {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job_id: 'dry-ignored' }) })
      }
      postBodies.push(body)
      postCount += 1
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ job_id: postCount === 1 ? 'job-error' : 'job-retry' }),
      })
    })

    await page.route('**/api/scrape/status/job-error', route =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'error', error: 'execution failed' }) })
    )
    await page.route('**/api/scrape/status/job-retry', route =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'completed', result: { races_collected: 2 } }) })
    )

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await page.getByTestId('execute-button').click()

    await expect(page.getByTestId('retry-button')).toBeVisible()
    await page.getByTestId('retry-button').click()
    await expect(page.getByTestId('batch-status-panel')).toContainText('取得完了')

    expect(postBodies).toHaveLength(2)
    expect(postBodies[0]).toEqual(postBodies[1])
  })

  test('monitoring uncertainty disables execute and hides retry', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    await setupAuthorizedPage(page, baseURL)

    await page.route('**/api/scrape', async route => {
      if (route.request().method() !== 'POST') return route.fallback()
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job_id: '' }) })
    })

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await page.getByTestId('execute-button').click()

    await expect(page.getByRole('alert').filter({ hasText: '実行状態を確認できません' }).first()).toBeVisible()
    await expect(page.getByRole('alert').filter({ hasText: '開始済みのサーバージョブは継続している可能性があります' }).first()).toBeVisible()
    await expect(page.getByTestId('retry-button')).toHaveCount(0)
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    await expect(page.getByTestId('refresh-history-button')).toBeEnabled()
  })

  test('invalid period is fail-closed and sends zero POST requests', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    let scrapePostCount = 0
    await setupAuthorizedPage(page, baseURL)

    await page.route('**/api/scrape', async route => {
      if (route.request().method() === 'POST') scrapePostCount += 1
      return route.fallback()
    })

    await page.goto('/data-collection')
    await page.getByTestId('start-period-input').fill('2026-02')
    await page.getByTestId('end-period-input').fill('2026-01')

    await expect(page.getByRole('alert').filter({ hasText: '期間エラー:' }).first()).toBeVisible()
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    expect(scrapePostCount).toBe(0)
  })

  for (const invalidValue of [null, '', false, '123']) {
    test(`malformed dry-run completed payload is rejected (${String(invalidValue)})`, async ({ page, baseURL }) => {
      if (!baseURL) throw new Error('Playwright baseURL is required')

      await setupAuthorizedPage(page, baseURL)

      await page.route('**/api/scrape', async route => {
        if (route.request().method() !== 'POST') return route.fallback()
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job_id: 'dry-invalid' }) })
      })

      await page.route('**/api/scrape/status/dry-invalid', route =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            status: 'completed',
            result: {
              fetch_summary: {
                dry_run: {
                  total_target_count: invalidValue,
                  unique_url_count: 1,
                  estimated_request_count: 1,
                  cache_hit_count: 0,
                  cache_miss_count: 1,
                  resume_hit_count: 0,
                  skipped_count: 0,
                  estimated_runtime_sec: 1.5,
                },
              },
            },
          }),
        })
      )

      await page.goto('/data-collection')
      await setSingleMonthRange(page, '2026-01')
      await page.getByTestId('dry-run-button').click()

      await expect(page.getByRole('alert').filter({ hasText: 'Dry-run失敗:' }).first()).toBeVisible()
      await expect(page.getByText('Dry-run 結果（実取得なし）')).toHaveCount(0)
    })
  }
})
