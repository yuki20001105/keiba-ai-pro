import { test, expect } from '@playwright/test'
import { mockAuth } from './helpers/mock-api'

test.describe('ホームページ', () => {
  test.beforeEach(async ({ page }) => {
    await mockAuth(page, { role: 'user', tier: 'free' })
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

  test('一般ユーザーには予測と成績の2機能だけが表示される', async ({ page }) => {
    await page.goto('/home')
    await expect(page.getByText('利用できる機能 — 2件')).toBeVisible()
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
    for (const path of ['/data-collection', '/train', '/user-management', '/production-readiness']) {
      await page.goto(path)
      await expect(page).toHaveURL(/\/home$/)
      await expect(page.getByRole('heading', { name: 'AI競馬予測', exact: true })).toBeVisible()
    }
  })

  test('管理者には切り替えなしで指定された5機能だけが表示される', async ({ page }) => {
    await mockAuth(page, { role: 'admin', tier: 'premium' })

    await page.goto('/home')
    await expect(page.getByText('基本的な使い方 — 5ステップ')).toBeVisible()
    const expectedLinks = [
      ['データ取得', '/data-collection'],
      ['モデル作成', '/train'],
      ['予測実行', '/predict-batch'],
      ['成績確認', '/dashboard'],
      ['ユーザー管理', '/user-management'],
    ] as const
    for (const [name, href] of expectedLinks) {
      await expect(page.getByRole('link', { name: new RegExp(name) })).toHaveAttribute('href', href)
    }
    await expect(page.getByRole('button', { name: '管理者モード' })).toHaveCount(0)
    await expect(page.getByLabel('管理者パスワード')).toHaveCount(0)
    await expect(page.getByText('本番前チェック')).toHaveCount(0)
  })

  test('管理者はユーザー管理を閲覧できるがロールは変更できない', async ({ page }) => {
    await mockAuth(page, { role: 'admin', tier: 'premium' })
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

    await page.goto('/user-management')
    await expect(page.getByRole('heading', { name: 'ユーザー管理' })).toBeVisible()
    await expect(page.getByText('e2e@example.com')).toBeVisible()
    await expect(page.getByRole('combobox')).toHaveCount(0)
    await expect(page.getByText('操作')).toHaveCount(0)
  })
})
