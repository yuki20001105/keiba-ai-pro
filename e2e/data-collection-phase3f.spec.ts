import { expect, Page, test } from '@playwright/test'
import {
  UNCERTAINTY_REVIEW_STORAGE_KEY,
  UNCERTAINTY_STORAGE_KEY,
  createPendingUncertaintyReview,
  type PendingUncertaintyReview,
  type PersistedUncertaintyLock,
} from '../src/lib/scrape-uncertainty-approval'
import {
  UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY,
  UNCERTAINTY_SERVER_REVIEW_LOCATOR_WRITE_LOCK_NAME,
  type ScrapeUncertaintyReviewLocator,
} from '../src/lib/scrape-uncertainty-review-server'
import { mockSupabaseIdentity, setSupabaseTestSession } from './helpers/mock-api'

const SUPABASE_ORIGIN = 'http://127.0.0.1:54321'
const SERVER_REQUEST_ID = '22222222-2222-4222-8222-222222222222'
const LOCK: PersistedUncertaintyLock = {
  version: 1,
  failureKind: 'monitoring',
  request: { startPeriod: '2026-01', endPeriod: '2026-02', forceRescrape: false },
  occurredAt: '2026-07-18T00:00:00.000Z',
}
const REVIEW: PendingUncertaintyReview = createPendingUncertaintyReview({
  lock: LOCK,
  requestId: '11111111-1111-4111-8111-111111111111',
  requestedAt: '2026-07-18T00:01:00.000Z',
  reason: 'サーバー状態が未確認のため、独立した管理者による監査記録を依頼します。',
  serverStateUnverified: true,
  noUnlockOrRetry: true,
})!
const LOCATOR: ScrapeUncertaintyReviewLocator = {
  version: 1,
  requestId: SERVER_REQUEST_ID,
  clientRequestId: REVIEW.requestId,
  requestPayloadHash: 'a'.repeat(64),
}

