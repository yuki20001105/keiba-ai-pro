import type { SupabaseClient } from '@supabase/supabase-js'
import {
  canonicalRetrainPayloadHash,
  parseRetrainApprovalRecord,
  parseRetrainDryRunPayload,
} from '@/lib/model-retrain-approval-contract'
import type {
  RetrainApprovalAllowedAction,
  RetrainApprovalExecutionPolicy,
  RetrainApprovalRecord,
  RetrainDryRunPayload,
} from '@/lib/model-retrain-approval-types'

export const MODEL_RETRAIN_APPROVAL_BODY_LIMIT_BYTES = 256 * 1024

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const CREATE_KEYS = new Set(['dry_run_payload', 'execution_policy', 'allowed_actions'])
const DECISION_KEYS = new Set(['action', 'expected_version', 'reason'])
const EXECUTION_POLICIES = new Set<RetrainApprovalExecutionPolicy>([
  'read-only-preview',
  'staging-train',
  'sandbox-train',
])
const ALLOWED_ACTIONS = new Set<RetrainApprovalAllowedAction>([
  'submit_approved_retrain',
  'view_approval_status',
  'view_job_status',
])

type RpcClient = Pick<SupabaseClient, 'rpc'>
type JsonObject = Record<string, unknown>

export type CreateModelRetrainApprovalInput = {
  dry_run_payload: RetrainDryRunPayload
  execution_policy: RetrainApprovalExecutionPolicy
  allowed_actions: RetrainApprovalAllowedAction[]
}

export type ModelRetrainApprovalDecisionInput = {
  action: 'approve' | 'reject' | 'revoke'
  expected_version: number
  reason: string
}

export type ModelRetrainApprovalLedgerRecord = {
  approval_record: RetrainApprovalRecord
  dry_run_payload: RetrainDryRunPayload
  record_version: number
  authoritative_record: true
  execution_enabled: false
  job_created: false
}

export type LedgerValidationResult<T> =
  | { ok: true; value: T }
  | { ok: false; detail: string }

export type LedgerRpcResult<T> =
  | { ok: true; value: T }
  | { ok: false; status: 403 | 404 | 409 | 502 | 503; detail: string }

