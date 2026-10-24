import { test, expect } from '@playwright/test'
import { mockAuth, mockDataStats, mockModels } from './helpers/mock-api'

const flows = [
  { label: 'データ取得', href: '/data-collection', heading: '期間' },
  { label: 'モデル作成', href: '/train', heading: '学習設定' },
  { label: '予測実行', href: '/predict-batch', heading: '① 日付・モデル設定' },
  { label: '成績確認', href: '/dashboard', heading: null },
  { label: 'ユーザー管理', href: '/user-management', heading: 'ユーザー管理' },
] as const

test.describe('リリース用５機能導線・読み取りfixture', () => {
  for (const flow of flows) {
    test(`ホームから${flow.label}へ移動できる`, async ({ page }) => {
      const unexpectedApiRequests: string[] = []
      // A missing fixture must fail locally rather than trigger acquisition,
      // learning, ledger writes, or contact the running Python service.
      await page.route('/api/**', route => {
        unexpectedApiRequests.push(`${route.request().method()} ${new URL(route.request().url()).pathname}`)
        return route.fulfill({ status: 503, json: { detail: 'Unexpected read-only release fixture API' } })
      })
      await mockAuth(page)
      await mockDataStats(page)
      await mockModels(page)
      await page.route('/api/scrape/health**', route => route.fulfill({ json: { status: 'healthy' } }))
      await page.route('/api/scrape/history**', route => route.fulfill({ json: { jobs: [], count: 0 } }))
      await page.route('/api/ml/train/capability**', route => route.fulfill({ json: { enabled: true, mode: 'local', reason: null } }))
      await page.route('/api/performance**', route => route.fulfill({ json: {
        records: [], total_count: 0, summary: {
          settled_cost: 0, settled_return: 0, profit: 0, roi: null,
          settled_races: 0, hit_races: 0, pending_races: 0, pending_cost: 0, skipped_races: 0,
        },
      } }))
      await page.route('/api/admin/profiles**', route => route.fulfill({ json: {
        version: 1, profiles: [{
          id: 'release-fixture-user', email: 'release-fixture@example.test', role: 'user',
          full_name: '検証ユーザー', subscription_tier: 'free', created_at: '2026-10-01T00:00:00Z',
        }],
      } }))

      await page.goto('/home')
      for (const entry of flows) {
        await expect(page.getByRole('link', { name: new RegExp(entry.label) })).toHaveAttribute('href', entry.href)
      }
      await page.getByRole('link', { name: new RegExp(flow.label) }).click()
      await expect(page).toHaveURL(new RegExp(`${flow.href}$`))
      if (flow.heading) {
        await expect(page.getByRole('heading', { name: flow.heading, exact: true })).toBeVisible()
      } else {
        await expect(page.getByRole('tab', { name: 'AI参考' })).toBeVisible()
        await expect(page.getByText('まだ成績がありません')).toBeVisible()
      }
      if (flow.href === '/user-management') {
        await expect(page.getByText('release-fixture@example.test', { exact: true })).toBeVisible()
        await expect(page.getByRole('button', { name: /権限変更|管理者にする/ })).toHaveCount(0)
      }
      // Next's development route announcer is an unrelated, normally empty
      // alert outside the application content.
      await expect(page.locator('main').getByRole('alert')).toHaveCount(0)
      expect(unexpectedApiRequests).toEqual([])
    })
  }
})
