import { expect, test, type Page } from '@playwright/test'
import { mockAuth, mockDataStats } from './helpers/mock-api'

const JOB_ID = '33333333-3333-4333-8333-333333333333'
const OPERATION_ID = '44444444-4444-4444-8444-444444444444'
const STORAGE_KEY = 'keiba-ai-pro:server-scrape-batch:v1:e2e-user-id'
const savedRequest = {
  version: 1, ownerUserId: 'e2e-user-id', jobId: JOB_ID, operationId: OPERATION_ID,
  startDate: '20160101', endDate: '20260930', forceRescrape: false,
  createdAt: '2026-10-12T07:00:00Z', origin: 'submitted', raceScope: 'jra',
}

async function setup(page: Page) {
  // Never fall through to real scraping endpoints or an external provider.
  await page.route('**/api/**', route => route.fulfill({ status: 503, json: { detail: 'unmocked-test-api' } }))
  await mockAuth(page)
  await mockDataStats(page)
  await page.route('**/api/health**', route => route.fulfill({ json: {
    status: 'ok', scrape_capabilities: { race_scopes: ['all', 'jra'], jra_month_reuse: true },
  } }))
  await page.route('**/api/scrape/health**', route => route.fulfill({ json: {
    status: 'healthy', scrape_capabilities: { race_scopes: ['all', 'jra'], jra_month_reuse: true },
  } }))
  await page.route('**/api/scrape/history**', route => route.fulfill({ json: { count: 0, jobs: [] } }))
  await page.addInitScript(({ key, request }) => {
    if (!sessionStorage.getItem('recovery-seeded')) {
      localStorage.setItem(key, JSON.stringify(request))
      sessionStorage.setItem('recovery-seeded', '1')
    }
  }, { key: STORAGE_KEY, request: savedRequest })
}

test.describe('保存済み取得依頼の安全な復旧', () => {
  test('API正常＋受付記録なしでも自動再送せず、更新後も同じ依頼を保持する', async ({ page }, testInfo) => {
    await setup(page)
    let posts = 0
    page.on('request', request => { if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/scrape') posts++ })
    await page.route('**/api/scrape/status/**', route => route.fulfill({ json: {
      job_id: JOB_ID, status: 'not_found', progress: {}, result: null,
    } }))
    await page.goto('/data-collection')
    await expect(page.getByTestId('batch-recovery-notice')).toBeVisible()
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    await expect(page.getByTestId('reconnect-batch-button')).toBeVisible()
    await expect(page.getByTestId('resubmit-batch-button')).not.toBeVisible()
    await page.getByTestId('batch-recovery-details').locator('summary').click()
    await expect(page.getByTestId('batch-recovery-details')).toContainText('受付記録が見つかりません')
    await expect(page.getByTestId('batch-recovery-details')).toContainText(JOB_ID)
    await expect(page.getByTestId('batch-recovery-details')).toContainText('中央競馬のみ')
    await expect(page.getByTestId('batch-recovery-details')).toContainText('2016/01～2026/09')
    await page.getByTestId('batch-recovery-details').locator('summary').click()
    await page.screenshot({ path: testInfo.outputPath('simple-recovery-desktop.png'), fullPage: true })
    await page.reload()
    await expect(page.getByTestId('batch-recovery-notice')).toBeVisible()
    expect(await page.evaluate(key => JSON.parse(localStorage.getItem(key) || 'null'), STORAGE_KEY)).toEqual(savedRequest)
    expect(posts).toBe(0)
  })

  test('確認サービスの障害から再確認で復帰し、新しい取得は開始しない', async ({ page }) => {
    await setup(page)
    let recovered = false
    let posts = 0
    page.on('request', request => { if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/scrape') posts++ })
    await page.route('**/api/scrape/status/**', route => recovered
      ? route.fulfill({ json: { job_id: JOB_ID, status: 'completed', result: { races_collected: 3, saved_horses: 42 } } })
      : route.fulfill({ status: 503, json: { detail: 'temporarily unavailable' } }))
    await page.goto('/data-collection')
    await expect(page.getByTestId('batch-recovery-notice')).toBeVisible()
    await page.getByTestId('batch-recovery-details').locator('summary').click()
    await expect(page.getByTestId('batch-recovery-details')).toContainText('確認サービスに接続できません')
    await expect(page.getByTestId('batch-recovery-details')).not.toContainText('受付記録が見つかりません')
    recovered = true
    await page.getByTestId('reconnect-batch-button').click()
    await expect(page.getByTestId('batch-status-panel')).toContainText('完了 · 3レース')
    expect(await page.evaluate(key => localStorage.getItem(key), STORAGE_KEY)).toBeNull()
    expect(posts).toBe(0)
  })

  test('権限確認の失敗は通信障害と分け、モバイルでも再確認できる', async ({ page }, testInfo) => {
    await setup(page)
    await page.setViewportSize({ width: 390, height: 844 })
    await page.route('**/api/scrape/status/**', route => route.fulfill({ status: 403, json: { detail: 'Admin role required' } }))
    await page.goto('/data-collection')
    await expect(page.getByRole('heading', { name: 'ログイン・権限の確認が必要です' })).toBeVisible()
    await expect(page.getByTestId('reconnect-batch-button')).toBeVisible()
    await expect(page.getByTestId('resubmit-batch-button')).not.toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath('simple-recovery-mobile.png'), fullPage: true })
  })

  test('明示再送は未受付の再確認と確認ダイアログ後に同じID・期間・対象を一度だけ送る', async ({ page }) => {
    await setup(page)
    const posts: unknown[] = []
    let accepted = false
    await page.route('**/api/scrape/status/**', route => route.fulfill({ json: accepted
      ? { job_id: JOB_ID, status: 'completed', result: { races_collected: 0 } }
      : { job_id: JOB_ID, status: 'not_found', progress: {} } }))
    await page.route('**/api/scrape', route => {
      posts.push(route.request().postDataJSON())
      accepted = true
      return route.fulfill({ json: { job_id: JOB_ID, operation_id: OPERATION_ID, status: 'queued' } })
    })
    await page.goto('/data-collection')
    await expect(page.getByTestId('batch-recovery-notice')).toBeVisible()
    await page.getByTestId('batch-recovery-details').locator('summary').click()
    page.once('dialog', dialog => dialog.dismiss())
    await page.getByTestId('resubmit-batch-button').click()
    await expect(page.getByTestId('resubmit-batch-button')).toBeEnabled()
    expect(posts).toHaveLength(0)
    page.once('dialog', dialog => dialog.accept())
    await page.getByTestId('resubmit-batch-button').click()
    await expect(page.getByTestId('batch-status-panel')).toContainText('完了 · 0レース')
    expect(posts).toEqual([{
      start_date: '20160101', end_date: '20260930', force_rescrape: false,
      server_batch: true, job_id: JOB_ID, operation_id: OPERATION_ID, race_scope: 'jra',
    }])
  })

  test('別ジョブの応答でロックや保存依頼を解除しない', async ({ page }) => {
    await setup(page)
    await page.route('**/api/scrape/status/**', route => route.fulfill({ json: {
      job_id: OPERATION_ID, status: 'completed', result: { races_collected: 100 },
    } }))
    await page.goto('/data-collection')
    await expect(page.getByTestId('batch-recovery-notice')).toBeVisible()
    await page.getByTestId('batch-recovery-details').locator('summary').click()
    await expect(page.getByTestId('batch-recovery-details')).toContainText('応答が一致しません')
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    expect(await page.evaluate(key => JSON.parse(localStorage.getItem(key) || 'null'), STORAGE_KEY)).toEqual(savedRequest)
  })
})
