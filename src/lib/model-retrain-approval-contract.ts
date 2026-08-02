import { createHash } from 'node:crypto'
import type {
  ApprovedRetrainJobPreconditions,
  RetrainApprovalAllowedAction,
  RetrainApprovalRecord,
  RetrainDryRunPayload,
} from '@/lib/model-retrain-approval-types'

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const DIGEST_RE = /^[0-9a-f]{64}$/
const COMMIT_RE = /^[0-9a-f]{40}$/
const IDENTIFIER_RE = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/
const DATE_RE = /^\d{8}$/
const MAX_FEATURES = 2_048

export const MODEL_FUTURE_FIELDS = new Set([
  'time_seconds',
  'finish_time',
  'last_3f',
  'last_3f_time',
  'last_3f_rank',
  'last_3f_rank_normalized',
  'corner_1',
  'corner_2',
  'corner_3',
  'corner_4',
  'corner_positions',
  'corner_positions_list',
  'corner_position_avg',
  'corner_position_variance',
  'last_corner_position',
  'position_change',
  'margin',
  'prize_money',
  'finish',
  'finish_position',
  'actual_finish',
])

const PAYLOAD_KEYS = new Set([
  'dry_run_id',
  'generated_at',
  'target',
  'model_type',
  'train_period',
  'validation_period',
  'feature_count',
  'selected_features',
  'removed_features',
  'expected_outputs',
  'estimated_runtime',
  'safety_checks',
  'source_model_id',
  'active_model_id',
  'feature_contract_hash',
  'data_snapshot_id',
  'code_version',
  'git_commit',
  'created_by',
  'state',
  'warnings',
  'notes',
])
const PERIOD_KEYS = new Set(['start', 'end'])
const RUNTIME_KEYS = new Set(['unit', 'min', 'max', 'note'])
const SAFETY_KEYS = new Set(['key', 'status', 'note'])
const APPROVAL_KEYS = new Set([
  'approval_id',
  'dry_run_id',
  'approved_by',
  'approved_at',
  'approval_status',
  'approval_comment',
  'approved_payload_hash',
  'requested_by',
  'requested_at',
  'expires_at',
  'invalidation_reason',
  'execution_policy',
  'allowed_actions',
])
const ALLOWED_ACTIONS = new Set<RetrainApprovalAllowedAction>([
  'submit_approved_retrain',
  'view_approval_status',
  'view_job_status',
])
const EXPECTED_OUTPUTS = new Set([
  'model-artifact',
  'model-metadata',
  'acceptance-observations',
  'evaluation-report',
  'comparison-report',
])
const REQUIRED_SAFETY_CHECKS = new Set([
  'future_field_exclusion',
  'out_of_time_split',
  'active_model_immutable',
  'production_write_blocked',
  'path_input_rejected',
])

type JsonObject = Record<string, unknown>

export type RetrainEligibilityContext = {
  now: string
  actor_id: string
  actor_is_admin: boolean
  current_active_model_id: string | null
  current_feature_contract_hash: string
  current_code_version: string
  current_git_commit: string
  production_write_blocked: boolean
  artifact_write_policy: 'disabled' | 'staging-train' | 'sandbox-train'
}

export type RetrainEligibilityAssessment = {
  success: true
  state: 'pass' | 'fail'
  code: 'approved-retrain-eligible' | 'approved-retrain-ineligible'
  execution_eligible: boolean
  execution_performed: false
  payload_hash: string | null
  approval_id: string | null
  dry_run_id: string | null
  preconditions: ApprovedRetrainJobPreconditions
  failure_codes: string[]
}

export function computeFeatureContractHash(input: {
  target: string
  model_type: string
  selected_features: string[]
  removed_features: string[]
}): string | null {
  if (!IDENTIFIER_RE.test(input.target) || !IDENTIFIER_RE.test(input.model_type)) return null
  const selected = parseIdentifierArray(input.selected_features)
  const removed = parseIdentifierArray(input.removed_features)
  if (!selected || selected.length === 0 || !removed) return null
  if (removed.some(feature => !selected.includes(feature))) return null
  const contract = {
    model_type: input.model_type,
    removed_features: [...removed].sort(),
    selected_features: [...selected].sort(),
    target: input.target,
  }
  return createHash('sha256').update(JSON.stringify(contract), 'utf8').digest('hex')
}

