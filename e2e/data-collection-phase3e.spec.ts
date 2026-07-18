import { expect, Page, test } from '@playwright/test'
import {
  UNCERTAINTY_REVIEW_STORAGE_KEY,
  UNCERTAINTY_STORAGE_KEY,
  fingerprintUncertaintyLock,
  type PendingUncertaintyReview,
  type PersistedUncertaintyLock,
} from '../src/lib/scrape-uncertainty-approval'
import { mockSupabaseIdentity, setSupabaseTestSession } from './helpers/mock-api'

const SUPABASE_ORIGIN = 'http://127.0.0.1:54321'
const LOCK: PersistedUncertaintyLock = {
  version: 1,
  failureKind: 'monitoring',
  request: { startPeriod: '2026-01', endPeriod: '2026-02', forceRescrape: false },
  occurredAt: '2026-07-18T00:00:00.000Z',
}

function reviewFor(lock: PersistedUncertaintyLock): PendingUncertaintyReview {
  return {
    version: 1,
    kind: 'jobless-uncertainty-review',
    status: 'pending_review',
    requestId: '11111111-1111-4111-8111-111111111111',
    lockFingerprint: fingerprintUncertaintyLock(lock),
    failureKind: lock.failureKind,
    request: { ...lock.request },
    uncertaintyOccurredAt: lock.occurredAt,
    requestedAt: '2026-07-18T00:01:00.000Z',
    reason: 'サーバー状態が未確認のため、監査担当者による状態確認を依頼します。',
    acknowledgements: { serverStateUnverified: true, noUnlockOrRetry: true },
    authoritative: false,
    executionEnabled: false,
    lockReleaseAllowed: false,
  }
}

async function authorizeAdmin(page: Page, baseURL: string) {
  await setSupabaseTestSession(page, {
    role: 'admin',
    tier: 'premium',
    appBaseUrl: baseURL,
    supabaseUrl: SUPABASE_ORIGIN,
  })
  await mockSupabaseIdentity(page, { authenticated: true, role: 'admin', tier: 'premium' })
  await page.route('**/api/scrape/health**', route => route.fulfill({ status: 200, json: { status: 'healthy' } }))
  await page.route('**/api/data-stats**', route => route.fulfill({ status: 200, json: { total_races: 1, total_horses: 1, latest_date: '2026-07-01' } }))
  await page.route('**/api/scrape/history**', route => route.fulfill({ status: 200, json: { jobs: [] } }))
}

async function seedStorage(page: Page, lock: unknown, review?: unknown) {
  await page.addInitScript(({ lockKey, reviewKey, lockValue, reviewValue }) => {
    localStorage.setItem(lockKey, JSON.stringify(lockValue))
    if (reviewValue !== undefined) localStorage.setItem(reviewKey, JSON.stringify(reviewValue))
  }, {
    lockKey: UNCERTAINTY_STORAGE_KEY,
    reviewKey: UNCERTAINTY_REVIEW_STORAGE_KEY,
    lockValue: lock,
    reviewValue: review,
  })
}

async function openDataCollection(page: Page, baseURL: string) {
  await authorizeAdmin(page, baseURL)
  await page.goto('/data-collection')
}

