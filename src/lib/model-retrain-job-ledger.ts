import type { SupabaseClient } from '@supabase/supabase-js'

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const SHA256_RE = /^[0-9a-f]{64}$/
const CREATE_KEYS = new Set(['approval_id', 'expected_approval_version', 'approved_payload_hash'])

type RpcClient = Pick<SupabaseClient, 'rpc'>
type JsonObject = Record<string, unknown>

export type CreateModelRetrainJobInput = {
  approval_id: string
  expected_approval_version: number
  approved_payload_hash: string
}

export type ModelRetrainJobState = 'queued' | 'claimed' | 'running' | 'artifact-registered' | 'failed'

export type ModelRetrainJobRecord = {
  job_id: string
  approval_id: string
  dry_run_id: string
  approved_payload_hash: string
  submitted_by: string
  requested_by: string
  approved_by: string
  execution_policy: 'staging-train' | 'sandbox-train'
  job_state: ModelRetrainJobState
  submitted_at: string
  record_version: number
  authoritative_record: true
  execution_started: boolean
  artifact_written: boolean
  artifact_uri: string | null
  artifact_sha256: string | null
  artifact_size_bytes: number | null
  artifact_media_type: 'application/octet-stream' | 'application/x-python-serialized-object' | null
  artifact_registered_at: string | null
  worker_id: string | null
  fencing_token: number | null
  lease_expires_at: string | null
  claimed_at: string | null
  started_at: string | null
  finished_at: string | null
  failure_code: string | null
}

export type JobLedgerResult<T> =
  | { ok: true; value: T }
  | { ok: false; status: 403 | 404 | 409 | 502 | 503; detail: string }

export type JobValidationResult<T> =
  | { ok: true; value: T }
  | { ok: false; detail: string }