function isObject(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasExactKeys(value: JsonObject, keys: Set<string>): boolean {
  const actual = Object.keys(value)
  return actual.length === keys.size && actual.every(key => keys.has(key))
}

function hasAllowedKeys(value: JsonObject, keys: Set<string>, required: string[]): boolean {
  return Object.keys(value).every(key => keys.has(key)) && required.every(key => key in value)
}

function isStrictTimestamp(value: unknown): value is string {
  if (typeof value !== 'string' || value.length < 20 || value.length > 32) return false
  const parsed = new Date(value)
  return Number.isFinite(parsed.getTime()) && parsed.toISOString() === value
}

function isBoundedText(value: unknown, max: number, allowEmpty = false): value is string {
  return typeof value === 'string'
    && (allowEmpty || value.length > 0)
    && value.length <= max
    && !/[\x00-\x1f\x7f]/.test(value)
}

function parsePeriod(value: unknown): { start: string | null; end: string | null } | null {
  if (!isObject(value) || !hasExactKeys(value, PERIOD_KEYS)) return null
  const { start, end } = value
  if (start !== null && !isDateToken(start)) return null
  if (end !== null && !isDateToken(end)) return null
  if (start !== null && end !== null && start > end) return null
  return { start: start as string | null, end: end as string | null }
}

function isDateToken(value: unknown): value is string {
  if (typeof value !== 'string' || !DATE_RE.test(value)) return false
  const year = Number(value.slice(0, 4))
  const month = Number(value.slice(4, 6))
  const day = Number(value.slice(6, 8))
  const parsed = new Date(Date.UTC(year, month - 1, day))
  return parsed.getUTCFullYear() === year
    && parsed.getUTCMonth() === month - 1
    && parsed.getUTCDate() === day
}

function parseIdentifierArray(value: unknown, max = MAX_FEATURES): string[] | null {
  if (!Array.isArray(value) || value.length > max) return null
  if (value.some(item => typeof item !== 'string' || !IDENTIFIER_RE.test(item))) return null
  const strings = value as string[]
  if (new Set(strings).size !== strings.length) return null
  return [...strings]
}

function parseTextArray(value: unknown, maxItems: number): string[] | null {
  if (!Array.isArray(value) || value.length > maxItems) return null
  if (value.some(item => !isBoundedText(item, 500))) return null
  return [...value] as string[]
}

export function parseRetrainDryRunPayload(value: unknown): RetrainDryRunPayload | null {
  const required = [...PAYLOAD_KEYS].filter(key => key !== 'warnings' && key !== 'notes')
  if (!isObject(value) || !hasAllowedKeys(value, PAYLOAD_KEYS, required)) return null
  if (typeof value.dry_run_id !== 'string' || !UUID_RE.test(value.dry_run_id)) return null
  if (!isStrictTimestamp(value.generated_at)) return null
  if (typeof value.target !== 'string' || !IDENTIFIER_RE.test(value.target)) return null
  if (typeof value.model_type !== 'string' || !IDENTIFIER_RE.test(value.model_type)) return null
  const trainPeriod = parsePeriod(value.train_period)
  const validationPeriod = parsePeriod(value.validation_period)
  if (!trainPeriod || !validationPeriod) return null
  if (
    trainPeriod.end !== null
    && validationPeriod.start !== null
    && trainPeriod.end >= validationPeriod.start
  ) return null

  const selectedFeatures = parseIdentifierArray(value.selected_features)
  const removedFeatures = parseIdentifierArray(value.removed_features)
  if (!selectedFeatures || selectedFeatures.length === 0 || !removedFeatures) return null
  if (removedFeatures.some(feature => !selectedFeatures.includes(feature))) return null
  const retainedCount = selectedFeatures.length - removedFeatures.length
  if (retainedCount < 1) return null
  if (typeNumber(value.feature_count) !== retainedCount) return null
  if (selectedFeatures.some(feature => MODEL_FUTURE_FIELDS.has(feature))) return null

  const expectedOutputs = parseIdentifierArray(value.expected_outputs, EXPECTED_OUTPUTS.size)
  if (
    !expectedOutputs
    || expectedOutputs.length !== EXPECTED_OUTPUTS.size
    || expectedOutputs.some(item => !EXPECTED_OUTPUTS.has(item))
  ) {
    return null
  }
  if (!isObject(value.estimated_runtime) || !hasExactKeys(value.estimated_runtime, RUNTIME_KEYS)) return null
  const runtime = value.estimated_runtime
  if (runtime.unit !== 'minute') return null
  if (typeof runtime.min !== 'number' || !Number.isFinite(runtime.min) || runtime.min < 0) return null
  if (typeof runtime.max !== 'number' || !Number.isFinite(runtime.max) || runtime.max < runtime.min) return null
  if (!isBoundedText(runtime.note, 500)) return null

  if (!Array.isArray(value.safety_checks) || value.safety_checks.length === 0 || value.safety_checks.length > 32) return null
  const safetyChecks = [] as RetrainDryRunPayload['safety_checks']
  const safetyKeys = new Set<string>()
  for (const item of value.safety_checks) {
    if (!isObject(item) || !hasExactKeys(item, SAFETY_KEYS)) return null
    if (typeof item.key !== 'string' || !IDENTIFIER_RE.test(item.key) || safetyKeys.has(item.key)) return null
    if (item.status !== 'pass' && item.status !== 'warn' && item.status !== 'fail') return null
    if (!isBoundedText(item.note, 500)) return null
    safetyKeys.add(item.key)
    safetyChecks.push({ key: item.key, status: item.status, note: item.note })
  }
  if ([...REQUIRED_SAFETY_CHECKS].some(key => !safetyKeys.has(key))) return null

  for (const key of ['source_model_id', 'active_model_id'] as const) {
    if (value[key] !== null && (typeof value[key] !== 'string' || !IDENTIFIER_RE.test(value[key]))) return null
  }
  if (typeof value.feature_contract_hash !== 'string' || !DIGEST_RE.test(value.feature_contract_hash)) return null
  if (value.feature_contract_hash !== computeFeatureContractHash({
    target: value.target,
    model_type: value.model_type,
    selected_features: selectedFeatures,
    removed_features: removedFeatures,
  })) return null
  if (typeof value.data_snapshot_id !== 'string' || !DIGEST_RE.test(value.data_snapshot_id)) return null
  if (typeof value.code_version !== 'string' || !IDENTIFIER_RE.test(value.code_version)) return null
  if (typeof value.git_commit !== 'string' || !COMMIT_RE.test(value.git_commit)) return null
  if (typeof value.created_by !== 'string' || !UUID_RE.test(value.created_by)) return null
  if (value.state !== 'preview-ready' && value.state !== 'preview-warn' && value.state !== 'preview-fail') return null
  const derivedState = safetyChecks.some(check => check.status === 'fail')
    ? 'preview-fail'
    : safetyChecks.some(check => check.status === 'warn')
      ? 'preview-warn'
      : 'preview-ready'
  if (value.state !== derivedState) return null
  const snapshotPlaceholder = value.data_snapshot_id === '0'.repeat(64)
  const commitPlaceholder = value.git_commit === '0'.repeat(40)
  if (
    snapshotPlaceholder
    && !safetyChecks.some(check => check.key === 'data_snapshot_bound' && check.status === 'fail')
  ) return null
  if (
    commitPlaceholder
    && !safetyChecks.some(check => check.key === 'candidate_commit_bound' && check.status === 'fail')
  ) return null
  if (value.state === 'preview-ready' && (snapshotPlaceholder || commitPlaceholder)) return null
  const warnings = value.warnings === undefined ? undefined : parseTextArray(value.warnings, 32)
  const notes = value.notes === undefined ? undefined : parseTextArray(value.notes, 32)
  if (value.warnings !== undefined && !warnings) return null
  if (value.notes !== undefined && !notes) return null

  return {
    dry_run_id: value.dry_run_id,
    generated_at: value.generated_at,
    target: value.target,
    model_type: value.model_type,
    train_period: trainPeriod,
    validation_period: validationPeriod,
    feature_count: retainedCount,
    selected_features: selectedFeatures,
    removed_features: removedFeatures,
    expected_outputs: expectedOutputs,
    estimated_runtime: {
      unit: 'minute',
      min: runtime.min,
      max: runtime.max,
      note: runtime.note,
    },
    safety_checks: safetyChecks,
    source_model_id: value.source_model_id as string | null,
    active_model_id: value.active_model_id as string | null,
    feature_contract_hash: value.feature_contract_hash,
    data_snapshot_id: value.data_snapshot_id,
    code_version: value.code_version,
    git_commit: value.git_commit,
    created_by: value.created_by,
    state: value.state,
    ...(warnings ? { warnings } : {}),
    ...(notes ? { notes } : {}),
  }
}

function typeNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null
}

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalize)
  if (!isObject(value)) return value
  return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonicalize(value[key])]))
}

