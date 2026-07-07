import { test, expect } from '@playwright/test'

const E2E_EMAIL = process.env.E2E_EMAIL || 'yuki20001105@icloud.com'
const E2E_PASSWORD = process.env.E2E_PASSWORD || ''
const NOTION_TOKEN_PREFIX = 'ntn' + '_'

async function login(page: import('@playwright/test').Page) {
  await page.goto('/login')
  await expect(page.locator('input[type="email"]')).toBeVisible({ timeout: 30000 })
  await page.locator('input[type="email"]').fill(E2E_EMAIL)
  await page.locator('input[type="password"]').fill(E2E_PASSWORD)
  await page.locator('form button[type="submit"]').click()
  await page.waitForURL('**/home', { timeout: 30000 })
}

test.describe('Refresh Plan UI (dry-run preview only)', () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!E2E_PASSWORD, 'E2E_PASSWORD is required for auth-guarded routes')
    await login(page)

    await page.route('**/api/scrape/refresh-plan**', async route => {
      const req = route.request()
      if (req.method() === 'PUT') {
        return route.fulfill({
          status: 501,
          contentType: 'application/json',
          body: JSON.stringify({ error: 'not-implemented' }),
        })
      }

      if (req.method() === 'POST' || req.method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            dry_run: true,
            update_enabled: false,
            update_action: 'not-implemented',
            plan: {
              policy: 'repair-missing',
              target: 'all',
              start_date: '20260101',
              end_date: '20260131',
              target_count: 100,
              existing_count: 95,
              missing_count: 5,
              skip_count: 70,
              repair_count: 8,
              reparse_count: 4,
              refetch_count: 6,
              update_candidate_count: 2,
              quarantine_count: 1,
              no_downgrade_skip_count: 4,
              estimated_http_request_count: 10,
              estimated_runtime: 120,
              verdict: 'warn',
              warnings: ['quarantine candidates detected'],
              decisions: [
                {
                  key: '202601010101:2021100001',
                  action: 'repair',
                  reason: 'required-missing-or-invalid',
                  quality_score: 61,
                  missing_fields: ['horse_name'],
                  parser_version: '2.0.0',
                  fetched_at: '2026-07-01 10:00:00',
                },
                {
                  key: '202601010102:2021100002',
                  action: 'skip',
                  reason: 'healthy-existing',
                  quality_score: 98,
                  missing_fields: [],
                  parser_version: '2.0.0',
                  fetched_at: '2026-07-01 10:05:00',
                },
                {
                  key: '202601010103:2021100003',
                  action: 'reparse-cache',
                  reason: 'stale-parser-version',
                  quality_score: 90,
                  missing_fields: [],
                  parser_version: '1.0.0',
                  fetched_at: '2026-06-01 10:00:00',
                },
                {
                  key: '202601010104:2021100004',
                  action: 'refetch',
                  reason: 'stale-fetched-at',
                  quality_score: 88,
                  missing_fields: [],
                  parser_version: '2.0.0',
                  fetched_at: '2026-05-01 10:00:00',
                },
                {
                  key: '202601010105:2021100005',
                  action: 'quarantine',
                  reason: 'duplicate-existing-record',
                  quality_score: 40,
                  missing_fields: ['venue'],
                  parser_version: '2.0.0',
                  fetched_at: '2026-07-01 10:00:00',
                },
                {
                  key: '202601010106:2021100006',
                  action: 'no-downgrade-skip',
                  reason: 'candidate-quality-lower-than-existing',
                  quality_score: 99,
                  missing_fields: [],
                  parser_version: '2.0.0',
                  fetched_at: '2026-07-01 10:00:00',
                },
              ],
            },
          }),
        })
      }

      return route.continue()
    })
  })

  test('Data Collection から遷移し dry-run plan を表示できる', async ({ page }) => {
    await page.goto('/data-collection')
    const refreshPlanLink = page.getByRole('link', { name: 'Refresh Plan' })
    await expect(refreshPlanLink).toBeVisible()
    await expect(refreshPlanLink).toHaveAttribute('href', '/data-collection/refresh-plan')

    await page.goto('/data-collection/refresh-plan')
    await expect(page).toHaveURL(/\/data-collection\/refresh-plan/)

    await expect(page.getByText('この画面は dry-run preview のみです。実DB更新・実スクレイピング・upsert・force refresh 実行は行いません。')).toBeVisible()

    await page.getByLabel('Start Date').fill('20260101')
    await page.getByLabel('End Date').fill('20260131')
    await page.getByLabel('Target').selectOption('all')
    await page.getByLabel('Policy').selectOption('repair-missing')
    await page.getByLabel('Stale Days').fill('30')
    await page.getByLabel('Current Parser Version').fill('2.0.0')

    await page.getByRole('button', { name: 'Generate Dry-run Plan' }).click()

    await expect(page.getByText('Plan Summary')).toBeVisible()
    await expect(page.getByText(/^skip_count:/)).toBeVisible()
    await expect(page.getByText(/^repair_count:/)).toBeVisible()
    await expect(page.getByText(/^reparse_count:/)).toBeVisible()
    await expect(page.getByText(/^refetch_count:/)).toBeVisible()
    await expect(page.getByText(/^quarantine_count:/)).toBeVisible()
    await expect(page.getByText(/^no_downgrade_skip_count:/)).toBeVisible()

    await expect(page.getByText('Decision Samples (action grouped)')).toBeVisible()
    await expect(page.getByText('202601010101:2021100001')).toBeVisible()

    const executeBtn = page.getByRole('button', { name: 'Execute Refresh (Disabled)' })
    await expect(executeBtn).toBeDisabled()

    await expect(page.locator('body')).not.toContainText('sb_secret_')
    await expect(page.locator('body')).not.toContainText(NOTION_TOKEN_PREFIX)
    await expect(page.locator('body')).not.toContainText('SUPABASE_SERVICE_ROLE_KEY')
  })

  test('path系入力はUIリクエストに含まれない', async ({ page }) => {
    let captured: Record<string, unknown> | null = null

    await page.unroute('**/api/scrape/refresh-plan**')
    await page.route('**/api/scrape/refresh-plan**', async route => {
      const req = route.request()
      if (req.method() === 'POST') {
        captured = (req.postDataJSON() as Record<string, unknown>) || {}
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            dry_run: true,
            update_enabled: false,
            update_action: 'not-implemented',
            plan: {
              policy: 'dry-run',
              target: 'all',
              target_count: 0,
              existing_count: 0,
              missing_count: 0,
              skip_count: 0,
              repair_count: 0,
              reparse_count: 0,
              refetch_count: 0,
              update_candidate_count: 0,
              quarantine_count: 0,
              no_downgrade_skip_count: 0,
              estimated_http_request_count: 0,
              estimated_runtime: 0,
              verdict: 'pass',
              warnings: [],
              decisions: [],
            },
          }),
        })
      }
      if (req.method() === 'PUT') {
        return route.fulfill({
          status: 501,
          contentType: 'application/json',
          body: JSON.stringify({ error: 'not-implemented' }),
        })
      }
      return route.continue()
    })

    await page.goto('/data-collection/refresh-plan')
    await expect(page.getByRole('button', { name: 'Generate Dry-run Plan' })).toBeVisible()
    await page.getByRole('button', { name: 'Generate Dry-run Plan' }).click()
    await expect.poll(() => captured).not.toBeNull()

    expect(captured).not.toBeNull()
    const forbidden = ['filePath', 'reportPath', 'dbPath', 'inputDb', 'inputCsv', 'output', 'sourcePath', 'modelPath', 'path']
    for (const key of forbidden) {
      expect(Object.prototype.hasOwnProperty.call(captured || {}, key)).toBeFalsy()
    }
  })
})
