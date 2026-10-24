import { expect, test, type Page } from '@playwright/test'
import { mockAuth, mockDataStats } from './helpers/mock-api'

const JOB = '55555555-5555-4555-8555-555555555555'
const OP = '66666666-6666-4666-8666-666666666666'
const KEY = 'keiba-ai-pro:server-scrape-batch:v1:e2e-user-id'
const ARCHIVE = `keiba-ai-pro:server-scrape-retirement:v1:e2e-user-id:${JOB}`
const ORIGINAL = {
  version: 1, ownerUserId: 'e2e-user-id', jobId: JOB, operationId: OP,
  startDate: '20160101', endDate: '20260930', forceRescrape: false,
  createdAt: '2026-10-12T07:00:00Z', origin: 'submitted', raceScope: 'all',
}
const CANONICAL = { start_date: '20160101', end_date: '20260930', force_rescrape: false, dry_run: false, server_batch: true }
const RECEIPT = { job_id: JOB, operation_id: OP, status: 'cancelled', retired: true,
  replacement_allowed: true, retirement_supported: true, request_payload: CANONICAL, progress: {} }
const ABSENT = { job_id: JOB, status: 'not_found', retirement_supported: true, progress: {} }

async function setup(page: Page) {
  await page.route('**/api/**', route => route.fulfill({ status: 503, json: { detail: 'unmocked-test-api' } }))
  await mockAuth(page)
  await mockDataStats(page)
  for (const [endpoint, status] of [['health', 'ok'], ['scrape/health', 'healthy']]) {
    await page.route(`**/api/${endpoint}`, route => route.fulfill({ json: {
      status, scrape_capabilities: { race_scopes: ['all', 'jra'], jra_month_reuse: true },
    } }))
  }
  await page.route('**/api/scrape/history**', route => route.fulfill({ json: { count: 0, jobs: [] } }))
  await page.addInitScript(({ key, value }) => {
    if (!sessionStorage.getItem('select-again-seeded')) {
      if (!localStorage.getItem(key)) localStorage.setItem(key, JSON.stringify(value))
      sessionStorage.setItem('select-again-seeded', '1')
    }
  }, { key: KEY, value: ORIGINAL })
}

const saved = (page: Page) => page.evaluate(key => JSON.parse(localStorage.getItem(key) || 'null'), KEY)

