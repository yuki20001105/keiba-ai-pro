import { test, expect } from '@playwright/test'
import { mockAuth, mockHealth, mockModels, mockRacesByDate, mockAnalyzeRace } from './helpers/mock-api'

test.describe('保存DBの更新と明示的な再予測', () => {
  test.beforeEach(async ({ page }) => {
    // Unexpected traffic is denied; this isolated suite cannot reach the live API.
    await page.route('/api/**', route => route.fulfill({ status: 503, json: { detail: 'isolated fixture only' } }))
    await mockAuth(page, { role: 'user', tier: 'premium' })
    await mockHealth(page); await mockModels(page); await mockRacesByDate(page); await mockAnalyzeRace(page)
  })

  test('保存結果を復元し、DB更新後はクリックでのみ再計算する', async ({ page }) => {
    let revision = 'e2e-saved-source'
    const bodies: Record<string, unknown>[] = []
    page.on('request', request => {
      if (request.url().includes('/api/analyze-race') && request.method() === 'POST') bodies.push(request.postDataJSON())
    })
    await page.route('/api/prediction-freshness**', route => route.fulfill({ json: {
      schema_version: 'prediction-freshness-v1', data_revision: revision,
      model_runtime_revision: 'e2e-model-runtime', resolved_model_id: 'abc123-def456',
      source_tracking: true, revision_kind: 'sqlite-source-generation', capabilities: { saved_db_recompute: true },
    } }))
    await page.goto('/predict-batch')
    await page.getByRole('button', { name: 'レース一覧を取得' }).click()
    await expect(page.getByText('テストレース1')).toBeVisible()
    await page.getByRole('button', { name: '全解除', exact: true }).click()
    await page.locator('input[type="checkbox"]').first().check()
    await page.getByRole('button', { name: /選択 1 レースを一括予測/ }).click()
    await expect(page.getByText('テスト馬A').first()).toBeVisible()
    await expect(page.getByTestId('prediction-freshness-notice')).toContainText('保存済みDBから予測')
    expect(bodies).toHaveLength(1)
    expect(bodies[0]).toMatchObject({ force_recompute: true, data_refresh: 'saved_db' })

    await page.reload()
    await page.getByRole('button', { name: 'レース一覧を取得' }).click()
    await page.getByRole('button', { name: /テストレース1/ }).click()
    await expect(page.getByTestId('prediction-freshness-notice')).toContainText('保存予測')
    expect(bodies).toHaveLength(1)

    revision = 'e2e-repaired-history'
    await page.getByRole('button', { name: 'レース一覧を取得' }).click()
    await page.getByRole('button', { name: /テストレース1/ }).click()
    await expect(page.getByTestId('prediction-freshness-notice')).toContainText('最新データで再予測してください')
    expect(bodies).toHaveLength(1)
    await expect(page.getByRole('region', { name: '買い目判定' })).toContainText('見送り')
    await page.screenshot({ path: 'reports/generated/prediction-refresh/20261012/stale-fixture.png', fullPage: true })
    await page.getByRole('button', { name: '全解除', exact: true }).click()
    await page.locator('input[type="checkbox"]').first().check()
    await page.getByRole('button', { name: /選択 1 レースを最新データで再予測/ }).click()
    await expect(page.getByTestId('prediction-freshness-notice')).toContainText('保存済みDBから予測')
    expect(bodies).toHaveLength(2)
    expect(bodies[1]).toMatchObject({ force_recompute: true, data_refresh: 'saved_db' })
    await page.screenshot({ path: 'reports/generated/prediction-refresh/20261012/refreshed-fixture.png', fullPage: true })
  })

  test('旧APIのときは自動取得へ進まず、予測POSTしない', async ({ page }) => {
    let posts = 0
    page.on('request', request => { if (request.url().includes('/api/analyze-race') && request.method() === 'POST') posts++ })
    await page.route('/api/prediction-freshness**', route => route.fulfill({ status: 404, json: { detail: 'old API' } }))
    await page.goto('/predict-batch')
    await page.getByRole('button', { name: 'レース一覧を取得' }).click()
    await expect(page.getByText('テストレース1')).toBeVisible()
    await page.locator('input[type="checkbox"]').first().check()
    await page.getByRole('button', { name: /選択 .* レースを一括予測/ }).click()
    await expect(page.getByText(/最新データで再予測できるか確認できません/).first()).toBeVisible()
    expect(posts).toBe(0)
  })
})
