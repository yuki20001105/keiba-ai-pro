import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ModelRetrainApprovalPanel } from '@/components/ModelRetrainApprovalPanel'
import type { RetrainDryRunPayload } from '@/lib/model-retrain-approval-types'

const ACTOR = '11111111-1111-4111-8111-111111111111'
const APPROVER = '22222222-2222-4222-8222-222222222222'
const DRY_RUN = '33333333-3333-4333-8333-333333333333'
const APPROVAL = '44444444-4444-4444-8444-444444444444'
const JOB = '55555555-5555-4555-8555-555555555555'
const HASH = 'a'.repeat(64)
const authFetchMock = vi.hoisted(() => vi.fn())

vi.mock('@/lib/auth-fetch', () => ({ authFetch: authFetchMock }))

function payload(): RetrainDryRunPayload {
  return {
    dry_run_id: DRY_RUN,
    generated_at: new Date().toISOString(),
    target: 'win',
    model_type: 'lightgbm',
    train_period: { start: '20240101', end: '20251231' },
    validation_period: { start: '20260101', end: '20260731' },
    feature_count: 1,
    selected_features: ['horse_age'],
    removed_features: [],
    expected_outputs: ['model-artifact', 'evaluation-report'],
    estimated_runtime: { unit: 'minute', min: 8, max: 25, note: 'bounded estimate' },
    safety_checks: [
      { key: 'future_field_exclusion', status: 'pass', note: 'checked' },
      { key: 'out_of_time_split', status: 'pass', note: 'checked' },
      { key: 'active_model_immutable', status: 'pass', note: 'checked' },
      { key: 'production_write_blocked', status: 'pass', note: 'checked' },
      { key: 'path_input_rejected', status: 'pass', note: 'checked' },
    ],
    source_model_id: 'source-v1',
    active_model_id: 'active-v1',
    feature_contract_hash: 'b'.repeat(64),
    data_snapshot_id: 'c'.repeat(64),
    code_version: 'keiba-ai-pro-v1',
    git_commit: 'd'.repeat(40),
    created_by: ACTOR,
    state: 'preview-ready',
    warnings: [],
    notes: [],
  }
}

function approval(status: 'pending' | 'approved' = 'pending', jobCreated = false) {
  const dryRun = payload()
  return {
    approval_record: {
      approval_id: APPROVAL,
      dry_run_id: DRY_RUN,
      approved_by: status === 'approved' ? APPROVER : null,
      approved_at: status === 'approved' ? new Date().toISOString() : null,
      approval_status: status,
      approval_comment: status === 'approved' ? 'Independent reviewer approved sandbox training.' : '',
      approved_payload_hash: HASH,
      requested_by: ACTOR,
      requested_at: new Date().toISOString(),
      expires_at: new Date(Date.now() + 1_800_000).toISOString(),
      invalidation_reason: null,
      execution_policy: 'sandbox-train',
      allowed_actions: ['submit_approved_retrain', 'view_approval_status', 'view_job_status'],
    },
    dry_run_payload: dryRun,
    record_version: status === 'approved' ? 2 : 1,
    authoritative_record: true,
    execution_enabled: false,
    job_created: jobCreated,
  }
}

function queuedJob() {
  return {
    job_id: JOB,
    approval_id: APPROVAL,
    dry_run_id: DRY_RUN,
    approved_payload_hash: HASH,
    submitted_by: ACTOR,
    requested_by: ACTOR,
    approved_by: APPROVER,
    execution_policy: 'sandbox-train',
    job_state: 'queued',
    submitted_at: new Date().toISOString(),
    record_version: 1,
    authoritative_record: true,
    execution_started: false,
    artifact_written: false,
    artifact_uri: null,
    artifact_sha256: null,
    artifact_size_bytes: null,
    artifact_media_type: null,
    artifact_registered_at: null,
    worker_id: null,
    fencing_token: null,
    lease_expires_at: null,
    claimed_at: null,
    started_at: null,
    finished_at: null,
    failure_code: null,
  }
}

function response(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('model retrain approval panel', () => {
  beforeEach(() => vi.clearAllMocks())

  it('keeps ledger mutations disabled for non-admin viewers', () => {
    render(<ModelRetrainApprovalPanel isAdmin={false} payload={payload()} approvedPayloadHash={HASH} payloadReady />)
    expect(screen.getByText('この操作はAdmin専用です。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '承認依頼を作成' })).toBeDisabled()
  })

  it('creates only a pending approval request from the exact preview payload', async () => {
    const dryRun = payload()
    authFetchMock.mockResolvedValueOnce(response({ success: true, approval: approval() }, 201))
    render(<ModelRetrainApprovalPanel isAdmin payload={dryRun} approvedPayloadHash={HASH} payloadReady />)

    fireEvent.click(screen.getByRole('button', { name: '承認依頼を作成' }))
    await screen.findByText(/別のAdminが同じIDを読み込み/)
    expect(screen.getByText(/status:/).parentElement).toHaveTextContent('pending')
    expect(authFetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = authFetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/model-redesign/approval')
    expect(JSON.parse(String(init.body))).toEqual({
      dry_run_payload: dryRun,
      execution_policy: 'sandbox-train',
      allowed_actions: ['submit_approved_retrain', 'view_approval_status', 'view_job_status'],
    })
  })

  it('loads an approved record and queues without claiming execution', async () => {
    authFetchMock
      .mockResolvedValueOnce(response({ success: true, approval: approval('approved') }))
      .mockResolvedValueOnce(response({ success: true, job: queuedJob() }, 201))
    render(<ModelRetrainApprovalPanel isAdmin payload={payload()} approvedPayloadHash={HASH} payloadReady />)

    fireEvent.change(screen.getByLabelText('approval ID'), { target: { value: APPROVAL } })
    fireEvent.click(screen.getByRole('button', { name: '承認レコードを読込' }))
    await screen.findByRole('button', { name: '申請者としてjob投入' })
    fireEvent.click(screen.getByRole('button', { name: '申請者としてjob投入' }))

    await screen.findByText(`job_id: ${JOB}`)
    expect(screen.getByText('state: queued / version: 1')).toBeInTheDocument()
    await waitFor(() => expect(authFetchMock).toHaveBeenCalledTimes(2))
    const [url, init] = authFetchMock.mock.calls[1] as [string, RequestInit]
    expect(url).toBe('/api/model-redesign/jobs')
    expect(JSON.parse(String(init.body))).toEqual({
      approval_id: APPROVAL,
      expected_approval_version: 2,
      approved_payload_hash: HASH,
    })
  })
})
