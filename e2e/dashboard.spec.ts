import { test, expect } from '@playwright/test'
import { mockAuth, mockDataStats } from './helpers/mock-api'

async function mockDashboardApis(page: import('@playwright/test').Page) {
  await mockAuth(page)
  await mockDataStats(page)
  await page.route('/api/purchase-history**', route => {
    if (route.request().method() === 'GET') {
      return route.fulfill({
        json: {
          success: true,
          history: [
            { id: 'bet-001', race_id: '202604070101', race_name: 'テストレース1', bet_type: '単勝', amount: 1000, total_cost: 1000, actual_return: null, is_hit: null, purchased_at: '2026-04-07T10:00:00Z' },
          ],
          count: 1,
        },
      })
    }
    return route.fulfill({ json: { success: true } })
  })
  await page.route('/api/statistics**', route =>
    route.fulfill({ json: { statistics: { by_bet_type: [{ bet_type: '単勝', count: 1, hit_count: 0, recovery_rate: 0, hit_rate: 0 }] } } })
  )
}

test.describe('ダッシュボード', () => {
  test.beforeEach(async ({ page }) => {
    await mockDashboardApis(page)
  })

  test('ページが正常に表示される', async ({ page }) => {
    await page.goto('/dashboard')
    await expect(page.getByText('ダッシュボード')).toBeVisible()
  })

  test('購入履歴テーブルが表示される', async ({ page }) => {
    await page.goto('/dashboard')
    await expect(page.getByRole('heading', { name: /購入履歴/ })).toBeVisible({ timeout: 5000 })
  })

  test('購入履歴の「結果入力」ボタンが表示される', async ({ page }) => {
    await page.goto('/dashboard')
    await expect(page.getByRole('button', { name: '結果入力' }).first()).toBeVisible({ timeout: 5000 })
  })

  test('「結果入力」をクリックするとインラインフォームが展開される', async ({ page }) => {
    await page.goto('/dashboard')
    await page.getByRole('button', { name: '結果入力' }).first().click()
    // 払い戻し金額の入力欄が出る
    await expect(page.getByPlaceholder('0')).toBeVisible({ timeout: 3000 })
  })

  test('払い戻し額を入力して保存できる', async ({ page }) => {
    await page.route('/api/purchase/bet-001**', route => {
      if (route.request().method() === 'PATCH') {
        return route.fulfill({ json: { success: true, message: '結果を更新しました' } })
      }
      return route.continue()
    })

    await page.goto('/dashboard')
    await page.getByRole('button', { name: '結果入力' }).first().click()
    const returnInput = page.getByPlaceholder('0')
    await returnInput.fill('3200')
    await page.getByRole('button', { name: /保存|確定|更新/ }).first().click()
    await expect(page.getByText(/記録しました|更新しました|保存しました/)).toBeVisible({ timeout: 5000 })
  })

  test('購入履歴がない場合は空状態メッセージが表示される', async ({ page }) => {
    await page.route('/api/purchase-history**', route =>
      route.request().method() === 'GET'
        ? route.fulfill({ json: { success: true, history: [], count: 0 } })
        : route.continue()
    )
    await page.goto('/dashboard')
    await expect(page.getByText(/購入履歴がまだありません|履歴がない/)).toBeVisible({ timeout: 5000 })
  })

  test('チャートセクションが表示される', async ({ page }) => {
    await page.goto('/dashboard')
    // サマリーカード（的中率など）が見える
    await expect(page.getByText(/的中率|回収率|購入回数/).first()).toBeVisible({ timeout: 5000 })
  })
})
