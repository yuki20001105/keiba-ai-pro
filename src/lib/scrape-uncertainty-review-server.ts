import type { SupabaseClient } from '@supabase/supabase-js'
import type { PendingUncertaintyReview } from '@/lib/scrape-uncertainty-approval'

export const SCRAPE_UNCERTAINTY_REVIEW_BODY_LIMIT_BYTES = 8 * 1024
export const SCRAPE_UNCERTAINTY_REVIEW_LIST_LIMIT = 100
export const UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY =
  'keiba-ai-pro:phase3f:uncertainty-review-request-locator:v1'
export const UNCERTAINTY_SERVER_REVIEW_LOCATOR_WRITE_LOCK_NAME =
  'keiba-ai-pro:phase3f:uncertainty-review-request-locator-write:v1'

export type ScrapeUncertaintyReviewStatus =
  | 'pending_review'
  | 'approved'
  | 'rejected'
  | 'revoked'
  | 'expired'

export type ScrapeUncertaintyReviewAction = 'approve' | 'reject' | 'revoke'
export type ScrapeUncertaintyFailureKind = 'monitoring' | 'client_stop'
export type ScrapeUncertaintyReviewScope = 'mine' | 'reviewable'

export type ScrapeUncertaintyRequestSnapshot = {
  start_period: string
  end_period: string
  force_rescrape: boolean
}

export type CreateScrapeUncertaintyReviewInput = {
  client_request_id: string
  failure_kind: ScrapeUncertaintyFailureKind
  request: ScrapeUncertaintyRequestSnapshot
  uncertainty_occurred_at: string
  reason: string
  acknowledgements: {
    server_state_unverified: true
    no_unlock_or_retry: true
  }
}

export type DecideScrapeUncertaintyReviewInput = {
  action: ScrapeUncertaintyReviewAction
  expected_version: number
  reason: string
}

export type ListScrapeUncertaintyReviewsInput = {
  scope: ScrapeUncertaintyReviewScope
  limit: number
}

export type ScrapeUncertaintyReviewRecord = {
  request_id: string
  client_request_id: string
  status: ScrapeUncertaintyReviewStatus
  version: number
  request_payload_hash: string
  failure_kind: ScrapeUncertaintyFailureKind
  request: ScrapeUncertaintyRequestSnapshot
  uncertainty_occurred_at: string
  reason: string
  requested_at: string
  expires_at: string
  decided_by: string | null
  decided_at: string | null
  decision_reason: string | null
  approval_scope: 'review_only'
  authoritative_record: true
  approval_granted: boolean
  execution_enabled: false
  lock_release_allowed: false
  automatic_action_taken: false
}

export type ScrapeUncertaintyReviewLocator = {
  version: 1
  requestId: string
  clientRequestId: string
  requestPayloadHash: string
}

export type ReviewValidationResult<T> =
  | { ok: true; value: T }
  | { ok: false; detail: string }

export type BoundedJsonResult =
  | { ok: true; value: unknown }
  | { ok: false; detail: string }

export type ReviewRpcFailure = {
  ok: false
  status: 403 | 404 | 409 | 502 | 503
  detail: string
}