test.describe('Phase 3E non-executable uncertainty review scaffold', () => {
  let unexpectedExternal: string[] = []
  let unexpectedApi: string[] = []
  let nonGetApiCount = 0
  let scrapePostCount = 0
  let statusGetCount = 0

  test.beforeEach(async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    unexpectedExternal = []
    unexpectedApi = []
    nonGetApiCount = 0
    scrapePostCount = 0
    statusGetCount = 0
    const appOrigin = new URL(baseURL).origin

    page.on('request', request => {
      const url = new URL(request.url())
      if (url.origin !== appOrigin) return
      if (url.pathname.startsWith('/api/') && request.method() !== 'GET') nonGetApiCount += 1
      if (url.pathname === '/api/scrape' && request.method() === 'POST') scrapePostCount += 1
      if (url.pathname.startsWith('/api/scrape/status/') && request.method() === 'GET') statusGetCount += 1
    })

    await page.route('**/*', route => {
      const url = new URL(route.request().url())
      if (url.origin === appOrigin || ['about:', 'blob:', 'data:'].includes(url.protocol)) return route.fallback()
      if (url.origin === SUPABASE_ORIGIN && ['/auth/v1/user', '/rest/v1/profiles'].includes(url.pathname)) {
        return route.fallback()
      }
      unexpectedExternal.push(url.href)
      return route.abort('blockedbyclient')
    })

    await page.route('**/api/**', route => {
      const url = new URL(route.request().url())
      if (url.origin !== appOrigin) return route.fallback()
      unexpectedApi.push(`${route.request().method()} ${url.pathname}${url.search}`)
      return route.fulfill({ status: 599, json: { detail: 'unexpected unmocked API' } })
    })
  })

  test.afterEach(() => {
    expect(unexpectedExternal).toEqual([])
    expect(unexpectedApi).toEqual([])
    expect(nonGetApiCount).toBe(0)
    expect(scrapePostCount).toBe(0)
    expect(statusGetCount).toBe(0)
  })

  test('jobless lock requires a valid reason and both acknowledgements without offering execution', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await seedStorage(page, LOCK)
    await openDataCollection(page, baseURL)

    await expect(page.getByTestId('phase3e-review-form')).toBeVisible()
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    await expect(page.getByTestId('dry-run-button')).toBeDisabled()
    await expect(page.getByTestId('retry-button')).toHaveCount(0)
    await expect(page.getByTestId('reconcile-status-button')).toHaveCount(0)
    await expect(page.getByTestId('quality-bridge-card')).toHaveCount(0)

    const record = page.getByTestId('phase3e-record-review')
    await page.getByTestId('phase3e-review-reason').fill('短い理由')
    await page.getByTestId('phase3e-ack-unverified').check()
    await page.getByTestId('phase3e-ack-no-unlock').check()
    await expect(record).toBeDisabled()
    await page.getByTestId('phase3e-review-reason').fill('サーバー状態が未確認のため、監査担当者による状態確認を依頼します。')
    await expect(record).toBeEnabled()
  })

  test('records pending_review locally while preserving the lock and false safety flags across reload', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await seedStorage(page, LOCK)
    await openDataCollection(page, baseURL)
    const before = await page.evaluate(key => localStorage.getItem(key), UNCERTAINTY_STORAGE_KEY)

    await page.getByTestId('phase3e-review-reason').fill('サーバー状態が未確認のため、監査担当者による状態確認を依頼します。')
    await page.getByTestId('phase3e-ack-unverified').check()
    await page.getByTestId('phase3e-ack-no-unlock').check()
    await page.getByTestId('phase3e-record-review').click()

    const pending = page.getByTestId('phase3e-pending-review')
    await expect(pending).toContainText('status: pending_review')
    await expect(pending).toContainText('authoritative=false / execution_enabled=false / lock_release_allowed=false')
    expect(await page.evaluate(key => localStorage.getItem(key), UNCERTAINTY_STORAGE_KEY)).toBe(before)
    const storedReview = await page.evaluate(key => JSON.parse(localStorage.getItem(key) || 'null'), UNCERTAINTY_REVIEW_STORAGE_KEY)
    expect(storedReview.status).toBe('pending_review')
    expect(storedReview.executionEnabled).toBe(false)

    await page.reload()
    await expect(page.getByTestId('phase3e-pending-review')).toBeVisible()
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    await expect(page.getByTestId('dry-run-button')).toBeDisabled()
    expect(await page.evaluate(key => localStorage.getItem(key), UNCERTAINTY_STORAGE_KEY)).toBe(before)
  })

  test('rejects a tampered or approved review while retaining the original lock', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    const tampered = { ...reviewFor(LOCK), request: { ...LOCK.request, endPeriod: '2026-03' }, status: 'approved' }
    await seedStorage(page, LOCK, tampered)
    await openDataCollection(page, baseURL)

    await expect(page.getByTestId('phase3e-review-form')).toBeVisible()
    await expect(page.getByTestId('phase3e-pending-review')).toHaveCount(0)
    await expect(page.getByTestId('phase3e-review-error')).toContainText('一致しないため無効')
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    const storedLock = await page.evaluate(key => JSON.parse(localStorage.getItem(key) || 'null'), UNCERTAINTY_STORAGE_KEY)
    expect(storedLock).toEqual(LOCK)
  })

  test('job-bound uncertainty never exposes the jobless review scaffold', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await seedStorage(page, { ...LOCK, jobId: '11111111-1111-4111-8111-111111111111' })
    await openDataCollection(page, baseURL)

    await expect(page.getByTestId('phase3e-review-form')).toHaveCount(0)
    await expect(page.getByTestId('phase3e-pending-review')).toHaveCount(0)
    await expect(page.getByTestId('reconcile-status-button')).toBeVisible()
    await expect(page.getByTestId('execute-button')).toBeDisabled()
  })

  test('malformed lock is retained and fails closed without a review form', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    const malformed = { ...LOCK, unlock: true }
    await seedStorage(page, malformed)
    await openDataCollection(page, baseURL)

    await expect(page.getByTestId('uncertainty-storage-blocked')).toBeVisible()
    await expect(page.getByTestId('phase3e-review-form')).toHaveCount(0)
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    await expect(page.getByTestId('dry-run-button')).toBeDisabled()
    const stored = await page.evaluate(key => JSON.parse(localStorage.getItem(key) || 'null'), UNCERTAINTY_STORAGE_KEY)
    expect(stored).toEqual(malformed)
  })

  test('review storage failure produces no pending state and never removes the lock', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await page.addInitScript(({ lockKey, reviewKey, lock }) => {
      localStorage.setItem(lockKey, JSON.stringify(lock))
      const original = Storage.prototype.setItem
      Storage.prototype.setItem = function (key: string, value: string) {
        if (key === reviewKey) throw new Error('review storage disabled')
        return original.call(this, key, value)
      }
    }, { lockKey: UNCERTAINTY_STORAGE_KEY, reviewKey: UNCERTAINTY_REVIEW_STORAGE_KEY, lock: LOCK })
    await openDataCollection(page, baseURL)

    await page.getByTestId('phase3e-review-reason').fill('サーバー状態が未確認のため、監査担当者による状態確認を依頼します。')
    await page.getByTestId('phase3e-ack-unverified').check()
    await page.getByTestId('phase3e-ack-no-unlock').check()
    await page.getByTestId('phase3e-record-review').click()

    await expect(page.getByTestId('phase3e-review-error')).toContainText('保存できません')
    await expect(page.getByTestId('phase3e-pending-review')).toHaveCount(0)
    expect(await page.evaluate(key => JSON.parse(localStorage.getItem(key) || 'null'), UNCERTAINTY_STORAGE_KEY)).toEqual(LOCK)
  })

  test('an already-open tab observes a lock written by another tab and fails closed', async ({ page, context, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await openDataCollection(page, baseURL)
    await expect(page.getByTestId('execute-button')).toBeEnabled()

    const second = await context.newPage()
    await second.goto(`${baseURL}/favicon.ico`)
    await second.evaluate(({ key, lock }) => localStorage.setItem(key, JSON.stringify(lock)), {
      key: UNCERTAINTY_STORAGE_KEY,
      lock: LOCK,
    })

    await expect(page.getByTestId('uncertainty-panel')).toBeVisible()
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    await expect(page.getByTestId('dry-run-button')).toBeDisabled()

    await second.evaluate(key => localStorage.removeItem(key), UNCERTAINTY_STORAGE_KEY)
    await expect(page.getByTestId('uncertainty-storage-blocked')).toContainText('別タブでlock保存領域が削除')
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    await expect(page.getByTestId('dry-run-button')).toBeDisabled()
    await second.close()
  })

  test('malformed cross-tab lock creation followed by removal cannot bypass fail-closed state', async ({ page, context, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await openDataCollection(page, baseURL)
    await expect(page.getByTestId('execute-button')).toBeEnabled()

    const second = await context.newPage()
    await second.goto(`${baseURL}/favicon.ico`)
    await second.evaluate(key => {
      localStorage.setItem(key, '{')
      localStorage.removeItem(key)
    }, UNCERTAINTY_STORAGE_KEY)

    await expect(page.getByTestId('uncertainty-storage-blocked')).toContainText('別タブでlock保存領域が削除')
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    await expect(page.getByTestId('dry-run-button')).toBeDisabled()
    await second.close()
  })
})
