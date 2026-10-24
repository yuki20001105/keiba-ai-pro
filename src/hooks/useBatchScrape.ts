'use client'

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { authFetch } from '@/lib/auth-fetch'
import { formatApiErrorDetail } from '@/lib/api-error'
import type { JobStatus } from '@/lib/types'
import { isScrapeRaceScope, supportsJraScrape, type ScrapeRaceScope } from '@/lib/scrape-race-scope'
import { isRetirementReceipt, retiredScrapeDraft, retirementCanonicalPayload, retirementRequest, type RetiredScrapeDraft } from '@/lib/scrape-retirement'

export type BatchProgress = {
  current: number; total: number; message: string; eta: string
  newSavedRaces?: number; newSavedHorses?: number
  existingRacesSkipped?: number; verifiedNoRaceDates?: number
}
export type BatchResult = {
  races_collected: number; saved_horses: number; existing_races_skipped: number
  verified_no_race_dates: number; elapsed_time: number
  stats: { period: string; total_months: number }
}
export type BatchAcceptedJob = {
  jobId: string; status: 'queued' | 'running'; startDate: string; endDate: string; acceptedAt: string
  raceScope?: ScrapeRaceScope
}
export type UseBatchScrapeOptions = {
  ownerUserId?: string | null
  pollIntervalMs?: number; maxPollAttempts?: number; maxPollDurationMs?: number
  maxConsecutiveStatusFailures?: number
  onJobAccepted?: (job: BatchAcceptedJob) => void
}
export type BatchFailureKind = 'validation' | 'start_rejected' | 'execution' | 'monitoring'
  | 'authentication' | 'client_stop' | 'cancelled' | 'busy' | null
export type BatchMonitoringIssue = 'network' | 'service_unavailable' | 'not_found' | 'invalid_response' | 'job_mismatch'
export class BatchScrapeError extends Error {
  constructor(
    message: string, readonly kind: Exclude<BatchFailureKind, null>,
    readonly safeToRetry: boolean, readonly jobId: string | null = null,
    readonly monitoringIssue: BatchMonitoringIssue | null = null,
  ) { super(message); this.name = 'BatchScrapeError' }
}
type StoredBatch = {
  version: 1; ownerUserId: string; jobId: string; operationId: string
  startDate: string; endDate: string; forceRescrape: boolean; createdAt: string
  origin?: 'submitted' | 'history'
  raceScope?: ScrapeRaceScope
}

// A browser observes the job; it does not own its multi-day execution lifetime.
export const BATCH_SCRAPE_MAX_POLL_DURATION_MS = Number.POSITIVE_INFINITY
export const BATCH_SCRAPE_STORAGE_KEY_PREFIX = 'keiba-ai-pro:server-scrape-batch:v1'
export const BATCH_SCRAPE_RETIREMENT_KEY_PREFIX = 'keiba-ai-pro:server-scrape-retirement:v1'
const AUTH_MESSAGE = 'ログイン・管理者権限を再確認してください。開始済みの取得はサーバーで継続します。'
const DETACHED_MESSAGE = '画面の監視を終了しました。開始済みの取得はサーバーで継続します。'
const COMPLETED_CONTRACT_MESSAGE = '完了結果を確認できません。ジョブIDを保持して再確認できます。'
const JRA_CAPABILITY_MESSAGE = '中央限定取得はサーバーへの反映待ちです。取得対象を保証できないため送信していません。'
const MONITORING_MESSAGES: Record<BatchMonitoringIssue, string> = {
  network: '状態確認の通信が途切れました。取得が停止したとは限りません。保存したジョブIDで再確認してください。',
  service_unavailable: '取得状態の確認サービスに接続できません。取得が停止したとは限りません。保存したジョブIDで再確認してください。',
  not_found: '保存した依頼の受付記録が見つかりません。取得開始は未確認です。状態を再確認してください。',
  invalid_response: '取得状態の応答形式を確認できません。保存したジョブIDを保持して再確認してください。',
  job_mismatch: '保存したジョブIDとサーバーの応答が一致しません。依頼は変更せず、状態を再確認してください。',
}
const PERIOD_PATTERN = /^(\d{4})-(0[1-9]|1[0-2])$/
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null
const storageKey = (owner: string) => `${BATCH_SCRAPE_STORAGE_KEY_PREFIX}:${owner}`
function isDate(value: unknown): value is string {
  if (typeof value !== 'string' || !/^\d{8}$/.test(value)) return false
  const iso = `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}`
  const date = new Date(`${iso}T00:00:00Z`)
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === iso
}

