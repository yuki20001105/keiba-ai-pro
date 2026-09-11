import { test, expect } from '@playwright/test'
import { mockAuth, mockDataStats } from './helpers/mock-api'

test.describe('データ取得ページ', () => {
  test.beforeEach(async ({ page }) => {
    await mockAuth(page)
    await mockDataStats(page)
    // ローカルAPIヘルスチェック
    await page.route('/api/scrape/status/__health_check__**', route =>
      route.fulfill({ json: { status: 'ok' } })
    )
  })

  test('ページが正常に表示される', async ({ page }) => {
    await page.goto('/data-collection')
    await expect(page.getByText('データ取得', { exact: true })).toBeVisible()
    await expect(page.getByText('期間指定一括取得')).toBeVisible()
  })

  test('開始年月・終了年月の入力欄（type=month）が2つある', async ({ page }) => {
    await page.goto('/data-collection')
    const monthInputs = page.locator('input[type="month"]')
    await expect(monthInputs).toHaveCount(2)
  })

  test('開始年月・終了年月のラベルが表示されている', async ({ page }) => {
    await page.goto('/data-collection')
    await expect(page.getByText('開始年月')).toBeVisible()
    await expect(page.getByText('終了年月')).toBeVisible()
  })

  test('90日以内の期間では警告が表示されない', async ({ page }) => {
    await page.goto('/data-collection')
    const startInput = page.getByTestId('start-period-input')
    const endInput = page.getByTestId('end-period-input')
    await expect(startInput).toBeVisible()
    await expect(endInput).toBeVisible()
    await startInput.fill('2026-03')
    await endInput.fill('2026-04')
    await expect(page.getByText(/大量リクエストはIPブロック/)).not.toBeVisible()
  })

  test('長期間でも月単位の取得範囲を保持する', async ({ page }) => {
    await page.goto('/data-collection')
    const startInput = page.getByTestId('start-period-input')
    const endInput = page.getByTestId('end-period-input')
    await expect(startInput).toBeVisible()
    await expect(endInput).toBeVisible()
    await startInput.fill('2025-01')
    await endInput.fill('2026-04')
    await expect(startInput).toHaveValue('2025-01')
    await expect(endInput).toHaveValue('2026-04')
    await expect(page.getByText('月単位で自動分割して順次取得')).toBeVisible()
    await expect(page.getByTestId('dry-run-button')).toBeEnabled()
    await expect(page.getByTestId('execute-button')).toBeEnabled()
  })

  test('ローカルAPI停止時はボタンが無効表示になる', async ({ page }) => {
    await page.route('/api/scrape/status/__health_check__**', route =>
      route.fulfill({ status: 503, json: { error: 'offline' } })
    )
    await page.goto('/data-collection')
    const btn = page.getByRole('button', { name: /API停止中|取得開始/ })
    await expect(btn).toBeVisible({ timeout: 5000 })
  })

  test('通常画面には保守・実験用の導線を表示しない', async ({ page }) => {
    await page.goto('/data-collection')
    for (const label of ['Refresh Plan', 'P0 Repair Plan', 'Targeted Refetch Plan', 'Live Validation', 'Review Queue', 'モデル学習へ']) {
      await expect(page.getByText(label, { exact: true })).toHaveCount(0)
    }
    await expect(page.getByTestId('force-rescrape-input')).toHaveCount(0)
    await expect(page.getByText('特徴量プロファイリングレポート（オプション）')).toHaveCount(0)
  })

  test('スクレイピング実行中はプログレスバーが表示される', async ({ page }) => {
    let pollCount = 0
    await page.route('/api/scrape/**', route => {
      const url = route.request().url()
      if (url.includes('__health_check__')) {
        return route.fulfill({ json: { status: 'ok' } })
      }
      if (route.request().method() === 'POST') {
        return route.fulfill({ json: { job_id: 'test-job-001' } })
      }
      return route.continue()
    })
    // Also handle the exact POST url without trailing path
    await page.route('/api/scrape', route => {
      if (route.request().method() === 'POST') {
        return route.fulfill({ json: { job_id: 'test-job-001' } })
      }
      return route.continue()
    })
    await page.route('/api/scrape/status/test-job-001**', route => {
      pollCount++
      if (pollCount < 2) {
        return route.fulfill({ json: { status: 'running', progress: { current: 5, total: 20, message: '取得中...', eta: '30秒' } } })
      }
      return route.fulfill({ json: { status: 'completed', races_collected: 15, elapsed_time: 12 } })
    })

    // Accept the confirm() dialog that appears before scraping starts
    page.on('dialog', dialog => dialog.accept())

    await page.goto('/data-collection')
    await page.getByRole('button', { name: /取得開始/ }).click()
    await expect(page.getByText(/取得中|スクレイピング|完了/).first()).toBeVisible({ timeout: 10000 })
  })
})
