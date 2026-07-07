import { test, expect } from '@playwright/test'
import { mockDataStats } from './helpers/mock-api'

const E2E_EMAIL = process.env.E2E_EMAIL || 'yuki20001105@icloud.com'
const E2E_PASSWORD = process.env.E2E_PASSWORD || ''

async function login(page: import('@playwright/test').Page) {
  await page.goto('/login')
  await expect(page.locator('input[type="email"]')).toBeVisible({ timeout: 30000 })
  await page.locator('input[type="email"]').fill(E2E_EMAIL)
  await page.locator('input[type="password"]').fill(E2E_PASSWORD)
  await page.locator('form button[type="submit"]').click()
  await page.waitForURL('**/home', { timeout: 30000 })
}

test.describe('データ取得 fetch summary history UI', () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!E2E_PASSWORD, 'E2E_PASSWORD is required for auth-guarded routes')
    await login(page)
    await mockDataStats(page)
    await page.route('/api/scrape/health**', route =>
      route.fulfill({ status: 200, json: { status: 'healthy' } })
    )
  })

  test('履歴セクションと更新ボタン、dry-run/execute項目が表示される', async ({ page }) => {
    await page.route('/api/scrape/history**', route =>
      route.fulfill({
        status: 200,
        json: {
          count: 2,
          jobs: [
            {
              job_id: 'hist-job-dry-001',
              status: 'completed',
              updated_at: '2026-07-07T10:00:00',
              fetch_summary: {
                mode: 'dry-run',
                start_date: '20260701',
                end_date: '20260731',
                dry_run: {
                  estimated_request_count: 8,
                  cache_hit_count: 4,
                  cache_miss_count: 4,
                  resume_hit_count: 1,
                  estimated_runtime_sec: 8,
                },
              },
            },
            {
              job_id: 'hist-job-exec-001',
              status: 'completed',
              updated_at: '2026-07-07T10:10:00',
              fetch_summary: {
                mode: 'execute',
                start_date: '20260701',
                end_date: '20260731',
                saved_races: 12,
                saved_horses: 168,
                elapsed_time_sec: 65,
                metrics: {
                  network_requests: 20,
                  retry_count: 2,
                },
              },
            },
          ],
        },
      })
    )

    await page.goto('/data-collection')

    await expect(page.getByText('fetch summary 履歴')).toBeVisible()
    await expect(page.getByRole('button', { name: '更新' })).toBeVisible()

    await expect(page.getByText('hist-job-dry-001')).toBeVisible()
    await expect(page.getByText('hist-job-exec-001')).toBeVisible()
    await expect(page.getByText('dry-run', { exact: true })).toBeVisible()
    await expect(page.getByText('execute', { exact: true })).toBeVisible()

    await expect(page.getByText('est req:')).toBeVisible()
    await expect(page.getByText('cache hit:')).toBeVisible()
    await expect(page.getByText('saved races:')).toBeVisible()
    await expect(page.getByText('network req:')).toBeVisible()
    await expect(page.getByText('retries:')).toBeVisible()

    const notionTokenPrefix = 'nt' + 'n_'
    await expect(page.getByText(notionTokenPrefix)).toHaveCount(0)
    await expect(page.getByText('SUPABASE_SERVICE_ROLE_KEY')).toHaveCount(0)
    await expect(page.getByText('E2E_PASSWORD')).toHaveCount(0)
  })

  test('履歴が空のとき空状態メッセージが表示される', async ({ page }) => {
    await page.route('/api/scrape/history**', route =>
      route.fulfill({ status: 200, json: { count: 0, jobs: [] } })
    )

    await page.goto('/data-collection')

    await expect(page.getByText('fetch summary 履歴')).toBeVisible()
    await expect(page.getByText('履歴がありません（Dry-run または 取得実行後に表示されます）')).toBeVisible()
  })
})