function readStored(owner: string): StoredBatch | null {
  const raw = localStorage.getItem(storageKey(owner))
  if (!raw) return null
  return parseStored(JSON.parse(raw), owner)
}
function parseStored(v: unknown, owner: string): StoredBatch {
  if (!isRecord(v) || v.version !== 1 || v.ownerUserId !== owner
    || typeof v.jobId !== 'string' || !UUID_PATTERN.test(v.jobId)
    || typeof v.operationId !== 'string' || !UUID_PATTERN.test(v.operationId)
    || !isDate(v.startDate) || !isDate(v.endDate) || v.startDate > v.endDate
    || typeof v.forceRescrape !== 'boolean' || typeof v.createdAt !== 'string'
    || (v.raceScope !== undefined && !isScrapeRaceScope(v.raceScope))
    || !Number.isFinite(Date.parse(v.createdAt))) {
    throw new Error('保存済みジョブを確認できません。保存情報はそのまま保持しています。')
  }
  return v as StoredBatch
}
function saveStored(job: StoredBatch) {
  parseStored(job, job.ownerUserId)
  localStorage.setItem(storageKey(job.ownerUserId), JSON.stringify(job))
  if (readStored(job.ownerUserId)?.jobId !== job.jobId) throw new Error('ジョブIDを安全に保存できません')
}
function removeStored(job: StoredBatch) {
  // Never clear a different tab's/newer job or another user's record.
  if (storedMatches(job)) localStorage.removeItem(storageKey(job.ownerUserId))
}
function storedMatches(job: StoredBatch): boolean {
  return JSON.stringify(readStored(job.ownerUserId)) === JSON.stringify(job)
}
async function withRequestLock<T>(owner: string, action: () => Promise<T> | T): Promise<T> {
  return globalThis.navigator?.locks ? navigator.locks.request(storageKey(owner), action) : action()
}
function archiveRetirement(job: StoredBatch, receipt: unknown) {
  if (job.origin !== 'submitted' || !isRetirementReceipt(receipt, job) || !storedMatches(job)) {
    throw new BatchScrapeError('整理結果と元の依頼を照合できません。保存した依頼は保持しています。', 'monitoring', false, job.jobId, 'job_mismatch')
  }
  const key = `${BATCH_SCRAPE_RETIREMENT_KEY_PREFIX}:${job.ownerUserId}:${job.jobId}`
  const archive = JSON.stringify({
    version: 1, ownerUserId: job.ownerUserId, request: job,
    receipt: { job_id: job.jobId, operation_id: job.operationId, status: 'cancelled', retired: true,
      replacement_allowed: true, request_payload: retirementCanonicalPayload(job) },
    archivedAt: new Date().toISOString(),
  })
  const existing = localStorage.getItem(key)
  if (existing) {
    const saved: unknown = JSON.parse(existing)
    if (!isRecord(saved) || saved.ownerUserId !== job.ownerUserId
      || JSON.stringify(saved.request) !== JSON.stringify(job) || !isRetirementReceipt(saved.receipt, job)) {
      throw new BatchScrapeError('整理済み履歴を照合できません。元の依頼は保持しています。', 'monitoring', false, job.jobId, 'invalid_response')
    }
  } else {
    localStorage.setItem(key, archive)
    if (localStorage.getItem(key) !== archive) throw new Error('整理履歴を安全に保存できません')
  }
  // Preserve a different tab's replacement, even if it reused an ID.
  if (!storedMatches(job)) throw new BatchScrapeError('別の画面で依頼が変わりました。保存情報を変更していません。', 'monitoring', false, job.jobId, 'job_mismatch')
  localStorage.removeItem(storageKey(job.ownerUserId))
}
function delay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const onAbort = () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')) }
    const timer = setTimeout(() => { signal.removeEventListener('abort', onAbort); resolve() }, ms)
    if (signal.aborted) onAbort()
    else signal.addEventListener('abort', onAbort, { once: true })
  })
}

async function checkJraCapability(): Promise<boolean> {
  try {
    const response = await authFetch('/api/health', { cache: 'no-store', signal: AbortSignal.timeout(4000) })
    const payload: unknown = await response.json().catch(() => null)
    return response.ok && supportsJraScrape(payload)
  } catch {
    return false
  }
}