export type ReviewRpcResult<T> = { ok: true; value: T } | ReviewRpcFailure

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const PERIOD_PATTERN = /^(\d{4})-(0[1-9]|1[0-2])$/
const SHA256_PATTERN = /^[0-9a-f]{64}$/
const CONTROL_CHARACTER_PATTERN = /[\x00-\x1f\x7f]/
const CREATE_KEYS = new Set([
  'client_request_id',
  'failure_kind',
  'request',
  'uncertainty_occurred_at',
  'reason',
  'acknowledgements',
])
const SNAPSHOT_KEYS = new Set(['start_period', 'end_period', 'force_rescrape'])
const ACKNOWLEDGEMENT_KEYS = new Set(['server_state_unverified', 'no_unlock_or_retry'])
const DECISION_KEYS = new Set(['action', 'expected_version', 'reason'])
const LIST_QUERY_KEYS = new Set(['scope', 'limit'])
const LOCATOR_KEYS = new Set(['version', 'requestId', 'clientRequestId', 'requestPayloadHash'])
const PUBLIC_RECORD_KEYS = new Set([
  'request_id',
  'client_request_id',
  'status',
  'version',
  'request_payload_hash',
  'failure_kind',
  'request',
  'uncertainty_occurred_at',
  'reason',
  'requested_at',
  'expires_at',
  'decided_by',
  'decided_at',
  'decision_reason',
  'approval_scope',
  'authoritative_record',
  'approval_granted',
  'execution_enabled',
  'lock_release_allowed',
  'automatic_action_taken',
])
const PUBLIC_ENVELOPE_KEYS = new Set(['version', 'request'])
const STATUSES: readonly ScrapeUncertaintyReviewStatus[] = [
  'pending_review',
  'approved',
  'rejected',
  'revoked',
  'expired',
]
const ACTIONS: readonly ScrapeUncertaintyReviewAction[] = ['approve', 'reject', 'revoke']
const SCOPES: readonly ScrapeUncertaintyReviewScope[] = ['mine', 'reviewable']

type RpcClient = Pick<SupabaseClient, 'rpc'>
type PostgrestErrorLike = { code?: unknown; message?: unknown; details?: unknown; hint?: unknown }

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasExactKeys(value: Record<string, unknown>, allowed: ReadonlySet<string>): boolean {
  return Object.keys(value).length === allowed.size && Object.keys(value).every(key => allowed.has(key))
}

function unknownKey(value: Record<string, unknown>, allowed: ReadonlySet<string>): string | null {
  return Object.keys(value).find(key => !allowed.has(key)) ?? null
}

function isUuid(value: unknown): value is string {
  return typeof value === 'string' && UUID_PATTERN.test(value)
}

function isStatus(value: unknown): value is ScrapeUncertaintyReviewStatus {
  return typeof value === 'string' && (STATUSES as readonly string[]).includes(value)
}

function isAction(value: unknown): value is ScrapeUncertaintyReviewAction {
  return typeof value === 'string' && (ACTIONS as readonly string[]).includes(value)
}

function isFailureKind(value: unknown): value is ScrapeUncertaintyFailureKind {
  return value === 'monitoring' || value === 'client_stop'
}

function isPeriod(value: unknown): value is string {
  return typeof value === 'string' && PERIOD_PATTERN.test(value)
}

function isTimestamp(value: unknown): value is string {
  if (typeof value !== 'string' || value.length < 20 || value.length > 40 || CONTROL_CHARACTER_PATTERN.test(value)) return false
  return Number.isFinite(Date.parse(value))
}

function timestampsEqual(left: string, right: string): boolean {
  const leftTime = Date.parse(left)
  const rightTime = Date.parse(right)
  return Number.isFinite(leftTime) && leftTime === rightTime
}

function normalizeReason(value: unknown, minimumLength: number): string | null {
  if (typeof value !== 'string' || CONTROL_CHARACTER_PATTERN.test(value)) return null
  const normalized = value.trim().replace(/\s+/g, ' ')
  return normalized.length >= minimumLength && normalized.length <= 500 ? normalized : null
}

function parseRequestSnapshot(raw: unknown): ReviewValidationResult<ScrapeUncertaintyRequestSnapshot> {
  if (!isObject(raw)) return { ok: false, detail: 'request must be an object' }
  const extra = unknownKey(raw, SNAPSHOT_KEYS)
  if (extra) return { ok: false, detail: `unknown request snapshot key: ${extra}` }
  if (!hasExactKeys(raw, SNAPSHOT_KEYS)) return { ok: false, detail: 'all request snapshot fields are required' }
  if (!isPeriod(raw.start_period) || !isPeriod(raw.end_period) || raw.start_period > raw.end_period) {
    return { ok: false, detail: 'invalid period range' }
  }
  if (typeof raw.force_rescrape !== 'boolean') return { ok: false, detail: 'force_rescrape must be boolean' }
  return {
    ok: true,
    value: {
      start_period: raw.start_period,
      end_period: raw.end_period,
      force_rescrape: raw.force_rescrape,
    },
  }
}

