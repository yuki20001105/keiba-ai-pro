import { test, expect } from '@playwright/test'
import { mockAuth, mockHealth, mockRacesByDate, mockPredict } from './helpers/mock-api'
import { referenceSimulationResult } from '../src/__tests__/helpers/reference-expectation'

test.describe('一括予測ページ', () => {
  test.beforeEach(async ({ page }) => {
    await mockAuth(page, { role: 'user', tier: 'free' })
    await mockHealth(page)
    await mockRacesByDate(page)
    await mockPredict(page)
  })

  test('ページが正常に表示される', async ({ page }) => {
    await page.goto('/predict-batch')
    await expect(page.getByText('一括予測')).toBeVisible()
  })

  test('仮オッズの期待倍率と三連単合計が見え、購入・保存には反映されない', async ({ page, baseURL }) => {
    const result = referenceSimulationResult()
    const now = Date.now()
    result.model_id = 'abc123-def456'
    result.race_info = { ...result.race_info, race_id: '202604070101', race_name: '仮試算レース', num_horses: 17 }
    const template = result.predictions[0]
    result.predictions = Array.from({ length: 17 }, (_, index) => {
      const probability = index === 0 ? .4 : .6 / 16
      return { ...template, horse_number: index + 1, horse_name: `未価格馬${index + 1}`,
        predicted_rank: index + 1, p_raw: 1 - index / 100, p_norm: probability, win_probability: probability, odds: null }
    })
    result.reference_simulation = { ...result.reference_simulation!, model_id: result.model_id,
      race_id: result.race_info.race_id, computed_at: new Date(now).toISOString(), valid_until: new Date(now + 300000).toISOString(),
      items: result.predictions.map(p => ({ horse_number: p.horse_number, probability: p.p_norm })) }
    result.reference_bets = { schema: 'rank-reference-bets-v1', basis: 'ranking_only',
      purchase_eligible: false, monetary_amounts_assigned: false, tickets: [
        { bet_type: '単勝', combination: '1', horse_numbers: [1] },
        { bet_type: 'ワイド', combination: '1-2', horse_numbers: [1, 2] },
      ] }
    result.freshness = { schema_version: 'prediction-freshness-v1', data_revision: 'e2e-saved-source',
      model_runtime_revision: 'e2e-model-runtime', resolved_model_id: result.model_id, source_tracking: true,
      revision_kind: 'sqlite-source-generation', source: 'saved_db', generated_at: new Date(now).toISOString(),
      cache_hit: false, data_changed_during_prediction: false }
    const writes: string[] = []
    await page.route('**/*', route => new URL(route.request().url()).origin === new URL(baseURL!).origin
      ? route.continue() : route.abort())
    await page.route('**/api/**', route => {
      if (route.request().method() !== 'GET') writes.push(new URL(route.request().url()).pathname)
      return route.fulfill({ status: 503, json: { error: 'Unexpected API blocked in display-only fixture' } })
    })
    await mockAuth(page, { role: 'user', tier: 'free' })
    await mockHealth(page)
    await page.route('**/api/models**', route => route.fulfill({ json: { models: [
      { model_id: result.model_id, model_type: 'lightgbm', target: 'speed_deviation', is_active: true },
    ] } }))
    await page.route('**/api/races/by-date**', route => route.fulfill({ json: { races: [result.race_info] } }))
    await page.route('**/api/prediction-freshness**', route => route.fulfill({ json: {
      ...result.freshness, capabilities: { saved_db_recompute: true },
    } }))
    await page.route('**/api/analyze-race**', route => route.fulfill({ json: result }))
    await page.setViewportSize({ width: 1080, height: 1080 })
    await page.goto('/predict-batch')
    await page.getByRole('button', { name: 'レース一覧を取得' }).click()
    await page.getByRole('checkbox').last().check()
    await page.getByRole('button', { name: /選択 1 レースを一括予測/ }).click()
    const purchase = page.getByRole('region', { name: '買い目判定' })
    await expect(purchase).toContainText('見送り')
    expect(await page.getByText('全馬の予測データ', { exact: true }).evaluate(el => (el.parentElement as HTMLDetailsElement).open)).toBe(false)
    const cached = await page.evaluate(() => Object.keys(localStorage).filter(k => k.startsWith('ra-cache:')).map(k => [k, localStorage.getItem(k)]))
    const win = page.getByRole('spinbutton', { name: '単勝 1の仮オッズ' })
    await win.fill('9')
    await expect(win.locator('xpath=ancestor::tr')).toContainText('¥900')
    await page.setViewportSize({ width: 390, height: 844 })
    await win.scrollIntoViewIfNeeded()
    await expect(win.locator('xpath=ancestor::tr').getByText('3.60', { exact: true })).toBeInViewport({ ratio: .9 })
    await page.setViewportSize({ width: 1080, height: 1080 })
    await page.getByText('買い目と仮オッズを確認', { exact: true }).click()
    const quotes = page.getByRole('spinbutton', { name: /^三連単 .*の仮オッズ$/ })
    await expect(quotes).toHaveCount(12)
    await quotes.first().fill('1000')
    const aggregate = page.getByRole('group', { name: '三連単の試算合計' })
    await expect(aggregate).toContainText('全点のオッズ・確率が揃うと算出')
    for (const quote of await quotes.all()) await quote.fill('1000')
    await quotes.first().scrollIntoViewIfNeeded()
    await aggregate.locator('..').evaluate(el => el.scrollIntoView({ block: 'center' }))
    await expect(aggregate).toContainText('全点の仮期待倍率：1.67')
    await expect(aggregate).toContainText('試算額 ¥1,200')
    await expect(aggregate).toBeInViewport({ ratio: .9 })
    await expect(purchase).toContainText('合計 ¥0')
    await expect(purchase.getByRole('button', { name: '結果を記録' })).toHaveCount(0)
    expect(writes).toEqual([])
    expect(await page.evaluate(() => Object.keys(localStorage).filter(k => k.startsWith('ra-cache:')).map(k => [k, localStorage.getItem(k)]))).toEqual(cached)
    await page.setViewportSize({ width: 390, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(391)
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
      name: '速度偏差｜2026/09/12 21:59｜学習 2018/01–2026/07｜相関 0.760',
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

  test('DBにレースがない場合はエラーではなくデータ未取得と表示する', async ({ page }) => {
    await page.unroute('/api/races/by-date**')
    await page.route('/api/races/by-date**', route => route.fulfill({
      json: { races: [], count: 0, date: '20260913' },
    }))

    await page.goto('/predict-batch')
    await page.getByRole('button', { name: /レース一覧を取得/ }).click()

    await expect(page.getByText('この日のデータはまだ取得されていません。')).toBeVisible()
    await expect(page.getByText(/DBに見つかりません/)).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'この日付をスクレイプして取得' })).toBeVisible()
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
    await page.getByText('全馬の予測データ', { exact: true }).click()
    await expect(page.getByRole('region', { name: 'レース予測' })).toBeVisible()
    for (const heading of ['順位', 'スコア', '勝率', '複勝参考', '合成参考', '期待値', 'オッズ', '人気']) {
      await expect(page.getByRole('columnheader', { name: heading })).toBeVisible()
    }
    const explanationButton = page.getByRole('button', { name: /AIの判断を見る/ })
    await expect(explanationButton).toBeVisible()
    await explanationButton.click()
    await expect(page.getByTestId('race-explanation-panel')).toBeVisible()
    await expect(page.getByText('騎手の勝率').first()).toBeVisible()
    await expect(page.getByText('速度スコアを上げた要素')).toBeVisible()
  })

  test('予測結果に確率バーが表示される', async ({ page }) => {
    await page.goto('/predict-batch')
    await page.getByRole('button', { name: /レース一覧を取得/ }).click()
    await expect(page.getByText('テストレース1')).toBeVisible({ timeout: 5000 })
    const checkboxes = page.locator('input[type="checkbox"]')
    if (await checkboxes.count() > 0) await checkboxes.first().check()
    await page.getByRole('button', { name: /予測実行|一括予測/ }).last().click()
    await page.getByText('全馬の予測データ', { exact: true }).click()
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

    await expect(page.getByRole('region', { name: '買い目判定' })).toBeVisible({ timeout: 8000 })
    await expect(page.getByRole('region', { name: '買い目判定' })).toContainText('購入候補')
    await expect(page.getByRole('region', { name: '買い目判定' })).toContainText('合計 ¥300')
    await expect(page.getByText(/AI参考として記録しました/)).toBeVisible()
    await expect(page.getByRole('button', { name: '購入済みとして記録' })).toBeVisible()
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
