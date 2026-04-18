/**
 * e2e/workflow.spec.ts
 * ──────────────────────────────────────────────────────────────
 * フルフロー統合テスト
 *
 *   Step 1: データ取得  (/data-collection)
 *   Step 2: モデル学習  (/train)
 *   Step 3: 予測実行    (/predict-batch)
 *   Step 4: 成績確認    (/dashboard)
 *   Step 5: 予測スコア詳細 (/race-analysis)
 *
 * 実行コマンド:
 *   npx playwright test e2e/workflow.spec.ts --reporter=list
 *
 * 全ステップ一括:
 *   npx playwright test e2e/workflow.spec.ts --reporter=list --headed
 * ──────────────────────────────────────────────────────────────
 */
import { test, expect, Page } from '@playwright/test'
import {
  mockAuth,
  mockHealth,
  mockDataStats,
  mockRacesByDate,
  mockModels,
  mockAnalyzeRace,
  mockPurchaseHistory,
} from './helpers/mock-api'

// ── 共通モック設定 ──────────────────────────────────────────────
async function setupCommonMocks(page: Page) {
  await mockAuth(page)
  await mockHealth(page)
  await mockDataStats(page)
  await mockRacesByDate(page)
  await mockModels(page)
  await mockAnalyzeRace(page)
  await mockPurchaseHistory(page)
}

// ── スクレイピング用モック ────────────────────────────────────────
async function mockScraping(page: Page) {
  let scrapeJobPoll = 0

  await page.route('/api/scrape/status/__health_check__**', route =>
    route.fulfill({ json: { status: 'ok' } })
  )
  await page.route('/api/scrape', route => {
    if (route.request().method() === 'POST') {
      scrapeJobPoll = 0
      return route.fulfill({ json: { job_id: 'wf-scrape-001' } })
    }
    return route.continue()
  })
  await page.route('/api/scrape/status/wf-scrape-001**', route => {
    scrapeJobPoll++
    if (scrapeJobPoll < 3) {
      return route.fulfill({
        json: {
          status: 'running',
          progress: { current: scrapeJobPoll * 30, total: 100, message: `取得中... (${scrapeJobPoll * 3}/10)`, eta: '20秒' },
        },
      })
    }
    return route.fulfill({
      json: { status: 'completed', races_collected: 24, elapsed_time: 18 },
    })
  })
}

// ── 学習用モック ──────────────────────────────────────────────────
async function mockTraining(page: Page) {
  let trainPoll = 0

  await page.route('/api/ml/train/start**', route =>
    route.fulfill({ json: { job_id: 'wf-train-001', status: 'started' } })
  )
  await page.route('/api/ml/train/status/wf-train-001**', route => {
    trainPoll++
    if (trainPoll < 3) {
      return route.fulfill({
        json: { status: 'running', progress: '学習中...', result: null, error: null },
      })
    }
    return route.fulfill({
      json: {
        status: 'completed',
        progress: '完了',
        result: {
          success: true,
          model_id: 'wf-model-001',
          model_path: 'models/wf-model-001.joblib',
          metrics: { auc: 0.758, cv_auc_mean: 0.741, logloss: 0.45 },
          data_count: 8500,
          race_count: 100,
          feature_count: 61,
          training_time: 62.0,
          message: '学習完了',
          optuna_executed: false,
          feature_columns: [],
        },
        error: null,
      },
    })
  })
}

// ══════════════════════════════════════════════════════════════════
// Step 1: データ取得
// ══════════════════════════════════════════════════════════════════
test.describe('【Step 1】データ取得フロー', () => {
  test.beforeEach(async ({ page }) => {
    await setupCommonMocks(page)
    await mockScraping(page)
    page.on('dialog', dialog => dialog.accept()) // confirm()ダイアログを承認
  })

  test('1-1: データ取得ページが正常に表示される', async ({ page }) => {
    await page.goto('/data-collection')
    await expect(page.getByText('データ取得', { exact: true })).toBeVisible()
    await expect(page.getByText('期間指定一括取得')).toBeVisible()
  })

  test('1-2: ローカルAPIが起動中と表示される', async ({ page }) => {
    await page.goto('/data-collection')
    await expect(page.getByText(/起動中|オンライン/)).toBeVisible({ timeout: 5000 })
  })

  test('1-3: 期間入力 → スクレイピング実行 → 進捗バーが表示される', async ({ page }) => {
    await page.goto('/data-collection')

    // 期間を設定
    const [startInput, endInput] = await page.locator('input[type="month"]').all()
    await startInput.fill('2026-03')
    await endInput.fill('2026-04')

    // 取得開始
    await page.getByRole('button', { name: /取得開始/ }).click()

    // 進捗バーまたは完了メッセージの確認
    await expect(page.getByText(/取得中|完了|24/).first()).toBeVisible({ timeout: 15000 })
  })

  test('1-4: 次のステップ（モデル学習）へのリンクが表示される', async ({ page }) => {
    await page.goto('/data-collection')
    await expect(page.getByRole('link', { name: /モデル学習/ })).toBeVisible()
  })
})

