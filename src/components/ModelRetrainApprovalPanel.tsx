'use client'

import { useState } from 'react'
import { authFetch } from '@/lib/auth-fetch'
import type { ModelRetrainApprovalLedgerRecord } from '@/lib/model-retrain-approval-ledger'
import type { ModelRetrainJobRecord } from '@/lib/model-retrain-job-ledger'
import type {
  RetrainApprovalExecutionPolicy,
  RetrainDryRunPayload,
} from '@/lib/model-retrain-approval-types'

type Operation = 'create' | 'load' | 'approve' | 'reject' | 'revoke' | 'queue' | 'refresh-job'

type ApiEnvelope = {
  success?: boolean
  code?: string
  error?: string
  approval?: ModelRetrainApprovalLedgerRecord
  job?: ModelRetrainJobRecord
}

type Props = {
  isAdmin: boolean
  payload: RetrainDryRunPayload | null
  approvedPayloadHash: string | null
  payloadReady: boolean
}

async function requestJson(url: string, init?: RequestInit): Promise<ApiEnvelope> {
  const response = await authFetch(url, {
    ...init,
    headers: init?.body
      ? { 'Content-Type': 'application/json; charset=utf-8', ...init.headers }
      : init?.headers,
    signal: AbortSignal.timeout(120_000),
  })
  const body = await response.json().catch(() => ({})) as ApiEnvelope
  if (!response.ok || body.success !== true) {
    throw new Error(body.error || body.code || `HTTP ${response.status}`)
  }
  return body
}

function shortDigest(value: string | null): string {
  return value ? `${value.slice(0, 12)}…${value.slice(-8)}` : 'N/A'
}

