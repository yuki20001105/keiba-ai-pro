import { test, expect } from '@playwright/test'
import { mockAuth, mockDataStats } from './helpers/mock-api'

test.describe('データ取得 Dry-run UI', () => {
  test.beforeEach(async ({ page }) => {
    await mockAuth(page)
    await mockDataStats(page)
    await page.route('/api/scrape/health**', route =>
      route.fulfill({ status: 200, json: { status: 'healthy' } })
    )
  })

  test('Dry-runボタンと結果カードを表示できる', async ({ page }) => {
    await page.route('/api/scrape', async route => {
      if (route.request().method() !== 'POST') {
        return route.continue()
      }
      const body = route.request().postDataJSON() as Record<string, unknown>
      if (body?.dry_run === true) {
        return route.fulfill({ status: 200, json: { job_id: 'dry-run-job-001', status: 'queued', mode: 'dry-run' } })
      }
      return route.fulfill({ status: 200, json: { job_id: 'exec-job-001', status: 'queued' } })
    })

    await page.route('/api/scrape/status/dry-run-job-001**', route =>
      route.fulfill({
        status: 200,
        json: {
          status: 'completed',
          result: {
            success: true,
            dry_run: true,
            fetch_summary: {
              dry_run: {
                total_target_count: 24,
                unique_url_count: 20,
                estimated_request_count: 8,
                cache_hit_count: 10,
                cache_miss_count: 10,
                resume_hit_count: 2,
                skipped_count: 12,
                estimated_runtime_sec: 8,
              },
              rate_limit_policy: {
                min_interval_sec: 1.0,
                scope: 'per-host',
              },
              retry_backoff_policy: {
                max_retries: 3,
                backoff: { type: 'exponential_with_jitter' },
                retry_after: 'respected',
              },
              circuit_breaker_policy: {
                failure_threshold: 3,
                cooldown_sec: 120,
              },
            },
          },
        },
      })
    )

    await page.goto('/data-collection')

    await expect(page.getByRole('button', { name: 'Dry-run' })).toBeVisible()
    await expect(page.getByText('Dry-run は HTTPアクセスを実行しません')).toBeVisible()

    await page.getByRole('button', { name: 'Dry-run' }).click()

    await expect(page.getByText('Dry-run 結果（実取得なし）')).toBeVisible()
    await expect(page.getByText('estimated request count')).toBeVisible()
    await expect(page.getByText('8')).toBeVisible()
    await expect(page.getByText('rate limit policy')).toBeVisible()
  })

  test('Dry-run未実行で本実行するとwarnが表示される', async ({ page }) => {
    page.on('dialog', dialog => dialog.dismiss())

    await page.goto('/data-collection')
    await page.getByRole('button', { name: '取得開始' }).click()

    await expect(page.getByText('Dry-run未実行です。本実行は可能ですが、推定アクセス数の確認を推奨します。')).toBeVisible()
  })
})
