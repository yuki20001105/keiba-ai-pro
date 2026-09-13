'use client'

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { authFetch } from '@/lib/auth-fetch'
import { formatApiErrorDetail } from '@/lib/api-error'
import type { JobStatus } from '@/lib/types'

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
}
export type UseBatchScrapeOptions = {
  ownerUserId?: string | null
  pollIntervalMs?: number; maxPollAttempts?: number; maxPollDurationMs?: number
  maxConsecutiveStatusFailures?: number
  onJobAccepted?: (job: BatchAcceptedJob) => void
}
export type BatchFailureKind = 'validation' | 'start_rejected' | 'execution' | 'monitoring'
  | 'authentication' | 'client_stop' | 'cancelled' | 'busy' | null
export class BatchScrapeError extends Error {
  constructor(
    message: string, readonly kind: Exclude<BatchFailureKind, null>,
    readonly safeToRetry: boolean, readonly jobId: string | null = null,
  ) { super(message); this.name = 'BatchScrapeError' }
}
type StoredBatch = {
  version: 1; ownerUserId: string; jobId: string; operationId: string
  startDate: string; endDate: string; forceRescrape: boolean; createdAt: string
  origin?: 'submitted' | 'history'
}

// A browser observes the job; it does not own its multi-day execution lifetime.
export const BATCH_SCRAPE_MAX_POLL_DURATION_MS = Number.POSITIVE_INFINITY
export const BATCH_SCRAPE_STORAGE_KEY_PREFIX = 'keiba-ai-pro:server-scrape-batch:v1'
const AUTH_MESSAGE = 'ログイン・管理者権限を再確認してください。開始済みの取得はサーバーで継続します。'
const DETACHED_MESSAGE = '画面の監視を終了しました。開始済みの取得はサーバーで継続します。'
const COMPLETED_CONTRACT_MESSAGE = '完了結果を確認できません。ジョブIDを保持して再確認できます。'
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
  if (readStored(job.ownerUserId)?.jobId === job.jobId) localStorage.removeItem(storageKey(job.ownerUserId))
}
function delay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const onAbort = () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')) }
    const timer = setTimeout(() => { signal.removeEventListener('abort', onAbort); resolve() }, ms)
    if (signal.aborted) onAbort()
    else signal.addEventListener('abort', onAbort, { once: true })
  })
}

