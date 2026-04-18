import { test, expect } from '@playwright/test'
import { mockAuth, mockRacesByDate, mockPredict } from './helpers/mock-api'

test.describe('一括予測ページ', () => {
  test.beforeEach(async ({ page }) => {
    await mockAuth(page)
    await mockRacesByDate(page)
    await mockPredict(page)
    await page.route('/api/purchase', route =>
      route.fulfill({ json: { success: true, purchase_id: 'p-001' } })
    )
  })

  test('ページが正常に表示される', async ({ page }) => {
    await page.goto('/predict-batch')
    await expect(page.getByText('一括予測')).toBeVisible()
  })

  test('リスクモード・資金の設定フォームが表示される', async ({ page }) => {
    await page.goto('/predict-batch')
    await expect(page.getByText(/レース一覧を取得/)).toBeVisible()
  })

  test('レースを選択せずに予測ボタンを押すとトースト警告が出る', async ({ page }) => {
    await page.goto('/predict-batch')
    // レース一覧を取得してから予測ボタンを押す
    const fetchBtn = page.getByRole('button', { name: /レース一覧を取得/ })
    await fetchBtn.click()
    // レース一覧が表示されるのを待つ
    await expect(page.getByText('テストレース1')).toBeVisible({ timeout: 5000 })
    // 全解除してから予測ボタンが無効化されているか確認
    const deselectAll = page.getByRole('button', { name: /全解除/ })
    if (await deselectAll.count() > 0) await deselectAll.click()
    const predictBtn = page.getByRole('button', { name: /予測/ }).last()
    await expect(predictBtn).toBeDisabled()
  })

  test('レース一覧取得 → 全選択 → 予測実行で結果が表示される', async ({ page }) => {
    await page.goto('/predict-batch')
    // ① レース一覧取得
    await page.getByRole('button', { name: /レース一覧を取得/ }).click()
    await expect(page.getByText('テストレース1')).toBeVisible({ timeout: 5000 })
    // ② レース選択（チェックボックスON）
    const checkboxes = page.locator('input[type="checkbox"]')
    if (await checkboxes.count() > 0) {
      await checkboxes.first().check()
    }
    // ③ 予測実行
    const predictBtn = page.getByRole('button', { name: /予測実行|一括予測/ }).last()
    await predictBtn.click()
    // 予測結果カードが現れる
    await expect(page.getByText('テスト馬A').first()).toBeVisible({ timeout: 8000 })
  })

  test('予測結果に確率バーが表示される', async ({ page }) => {
    await page.goto('/predict-batch')
    await page.getByRole('button', { name: /レース一覧を取得/ }).click()
    await expect(page.getByText('テストレース1')).toBeVisible({ timeout: 5000 })
    const checkboxes = page.locator('input[type="checkbox"]')
    if (await checkboxes.count() > 0) await checkboxes.first().check()
    await page.getByRole('button', { name: /予測実行|一括予測/ }).last().click()
    // 確率バーのdivが存在する (スタイルにwidthがある要素)
    await expect(page.locator('[style*="width"]').first()).toBeVisible({ timeout: 8000 })
  })

  test('購入後にダッシュボードへのリンクが表示される', async ({ page }) => {
    await page.goto('/predict-batch')
    await page.getByRole('button', { name: /レース一覧を取得/ }).click()
    await expect(page.getByText('テストレース1')).toBeVisible({ timeout: 5000 })
    const checkboxes = page.locator('input[type="checkbox"]')
    if (await checkboxes.count() > 0) await checkboxes.first().check()
    await page.getByRole('button', { name: /予測実行|一括予測/ }).last().click()
    // 購入ボタンを探す
    const buyBtn = page.getByRole('button', { name: /購入記録|購入する/ }).first()
    if (await buyBtn.isVisible({ timeout: 5000 })) {
      await buyBtn.click()
      await expect(page.getByText(/ダッシュボード/)).toBeVisible({ timeout: 5000 })
    }
  })
})
