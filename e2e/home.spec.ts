import { test, expect } from '@playwright/test'
import { mockAuth, mockHealth, mockDataStats } from './helpers/mock-api'

test.describe('ホームページ', () => {
  test.beforeEach(async ({ page }) => {
    await mockAuth(page)
    await mockHealth(page)
    await mockDataStats(page)
  })

  test('ランディングページが表示される', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveTitle(/keiba|競馬/i)
    await expect(page.getByText('予測を始める')).toBeVisible()
  })

  test('「アプリへ」でホームページへ遷移する', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('link', { name: 'アプリへ' }).click()
    await page.waitForURL('/home')
    await expect(page.getByText('AI競馬予測')).toBeVisible()
  })

  test('ホームページに4ステップのナビゲーションカードが表示される', async ({ page }) => {
    await page.goto('/home')
    await expect(page.getByText('データ取得')).toBeVisible()
    await expect(page.getByText('モデル学習')).toBeVisible()
    await expect(page.getByText('予測実行')).toBeVisible()
    await expect(page.getByText('成績確認')).toBeVisible()
    await expect(page.getByText('予測スコア詳細')).toBeVisible()
  })

  test('システムステータスカードが3つ表示される', async ({ page }) => {
    await page.goto('/home')
    await expect(page.getByText('API')).toBeVisible()
    await expect(page.getByText('レース数')).toBeVisible()
    await expect(page.getByText('モデル数')).toBeVisible()
  })

  test('APIオンライン時は緑色のステータスが表示される', async ({ page }) => {
    await page.goto('/home')
    await expect(page.getByText('オンライン')).toBeVisible({ timeout: 6000 })
  })

  test('APIオフライン時は赤色のステータスが表示される', async ({ page }) => {
    // health をオフラインに上書き
    await page.route('/api/health**', route => route.fulfill({ status: 503, json: { status: 'offline' } }))
    await page.goto('/home')
    await expect(page.getByText('オフライン')).toBeVisible({ timeout: 6000 })
  })

  test('データ統計が表示される', async ({ page }) => {
    await page.goto('/home')
    await expect(page.getByText('12,345')).toBeVisible({ timeout: 6000 })
    // モデル数カード内の数値
    const modelCard = page.locator('div').filter({ hasText: /^モデル数$/ }).first()
    await expect(modelCard.locator('..').getByText('3')).toBeVisible({ timeout: 6000 })
  })

  test('Step 01から始めるボタンでデータ取得ページへ遷移', async ({ page }) => {
    await page.goto('/home')
    await page.getByRole('link', { name: /Step 01 から始める/ }).click()
    await page.waitForURL('/data-collection')
  })
})