export function canonicalRetrainPayloadHash(value: unknown): string | null {
  const payload = parseRetrainDryRunPayload(value)
  if (!payload) return null
  return createHash('sha256').update(JSON.stringify(canonicalize(payload)), 'utf8').digest('hex')
}

export function parseRetrainApprovalRecord(value: unknown): RetrainApprovalRecord | null {
  if (!isObject(value) || !hasExactKeys(value, APPROVAL_KEYS)) return null
  if (typeof value.approval_id !== 'string' || !UUID_RE.test(value.approval_id)) return null
  if (typeof value.dry_run_id !== 'string' || !UUID_RE.test(value.dry_run_id)) return null
  if (value.approved_by !== null && (typeof value.approved_by !== 'string' || !UUID_RE.test(value.approved_by))) return null
  if (value.approved_at !== null && !isStrictTimestamp(value.approved_at)) return null
  const statuses = new Set(['pending', 'approved', 'rejected', 'expired', 'invalidated'])
  if (typeof value.approval_status !== 'string' || !statuses.has(value.approval_status)) return null
  if (!isBoundedText(value.approval_comment, 500, true)) return null
  if (typeof value.approved_payload_hash !== 'string' || !DIGEST_RE.test(value.approved_payload_hash)) return null
  if (typeof value.requested_by !== 'string' || !UUID_RE.test(value.requested_by)) return null
  if (!isStrictTimestamp(value.requested_at) || !isStrictTimestamp(value.expires_at)) return null
  if (value.requested_at >= value.expires_at) return null
  const reasons = new Set([
    'payload-mismatch', 'hash-mismatch', 'expired', 'active-model-changed',
    'feature-contract-changed', 'code-version-changed', 'manual-invalidation', 'other',
  ])
  if (value.invalidation_reason !== null && (typeof value.invalidation_reason !== 'string' || !reasons.has(value.invalidation_reason))) return null
  if (value.execution_policy !== 'read-only-preview' && value.execution_policy !== 'staging-train' && value.execution_policy !== 'sandbox-train') return null
  const actions = parseIdentifierArray(value.allowed_actions, ALLOWED_ACTIONS.size)
  if (!actions || actions.some(action => !ALLOWED_ACTIONS.has(action as RetrainApprovalAllowedAction))) return null
  if (value.approval_status === 'pending' && (value.approved_by !== null || value.approved_at !== null)) return null
  if (value.approval_status === 'pending' && value.invalidation_reason !== null) return null
  if (value.approval_status === 'approved' && (value.approved_by === null || value.approved_at === null || value.invalidation_reason !== null)) return null
  if (
    value.approval_status === 'approved'
    && (
      value.approved_by === value.requested_by
      || value.approved_at! < value.requested_at
      || value.approved_at! > value.expires_at
    )
  ) return null
  if ((value.approval_status === 'expired' || value.approval_status === 'invalidated') && value.invalidation_reason === null) return null
  return value as unknown as RetrainApprovalRecord
}

