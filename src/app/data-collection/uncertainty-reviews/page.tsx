'use client'

import Link from 'next/link'
import { useMemo, useRef, useState } from 'react'
import { Logo } from '@/components/Logo'
import { useAuth } from '@/contexts/AuthContext'
import { authFetch } from '@/lib/auth-fetch'
import {
  buildReviewOnlyDecisionBody,
  normalizeReviewOnlyDecisionReason,
  parseCorrelatedReviewDecision,
  parseReviewableRequestList,
  type ReviewOnlyDecisionBody,
} from '@/lib/scrape-uncertainty-review-public'
import type { ScrapeUncertaintyReviewRecord } from '@/lib/scrape-uncertainty-review-server'

const REVIEWABLE_REQUESTS_URL = '/api/scrape/uncertainty-review-requests?scope=reviewable&limit=20'

function responseDetail(payload: unknown, status: number): string {
  if (typeof payload === 'object'
    && payload !== null
    && 'detail' in payload
    && typeof payload.detail === 'string') {
    return payload.detail
  }
  return `HTTP ${status}`
}

export default function UncertaintyReviewQueuePage() {
  const { isAdmin, loading: authLoading } = useAuth()
  const [requests, setRequests] = useState<ScrapeUncertaintyReviewRecord[]>([])
  const [selectedRequestId, setSelectedRequestId] = useState<string | null>(null)
  const [decisionReason, setDecisionReason] = useState('')
  const [reviewOnlyAcknowledged, setReviewOnlyAcknowledged] = useState(false)
  const [loadAttempted, setLoadAttempted] = useState(false)
  const [loadPending, setLoadPending] = useState(false)
  const [decisionPending, setDecisionPending] = useState(false)
  const [error, setError] = useState('')
  const [decisionResult, setDecisionResult] = useState<ScrapeUncertaintyReviewRecord | null>(null)
  const loadInFlightRef = useRef(false)
  const decisionInFlightRef = useRef(false)

  const selectedRequest = useMemo(
    () => requests.find(request => request.request_id === selectedRequestId) ?? null,
    [requests, selectedRequestId],
  )
  const normalizedDecisionReason = normalizeReviewOnlyDecisionReason(decisionReason)
  const canDecide = Boolean(
    isAdmin
    && !authLoading
    && selectedRequest
    && normalizedDecisionReason
    && reviewOnlyAcknowledged
    && !loadPending
    && !decisionPending,
  )

  if (authLoading || !isAdmin) {
    return (
      <div className="min-h-screen bg-[#0a0a0a] text-white">
        <header className="border-b border-[#1e1e1e] px-6 py-4 flex items-center justify-between">
          <Logo href="/home" />
          <Link href="/data-collection" className="text-xs text-[#666] hover:text-white transition-colors">
            データ取得へ戻る
          </Link>
        </header>
        <main className="max-w-3xl mx-auto px-6 py-10">
          <div
            role={authLoading ? 'status' : 'alert'}
            data-testid={authLoading ? 'phase3g-admin-checking' : 'phase3g-admin-required'}
            className="rounded border border-[#7f1d1d] bg-[#1f0a0a] px-4 py-3 text-sm text-[#fecaca]"
          >
            {authLoading ? '管理者権限を確認しています。' : 'この監査キューはAdmin専用です。'}
          </div>
        </main>
      </div>
    )
  }

  const loadReviewableRequests = async () => {
    if (loadInFlightRef.current || decisionInFlightRef.current) return
    loadInFlightRef.current = true
    setLoadPending(true)
    setLoadAttempted(true)
    setError('')
    setDecisionResult(null)
    setSelectedRequestId(null)
    setDecisionReason('')
    setReviewOnlyAcknowledged(false)
    try {
      const response = await authFetch(REVIEWABLE_REQUESTS_URL, {
        cache: 'no-store',
        signal: AbortSignal.timeout(10_000),
      })
      const payload = await response.json().catch(() => null)
      if (!response.ok) throw new Error(responseDetail(payload, response.status))
      const parsed = parseReviewableRequestList(payload, 20)
      if (!parsed.ok) throw new Error(parsed.detail)
      setRequests(parsed.value.requests)
    } catch (caught: unknown) {
      setRequests([])
      setError(caught instanceof Error ? caught.message : 'reviewable request list failed')
    } finally {
      loadInFlightRef.current = false
      setLoadPending(false)
    }
  }

  const selectRequest = (requestId: string) => {
    if (decisionInFlightRef.current || decisionPending) return
    setSelectedRequestId(requestId)
    setDecisionReason('')
    setReviewOnlyAcknowledged(false)
    setDecisionResult(null)
    setError('')
  }

  const submitDecision = async (action: ReviewOnlyDecisionBody['action']) => {
    if (decisionInFlightRef.current || !selectedRequest || !reviewOnlyAcknowledged) return
    const decision = buildReviewOnlyDecisionBody(action, selectedRequest.version, decisionReason)
    if (!decision.ok) {
      setError(decision.detail)
      return
    }

    decisionInFlightRef.current = true
    setDecisionPending(true)
    setError('')
    setDecisionResult(null)
    try {
      const response = await authFetch(
        `/api/scrape/uncertainty-review-requests/${encodeURIComponent(selectedRequest.request_id)}/decision`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(decision.value),
          signal: AbortSignal.timeout(10_000),
        },
      )
      const payload = await response.json().catch(() => null)
      if (!response.ok) throw new Error(responseDetail(payload, response.status))
      const parsed = parseCorrelatedReviewDecision(payload, selectedRequest, action, decision.value.reason)
      if (!parsed.ok) throw new Error(parsed.detail)

      setDecisionResult(parsed.value)
      setRequests(current => current.filter(request => request.request_id !== selectedRequest.request_id))
      setSelectedRequestId(null)
      setDecisionReason('')
      setReviewOnlyAcknowledged(false)
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : 'review-only decision failed')
    } finally {
      decisionInFlightRef.current = false
      setDecisionPending(false)
    }
  }

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <header className="border-b border-[#1e1e1e] px-6 py-4 flex items-center justify-between">
        <Logo href="/home" />
        <div className="flex items-center gap-4">
          <Link href="/data-collection" className="text-xs text-[#666] hover:text-white transition-colors">
            データ取得へ戻る
          </Link>
          <span className="text-sm text-[#888]">Uncertainty Review Queue (Admin)</span>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-8 space-y-5">
        <section className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5 space-y-4">
          <div>
            <h1 className="text-base font-medium">独立Admin監査キュー</h1>
            <p className="mt-1 text-xs text-[#9ca3af]">
              jobIdのない実行状態不明レコードを、サーバー権威の台帳でreview-only判断します。
            </p>
          </div>
          <div data-testid="phase3g-safety-notice" className="rounded border border-[#92400e] bg-[#271503] px-3 py-3 text-xs text-[#fde68a] space-y-1">
            <div>approveを含むすべての判断は監査記録だけです。</div>
            <div>execution_enabled=false / lock_release_allowed=false / automatic_action_taken=false</div>
            <div>この画面は取得開始、Dry-run、retry、lock解除、自動遷移を行いません。</div>
          </div>
          <button
            type="button"
            data-testid="phase3g-load-reviewable"
            onClick={() => void loadReviewableRequests()}
            disabled={loadPending || decisionPending}
            className={`rounded px-4 py-2 text-sm font-medium ${loadPending || decisionPending ? 'bg-[#222] text-[#555] cursor-not-allowed' : 'bg-white text-black hover:bg-[#eee]'}`}
          >
            {loadPending ? 'Loading...' : '監査待ちを明示取得'}
          </button>
          {error && (
            <div role="alert" data-testid="phase3g-error" className="rounded border border-red-900 bg-red-950/40 px-3 py-2 text-xs text-red-200">
              {error}
            </div>
          )}
        </section>

        <section className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5 space-y-3" data-testid="phase3g-reviewable-list">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium">reviewable requests</h2>
            <span className="text-xs text-[#9ca3af]">count: {requests.length}</span>
          </div>
          {loadAttempted && !loadPending && requests.length === 0 && !error && (
            <div data-testid="phase3g-empty" className="text-xs text-[#6b7280]">監査待ちは0件です。</div>
          )}
          {requests.map(request => (
            <article key={request.request_id} data-testid="phase3g-review-card" className="rounded border border-[#27272a] bg-[#0a0a0a] p-4 space-y-2 text-xs">
              <div className="font-mono break-all" data-testid="phase3g-request-id">request_id: {request.request_id}</div>
              <div className="font-mono break-all">payload_hash: {request.request_payload_hash}</div>
              <div>version: {request.version} / status: {request.status}</div>
              <div>period: {request.request.start_period} - {request.request.end_period} / force_rescrape: {String(request.request.force_rescrape)}</div>
              <div>reason: {request.reason}</div>
              <div>uncertainty_at: {request.uncertainty_occurred_at}</div>
              <div>expires_at: {request.expires_at}</div>
              <div className="text-emerald-300">
                approval_scope={request.approval_scope} / execution_enabled={String(request.execution_enabled)} / lock_release_allowed={String(request.lock_release_allowed)} / automatic_action_taken={String(request.automatic_action_taken)}
              </div>
              <button
                type="button"
                data-testid="phase3g-select-review"
                onClick={() => selectRequest(request.request_id)}
                disabled={decisionPending}
                className="rounded bg-[#1f2937] px-3 py-1.5 text-[#bfdbfe] hover:bg-[#273449] disabled:cursor-not-allowed disabled:text-[#555]"
              >
                この記録をreview-only判断
              </button>
            </article>
          ))}
        </section>

        {selectedRequest && (
          <section className="bg-[#111827] border border-[#1f2937] rounded-lg p-5 space-y-3" data-testid="phase3g-decision-panel">
            <h2 className="text-sm font-medium">review-only decision</h2>
            <div className="text-xs font-mono break-all">request_id: {selectedRequest.request_id}</div>
            <div className="text-xs">expected_version: {selectedRequest.version}</div>
            <textarea
              data-testid="phase3g-decision-reason"
              value={decisionReason}
              onChange={event => setDecisionReason(event.target.value)}
              disabled={decisionPending}
              maxLength={500}
              rows={4}
              placeholder="独立監査の判断理由を20〜500文字で入力"
              className="w-full rounded border border-[#334155] bg-[#0a0a0a] px-3 py-2 text-xs text-white"
            />
            <div className="text-xs text-[#9ca3af]">normalized length: {normalizedDecisionReason?.length ?? 0} / 20-500</div>
            <label className="flex items-start gap-2 text-xs text-[#d1d5db]">
              <input
                type="checkbox"
                data-testid="phase3g-review-only-ack"
                checked={reviewOnlyAcknowledged}
                onChange={event => setReviewOnlyAcknowledged(event.target.checked)}
                disabled={decisionPending}
                className="mt-0.5"
              />
              <span>この判断はreview-onlyであり、取得・retry・lock解除を一切許可しないことを確認しました。</span>
            </label>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                data-testid="phase3g-approve"
                onClick={() => void submitDecision('approve')}
                disabled={!canDecide}
                className={`rounded px-3 py-1.5 text-xs font-medium ${canDecide ? 'bg-[#166534] text-white hover:bg-[#15803d]' : 'bg-[#222] text-[#555] cursor-not-allowed'}`}
              >
                {decisionPending ? 'Submitting...' : 'Approve (review-only)'}
              </button>
              <button
                type="button"
                data-testid="phase3g-reject"
                onClick={() => void submitDecision('reject')}
                disabled={!canDecide}
                className={`rounded px-3 py-1.5 text-xs font-medium ${canDecide ? 'bg-[#991b1b] text-white hover:bg-[#b91c1c]' : 'bg-[#222] text-[#555] cursor-not-allowed'}`}
              >
                {decisionPending ? 'Submitting...' : 'Reject (review-only)'}
              </button>
            </div>
          </section>
        )}

        {decisionResult && (
          <section data-testid="phase3g-decision-result" className="rounded border border-[#075985] bg-[#071a24] p-5 space-y-2 text-xs text-[#bae6fd]">
            <h2 className="text-sm font-medium">サーバー監査判断を記録しました</h2>
            <div>request_id: {decisionResult.request_id}</div>
            <div>status: {decisionResult.status} / version: {decisionResult.version}</div>
            <div>approval_scope=review_only / execution_enabled=false / lock_release_allowed=false / automatic_action_taken=false</div>
            <div>画面遷移・取得開始・lock操作は行っていません。</div>
          </section>
        )}
      </main>
    </div>
  )
}