function serverEnvelope(
  status: 'pending_review' | 'approved' | 'rejected' | 'revoked' | 'expired' = 'pending_review',
  locator: ScrapeUncertaintyReviewLocator = LOCATOR,
) {
  const decided = status !== 'pending_review'
  return {
    version: 1,
    request: {
      request_id: locator.requestId,
      client_request_id: locator.clientRequestId,
      status,
      version: decided ? 2 : 1,
      request_payload_hash: locator.requestPayloadHash,
      failure_kind: REVIEW.failureKind,
      request: { start_period: '2026-01', end_period: '2026-02', force_rescrape: false },
      uncertainty_occurred_at: REVIEW.uncertaintyOccurredAt,
      reason: REVIEW.reason,
      requested_at: '2026-07-18T00:02:00.000Z',
      expires_at: '2026-07-18T00:32:00.000Z',
      decided_by: decided && status !== 'expired' ? '33333333-3333-4333-8333-333333333333' : null,
      decided_at: decided ? '2026-07-18T00:03:00.000Z' : null,
      decision_reason: decided ? '独立管理者がreview-onlyの監査証跡を確認し、実行許可なしで判断しました。' : null,
      approval_scope: 'review_only',
      authoritative_record: true,
      approval_granted: status === 'approved',
      execution_enabled: false,
      lock_release_allowed: false,
      automatic_action_taken: false,
    },
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

async function seedReviewState(page: Page, locator?: unknown) {
  await page.addInitScript(({ lockKey, reviewKey, locatorKey, lock, review, locatorValue }) => {
    localStorage.setItem(lockKey, JSON.stringify(lock))
    localStorage.setItem(reviewKey, JSON.stringify(review))
    if (locatorValue !== undefined) localStorage.setItem(locatorKey, JSON.stringify(locatorValue))
  }, {
    lockKey: UNCERTAINTY_STORAGE_KEY,
    reviewKey: UNCERTAINTY_REVIEW_STORAGE_KEY,
    locatorKey: UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY,
    lock: LOCK,
    review: REVIEW,
    locatorValue: locator,
  })
}

test.describe('Phase 3F server-authoritative review-only ledger bridge', () => {
  let unexpectedExternal: string[] = []
  let unexpectedApi: string[] = []
  let scrapePostCount = 0
  let forbiddenWriteCount = 0

  test.beforeEach(async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    unexpectedExternal = []
    unexpectedApi = []
    scrapePostCount = 0
    forbiddenWriteCount = 0
    const appOrigin = new URL(baseURL).origin

    page.on('request', request => {
      const url = new URL(request.url())
      if (url.origin !== appOrigin) return
      if (url.pathname === '/api/scrape' && request.method() === 'POST') scrapePostCount += 1
      if (request.method() !== 'GET'
        && url.pathname.startsWith('/api/')
        && url.pathname !== '/api/scrape/uncertainty-review-requests') {
        forbiddenWriteCount += 1
      }
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
    expect(scrapePostCount).toBe(0)
    expect(forbiddenWriteCount).toBe(0)
  })

  test('submits one exact owner-free request while preserving both local records and every execution lock', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await seedReviewState(page)
    let ledgerPostCount = 0
    let submittedBody: unknown = null
    await page.route('**/api/scrape/uncertainty-review-requests', async route => {
      if (route.request().method() === 'POST') {
        ledgerPostCount += 1
        submittedBody = route.request().postDataJSON()
        return route.fulfill({ status: 200, json: serverEnvelope() })
      }
      return route.fulfill({ status: 405, json: { detail: 'unexpected method' } })
    })
    await page.route(`**/api/scrape/uncertainty-review-requests/${SERVER_REQUEST_ID}`, route =>
      route.fulfill({ status: 200, json: serverEnvelope() }))
    await authorizeAdmin(page, baseURL)
    await page.goto('/data-collection')

    const lockBefore = await page.evaluate(key => localStorage.getItem(key), UNCERTAINTY_STORAGE_KEY)
    const reviewBefore = await page.evaluate(key => localStorage.getItem(key), UNCERTAINTY_REVIEW_STORAGE_KEY)
    await expect(page.getByTestId('phase3f-submit-review-request')).toBeEnabled()
    await page.getByTestId('phase3f-submit-review-request').click()

    await expect(page.getByTestId('phase3f-server-review-panel')).toBeVisible()
    await expect(page.getByTestId('phase3f-server-review-status')).toContainText('pending_review')
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    await expect(page.getByTestId('dry-run-button')).toBeDisabled()
    await expect(page.getByTestId('retry-button')).toHaveCount(0)
    expect(ledgerPostCount).toBe(1)
    expect(submittedBody).toEqual({
      client_request_id: REVIEW.requestId,
      failure_kind: 'monitoring',
      request: { start_period: '2026-01', end_period: '2026-02', force_rescrape: false },
      uncertainty_occurred_at: REVIEW.uncertaintyOccurredAt,
      reason: REVIEW.reason,
      acknowledgements: { server_state_unverified: true, no_unlock_or_retry: true },
    })
    expect(JSON.stringify(submittedBody)).not.toMatch(/owner|actor|status|payload_hash|approved|execution_enabled|lock_release_allowed/)
    expect(await page.evaluate(key => localStorage.getItem(key), UNCERTAINTY_STORAGE_KEY)).toBe(lockBefore)
    expect(await page.evaluate(key => localStorage.getItem(key), UNCERTAINTY_REVIEW_STORAGE_KEY)).toBe(reviewBefore)
    expect(await page.evaluate(key => JSON.parse(localStorage.getItem(key) || 'null'), UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY)).toEqual(LOCATOR)
  })

  for (const status of ['approved', 'rejected', 'revoked', 'expired'] as const) {
    test(`restores ${status} read-only status without a POST or unlock`, async ({ page, baseURL }) => {
      if (!baseURL) throw new Error('Playwright baseURL is required')
      await seedReviewState(page, LOCATOR)
      let ledgerPostCount = 0
      let ledgerGetCount = 0
      await page.route(`**/api/scrape/uncertainty-review-requests/${SERVER_REQUEST_ID}`, route => {
        if (route.request().method() === 'GET') {
          ledgerGetCount += 1
          return route.fulfill({ status: 200, json: serverEnvelope(status) })
        }
        ledgerPostCount += 1
        return route.fulfill({ status: 405, json: { detail: 'writes forbidden' } })
      })
      await authorizeAdmin(page, baseURL)
      await page.goto('/data-collection')

      await expect(page.getByTestId('phase3f-server-review-status')).toContainText(status)
      await expect(page.getByTestId('phase3f-server-review-panel')).toContainText('execution_enabled=false')
      await expect(page.getByTestId('execute-button')).toBeDisabled()
      await expect(page.getByTestId('dry-run-button')).toBeDisabled()
      await expect(page.getByTestId('retry-button')).toHaveCount(0)
      expect(ledgerGetCount).toBe(1)
      expect(ledgerPostCount).toBe(0)
    })
  }

  for (const failureStatus of [401, 403, 409, 413, 429, 503]) {
    test(`fails closed on HTTP ${failureStatus} without automatic retry or locator persistence`, async ({ page, baseURL }) => {
      if (!baseURL) throw new Error('Playwright baseURL is required')
      await seedReviewState(page)
      let ledgerPostCount = 0
      await page.route('**/api/scrape/uncertainty-review-requests', route => {
        ledgerPostCount += 1
        return route.fulfill({ status: failureStatus, json: { detail: 'review request was not accepted' } })
      })
      await authorizeAdmin(page, baseURL)
      await page.goto('/data-collection')
      await page.getByTestId('phase3f-submit-review-request').click()

      await expect(page.getByTestId('phase3f-server-review-error')).toContainText('lockを維持')
      await expect(page.getByTestId('phase3f-server-review-panel')).toHaveCount(0)
      await expect(page.getByTestId('execute-button')).toBeDisabled()
      await page.waitForTimeout(1_200)
      expect(ledgerPostCount).toBe(1)
      expect(await page.evaluate(key => localStorage.getItem(key), UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY)).toBeNull()
    })
  }

  test('rejects an approved-looking unsafe response and retains the original lock', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await seedReviewState(page)
    const unsafe = serverEnvelope('approved')
    unsafe.request.execution_enabled = true
    await page.route('**/api/scrape/uncertainty-review-requests', route =>
      route.fulfill({ status: 200, json: unsafe }))
    await authorizeAdmin(page, baseURL)
    await page.goto('/data-collection')
    await page.getByTestId('phase3f-submit-review-request').click()

    await expect(page.getByTestId('phase3f-server-review-error')).toContainText('lockを維持')
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    expect(await page.evaluate(key => JSON.parse(localStorage.getItem(key) || 'null'), UNCERTAINTY_STORAGE_KEY)).toEqual(LOCK)
    expect(await page.evaluate(key => localStorage.getItem(key), UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY)).toBeNull()
  })

  test('rejects a response that does not correlate to the durable local request', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await seedReviewState(page)
    const mismatched = serverEnvelope()
    mismatched.request.request.end_period = '2026-03'
    await page.route('**/api/scrape/uncertainty-review-requests', route =>
      route.fulfill({ status: 200, json: mismatched }))
    await authorizeAdmin(page, baseURL)
    await page.goto('/data-collection')
    await page.getByTestId('phase3f-submit-review-request').click()

    await expect(page.getByTestId('phase3f-server-review-error')).toBeVisible()
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    expect(await page.evaluate(key => localStorage.getItem(key), UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY)).toBeNull()
  })

  test('re-reads the durable draft immediately before submit and sends no POST after same-tab tampering', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await seedReviewState(page)
    let ledgerPostCount = 0
    await page.route('**/api/scrape/uncertainty-review-requests', route => {
      ledgerPostCount += 1
      return route.fulfill({ status: 200, json: serverEnvelope() })
    })
    await authorizeAdmin(page, baseURL)
    await page.goto('/data-collection')
    await page.evaluate(({ key }) => {
      const value = JSON.parse(localStorage.getItem(key) || 'null')
      value.reason = 'This locally tampered reason must never be submitted to the ledger.'
      localStorage.setItem(key, JSON.stringify(value))
    }, { key: UNCERTAINTY_REVIEW_STORAGE_KEY })
    await page.getByTestId('phase3f-submit-review-request').click()

    await expect(page.getByTestId('phase3f-server-review-error')).toBeVisible()
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    expect(ledgerPostCount).toBe(0)
  })

  test('same-tab lock deletion followed by reload keeps orphaned review evidence fail-closed', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await page.route(`**/api/scrape/uncertainty-review-requests/${SERVER_REQUEST_ID}`, route =>
      route.fulfill({ status: 200, json: serverEnvelope() }))
    await authorizeAdmin(page, baseURL)
    await page.goto('/')
    await page.evaluate(({ lockKey, reviewKey, locatorKey, lock, review, locator }) => {
      localStorage.setItem(lockKey, JSON.stringify(lock))
      localStorage.setItem(reviewKey, JSON.stringify(review))
      localStorage.setItem(locatorKey, JSON.stringify(locator))
    }, {
      lockKey: UNCERTAINTY_STORAGE_KEY,
      reviewKey: UNCERTAINTY_REVIEW_STORAGE_KEY,
      locatorKey: UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY,
      lock: LOCK,
      review: REVIEW,
      locator: LOCATOR,
    })
    await page.goto('/data-collection')
    await expect(page.getByTestId('phase3f-server-review-panel')).toBeVisible()

    await page.evaluate(key => localStorage.removeItem(key), UNCERTAINTY_STORAGE_KEY)
    await page.reload()

    await expect(page.getByTestId('execute-button')).toBeDisabled()
    await expect(page.getByTestId('dry-run-button')).toBeDisabled()
    await expect(page.getByTestId('phase3f-server-review-panel')).toHaveCount(0)
    expect(await page.evaluate(key => localStorage.getItem(key), UNCERTAINTY_REVIEW_STORAGE_KEY)).not.toBeNull()
    expect(await page.evaluate(key => localStorage.getItem(key), UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY)).not.toBeNull()
  })

  test('a stale locator response cannot overwrite the replacement locator record', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    const replacement: ScrapeUncertaintyReviewLocator = {
      version: 1,
      requestId: '44444444-4444-4444-8444-444444444444',
      clientRequestId: REVIEW.requestId,
      requestPayloadHash: 'b'.repeat(64),
    }
    await seedReviewState(page, LOCATOR)
    let releaseOriginal!: () => void
    let markOriginalStarted: (() => void) | null = null
    const originalStarted = new Promise<void>(resolve => { markOriginalStarted = resolve })
    const originalRelease = new Promise<void>(resolve => { releaseOriginal = resolve })
    await page.route('**/api/scrape/uncertainty-review-requests/*', async route => {
      const requestId = new URL(route.request().url()).pathname.split('/').pop()
      if (requestId === SERVER_REQUEST_ID) {
        markOriginalStarted?.()
        await originalRelease
        return route.fulfill({ status: 200, json: serverEnvelope('approved', LOCATOR) })
      }
      if (requestId === replacement.requestId) {
        return route.fulfill({ status: 200, json: serverEnvelope('rejected', replacement) })
      }
      return route.fulfill({ status: 404, json: { detail: 'not found' } })
    })
    await authorizeAdmin(page, baseURL)
    await page.goto('/data-collection')
    await originalStarted

    await page.evaluate(({ key, value }) => {
      const oldValue = localStorage.getItem(key)
      const newValue = JSON.stringify(value)
      localStorage.setItem(key, newValue)
      window.dispatchEvent(new StorageEvent('storage', { key, oldValue, newValue }))
    }, { key: UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY, value: replacement })

    await expect(page.getByTestId('phase3f-server-review-request-id')).toContainText(replacement.requestId)
    await expect(page.getByTestId('phase3f-server-review-status')).toContainText('rejected')
    releaseOriginal()
    await page.waitForTimeout(200)
    await expect(page.getByTestId('phase3f-server-review-request-id')).toContainText(replacement.requestId)
    await expect(page.getByTestId('phase3f-server-review-status')).toContainText('rejected')
  })

  test('a locator written by another tab during POST is never overwritten by the stale submission response', async ({ page, context, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    const replacement: ScrapeUncertaintyReviewLocator = {
      version: 1,
      requestId: '55555555-5555-4555-8555-555555555555',
      clientRequestId: REVIEW.requestId,
      requestPayloadHash: 'c'.repeat(64),
    }
    await seedReviewState(page)
    let releaseSubmission!: () => void
    let markSubmissionStarted: (() => void) | null = null
    const submissionStarted = new Promise<void>(resolve => { markSubmissionStarted = resolve })
    const submissionRelease = new Promise<void>(resolve => { releaseSubmission = resolve })
    let ledgerPostCount = 0
    await page.route('**/api/scrape/uncertainty-review-requests', async route => {
      if (route.request().method() !== 'POST') {
        return route.fulfill({ status: 405, json: { detail: 'unexpected method' } })
      }
      ledgerPostCount += 1
      markSubmissionStarted?.()
      await submissionRelease
      return route.fulfill({ status: 200, json: serverEnvelope() })
    })
    await page.route(`**/api/scrape/uncertainty-review-requests/${replacement.requestId}`, route =>
      route.fulfill({ status: 200, json: serverEnvelope('rejected', replacement) }))
    await authorizeAdmin(page, baseURL)
    await page.goto('/data-collection')
    await page.getByTestId('phase3f-submit-review-request').click()
    await submissionStarted

    const second = await context.newPage()
    await second.goto(`${baseURL}/favicon.ico`)
    const heldKey = 'phase3f-test:locator-write-lock-held'
    const releaseKey = 'phase3f-test:locator-write-lock-release'
    await second.evaluate(({ lockName, locatorKey, held, release, value }) => {
      void navigator.locks.request(lockName, { mode: 'exclusive' }, async () => {
        localStorage.setItem(held, '1')
        while (localStorage.getItem(release) !== '1') {
          await new Promise(resolve => setTimeout(resolve, 10))
        }
        localStorage.setItem(locatorKey, JSON.stringify(value))
      })
    }, {
      lockName: UNCERTAINTY_SERVER_REVIEW_LOCATOR_WRITE_LOCK_NAME,
      locatorKey: UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY,
      held: heldKey,
      release: releaseKey,
      value: replacement,
    })
    await expect.poll(() => second.evaluate(key => localStorage.getItem(key), heldKey)).toBe('1')

    releaseSubmission()
    await page.waitForTimeout(100)
    await second.evaluate(key => localStorage.setItem(key, '1'), releaseKey)

    await expect(page.getByTestId('phase3f-server-review-request-id')).toContainText(replacement.requestId)
    await expect(page.getByTestId('phase3f-server-review-status')).toContainText('rejected')
    await page.waitForTimeout(250)

    expect(ledgerPostCount).toBe(1)
    expect(await page.evaluate(key => JSON.parse(localStorage.getItem(key) || 'null'), UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY)).toEqual(replacement)
    await expect(page.getByTestId('phase3f-server-review-request-id')).toContainText(replacement.requestId)
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    await expect(page.getByTestId('dry-run-button')).toBeDisabled()
    await second.evaluate(({ held, release }) => {
      localStorage.removeItem(held)
      localStorage.removeItem(release)
    }, { held: heldKey, release: releaseKey })
    await second.close()
  })

  test('a validated server response remains visible and cannot be resubmitted when locator persistence fails', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await seedReviewState(page)
    let ledgerPostCount = 0
    await page.route('**/api/scrape/uncertainty-review-requests', route => {
      ledgerPostCount += 1
      return route.fulfill({ status: 200, json: serverEnvelope() })
    })
    await authorizeAdmin(page, baseURL)
    await page.goto('/data-collection')
    await page.evaluate(key => {
      const originalSetItem = Storage.prototype.setItem
      Storage.prototype.setItem = function setItem(storageKey: string, value: string) {
        if (storageKey === key) throw new DOMException('simulated locator persistence failure', 'QuotaExceededError')
        return originalSetItem.call(this, storageKey, value)
      }
    }, UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY)

    await page.getByTestId('phase3f-submit-review-request').click()

    await expect(page.getByTestId('phase3f-server-review-panel')).toBeVisible()
    await expect(page.getByTestId('phase3f-server-review-request-id')).toContainText(SERVER_REQUEST_ID)
    await expect(page.getByTestId('phase3f-server-review-error')).toContainText(SERVER_REQUEST_ID)
    await expect(page.getByTestId('phase3f-submit-review-request')).toHaveCount(0)
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    await expect(page.getByTestId('dry-run-button')).toBeDisabled()
    await page.waitForTimeout(1_200)
    expect(ledgerPostCount).toBe(1)
    expect(await page.evaluate(key => localStorage.getItem(key), UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY)).toBeNull()
  })

  test('cross-tab locator deletion is never treated as unlock evidence', async ({ page, context, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await seedReviewState(page, LOCATOR)
    await page.route(`**/api/scrape/uncertainty-review-requests/${SERVER_REQUEST_ID}`, route =>
      route.fulfill({ status: 200, json: serverEnvelope() }))
    await authorizeAdmin(page, baseURL)
    await page.goto('/data-collection')
    await expect(page.getByTestId('phase3f-server-review-panel')).toBeVisible()

    const second = await context.newPage()
    await second.goto(`${baseURL}/favicon.ico`)
    await second.evaluate(key => localStorage.removeItem(key), UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY)

    await expect(page.getByTestId('phase3f-server-review-error')).toContainText('lockを維持')
    await expect(page.getByTestId('execute-button')).toBeDisabled()
    await expect(page.getByTestId('dry-run-button')).toBeDisabled()
    expect(await page.evaluate(key => JSON.parse(localStorage.getItem(key) || 'null'), UNCERTAINTY_STORAGE_KEY)).toEqual(LOCK)
    await second.close()
  })
})