test.describe('古い依頼の安全な整理と対象の選び直し', () => {
  test('自動再接続中は取得開始と区別し、未受付の進捗や件数を表示しない', async ({ page }) => {
    await setup(page)
    let release!: () => void
    const gate = new Promise<void>(resolve => { release = resolve })
    let starts = 0
    page.on('request', request => { if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/scrape') starts++ })
    await page.route('**/api/scrape/status/**', async route => {
      await gate
      await route.fulfill({ json: ABSENT }).catch(() => {})
    })
    await page.goto('/data-collection')
    await expect(page.getByTestId('batch-status-panel')).toContainText('前回の依頼を確認中・取得は開始していません')
    await expect(page.getByTestId('scrape-progress-counters')).toHaveCount(0)
    await expect(page.getByRole('progressbar', { name: '取得進捗' })).toHaveCount(0)
    await expect(page.getByTestId('scrape-race-scope')).toBeDisabled()
    expect(starts).toBe(0)
    release()
    await expect(page.getByTestId('retire-batch-button')).toBeVisible()
    expect(starts).toBe(0)
  })

  test('旧全場依頼を整理後に中央のみを選べて、取得は明示開始の一度だけ', async ({ page }, testInfo) => {
    await setup(page)
    const starts: Record<string, unknown>[] = []
    const retirements: unknown[] = []
    let retired = false
    await page.route('**/api/scrape/status/**', route => {
      const id = new URL(route.request().url()).pathname.split('/').pop()
      return route.fulfill({ json: id === JOB ? retired ? RECEIPT : ABSENT
        : { job_id: id, status: 'completed', result: { races_collected: 2, saved_horses: 20 } } })
    })
    await page.route(`**/api/scrape/retire/${JOB}`, route => {
      retirements.push(route.request().postDataJSON()); retired = true
      return route.fulfill({ json: RECEIPT })
    })
    await page.route('**/api/scrape', route => {
      const body = route.request().postDataJSON(); starts.push(body)
      return route.fulfill({ json: { job_id: body.job_id, operation_id: body.operation_id, status: 'queued' } })
    })
    await page.goto('/data-collection')
    await expect(page.getByTestId('retire-batch-button')).toBeVisible()
    await expect(page.getByTestId('scrape-race-scope')).toHaveValue('all')
    await expect(page.getByTestId('scrape-race-scope')).toBeDisabled()
    expect(starts).toHaveLength(0); expect(retirements).toHaveLength(0)
    await page.screenshot({ path: testInfo.outputPath('before-select-again.png'), fullPage: true })
    page.once('dialog', async dialog => {
      expect(dialog.message()).toContain('保存済みデータは削除しません')
      expect(dialog.message()).toContain('新しい取得も開始しません')
      await dialog.accept()
    })
    await page.getByTestId('retire-batch-button').click()
    await expect(page.getByTestId('scrape-race-scope')).toBeEnabled()
    await expect(page.getByTestId('scrape-race-scope')).toHaveValue('jra')
    await expect(page.getByTestId('start-period-input')).toHaveValue('2016-01')
    await expect(page.getByTestId('end-period-input')).toHaveValue('2026-09')
    await expect(page.getByTestId('execute-button')).toBeEnabled()
    expect(await saved(page)).toBeNull()
    const archive = await page.evaluate(key => JSON.parse(localStorage.getItem(key) || 'null'), ARCHIVE)
    expect(archive.request).toEqual(ORIGINAL)
    expect(archive.receipt).toMatchObject({ job_id: JOB, operation_id: OP, retired: true })
    expect(starts).toHaveLength(0)
    expect(retirements).toEqual([{ operation_id: OP, start_date: '20160101', end_date: '20260930',
      force_rescrape: false, server_batch: true, race_scope: 'all' }])
    await page.getByTestId('scrape-race-scope').selectOption('all')
    await expect(page.getByTestId('scrape-race-scope')).toHaveValue('all')
    await page.getByTestId('scrape-race-scope').selectOption('jra')
    await page.screenshot({ path: testInfo.outputPath('ready-to-select.png'), fullPage: true })
    page.once('dialog', dialog => dialog.accept())
    await page.getByTestId('execute-button').click()
    await expect(page.getByTestId('batch-status-panel')).toContainText('完了 · 2レース')
    expect(starts).toHaveLength(1)
    expect(starts[0]).toMatchObject({ start_date: '20160101', end_date: '20260930', race_scope: 'jra', server_batch: true, force_rescrape: false })
    expect(starts[0].job_id).not.toBe(JOB); expect(starts[0].operation_id).not.toBe(OP)
  })

  test('整理確認を取り消した場合は原依頼を保持する', async ({ page }) => {
    await setup(page)
    let retireCount = 0
    await page.route('**/api/scrape/status/**', route => route.fulfill({ json: ABSENT }))
    await page.route(`**/api/scrape/retire/${JOB}`, route => { retireCount++; return route.fulfill({ json: RECEIPT }) })
    await page.goto('/data-collection')
    await expect(page.getByTestId('retire-batch-button')).toBeVisible()
    page.once('dialog', dialog => dialog.dismiss())
    await page.getByTestId('retire-batch-button').click()
    await expect(page.getByTestId('retire-batch-button')).toBeEnabled()
    await expect(page.getByTestId('scrape-race-scope')).toBeDisabled()
    expect(await saved(page)).toEqual(ORIGINAL); expect(retireCount).toBe(0)
  })

  test('取消記録の応答が失われても解除せず、再確認の厳密な記録から復帰する', async ({ page }) => {
    await setup(page)
    let retired = false
    let starts = 0
    page.on('request', request => { if (new URL(request.url()).pathname === '/api/scrape' && request.method() === 'POST') starts++ })
    await page.route('**/api/scrape/status/**', route => route.fulfill({ json: retired ? RECEIPT : ABSENT }))
    await page.route(`**/api/scrape/retire/${JOB}`, route => { retired = true; return route.fulfill({ status: 502, json: { detail: 'receipt lost' } }) })
    await page.goto('/data-collection')
    await expect(page.getByTestId('retire-batch-button')).toBeVisible()
    page.once('dialog', dialog => dialog.accept())
    await page.getByTestId('retire-batch-button').click()
    await expect(page.getByTestId('batch-recovery-notice')).toContainText('元の依頼は保持')
    expect(await saved(page)).toEqual(ORIGINAL)
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    await page.getByTestId('reconnect-batch-button').click()
    await expect(page.getByTestId('scrape-race-scope')).toBeEnabled()
    await expect(page.getByTestId('scrape-race-scope')).toHaveValue('jra')
    expect(await saved(page)).toBeNull(); expect(starts).toBe(0)
  })

  test('整理直前に受付済みと判明したら取消や新規取得はせず元ジョブを監視する', async ({ page }) => {
    await setup(page)
    let accepted = false
    let retireCount = 0
    await page.route('**/api/scrape/status/**', route => route.fulfill({ json: accepted
      ? { job_id: JOB, status: 'running', request_payload: CANONICAL, progress: { done: 1, total: 10, message: '実行中' } }
      : ABSENT }))
    await page.route(`**/api/scrape/retire/${JOB}`, route => { retireCount++; return route.fulfill({ json: RECEIPT }) })
    await page.goto('/data-collection')
    await expect(page.getByTestId('retire-batch-button')).toBeVisible()
    accepted = true
    await page.getByTestId('retire-batch-button').click()
    await expect(page.getByTestId('batch-recovery-notice')).toContainText('受付記録が確認されました')
    expect(await saved(page)).toEqual(ORIGINAL); expect(retireCount).toBe(0)
    await page.getByTestId('reconnect-batch-button').click()
    await expect(page.getByTestId('batch-status-panel')).toContainText('取得実行中')
    await expect(page.getByTestId('scrape-race-scope')).toBeDisabled()
  })

  test('取り消し応答の元対象・ID・期間が一致しなければ選び直しを許可しない', async ({ page }) => {
    await setup(page)
    await page.route('**/api/scrape/status/**', route => route.fulfill({ json: ABSENT }))
    await page.route(`**/api/scrape/retire/${JOB}`, route => route.fulfill({ json: {
      ...RECEIPT, request_payload: { ...CANONICAL, race_scope: 'jra' },
    } }))
    await page.goto('/data-collection')
    await expect(page.getByTestId('retire-batch-button')).toBeVisible()
    page.once('dialog', dialog => dialog.accept())
    await page.getByTestId('retire-batch-button').click()
    await expect(page.getByTestId('batch-recovery-notice')).toContainText('元の依頼を照合できません')
    expect(await saved(page)).toEqual(ORIGINAL)
    await expect(page.getByTestId('scrape-race-scope')).toBeDisabled()
  })

  test('整理APIが未反映のサーバーでは選び直しを安全側で停止する', async ({ page }) => {
    await setup(page)
    await page.route('**/api/scrape/status/**', route => route.fulfill({ json: { ...ABSENT, retirement_supported: false } }))
    await page.goto('/data-collection')
    await expect(page.getByTestId('scrape-retirement-server-pending')).toBeVisible()
    await expect(page.getByTestId('retire-batch-button')).toHaveCount(0)
    await expect(page.getByTestId('scrape-race-scope')).toBeDisabled()
    expect(await saved(page)).toEqual(ORIGINAL)
  })

  test('別タブの整理と新依頼を再読込しても旧全場依頼を再送しない', async ({ page, context }) => {
    await setup(page)
    let retired = false
    let posts = 0
    await page.route('**/api/scrape/status/**', route => route.fulfill({ json: retired ? RECEIPT : ABSENT }))
    await page.route(`**/api/scrape/retire/${JOB}`, route => { retired = true; return route.fulfill({ json: RECEIPT }) })
    await page.goto('/data-collection')
    await expect(page.getByTestId('retire-batch-button')).toBeVisible()
    const other = await context.newPage()
    await setup(other)
    other.on('request', request => { if (new URL(request.url()).pathname === '/api/scrape' && request.method() === 'POST') posts++ })
    await other.route('**/api/scrape/status/**', route => {
      const id = new URL(route.request().url()).pathname.split('/').pop()
      return route.fulfill({ json: id === JOB ? retired ? RECEIPT : ABSENT
        : { job_id: id, status: 'running', progress: { done: 0, total: 10, message: '中央限定を取得中' } } })
    })
    await other.goto('/data-collection')
    await expect(other.getByTestId('retire-batch-button')).toBeVisible()
    page.once('dialog', dialog => dialog.accept())
    await page.getByTestId('retire-batch-button').click()
    await expect(page.getByTestId('scrape-race-scope')).toBeEnabled()
    const replacement = { ...ORIGINAL, jobId: '77777777-7777-4777-8777-777777777777',
      operationId: '88888888-8888-4888-8888-888888888888', raceScope: 'jra' }
    await page.evaluate(({ key, value }) => localStorage.setItem(key, JSON.stringify(value)), { key: KEY, value: replacement })
    await other.reload()
    await expect(other.getByTestId('batch-status-panel')).toContainText('中央限定を取得中')
    await expect(other.getByTestId('scrape-race-scope')).toHaveValue('jra')
    expect(await saved(other)).toEqual(replacement); expect(posts).toBe(0)
  })
})
