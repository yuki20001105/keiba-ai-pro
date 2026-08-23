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

test.describe('P0 Repair Plan UI (read-only preview only)', () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!E2E_PASSWORD, 'E2E_PASSWORD is required for auth-guarded routes')
    await login(page)

    await page.route('**/api/scrape/p0-repair-plan**', async route => {
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
            read_only: true,
            update_enabled: false,
            update_action: 'not-implemented',
            plan: {
              verdict: 'warn',
              target: 'all',
              p0_total_count: 6,
              refetch_required_count: 2,
              reparse_cache_count: 1,
              repair_from_metadata_count: 1,
              schema_review_count: 1,
              manual_review_count: 1,
              no_action_count: 1,
              estimated_http_request_count: 2,
              estimated_runtime_seconds: 3,
              p0_action_breakdown: [
                { action: 'refetch-required', column: '(check)', count: 2 },
                { action: 'reparse-cache', column: 'finish_position', count: 1 },
              ],
              p0_reason_breakdown: [
                { reason: 'consistency:race_without_horse_data', column: '(check)', count: 2 },
                { reason: 'true-missing', column: 'finish_position', count: 1 },
              ],
              sample_targets: [
                {
                  race_id: '202601010101',
                  horse_id: '2021100001',
                  column: 'finish_position',
                  reason: 'true-missing',
                  action: 'reparse-cache',
                  priority: 'P0',
                  source_hint: 'result-cache-reparse-priority',
                  recommended_next_action: 'reparse-cache-first',
                },
              ],
              recommended_next_actions: [
                'finish_position true missing は result page の reparse-cache を優先',
              ],
              safeguards: {
                read_only: true,
                no_db_write: true,
                no_scrape_execute: true,
                no_upsert: true,
                no_force_refresh_execute: true,
              },
            },
          }),
        })
      }

      return route.continue()
    })
  })

  test('Data Collection から遷移し read-only p0 plan を表示できる', async ({ page }) => {
    await page.goto('/data-collection')
    const p0PlanLink = page.getByRole('link', { name: 'P0 Repair Plan' })
    await expect(p0PlanLink).toBeVisible()
    await expect(p0PlanLink).toHaveAttribute('href', '/data-collection/p0-repair-plan')

    await page.goto('/data-collection/p0-repair-plan')
    await expect(page).toHaveURL(/\/data-collection\/p0-repair-plan/)

    await expect(page.getByText('この画面は read-only preview のみです。実DB更新・実スクレイピング・upsert・force refresh・repair 実行は行いません。')).toBeVisible()

    await page.getByLabel('Target').selectOption('all')
    await page.getByRole('button', { name: 'Generate P0 Repair Plan' }).click()

    await expect(page.getByText('Plan Summary')).toBeVisible()
    await expect(page.getByText(/^p0_total_count:/)).toBeVisible()
    await expect(page.getByText(/^refetch_required_count:/)).toBeVisible()
    await expect(page.getByText(/^schema_review_count:/)).toBeVisible()
    await expect(page.getByText(/^manual_review_count:/)).toBeVisible()

    await expect(page.getByText('Sample Targets (action grouped)')).toBeVisible()
    await expect(page.getByText('202601010101')).toBeVisible()
    await expect(page.getByText('Recommended Next Actions')).toBeVisible()

    const executeBtn = page.getByRole('button', { name: 'Execute P0 Repair (Disabled)' })
    await expect(executeBtn).toBeDisabled()

    await expect(page.locator('body')).not.toContainText('sb_secret_')
    await expect(page.locator('body')).not.toContainText(NOTION_TOKEN_PREFIX)
    await expect(page.locator('body')).not.toContainText('SUPABASE_SERVICE_ROLE_KEY')
  })

  test('path系入力はUIリクエストに含まれない', async ({ page }) => {
    let captured: Record<string, unknown> | null = null

    await page.unroute('**/api/scrape/p0-repair-plan**')
    await page.route('**/api/scrape/p0-repair-plan**', async route => {
      const req = route.request()
      if (req.method() === 'POST') {
        captured = (req.postDataJSON() as Record<string, unknown>) || {}
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            dry_run: true,
            read_only: true,
            update_enabled: false,
            update_action: 'not-implemented',
            plan: {
              verdict: 'pass',
              target: 'all',
              p0_total_count: 0,
              refetch_required_count: 0,
              reparse_cache_count: 0,
              repair_from_metadata_count: 0,
              schema_review_count: 0,
              manual_review_count: 0,
              no_action_count: 0,
              estimated_http_request_count: 0,
              estimated_runtime_seconds: 0,
              p0_action_breakdown: [],
              p0_reason_breakdown: [],
              sample_targets: [],
              recommended_next_actions: [],
              safeguards: {
                read_only: true,
                no_db_write: true,
                no_scrape_execute: true,
                no_upsert: true,
                no_force_refresh_execute: true,
              },
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

    await page.goto('/data-collection/p0-repair-plan')
    await expect(page.getByRole('button', { name: 'Generate P0 Repair Plan' })).toBeVisible()
    await page.getByRole('button', { name: 'Generate P0 Repair Plan' }).click()
    await expect.poll(() => captured).not.toBeNull()

    expect(captured).not.toBeNull()
    const forbidden = ['filePath', 'reportPath', 'inputAudit', 'inputRefreshPlan', 'output', 'dbPath', 'inputDb', 'inputCsv', 'sourcePath', 'modelPath', 'path']
    for (const key of forbidden) {
      expect(Object.prototype.hasOwnProperty.call(captured || {}, key)).toBeFalsy()
    }
  })
})
