import { test, expect } from '@playwright/test'
import { mockAuth, mockRacesByDate, mockPredict } from './helpers/mock-api'

test.describe('予測結果確認ページ（Race Analysis）', () => {
  test.beforeEach(async ({ page }) => {
    await mockAuth(page)
    await mockRacesByDate(page)
    await mockPredict(page)
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

  test('AIの判断タブで予測に寄与した要素を確認できる', async ({ page }) => {
    await page.goto('/race-analysis')
    await page.getByText('テストレース1').click()
    const explainTab = page.getByRole('button', { name: 'AIの判断' })
    await expect(explainTab).toBeVisible({ timeout: 5000 })
    await explainTab.click()
    await expect(page.getByText('騎手の勝率').first()).toBeVisible({ timeout: 5000 })
  })

  test('速度スコアを上げた要素と下げた要素を分けて表示する', async ({ page }) => {
    await page.goto('/race-analysis')
    await page.getByText('テストレース1').click()
    await page.getByRole('button', { name: 'AIの判断' }).click()
    await expect(page.getByText('速度スコアを上げた要素')).toBeVisible()
    await expect(page.getByText('速度スコアを下げた要素')).toBeVisible()
  })

  test('予測上位3頭の説明を切り替えられる', async ({ page }) => {
    await page.goto('/race-analysis')
    await page.getByText('テストレース1').click()
    await page.getByRole('button', { name: 'AIの判断' }).click()
    await page.getByRole('button', { name: /2位.*テスト馬B/ }).click()
    await expect(page.getByText(/テスト馬Bを2位と予測/)).toBeVisible()
  })

  test('AIの判断を開いても旧デバッグ特徴量APIを呼ばない', async ({ page }) => {
    let debugRequestCount = 0
    page.on('request', request => {
      if (request.url().includes('/api/debug/race/')) debugRequestCount += 1
    })
    await page.goto('/race-analysis')
    await page.getByText('テストレース1').click()
    await page.getByRole('button', { name: 'AIの判断' }).click()
    await expect(page.getByTestId('race-explanation-panel')).toBeVisible()
    expect(debugRequestCount).toBe(0)
  })

  test('レースが見つからない場合は空状態メッセージが表示される', async ({ page }) => {
    await page.route('/api/races/by-date**', route =>
      route.fulfill({ json: { races: [] } })
    )
    await page.goto('/race-analysis')
    await expect(page.getByText('レースが見つかりません')).toBeVisible({ timeout: 5000 })
  })
})
