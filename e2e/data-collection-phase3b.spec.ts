import { expect, Page, Route, test } from '@playwright/test'
import { mockSupabaseIdentity, setSupabaseTestSession } from './helpers/mock-api'

type BatchScenario = {
  jobId: string
  polls: Array<Record<string, unknown>>
}

const SUPABASE_ORIGIN = process.env.NEXT_PUBLIC_SUPABASE_URL || 'http://127.0.0.1:54321'
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const acceptedParentIds = new Set<string>()
let acceptedParentPostCount = 0
const PARENT_STORAGE_KEY = 'keiba-ai-pro:server-scrape-batch:v1:e2e-user-id'

function acceptedParent(route: Route) {
  const body = route.request().postDataJSON() as Record<string, unknown>
  expect(body.server_batch).toBe(true)
  expect(body.job_id).toMatch(UUID)
  expect(body.operation_id).toMatch(UUID)
  expect(body.job_id).not.toBe(body.operation_id)
  acceptedParentIds.add(String(body.job_id))
  acceptedParentPostCount += 1
  return { job_id: body.job_id, operation_id: body.operation_id, server_batch: true, status: 'queued' }
}

function parentStatus(route: Route, payload: Record<string, unknown>) {
  const jobId = new URL(route.request().url()).pathname.split('/').pop() || ''
  expect(acceptedParentIds.has(jobId)).toBe(true)
  return { ...payload, job_id: jobId }
}

async function expectStoredParentLock(page: Page) {
  const jobId = [...acceptedParentIds].at(-1)
  expect(jobId).toMatch(UUID)
  await expect(page.getByTestId('execute-button')).toBeDisabled()
  await expect.poll(() => page.evaluate(key => {
    const stored = localStorage.getItem(key)
    return stored ? JSON.parse(stored).jobId : null
  }, PARENT_STORAGE_KEY)).toBe(jobId)
  expect(acceptedParentPostCount).toBe(1)
}

async function expectParentLockCleared(page: Page) {
  await expect.poll(() => page.evaluate(key => localStorage.getItem(key), PARENT_STORAGE_KEY)).toBeNull()
  await expect(page.getByTestId('execute-button')).toBeEnabled()
}

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

async function mockBatchWorkflow(
  page: Page,
  scenarios: BatchScenario[],
  options?: {
    dryRunStatus?: Record<string, unknown>
    onExecuteBody?: (body: Record<string, unknown>) => void
  },
) {
  let postIndex = 0
  const pollCount: Record<string, number> = {}
  const acceptedScenarios = new Map<string, BatchScenario>()

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

    options?.onExecuteBody?.(body)
    const scenario = scenarios[postIndex]
    postIndex += 1
    if (!scenario) {
      return route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'missing scenario' }),
      })
    }

    const accepted = acceptedParent(route)
    acceptedScenarios.set(String(accepted.job_id), scenario)
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(accepted) })
  })

  await page.route('**/api/scrape/status/**', route => {
    const url = route.request().url()
    const jobId = url.split('/').pop() || ''

    if (jobId === 'dry-job') {
      const payload = options?.dryRunStatus ?? { status: 'running', progress: { done: 0, total: 1, message: 'dry-running' } }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(payload) })
    }

    const scenario = acceptedScenarios.get(jobId)
    if (!scenario) {
      return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ status: 'not_found' }) })
    }

    pollCount[jobId] = (pollCount[jobId] || 0) + 1
    const idx = Math.min(pollCount[jobId] - 1, scenario.polls.length - 1)
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(parentStatus(route, scenario.polls[idx])) })
  })
}

async function setSingleMonthRange(page: Page, month: string) {
  await waitForPeriodInputs(page)
  await page.getByTestId('start-period-input').fill(month)
  await page.getByTestId('end-period-input').fill(month)
}

async function waitForPeriodInputs(page: Page) {
  await expect(page.getByTestId('start-period-input')).toBeEnabled({ timeout: 15_000 })
  await expect(page.getByTestId('end-period-input')).toBeEnabled({ timeout: 15_000 })
}