// ══════════════════════════════════════════════════════════════════
// Step 2: モデル学習
// ══════════════════════════════════════════════════════════════════
test.describe('【Step 2】モデル学習フロー', () => {
  test.beforeEach(async ({ page }) => {
    await setupCommonMocks(page)
    await mockTraining(page)
  })

  test('2-1: モデル学習ページが正常に表示される', async ({ page }) => {
    await page.goto('/train')
    await expect(page.getByText('モデル学習')).toBeVisible()
    await expect(page.getByText('学習設定')).toBeVisible()
  })

  test('2-2: 保存済みモデル一覧が表示される（AUC付き）', async ({ page }) => {
    await page.goto('/train')
    await expect(page.getByText('保存済みモデル')).toBeVisible()
    await expect(page.getByText('abc123-def456')).toBeVisible({ timeout: 5000 })
    // AUC値が表示される
    await expect(page.getByText(/AUC.*0\.7/)).toBeVisible({ timeout: 5000 })
  })

  test('2-3: 学習実行 → プログレス → 完了とAUCが表示される', async ({ page }) => {
    await page.goto('/train')

    // 学習開始ボタンをクリック
    await page.getByRole('button', { name: '学習開始' }).click()

    // プログレス表示
    await expect(page.getByText(/学習中|実行中/).first()).toBeVisible({ timeout: 5000 })

    // 完了後のAUC表示
    await expect(page.getByText(/0\.758|0\.741|完了/).first()).toBeVisible({ timeout: 20000 })
  })

  test('2-4: 詳細設定パネルを開けることができる', async ({ page }) => {
    await page.goto('/train')
    // 詳細設定トグルがある
    const advToggle = page.getByRole('button', { name: /詳細設定/ })
    await expect(advToggle).toBeVisible()
    await advToggle.click()
    // テストサイズなど詳細設定が展開される
    await expect(page.getByText(/テストサイズ|CV/).first()).toBeVisible()
  })
})

// ══════════════════════════════════════════════════════════════════
// Step 3: 予測実行
// ══════════════════════════════════════════════════════════════════
test.describe('【Step 3】予測実行フロー', () => {
  test.beforeEach(async ({ page }) => {
    await setupCommonMocks(page)
    // 購入API
    await page.route('/api/purchase', route =>
      route.fulfill({ json: { success: true, purchase_id: 'wf-p-001' } })
    )
  })

  test('3-1: 予測実行ページが正常に表示される', async ({ page }) => {
    await page.goto('/predict-batch')
    await expect(page.getByText('一括予測')).toBeVisible()
    await expect(page.getByRole('button', { name: /レース一覧を取得/ })).toBeVisible()
  })

  test('3-2: 賭け設定パネルが折りたたまれて概要が表示される', async ({ page }) => {
    await page.goto('/predict-batch')
    // 賭け設定のサマリーテキストが見える（折りたたみ）
    await expect(page.getByText(/賭け設定/)).toBeVisible()
    await expect(page.getByText(/バンクロール|¥/)).toBeVisible()
  })

  test('3-3: レース一覧取得 → 予測実行 → 結果カードが表示される', async ({ page }) => {
    await page.goto('/predict-batch')

    // レース一覧取得
    await page.getByRole('button', { name: /レース一覧を取得/ }).click()
    await expect(page.getByText('テストレース1')).toBeVisible({ timeout: 5000 })

    // チェックボックスで選択
    const checkbox = page.locator('input[type="checkbox"]').first()
    if (await checkbox.count() > 0) {
      await checkbox.check()
    }

    // 予測実行
    const predictBtn = page.getByRole('button', { name: /予測実行|一括予測/ }).last()
    await predictBtn.click()

    // 予測結果：馬名が表示される
    await expect(page.getByText('テスト馬A').first()).toBeVisible({ timeout: 8000 })
  })

  test('3-4: 予測スコア（確率）が数値で表示される', async ({ page }) => {
    await page.goto('/predict-batch')

    await page.getByRole('button', { name: /レース一覧を取得/ }).click()
    await expect(page.getByText('テストレース1')).toBeVisible({ timeout: 5000 })

    const checkbox = page.locator('input[type="checkbox"]').first()
    if (await checkbox.count() > 0) await checkbox.check()

    await page.getByRole('button', { name: /予測実行|一括予測/ }).last().click()

    // スコア数値（40%や35%など）が表示される
    await expect(page.getByText(/\d+(\.\d+)?%/).first()).toBeVisible({ timeout: 8000 })
  })

  test('3-5: 購入推奨の単位金額が表示される', async ({ page }) => {
    await page.goto('/predict-batch')

    await page.getByRole('button', { name: /レース一覧を取得/ }).click()
    await expect(page.getByText('テストレース1')).toBeVisible({ timeout: 5000 })

    const checkbox = page.locator('input[type="checkbox"]').first()
    if (await checkbox.count() > 0) await checkbox.check()

    await page.getByRole('button', { name: /予測実行|一括予測/ }).last().click()

    // 期待値か購入推奨が表示される
    await expect(page.getByText(/期待値|推奨|購入/).first()).toBeVisible({ timeout: 8000 })
  })
})