export function validateReviewRequestId(value: unknown): ReviewValidationResult<string> {
  return isUuid(value)
    ? { ok: true, value }
    : { ok: false, detail: 'requestId must be a complete UUID' }
}

export function validateVerifiedUserId(value: unknown): ReviewValidationResult<string> {
  return isUuid(value)
    ? { ok: true, value }
    : { ok: false, detail: 'verified identity is unavailable' }
}

export function validateCreateReviewBody(raw: unknown): ReviewValidationResult<CreateScrapeUncertaintyReviewInput> {
  if (!isObject(raw)) return { ok: false, detail: 'request body must be an object' }
  const extra = unknownKey(raw, CREATE_KEYS)
  if (extra) return { ok: false, detail: `unknown request key: ${extra}` }
  if (!hasExactKeys(raw, CREATE_KEYS)) return { ok: false, detail: 'all create request fields are required' }
  if (!isUuid(raw.client_request_id)) return { ok: false, detail: 'client_request_id must be a complete UUID' }
  if (!isFailureKind(raw.failure_kind)) return { ok: false, detail: 'invalid failure_kind' }

  const snapshot = parseRequestSnapshot(raw.request)
  if (!snapshot.ok) return snapshot
  if (!isTimestamp(raw.uncertainty_occurred_at)) return { ok: false, detail: 'invalid uncertainty_occurred_at' }
  const reason = normalizeReason(raw.reason, 20)
  if (!reason) return { ok: false, detail: 'reason must be 20 to 500 characters without control characters' }

  if (!isObject(raw.acknowledgements)) return { ok: false, detail: 'acknowledgements must be an object' }
  const acknowledgementExtra = unknownKey(raw.acknowledgements, ACKNOWLEDGEMENT_KEYS)
  if (acknowledgementExtra) return { ok: false, detail: `unknown acknowledgement key: ${acknowledgementExtra}` }
  if (!hasExactKeys(raw.acknowledgements, ACKNOWLEDGEMENT_KEYS)
    || raw.acknowledgements.server_state_unverified !== true
    || raw.acknowledgements.no_unlock_or_retry !== true) {
    return { ok: false, detail: 'both safety acknowledgements are required' }
  }

  return {
    ok: true,
    value: {
      client_request_id: raw.client_request_id,
      failure_kind: raw.failure_kind,
      request: snapshot.value,
      uncertainty_occurred_at: raw.uncertainty_occurred_at,
      reason,
      acknowledgements: {
        server_state_unverified: true,
        no_unlock_or_retry: true,
      },
    },
  }
}

export function validateDecisionBody(raw: unknown): ReviewValidationResult<DecideScrapeUncertaintyReviewInput> {
  if (!isObject(raw)) return { ok: false, detail: 'request body must be an object' }
  const extra = unknownKey(raw, DECISION_KEYS)
  if (extra) return { ok: false, detail: `unknown request key: ${extra}` }
  if (!hasExactKeys(raw, DECISION_KEYS)) return { ok: false, detail: 'action, expected_version, and reason are required' }
  if (!isAction(raw.action)) return { ok: false, detail: 'action must be approve, reject, or revoke' }
  if (!Number.isSafeInteger(raw.expected_version) || (raw.expected_version as number) < 1) {
    return { ok: false, detail: 'expected_version must be a positive integer' }
  }
  const reason = normalizeReason(raw.reason, 20)
  if (!reason) return { ok: false, detail: 'reason must be 20 to 500 characters without control characters' }
  return {
    ok: true,
    value: {
      action: raw.action,
      expected_version: raw.expected_version as number,
      reason,
    },
  }
}