test.describe('Phase3B Data Collection workflow', () => {
  let unexpectedExternalRequests: string[] = []
  let unexpectedAppApiRequests: string[] = []

  test.beforeEach(async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    unexpectedExternalRequests = []
    unexpectedAppApiRequests = []
    acceptedParentIds.clear()
    acceptedParentPostCount = 0

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
    await expect(panel).toContainText('完了 · 0レース · 0頭')
    await expect(panel).not.toContainText('Dry-runはまだ処理中です')
    await expect(page.getByTestId('quality-bridge-card')).toHaveCount(0)
  })

  test('queued/running/multi-month-running中はcompleted表示へ早期遷移しない', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    await setupAuthorizedPage(page, baseURL)
    const parentScenario: BatchScenario = {
      jobId: 'two-month-parent',
      polls: [
        { status: 'queued', progress: { done: 0, total: 2000, completed_months: 0, total_months: 2 } },
        { status: 'running', progress: { done: 1400, total: 2000, completed_months: 1, total_months: 2, current_month: '2026-02', saved_races: 2, message: 'running feb' } },
      ],
    }
    const requests: Record<string, unknown>[] = []
    await mockBatchWorkflow(page, [parentScenario], { onExecuteBody: body => requests.push(body) })

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await waitForPeriodInputs(page)
    await page.getByTestId('start-period-input').fill('2026-01')
    await page.getByTestId('end-period-input').fill('2026-02')
    await page.getByTestId('execute-button').click()

    const statusPanel = page.getByTestId('batch-status-panel')
    await expect(statusPanel).toContainText('開始待ち')
    await expect(statusPanel).not.toContainText('完了 ·')
    await expect(page.getByTestId('quality-bridge-card')).toHaveCount(0)

    await expect(statusPanel).toContainText('取得実行中')
    await expect(statusPanel).not.toContainText('完了 ·')
    await expect(page.getByTestId('quality-bridge-card')).toHaveCount(0)
    await expect(statusPanel).toContainText('2026-02')
    expect(requests).toHaveLength(1)
    expect(requests[0]).toMatchObject({ start_date: '20260101', end_date: '20260228', server_batch: true })

    parentScenario.polls.push({ status: 'completed', result: { races_collected: 5, completed_months: 2, total_months: 2 } })
    await expect(statusPanel).toContainText('完了 ·')
    await expect(statusPanel).toContainText('5レース')
    expect(requests).toHaveLength(1)
  })

  test('実行中はフォーム入力と実行系ボタンをlockする', async ({ page, baseURL }) => {
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
    await expect(page.getByTestId('force-rescrape-input')).toHaveCount(0)
    await expect(page.getByTestId('dry-run-button')).toBeDisabled()
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    await expect(page.getByTestId('quality-bridge-card')).toHaveCount(0)
    await expect(page.getByTestId('batch-status-panel')).not.toContainText('完了 ·')

    await expect(page.getByTestId('batch-status-panel')).toContainText('完了 ·')
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

    await expect(page.getByTestId('dry-run-result')).toHaveCount(0)
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

      await expect(page.getByRole('alert').filter({ hasText: '確認失敗:' }).first()).toBeVisible()
      await expect(page.getByTestId('dry-run-result')).toHaveCount(0)
      await expect(page.getByText('0レース・正常完了')).toHaveCount(0)
      await expect(page.getByTestId('quality-bridge-card')).toHaveCount(0)
      expect(dryRunPostCount).toBe(1)
      expect(writePostCount).toBe(0)
    })
  }

  test('通常UIは保守導線を隠し、通常取得をforce_rescrape=falseで開始する', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    let executeBody: Record<string, unknown> | null = null
    await setupAuthorizedPage(page, baseURL)
    await mockBatchWorkflow(
      page,
      [{ jobId: 'job-ok', polls: [{ status: 'completed', result: { races_collected: 1 } }] }],
      { onExecuteBody: body => { executeBody = body } },
    )

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await page.getByTestId('execute-button').click()

    await expect(page.getByTestId('batch-status-panel')).toContainText('完了 ·')
    expect(executeBody).toMatchObject({ force_rescrape: false, server_batch: true })
    await expect(page.getByTestId('force-rescrape-input')).toHaveCount(0)
    await expect(page.getByTestId('quality-bridge-card')).toHaveCount(0)
    for (const name of ['Refresh Plan', 'P0 Repair Plan', 'Targeted Refetch Plan', 'Live Validation', 'Review Queue']) {
      await expect(page.getByRole('link', { name, exact: true })).toHaveCount(0)
    }
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
        body: JSON.stringify(acceptedParent(route)),
      })
    })

    await page.route('**/api/scrape/status/**', route => route.fulfill({
      status: 200,
      json: parentStatus(route, postCount === 1
        ? { status: 'error', error: 'backend failed detail' }
        : { status: 'completed', result: { races_collected: 2 } }),
    }))

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await page.getByTestId('execute-button').click()

    await expect(page.getByTestId('batch-status-panel')).toContainText('backend failed detail')
    await expect(page.getByTestId('retry-button')).toBeVisible()

    await page.getByTestId('retry-button').click()
    await expect(page.getByTestId('batch-status-panel')).toContainText('完了 ·')
    expect(postBodies).toHaveLength(2)
    const { job_id: firstJob, operation_id: firstOperation, ...firstSettings } = postBodies[0]
    const { job_id: secondJob, operation_id: secondOperation, ...secondSettings } = postBodies[1]
    expect(firstSettings).toEqual(secondSettings)
    // An explicitly failed parent is settled; a user-requested new run gets
    // new IDs instead of retrying the already-terminal idempotency key.
    expect(firstJob).not.toBe(secondJob)
    expect(firstOperation).not.toBe(secondOperation)
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
    await waitForPeriodInputs(page)
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
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(acceptedParent(route)) })
      })

      await page.route('**/api/scrape/status/**', route =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(parentStatus(route, { status: 'completed', result: { races_collected: invalidValue } })),
        })
      )

      page.on('dialog', dialog => dialog.accept())

      await page.goto('/data-collection')
      await setSingleMonthRange(page, '2026-01')
      await page.getByTestId('execute-button').click()

      await expect(page.getByTestId('batch-status-panel')).not.toContainText('完了 ·')
      await expect(page.getByText('0レース・正常完了')).toHaveCount(0)
      await expect(page.getByTestId('quality-bridge-card')).toHaveCount(0)
      await expect(page.getByTestId('batch-status-panel')).toContainText('完了結果を確認できません')
      await expect(page.getByTestId('reconnect-batch-button')).toBeVisible()
      await expect(page.getByTestId('uncertainty-panel')).toHaveCount(0)
      await expectStoredParentLock(page)
    })
  }

  test('monitoring lockはreload後も維持される', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    await setupAuthorizedPage(page, baseURL)

    await page.route('**/api/scrape', async route => {
      if (route.request().method() !== 'POST') return route.fallback()
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(acceptedParent(route)) })
    })

    await page.route('**/api/scrape/status/**', route =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(parentStatus(route, { status: 'completed', result: { races_collected: '8' } })) })
    )

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await page.getByTestId('execute-button').click()

    await expect(page.getByTestId('reconnect-batch-button')).toBeVisible()
    await expectStoredParentLock(page)
    const originalStoredParent = await page.evaluate(key => localStorage.getItem(key), PARENT_STORAGE_KEY)
    await page.reload()
    await expect(page.getByTestId('reconnect-batch-button')).toBeVisible()
    await expect(page.getByTestId('batch-status-panel')).toContainText('完了結果を確認できません')
    await expect.poll(() => page.evaluate(key => localStorage.getItem(key), PARENT_STORAGE_KEY)).toBe(originalStoredParent)
    await expect(page.getByTestId('retry-button')).toHaveCount(0)
    await expectStoredParentLock(page)
  })

  test('status再確認がqueued/runningならlock維持し、新規POSTしない', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    let scrapePostCount = 0
    let statusCallCount = 0
    await setupAuthorizedPage(page, baseURL)

    await page.route('**/api/scrape', async route => {
      if (route.request().method() !== 'POST') return route.fallback()
      scrapePostCount += 1
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(acceptedParent(route)) })
    })

    await page.route('**/api/scrape/status/**', route => {
      statusCallCount += 1
      if (statusCallCount === 1) {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(parentStatus(route, { status: 'completed', result: { races_collected: '8' } })) })
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(parentStatus(route, { status: 'queued' })) })
    })

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await page.getByTestId('execute-button').click()

    await expect(page.getByTestId('reconnect-batch-button')).toBeVisible()
    await page.getByTestId('reconnect-batch-button').click()
    await expect.poll(() => statusCallCount).toBeGreaterThan(1)
    await expect(page.getByTestId('batch-status-panel')).toContainText('開始待ち')
    await expectStoredParentLock(page)
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
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(acceptedParent(route)) })
    })

    await page.route('**/api/scrape/status/**', route => {
      statusCallCount += 1
      if (statusCallCount === 1) {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(parentStatus(route, { status: 'completed', result: { races_collected: '8' } })) })
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(parentStatus(route, { status: 'running', progress: { done: 2, total: 10, message: 'running' } })) })
    })

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await page.getByTestId('execute-button').click()

    await expect(page.getByTestId('reconnect-batch-button')).toBeVisible()
    await page.getByTestId('reconnect-batch-button').click()
    await expect(page.getByTestId('batch-status-panel')).toContainText('取得実行中')
    await expect(page.getByTestId('batch-status-panel')).toContainText('running')
    await expectStoredParentLock(page)
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
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(acceptedParent(route)) })
    })

    await page.route('**/api/scrape/status/**', route => {
      statusCallCount += 1
      if (statusCallCount === 1) {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(parentStatus(route, { status: 'completed', result: { races_collected: '8' } })) })
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(parentStatus(route, { status: 'not_found' })) })
    })

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await page.getByTestId('execute-button').click()
    await expect(page.getByTestId('reconnect-batch-button')).toBeVisible()
    await page.getByTestId('reconnect-batch-button').click()
    await expect.poll(() => statusCallCount).toBeGreaterThan(1)
    await expectStoredParentLock(page)
    await expect(page.getByTestId('batch-status-panel')).not.toContainText('完了 ·')
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
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(acceptedParent(route)) })
    })

    await page.route('**/api/scrape/status/**', route => {
      statusCallCount += 1
      if (statusCallCount === 1) {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(parentStatus(route, { status: 'completed', result: { races_collected: '8' } })) })
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(parentStatus(route, { status: 'completed', result: { races_collected: 'bad' } })) })
    })

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await page.getByTestId('execute-button').click()
    await expect(page.getByTestId('reconnect-batch-button')).toBeVisible()
    await page.getByTestId('reconnect-batch-button').click()
    await expect.poll(() => statusCallCount).toBeGreaterThan(1)
    await expect(page.getByTestId('batch-status-panel')).toContainText('完了結果を確認できません')
    await expect(page.getByTestId('reconnect-batch-button')).toBeVisible()
    await expectStoredParentLock(page)
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
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(acceptedParent(route)) })
    })

    await page.route('**/api/scrape/status/**', route => {
      if (scrapePostCount > 1) {
        return route.fulfill({ status: 200, json: parentStatus(route, { status: 'completed', result: { races_collected: 2 } }) })
      }
      statusCallCount += 1
      if (statusCallCount === 1) {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(parentStatus(route, { status: 'completed', result: { races_collected: '8' } })) })
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(parentStatus(route, { status: 'error', error: 'terminal failed' })) })
    })

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await page.getByTestId('execute-button').click()

    await expect(page.getByTestId('reconnect-batch-button')).toBeVisible()
    await expectStoredParentLock(page)
    expect(scrapePostCount).toBe(1)
    await page.getByTestId('reconnect-batch-button').click()
    await expect(page.getByTestId('batch-status-panel')).toContainText('terminal failed')
    await expect(page.getByTestId('reconnect-batch-button')).toHaveCount(0)
    await expectParentLockCleared(page)

    await page.getByTestId('execute-button').click()
    await expect(page.getByTestId('batch-status-panel')).toContainText('完了 ·')
    expect(scrapePostCount).toBe(2)
  })

  test('status再確認がvalid terminal completedなら結果を表示してlock解除し、次回execute可能', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    let scrapePostCount = 0
    let statusCallCount = 0
    await setupAuthorizedPage(page, baseURL)

    await page.route('**/api/scrape', async route => {
      if (route.request().method() !== 'POST') return route.fallback()
      scrapePostCount += 1
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(acceptedParent(route)) })
    })

    await page.route('**/api/scrape/status/**', route => {
      if (scrapePostCount > 1) {
        return route.fulfill({ status: 200, json: parentStatus(route, { status: 'completed', result: { races_collected: 1 } }) })
      }
      statusCallCount += 1
      if (statusCallCount === 1) {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(parentStatus(route, { status: 'completed', result: { races_collected: '8' } })) })
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(parentStatus(route, { status: 'completed', result: { races_collected: 3 } })) })
    })

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await page.getByTestId('execute-button').click()

    await expect(page.getByTestId('reconnect-batch-button')).toBeVisible()
    await expectStoredParentLock(page)
    expect(scrapePostCount).toBe(1)

    await page.getByTestId('reconnect-batch-button').click()
    await expect(page.getByTestId('reconnect-batch-button')).toHaveCount(0)
    await expect(page.getByTestId('quality-bridge-card')).toHaveCount(0)
    await expect(page.getByTestId('batch-status-panel')).toContainText('完了 · 3レース')
    await expectParentLockCleared(page)
    expect(scrapePostCount).toBe(1)

    await page.getByTestId('execute-button').click()
    await expect(page.getByTestId('batch-status-panel')).toContainText('完了 ·')
    expect(scrapePostCount).toBe(2)
  })
})