function isObject(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasExactKeys(value: JsonObject, keys: ReadonlySet<string>): boolean {
  return Object.keys(value).length === keys.size && Object.keys(value).every(key => keys.has(key))
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

export function validateCreateModelRetrainJobBody(
  value: unknown,
): JobValidationResult<CreateModelRetrainJobInput> {
  if (!isObject(value) || !hasExactKeys(value, CREATE_KEYS)) {
    return { ok: false, detail: 'model retrain job request schema is invalid' }
  }
  if (!UUID_RE.test(String(value.approval_id || ''))) {
    return { ok: false, detail: 'approval id is invalid' }
  }
  if (!Number.isInteger(value.expected_approval_version) || Number(value.expected_approval_version) < 1) {
    return { ok: false, detail: 'approval version is invalid' }
  }
  if (typeof value.approved_payload_hash !== 'string' || !SHA256_RE.test(value.approved_payload_hash)) {
    return { ok: false, detail: 'approved payload hash is invalid' }
  }
  return {
    ok: true,
    value: {
      approval_id: String(value.approval_id).toLowerCase(),
      expected_approval_version: Number(value.expected_approval_version),
      approved_payload_hash: value.approved_payload_hash,
    },
  }
}

export function validateModelRetrainJobId(value: string): JobValidationResult<string> {
  return UUID_RE.test(value)
    ? { ok: true, value: value.toLowerCase() }
    : { ok: false, detail: 'job id is invalid' }
}

function projectJob(value: JsonObject): JobValidationResult<ModelRetrainJobRecord> {
  const submittedAt = normalizeTimestamp(value.submitted_at)
  const leaseExpiresAt = value.lease_expires_at === null ? null : normalizeTimestamp(value.lease_expires_at)
  const claimedAt = value.claimed_at === null ? null : normalizeTimestamp(value.claimed_at)
  const startedAt = value.started_at === null ? null : normalizeTimestamp(value.started_at)
  const finishedAt = value.finished_at === null ? null : normalizeTimestamp(value.finished_at)
  const artifactRegisteredAt = value.artifact_registered_at === null
    ? null
    : normalizeTimestamp(value.artifact_registered_at)
  const recordVersion = Number(value.record_version)
  const fencingToken = value.fencing_token === null ? null : Number(value.fencing_token)
  const artifactSize = value.artifact_size_bytes === null ? null : Number(value.artifact_size_bytes)
  const state = value.job_state
  const workerId = value.worker_id
  const failureCode = value.failure_code
  const artifactUri = value.artifact_uri
  const artifactSha256 = value.artifact_sha256
  const artifactMediaType = value.artifact_media_type
  const artifactValid = state === 'artifact-registered'
    ? value.artifact_written === true
      && typeof artifactUri === 'string'
      && typeof artifactSha256 === 'string' && SHA256_RE.test(artifactSha256)
      && artifactUri === `models://retrain/${String(value.job_id).toLowerCase()}/${artifactSha256}.${artifactUri.split('.').pop()}`
      && /\.(joblib|pkl|bin)$/.test(artifactUri)
      && artifactSize !== null && Number.isSafeInteger(artifactSize)
      && artifactSize >= 1 && artifactSize <= 104_857_600
      && (artifactMediaType === 'application/octet-stream'
        || artifactMediaType === 'application/x-python-serialized-object')
      && artifactRegisteredAt !== null
    : value.artifact_written === false
      && artifactUri === null && artifactSha256 === null && artifactSize === null
      && artifactMediaType === null && artifactRegisteredAt === null
  const stateValid = (
    state === 'queued'
      ? workerId === null && fencingToken === null && leaseExpiresAt === null
        && claimedAt === null && startedAt === null && finishedAt === null
        && failureCode === null && value.execution_started === false
      : state === 'claimed'
        ? typeof workerId === 'string' && fencingToken !== null && leaseExpiresAt !== null
          && claimedAt !== null && startedAt === null && finishedAt === null
          && failureCode === null && value.execution_started === false
        : state === 'running'
          ? typeof workerId === 'string' && fencingToken !== null && leaseExpiresAt !== null
            && claimedAt !== null && startedAt !== null && finishedAt === null
            && failureCode === null && value.execution_started === true
          : state === 'artifact-registered'
            ? typeof workerId === 'string' && fencingToken !== null && leaseExpiresAt !== null
              && claimedAt !== null && startedAt !== null && finishedAt !== null
              && failureCode === null && value.execution_started === true
            : state === 'failed'
              ? typeof workerId === 'string' && fencingToken !== null && leaseExpiresAt !== null
                && claimedAt !== null && finishedAt !== null && typeof failureCode === 'string'
                && typeof value.execution_started === 'boolean'
              : false
  )
  if (
    !UUID_RE.test(String(value.job_id || ''))
    || !UUID_RE.test(String(value.approval_id || ''))
    || !UUID_RE.test(String(value.dry_run_id || ''))
    || !UUID_RE.test(String(value.submitted_by || ''))
    || !UUID_RE.test(String(value.requested_by || ''))
    || !UUID_RE.test(String(value.approved_by || ''))
    || typeof value.approved_payload_hash !== 'string'
    || !SHA256_RE.test(value.approved_payload_hash)
    || (value.execution_policy !== 'staging-train' && value.execution_policy !== 'sandbox-train')
    || !submittedAt
    || !Number.isInteger(recordVersion) || recordVersion < 1
    || value.authoritative_record !== true
    || typeof value.execution_started !== 'boolean'
    || value.submitted_by !== value.requested_by
    || value.approved_by === value.requested_by
    || (workerId !== null && (typeof workerId !== 'string' || !/^[a-z0-9][a-z0-9._:-]{2,79}$/.test(workerId)))
    || (fencingToken !== null && (!Number.isSafeInteger(fencingToken) || fencingToken < 1))
    || !artifactValid
    || !stateValid
  ) {
    return { ok: false, detail: 'job backend returned an invalid record' }
  }
  return {
    ok: true,
    value: {
      job_id: String(value.job_id).toLowerCase(),
      approval_id: String(value.approval_id).toLowerCase(),
      dry_run_id: String(value.dry_run_id).toLowerCase(),
      approved_payload_hash: value.approved_payload_hash,
      submitted_by: String(value.submitted_by).toLowerCase(),
      requested_by: String(value.requested_by).toLowerCase(),
      approved_by: String(value.approved_by).toLowerCase(),
      execution_policy: value.execution_policy,
      job_state: state as ModelRetrainJobState,
      submitted_at: submittedAt,
      record_version: recordVersion,
      authoritative_record: true,
      execution_started: value.execution_started as boolean,
      artifact_written: value.artifact_written as boolean,
      artifact_uri: artifactUri as string | null,
      artifact_sha256: artifactSha256 as string | null,
      artifact_size_bytes: artifactSize,
      artifact_media_type: artifactMediaType as ModelRetrainJobRecord['artifact_media_type'],
      artifact_registered_at: artifactRegisteredAt,
      worker_id: workerId as string | null,
      fencing_token: fencingToken,
      lease_expires_at: leaseExpiresAt,
      claimed_at: claimedAt,
      started_at: startedAt,
      finished_at: finishedAt,
      failure_code: failureCode as string | null,
    },
  }
}

function classifyRpcError(error: unknown, operation: string): JobLedgerResult<never> {
  const raw = isObject(error) ? error : {}
  const code = typeof raw.code === 'string' ? raw.code : ''
  const text = [raw.message, raw.details, raw.hint].filter(item => typeof item === 'string').join(' ').toLowerCase()
  if (code === 'P0002' || /approval.*not found|job.*not found/.test(text)) {
    return { ok: false, status: 404, detail: 'model retrain approval or job not found' }
  }
  if (code === '42501') return { ok: false, status: 403, detail: 'Admin role required' }
  if (['23505', '40001', '22023', '55000'].includes(code) || /conflict|not eligible|expired|version/.test(text)) {
    return { ok: false, status: 409, detail: 'model retrain job conflicts with approval state' }
  }
  return { ok: false, status: 503, detail: `model retrain job ${operation} backend unavailable` }
}

async function invoke(
  client: RpcClient,
  name: string,
  args: Record<string, unknown>,
  operation: string,
): Promise<JobLedgerResult<ModelRetrainJobRecord>> {
  let result: { data: unknown; error: unknown }
  try {
    result = await client.rpc(name, args)
  } catch {
    return { ok: false, status: 503, detail: `model retrain job ${operation} backend unavailable` }
  }
  if (result.error) return classifyRpcError(result.error, operation)
  const raw = unwrapSingleRecord(result.data)
  if (raw === null) return { ok: false, status: 404, detail: 'model retrain job not found' }
  if (raw === 'invalid') return { ok: false, status: 502, detail: 'job backend returned an invalid record' }
  const projected = projectJob(raw)
  return projected.ok ? projected : { ok: false, status: 502, detail: projected.detail }
}

export async function createModelRetrainJobViaRpc(
  client: RpcClient,
  actorUserId: string,
  input: CreateModelRetrainJobInput,
): Promise<JobLedgerResult<ModelRetrainJobRecord>> {
  const result = await invoke(client, 'create_model_retrain_job', {
    p_actor_user_id: actorUserId,
    p_approval_id: input.approval_id,
    p_expected_approval_version: input.expected_approval_version,
    p_approved_payload_hash: input.approved_payload_hash,
  }, 'create')
  if (!result.ok) return result
  if (
    result.value.approval_id !== input.approval_id
    || result.value.approved_payload_hash !== input.approved_payload_hash
    || result.value.submitted_by !== actorUserId
    || result.value.job_state !== 'queued'
    || result.value.record_version !== 1
    || result.value.execution_started !== false
  ) {
    return { ok: false, status: 502, detail: 'job backend returned an uncorrelated create record' }
  }
  return result
}

export async function getModelRetrainJobViaRpc(
  client: RpcClient,
  actorUserId: string,
  jobId: string,
): Promise<JobLedgerResult<ModelRetrainJobRecord>> {
  return invoke(client, 'get_model_retrain_job', {
    p_actor_user_id: actorUserId,
    p_job_id: jobId,
  }, 'read')
}