export function validateListReviewQuery(url: URL): ReviewValidationResult<ListScrapeUncertaintyReviewsInput> {
  for (const key of url.searchParams.keys()) {
    if (!LIST_QUERY_KEYS.has(key)) return { ok: false, detail: `unknown query key: ${key}` }
    if (url.searchParams.getAll(key).length !== 1) return { ok: false, detail: `duplicate query key: ${key}` }
  }
  const scopeValue = url.searchParams.get('scope') ?? 'mine'
  if (!(SCOPES as readonly string[]).includes(scopeValue)) return { ok: false, detail: 'scope must be mine or reviewable' }
  const limitValue = url.searchParams.get('limit')
  const limit = limitValue === null ? 20 : Number(limitValue)
  if (!/^\d+$/.test(limitValue ?? '20') || !Number.isSafeInteger(limit) || limit < 1 || limit > SCRAPE_UNCERTAINTY_REVIEW_LIST_LIMIT) {
    return { ok: false, detail: `limit must be an integer between 1 and ${SCRAPE_UNCERTAINTY_REVIEW_LIST_LIMIT}` }
  }
  return { ok: true, value: { scope: scopeValue as ScrapeUncertaintyReviewScope, limit } }
}

export async function readBoundedJsonBody(
  request: Request,
  maximumBytes = SCRAPE_UNCERTAINTY_REVIEW_BODY_LIMIT_BYTES,
): Promise<BoundedJsonResult> {
  const contentLength = request.headers.get('content-length')
  if (contentLength !== null && (!/^\d+$/.test(contentLength) || Number(contentLength) > maximumBytes)) {
    return { ok: false, detail: 'request body is too large' }
  }
  if (!request.body) return { ok: false, detail: 'invalid JSON body' }

  const reader = request.body.getReader()
  const chunks: Uint8Array[] = []
  let totalBytes = 0
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      totalBytes += value.byteLength
      if (totalBytes > maximumBytes) {
        await reader.cancel()
        return { ok: false, detail: 'request body is too large' }
      }
      chunks.push(value)
    }
  } catch {
    return { ok: false, detail: 'invalid JSON body' }
  }

  const bytes = new Uint8Array(totalBytes)
  let offset = 0
  for (const chunk of chunks) {
    bytes.set(chunk, offset)
    offset += chunk.byteLength
  }
  try {
    const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes)
    return { ok: true, value: JSON.parse(text) as unknown }
  } catch {
    return { ok: false, detail: 'invalid JSON body' }
  }
}

function nullableUuid(value: unknown): value is string | null {
  return value === null || isUuid(value)
}

function nullableTimestamp(value: unknown): value is string | null {
  return value === null || isTimestamp(value)
}

function nullableDecisionReason(value: unknown): value is string | null {
  return value === null
    || (typeof value === 'string' && value.length <= 500 && value.trim().length >= 20 && !CONTROL_CHARACTER_PATTERN.test(value))
}

