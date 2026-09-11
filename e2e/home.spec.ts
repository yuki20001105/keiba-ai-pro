import { test, expect } from '@playwright/test'
import { mockAuth, mockHealth, mockDataStats } from './helpers/mock-api'

test.describe('ホームページ', () => {
  test.beforeEach(async ({ page }) => {
    await mockAuth(page, { role: 'user', tier: 'free' })
    await mockHealth(page)
    await mockDataStats(page)
  })

  test('ランディングページが表示される', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveTitle(/keiba|競馬/i)
    await expect(page.getByText('予測を始める')).toBeVisible()
  })

  test('ランディングページのCTAでホームページへ遷移する', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('link', { name: '予測を始める' }).click()
    await page.waitForURL('/home')
    await expect(page.getByText('AI競馬予測')).toBeVisible()
  })

  test('ホームページに2ステップの主要ナビゲーションだけが表示される', async ({ page }) => {
    await page.goto('/home')
    await expect(page.getByText('基本的な使い方 — 2ステップ')).toBeVisible()
    await expect(page.getByRole('link', { name: /予測実行/ }).first()).toHaveAttribute('href', '/predict-batch')
    await expect(page.getByRole('link', { name: /成績確認/ }).first()).toHaveAttribute('href', '/dashboard')

    for (const href of [
      '/data-collection',
      '/train',
      '/race-analysis',
      '/prediction-history',
      '/production-readiness',
      '/notion-report',
      '/model-redesign-workbench',
    ]) {
      await expect(page.locator(`a[href="${href}"]`)).toHaveCount(0)
    }
    await expect(page.locator('a[href="/admin"]')).toHaveCount(0)
  })

  test('管理者には管理メニューへの入口が表示される', async ({ page }) => {
    await mockAuth(page, { role: 'admin', tier: 'premium' })
    await page.goto('/home')
    await expect(page.getByRole('link', { name: '管理者' })).toHaveAttribute('href', '/admin')
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

  test('「予測を始める」ボタンで一括予測ページへ遷移', async ({ page }) => {
    await page.goto('/home')
    await page.getByRole('link', { name: /予測を始める/ }).click()
    await page.waitForURL('/predict-batch')
  })
})
