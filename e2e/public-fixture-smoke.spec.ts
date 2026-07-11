import { test, expect } from '@playwright/test'

test.describe('Public/Fixture Smoke', () => {
  test('a) ランディング表示', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveTitle(/keiba|競馬/i)
    await expect(page.getByText('予測を始める')).toBeVisible()
  })

  test('b) ログイン画面表示', async ({ page }) => {
    await page.goto('/login')
    await expect(page.locator('form').getByRole('button', { name: 'ログイン' })).toBeVisible()
    await expect(page.locator('input[type="email"]')).toBeVisible()
    await expect(page.locator('input[type="password"]')).toBeVisible()
  })

  test('c) 未認証で保護ページへアクセスすると /login へ遷移', async ({ page }) => {
    await page.goto('/home')
    await page.waitForURL('**/login')
    await expect(page.locator('input[type="email"]')).toBeVisible()
  })

  test('d) API route mock で代表画面の成功状態を表示（signup）', async ({ page }) => {
    await page.route('**/auth/v1/signup**', route =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          user: { id: 'ci-user-1', email: 'ci@example.com' },
          session: null,
        }),
      })
    )

    await page.goto('/login')
    await page.getByRole('button', { name: '新規登録' }).click()
    await page.locator('input[type="email"]').fill('ci@example.com')
    await page.locator('input[type="password"]').fill('password123')
    await page.getByRole('button', { name: '登録する' }).click()

    await expect(page.getByText('確認メールを送信しました。メールを確認してください。')).toBeVisible()
  })

  test('e) API 500 時のエラー表示（signup）', async ({ page }) => {
    await page.route('**/auth/v1/signup**', route =>
      route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({
          error: 'server_error',
          error_description: 'upstream failed',
          msg: 'upstream failed',
        }),
      })
    )

    await page.goto('/login')
    await page.getByRole('button', { name: '新規登録' }).click()
    await page.locator('input[type="email"]').fill('ci@example.com')
    await page.locator('input[type="password"]').fill('password123')
    await page.getByRole('button', { name: '登録する' }).click()

    await expect(page.getByText(/upstream failed|server_error|500/i)).toBeVisible()
  })
})