export function projectReviewRecord(raw: unknown): ReviewValidationResult<ScrapeUncertaintyReviewRecord> {
  if (!isObject(raw)
    || !isUuid(raw.review_id)
    || !isUuid(raw.client_request_id)
    || !isStatus(raw.status)
    || !Number.isSafeInteger(raw.version)
    || (raw.version as number) < 1
    || typeof raw.request_payload_hash !== 'string'
    || !SHA256_PATTERN.test(raw.request_payload_hash)
    || !isFailureKind(raw.failure_kind)
    || !isPeriod(raw.start_period)
    || !isPeriod(raw.end_period)
    || raw.start_period > raw.end_period
    || typeof raw.force_rescrape !== 'boolean'
    || !isTimestamp(raw.uncertainty_occurred_at)
    || typeof raw.reason !== 'string'
    || raw.reason.length > 500
    || raw.reason.trim().length < 20
    || CONTROL_CHARACTER_PATTERN.test(raw.reason)
    || !isTimestamp(raw.requested_at)
    || !isTimestamp(raw.expires_at)
    || !nullableUuid(raw.decided_by)
    || !nullableTimestamp(raw.decided_at)
    || !nullableDecisionReason(raw.decision_reason)
    || raw.approval_scope !== 'review_only'
    || raw.authoritative !== true
    || raw.execution_enabled !== false
    || raw.lock_release_allowed !== false) {
    return { ok: false, detail: 'review backend returned an invalid record' }
  }

  const hasNoDecision = raw.decided_by === null && raw.decided_at === null && raw.decision_reason === null
  const hasCompleteActorDecision = raw.decided_by !== null && raw.decided_at !== null && raw.decision_reason !== null
  const hasCompleteExpiryDecision = raw.decided_at !== null && raw.decision_reason !== null
  if ((raw.status === 'pending_review' && !hasNoDecision)
    || (['approved', 'rejected', 'revoked'].includes(raw.status) && !hasCompleteActorDecision)
    || (raw.status === 'expired' && !hasCompleteExpiryDecision)) {
    return { ok: false, detail: 'review backend returned an invalid record' }
  }

  return {
    ok: true,
    value: {
      request_id: raw.review_id,
      client_request_id: raw.client_request_id,
      status: raw.status,
      version: raw.version as number,
      request_payload_hash: raw.request_payload_hash,
      failure_kind: raw.failure_kind,
      request: {
        start_period: raw.start_period,
        end_period: raw.end_period,
        force_rescrape: raw.force_rescrape,
      },
      uncertainty_occurred_at: raw.uncertainty_occurred_at,
      reason: raw.reason,
      requested_at: raw.requested_at,
      expires_at: raw.expires_at,
      decided_by: raw.decided_by,
      decided_at: raw.decided_at,
      decision_reason: raw.decision_reason,
      approval_scope: 'review_only',
      authoritative_record: true,
      approval_granted: raw.status === 'approved',
      execution_enabled: false,
      lock_release_allowed: false,
      automatic_action_taken: false,
    },
  }
}

export function buildServerReviewSubmission(
  localReview: PendingUncertaintyReview,
): CreateScrapeUncertaintyReviewInput {
  return {
    client_request_id: localReview.requestId,
    failure_kind: localReview.failureKind,
    request: {
      start_period: localReview.request.startPeriod,
      end_period: localReview.request.endPeriod,
      force_rescrape: localReview.request.forceRescrape,
    },
    uncertainty_occurred_at: localReview.uncertaintyOccurredAt,
    reason: localReview.reason,
    acknowledgements: {
      server_state_unverified: true,
      no_unlock_or_retry: true,
    },
  }
}

export function parsePublicReviewRecord(raw: unknown): ReviewValidationResult<ScrapeUncertaintyReviewRecord> {
  if (!isObject(raw) || !hasExactKeys(raw, PUBLIC_RECORD_KEYS)) {
    return { ok: false, detail: 'review response has an invalid shape' }
  }
  if (!isObject(raw.request) || !hasExactKeys(raw.request, SNAPSHOT_KEYS)) {
    return { ok: false, detail: 'review response has an invalid request snapshot' }
  }
  const projected = projectReviewRecord({
    ...raw,
    review_id: raw.request_id,
    start_period: raw.request.start_period,
    end_period: raw.request.end_period,
    force_rescrape: raw.request.force_rescrape,
    authoritative: raw.authoritative_record,
  })
  if (!projected.ok) return projected
  if (raw.approval_granted !== (projected.value.status === 'approved')
    || raw.automatic_action_taken !== false) {
    return { ok: false, detail: 'review response has unsafe safety flags' }
  }
  return projected
}

