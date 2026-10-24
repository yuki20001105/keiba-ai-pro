import { test, expect } from '@playwright/test'
import { mockAuth } from './helpers/mock-api'

test.describe('条件・展開特徴量の通常画面', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('/api/**', route => route.fulfill({ status: 503, json: { detail: 'isolated fixture only' } }))
    await mockAuth(page)
    await page.route('/api/models**', route => route.fulfill({ json: { models: [] } }))
  })

  test('初期46を維持し、明示選択した80の期間と方式だけを送信する', async ({ page }) => {
    const starts: unknown[] = []
    let activation = 0
    page.on('request', request => { if (/activate/.test(request.url())) activation++ })
    await page.route('/api/ml/train/capability**', route => route.fulfill({ json: {
      enabled: true, acceleration: { gpu_available: true, feature_cache_supported: true },
      feature_profiles: [{ pipeline: 'local-db-context-v1', feature_count: 80, reference_only: true }],
    } }))
    await page.route('/api/ml/train/start**', route => {
      starts.push(route.request().postDataJSON())
      return route.fulfill({ json: { job_id: '11111111-1111-4111-8111-111111111111' } })
    })
    await page.route('/api/ml/train/status/**', route => route.fulfill({ json: {
      status: 'running', progress: '条件・展開の固定候補を比較中', pct: 25,
    } }))
    await page.goto('/train')
    await page.getByRole('button', { name: '詳細設定（上級者向け）' }).click()
    const profile = page.getByRole('combobox', { name: '特徴量構成' })
    await expect(profile).toHaveValue('local-db-shared-optimized-v1')
    expect(starts).toHaveLength(0)
    await expect(profile.getByRole('option', { name: '条件・展開を追加（比較候補80項目）' })).toBeEnabled()
    await profile.selectOption('local-db-context-v1')
    await page.locator('input[type="month"]').nth(0).fill('2016-01')
    await page.locator('input[type="month"]').nth(1).fill('2026-09')
    await page.getByRole('combobox', { name: '学習方式' }).selectOption('gpu')
    await expect(page.getByTestId('training-context-profile-note')).toContainText('対象レースより前')
    await expect(page.getByTestId('training-context-profile-note')).toContainText('直接観測した値ではありません')
    await page.screenshot({ path: 'reports/generated/context-features/20261012/train-fixture.png', fullPage: true })
    await page.getByRole('button', { name: 'モデル作成', exact: true }).click()
    await expect.poll(() => starts.length).toBe(1)
    expect(starts[0]).toMatchObject({ feature_pipeline: 'local-db-context-v1', training_date_from: '2016-01',
      training_date_to: '2026-09', target: 'speed_deviation', compute_device: 'gpu', reuse_features: true })
    expect(activation).toBe(0)
  })

  test('旧APIで80を選択させず自動学習もしない', async ({ page }) => {
    let starts = 0
    page.on('request', request => { if (request.url().includes('/api/ml/train/start')) starts++ })
    await page.route('/api/ml/train/capability**', route => route.fulfill({ json: { enabled: true } }))
    await page.goto('/train')
    await page.getByRole('button', { name: '詳細設定（上級者向け）' }).click()
    const profile = page.getByRole('combobox', { name: '特徴量構成' })
    await expect(profile.getByRole('option', { name: '条件・展開を追加（比較候補80項目）' })).toBeDisabled()
    await expect(profile).toHaveValue('local-db-shared-optimized-v1')
    expect(starts).toBe(0)
  })
})
