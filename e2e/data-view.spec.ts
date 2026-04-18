import { test, expect } from '@playwright/test'
import { mockAuth, mockRacesByDate } from './helpers/mock-api'

const FEAT_DATA = {
  race_id: '202604070101',
  feature_count: 10,
  horse_count: 2,
  feature_columns: ['horse_number', 'horse_name', 'odds_win', 'odds_place', 'past_1_rank', 'jockey_win_rate', 'weight', 'age', 'sex', 'track_surface'],
  records: [
    { horse_number: 1, horse_name: 'テスト馬A', odds_win: 3.2, odds_place: 1.5, past_1_rank: 1, jockey_win_rate: 0.15, weight: 480, age: 4, sex: '牡', track_surface: '芝' },
    { horse_number: 2, horse_name: 'テスト馬B', odds_win: 5.0, odds_place: 2.1, past_1_rank: 3, jockey_win_rate: 0.12, weight: 490, age: 5, sex: '牝', track_surface: '芝' },
  ],
}

const RAW_DATA = {
  race_id: '202604070101',
  race_info_columns: ['race_name', 'venue', 'date', 'race_no', 'track_type', 'distance'],
  horse_columns: ['horse_number', 'horse_name', 'jockey', 'weight'],
  race_info: { race_name: 'テストレース1', venue: '東京', date: '2026-04-07', race_no: 1, track_type: '芝', distance: 1600 },
  horses: [
    { horse_number: 1, horse_name: 'テスト馬A', jockey: '騎手A', weight: 480 },
    { horse_number: 2, horse_name: 'テスト馬B', jockey: '騎手B', weight: 490 },
  ],
}

test.describe('データ確認ページ（Data View）', () => {
  test.beforeEach(async ({ page }) => {
    await mockAuth(page)
    await mockRacesByDate(page)
    await page.route('/api/debug/race/**', route => {
      if (route.request().url().includes('/features')) {
        return route.fulfill({ json: FEAT_DATA })
      }
      return route.fulfill({ json: RAW_DATA })
    })
  })

  test('ページが正常に表示される', async ({ page }) => {
    await page.goto('/data-view')
    await expect(page.getByText('データ確認')).toBeVisible()
  })

  test('生データ・特徴量のタブが表示される', async ({ page }) => {
    await page.goto('/data-view')
    // レースを選択すればタブが表示される
    await expect(page.getByText('テストレース1')).toBeVisible({ timeout: 5000 })
    await page.getByText('テストレース1').click()
    await expect(page.getByRole('button', { name: /生データ/ })).toBeVisible({ timeout: 5000 })
    await expect(page.getByRole('button', { name: /特徴量/ })).toBeVisible({ timeout: 5000 })
  })

  test('レース一覧が読み込まれる', async ({ page }) => {
    await page.goto('/data-view')
    await expect(page.getByText('テストレース1')).toBeVisible({ timeout: 5000 })
  })

  test('特徴量タブにグループチップが表示される', async ({ page }) => {
    await page.goto('/data-view')
    await page.getByText('テストレース1').click()
    const featTab = page.getByRole('button', { name: /特徴量/ })
    await featTab.click()
    // グループチップ
    await expect(page.getByRole('button', { name: /odds/ })).toBeVisible({ timeout: 5000 })
    await expect(page.getByRole('button', { name: /past/ })).toBeVisible({ timeout: 5000 })
  })

  test('グループチップで列の表示/非表示を切り替えられる', async ({ page }) => {
    await page.goto('/data-view')
    await page.getByText('テストレース1').click()
    await page.getByRole('button', { name: /特徴量/ }).click()
    // oddsチップをクリック
    await page.getByRole('button', { name: /^odds/ }).click()
    // odds列が消える
    await expect(page.getByRole('columnheader', { name: 'odds_win' })).not.toBeVisible({ timeout: 3000 })
    // 再度クリックで復元
    await page.getByRole('button', { name: /^odds/ }).click()
    await expect(page.getByRole('columnheader', { name: 'odds_win' })).toBeVisible({ timeout: 3000 })
  })

  test('列名フィルターが機能する', async ({ page }) => {
    await page.goto('/data-view')
    await page.getByText('テストレース1').click()
    await page.getByRole('button', { name: /特徴量/ }).click()
    await page.getByPlaceholder(/絞り込み/).fill('odds')
    await expect(page.getByText(/列を表示/)).toBeVisible({ timeout: 3000 })
  })
})
