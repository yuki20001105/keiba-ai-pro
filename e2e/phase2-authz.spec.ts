import { test, expect, Page } from '@playwright/test'
import {
  clearSupabaseTestSession,
  mockSupabaseIdentity,
  setSupabaseTestSession,
} from './helpers/mock-api'

async function mockRacesByDate(page: Page) {
  await page.route('/api/races/by-date**', route =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        races: [
          {
            race_id: '202604070101',
            race_name: '認可E2Eテストレース',
            venue: '東京',
            race_no: 1,
            num_horses: 8,
            track_type: '芝',
            distance: 1600,
          },
        ],
      }),
    })
  )
}

test.describe('Phase2 AuthZ E2E', () => {
  test('未認証ユーザーは保護ページで login にリダイレクトされる', async ({ page }) => {
    await clearSupabaseTestSession(page)
    await mockSupabaseIdentity(page, { authenticated: false })
    await page.goto('/home')
    await page.waitForURL('**/login')
    await expect(page.locator('form').getByRole('button', { name: 'ログイン' })).toBeVisible()
  })

  test('Free ユーザーは Premium 機能がロックされる', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    let premiumApiCalled = false
    await page.route('/api/features/**', route => {
      premiumApiCalled = true
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) })
    })

    await setSupabaseTestSession(page, {
      role: 'user',
      tier: 'free',
      appBaseUrl: baseURL,
      supabaseUrl: 'http://127.0.0.1:54321',
    })
    await mockSupabaseIdentity(page, { authenticated: true, role: 'user', tier: 'free' })
    await page.goto('/feature-lab')
    await expect(page.getByRole('heading', { name: '特徴量ラボは Premium 専用です' })).toBeVisible()
    expect(premiumApiCalled).toBe(false)
  })

  test('Premium ユーザーは Premium 機能を利用できる', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await setSupabaseTestSession(page, {
      role: 'user',
      tier: 'premium',
      appBaseUrl: baseURL,
      supabaseUrl: 'http://127.0.0.1:54321',
    })
    await mockSupabaseIdentity(page, { authenticated: true, role: 'user', tier: 'premium' })
    await page.route('/api/features/summary**', route =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ rows: 123, races: 45 }) })
    )
    await page.route('/api/features/importance**', route =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ features: [] }) })
    )
    await page.route('/api/features/coverage**', route =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ coverage: [] }) })
    )

    await page.goto('/feature-lab')
    await expect(page.getByText('特徴量ラボ')).toBeVisible()
    await expect(page.getByText('Premium 専用')).not.toBeVisible()
  })

  test('Admin ユーザーは管理画面を利用できる', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    let observedAuthorization = ''
    await setSupabaseTestSession(page, {
      role: 'admin',
      tier: 'free',
      appBaseUrl: baseURL,
      supabaseUrl: 'http://127.0.0.1:54321',
    })
    await mockSupabaseIdentity(page, { authenticated: true, role: 'admin', tier: 'free' })
    await page.route('/api/data-stats**', route =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ total_races: 100, total_models: 2 }),
      })
    )
    await page.route('/api/scrape**', route => {
      observedAuthorization = route.request().headers()['authorization'] || ''
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true }) })
    })

    await page.goto('/admin')
    await expect(page).toHaveURL(/\/admin$/)
    await expect(page.getByText('Admin 専用')).toBeVisible()
    const status = await page.evaluate(async () => {
      const res = await fetch('/api/scrape', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: 'Bearer e2e-admin-token',
        },
        body: JSON.stringify({ start_date: '20260101', end_date: '20260101', force_rescrape: false, dry_run: true }),
      })
      return res.status
    })
    expect(status).toBe(200)
    expect(observedAuthorization.startsWith('Bearer ')).toBe(true)
  })

  for (const status of [401, 403, 429, 503]) {
    test(`Race Analysis の ${status} エラー表示`, async ({ page, baseURL }) => {
      if (!baseURL) throw new Error('Playwright baseURL is required')
      await setSupabaseTestSession(page, {
        role: 'admin',
        tier: 'premium',
        appBaseUrl: baseURL,
        supabaseUrl: 'http://127.0.0.1:54321',
      })
      await mockSupabaseIdentity(page, { authenticated: true, role: 'admin', tier: 'premium' })
      await mockRacesByDate(page)

      const detail = `E2E-ERROR-${status}`
      await page.route('/api/analyze-race**', route =>
        route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ detail }) })
      )
      await page.route('/api/debug/race/**', route =>
        route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ feature_columns: [], records: [] }) })
      )

      await page.goto('/race-analysis')
      await page.getByText('認可E2Eテストレース').click()
      await expect(page.getByText(detail)).toBeVisible()
    })
  }
})
