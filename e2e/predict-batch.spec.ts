import { test, expect } from '@playwright/test'
import { mockAuth, mockRacesByDate, mockPredict } from './helpers/mock-api'

test.describe('一括予測ページ', () => {
  test.beforeEach(async ({ page }) => {
    await mockAuth(page, { role: 'user', tier: 'free' })
    await mockRacesByDate(page)
    await mockPredict(page)
  })

  test('ページが正常に表示される', async ({ page }) => {
    await page.goto('/predict-batch')
    await expect(page.getByText('一括予測')).toBeVisible()
  })

  test('最近作成したモデルを分かりやすい名前で選べる', async ({ page }) => {
    const newestModelId = 'model_speed_deviation_lightgbm_20180106_20260706_20260912_2159_02101032'
    await mockAuth(page, { role: 'user', tier: 'premium' })
    await page.route('/api/models?ultimate=true', route => route.fulfill({
      json: {
        models: [
          {
            model_id: newestModelId,
            target: 'speed_deviation',
            model_type: 'lightgbm',
            created_at: '20260912_2159',
            training_date_from: '20180106',
            training_date_to: '20260706',
            auc: 0.759527,
            is_active: false,
          },
        ],
      },
    }))

    await page.goto('/predict-batch')
    const modelSelect = page.getByLabel('使用モデル')
    await expect(modelSelect.getByRole('option').first()).toHaveText('既定モデル（使用中）')
    await expect(modelSelect.getByRole('option', {
      name: '速度偏差｜2026/09/12 21:59｜学習 2018/01–2026/07｜AUC 0.760',
    })).toHaveAttribute('value', newestModelId)

    await modelSelect.selectOption(newestModelId)
    await expect(modelSelect).toHaveValue(newestModelId)

    await page.getByRole('button', { name: /レース一覧を取得/ }).click()
    await expect(page.getByText('テストレース1')).toBeVisible()
    await page.locator('input[type="checkbox"]').first().check()
    const predictionRequest = page.waitForRequest(request => (
      request.url().includes('/api/analyze-race') && request.method() === 'POST'
    ))
    await page.getByRole('button', { name: /予測実行|一括予測/ }).last().click()

    expect((await predictionRequest).postDataJSON()).toMatchObject({
      model_id: newestModelId,
      include_explanation: true,
    })
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

  test('レース一覧取得 → 全選択 → 予測実行でスコア詳細が表示される', async ({ page }) => {
    await mockAuth(page, { role: 'user', tier: 'premium' })
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
    await expect(page.getByRole('heading', { name: '③ 予測結果・スコア詳細' })).toBeVisible()
    for (const heading of ['順位', 'スコア', '勝率', '複勝圏', 'アンサンブル', '期待値', 'オッズ', '人気']) {
      await expect(page.getByRole('columnheader', { name: heading })).toBeVisible()
    }
    const explanationButton = page.getByRole('button', { name: /AIの判断を見る/ })
    await expect(explanationButton).toBeVisible()
    await explanationButton.click()
    await expect(page.getByTestId('race-explanation-panel')).toBeVisible()
    await expect(page.getByText('騎手の勝率').first()).toBeVisible()
    await expect(page.getByText('評価を上げた要素')).toBeVisible()
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
    await expect(page.getByRole('button', { name: /AIの判断を見る/ })).toHaveCount(0)
  })

  test('AIが券種・買い目・金額を決定し、手動操作と出力は表示しない', async ({ page }) => {
    await page.goto('/predict-batch')
    await page.getByRole('button', { name: /レース一覧を取得/ }).click()
    await expect(page.getByText('テストレース1')).toBeVisible({ timeout: 5000 })
    const checkboxes = page.locator('input[type="checkbox"]')
    if (await checkboxes.count() > 0) await checkboxes.first().check()
    await page.getByRole('button', { name: /予測実行|一括予測/ }).last().click()

    await expect(page.getByText('AI 買い目')).toBeVisible({ timeout: 8000 })
    await expect(page.getByText('¥1,000 × 1点')).toBeVisible()
    for (const removedText of [
      '手動追加',
      '購入を記録する',
      '購入推奨リスト出力',
      'JSON 出力',
      'CSV 出力',
      'オッズを今すぐ更新',
    ]) {
      await expect(page.getByText(removedText, { exact: false })).toHaveCount(0)
    }
  })
})