function isObject(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasExactKeys(value: JsonObject, keys: ReadonlySet<string>): boolean {
  return Object.keys(value).length === keys.size && Object.keys(value).every(key => keys.has(key))
}

function isBoundedReason(value: unknown): value is string {
  return typeof value === 'string'
    && value.trim().length >= 20
    && value.trim().length <= 500
    && !/[\x00-\x1f\x7f]/.test(value)
}

function isExecutionPolicy(value: unknown): value is RetrainApprovalExecutionPolicy {
  return typeof value === 'string'
    && EXECUTION_POLICIES.has(value as RetrainApprovalExecutionPolicy)
}

function parseAllowedActions(value: unknown): RetrainApprovalAllowedAction[] | null {
  if (!Array.isArray(value) || value.length < 1 || value.length > ALLOWED_ACTIONS.size) return null
  if (value.some(item => typeof item !== 'string' || !ALLOWED_ACTIONS.has(item as RetrainApprovalAllowedAction))) {
    return null
  }
  const actions = value as RetrainApprovalAllowedAction[]
  if (new Set(actions).size !== actions.length || !actions.includes('view_approval_status')) return null
  return [...actions].sort() as RetrainApprovalAllowedAction[]
}

export async function readModelRetrainApprovalJsonBody(
  request: Request,
): Promise<LedgerValidationResult<unknown>> {
  const contentLength = Number(request.headers.get('content-length') || 0)
  if (Number.isFinite(contentLength) && contentLength > MODEL_RETRAIN_APPROVAL_BODY_LIMIT_BYTES) {
    return { ok: false, detail: 'request body is too large' }
  }
  try {
    const raw = await request.text()
    if (Buffer.byteLength(raw, 'utf8') > MODEL_RETRAIN_APPROVAL_BODY_LIMIT_BYTES) {
      return { ok: false, detail: 'request body is too large' }
    }
    return { ok: true, value: JSON.parse(raw) as unknown }
  } catch {
    return { ok: false, detail: 'invalid JSON body' }
  }
}

export function validateCreateModelRetrainApprovalBody(
  value: unknown,
  actorUserId: string,
): LedgerValidationResult<CreateModelRetrainApprovalInput> {
  if (!UUID_RE.test(actorUserId)) return { ok: false, detail: 'verified actor is invalid' }
  if (!isObject(value) || !hasExactKeys(value, CREATE_KEYS)) {
    return { ok: false, detail: 'approval request schema is invalid' }
  }
  const payload = parseRetrainDryRunPayload(value.dry_run_payload)
  if (!payload || payload.state !== 'preview-ready') {
    return { ok: false, detail: 'approval requires a complete preview-ready payload' }
  }
  if (payload.created_by !== actorUserId) {
    return { ok: false, detail: 'approval requester does not match dry-run creator' }
  }
  const generatedAt = new Date(payload.generated_at).getTime()
  const now = Date.now()
  if (generatedAt < now - 24 * 60 * 60 * 1000 || generatedAt > now + 5 * 60 * 1000) {
    return { ok: false, detail: 'dry-run preview is outside the approval freshness window' }
  }
  if (!isExecutionPolicy(value.execution_policy)) {
    return { ok: false, detail: 'execution policy is invalid' }
  }
  const actions = parseAllowedActions(value.allowed_actions)
  if (!actions) return { ok: false, detail: 'allowed actions are invalid' }
  if (
    value.execution_policy === 'read-only-preview'
      ? actions.includes('submit_approved_retrain') || actions.includes('view_job_status')
      : !actions.includes('submit_approved_retrain') || !actions.includes('view_job_status')
  ) {
    return { ok: false, detail: 'allowed actions do not match execution policy' }
  }
  return {
    ok: true,
    value: {
      dry_run_payload: payload,
      execution_policy: value.execution_policy,
      allowed_actions: actions,
    },
  }
}

export function validateModelRetrainApprovalDecisionBody(
  value: unknown,
): LedgerValidationResult<ModelRetrainApprovalDecisionInput> {
  if (!isObject(value) || !hasExactKeys(value, DECISION_KEYS)) {
    return { ok: false, detail: 'approval decision schema is invalid' }
  }
  if (value.action !== 'approve' && value.action !== 'reject' && value.action !== 'revoke') {
    return { ok: false, detail: 'approval decision action is invalid' }
  }
  if (!Number.isInteger(value.expected_version) || Number(value.expected_version) < 1) {
    return { ok: false, detail: 'approval decision version is invalid' }
  }
  if (!isBoundedReason(value.reason)) {
    return { ok: false, detail: 'approval decision reason is invalid' }
  }
  return {
    ok: true,
    value: {
      action: value.action,
      expected_version: Number(value.expected_version),
      reason: value.reason.trim().replace(/\s+/g, ' '),
    },
  }
}

export function validateModelRetrainApprovalId(value: string): LedgerValidationResult<string> {
  return UUID_RE.test(value)
    ? { ok: true, value: value.toLowerCase() }
    : { ok: false, detail: 'approval id is invalid' }
}

function normalizeTimestamp(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const parsed = new Date(value)
  return Number.isFinite(parsed.getTime()) ? parsed.toISOString() : null
}

function unwrapSingleRecord(value: unknown): JsonObject | null | 'invalid' {
  if (Array.isArray(value)) {
    if (value.length === 0) return null
    if (value.length !== 1 || !isObject(value[0])) return 'invalid'
    return value[0]
  }
  return isObject(value) ? value : value == null ? null : 'invalid'
}

function projectLedgerRecord(value: JsonObject): LedgerValidationResult<ModelRetrainApprovalLedgerRecord> {
  const dryRun = parseRetrainDryRunPayload(value.dry_run_payload)
  const requestedAt = normalizeTimestamp(value.requested_at)
  const expiresAt = normalizeTimestamp(value.expires_at)
  const approvedAt = value.approved_at === null ? null : normalizeTimestamp(value.approved_at)
  const recordVersion = Number(value.record_version)
  if (!dryRun || !requestedAt || !expiresAt || (value.approved_at !== null && !approvedAt)) {
    return { ok: false, detail: 'approval backend returned invalid bound data' }
  }
  const approval = parseRetrainApprovalRecord({
    approval_id: value.approval_id,
    dry_run_id: value.dry_run_id,
    approved_by: value.approved_by,
    approved_at: approvedAt,
    approval_status: value.approval_status,
    approval_comment: value.approval_comment,
    approved_payload_hash: value.approved_payload_hash,
    requested_by: value.requested_by,
    requested_at: requestedAt,
    expires_at: expiresAt,
    invalidation_reason: value.invalidation_reason,
    execution_policy: value.execution_policy,
    allowed_actions: value.allowed_actions,
  })
  if (
    !approval
    || !Number.isInteger(recordVersion)
    || recordVersion < 1
    || value.authoritative_record !== true
    || value.execution_enabled !== false
    || value.job_created !== false
    || dryRun.dry_run_id !== approval.dry_run_id
    || dryRun.created_by !== approval.requested_by
    || canonicalRetrainPayloadHash(dryRun) !== approval.approved_payload_hash
  ) {
    return { ok: false, detail: 'approval backend returned an invalid record' }
  }
  return {
    ok: true,
    value: {
      approval_record: approval,
      dry_run_payload: dryRun,
      record_version: recordVersion,
      authoritative_record: true,
      execution_enabled: false,
      job_created: false,
    },
  }
}

function classifyRpcError(error: unknown, operation: string): LedgerRpcResult<never> {
  const raw = isObject(error) ? error : {}
  const code = typeof raw.code === 'string' ? raw.code : ''
  const text = [raw.message, raw.details, raw.hint].filter(item => typeof item === 'string').join(' ').toLowerCase()
  if (code === 'P0002' || code === 'PGRST116' || /approval.*not found/.test(text)) {
    return { ok: false, status: 404, detail: 'model retrain approval not found' }
  }
  if (code === '42501' && /admin role required/.test(text)) {
    return { ok: false, status: 403, detail: 'Admin role required' }
  }
  if (
    ['23505', '40001', '23514', '22023', '55000'].includes(code)
    || /conflict|version|self.?approval|own approval|not pending|expired|invalid transition/.test(text)
  ) {
    return { ok: false, status: 409, detail: 'model retrain approval conflicts with current state' }
  }
  return { ok: false, status: 503, detail: `model retrain approval ${operation} backend unavailable` }
}

async function callRpc(client: RpcClient, name: string, args: Record<string, unknown>) {
  try {
    return await client.rpc(name, args)
  } catch {
    return { data: null, error: { code: 'RPC_UNAVAILABLE' } }
  }
}

function projectRpcResult(
  data: unknown,
  error: unknown,
  operation: string,
): LedgerRpcResult<ModelRetrainApprovalLedgerRecord> {
  if (error) return classifyRpcError(error, operation)
  const raw = unwrapSingleRecord(data)
  if (raw === null) return { ok: false, status: 404, detail: 'model retrain approval not found' }
  if (raw === 'invalid') return { ok: false, status: 502, detail: 'approval backend returned an invalid record' }
  const projected = projectLedgerRecord(raw)
  return projected.ok ? projected : { ok: false, status: 502, detail: projected.detail }
}

export async function createModelRetrainApprovalViaRpc(
  client: RpcClient,
  actorUserId: string,
  input: CreateModelRetrainApprovalInput,
): Promise<LedgerRpcResult<ModelRetrainApprovalLedgerRecord>> {
  const payloadHash = canonicalRetrainPayloadHash(input.dry_run_payload)
  if (!payloadHash) return { ok: false, status: 409, detail: 'model retrain payload is invalid' }
  const result = await callRpc(client, 'create_model_retrain_approval', {
    p_actor_user_id: actorUserId,
    p_dry_run_id: input.dry_run_payload.dry_run_id,
    p_approved_payload_hash: payloadHash,
    p_dry_run_payload: input.dry_run_payload,
    p_execution_policy: input.execution_policy,
    p_allowed_actions: input.allowed_actions,
  })
  const projected = projectRpcResult(result.data, result.error, 'create')
  if (!projected.ok) return projected
  if (
    projected.value.approval_record.dry_run_id !== input.dry_run_payload.dry_run_id
    || projected.value.approval_record.requested_by !== actorUserId
    || projected.value.approval_record.approved_payload_hash !== payloadHash
    || canonicalRetrainPayloadHash(projected.value.dry_run_payload) !== payloadHash
    || projected.value.approval_record.execution_policy !== input.execution_policy
    || JSON.stringify(projected.value.approval_record.allowed_actions) !== JSON.stringify(input.allowed_actions)
  ) {
    return { ok: false, status: 502, detail: 'approval backend returned an uncorrelated create record' }
  }
  return projected
}

export async function getModelRetrainApprovalViaRpc(
  client: RpcClient,
  actorUserId: string,
  approvalId: string,
): Promise<LedgerRpcResult<ModelRetrainApprovalLedgerRecord>> {
  const result = await callRpc(client, 'get_model_retrain_approval', {
    p_actor_user_id: actorUserId,
    p_approval_id: approvalId,
  })
  return projectRpcResult(result.data, result.error, 'read')
}

export async function transitionModelRetrainApprovalViaRpc(
  client: RpcClient,
  actorUserId: string,
  approvalId: string,
  input: ModelRetrainApprovalDecisionInput,
): Promise<LedgerRpcResult<ModelRetrainApprovalLedgerRecord>> {
  const result = await callRpc(client, 'transition_model_retrain_approval', {
    p_actor_user_id: actorUserId,
    p_approval_id: approvalId,
    p_expected_version: input.expected_version,
    p_action: input.action,
    p_reason: input.reason,
  })
  const projected = projectRpcResult(result.data, result.error, 'transition')
  if (!projected.ok) return projected
  const expectedStatus = input.action === 'approve'
    ? 'approved'
    : input.action === 'reject'
      ? 'rejected'
      : 'invalidated'
  if (
    projected.value.approval_record.approval_id !== approvalId
    || projected.value.record_version !== input.expected_version + 1
    || projected.value.approval_record.approval_status !== expectedStatus
  ) {
    return { ok: false, status: 502, detail: 'approval backend returned an uncorrelated transition' }
  }
  return projected
}
