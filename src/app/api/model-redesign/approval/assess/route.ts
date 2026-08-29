import fs from 'node:fs'
import path from 'node:path'
import { NextResponse } from 'next/server'
import { verifyRequestAuth } from '@/lib/server-auth'
import {
  assessApprovedRetrainEligibility,
  computeFeatureContractHash,
  parseRetrainDryRunPayload,
} from '@/lib/model-retrain-approval-contract'

export const runtime = 'nodejs'

const MAX_REQUEST_BYTES = 256 * 1024
const REQUEST_KEYS = new Set(['dry_run_payload', 'approval_record'])
const PROJECT_ROOT = process.cwd()
const ACTIVE_MODEL_PATH = path.join(PROJECT_ROOT, 'python-api', 'models', '.active_model.json')
const PACKAGE_PATH = path.join(PROJECT_ROOT, 'package.json')

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function readBoundedJson(pathname: string, maxBytes: number): Record<string, unknown> | null {
  try {
    const stat = fs.statSync(pathname)
    if (!stat.isFile() || stat.size <= 0 || stat.size > maxBytes) return null
    const parsed: unknown = JSON.parse(fs.readFileSync(pathname, 'utf8'))
    return isObject(parsed) ? parsed : null
  } catch {
    return null
  }
}

function currentActiveModelId(): string | null {
  const active = readBoundedJson(ACTIVE_MODEL_PATH, 4 * 1024)
  return typeof active?.model_id === 'string' ? active.model_id : null
}

function currentCodeVersion(): string {
  const packageJson = readBoundedJson(PACKAGE_PATH, 64 * 1024)
  return typeof packageJson?.version === 'string' ? packageJson.version : ''
}

function currentCommit(): string {
  return (
    process.env.APP_COMMIT_SHA
    || process.env.VERCEL_GIT_COMMIT_SHA
    || process.env.GITHUB_SHA
    || ''
  ).trim().toLowerCase()
}

function artifactWritePolicy(): 'disabled' | 'staging-train' | 'sandbox-train' {
  const configured = (process.env.MODEL_RETRAIN_ARTIFACT_WRITE_POLICY || 'disabled').trim()
  return configured === 'staging-train' || configured === 'sandbox-train'
    ? configured
    : 'disabled'
}

function response(payload: unknown, status = 200): NextResponse {
  return NextResponse.json(payload, {
    status,
    headers: { 'Cache-Control': 'no-store' },
  })
}

export async function POST(request: Request) {
  const authz = await verifyRequestAuth(request, { requireAdmin: true })
  if (!authz.ok) {
    return response({ success: false, state: 'fail', code: 'authorization-failed', error: authz.detail }, authz.status)
  }

  const contentLength = Number(request.headers.get('content-length') || 0)
  if (Number.isFinite(contentLength) && contentLength > MAX_REQUEST_BYTES) {
    return response({ success: false, state: 'fail', code: 'request-too-large' }, 413)
  }

  let body: unknown
  try {
    const raw = await request.text()
    if (Buffer.byteLength(raw, 'utf8') > MAX_REQUEST_BYTES) {
      return response({ success: false, state: 'fail', code: 'request-too-large' }, 413)
    }
    body = JSON.parse(raw)
  } catch {
    return response({ success: false, state: 'fail', code: 'invalid-json' }, 400)
  }
  if (
    !isObject(body)
    || Object.keys(body).length !== REQUEST_KEYS.size
    || Object.keys(body).some(key => !REQUEST_KEYS.has(key))
  ) {
    return response({ success: false, state: 'fail', code: 'request-schema-invalid' }, 400)
  }

  const payload = parseRetrainDryRunPayload(body.dry_run_payload)
  const featureHash = payload
    ? computeFeatureContractHash({
        target: payload.target,
        model_type: payload.model_type,
        selected_features: payload.selected_features,
        removed_features: payload.removed_features,
      }) || ''
    : ''
  const assessment = assessApprovedRetrainEligibility({
    payload: body.dry_run_payload,
    approval: body.approval_record,
    context: {
      now: new Date().toISOString(),
      actor_id: authz.context.user.id,
      actor_is_admin: authz.context.profile.role === 'admin',
      current_active_model_id: currentActiveModelId(),
      current_feature_contract_hash: featureHash,
      current_code_version: currentCodeVersion(),
      current_git_commit: currentCommit(),
      production_write_blocked: process.env.MODEL_RETRAIN_PRODUCTION_WRITE_ENABLED !== 'true',
      artifact_write_policy: artifactWritePolicy(),
    },
  })

  return response({
    ...assessment,
    guard: {
      assessment_only: true,
      job_submission: 'not-implemented',
      model_artifact_written: false,
      active_model_switched: false,
      production_write: false,
    },
  })
}
