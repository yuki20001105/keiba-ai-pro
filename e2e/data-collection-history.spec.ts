import { test, expect } from '@playwright/test'
import { mockAuth, mockDataStats } from './helpers/mock-api'

test.describe('データ取得 最新実行結果 UI', () => {
  test.beforeEach(async ({ page }) => {
    await mockAuth(page)
    await mockDataStats(page)
    await page.route('/api/scrape/health**', route =>
      route.fulfill({ status: 200, json: { status: 'healthy' } })
    )
  })

  test('最新1件だけを通常運用向けの要約で表示する', async ({ page }) => {
    await page.route('/api/scrape/history**', route =>
      route.fulfill({
        status: 200,
        json: {
          count: 2,
          jobs: [
            {
              job_id: 'hist-job-exec-latest',
              status: 'completed',
              updated_at: '2026-07-07T10:10:00',
              result: {
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
            },
            {
              job_id: 'hist-job-dry-older',
              status: 'completed',
              updated_at: '2026-07-07T10:00:00',
              fetch_summary: {
                mode: 'dry-run',
                start_date: '20260701',
                end_date: '20260731',
                dry_run: {
                  estimated_request_count: 8,
                  new_fetch_required_count: 9876,
                  cache_hit_count: 4,
                  cache_miss_count: 4,
                  resume_hit_count: 1,
                  estimated_runtime_sec: 8,
                },
              },
            },
          ],
        },
      })
    )

    await page.goto('/data-collection')

    const latest = page.getByTestId('latest-fetch-summary')
    await expect(latest).toBeVisible()
    await expect(latest.getByText('最新', { exact: true })).toBeVisible()
    await expect(latest.getByRole('button', { name: '更新' })).toBeVisible()

    await expect(latest.getByText('取得', { exact: true })).toBeVisible()
    await expect(latest).toContainText('2026/07/01～2026/07/31')
    await expect(latest).toContainText('12レース')
    await expect(latest).toContainText('約2分')
    await expect(page.getByText('168', { exact: true })).toHaveCount(0)
    await expect(page.getByText('9876', { exact: true })).toHaveCount(0)
    await expect(page.getByText('fetch summary 履歴')).toHaveCount(0)

    const notionTokenPrefix = 'nt' + 'n_'
    await expect(page.getByText(notionTokenPrefix)).toHaveCount(0)
    await expect(page.getByText('SUPABASE_SERVICE_ROLE_KEY')).toHaveCount(0)
    await expect(page.getByText('E2E_PASSWORD')).toHaveCount(0)
  })

  test('実行結果がないときは履歴用の空カードを表示しない', async ({ page }) => {
    await page.route('/api/scrape/history**', route =>
      route.fulfill({ status: 200, json: { count: 0, jobs: [] } })
    )

    await page.goto('/data-collection')

    await expect(page.getByTestId('latest-fetch-summary')).toHaveCount(0)
    await expect(page.getByText('履歴がありません（Dry-run または 取得実行後に表示されます）')).toHaveCount(0)
    await expect(page.getByText('保存済み', { exact: true })).toBeVisible()
  })

  test('履歴応答が壊れている場合は新しい実行を安全側で停止する', async ({ page }) => {
    await page.route('/api/scrape/history**', route =>
      route.fulfill({ status: 200, json: { count: 1, jobs: [{ job_id: '', status: 'running' }] } })
    )

    await page.goto('/data-collection')

    await expect(page.getByTestId('active-job-check-error')).toContainText('実行状態を確認できません')
    await expect(page.getByTestId('dry-run-button')).toBeDisabled()
    await expect(page.getByTestId('execute-button')).toBeDisabled()
  })
})
