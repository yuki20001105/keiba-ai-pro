import { expect, Page, test } from '@playwright/test'
import {
  UNCERTAINTY_REVIEW_STORAGE_KEY,
  UNCERTAINTY_STORAGE_KEY,
} from '../src/lib/scrape-uncertainty-approval'
import { UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY } from '../src/lib/scrape-uncertainty-review-server'
import { mockSupabaseIdentity, setSupabaseTestSession } from './helpers/mock-api'

const SUPABASE_ORIGIN = 'http://127.0.0.1:54321'
const REQUEST_ID = '11111111-1111-4111-8111-111111111111'
const CLIENT_REQUEST_ID = '22222222-2222-4222-8222-222222222222'
const DECISION_REASON = 'Independent Admin verified the review-only evidence and confirms no execution is permitted.'
const FIXTURE_NOW_MS = Date.now()
const UNCERTAINTY_OCCURRED_AT = new Date(FIXTURE_NOW_MS - 120_000).toISOString()
const REQUESTED_AT = new Date(FIXTURE_NOW_MS - 60_000).toISOString()
const DECIDED_AT = new Date(FIXTURE_NOW_MS).toISOString()
const EXPIRES_AT = new Date(FIXTURE_NOW_MS + 30 * 60_000).toISOString()
const STORAGE_SENTINELS: Record<string, string> = {
  [UNCERTAINTY_STORAGE_KEY]: '{"phase3g":"lock-sentinel"}',
  [UNCERTAINTY_REVIEW_STORAGE_KEY]: '{"phase3g":"review-sentinel"}',
  [UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY]: '{"phase3g":"locator-sentinel"}',
}

function publicRecord(status: 'pending_review' | 'approved' | 'rejected' = 'pending_review') {
  const decided = status !== 'pending_review'
  return {
    request_id: REQUEST_ID,
    client_request_id: CLIENT_REQUEST_ID,
    status,
    version: decided ? 2 : 1,
    request_payload_hash: 'a'.repeat(64),
    failure_kind: 'monitoring',
    request: { start_period: '2026-01', end_period: '2026-02', force_rescrape: false },
    uncertainty_occurred_at: UNCERTAINTY_OCCURRED_AT,
    reason: 'The server state is unknown and requires an independent review.',
    requested_at: REQUESTED_AT,
    expires_at: EXPIRES_AT,
    decided_by: decided ? '33333333-3333-4333-8333-333333333333' : null,
    decided_at: decided ? DECIDED_AT : null,
    decision_reason: decided ? DECISION_REASON : null,
    approval_scope: 'review_only',
    authoritative_record: true,
    approval_granted: status === 'approved',
    execution_enabled: false,
    lock_release_allowed: false,
    automatic_action_taken: false,
  }
}

function listEnvelope(requests: unknown[] = [publicRecord()]) {
  return { version: 1, requests }
}

function decisionEnvelope(status: 'approved' | 'rejected') {
  return { version: 1, request: publicRecord(status) }
}

async function authorizeAdmin(page: Page, baseURL: string) {
  await setSupabaseTestSession(page, {
    role: 'admin',
    tier: 'premium',
    appBaseUrl: baseURL,
    supabaseUrl: SUPABASE_ORIGIN,
  })
  await mockSupabaseIdentity(page, { authenticated: true, role: 'admin', tier: 'premium' })
}

async function openReviewQueue(page: Page, baseURL: string) {
  await authorizeAdmin(page, baseURL)
  await page.goto('/data-collection/uncertainty-reviews')
}

async function loadAndSelect(page: Page) {
  await page.getByTestId('phase3g-load-reviewable').click()
  await expect(page.getByTestId('phase3g-review-card')).toHaveCount(1)
  await page.getByTestId('phase3g-select-review').click()
  await page.getByTestId('phase3g-decision-reason').fill(DECISION_REASON)
  await page.getByTestId('phase3g-review-only-ack').check()
}