// ══════════════════════════════════════════════════════════════════
// Step 4: 成績確認（ダッシュボード）
// ══════════════════════════════════════════════════════════════════
test.describe('【Step 4】成績確認フロー', () => {
  test.beforeEach(async ({ page }) => {
    await setupCommonMocks(page)
  })

  test('4-1: ダッシュボードが正常に表示される', async ({ page }) => {
    await page.goto('/dashboard')
    await expect(page.getByText('ダッシュボード')).toBeVisible()
  })

  test('4-2: DB統計（レース数・馬数・モデル数）が表示される', async ({ page }) => {
    await page.goto('/dashboard')
    // data-statsモックの値 12345, 98765, 3 が表示される
    await expect(page.getByText(/12.?345|12345/)).toBeVisible({ timeout: 5000 })
    await expect(page.getByText(/DB|レース|馬/).first()).toBeVisible({ timeout: 5000 })
  })

  test('4-3: 購入履歴テーブルが表示される', async ({ page }) => {
    await page.goto('/dashboard')
    await expect(page.getByText('購入履歴')).toBeVisible({ timeout: 5000 })
  })

  test('4-4: 購入サマリーカードが表示される（購入回数・的中率・回収率）', async ({ page }) => {
    await page.goto('/dashboard')
    await expect(page.getByText(/購入回数|的中率|回収率/).first()).toBeVisible({ timeout: 5000 })
  })

  test('4-5: 購入履歴から結果入力ができる（テスト馬の払戻）', async ({ page }) => {
    // PATCHモック
    await page.route('/api/purchase/**', route => {
      if (route.request().method() === 'PATCH') {
        return route.fulfill({ json: { success: true } })
      }
      return route.continue()
    })
    await page.goto('/dashboard')

    // 「結果入力」ボタンをクリック
    const resultBtn = page.getByRole('button', { name: /結果入力/ }).first()
    if (await resultBtn.isVisible({ timeout: 5000 })) {
      await resultBtn.click()
      // 払戻金額入力フォームが表示される
      await expect(page.getByPlaceholder('0')).toBeVisible({ timeout: 3000 })
    }
  })

  test('4-6: データ取得ページへのリンクが表示される（次サイクルへ）', async ({ page }) => {
    await page.goto('/dashboard')
    await expect(page.getByRole('link', { name: /データ取得/ })).toBeVisible({ timeout: 5000 })
  })
})

