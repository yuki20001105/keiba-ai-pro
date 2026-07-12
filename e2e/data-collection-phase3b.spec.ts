import { test, expect, Page } from '@playwright/test'
import { mockSupabaseIdentity, setSupabaseTestSession } from './helpers/mock-api'

type BatchScenario = {
  jobId: string
  polls: Array<Record<string, unknown>>
}

async function setupAuthorizedPage(page: Page, baseURL: string) {
  await setSupabaseTestSession(page, {
    role: 'admin',
    tier: 'premium',
    appBaseUrl: baseURL,
    supabaseUrl: 'http://127.0.0.1:54321',
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

async function mockBatchWorkflow(page: Page, scenarios: BatchScenario[]) {
  let postIndex = 0
  const pollCount: Record<string, number> = {}

  await page.route('**/api/scrape', async route => {
    if (route.request().method() !== 'POST') {
      return route.fallback()
    }

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
      return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'missing scenario' }) })
    }

    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job_id: scenario.jobId }) })
  })

  await page.route('**/api/scrape/status/**', route => {
    const url = route.request().url()
    const jobId = url.split('/').pop() || ''

    if (jobId === 'dry-job') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'running', progress: { done: 0, total: 1, message: 'dry-running' } }),
      })
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
  await page.locator('input[type="month"]').first().fill(month)
  await page.locator('input[type="month"]').nth(1).fill(month)
}

test.describe('Phase3B Data Collection workflow', () => {
  test('queued/running/completed, quality bridge, and no quality API auto-call', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    let refreshApiCalls = 0
    let p0ApiCalls = 0

    await setupAuthorizedPage(page, baseURL)
    await mockBatchWorkflow(page, [
      {
        jobId: 'job-main',
        polls: [
          { status: 'queued', progress: { done: 0, total: 10, message: 'queued' } },
          { status: 'running', progress: { done: 5, total: 10, message: 'running' } },
          { status: 'completed', result: { races_collected: 8 } },
        ],
      },
    ])

    await page.route('**/api/data-collection/refresh-plan**', route => {
      refreshApiCalls += 1
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) })
    })
    await page.route('**/api/data-collection/p0-repair-plan**', route => {
      p0ApiCalls += 1
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) })
    })

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await page.getByRole('button', { name: '取得開始' }).click()

    const statusPanel = page.getByTestId('batch-status-panel')
    await expect(statusPanel).toContainText('開始待ち')
    await expect(statusPanel).toContainText('取得実行中')
    await expect(statusPanel).toContainText('取得完了')

    const qualityCard = page.getByTestId('quality-bridge-card')
    await expect(qualityCard).toBeVisible()
    await expect(qualityCard.getByText('取得は完了しましたが、品質確認は未実施です')).toBeVisible()

    await expect(page.getByTestId('quality-bridge-refresh-link')).toHaveAttribute('href', '/data-collection/refresh-plan')
    await expect(page.getByTestId('quality-bridge-p0-link')).toHaveAttribute('href', '/data-collection/p0-repair-plan')

    expect(refreshApiCalls).toBe(0)
    expect(p0ApiCalls).toBe(0)
  })

  test('completed zero is shown as normal completion and distinct from pending', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    await setupAuthorizedPage(page, baseURL)
    await mockBatchWorkflow(page, [
      {
        jobId: 'job-zero',
        polls: [
          { status: 'queued', progress: { done: 0, total: 10, message: 'queued' } },
          { status: 'completed', result: { races_collected: 0 } },
        ],
      },
    ])

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await page.getByRole('button', { name: '取得開始' }).click()

    await expect(page.getByTestId('batch-status-panel')).toContainText('0レース・正常完了')
    await expect(page.getByText('Dry-runはまだ処理中です。しばらく待って再実行してください。')).not.toBeVisible()
  })

  test('error is persisted on screen and can re-run', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    let postCount = 0
    const pollCount: Record<string, number> = {}

    await setupAuthorizedPage(page, baseURL)

    await page.route('**/api/scrape', async route => {
      if (route.request().method() !== 'POST') return route.fallback()
      postCount += 1
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ job_id: postCount === 1 ? 'job-error' : 'job-retry' }),
      })
    })

    await page.route('**/api/scrape/status/**', route => {
      const jobId = route.request().url().split('/').pop() || ''
      pollCount[jobId] = (pollCount[jobId] || 0) + 1
      if (jobId === 'job-error') {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'error', error: 'backend error detail' }) })
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'completed', result: { races_collected: 3 } }) })
    })

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await setSingleMonthRange(page, '2026-01')
    await page.getByRole('button', { name: '取得開始' }).click()

    await expect(page.locator('[data-testid="batch-status-panel"] [role="alert"]').first()).toContainText('取得失敗: backend error detail')
    await page.getByRole('button', { name: '再実行' }).click()
    await expect(page.getByTestId('batch-status-panel')).toContainText('取得完了')
  })

  test('multiple months do not become globally completed on first month completion', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    await setupAuthorizedPage(page, baseURL)
    await mockBatchWorkflow(page, [
      {
        jobId: 'job-jan',
        polls: [{ status: 'completed', result: { races_collected: 2 } }],
      },
      {
        jobId: 'job-feb',
        polls: [
          { status: 'running', progress: { done: 2, total: 10, message: 'running feb' } },
          { status: 'completed', result: { races_collected: 4 } },
        ],
      },
    ])

    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await page.locator('input[type="month"]').first().fill('2026-01')
    await page.locator('input[type="month"]').nth(1).fill('2026-02')
    await page.getByRole('button', { name: '取得開始' }).click()

    await expect(page.getByTestId('batch-status-panel')).toContainText('取得実行中')
    await expect(page.getByTestId('batch-status-panel')).toContainText('取得完了')
    await expect(page.getByText('6レース', { exact: true })).toBeVisible()
  })

  test('dry-run pending at polling limit does not show completed-zero card', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')

    await setupAuthorizedPage(page, baseURL)
    await mockBatchWorkflow(page, [])

    await page.goto('/data-collection')
    await page.getByRole('button', { name: 'Dry-run' }).click()

    await expect(page.getByRole('status').filter({ hasText: 'Dry-runはまだ処理中です。しばらく待って再実行してください。' }).first()).toBeVisible()
    await expect(page.getByText('Dry-run 結果（実取得なし）')).not.toBeVisible()
  })
})