test.describe('Phase 3G review-only Admin evidence console', () => {
  let unexpectedExternal: string[] = []
  let unexpectedApi: string[] = []
  let forbiddenWriteCount = 0

  test.beforeEach(async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    unexpectedExternal = []
    unexpectedApi = []
    forbiddenWriteCount = 0
    const appOrigin = new URL(baseURL).origin

    await page.addInitScript(sentinels => {
      for (const [key, value] of Object.entries(sentinels)) localStorage.setItem(key, value)
    }, STORAGE_SENTINELS)

    page.on('request', request => {
      const url = new URL(request.url())
      if (url.origin !== appOrigin || request.method() === 'GET') return
      const isDecision = /^\/api\/scrape\/uncertainty-review-requests\/[0-9a-f-]+\/decision$/i.test(url.pathname)
      if (!isDecision) forbiddenWriteCount += 1
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

  test.afterEach(async ({ page, baseURL }) => {
    expect(unexpectedExternal).toEqual([])
    expect(unexpectedApi).toEqual([])
    expect(forbiddenWriteCount).toBe(0)
    if (!page.isClosed() && baseURL && page.url().startsWith(new URL(baseURL).origin)) {
      const persisted = await page.evaluate(keys => Object.fromEntries(
        keys.map(key => [key, localStorage.getItem(key)]),
      ), Object.keys(STORAGE_SENTINELS))
      expect(persisted).toEqual(STORAGE_SENTINELS)
    }
  })

  test('blocks a non-Admin from the reviewer surface before any ledger API request', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    await setSupabaseTestSession(page, {
      role: 'user',
      tier: 'premium',
      appBaseUrl: baseURL,
      supabaseUrl: SUPABASE_ORIGIN,
    })
    await mockSupabaseIdentity(page, { authenticated: true, role: 'user', tier: 'premium' })
    await page.goto('/data-collection/uncertainty-reviews')

    await expect(page.getByTestId('phase3g-admin-required')).toBeVisible()
    await expect(page.getByTestId('phase3g-load-reviewable')).toHaveCount(0)
  })

  test('loads reviewable records only after an explicit action and displays immutable safety evidence', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    let listGetCount = 0
    await page.route('**/api/scrape/uncertainty-review-requests?scope=reviewable&limit=20', route => {
      listGetCount += 1
      return route.fulfill({ status: 200, json: listEnvelope() })
    })
    await openReviewQueue(page, baseURL)

    expect(listGetCount).toBe(0)
    await expect(page.getByTestId('phase3g-safety-notice')).toContainText('execution_enabled=false')
    await page.getByTestId('phase3g-load-reviewable').click()

    await expect(page.getByTestId('phase3g-review-card')).toContainText(REQUEST_ID)
    await expect(page.getByTestId('phase3g-review-card')).toContainText('payload_hash')
    await expect(page.getByTestId('phase3g-review-card')).toContainText('version: 1')
    await expect(page.getByTestId('phase3g-review-card')).toContainText('period: 2026-01 - 2026-02')
    await expect(page.getByTestId('phase3g-review-card')).toContainText('lock_release_allowed=false')
    await page.getByTestId('phase3g-select-review').click()
    await expect(page.getByTestId('phase3g-approve')).toBeDisabled()
    await expect(page.getByTestId('phase3g-reject')).toBeDisabled()
    await page.getByTestId('phase3g-decision-reason').fill(DECISION_REASON)
    await expect(page.getByTestId('phase3g-approve')).toBeDisabled()
    await page.getByTestId('phase3g-review-only-ack').check()
    await expect(page.getByTestId('phase3g-approve')).toBeEnabled()
    await expect(page.getByTestId('phase3g-reject')).toBeEnabled()
    expect(listGetCount).toBe(1)
  })

  test('fails closed instead of rendering an already-expired pending record', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    const expired = {
      ...publicRecord(),
      requested_at: new Date(FIXTURE_NOW_MS - 120_000).toISOString(),
      expires_at: new Date(FIXTURE_NOW_MS - 60_000).toISOString(),
    }
    await page.route('**/api/scrape/uncertainty-review-requests?scope=reviewable&limit=20', route =>
      route.fulfill({ status: 200, json: listEnvelope([expired]) }))
    await openReviewQueue(page, baseURL)
    await page.getByTestId('phase3g-load-reviewable').click()

    await expect(page.getByTestId('phase3g-error')).toContainText('pending invariants')
    await expect(page.getByTestId('phase3g-review-card')).toHaveCount(0)
    await expect(page.getByTestId('phase3g-decision-panel')).toHaveCount(0)
  })

  test('submits one exact approve body under synchronous double click and performs no automatic action', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    let decisionPostCount = 0
    let decisionBody: unknown = null
    await page.route('**/api/scrape/uncertainty-review-requests?scope=reviewable&limit=20', route =>
      route.fulfill({ status: 200, json: listEnvelope() }))
    await page.route(`**/api/scrape/uncertainty-review-requests/${REQUEST_ID}/decision`, async route => {
      decisionPostCount += 1
      decisionBody = route.request().postDataJSON()
      await new Promise(resolve => setTimeout(resolve, 100))
      return route.fulfill({ status: 200, json: decisionEnvelope('approved') })
    })
    await openReviewQueue(page, baseURL)
    await loadAndSelect(page)

    await page.getByTestId('phase3g-approve').evaluate((button: HTMLButtonElement) => {
      button.click()
      button.click()
    })

    await expect(page.getByTestId('phase3g-decision-result')).toContainText('status: approved')
    await expect(page.getByTestId('phase3g-decision-result')).toContainText('execution_enabled=false')
    expect(decisionPostCount).toBe(1)
    expect(decisionBody).toEqual({ action: 'approve', expected_version: 1, reason: DECISION_REASON })
    expect(JSON.stringify(decisionBody)).not.toMatch(/owner|actor|unlock|execution_enabled|lock_release_allowed/)
    await page.waitForTimeout(300)
    expect(decisionPostCount).toBe(1)
  })

  test('records an exact reject decision without navigation or implicit reload', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    let listGetCount = 0
    let decisionBody: unknown = null
    await page.route('**/api/scrape/uncertainty-review-requests?scope=reviewable&limit=20', route => {
      listGetCount += 1
      return route.fulfill({ status: 200, json: listEnvelope() })
    })
    await page.route(`**/api/scrape/uncertainty-review-requests/${REQUEST_ID}/decision`, route => {
      decisionBody = route.request().postDataJSON()
      return route.fulfill({ status: 200, json: decisionEnvelope('rejected') })
    })
    await openReviewQueue(page, baseURL)
    await loadAndSelect(page)
    await page.getByTestId('phase3g-reject').click()

    await expect(page.getByTestId('phase3g-decision-result')).toContainText('status: rejected')
    expect(page.url()).toBe(`${baseURL}/data-collection/uncertainty-reviews`)
    expect(listGetCount).toBe(1)
    expect(decisionBody).toEqual({ action: 'reject', expected_version: 1, reason: DECISION_REASON })
  })

  for (const failureStatus of [403, 409, 503]) {
    test(`fails closed on decision HTTP ${failureStatus} without automatic retry`, async ({ page, baseURL }) => {
      if (!baseURL) throw new Error('Playwright baseURL is required')
      let decisionPostCount = 0
      await page.route('**/api/scrape/uncertainty-review-requests?scope=reviewable&limit=20', route =>
        route.fulfill({ status: 200, json: listEnvelope() }))
      await page.route(`**/api/scrape/uncertainty-review-requests/${REQUEST_ID}/decision`, route => {
        decisionPostCount += 1
        return route.fulfill({ status: failureStatus, json: { detail: `review decision failed with ${failureStatus}` } })
      })
      await openReviewQueue(page, baseURL)
      await loadAndSelect(page)
      await page.getByTestId('phase3g-approve').click()

      await expect(page.getByTestId('phase3g-error')).toContainText(String(failureStatus))
      await expect(page.getByTestId('phase3g-decision-result')).toHaveCount(0)
      await expect(page.getByTestId('phase3g-decision-panel')).toBeVisible()
      await page.waitForTimeout(300)
      expect(decisionPostCount).toBe(1)
    })
  }

  test('rejects malformed and unsafe decision responses while retaining the selected review', async ({ page, baseURL }) => {
    if (!baseURL) throw new Error('Playwright baseURL is required')
    const unsafe = decisionEnvelope('approved')
    unsafe.request.execution_enabled = true
    let decisionPostCount = 0
    await page.route('**/api/scrape/uncertainty-review-requests?scope=reviewable&limit=20', route =>
      route.fulfill({ status: 200, json: listEnvelope() }))
    await page.route(`**/api/scrape/uncertainty-review-requests/${REQUEST_ID}/decision`, route => {
      decisionPostCount += 1
      return route.fulfill({ status: 200, json: unsafe })
    })
    await openReviewQueue(page, baseURL)
    await loadAndSelect(page)
    await page.getByTestId('phase3g-approve').click()

    await expect(page.getByTestId('phase3g-error')).toBeVisible()
    await expect(page.getByTestId('phase3g-decision-result')).toHaveCount(0)
    await expect(page.getByTestId('phase3g-decision-panel')).toBeVisible()
    await page.waitForTimeout(300)
    expect(decisionPostCount).toBe(1)
  })
})
