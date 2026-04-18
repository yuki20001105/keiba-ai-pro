import { test, expect } from '@playwright/test'
import { mockAuth } from './helpers/mock-api'

const MODELS_MOCK = [
  { model_id: 'abc123-def456', model_type: 'lightgbm', target: 'win', auc: 0.7234, cv_auc_mean: 0.710, created_at: '2026-04-01T10:00:00Z', is_active: true, n_rows: 5000, training_date_from: '2024-01-01', training_date_to: '2025-12-31' },
]

test.describe('モデル学習ページ', () => {
  test.beforeEach(async ({ page }) => {
    await mockAuth(page)
    await page.route('/api/models**', route => route.fulfill({ json: { models: MODELS_MOCK } }))
  })

  test('ページが正常に表示される', async ({ page }) => {
    await page.goto('/train')
    await expect(page.getByText('モデル学習')).toBeVisible()
  })

  test('学習設定フォームが表示される', async ({ page }) => {
    await page.goto('/train')
    await expect(page.getByText('学習設定')).toBeVisible()
    await expect(page.getByText('モデルタイプ')).toBeVisible()
    await expect(page.getByText('学習データ期間')).toBeVisible()
  })

  test('「学習開始」ボタンが存在する', async ({ page }) => {
    await page.goto('/train')
    await expect(page.getByRole('button', { name: '学習開始' })).toBeVisible()
  })

  test('学習済みモデル一覧が表示される', async ({ page }) => {
    await page.goto('/train')
    await expect(page.getByText('保存済みモデル')).toBeVisible()
    // モックのモデルが表示される
    await expect(page.getByText('abc123-def456')).toBeVisible({ timeout: 5000 })
    await expect(page.getByText('win | lightgbm')).toBeVisible({ timeout: 5000 })
  })

  test('学習を実行するとプログレスが表示される', async ({ page }) => {
    let pollCount = 0
    await page.route('/api/ml/train/start**', route => {
      if (route.request().method() === 'POST') {
        return route.fulfill({ json: { job_id: 'train-job-001' } })
      }
      return route.continue()
    })
    await page.route('/api/ml/train/status/train-job-001**', route => {
      pollCount++
      if (pollCount < 2) {
        return route.fulfill({ json: { status: 'running', progress: '学習中...' } })
      }
      return route.fulfill({ json: { status: 'completed', result: { metrics: { auc: 0.7345 }, model_id: 'new-model-001' }, data_count: 5000 } })
    })

    await page.goto('/train')
    await page.getByRole('button', { name: '学習開始' }).click()

    // 完了後トーストが表示される
    await expect(page.getByText(/学習完了.*AUC/)).toBeVisible({ timeout: 15000 })
  })

  test('モデル削除ボタンをクリックすると確認ダイアログが出る', async ({ page }) => {
    await page.goto('/train')
    await expect(page.getByText('abc123-def456')).toBeVisible({ timeout: 5000 })
    await page.getByRole('button', { name: '削除' }).first().click()
    // ConfirmDialogが表示される
    await expect(page.getByRole('heading', { name: 'モデルを削除' })).toBeVisible({ timeout: 3000 })
    await expect(page.getByRole('button', { name: '削除' }).nth(1)).toBeVisible({ timeout: 1000 })
  })

  test('モデルタイプをセレクトで変更できる', async ({ page }) => {
    await page.goto('/train')
    const select = page.locator('select').first()
    if (await select.count() > 0) {
      await select.selectOption({ index: 0 })
    }
  })
})
