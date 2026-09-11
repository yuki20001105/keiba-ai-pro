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
    let dryRunRequestBody: Record<string, unknown> | null = null

    await page.route('/api/scrape', async route => {
      if (route.request().method() !== 'POST') {
        return route.continue()
      }
      const body = route.request().postDataJSON() as Record<string, unknown>
      if (body?.dry_run === true) {
        dryRunRequestBody = body
        return route.fulfill({ status: 200, json: { job_id: 'dry-run-job-001', status: 'queued', mode: 'dry-run' } })
      }
      return route.fulfill({ status: 200, json: { job_id: 'exec-job-001', status: 'queued' } })
    })

    let pollCount = 0
    await page.route('/api/scrape/status/dry-run-job-001**', route => {
      pollCount += 1
      if (pollCount === 1) {
        return route.fulfill({ status: 200, json: { status: 'running', progress: 'planning' } })
      }
      return route.fulfill({
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
                db_existing_skip_count: 14,
                db_existing_race_count: 7,
                db_existing_horse_count: 96,
                db_existing_result_count: 7,
                db_existing_pedigree_count: 94,
                new_fetch_required_count: 8,
                already_covered_count: 26,
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
    })

    await page.goto('/data-collection')

    await expect(page.getByRole('button', { name: 'Dry-run' })).toBeVisible()
    await expect(page.getByText('Dry-run は HTTPアクセスを実行しません')).toBeVisible()
    await page.getByTestId('start-period-input').fill('2026-01')
    await page.getByTestId('end-period-input').fill('2026-01')

    await page.getByRole('button', { name: 'Dry-run' }).click()

    await expect(page.getByText('Dry-run 実行中')).toBeVisible()
    await expect(page.getByText('見積もり生成中')).toBeVisible()
    await expect(page.getByText('HTTPアクセスは実行していません')).toBeVisible()
    await expect(page.getByText(/経過秒:\s*\d+\s*sec/)).toBeVisible()
    await expect(page.getByText('Dry-run 結果（実取得なし）')).not.toBeVisible()
    await expect(page.getByRole('button', { name: '取得開始' })).toBeDisabled()
    await expect(page.locator('input[type="month"]').first()).toBeDisabled()
    await expect(page.locator('input[type="month"]').nth(1)).toBeDisabled()
    await expect(page.getByTestId('force-rescrape-input')).toHaveCount(0)

    const result = page.getByTestId('dry-run-result')
    await expect(result.getByText('Dry-run 結果（実取得なし）')).toBeVisible()
    await expect(result.getByText('新規取得', { exact: true }).locator('..')).toContainText('8')
    await expect(result.getByText('既存データ', { exact: true }).locator('..')).toContainText('26')
    await expect(result.getByText('HTTP予定', { exact: true }).locator('..')).toContainText('8')
    await expect(result.getByText('推定時間', { exact: true }).locator('..')).toContainText('8 sec')
    expect(dryRunRequestBody).toMatchObject({ dry_run: true, force_rescrape: false })
    await expect(page.getByText('rate limit policy')).toHaveCount(0)
  })

  test('Dry-runエラー時に0件ではなくエラーメッセージを表示する', async ({ page }) => {
    await page.route('/api/scrape', async route => {
      if (route.request().method() !== 'POST') {
        return route.continue()
      }
      return route.fulfill({
        status: 500,
        json: { detail: 'Dry-run結果を取得できませんでした。期間を短くするか、再実行してください。' },
      })
    })

    await page.goto('/data-collection')
    await page.getByRole('button', { name: 'Dry-run' }).click()

    await expect(page.getByTestId('dry-run-error')).toContainText(
      'Dry-run結果を取得できませんでした。期間を短くするか、再実行してください。'
    )
    await expect(page.getByText('Dry-run 結果（実取得なし）')).not.toBeVisible()
  })

  test('Dry-run未実行で本実行するとwarnが表示される', async ({ page }) => {
    page.on('dialog', dialog => dialog.dismiss())

    await page.goto('/data-collection')
    await page.getByRole('button', { name: '取得開始' }).click()

    await expect(page.getByText('Dry-run未実行です。本実行は可能ですが、推定アクセス数の確認を推奨します。')).toBeVisible()
  })
})