export function parseReviewResponseEnvelope(raw: unknown): ReviewValidationResult<ScrapeUncertaintyReviewRecord> {
  if (!isObject(raw) || !hasExactKeys(raw, PUBLIC_ENVELOPE_KEYS) || raw.version !== 1) {
    return { ok: false, detail: 'review response has an invalid envelope' }
  }
  return parsePublicReviewRecord(raw.request)
}

export function createServerReviewLocator(
  record: ScrapeUncertaintyReviewRecord,
): ScrapeUncertaintyReviewLocator {
  return {
    version: 1,
    requestId: record.request_id,
    clientRequestId: record.client_request_id,
    requestPayloadHash: record.request_payload_hash,
  }
}

export function parseServerReviewLocator(raw: unknown): ScrapeUncertaintyReviewLocator | null {
  if (!isObject(raw) || !hasExactKeys(raw, LOCATOR_KEYS) || raw.version !== 1) return null
  if (!isUuid(raw.requestId) || !isUuid(raw.clientRequestId)) return null
  if (typeof raw.requestPayloadHash !== 'string' || !SHA256_PATTERN.test(raw.requestPayloadHash)) return null
  return {
    version: 1,
    requestId: raw.requestId,
    clientRequestId: raw.clientRequestId,
    requestPayloadHash: raw.requestPayloadHash,
  }
}

export function serverRecordMatchesLocalReview(
  record: ScrapeUncertaintyReviewRecord,
  localReview: PendingUncertaintyReview,
): boolean {
  return record.client_request_id === localReview.requestId
    && record.failure_kind === localReview.failureKind
    && record.request.start_period === localReview.request.startPeriod
    && record.request.end_period === localReview.request.endPeriod
    && record.request.force_rescrape === localReview.request.forceRescrape
    && timestampsEqual(record.uncertainty_occurred_at, localReview.uncertaintyOccurredAt)
    && record.reason === localReview.reason
    && record.approval_scope === 'review_only'
    && record.execution_enabled === false
    && record.lock_release_allowed === false
    && record.automatic_action_taken === false
}

export function serverRecordMatchesLocator(
  record: ScrapeUncertaintyReviewRecord,
  locator: ScrapeUncertaintyReviewLocator,
): boolean {
  return record.request_id === locator.requestId
    && record.client_request_id === locator.clientRequestId
    && record.request_payload_hash === locator.requestPayloadHash
}

function unwrapSingleRecord(data: unknown): unknown | null | 'invalid' {
  if (Array.isArray(data)) return data.length === 0 ? null : data.length === 1 ? data[0] : 'invalid'
  return data === null || data === undefined ? null : data
}

function classifyRpcError(error: PostgrestErrorLike, operation: 'create' | 'get' | 'list' | 'transition'): ReviewRpcFailure {
  const code = typeof error.code === 'string' ? error.code : ''
  const classifier = [error.message, error.details, error.hint]
    .filter((part): part is string => typeof part === 'string')
    .join(' ')
    .toLowerCase()

  if (code === 'P0002' || code === 'PGRST116' || /(?:review|request)[_ -]?not[_ -]?found/.test(classifier)) {
    return { ok: false, status: 404, detail: 'review request not found' }
  }
  if (code === '42501' && /admin role required/.test(classifier)) {
    return { ok: false, status: 403, detail: 'Admin role required' }
  }
  if (code === '23505'
    || code === '40001'
    || code === '23P01'
    || code === '23514'
    || code === '22023'
    || code === '55000'
    || (code === '42501' && /requester|own review|only requester/.test(classifier))
    || /conflict|version[_ -]?mismatch|stale[_ -]?version|invalid[_ -]?transition|two[_ -]?person|same[_ -]?(?:owner|actor)|already[_ -]?(?:exists|decided)/.test(classifier)) {
    return { ok: false, status: 409, detail: 'review request conflicts with current state' }
  }
  return { ok: false, status: 503, detail: `review ${operation} backend unavailable` }
}

async function callRpc(client: RpcClient, name: string, args: Record<string, unknown>) {
  try {
    return await client.rpc(name, args)
  } catch {
    return { data: null, error: { code: 'RPC_UNAVAILABLE' } }
  }
}

