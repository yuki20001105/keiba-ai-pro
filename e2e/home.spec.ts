import { test, expect } from '@playwright/test'
import { mockAuth, mockHealth, mockDataStats } from './helpers/mock-api'
import { buildTestSession } from './helpers/supabase-session'

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
    await expect(page.getByRole('button', { name: '管理者モード' })).toHaveCount(0)
    await expect(page.getByText('ユーザー管理')).toHaveCount(0)
  })

  test('一般ユーザーは管理ツールのURLを直接開いてもホームへ戻る', async ({ page }) => {
    for (const path of ['/data-collection', '/train', '/production-readiness']) {
      await page.goto(path)
      await expect(page).toHaveURL(/\/home$/)
      await expect(page.getByRole('heading', { name: 'AI競馬予測', exact: true })).toBeVisible()
    }
  })

  test('管理者はパスワード確認後に同じホームで管理モードへ切り替えられる', async ({ page }) => {
    await mockAuth(page, { role: 'admin', tier: 'premium' })
    const session = buildTestSession({ role: 'admin', tier: 'premium' })
    let unlockPostObserved = false
    await page.route('**/auth/v1/token**', route => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(session),
    }))
    await page.route('/api/admin/unlock**', route => {
      const request = route.request()
      if (request.method() === 'GET') {
        return route.fulfill({ status: 403, json: { detail: 'Admin mode is locked' } })
      }
      if (request.method() === 'POST') {
        unlockPostObserved = true
        expect(request.headers().authorization).toBe(`Bearer ${session.access_token}`)
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          headers: { 'Set-Cookie': 'keiba_admin_mode=e2e-signed-grant; Path=/; HttpOnly; SameSite=Strict' },
          body: JSON.stringify({
            version: 1,
            unlocked: true,
            expires_at: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
          }),
        })
      }
      return route.fulfill({ status: 200, json: { version: 1, unlocked: false } })
    })
    await page.route('/api/admin/profiles**', route => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        version: 1,
        profiles: [{
          id: 'e2e-user-id',
          email: 'e2e@example.com',
          role: 'admin',
          full_name: 'E2E Admin',
          subscription_tier: 'premium',
          created_at: '2026-01-01T00:00:00Z',
        }],
      }),
    }))

    await page.goto('/home')
    await expect(page.getByRole('button', { name: '管理者モード' })).toBeVisible()
    await expect(page.getByText('ユーザー管理')).toHaveCount(0)

    await page.getByRole('button', { name: '管理者モード' }).click()
    await page.getByRole('textbox', { name: '管理者パスワード', exact: true }).fill('verified-password')
    await page.getByRole('button', { name: '確認して切り替える' }).click()

    await expect(page).toHaveURL(/\/home$/)
    await expect(page.getByRole('heading', { name: '管理者モード' })).toBeVisible()
    expect(unlockPostObserved).toBe(true)
    await expect(page.getByRole('heading', { name: 'ユーザー管理', exact: true })).toBeVisible()
    expect((await page.context().cookies()).find(cookie => cookie.name === 'keiba_admin_mode')?.httpOnly).toBe(true)
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
