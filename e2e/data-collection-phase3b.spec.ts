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

  test('0レースも正常完了で、pending表示と区別される', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    await setupAuthorizedPage(page, baseURL)
    await mockBatchWorkflow(page, [{ jobId: 'job-zero', polls: [{ status: 'completed', result: { races_collected: 0 } }] }])

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await page.getByTestId('execute-button').click()

    const panel = page.getByTestId('batch-status-panel')
    await expect(panel).toContainText('取得完了: 0レース（0レース・正常完了）')
    await expect(panel).not.toContainText('Dry-runはまだ処理中です')
    await expect(page.getByTestId('quality-bridge-card')).toBeVisible()
  })

  test('queued/running/multi-month-running中はcompleted/quality bridgeを表示しない', async ({ page, baseURL }) => {
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
    await expect(statusPanel).toContainText('5レース')
  })

  test('実行中はフォーム入力と実行系ボタンをlockし、completed前にbridgeを表示しない', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    await setupAuthorizedPage(page, baseURL)
    await mockBatchWorkflow(page, [
      {
        jobId: 'job-running-lock',
        polls: [
          { status: 'running', progress: { done: 1, total: 10, message: 'running' } },
          { status: 'running', progress: { done: 6, total: 10, message: 'running' } },
          { status: 'completed', result: { races_collected: 2 } },
        ],
      },
    ])

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await page.getByTestId('execute-button').click()

    await expect(page.getByTestId('batch-status-panel')).toContainText('取得実行中')
    await expect(page.getByTestId('start-period-input')).toBeDisabled()
    await expect(page.getByTestId('end-period-input')).toBeDisabled()
    await expect(page.getByTestId('force-rescrape-input')).toBeDisabled()
    await expect(page.getByTestId('dry-run-button')).toBeDisabled()
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    await expect(page.getByTestId('quality-bridge-card')).toHaveCount(0)
    await expect(page.getByTestId('batch-status-panel')).not.toContainText('取得完了')

    await expect(page.getByTestId('batch-status-panel')).toContainText('取得完了')
  })

  test('Dry-runが非終端のまま上限到達時は0件カードを表示しない', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    await setupAuthorizedPage(page, baseURL)
    await mockBatchWorkflow(page, [], {
      dryRunStatus: { status: 'running', progress: { done: 0, total: 1, message: 'still running' } },
    })

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await expect(page.getByTestId('dry-run-button')).toBeEnabled()
    await page.getByTestId('dry-run-button').click()

    await expect(page.getByText('Dry-run 結果（実取得なし）')).toHaveCount(0)
    await expect(page.getByText('0レース・正常完了')).toHaveCount(0)
  })

  for (const invalidValue of [null, '', false, '123']) {
    test(`malformed dry-run completed payload is rejected (${String(invalidValue)})`, async ({ page, baseURL }) => {
      if (!baseURL) throw new Error('Playwright baseURL is required')

      let dryRunPostCount = 0
      let writePostCount = 0
      await setupAuthorizedPage(page, baseURL)

      await page.route('**/api/scrape', async route => {
        if (route.request().method() !== 'POST') return route.fallback()
        const body = (route.request().postDataJSON() as Record<string, unknown>) || {}
        if (body.dry_run === true) {
          dryRunPostCount += 1
          return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job_id: 'dry-invalid' }) })
        }
        writePostCount += 1
        return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'unexpected write' }) })
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
                  estimated_runtime_sec: 1,
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
      await expect(page.getByText('0レース・正常完了')).toHaveCount(0)
      await expect(page.getByTestId('quality-bridge-card')).toHaveCount(0)
      expect(dryRunPostCount).toBe(1)
      expect(writePostCount).toBe(0)
    })
  }

  test('quality bridge本文とリンク先を維持', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    await setupAuthorizedPage(page, baseURL)
    await mockBatchWorkflow(page, [{ jobId: 'job-ok', polls: [{ status: 'completed', result: { races_collected: 1 } }] }])

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await page.getByTestId('execute-button').click()

    const bridge = page.getByTestId('quality-bridge-card')
    await expect(bridge).toBeVisible()
    await expect(bridge).toContainText('取得は完了しましたが、品質確認は未実施です')
    await expect(page.getByTestId('quality-bridge-refresh-link')).toHaveAttribute('href', '/data-collection/refresh-plan')
    await expect(page.getByTestId('quality-bridge-p0-link')).toHaveAttribute('href', '/data-collection/p0-repair-plan')
  })

  test('retry前にbackend error detailが保持される', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    const postBodies: Array<Record<string, unknown>> = []
    let postCount = 0

    await setupAuthorizedPage(page, baseURL)

    await page.route('**/api/scrape', async route => {
      if (route.request().method() !== 'POST') return route.fallback()
      const body = (route.request().postDataJSON() as Record<string, unknown>) || {}
      postBodies.push(body)
      postCount += 1
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ job_id: postCount === 1 ? 'job-error' : 'job-retry' }),
      })
    })

    await page.route('**/api/scrape/status/job-error', route =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'error', error: 'backend failed detail' }) })
    )
    await page.route('**/api/scrape/status/job-retry', route =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'completed', result: { races_collected: 2 } }) })
    )

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await page.getByTestId('execute-button').click()

    await expect(page.getByTestId('batch-status-panel')).toContainText('backend failed detail')
    await expect(page.getByTestId('retry-button')).toBeVisible()

    await page.getByTestId('retry-button').click()
    await expect(page.getByTestId('batch-status-panel')).toContainText('取得完了')
    expect(postBodies).toHaveLength(2)
    expect(postBodies[0]).toEqual(postBodies[1])
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

  for (const invalidValue of [null, '', false, '8']) {
    test(`malformed execute completed payload is rejected (${String(invalidValue)})`, async ({ page, baseURL }) => {
      if (!baseURL) throw new Error('Playwright baseURL is required')

      await setupAuthorizedPage(page, baseURL)

      await page.route('**/api/scrape', async route => {
        if (route.request().method() !== 'POST') return route.fallback()
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job_id: 'job-malformed' }) })
      })

      await page.route('**/api/scrape/status/job-malformed', route =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ status: 'completed', result: { races_collected: invalidValue } }),
        })
      )

      page.on('dialog', dialog => dialog.accept())

      await page.goto('/data-collection')
      await setSingleMonthRange(page, '2026-01')
      await page.getByTestId('execute-button').click()

      await expect(page.getByTestId('batch-status-panel')).not.toContainText('取得完了')
      await expect(page.getByText('0レース・正常完了')).toHaveCount(0)
      await expect(page.getByTestId('quality-bridge-card')).toHaveCount(0)
      await expect(page.getByTestId('uncertainty-panel')).toBeVisible()
      await expect(page.getByTestId('execute-button')).toBeDisabled()
    })
  }

  test('monitoring lockはreload後も維持される', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    await setupAuthorizedPage(page, baseURL)

    await page.route('**/api/scrape', async route => {
      if (route.request().method() !== 'POST') return route.fallback()
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job_id: 'job-reload' }) })
    })

    await page.route('**/api/scrape/status/job-reload', route =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'completed', result: { races_collected: '8' } }) })
    )

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await page.getByTestId('execute-button').click()

    await expect(page.getByTestId('uncertainty-panel')).toBeVisible()
    await page.reload()
    await expect(page.getByTestId('uncertainty-panel')).toBeVisible()
    await expect(page.getByTestId('retry-button')).toHaveCount(0)
    await expect(page.getByTestId('execute-button')).toBeDisabled()
  })

  test('status再確認がqueued/runningならlock維持し、新規POSTしない', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    let scrapePostCount = 0
    let statusCallCount = 0
    await setupAuthorizedPage(page, baseURL)

    await page.route('**/api/scrape', async route => {
      if (route.request().method() !== 'POST') return route.fallback()
      scrapePostCount += 1
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job_id: 'job-reconcile-qr' }) })
    })

    await page.route('**/api/scrape/status/job-reconcile-qr', route => {
      statusCallCount += 1
      if (statusCallCount === 1) {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'completed', result: { races_collected: '8' } }) })
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'queued' }) })
    })

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await page.getByTestId('execute-button').click()

    await expect(page.getByTestId('uncertainty-panel')).toBeVisible()
    await page.getByTestId('reconcile-status-button').click()
    await expect(page.getByTestId('uncertainty-panel')).toContainText('lockを維持')
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    expect(scrapePostCount).toBe(1)
  })

  test('status再確認がrunningならlock維持し、新規POSTしない', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    let scrapePostCount = 0
    let statusCallCount = 0
    await setupAuthorizedPage(page, baseURL)

    await page.route('**/api/scrape', async route => {
      if (route.request().method() !== 'POST') return route.fallback()
      scrapePostCount += 1
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job_id: 'job-reconcile-running' }) })
    })

    await page.route('**/api/scrape/status/job-reconcile-running', route => {
      statusCallCount += 1
      if (statusCallCount === 1) {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'completed', result: { races_collected: '8' } }) })
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'running', progress: { done: 2, total: 10, message: 'running' } }) })
    })

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await page.getByTestId('execute-button').click()

    await expect(page.getByTestId('uncertainty-panel')).toBeVisible()
    await page.getByTestId('reconcile-status-button').click()
    await expect(page.getByTestId('uncertainty-panel')).toContainText('lockを維持')
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    expect(scrapePostCount).toBe(1)
  })

  test('status再確認がnot_foundならlock維持し、新規POSTしない', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    let scrapePostCount = 0
    let statusCallCount = 0
    await setupAuthorizedPage(page, baseURL)

    await page.route('**/api/scrape', async route => {
      if (route.request().method() !== 'POST') return route.fallback()
      scrapePostCount += 1
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job_id: 'job-reconcile-notfound' }) })
    })

    await page.route('**/api/scrape/status/job-reconcile-notfound', route => {
      statusCallCount += 1
      if (statusCallCount === 1) {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'completed', result: { races_collected: '8' } }) })
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'not_found' }) })
    })

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await page.getByTestId('execute-button').click()
    await page.getByTestId('reconcile-status-button').click()

    await expect(page.getByTestId('uncertainty-panel')).toContainText('lockを維持')
    expect(scrapePostCount).toBe(1)
  })

  test('status再確認がcompleted malformedならlock維持', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    let scrapePostCount = 0
    let statusCallCount = 0
    await setupAuthorizedPage(page, baseURL)

    await page.route('**/api/scrape', async route => {
      if (route.request().method() !== 'POST') return route.fallback()
      scrapePostCount += 1
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job_id: 'job-reconcile-malformed-completed' }) })
    })

    await page.route('**/api/scrape/status/job-reconcile-malformed-completed', route => {
      statusCallCount += 1
      if (statusCallCount === 1) {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'completed', result: { races_collected: '8' } }) })
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'completed', result: { races_collected: 'bad' } }) })
    })

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await page.getByTestId('execute-button').click()
    await page.getByTestId('reconcile-status-button').click()

    await expect(page.getByTestId('uncertainty-panel')).toContainText('状態確認が必要')
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    expect(scrapePostCount).toBe(1)
  })

  test('status再確認がterminal errorならlock解除', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    let scrapePostCount = 0
    let statusCallCount = 0
    await setupAuthorizedPage(page, baseURL)

    await page.route('**/api/scrape', async route => {
      if (route.request().method() !== 'POST') return route.fallback()
      scrapePostCount += 1
      const jobId = scrapePostCount === 1 ? 'job-reconcile-error' : 'job-reconcile-error-new'
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job_id: jobId }) })
    })

    await page.route('**/api/scrape/status/job-reconcile-error', route => {
      statusCallCount += 1
      if (statusCallCount === 1) {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'completed', result: { races_collected: '8' } }) })
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'error', error: 'terminal failed' }) })
    })

    await page.route('**/api/scrape/status/job-reconcile-error-new', route =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'completed', result: { races_collected: 2 } }) })
    )

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await page.getByTestId('execute-button').click()

    await expect(page.getByTestId('uncertainty-panel')).toBeVisible()
    expect(scrapePostCount).toBe(1)
    await page.getByTestId('reconcile-status-button').click()
    await expect(page.getByTestId('uncertainty-panel')).toHaveCount(0)
    await expect(page.getByTestId('execute-button')).toBeEnabled()

    await page.getByTestId('execute-button').click()
    await expect(page.getByTestId('batch-status-panel')).toContainText('取得完了')
    expect(scrapePostCount).toBe(2)
  })

  test('status再確認がvalid terminal completedならlock解除し、自動昇格せず次回execute可能', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    let scrapePostCount = 0
    let statusCallCount = 0
    await setupAuthorizedPage(page, baseURL)

    await page.route('**/api/scrape', async route => {
      if (route.request().method() !== 'POST') return route.fallback()
      scrapePostCount += 1
      const jobId = scrapePostCount === 1 ? 'job-reconcile-completed' : 'job-reconcile-completed-new'
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job_id: jobId }) })
    })

    await page.route('**/api/scrape/status/job-reconcile-completed', route => {
      statusCallCount += 1
      if (statusCallCount === 1) {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'completed', result: { races_collected: '8' } }) })
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'completed', result: { races_collected: 3 } }) })
    })

    await page.route('**/api/scrape/status/job-reconcile-completed-new', route =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'completed', result: { races_collected: 1 } }) })
    )

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await page.getByTestId('execute-button').click()

    await expect(page.getByTestId('uncertainty-panel')).toBeVisible()
    expect(scrapePostCount).toBe(1)

    await page.getByTestId('reconcile-status-button').click()
    await expect(page.getByTestId('uncertainty-panel')).toHaveCount(0)
    await expect(page.getByTestId('quality-bridge-card')).toHaveCount(0)
    await expect(page.getByTestId('batch-status-panel')).not.toContainText('取得完了: 3レース')
    expect(scrapePostCount).toBe(1)

    await page.getByTestId('execute-button').click()
    await expect(page.getByTestId('batch-status-panel')).toContainText('取得完了')
    expect(scrapePostCount).toBe(2)
  })
})