/** Submit the whole period once. Reopening only observes the durable server job. */
export function useBatchScrape(options?: UseBatchScrapeOptions) {
  const ownerUserId = options?.ownerUserId ?? null
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState<JobStatus>('idle')
  const [error, setError] = useState<string | null>(null)
  const [failureKind, setFailureKind] = useState<BatchFailureKind>(null)
  const [monitoringIssue, setMonitoringIssue] = useState<BatchMonitoringIssue | null>(null)
  const [observationPhase, setObservationPhase] = useState<'idle' | 'submitting' | 'monitoring'>('idle')
  const [retiredRequestDraft, setRetiredRequestDraft] = useState<RetiredScrapeDraft | null>(null)
  const [retirementSupported, setRetirementSupported] = useState(false)
  const [canRetry, setCanRetry] = useState(false)
  const [canResubmit, setCanResubmit] = useState(false)
  const [isExecutionLocked, setIsExecutionLocked] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [requestRaceScope, setRequestRaceScope] = useState<ScrapeRaceScope | null>(null)
  const [requestPeriod, setRequestPeriod] = useState<{
    jobId: string; startPeriod: string; endPeriod: string
  } | null>(null)
  const [progress, setProgress] = useState<BatchProgress>({ current: 0, total: 100, message: '', eta: '' })
  const [result, setResult] = useState<BatchResult | null>(null)
  const mountedRef = useRef(true)
  const ownerRef = useRef(ownerUserId)
  const controllerRef = useRef<AbortController | null>(null)
  const pendingRef = useRef<StoredBatch | null>(null)
  const inFlightRef = useRef(false)
  const executionLockedRef = useRef(false)
  const callbackRef = useRef(options?.onJobAccepted)
  useLayoutEffect(() => {
    ownerRef.current = ownerUserId
    callbackRef.current = options?.onJobAccepted
  }, [ownerUserId, options?.onJobAccepted])
  const pollIntervalMs = Math.max(0, options?.pollIntervalMs ?? 3000)
  const maxAttempts = options?.maxPollAttempts ?? Number.POSITIVE_INFINITY
  const maxDuration = options?.maxPollDurationMs ?? BATCH_SCRAPE_MAX_POLL_DURATION_MS
  const maxFailures = Math.max(1, options?.maxConsecutiveStatusFailures ?? 10)
  const setLock = useCallback((locked: boolean) => {
    executionLockedRef.current = locked
    if (mountedRef.current) setIsExecutionLocked(locked)
  }, [])
  const finishRetirement = useCallback((job: StoredBatch) => {
    pendingRef.current = null
    const draft = retiredScrapeDraft(job)
    setRetiredRequestDraft(draft)
    setJobId(null); setRequestRaceScope(null); setRequestPeriod(null); setLock(false)
    setStatus('idle'); setObservationPhase('idle'); setError(null); setFailureKind(null); setMonitoringIssue(null)
    setCanRetry(false); setCanResubmit(false); setRetirementSupported(false); setResult(null)
    setProgress({ current: 0, total: 100, message: '', eta: '' })
    return draft
  }, [setLock])

  const observe = useCallback(async (job: StoredBatch, submit: boolean, resubmission = false): Promise<BatchResult> => {
    if (!mountedRef.current || ownerRef.current !== job.ownerUserId) {
      throw new BatchScrapeError(DETACHED_MESSAGE, 'client_stop', false, job.jobId)
    }
    const controller = new AbortController()
    controllerRef.current = controller
    const signal = controller.signal
    const isCurrent = () => mountedRef.current && ownerRef.current === job.ownerUserId && !signal.aborted
    const checkCurrent = () => {
      if (!isCurrent()) throw new BatchScrapeError(DETACHED_MESSAGE, 'client_stop', false, job.jobId)
    }
    let published = false
    let retired = false
    const publish = (p: Record<string, unknown>) => {
      if (published || !isCurrent()) return
      published = true
      callbackRef.current?.({
        jobId: job.jobId, status: p.status === 'running' ? 'running' : 'queued',
        startDate: job.startDate, endDate: job.endDate,
        acceptedAt: typeof p.created_at === 'string' ? p.created_at : job.createdAt,
        raceScope: job.raceScope ?? 'all',
      })
    }
    pendingRef.current = job
    setJobId(job.jobId); setLoading(true); setStatus('queued')
    setRequestRaceScope(job.raceScope ?? 'all')
    setRequestPeriod({ jobId: job.jobId,
      startPeriod: `${job.startDate.slice(0, 4)}-${job.startDate.slice(4, 6)}`,
      endPeriod: `${job.endDate.slice(0, 4)}-${job.endDate.slice(4, 6)}` })
    setError(null); setFailureKind(null); setMonitoringIssue(null); setCanRetry(false); setResult(null); setLock(true)
    setCanResubmit(false)
    setRetirementSupported(false)
    setRetiredRequestDraft(null)
    setObservationPhase(submit ? 'submitting' : 'monitoring')
    setProgress(current => ({ ...current, message: submit ? 'サーバーへ受付中' : '再接続中', eta: '' }))
    try {
      if (submit) {
        await withRequestLock(job.ownerUserId, async () => {
          checkCurrent()
          if (!storedMatches(job)) {
            throw new BatchScrapeError('保存された依頼が変わったため送信していません。状態を再確認してください。', 'monitoring', false, job.jobId, 'job_mismatch')
          }
          let response: Response | null = null
          try {
            response = await authFetch('/api/scrape', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                start_date: job.startDate, end_date: job.endDate, force_rescrape: job.forceRescrape,
                server_batch: true, job_id: job.jobId, operation_id: job.operationId,
                // Old saved requests omit scope; never rewrite their resend body.
                ...(job.raceScope === undefined ? {} : { race_scope: job.raceScope }),
              }),
              signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
            })
          } catch {
            checkCurrent()
            // It may have been accepted. Read the saved ID, never blindly repeat POST.
          }
          checkCurrent()
          if (response) {
            const payload: unknown = await response.json().catch(() => null)
            checkCurrent()
            if (response.status === 401 || response.status === 403) {
              if (resubmission) {
                // A retry rejection cannot disprove an earlier/delayed acceptance.
                throw new BatchScrapeError(AUTH_MESSAGE, 'authentication', false, job.jobId)
              }
              // The single submission was explicitly rejected before execution.
              // Do not leave a never-accepted ID permanently blocking this account.
              removeStored(job); pendingRef.current = null; setJobId(null); setLock(false)
              throw new BatchScrapeError(AUTH_MESSAGE, 'authentication', true)
            }
            if (!response.ok && response.status >= 400 && response.status < 500) {
              const detail = isRecord(payload) ? payload.detail : undefined
              if (response.status === 409 && detail === 'owner-active-job') {
                if (resubmission) {
                  throw new BatchScrapeError('取得が実行中です。元のジョブIDで状態を再確認してください。', 'monitoring', false, job.jobId)
                }
                removeStored(job); pendingRef.current = null; setLock(false)
                throw new BatchScrapeError('別のデータ取得が実行中です。完了までお待ちください。', 'busy', false)
              }
              // Only definite validation rejection can permit a fresh submission.
              if (response.status === 400 || response.status === 422) {
                if (resubmission) {
                  throw new BatchScrapeError('再送は拒否されました。元のジョブIDを保持して状態を再確認してください。', 'monitoring', false, job.jobId)
                }
                removeStored(job); pendingRef.current = null; setLock(false)
                throw new BatchScrapeError(formatApiErrorDetail(detail, `HTTP ${response.status}`), 'start_rejected', true)
              }
            }
            if (response.ok && isRecord(payload) && payload.job_id === job.jobId && payload.retired !== true) publish(payload)
            // Missing IDs, mismatched IDs and 5xx are resolved via the original ID.
          }
        })
      }
      setObservationPhase('monitoring')
      let failures = 0
      let attempts = 0
      const began = Date.now()
      while (true) {
        checkCurrent()
        if (++attempts > maxAttempts || Date.now() - began >= maxDuration) {
          throw new BatchScrapeError('監視を再接続してください。取得処理とジョブIDは保持されています。', 'monitoring', false, job.jobId)
        }
        if (attempts > 1) await delay(pollIntervalMs, signal)
        checkCurrent()
        let response: Response
        try {
          response = await authFetch(`/api/scrape/status/${encodeURIComponent(job.jobId)}`, {
            cache: 'no-store', signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]),
          })
        } catch {
          checkCurrent()
          if (++failures >= maxFailures) throw new BatchScrapeError(MONITORING_MESSAGES.network, 'monitoring', false, job.jobId, 'network')
          setProgress(current => ({ ...current, message: '再接続中', eta: '' }))
          continue
        }
        checkCurrent()
        if (response.status === 401 || response.status === 403) {
          throw new BatchScrapeError(AUTH_MESSAGE, 'authentication', false, job.jobId)
        }
        const payload: unknown = await response.json().catch(() => null)
        checkCurrent()
        if (response.ok && isRecord(payload) && payload.job_id === job.jobId && payload.status === 'not_found') {
          setRetirementSupported(payload.retirement_supported === true)
        }
        if (!response.ok || !isRecord(payload) || payload.status === 'not_found'
          || payload.job_id !== job.jobId
          || !['queued', 'running', 'recovering', 'waiting_resources', 'cancelling', 'completed', 'error', 'failed', 'cancelled'].includes(String(payload.status))) {
          // Health availability does not prove this owner's request was
          // accepted. Keep transport, ledger absence and contract failures
          // distinct without clearing identity or treating them as a stop.
          const issue: BatchMonitoringIssue = !response.ok ? 'service_unavailable'
            : !isRecord(payload) ? 'invalid_response'
              : payload.job_id !== job.jobId ? 'job_mismatch'
                : payload.status === 'not_found' ? 'not_found' : 'invalid_response'
          if (++failures >= maxFailures || (!submit && issue === 'not_found'
            && isRecord(payload) && payload.retirement_supported === true)) {
            throw new BatchScrapeError(MONITORING_MESSAGES[issue], 'monitoring', false, job.jobId, issue)
          }
          setProgress(current => ({ ...current, message: '再接続中', eta: '' }))
          continue
        }
        failures = 0
        const rawStatus = payload.status
        if (payload.retired === true) {
          await withRequestLock(job.ownerUserId, () => {
            checkCurrent()
            archiveRetirement(job, payload)
          })
          checkCurrent()
          retired = true
          finishRetirement(job)
          // This is a retired request, not a completed acquisition result.
          throw new BatchScrapeError('前回の未受付依頼を整理しました。期間・対象を選び直してください。', 'cancelled', false, job.jobId)
        }
        if (['queued', 'running', 'recovering', 'waiting_resources', 'cancelling'].includes(String(rawStatus))) publish(payload)
        const p = isRecord(payload.progress) ? payload.progress : {}
        const number = (v: unknown) => typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : 0
        const total = number(p.total)
        const current = total > 0 ? Math.min(99, Math.round(number(p.done) / total * 100)) : 0
        const month = typeof p.current_month === 'string' ? `${p.current_month} · ` : ''
        setProgress({
          current, total: 100, eta: '',
          message: rawStatus === 'cancelling' ? '安全な区切りで停止中'
            : `${month}${typeof p.message === 'string' ? p.message : 'サーバーで実行中'}`,
          newSavedRaces: number(p.saved_races), newSavedHorses: number(p.saved_horses),
          existingRacesSkipped: number(p.existing_races_skipped), verifiedNoRaceDates: number(p.no_race_dates),
        })
        if (rawStatus === 'completed') {
          if (!isRecord(payload.result) || !Number.isInteger(payload.result.races_collected)
            || typeof payload.result.races_collected !== 'number' || payload.result.races_collected < 0) {
            throw new BatchScrapeError(COMPLETED_CONTRACT_MESSAGE, 'monitoring', false, job.jobId, 'invalid_response')
          }
          const r = payload.result
          const months = (Number(job.endDate.slice(0, 4)) - Number(job.startDate.slice(0, 4))) * 12
            + Number(job.endDate.slice(4, 6)) - Number(job.startDate.slice(4, 6)) + 1
          const batchResult: BatchResult = {
            races_collected: r.races_collected as number,
            saved_horses: number(r.saved_horses), existing_races_skipped: number(r.existing_races_skipped),
            verified_no_race_dates: number(r.verified_no_race_dates), elapsed_time: number(r.elapsed_time),
            stats: { period: `${job.startDate}〜${job.endDate}`, total_months: months },
          }
          removeStored(job); pendingRef.current = null; setLock(false)
          setStatus('completed'); setProgress(current => ({ ...current, current: 100, message: '完了', eta: '' }))
          setResult(batchResult)
          return batchResult
        }
        if (rawStatus === 'error' || rawStatus === 'failed' || rawStatus === 'cancelled') {
          // Older terminal receipts may expose owner cancellation as an
          // error plus this explicit reason. It is a confirmed safe stop,
          // not a failed request to encourage automatically retrying.
          const cancelled = rawStatus === 'cancelled' || payload.error === 'cancelled-by-owner'
          removeStored(job); pendingRef.current = null; setLock(false)
          throw new BatchScrapeError(
            cancelled ? '取得を停止しました。保存済みデータは保持されています。'
              : typeof payload.error === 'string' ? payload.error : 'データ取得が失敗しました',
            cancelled ? 'cancelled' : 'execution', !cancelled, job.jobId,
          )
        }
        setStatus(rawStatus === 'queued' ? 'queued' : rawStatus === 'cancelling' ? 'cancelling' : 'running')
      }
    } catch (cause) {
      const failure = !isCurrent() ? new BatchScrapeError(DETACHED_MESSAGE, 'client_stop', false, job.jobId)
        : cause instanceof BatchScrapeError ? cause
          : new BatchScrapeError(cause instanceof Error ? cause.message : '状態を再確認してください', 'monitoring', false, job.jobId)
      if (isCurrent() && !retired) {
        setStatus(failure.kind === 'cancelled' ? 'cancelled' : 'error')
        setError(failure.kind === 'cancelled' ? null : failure.message)
        setFailureKind(failure.kind); setCanRetry(failure.safeToRetry)
        setMonitoringIssue(failure.monitoringIssue)
        // Never offer a resend merely because a proxy is unavailable or its
        // response is malformed. Resubmit still rechecks and confirms the
        // same original request before any idempotent POST.
        setCanResubmit(failure.kind === 'monitoring' && failure.monitoringIssue === 'not_found'
          && pendingRef.current?.origin === 'submitted')
      }
      throw failure
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null; inFlightRef.current = false
        if (mountedRef.current && ownerRef.current === job.ownerUserId) setLoading(false)
      }
    }
  }, [maxFailures, maxAttempts, maxDuration, pollIntervalMs, setLock, finishRetirement])

  const reconnect = useCallback(async (knownJob?: BatchAcceptedJob): Promise<BatchResult | null> => {
    if (!ownerUserId || ownerRef.current !== ownerUserId || inFlightRef.current || !mountedRef.current) return null
    const stored = readStored(ownerUserId)
    if (stored && knownJob && stored.jobId !== knownJob.jobId) return null
    // knownJob must come from the owner-filtered server history, never a URL.
    const job = stored ?? (knownJob ? {
      version: 1 as const, ownerUserId, jobId: knownJob.jobId, operationId: knownJob.jobId,
      startDate: knownJob.startDate, endDate: knownJob.endDate, forceRescrape: false, createdAt: knownJob.acceptedAt,
      origin: 'history' as const,
      ...(knownJob.raceScope === undefined ? {} : { raceScope: knownJob.raceScope }),
    } : null)
    if (!job) return null
    if (!stored) saveStored(job)
    inFlightRef.current = true
    return observe(job, false)
  }, [observe, ownerUserId])

  useEffect(() => {
    mountedRef.current = true
    setStatus('idle'); setJobId(null); setResult(null); setError(null); setFailureKind(null); setMonitoringIssue(null)
    setObservationPhase('idle'); setRetiredRequestDraft(null)
    setRetirementSupported(false)
    setRequestRaceScope(null)
    setRequestPeriod(null)
    setCanRetry(false); setCanResubmit(false); setLoading(false); pendingRef.current = null; setLock(false)
    if (ownerUserId) {
      void reconnect().catch(cause => {
        if (cause instanceof BatchScrapeError) return
        if (mountedRef.current && ownerRef.current === ownerUserId) {
          setLock(true); setStatus('error'); setFailureKind('monitoring')
          setError(cause instanceof Error ? cause.message : '保存済みジョブを確認できません')
        }
      })
    }
    return () => {
      mountedRef.current = false
      controllerRef.current?.abort(); controllerRef.current = null; inFlightRef.current = false
    }
  }, [ownerUserId, reconnect, setLock])

  const start = useCallback(async (
    startPeriod: string, endPeriod: string, forceRescrape: boolean, raceScope?: ScrapeRaceScope,
  ): Promise<BatchResult> => {
    if (!mountedRef.current || !ownerUserId || ownerRef.current !== ownerUserId) throw new BatchScrapeError(AUTH_MESSAGE, 'authentication', false)
    if (inFlightRef.current || executionLockedRef.current) throw new BatchScrapeError('取得状態を再確認してください', 'busy', false, pendingRef.current?.jobId)
    if (!PERIOD_PATTERN.test(startPeriod) || !PERIOD_PATTERN.test(endPeriod) || startPeriod > endPeriod
      || (raceScope !== undefined && !isScrapeRaceScope(raceScope))) {
      const failure = new BatchScrapeError('期間指定が不正です（開始・終了をYYYY-MMで指定）', 'validation', false)
      setStatus('error'); setFailureKind(failure.kind); setError(failure.message)
      throw failure
    }
    inFlightRef.current = true
    try {
      if (raceScope === 'jra') {
        const supported = await checkJraCapability()
        if (!mountedRef.current || ownerRef.current !== ownerUserId) throw new BatchScrapeError(DETACHED_MESSAGE, 'client_stop', false)
        if (!supported) {
          const failure = new BatchScrapeError(JRA_CAPABILITY_MESSAGE, 'start_rejected', false)
          setStatus('error'); setFailureKind(failure.kind); setError(failure.message)
          throw failure
        }
      }
      const reserve = () => {
        if (!mountedRef.current || ownerRef.current !== ownerUserId) throw new BatchScrapeError(DETACHED_MESSAGE, 'client_stop', false)
        if (readStored(ownerUserId)) throw new BatchScrapeError('保存済みジョブの状態を再確認してください', 'busy', false)
        if (!globalThis.crypto?.randomUUID) throw new Error('ジョブIDを安全に生成できません')
        const [endYear, endMonth] = endPeriod.split('-').map(Number)
        const job: StoredBatch = {
          version: 1, ownerUserId, jobId: globalThis.crypto.randomUUID(), operationId: globalThis.crypto.randomUUID(),
          startDate: `${startPeriod.replace('-', '')}01`,
          endDate: `${endPeriod.replace('-', '')}${String(new Date(endYear, endMonth, 0).getDate()).padStart(2, '0')}`,
          forceRescrape, createdAt: new Date().toISOString(), origin: 'submitted',
          ...(raceScope === undefined ? {} : { raceScope }),
        }
        // Persist before crossing the network, including the idempotency identity.
        saveStored(job)
        return job
      }
      const job = await withRequestLock(ownerUserId, reserve)
      if (!mountedRef.current || ownerRef.current !== ownerUserId) {
        removeStored(job)
        throw new BatchScrapeError(DETACHED_MESSAGE, 'client_stop', false)
      }
      return await observe(job, true)
    } catch (cause) {
      if (ownerRef.current === ownerUserId && !controllerRef.current) inFlightRef.current = false
      if (!(cause instanceof BatchScrapeError)) {
        const failure = new BatchScrapeError('ジョブ情報を保存できないため開始していません。保存領域を確認してください。', 'start_rejected', false)
        setStatus('error'); setError(failure.message); setFailureKind(failure.kind)
        throw failure
      }
      throw cause
    }
  }, [observe, ownerUserId])

  const resubmit = useCallback(async (confirmSameRequest: () => boolean): Promise<BatchResult | null> => {
    if (!ownerUserId || ownerRef.current !== ownerUserId || !mountedRef.current || inFlightRef.current) return null
    const saved = readStored(ownerUserId)
    if (!saved || saved.origin !== 'submitted') return null
    inFlightRef.current = true
    setLoading(true)
    try {
      const action = await withRequestLock(ownerUserId, async () => {
        if (!mountedRef.current || ownerRef.current !== ownerUserId) return null
        if (!storedMatches(saved)) {
          throw new BatchScrapeError('保存された依頼が変わったため再送していません。', 'monitoring', false, saved.jobId, 'job_mismatch')
        }
        const response = await authFetch(`/api/scrape/status/${encodeURIComponent(saved.jobId)}`, {
          cache: 'no-store', signal: AbortSignal.timeout(10000),
        })
        const payload: unknown = await response.json().catch(() => null)
        if (!mountedRef.current || ownerRef.current !== ownerUserId) return null
        if (response.status === 401 || response.status === 403) {
          throw new BatchScrapeError(AUTH_MESSAGE, 'authentication', false, saved.jobId)
        }
        if (!response.ok || !isRecord(payload) || payload.job_id !== saved.jobId) {
          const issue: BatchMonitoringIssue = !response.ok ? 'service_unavailable'
            : !isRecord(payload) ? 'invalid_response' : 'job_mismatch'
          throw new BatchScrapeError(MONITORING_MESSAGES[issue], 'monitoring', false, saved.jobId, issue)
        }
        if (payload.status !== 'not_found') return 'observe'
        if (saved.raceScope === 'jra' && !await checkJraCapability()) {
          throw new BatchScrapeError(JRA_CAPABILITY_MESSAGE, 'monitoring', false, saved.jobId)
        }
        if (!mountedRef.current || ownerRef.current !== ownerUserId) return null
        if (!confirmSameRequest()) return null
        if (!storedMatches(saved)) {
          throw new BatchScrapeError('保存された依頼が変わったため再送していません。', 'monitoring', false, saved.jobId, 'job_mismatch')
        }
        return 'submit'
      })
      if (!action) return null
      // Explicit retry is idempotent: no new IDs, period or force mode. History
      // adoption has no original operation identity and can never take this path.
      return await observe(saved, action === 'submit', true)
    } catch (cause) {
      const failure = cause instanceof BatchScrapeError ? cause
        : new BatchScrapeError(MONITORING_MESSAGES.network, 'monitoring', false, saved.jobId, 'network')
      if (mountedRef.current && ownerRef.current === ownerUserId) {
        setError(failure.message); setFailureKind(failure.kind); setMonitoringIssue(failure.monitoringIssue)
        setCanResubmit(failure.kind === 'monitoring' && failure.monitoringIssue === 'not_found')
      }
      throw failure
    } finally {
      if (ownerRef.current === ownerUserId && !controllerRef.current) {
        inFlightRef.current = false
        if (mountedRef.current) setLoading(false)
      }
    }
  }, [observe, ownerUserId])

  const retire = useCallback(async (confirmRetirement: () => boolean): Promise<RetiredScrapeDraft | null> => {
    if (!ownerUserId || ownerRef.current !== ownerUserId || !mountedRef.current || inFlightRef.current
      || !canResubmit || monitoringIssue !== 'not_found' || !retirementSupported) return null
    const saved = readStored(ownerUserId)
    if (!saved || saved.origin !== 'submitted') return null
    const controller = new AbortController()
    controllerRef.current = controller
    inFlightRef.current = true
    setLoading(true); setObservationPhase('monitoring')
    const current = () => mountedRef.current && ownerRef.current === ownerUserId && !controller.signal.aborted
    const checkCurrent = () => {
      if (!current()) throw new BatchScrapeError(DETACHED_MESSAGE, 'client_stop', false, saved.jobId)
    }
    const checkStored = () => {
      checkCurrent()
      if (!storedMatches(saved)) throw new BatchScrapeError('別の画面で依頼が変わりました。元の依頼を整理していません。', 'monitoring', false, saved.jobId, 'job_mismatch')
    }
    try {
      return await withRequestLock(ownerUserId, async () => {
        checkStored()
        // Never turn a health check or a previous cached absence into proof.
        const response = await authFetch(`/api/scrape/status/${encodeURIComponent(saved.jobId)}`, {
          cache: 'no-store', signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]),
        })
        const payload: unknown = await response.json().catch(() => null)
        checkStored()
        if (response.status === 401 || response.status === 403) throw new BatchScrapeError(AUTH_MESSAGE, 'authentication', false, saved.jobId)
        if (!response.ok || !isRecord(payload) || payload.job_id !== saved.jobId) {
          const issue: BatchMonitoringIssue = !response.ok ? 'service_unavailable'
            : !isRecord(payload) ? 'invalid_response' : 'job_mismatch'
          throw new BatchScrapeError(MONITORING_MESSAGES[issue], 'monitoring', false, saved.jobId, issue)
        }
        if (payload.retired === true) {
          // A previous explicit retirement may have committed even though its
          // response was lost. Recover only an exact operation-bound receipt.
          archiveRetirement(saved, payload)
          return finishRetirement(saved)
        }
        if (payload.status !== 'not_found') {
          throw new BatchScrapeError('取得の受付記録が確認されました。元の依頼は保持しています。状態を再確認してください。', 'monitoring', false, saved.jobId)
        }
        if (payload.retirement_supported !== true) {
          setRetirementSupported(false)
          throw new BatchScrapeError('安全な依頼の整理はサーバーへの反映待ちです。元の依頼は保持しています。', 'monitoring', false, saved.jobId, 'not_found')
        }
        if (!confirmRetirement()) return null
        checkStored()
        // This creates only a server tombstone: never a replacement scrape.
        const retiredResponse = await authFetch(`/api/scrape/retire/${encodeURIComponent(saved.jobId)}`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(retirementRequest(saved)), cache: 'no-store',
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
        })
        const receipt: unknown = await retiredResponse.json().catch(() => null)
        checkStored()
        if (retiredResponse.status === 401 || retiredResponse.status === 403) throw new BatchScrapeError(AUTH_MESSAGE, 'authentication', false, saved.jobId)
        if (!retiredResponse.ok) {
          throw new BatchScrapeError('サーバーで依頼を整理できませんでした。取得受付済み・実行中の可能性があるため、元の依頼は保持しています。', 'monitoring', false, saved.jobId, 'service_unavailable')
        }
        if (!isRetirementReceipt(receipt, saved)) {
          throw new BatchScrapeError('整理結果と元の依頼を照合できません。元の依頼は保持しています。', 'monitoring', false, saved.jobId, 'invalid_response')
        }
        archiveRetirement(saved, receipt)
        checkCurrent()
        return finishRetirement(saved)
      })
    } catch (cause) {
      const failure = !current() ? new BatchScrapeError(DETACHED_MESSAGE, 'client_stop', false, saved.jobId)
        : cause instanceof BatchScrapeError ? cause
          : new BatchScrapeError('依頼の整理結果を確認・保存できません。元の依頼は保持しています。状態を再確認してください。', 'monitoring', false, saved.jobId, 'network')
      if (current()) {
        setStatus('error'); setError(failure.message); setFailureKind(failure.kind); setMonitoringIssue(failure.monitoringIssue)
        setCanRetry(false); setCanResubmit(failure.monitoringIssue === 'not_found')
      }
      throw failure
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null; inFlightRef.current = false
        if (mountedRef.current && ownerRef.current === ownerUserId) setLoading(false)
      }
    }
  }, [canResubmit, monitoringIssue, retirementSupported, ownerUserId, finishRetirement])

  const abort = useCallback(() => controllerRef.current?.abort(), [])
  // Parent cancellation is an explicit API request by the page, not a local flag.
  const requestBatchStop = useCallback(() => {}, [])
  const clearExecutionLockAfterReconciliation = useCallback((verifiedJobId?: string): boolean => {
    if (inFlightRef.current) return false
    if (pendingRef.current && pendingRef.current.jobId !== verifiedJobId) return false
    if (pendingRef.current) removeStored(pendingRef.current)
    pendingRef.current = null; setLock(false)
    return true
  }, [setLock])
  return { loading, status, error, failureKind, monitoringIssue, observationPhase, retirementSupported, retiredRequestDraft,
    canRetry, canResubmit, canRetire: canResubmit && retirementSupported, isExecutionLocked, jobId,
    raceScope: requestRaceScope, requestPeriod, progress, result,
    start, abort, reconnect, resubmit, retire, requestBatchStop, clearExecutionLockAfterReconciliation }
}
