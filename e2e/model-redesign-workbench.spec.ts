import { test, expect, Page } from '@playwright/test'

async function mockPremiumAuth(page: Page) {
  await page.route('**/auth/v1/user**', route =>
    route.fulfill({
      status: 200,
      json: {
        id: 'user-premium-1',
        email: 'premium@example.com',
      },
    })
  )

  await page.route('**/rest/v1/profiles**', route =>
    route.fulfill({
      status: 200,
      json: [{ role: 'user', subscription_tier: 'premium' }],
    })
  )
}

async function mockFreeAuth(page: Page) {
  await page.route('**/auth/v1/user**', route =>
    route.fulfill({
      status: 200,
      json: {
        id: 'user-free-1',
        email: 'free@example.com',
      },
    })
  )

  await page.route('**/rest/v1/profiles**', route =>
    route.fulfill({
      status: 200,
      json: [{ role: 'user', subscription_tier: 'free' }],
    })
  )
}

async function mockSummaryApi(page: Page) {
  await page.route('/api/model-redesign/summary**', route =>
    route.fulfill({
      status: 200,
      json: {
        success: true,
        state: 'warn',
        code: 'data-missing',
        generated_at: '2026-07-06T00:00:00.000Z',
        warnings: ['feature-analysis-missing'],
        active_model: {
          model_id: 'model-20260706',
          model_file_exists: true,
          model_file_size_bytes: 123456,
          model_file_updated_at: '2026-07-06T00:00:00.000Z',
          active_model_path: 'python-api/models/.active_model.json',
        },
        metrics: {
          rmse: { value: 0.1234, status: 'pass', note: 'ok' },
          auc: { value: 0.789, status: 'pass', note: 'ok' },
          spearman: { value: 0.2222, status: 'pass', note: 'ok' },
          hit_rate: { value: 0.25, status: 'pass', note: 'ok' },
          roi: { value: 0.12, status: 'pass', note: 'ok' },
        },
        feature_importance: {
          source: 'docs/reports/feature_analysis.json',
          top_features: [
            { feature: 'f_speed', total_score: 1.2, spearman: 0.3, vif: 10, op_class: '保持' },
          ],
        },
        correlation_warnings: {
          high_vif: [
            { feature: 'f_dup', total_score: 0.1, spearman: 0.01, vif: 120, op_class: '削除候補' },
          ],
          duplicate_pairs: [
            { pair: 'prev_rank / prev2_rank', reason: 'prefix-pair' },
          ],
        },
        removal_candidates: [
          { feature: 'f_dup', total_score: 0.1, spearman: 0.01, vif: 120, op_class: '削除候補' },
        ],
        improvement_preview: {
          source: 'docs/reports/iter_01_metrics.json',
          recommendations: ['高VIF特徴量を整理してください。'],
        },
        guard: {
          read_only_mode: true,
          retrain_execution: 'not-implemented',
          active_model_switch: 'not-implemented',
          production_write: false,
        },
      },
    })
  )
}

test.describe('モデル再設計ワークベンチ', () => {
  test('Premium/Admin はワークベンチを表示し、MVPガードを維持する', async ({ page }) => {
    await mockPremiumAuth(page)
    await mockSummaryApi(page)

    await page.goto('/model-redesign-workbench')

    await expect(page.getByRole('heading', { name: 'Model Redesign Workbench' })).toBeVisible()
    await expect(page.getByText('Active Model Summary')).toBeVisible()
    await expect(page.getByText('Current Model Metrics')).toBeVisible()
    await expect(page.getByText('相関・重複特徴量の警告')).toBeVisible()
    await expect(page.getByText('削除候補特徴量')).toBeVisible()
    await expect(page.getByText('改善提案 preview')).toBeVisible()

    const retrainButton = page.getByRole('button', { name: /再学習を実行/ })
    await expect(retrainButton).toBeDisabled()

    const switchButton = page.getByRole('button', { name: /active model を切替/ })
    await expect(switchButton).toBeDisabled()

    await expect(page.getByRole('link', { name: 'Notion出力UIへ' })).toBeVisible()
  })

  test('非Premium/Admin は権限不足表示になる', async ({ page }) => {
    await mockFreeAuth(page)
    await page.route('/api/model-redesign/summary**', route => route.abort())

    await page.goto('/model-redesign-workbench')

    await expect(page.getByText(/Premium または Admin 専用です/)).toBeVisible()
    await expect(page.getByText(/権限不足時は summary API を呼び出しません/)).toBeVisible()
  })
})
