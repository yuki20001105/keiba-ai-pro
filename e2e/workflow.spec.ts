/**
 * e2e/workflow.spec.ts
 * ──────────────────────────────────────────────────────────────
 * フルフロー統合テスト
 *
 *   一般ユーザーの主要フロー:
 *     Step 1: 予測実行 (/predict-batch)
 *     Step 2: 成績確認 (/dashboard)
 *
 *   データ取得・モデル管理・本番前チェックは管理者導線、
 *   予測スコア詳細と予測履歴は主要画面から辿る補助機能として個別検証する。
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
async function setupCommonMocks(
  page: Page,
  auth: { role?: 'admin' | 'user'; tier?: 'free' | 'premium' } = {},
) {
  await mockAuth(page, auth)
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

  await page.route('/api/scrape/health**', route =>
    route.fulfill({ json: { status: 'healthy' } })
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
  const trainJobId = '11111111-1111-4111-8111-111111111111'

  await page.route('/api/ml/train/capability**', route =>
    route.fulfill({ json: { enabled: true, reason: null } })
  )

  await page.route('/api/ml/train/start**', route =>
    route.fulfill({ json: { job_id: trainJobId, status: 'started' } })
  )
  await page.route(`/api/ml/train/status/${trainJobId}**`, route => {
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
          evaluation: {
            primary: {
              rank_correlation: 0.758, rmse: 0.45,
              top_pick_win_rate: 0.365, favorite_win_rate: 0.410,
              top_pick_win_rate_delta: -0.045, win_roi: 91.2,
            },
            details: {
              mae: 0.34, r2: 0.61, evaluation_date_from: '2026-01-01',
              evaluation_date_to: '2026-04-01', evaluation_race_count: 100,
            },
            time_slices: [],
          },
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
    await expect(page.getByRole('heading', { name: '期間' })).toBeVisible()
    await expect(page.getByTestId('dry-run-button')).toHaveText('事前確認')
    await expect(page.getByTestId('execute-button')).toHaveText('取得開始')
  })

  test('1-2: ローカルAPIが起動中と表示される', async ({ page }) => {
    await page.goto('/data-collection')
    await expect(page.getByText('API 稼働中', { exact: true })).toBeVisible({ timeout: 5000 })
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

  test('1-4: データ取得画面は取得操作だけに集中している', async ({ page }) => {
    await page.goto('/data-collection')
    await expect(page.getByRole('heading', { name: '期間' })).toBeVisible()
    await expect(page.getByRole('link', { name: /モデル学習/ })).toHaveCount(0)
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
    await expect(page.getByText('モデル作成').first()).toBeVisible()
    await expect(page.getByText('学習設定')).toBeVisible()
  })

  test('2-2: 保存済みモデル一覧に5つの主要指標が表示される', async ({ page }) => {
    await page.goto('/train')
    await expect(page.getByText('保存済みモデル')).toBeVisible()
    await expect(page.getByText('abc123-def456')).toBeVisible({ timeout: 5000 })
    await expect(page.getByText('順位相関').first()).toBeVisible({ timeout: 5000 })
    await expect(page.getByText('0.723').first()).toBeVisible()
    await expect(page.getByText('回収率').first()).toBeVisible()
  })

  test('2-3: 学習実行 → プログレス → 完了と順位相関が表示される', async ({ page }) => {
    await page.goto('/train')

    // モデル作成ボタンをクリック
    await page.getByRole('button', { name: 'モデル作成' }).click()

    // プログレス表示
    await expect(page.getByText(/学習中|実行中/).first()).toBeVisible({ timeout: 5000 })

    // 完了後の順位相関表示
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

  test('4-6: 予測履歴と次の予測へのリンクが表示される', async ({ page }) => {
    await page.goto('/dashboard')
    await expect(page.getByRole('link', { name: /予測履歴/ })).toHaveAttribute('href', '/prediction-history')
    await expect(page.getByRole('link', { name: /予測実行へ/ })).toHaveAttribute('href', '/predict-batch')
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

  test('5-5: AIの判断タブで予測に寄与した要素が確認できる', async ({ page }) => {
    await page.goto('/race-analysis')
    await page.getByText('テストレース1').click()

    const explainTab = page.getByRole('button', { name: 'AIの判断' })
    await expect(explainTab).toBeVisible({ timeout: 5000 })
    await explainTab.click()

    await expect(page.getByText('騎手の勝率').first()).toBeVisible({ timeout: 5000 })
    await expect(page.getByText('速度スコアを上げた要素')).toBeVisible()
  })

  test('5-6: 文章解説はユーザー操作時だけ取得する', async ({ page }) => {
    let explanationRequests = 0
    await page.route('/api/prediction-explanation', route => {
      explanationRequests += 1
      return route.fulfill({ json: { explanation: '騎手の勝率が評価を押し上げています。', source: 'openai' } })
    })
    await page.goto('/race-analysis')
    await page.getByText('テストレース1').click()

    await page.getByRole('button', { name: 'AIの判断' }).click()
    expect(explanationRequests).toBe(0)
    await page.getByRole('button', { name: '文章で解説' }).click()
    await expect(page.getByText('LLM解説')).toBeVisible()
    expect(explanationRequests).toBe(1)
  })

  test('5-7: 日付とレースIDのリンクから対象レースを自動表示できる', async ({ page }) => {
    await page.goto('/race-analysis?date=20260407&race_id=202604070101')

    await expect(page.locator('input[type="date"]')).toHaveValue('2026-04-07')
    await expect(page.getByText('テスト馬A').first()).toBeVisible({ timeout: 5000 })
  })

  test('5-8: 旧featuresリンクでもAIの判断を直接表示できる', async ({ page }) => {
    await page.goto('/race-analysis?date=20260407&race_id=202604070101&tab=features')

    await expect(page.locator('input[type="date"]')).toHaveValue('2026-04-07')
    await expect(page.getByText('速度スコアを上げた要素')).toBeVisible({ timeout: 5000 })
  })
})

// ══════════════════════════════════════════════════════════════════
// Full Workflow: 全ステップを通した統合シナリオ
// ══════════════════════════════════════════════════════════════════
test.describe('【Full Workflow】ホームから主要機能へ遷移するシナリオ', () => {
  test.beforeEach(async ({ page }) => {
    await setupCommonMocks(page, { role: 'user', tier: 'free' })
    await mockScraping(page)
    await mockTraining(page)
    page.on('dialog', dialog => dialog.accept())
  })

  test('ホーム → 予測 → 成績の主要フローを正常に遷移できる', async ({ page }) => {
    // ── ホーム ──
    await page.goto('/home')
    await expect(page.getByText('AI競馬予測')).toBeVisible()
    await page.getByRole('link', { name: /予測実行/ }).first().click()
    await page.waitForURL('/predict-batch')
    await expect(page.getByText('一括予測')).toBeVisible()

    await page.goto('/home')
    await page.getByRole('link', { name: /成績確認/ }).first().click()
    await page.waitForURL('/dashboard')
    await expect(page.getByText('ダッシュボード')).toBeVisible()
  })

  test('一般ユーザーのホームは利用可能な2機能だけにリンクされる', async ({ page }) => {
    await page.goto('/home')

    await expect(page.getByRole('link', { name: /予測実行/ }).first()).toHaveAttribute('href', '/predict-batch')
    await expect(page.getByRole('link', { name: /成績確認/ }).first()).toHaveAttribute('href', '/dashboard')

    for (const href of ['/data-collection', '/train', '/race-analysis', '/prediction-history', '/production-readiness', '/notion-report', '/model-redesign-workbench']) {
      await expect(page.locator(`a[href="${href}"]`)).toHaveCount(0)
    }
  })
})