export function assessApprovedRetrainEligibility(input: {
  payload: unknown
  approval: unknown
  context: RetrainEligibilityContext
}): RetrainEligibilityAssessment {
  const payload = parseRetrainDryRunPayload(input.payload)
  const approval = parseRetrainApprovalRecord(input.approval)
  const payloadHash = payload ? canonicalRetrainPayloadHash(payload) : null
  const nowValid = isStrictTimestamp(input.context.now)
  const actorValid = UUID_RE.test(input.context.actor_id)
  const currentFeatureHashValid = DIGEST_RE.test(input.context.current_feature_contract_hash)
  const currentCommitValid = COMMIT_RE.test(input.context.current_git_commit)
  const failures: string[] = []
  const append = (condition: boolean, code: string) => {
    if (!condition && !failures.includes(code)) failures.push(code)
  }

  append(payload !== null, 'dry-run-payload-invalid')
  append(approval !== null, 'approval-record-invalid')
  append(nowValid, 'assessment-time-invalid')
  append(actorValid, 'assessment-actor-invalid')
  append(currentFeatureHashValid, 'current-feature-contract-invalid')
  append(IDENTIFIER_RE.test(input.context.current_code_version), 'current-code-version-invalid')
  append(currentCommitValid, 'current-git-commit-invalid')

  const approvalStatus = approval?.approval_status === 'approved'
  const payloadHashMatches = payload !== null && approval !== null && payloadHash === approval.approved_payload_hash
  const approvalNotExpired = approval !== null
    && nowValid
    && input.context.now <= approval.expires_at
    && (approval.approved_at === null || input.context.now >= approval.approved_at)
  const activeModelUnchanged = payload !== null
    && input.context.current_active_model_id === payload.active_model_id
  const featureContractUnchanged = payload !== null
    && currentFeatureHashValid
    && input.context.current_feature_contract_hash === payload.feature_contract_hash
  const codeVersionUnchanged = payload !== null
    && currentCommitValid
    && input.context.current_code_version === payload.code_version
    && input.context.current_git_commit === payload.git_commit
  const adminAllowed = actorValid && input.context.actor_is_admin
  const productionWriteBlocked = input.context.production_write_blocked === true
  const artifactWriteAllowed = approval !== null
    && approval.execution_policy !== 'read-only-preview'
    && input.context.artifact_write_policy === approval.execution_policy

  append(payload !== null && approval !== null && payload.dry_run_id === approval.dry_run_id, 'dry-run-approval-id-mismatch')
  append(payload !== null && approval !== null && payload.created_by === approval.requested_by, 'approval-requester-mismatch')
  append(payload !== null && approval !== null && payload.generated_at <= approval.requested_at, 'approval-before-preview')
  append(approvalStatus, 'approval-status-not-approved')
  append(payloadHashMatches, 'approved-payload-hash-mismatch')
  append(approvalNotExpired, 'approval-expired')
  append(activeModelUnchanged, 'active-model-changed')
  append(featureContractUnchanged, 'feature-contract-changed')
  append(codeVersionUnchanged, 'code-version-changed')
  append(adminAllowed, 'admin-role-required')
  append(productionWriteBlocked, 'production-write-not-blocked')
  append(artifactWriteAllowed, 'artifact-write-policy-invalid')
  append(payload?.state === 'preview-ready', 'dry-run-preview-not-ready')
  append(approval?.allowed_actions.includes('submit_approved_retrain') === true, 'submit-action-not-approved')

  const preconditions: ApprovedRetrainJobPreconditions = {
    approval_status: approval?.approval_status ?? 'invalidated',
    payload_hash_matches: payloadHashMatches,
    approval_not_expired: approvalNotExpired,
    active_model_unchanged: activeModelUnchanged,
    feature_contract_unchanged: featureContractUnchanged,
    code_version_unchanged: codeVersionUnchanged,
    admin_allowed: adminAllowed,
    production_write_blocked: productionWriteBlocked,
    artifact_write_allowed: artifactWriteAllowed,
  }
  const eligible = failures.length === 0
  return {
    success: true,
    state: eligible ? 'pass' : 'fail',
    code: eligible ? 'approved-retrain-eligible' : 'approved-retrain-ineligible',
    execution_eligible: eligible,
    execution_performed: false,
    payload_hash: payloadHash,
    approval_id: approval?.approval_id ?? null,
    dry_run_id: payload?.dry_run_id ?? null,
    preconditions,
    failure_codes: failures,
  }
}