// ══════════════════════════════════════════════════════════════════
// Step 5: 予測スコア詳細（Race Analysis）
// ══════════════════════════════════════════════════════════════════
test.describe('【Step 5】予測スコア詳細フロー', () => {
  const FEAT_DATA = {
    race_id: '202604070101',
    feature_count: 8,
    horse_count: 3,
    feature_columns: ['horse_number', 'horse_name', 'odds_win', 'odds_place', 'past_1_rank', 'past_2_rank', 'jockey_win_rate', 'age'],
    records: [
      { horse_number: 1, horse_name: 'テスト馬A', odds_win: 3.2, odds_place: 1.5, past_1_rank: 1, past_2_rank: 2, jockey_win_rate: 0.15, age: 4 },
      { horse_number: 2, horse_name: 'テスト馬B', odds_win: 5.0, odds_place: 2.1, past_1_rank: 3, past_2_rank: 1, jockey_win_rate: 0.12, age: 5 },
    ],
  }

  test.beforeEach(async ({ page }) => {
    await setupCommonMocks(page)
    await page.route('/api/debug/race/**', route => route.fulfill({ json: FEAT_DATA }))
  })

  test('5-1: 予測スコア詳細ページが正常に表示される', async ({ page }) => {
    await page.goto('/race-analysis')
    await expect(page.getByText('予測結果確認')).toBeVisible()
    await expect(page.locator('input[type="date"]')).toBeVisible()
  })

  test('5-2: レース一覧が自動ロードされる', async ({ page }) => {
    await page.goto('/race-analysis')
    await expect(page.getByText('テストレース1')).toBeVisible({ timeout: 5000 })
    await expect(page.getByText('テストレース2')).toBeVisible({ timeout: 5000 })
  })

  test('5-3: レースを選択すると予測スコアが表示される', async ({ page }) => {
    await page.goto('/race-analysis')
    await page.getByText('テストレース1').click()

    // 予測スコア（確率）が表示される
    await expect(page.getByText('テスト馬A').first()).toBeVisible({ timeout: 5000 })
    await expect(page.getByText('テスト馬B').first()).toBeVisible({ timeout: 5000 })
  })

  test('5-4: 予測タブで馬ごとのスコアと期待値が確認できる', async ({ page }) => {
    await page.goto('/race-analysis')
    await page.getByText('テストレース1').click()

    // 予測結果タブをクリック
    const predTab = page.getByRole('button', { name: /予測結果/ })
    if (await predTab.isVisible({ timeout: 3000 })) await predTab.click()

    // 期待値か確率数値が表示される
    await expect(page.getByText(/0\.\d{2}|%|\d+\.\d+/).first()).toBeVisible({ timeout: 5000 })
  })

  test('5-5: 特徴量タブで入力特徴量データが確認できる', async ({ page }) => {
    await page.goto('/race-analysis')
    await page.getByText('テストレース1').click()

    // 特徴量タブへ切り替え
    const featTab = page.getByRole('button', { name: /特徴量/ })
    await expect(featTab).toBeVisible({ timeout: 5000 })
    await featTab.click()

    // 文字列テーブルが表示される
    await expect(page.getByRole('columnheader', { name: 'odds_win' })).toBeVisible({ timeout: 5000 })
    await expect(page.getByText('テスト馬A', { exact: false }).first()).toBeVisible({ timeout: 5000 })
  })

  test('5-6: 特徴量グループチップで列フィルタリングができる', async ({ page }) => {
    await page.goto('/race-analysis')
    await page.getByText('テストレース1').click()

    const featTab = page.getByRole('button', { name: /特徴量/ })
    await featTab.click()

    // oddsグループチップを非表示に
    const oddsChip = page.getByRole('button', { name: /^odds/ })
    await expect(oddsChip).toBeVisible({ timeout: 5000 })
    await oddsChip.click()

    // odds_win列が消える
    await expect(page.getByRole('columnheader', { name: 'odds_win' })).not.toBeVisible()
  })
})

// ══════════════════════════════════════════════════════════════════
// Full Workflow: 全ステップを通した統合シナリオ
// ══════════════════════════════════════════════════════════════════
test.describe('【Full Workflow】ホームから全ページ遷移シナリオ', () => {
  test.beforeEach(async ({ page }) => {
    await setupCommonMocks(page)
    await mockScraping(page)
    await mockTraining(page)
    page.on('dialog', dialog => dialog.accept())
  })

  test('ホーム → データ取得 → 学習 → 予測 → 成績 まで全ページ正常遷移', async ({ page }) => {
    // ── ホーム ──
    await page.goto('/home')
    await expect(page.getByText('AI競馬予測')).toBeVisible()
    await expect(page.getByText('データ取得')).toBeVisible()

    // Step 1 へ
    await page.getByRole('link', { name: 'データ取得' }).first().click()
    await page.waitForURL('/data-collection')
    await expect(page.getByText('データ取得', { exact: true })).toBeVisible()

    // Step 2 へ
    await page.getByRole('link', { name: /モデル学習/ }).click()
    await page.waitForURL('/train')
    await expect(page.getByText('モデル学習')).toBeVisible()

    // Step 3 へ（ヘッダーロゴからホームに戻り再遷移、または直接）
    await page.goto('/predict-batch')
    await expect(page.getByText('一括予測')).toBeVisible()

    // Step 4 へ
    await page.goto('/dashboard')
    await expect(page.getByText('ダッシュボード')).toBeVisible()

    // Step 5 へ（詳細分析）
    await page.goto('/race-analysis')
    await expect(page.getByText('予測結果確認')).toBeVisible()
  })

  test('ホームのStepカードから各ページへ正しくリンクされる', async ({ page }) => {
    await page.goto('/home')

    // 各Stepリンクが正しいURLを持つ
    await expect(page.getByRole('link', { name: 'データ取得' }).first()).toHaveAttribute('href', '/data-collection')
    await expect(page.getByRole('link', { name: 'モデル学習' }).first()).toHaveAttribute('href', '/train')
    await expect(page.getByRole('link', { name: '予測実行' }).first()).toHaveAttribute('href', '/predict-batch')
    await expect(page.getByRole('link', { name: '成績確認' }).first()).toHaveAttribute('href', '/dashboard')
  })
})