export function ModelRetrainApprovalPanel({
  isAdmin,
  payload,
  approvedPayloadHash,
  payloadReady,
}: Props) {
  const [policy, setPolicy] = useState<RetrainApprovalExecutionPolicy>('sandbox-train')
  const [approvalId, setApprovalId] = useState('')
  const [reason, setReason] = useState('')
  const [approval, setApproval] = useState<ModelRetrainApprovalLedgerRecord | null>(null)
  const [job, setJob] = useState<ModelRetrainJobRecord | null>(null)
  const [operation, setOperation] = useState<Operation | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const busy = operation !== null
  const canCreate = isAdmin && payloadReady && payload !== null && approvedPayloadHash !== null && !busy
  const normalizedApprovalId = approvalId.trim().toLowerCase()

  const run = async (next: Operation, task: () => Promise<void>) => {
    setOperation(next)
    setError('')
    setNotice('')
    try {
      await task()
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : '操作に失敗しました')
    } finally {
      setOperation(null)
    }
  }

  const createApproval = () => run('create', async () => {
    if (!payload || !approvedPayloadHash) return
    const body = await requestJson('/api/model-redesign/approval', {
      method: 'POST',
      body: JSON.stringify({
        dry_run_payload: payload,
        execution_policy: policy,
        allowed_actions: ['submit_approved_retrain', 'view_approval_status', 'view_job_status'],
      }),
    })
    if (!body.approval) throw new Error('承認台帳の応答が不完全です')
    setApproval(body.approval)
    setApprovalId(body.approval.approval_record.approval_id)
    setJob(null)
    setNotice('承認依頼を作成しました。別のAdminが同じIDを読み込み、独立に決裁してください。')
  })

  const loadApproval = () => run('load', async () => {
    if (!normalizedApprovalId) throw new Error('approval IDを入力してください')
    const body = await requestJson(`/api/model-redesign/approval/${encodeURIComponent(normalizedApprovalId)}`)
    if (!body.approval) throw new Error('承認台帳の応答が不完全です')
    setApproval(body.approval)
    setJob(null)
    setNotice('承認レコードを再読込しました。')
  })

  const decide = (action: 'approve' | 'reject' | 'revoke') => run(action, async () => {
    if (!approval) throw new Error('先に承認レコードを読み込んでください')
    if (reason.trim().length < 20) throw new Error('決裁理由は20文字以上で入力してください')
    const id = approval.approval_record.approval_id
    const body = await requestJson(`/api/model-redesign/approval/${encodeURIComponent(id)}/decision`, {
      method: 'POST',
      body: JSON.stringify({
        action,
        expected_version: approval.record_version,
        reason: reason.trim(),
      }),
    })
    if (!body.approval) throw new Error('決裁台帳の応答が不完全です')
    setApproval(body.approval)
    setReason('')
    setNotice(action === 'approve'
      ? '独立承認を記録しました。job投入は元の申請者が行います。'
      : '決裁結果を記録しました。')
  })

  const queueJob = () => run('queue', async () => {
    if (!approval) throw new Error('先に承認レコードを読み込んでください')
    const record = approval.approval_record
    if (record.approval_status !== 'approved') throw new Error('approved状態の承認だけがjob投入できます')
    const body = await requestJson('/api/model-redesign/jobs', {
      method: 'POST',
      body: JSON.stringify({
        approval_id: record.approval_id,
        expected_approval_version: approval.record_version,
        approved_payload_hash: record.approved_payload_hash,
      }),
    })
    if (!body.job) throw new Error('job台帳の応答が不完全です')
    setJob(body.job)
    setApproval({ ...approval, job_created: true })
    setNotice('承認済みjobを投入しました。投入だけではtrainingは開始されません。')
  })

  const refreshJob = () => run('refresh-job', async () => {
    if (!job) throw new Error('job IDがありません')
    const body = await requestJson(`/api/model-redesign/jobs/${encodeURIComponent(job.job_id)}`)
    if (!body.job) throw new Error('job台帳の応答が不完全です')
    setJob(body.job)
    setNotice('job状態を更新しました。')
  })

  return (
    <section className="mt-5 border-t border-[#2a2a2a] pt-5" aria-label="Retrain approval workflow">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-medium text-white">承認・job台帳</h3>
          <p className="mt-1 text-[11px] text-[#888]">
            申請、独立決裁、job投入のみを行います。training・artifact upload・active model切替は自動実行されません。
          </p>
        </div>
        <span className="rounded border border-yellow-800/50 px-2 py-1 text-[10px] text-yellow-300">
          two-person / fail-closed
        </span>
      </div>

      {!isAdmin && (
        <div className="mt-3 rounded border border-red-800/50 bg-red-950/20 p-3 text-xs text-red-200">
          この操作はAdmin専用です。
        </div>
      )}

      <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div className="rounded border border-[#262626] bg-[#0d0d0d] p-3">
          <label className="text-xs text-[#aaa]">
            execution policy
            <select
              value={policy}
              onChange={event => setPolicy(event.target.value as RetrainApprovalExecutionPolicy)}
              disabled={!isAdmin || busy}
              className="mt-1 block w-full rounded border border-[#333] bg-[#111] px-3 py-2 text-xs text-white"
            >
              <option value="sandbox-train">sandbox-train</option>
              <option value="staging-train">staging-train</option>
            </select>
          </label>
          <div className="mt-2 break-all text-[11px] text-[#777]">
            payload: {shortDigest(approvedPayloadHash)}
          </div>
          <button
            type="button"
            onClick={() => { void createApproval() }}
            disabled={!canCreate}
            className="mt-3 rounded bg-[#125b9a] px-3 py-2 text-xs text-white disabled:cursor-not-allowed disabled:bg-[#2a2a2a] disabled:text-[#777]"
          >
            {operation === 'create' ? '作成中…' : '承認依頼を作成'}
          </button>
          {!payloadReady && <p className="mt-2 text-[11px] text-red-300">preview-ready payloadが必要です。</p>}
        </div>

        <div className="rounded border border-[#262626] bg-[#0d0d0d] p-3">
          <label className="text-xs text-[#aaa]">
            approval ID
            <input
              value={approvalId}
              onChange={event => setApprovalId(event.target.value)}
              maxLength={36}
              spellCheck={false}
              className="mt-1 block w-full rounded border border-[#333] bg-[#111] px-3 py-2 font-mono text-xs text-white"
              placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
            />
          </label>
          <button
            type="button"
            onClick={() => { void loadApproval() }}
            disabled={!isAdmin || busy || normalizedApprovalId.length !== 36}
            className="mt-3 rounded border border-[#444] px-3 py-2 text-xs text-[#ddd] disabled:cursor-not-allowed disabled:text-[#666]"
          >
            {operation === 'load' ? '読込中…' : '承認レコードを読込'}
          </button>
        </div>
      </div>

      {approval && (
        <div className="mt-4 rounded border border-[#2a2a2a] bg-[#0d0d0d] p-3 text-xs">
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            <div>status: <span className="font-medium text-white">{approval.approval_record.approval_status}</span></div>
            <div>version: {approval.record_version}</div>
            <div className="break-all">requester: {approval.approval_record.requested_by}</div>
            <div className="break-all">approver: {approval.approval_record.approved_by || 'N/A'}</div>
            <div>expires: {new Date(approval.approval_record.expires_at).toLocaleString('ja-JP')}</div>
            <div>job_created: {String(approval.job_created)}</div>
          </div>

          <label className="mt-3 block text-[#aaa]">
            決裁理由（20～500文字）
            <textarea
              value={reason}
              onChange={event => setReason(event.target.value)}
              maxLength={500}
              rows={3}
              disabled={!isAdmin || busy}
              className="mt-1 block w-full rounded border border-[#333] bg-[#111] px-3 py-2 text-xs text-white"
            />
          </label>
          <div className="mt-3 flex flex-wrap gap-2">
            {approval.approval_record.approval_status === 'pending' && (
              <>
                <button type="button" onClick={() => { void decide('approve') }} disabled={busy || reason.trim().length < 20} className="rounded bg-emerald-800 px-3 py-2 text-xs disabled:bg-[#2a2a2a]">独立承認</button>
                <button type="button" onClick={() => { void decide('reject') }} disabled={busy || reason.trim().length < 20} className="rounded bg-red-900 px-3 py-2 text-xs disabled:bg-[#2a2a2a]">却下</button>
              </>
            )}
            {approval.approval_record.approval_status === 'approved' && !approval.job_created && (
              <>
                <button type="button" onClick={() => { void queueJob() }} disabled={busy} className="rounded bg-[#0e8f5b] px-3 py-2 text-xs disabled:bg-[#2a2a2a]">申請者としてjob投入</button>
                <button type="button" onClick={() => { void decide('revoke') }} disabled={busy || reason.trim().length < 20} className="rounded border border-red-900 px-3 py-2 text-xs text-red-300 disabled:text-[#666]">承認取消</button>
              </>
            )}
          </div>
          <p className="mt-2 text-[11px] text-[#777]">
            自己承認と、申請者以外によるjob投入は台帳RPCが拒否します。CAS競合時は再読込してください。
          </p>
        </div>
      )}

      {job && (
        <div className="mt-4 rounded border border-[#2a2a2a] bg-[#0d0d0d] p-3 text-xs">
          <div className="break-all">job_id: {job.job_id}</div>
          <div className="mt-1">state: {job.job_state} / version: {job.record_version}</div>
          <div className="mt-1">artifact: {job.artifact_written ? shortDigest(job.artifact_sha256) : 'not registered'}</div>
          <button type="button" onClick={() => { void refreshJob() }} disabled={busy} className="mt-3 rounded border border-[#444] px-3 py-2 text-xs text-[#ddd] disabled:text-[#666]">
            {operation === 'refresh-job' ? '更新中…' : 'job状態を更新'}
          </button>
        </div>
      )}

      {error && <div role="alert" className="mt-3 rounded border border-red-800/50 bg-red-950/20 p-3 text-xs text-red-200">{error}</div>}
      {notice && <div role="status" className="mt-3 rounded border border-emerald-800/50 bg-emerald-950/20 p-3 text-xs text-emerald-200">{notice}</div>}
    </section>
  )
}
