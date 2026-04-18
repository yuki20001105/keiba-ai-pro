import { test, expect } from '@playwright/test'
import { mockAuth, mockRacesByDate, mockPredict } from './helpers/mock-api'

const FEAT_DATA = {
  race_id: '202604070101',
  feature_count: 12,
  horse_count: 3,
  feature_columns: ['horse_number', 'horse_name', 'odds_win', 'odds_place', 'past_1_rank', 'past_2_rank', 'jockey_win_rate', 'track_surface', 'weight', 'weight_diff', 'age', 'sex'],
  records: [
    { horse_number: 1, horse_name: 'テスト馬A', odds_win: 3.2, odds_place: 1.5, past_1_rank: 1, past_2_rank: 2, jockey_win_rate: 0.15, track_surface: '芝', weight: 480, weight_diff: 2, age: 4, sex: '牡' },
    { horse_number: 2, horse_name: 'テスト馬B', odds_win: 5.0, odds_place: 2.1, past_1_rank: 3, past_2_rank: 1, jockey_win_rate: 0.12, track_surface: '芝', weight: 490, weight_diff: -4, age: 5, sex: '牝' },
  ],
}

test.describe('予測結果確認ページ（Race Analysis）', () => {
  test.beforeEach(async ({ page }) => {
    await mockAuth(page)
    await mockRacesByDate(page)
    await mockPredict(page)
    await page.route('/api/debug/race/**', route =>
      route.fulfill({ json: FEAT_DATA })
    )
  })

  test('ページが正常に表示される', async ({ page }) => {
    await page.goto('/race-analysis')
    await expect(page.getByText('予測結果確認')).toBeVisible()
  })

  test('日付選択がある', async ({ page }) => {
    await page.goto('/race-analysis')
    await expect(page.locator('input[type="date"]')).toBeVisible()
  })

  test('レース一覧が取得される', async ({ page }) => {
    await page.goto('/race-analysis')
    await expect(page.getByText('テストレース1')).toBeVisible({ timeout: 5000 })
  })

  test('レース選択後に予測結果タブが表示される', async ({ page }) => {
    await page.goto('/race-analysis')
    await page.getByText('テストレース1').click()
    await expect(page.getByRole('button', { name: /予測結果/ })).toBeVisible({ timeout: 8000 })
  })

  test('特徴量タブに切り替えられる', async ({ page }) => {
    await page.goto('/race-analysis')
    await page.getByText('テストレース1').click()
    // 特徴量タブをクリック
    const featTab = page.getByRole('button', { name: /特徴量/ })
    await expect(featTab).toBeVisible({ timeout: 5000 })
    await featTab.click()
    // 列名が表示される
    await expect(page.getByRole('columnheader', { name: 'odds_win' })).toBeVisible({ timeout: 5000 })
  })

  test('特徴量タブにグループチップが表示される', async ({ page }) => {
    await page.goto('/race-analysis')
    await page.getByText('テストレース1').click()
    const featTab = page.getByRole('button', { name: /特徴量/ })
    await featTab.click()
    // グループチップ（プレフィックスボタン）が表示される
    await expect(page.getByRole('button', { name: /odds/ })).toBeVisible({ timeout: 5000 })
    await expect(page.getByRole('button', { name: /past/ })).toBeVisible({ timeout: 5000 })
  })

  test('グループチップをクリックすると列が非表示になる', async ({ page }) => {
    await page.goto('/race-analysis')
    await page.getByText('テストレース1').click()
    const featTab = page.getByRole('button', { name: /特徴量/ })
    await featTab.click()
    // oddsグループをクリックして非表示に
    const oddsChip = page.getByRole('button', { name: /^odds/ })
    await oddsChip.click()
    // odds列が消える
    await expect(page.getByRole('columnheader', { name: 'odds_win' })).not.toBeVisible()
  })

  test('列名フィルターで絞り込みができる', async ({ page }) => {
    await page.goto('/race-analysis')
    await page.getByText('テストレース1').click()
    const featTab = page.getByRole('button', { name: /特徴量/ })
    await featTab.click()
    await page.getByPlaceholder(/列名で絞り込み/).fill('odds')
    await expect(page.getByText(/列を表示/)).toBeVisible({ timeout: 3000 })
  })

  test('レースが見つからない場合は空状態メッセージが表示される', async ({ page }) => {
    await page.route('/api/races/by-date**', route =>
      route.fulfill({ json: { races: [] } })
    )
    await page.goto('/race-analysis')
    await expect(page.getByText('レースが見つかりません')).toBeVisible({ timeout: 5000 })
  })
})