export async function createReviewViaRpc(
  client: RpcClient,
  actorUserId: string,
  input: CreateScrapeUncertaintyReviewInput,
): Promise<ReviewRpcResult<ScrapeUncertaintyReviewRecord>> {
  const result = await callRpc(client, 'create_scrape_uncertainty_review', {
    p_actor_user_id: actorUserId,
    p_client_request_id: input.client_request_id,
    p_failure_kind: input.failure_kind,
    p_start_period: input.request.start_period,
    p_end_period: input.request.end_period,
    p_force_rescrape: input.request.force_rescrape,
    p_uncertainty_occurred_at: input.uncertainty_occurred_at,
    p_reason: input.reason,
    p_server_state_unverified: input.acknowledgements.server_state_unverified,
    p_no_unlock_or_retry: input.acknowledgements.no_unlock_or_retry,
  })
  if (result.error) return classifyRpcError(result.error, 'create')
  const raw = unwrapSingleRecord(result.data)
  if (raw === null || raw === 'invalid') return { ok: false, status: 502, detail: 'review backend returned an invalid record' }
  const projected = projectReviewRecord(raw)
  return projected.ok ? projected : { ok: false, status: 502, detail: projected.detail }
}

export async function getReviewViaRpc(
  client: RpcClient,
  actorUserId: string,
  requestId: string,
): Promise<ReviewRpcResult<ScrapeUncertaintyReviewRecord>> {
  const result = await callRpc(client, 'get_scrape_uncertainty_review', {
    p_actor_user_id: actorUserId,
    p_review_id: requestId,
  })
  if (result.error) return classifyRpcError(result.error, 'get')
  const raw = unwrapSingleRecord(result.data)
  if (raw === null) return { ok: false, status: 404, detail: 'review request not found' }
  if (raw === 'invalid') return { ok: false, status: 502, detail: 'review backend returned an invalid record' }
  const projected = projectReviewRecord(raw)
  return projected.ok ? projected : { ok: false, status: 502, detail: projected.detail }
}

export async function listReviewsViaRpc(
  client: RpcClient,
  actorUserId: string,
  input: ListScrapeUncertaintyReviewsInput,
): Promise<ReviewRpcResult<ScrapeUncertaintyReviewRecord[]>> {
  const result = await callRpc(client, 'list_scrape_uncertainty_reviews', {
    p_actor_user_id: actorUserId,
    p_scope: input.scope,
    p_limit: input.limit,
  })
  if (result.error) return classifyRpcError(result.error, 'list')
  if (!Array.isArray(result.data)) return { ok: false, status: 502, detail: 'review backend returned an invalid list' }
  const reviews: ScrapeUncertaintyReviewRecord[] = []
  for (const raw of result.data) {
    const projected = projectReviewRecord(raw)
    if (!projected.ok) return { ok: false, status: 502, detail: projected.detail }
    reviews.push(projected.value)
  }
  return { ok: true, value: reviews }
}

export async function transitionReviewViaRpc(
  client: RpcClient,
  actorUserId: string,
  requestId: string,
  input: DecideScrapeUncertaintyReviewInput,
): Promise<ReviewRpcResult<ScrapeUncertaintyReviewRecord>> {
  const result = await callRpc(client, 'transition_scrape_uncertainty_review', {
    p_actor_user_id: actorUserId,
    p_review_id: requestId,
    p_expected_version: input.expected_version,
    p_action: input.action,
    p_reason: input.reason,
  })
  if (result.error) return classifyRpcError(result.error, 'transition')
  const raw = unwrapSingleRecord(result.data)
  if (raw === null || raw === 'invalid') return { ok: false, status: 502, detail: 'review backend returned an invalid record' }
  const projected = projectReviewRecord(raw)
  return projected.ok ? projected : { ok: false, status: 502, detail: projected.detail }
}