/** Submit the whole period once. Reopening only observes the durable server job. */
export function useBatchScrape(options?: UseBatchScrapeOptions) {
  const ownerUserId = options?.ownerUserId ?? null
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState<JobStatus>('idle')
  const [error, setError] = useState<string | null>(null)
  const [failureKind, setFailureKind] = useState<BatchFailureKind>(null)
  const [canRetry, setCanRetry] = useState(false)
  const [canResubmit, setCanResubmit] = useState(false)
  const [isExecutionLocked, setIsExecutionLocked] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
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
    const publish = (p: Record<string, unknown>) => {
      if (published || !isCurrent()) return
      published = true
      callbackRef.current?.({
        jobId: job.jobId, status: p.status === 'running' ? 'running' : 'queued',
        startDate: job.startDate, endDate: job.endDate,
        acceptedAt: typeof p.created_at === 'string' ? p.created_at : job.createdAt,
      })
    }
    pendingRef.current = job
    setJobId(job.jobId); setLoading(true); setStatus('queued')
    setError(null); setFailureKind(null); setCanRetry(false); setResult(null); setLock(true)
    setCanResubmit(false)
    setProgress(current => ({ ...current, message: submit ? 'サーバーへ受付中' : '再接続中', eta: '' }))
    try {
      if (submit) {
        checkCurrent()
        let response: Response | null = null
        try {
          response = await authFetch('/api/scrape', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              start_date: job.startDate, end_date: job.endDate, force_rescrape: job.forceRescrape,
              server_batch: true, job_id: job.jobId, operation_id: job.operationId,
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
          if (response.ok && isRecord(payload) && payload.job_id === job.jobId) publish(payload)
          // Missing IDs, mismatched IDs and 5xx are resolved via the original ID.
        }
      }
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
            signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]),
          })
        } catch {
          checkCurrent()
          if (++failures >= maxFailures) throw new BatchScrapeError('通信を確認できません。再接続して進捗を確認できます。', 'monitoring', false, job.jobId)
          setProgress(current => ({ ...current, message: '再接続中', eta: '' }))
          continue
        }
        checkCurrent()
        if (response.status === 401 || response.status === 403) {
          throw new BatchScrapeError(AUTH_MESSAGE, 'authentication', false, job.jobId)
        }
        const payload: unknown = await response.json().catch(() => null)
        checkCurrent()
        if (!response.ok || !isRecord(payload) || payload.status === 'not_found'
          || payload.job_id !== job.jobId
          || !['queued', 'running', 'recovering', 'waiting_resources', 'cancelling', 'completed', 'error', 'failed', 'cancelled'].includes(String(payload.status))) {
          if (++failures >= maxFailures) {
            throw new BatchScrapeError('サーバーの受付・実行状態を確認できません。保存したジョブIDで再確認してください。', 'monitoring', false, job.jobId)
          }
          setProgress(current => ({ ...current, message: '再接続中', eta: '' }))
          continue
        }
        failures = 0
        const rawStatus = payload.status
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
            throw new BatchScrapeError(COMPLETED_CONTRACT_MESSAGE, 'monitoring', false, job.jobId)
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
          removeStored(job); pendingRef.current = null; setLock(false)
          throw new BatchScrapeError(
            rawStatus === 'cancelled' ? '取得を停止しました。保存済みデータは保持されています。'
              : typeof payload.error === 'string' ? payload.error : 'データ取得が失敗しました',
            rawStatus === 'cancelled' ? 'cancelled' : 'execution', rawStatus !== 'cancelled', job.jobId,
          )
        }
        setStatus(rawStatus === 'queued' ? 'queued' : rawStatus === 'cancelling' ? 'cancelling' : 'running')
      }
    } catch (cause) {
      const failure = !isCurrent() ? new BatchScrapeError(DETACHED_MESSAGE, 'client_stop', false, job.jobId)
        : cause instanceof BatchScrapeError ? cause
          : new BatchScrapeError(cause instanceof Error ? cause.message : '状態を再確認してください', 'monitoring', false, job.jobId)
      if (isCurrent()) {
        setStatus(failure.kind === 'cancelled' ? 'cancelled' : 'error')
        setError(failure.kind === 'cancelled' ? null : failure.message)
        setFailureKind(failure.kind); setCanRetry(failure.safeToRetry)
        setCanResubmit(failure.kind === 'monitoring' && pendingRef.current?.origin === 'submitted')
      }
      throw failure
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null; inFlightRef.current = false
        if (mountedRef.current && ownerRef.current === job.ownerUserId) setLoading(false)
      }
    }
  }, [maxFailures, maxAttempts, maxDuration, pollIntervalMs, setLock])

  const reconnect = useCallback(async (knownJob?: BatchAcceptedJob): Promise<BatchResult | null> => {
    if (!ownerUserId || ownerRef.current !== ownerUserId || inFlightRef.current || !mountedRef.current) return null
    const stored = readStored(ownerUserId)
    if (stored && knownJob && stored.jobId !== knownJob.jobId) return null
    // knownJob must come from the owner-filtered server history, never a URL.
    const job = stored ?? (knownJob ? {
      version: 1 as const, ownerUserId, jobId: knownJob.jobId, operationId: knownJob.jobId,
      startDate: knownJob.startDate, endDate: knownJob.endDate, forceRescrape: false, createdAt: knownJob.acceptedAt,
      origin: 'history' as const,
    } : null)
    if (!job) return null
    if (!stored) saveStored(job)
    inFlightRef.current = true
    return observe(job, false)
  }, [observe, ownerUserId])

  useEffect(() => {
    mountedRef.current = true
    setStatus('idle'); setJobId(null); setResult(null); setError(null); setFailureKind(null)
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

  const start = useCallback(async (startPeriod: string, endPeriod: string, forceRescrape: boolean): Promise<BatchResult> => {
    if (!mountedRef.current || !ownerUserId || ownerRef.current !== ownerUserId) throw new BatchScrapeError(AUTH_MESSAGE, 'authentication', false)
    if (inFlightRef.current || executionLockedRef.current) throw new BatchScrapeError('取得状態を再確認してください', 'busy', false, pendingRef.current?.jobId)
    if (!PERIOD_PATTERN.test(startPeriod) || !PERIOD_PATTERN.test(endPeriod) || startPeriod > endPeriod) {
      const failure = new BatchScrapeError('期間指定が不正です（開始・終了をYYYY-MMで指定）', 'validation', false)
      setStatus('error'); setFailureKind(failure.kind); setError(failure.message)
      throw failure
    }
    inFlightRef.current = true
    try {
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
        }
        // Persist before crossing the network, including the idempotency identity.
        saveStored(job)
        return job
      }
      const job = globalThis.navigator?.locks ? await navigator.locks.request(storageKey(ownerUserId), reserve) : reserve()
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
      const response = await authFetch(`/api/scrape/status/${encodeURIComponent(saved.jobId)}`, {
        cache: 'no-store', signal: AbortSignal.timeout(10000),
      })
      const payload: unknown = await response.json().catch(() => null)
      if (!mountedRef.current || ownerRef.current !== ownerUserId) return null
      if (!response.ok || !isRecord(payload) || payload.job_id !== saved.jobId) {
        throw new BatchScrapeError(response.status === 401 || response.status === 403 ? AUTH_MESSAGE : '受付状態を確認できないため再送していません。', 'monitoring', false, saved.jobId)
      }
      if (payload.status !== 'not_found') return await observe(saved, false)
      if (!confirmSameRequest()) return null
      if (JSON.stringify(readStored(ownerUserId)) !== JSON.stringify(saved)) {
        throw new BatchScrapeError('保存された依頼が変わったため再送していません。', 'monitoring', false, saved.jobId)
      }
      // Explicit retry is idempotent: no new IDs, period or force mode. History
      // adoption has no original operation identity and can never take this path.
      return await observe(saved, true, true)
    } catch (cause) {
      if (mountedRef.current && ownerRef.current === ownerUserId) {
        setError(cause instanceof Error ? cause.message : '再送できませんでした。状態を再確認してください。')
      }
      throw cause
    } finally {
      if (ownerRef.current === ownerUserId && !controllerRef.current) {
        inFlightRef.current = false
        if (mountedRef.current) setLoading(false)
      }
    }
  }, [observe, ownerUserId])

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
  return { loading, status, error, failureKind, canRetry, canResubmit, isExecutionLocked, jobId, progress, result,
    start, abort, reconnect, resubmit, requestBatchStop, clearExecutionLockAfterReconciliation }
}
