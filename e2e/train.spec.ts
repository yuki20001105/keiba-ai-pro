import { test, expect } from '@playwright/test'
import { mockAuth } from './helpers/mock-api'

const MODELS_MOCK = [
  { model_id: 'abc123-def456', model_type: 'lightgbm', target: 'win', auc: 0.7234, cv_auc_mean: 0.710, created_at: '2026-04-01T10:00:00Z', is_active: true, n_rows: 5000, training_date_from: '2024-01-01', training_date_to: '2025-12-31' },
  { model_id: 'candidate-model-001', model_type: 'lightgbm', target: 'speed_deviation', auc: 0.7595, created_at: '20260912_2159', is_active: false, n_rows: 112921, training_date_from: '20180106', training_date_to: '20260706' },
]
const TRAIN_JOB_ID = '11111111-1111-4111-8111-111111111111'

test.describe('モデル学習ページ', () => {
  test.beforeEach(async ({ page }) => {
    await mockAuth(page)
    await page.route('/api/models**', route => route.fulfill({ json: { models: MODELS_MOCK } }))
    await page.route('/api/ml/train/capability**', route => route.fulfill({
      json: { enabled: true, reason: null },
    }))
  })

  test('ページが正常に表示される', async ({ page }) => {
    await page.goto('/train')
    await expect(page.getByText('モデル作成').first()).toBeVisible()
  })

  test('学習設定フォームが表示される', async ({ page }) => {
    await page.goto('/train')
    await expect(page.getByText('学習設定')).toBeVisible()
    await expect(page.getByText('モデルタイプ')).toBeVisible()
    await expect(page.getByText('学習データ期間')).toBeVisible()
  })

  test('「モデル作成」ボタンが存在する', async ({ page }) => {
    await page.goto('/train')
    await expect(page.getByRole('button', { name: 'モデル作成' })).toBeVisible()
  })

  test('学習済みモデル一覧が表示される', async ({ page }) => {
    await page.goto('/train')
    await expect(page.getByText('保存済みモデル')).toBeVisible()
    // モックのモデルが表示される
    await expect(page.getByText('abc123-def456')).toBeVisible({ timeout: 5000 })
    await expect(page.getByText('AUC 0.7234')).toBeVisible({ timeout: 5000 })
  })

  test('既定モデルの操作意味が分かり、ローカル管理者は変更できる', async ({ page }) => {
    let activationRequests = 0
    await page.route('/api/models/candidate-model-001/activate', route => {
      activationRequests++
      return route.fulfill({ json: { success: true, active_model_id: 'candidate-model-001' } })
    })

    await page.goto('/train')
    const activateButton = page.getByRole('button', { name: '既定にする' })
    await expect(activateButton).toBeEnabled()
    await expect(activateButton).toHaveAttribute('title', 'モデル未指定の予測で使う')
    await activateButton.click()

    await expect(page.getByText('既定モデルを変更しました')).toBeVisible()
    expect(activationRequests).toBe(1)
  })

  test('学習を実行するとプログレスが表示される', async ({ page }) => {
    let pollCount = 0
    await page.route('/api/ml/train/start**', route => {
      if (route.request().method() === 'POST') {
        return route.fulfill({ json: { job_id: TRAIN_JOB_ID } })
      }
      return route.continue()
    })
    await page.route(`/api/ml/train/status/${TRAIN_JOB_ID}**`, route => {
      pollCount++
      if (pollCount < 2) {
        return route.fulfill({ json: { status: 'running', progress: '学習中...' } })
      }
      return route.fulfill({ json: { status: 'completed', result: { metrics: { auc: 0.7345 }, model_id: 'new-model-001' }, data_count: 5000 } })
    })

    await page.goto('/train')
    await page.getByRole('button', { name: 'モデル作成' }).click()

    // 完了後トーストが表示される
    await expect(page.getByText(/学習完了.*AUC/)).toBeVisible({ timeout: 15000 })
  })

  test('既に実行中の学習ジョブへ再接続できる', async ({ page }) => {
    let statusRequests = 0
    let startRequests = 0
    await page.route('/api/ml/train/start**', route => {
      startRequests++
      return route.fulfill({
        status: 409,
        json: {
          detail: {
            code: 'train-job-active',
            job_id: TRAIN_JOB_ID,
            message: '別のモデル作成が実行中です',
          },
        },
      })
    })
    await page.route(`/api/ml/train/status/${TRAIN_JOB_ID}**`, route => {
      statusRequests++
      return route.fulfill({
        json: {
          status: 'completed',
          progress: '完了',
          result: { metrics: { auc: 0.7345 }, model_id: 'existing-model-001' },
          data_count: 5000,
        },
      })
    })

    await page.goto('/train')
    await page.getByRole('button', { name: 'モデル作成' }).click()

    await expect(page.getByText(/学習完了.*AUC/)).toBeVisible({ timeout: 10000 })
    expect(startRequests).toBe(1)
    expect(statusRequests).toBeGreaterThan(0)
  })

  test('ページ再読込後も保存したジョブIDから再接続し、開始要求を重複送信しない', async ({ page }) => {
    let reloaded = false
    let startRequests = 0
    let statusRequests = 0
    await page.route('/api/ml/train/start**', route => {
      startRequests++
      return route.fulfill({ json: { job_id: TRAIN_JOB_ID, status: 'queued' } })
    })
    await page.route(`/api/ml/train/status/${TRAIN_JOB_ID}**`, route => {
      statusRequests++
      return route.fulfill({
        json: reloaded
          ? {
              status: 'completed',
              progress: '完了',
              result: { metrics: { auc: 0.7345 }, model_id: 'restored-model-001' },
            }
          : { status: 'running', progress: '学習中...', pct: 40 },
      })
    })

    await page.goto('/train')
    await page.getByRole('button', { name: 'モデル作成' }).click()
    await expect(page.getByText('学習中', { exact: true })).toBeVisible()

    reloaded = true
    await page.reload()

    await expect(page.getByText(/学習完了.*AUC/)).toBeVisible({ timeout: 10000 })
    expect(startRequests).toBe(1)
    expect(statusRequests).toBeGreaterThan(0)
    await expect.poll(() => page.evaluate(() => {
      const value = localStorage.getItem('keiba-ai-pro:train-ui:v1')
      return value ? JSON.parse(value).activeJobId : undefined
    })).toBeNull()
  })

  test('モデル削除は承認フローができるまで無効', async ({ page }) => {
    await page.goto('/train')
    await expect(page.getByText('abc123-def456')).toBeVisible({ timeout: 5000 })
    const deleteButton = page.getByRole('button', { name: '削除' }).first()
    await expect(deleteButton).toBeDisabled()
    await expect(deleteButton).toHaveAttribute('title', 'モデル削除には別の永続的な廃止承認が必要です')
  })

  test('モデルタイプは実装済みのLightGBMに固定される', async ({ page }) => {
    await page.goto('/train')
    await expect(page.getByText('LightGBM（推奨）', { exact: true })).toBeVisible()
    await expect(page.locator('select').filter({ has: page.locator('option[value="logistic_regression"]') })).toHaveCount(0)
  })
})
