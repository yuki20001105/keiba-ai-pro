'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import Link from 'next/link'
import { Logo } from '@/components/Logo'
import { Toast } from '@/components/Toast'
import { authFetch } from '@/lib/auth-fetch'
import { formatApiErrorDetail } from '@/lib/api-error'
import {
  aggregateDryRunResults,
  enumerateMonthDateRanges,
  type DryRunResult as ScrapeDryRunResult,
} from '@/lib/dry-run-batch'
import { BatchScrapeError, useBatchScrape } from '@/hooks/useBatchScrape'
import {
  UNCERTAINTY_REVIEW_STORAGE_KEY,
  UNCERTAINTY_STORAGE_KEY,
  createPendingUncertaintyReview,
  fingerprintUncertaintyLock,
  parsePendingUncertaintyReview,
  parsePersistedUncertaintyLock,
  reviewMatchesLock,
  validateReviewReason,
  type BatchRequestSnapshot,
  type PendingUncertaintyReview,
  type PersistedUncertaintyLock,
  type UncertaintyFailureKind,
} from '@/lib/scrape-uncertainty-approval'
import {
  UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY,
  UNCERTAINTY_SERVER_REVIEW_LOCATOR_WRITE_LOCK_NAME,
  buildServerReviewSubmission,
  createServerReviewLocator,
  parseReviewResponseEnvelope,
  parseServerReviewLocator,
  serverRecordMatchesLocalReview,
  serverRecordMatchesLocator,
  type ScrapeUncertaintyReviewLocator,
  type ScrapeUncertaintyReviewRecord,
} from '@/lib/scrape-uncertainty-review-server'

const ACTIVE_DRY_RUN_JOB_KEY = 'keiba-ai-pro:active-dry-run-job:v1'
const DRY_RUN_TIMEOUT_MS = 24 * 60 * 60 * 1000

type ScrapeHealthStatus = 'healthy' | 'degraded' | 'unhealthy' | 'unknown'
type LocalApiStatus = 'checking' | ScrapeHealthStatus

type StoredDryRunJob = {
  jobId: string
  startedAt: number
}

type FetchSummaryHistoryItem = {
  job_id: string
  status: string
  created_at?: string
  updated_at?: string
  fetch_summary?: {
    mode?: string
    start_date?: string
    end_date?: string
    saved_races?: number
    saved_horses?: number
    existing_races_skipped?: number
    verified_no_race_dates?: number
    execution_mode?: string
    elapsed_time_sec?: number
    dry_run?: {
      estimated_request_count?: number
      cache_hit_count?: number
      cache_miss_count?: number
      resume_hit_count?: number
      db_existing_skip_count?: number
      new_fetch_required_count?: number
      already_covered_count?: number
      estimated_runtime_sec?: number
    }
    metrics?: {
      network_requests?: number
      cache_hits?: number
      resume_hits?: number
      retry_count?: number
      status_429?: number
      status_403?: number
      status_500?: number
      status_503?: number
      timeout_count?: number
    }
  }
}

type PeriodValidation = {
  ok: boolean
  message?: string
}

const PERIOD_PATTERN = /^(\d{4})-(0[1-9]|1[0-2])$/
const COMPLETED_CONTRACT_MESSAGE = '完了応答の形式を確認できないため、サーバージョブの状態確認が必要'

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function hasStrictCompletedResult(payload: unknown): boolean {
  if (!isObject(payload)) return false
  const racesCollected = payload.races_collected
  return typeof racesCollected === 'number'
    && Number.isFinite(racesCollected)
    && Number.isInteger(racesCollected)
    && racesCollected >= 0
}

function validatePeriodRange(startPeriod: string, endPeriod: string): PeriodValidation {
  if (!PERIOD_PATTERN.test(startPeriod) || !PERIOD_PATTERN.test(endPeriod)) {
    return { ok: false, message: '期間指定が不正です（YYYY-MM形式、月は01〜12）' }
  }

  const [startYearStr, startMonthStr] = startPeriod.split('-')
  const [endYearStr, endMonthStr] = endPeriod.split('-')
  const sy = Number(startYearStr)
  const sm = Number(startMonthStr)
  const ey = Number(endYearStr)
  const em = Number(endMonthStr)

  if (![sy, sm, ey, em].every(Number.isFinite)) {
    return { ok: false, message: '期間指定に数値以外が含まれています' }
  }
  if (sm < 1 || sm > 12 || em < 1 || em > 12) {
    return { ok: false, message: '月の指定が不正です（01〜12）' }
  }
  if (new Date(sy, sm - 1, 1) > new Date(ey, em - 1, 1)) {
    return { ok: false, message: '開始年月は終了年月以前にしてください' }
  }

  const totalMonths = (ey - sy) * 12 + (em - sm) + 1
  if (!Number.isFinite(totalMonths) || totalMonths <= 0) {
    return { ok: false, message: '期間内の対象月が0件です' }
  }

  return { ok: true }
}

function normalizeDryRunResult(resultPayload: any): ScrapeDryRunResult {
  const fetchSummary = resultPayload?.fetch_summary
  const dryRun = fetchSummary?.dry_run
  if (!fetchSummary || typeof fetchSummary !== 'object' || !dryRun || typeof dryRun !== 'object') {
    throw new Error('Dry-run結果形式が不正です（fetch_summary.dry_run）')
  }

  const parseStrictNumber = (value: unknown, key: string, integerOnly: boolean): number => {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
      throw new Error(`Dry-run結果形式が不正です（${key}）`)
    }
    if (integerOnly && !Number.isInteger(value)) {
      throw new Error(`Dry-run結果形式が不正です（${key}）`)
    }
    return value
  }

  return {
    dry_run: {
      total_target_count: parseStrictNumber(dryRun.total_target_count, 'total_target_count', true),
      unique_url_count: parseStrictNumber(dryRun.unique_url_count, 'unique_url_count', true),
      estimated_request_count: parseStrictNumber(dryRun.estimated_request_count, 'estimated_request_count', true),
      cache_hit_count: parseStrictNumber(dryRun.cache_hit_count, 'cache_hit_count', true),
      cache_miss_count: parseStrictNumber(dryRun.cache_miss_count, 'cache_miss_count', true),
      resume_hit_count: parseStrictNumber(dryRun.resume_hit_count, 'resume_hit_count', true),
      skipped_count: parseStrictNumber(dryRun.skipped_count, 'skipped_count', true),
      db_existing_skip_count: parseStrictNumber(dryRun.db_existing_skip_count, 'db_existing_skip_count', true),
      db_existing_race_count: parseStrictNumber(dryRun.db_existing_race_count, 'db_existing_race_count', true),
      db_existing_horse_count: parseStrictNumber(dryRun.db_existing_horse_count, 'db_existing_horse_count', true),
      db_existing_result_count: parseStrictNumber(dryRun.db_existing_result_count, 'db_existing_result_count', true),
      db_existing_pedigree_count: parseStrictNumber(dryRun.db_existing_pedigree_count, 'db_existing_pedigree_count', true),
      new_fetch_required_count: parseStrictNumber(dryRun.new_fetch_required_count, 'new_fetch_required_count', true),
      already_covered_count: parseStrictNumber(dryRun.already_covered_count, 'already_covered_count', true),
      estimated_runtime_sec: parseStrictNumber(dryRun.estimated_runtime_sec, 'estimated_runtime_sec', false),
    },
    rate_limit_policy: fetchSummary?.rate_limit_policy || {},
    retry_backoff_policy: fetchSummary?.retry_backoff_policy || {},
    circuit_breaker_policy: fetchSummary?.circuit_breaker_policy || {},
  }
}

export default function DataCollectionPage() {
  const e2ePollIntervalRaw = process.env.NEXT_PUBLIC_E2E_BATCH_POLL_INTERVAL_MS
  const e2ePollInterval = e2ePollIntervalRaw ? Number(e2ePollIntervalRaw) : undefined
  const batchScrapeOptions = Number.isFinite(e2ePollInterval) && (e2ePollInterval as number) >= 0
    ? { pollIntervalMs: e2ePollInterval as number }
    : undefined
  // 期間指定用
  const now = new Date()
  const [startPeriod, setStartPeriod] = useState(`${now.getFullYear() - 1}-01`)
  const [endPeriod, setEndPeriod] = useState(
    `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
  )
  const forceRescrape = false

  // A resume link lets an interrupted long-running batch reopen at the exact
  // month without relying on browser-specific month-input automation.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const resumeStart = params.get('start')
    const resumeEnd = params.get('end')
    if (!resumeStart || !resumeEnd) return

    const validation = validatePeriodRange(resumeStart, resumeEnd)
    if (!validation.ok) return
    setStartPeriod(resumeStart)
    setEndPeriod(resumeEnd)
  }, [])
  const [dryRunLoading, setDryRunLoading] = useState(false)
  const [dryRunStartedAt, setDryRunStartedAt] = useState<number | null>(null)
  const [dryRunElapsedSeconds, setDryRunElapsedSeconds] = useState(0)
  const [dryRunError, setDryRunError] = useState('')
  const [dryRunResultReady, setDryRunResultReady] = useState(false)
  const [dryRunResult, setDryRunResult] = useState<ScrapeDryRunResult | null>(null)
  const [dryRunExecuted, setDryRunExecuted] = useState(false)

  useEffect(() => {
    if (dryRunStartedAt == null) return
    const updateElapsed = () => {
      setDryRunElapsedSeconds(Math.floor(Math.max(0, Date.now() - dryRunStartedAt) / 1000))
    }
    updateElapsed()
    const timer = window.setInterval(updateElapsed, 1000)
    return () => window.clearInterval(timer)
  }, [dryRunStartedAt])
  const [dryRunPendingMessage, setDryRunPendingMessage] = useState('')
  const [dryRunErrorMessage, setDryRunErrorMessage] = useState('')
  const [periodErrorMessage, setPeriodErrorMessage] = useState('')
  const [retrySnapshot, setRetrySnapshot] = useState<BatchRequestSnapshot | null>(null)
  const [executeWarn, setExecuteWarn] = useState('')
  const [persistedUncertainty, setPersistedUncertainty] = useState<PersistedUncertaintyLock | null>(null)
  const [uncertaintyHydrated, setUncertaintyHydrated] = useState(false)
  const [uncertaintyStorageBlocked, setUncertaintyStorageBlocked] = useState(false)
  const [transientUncertaintyDismissed, setTransientUncertaintyDismissed] = useState(false)
  const [reconcileLoading, setReconcileLoading] = useState(false)
  const [reconcileMessage, setReconcileMessage] = useState('')
  const [reviewHydrated, setReviewHydrated] = useState(false)
  const [pendingReview, setPendingReview] = useState<PendingUncertaintyReview | null>(null)
  const [reviewReason, setReviewReason] = useState('')
  const [ackServerStateUnverified, setAckServerStateUnverified] = useState(false)
  const [ackNoUnlockOrRetry, setAckNoUnlockOrRetry] = useState(false)
  const [reviewPersistError, setReviewPersistError] = useState('')
  const [serverReviewHydrated, setServerReviewHydrated] = useState(false)
  const [serverReviewLocator, setServerReviewLocator] = useState<ScrapeUncertaintyReviewLocator | null>(null)
  const [serverReviewRecord, setServerReviewRecord] = useState<ScrapeUncertaintyReviewRecord | null>(null)
  const [serverReviewSubmissionReceipt, setServerReviewSubmissionReceipt] = useState<ScrapeUncertaintyReviewRecord | null>(null)
  const [serverReviewSubmitLoading, setServerReviewSubmitLoading] = useState(false)
  const [serverReviewStatusLoading, setServerReviewStatusLoading] = useState(false)
  const [serverReviewError, setServerReviewError] = useState('')
  const [serverReviewLastCheckedAt, setServerReviewLastCheckedAt] = useState<string | null>(null)
  const [fetchHistory, setFetchHistory] = useState<FetchSummaryHistoryItem[]>([])
  const [fetchHistoryLoading, setFetchHistoryLoading] = useState(false)
  const [toast, setToast] = useState({ visible: false, message: '', type: 'success' as 'success' | 'error' })
  const showToast = (message: string, type: 'success' | 'error' = 'success') =>
    setToast({ visible: true, message, type })

  // バッチスクレイピング（月単位ループ + ポーリングをフックが担当）
  const {
    loading: batchLoading,
    status: batchStatus,
    error: batchError,
    failureKind,
    canRetry,
    isExecutionLocked,
    jobId: activeJobId,
    progress: batchProgress,
    result: batchResult,
    start: startBatchScrape,
    clearExecutionLockAfterReconciliation,
  } = useBatchScrape(batchScrapeOptions)
  const lastRequestSnapshotRef = useRef<BatchRequestSnapshot | null>(null)
  const serverReviewInitialFetchRef = useRef<string | null>(null)
  const serverReviewLocatorRef = useRef<ScrapeUncertaintyReviewLocator | null>(null)
  const serverReviewLoadGenerationRef = useRef(0)
  const replaceActiveServerReview = useCallback((
    locator: ScrapeUncertaintyReviewLocator | null,
    record: ScrapeUncertaintyReviewRecord | null = null,
  ) => {
    serverReviewLoadGenerationRef.current += 1
    serverReviewLocatorRef.current = locator
    serverReviewInitialFetchRef.current = null
    setServerReviewLocator(locator)
    setServerReviewRecord(record)
    setServerReviewStatusLoading(false)
    setServerReviewLastCheckedAt(null)
  }, [])
  const isBatchBusy = batchLoading || batchStatus === 'queued' || batchStatus === 'running'
  const isOperationBusy = isBatchBusy || dryRunLoading
  const periodValidation = validatePeriodRange(startPeriod, endPeriod)
  const isPeriodValid = periodValidation.ok

  // データ統計と表示
  const [dataStats, setDataStats] = useState({ totalRaces: 0, totalResults: 0, latestDate: '' })

  // ローカルAPI稼働チェック
  const [localApiStatus, setLocalApiStatus] = useState<LocalApiStatus>('checking')
  const [localApiReason, setLocalApiReason] = useState('')
  const dryRunTrackingRef = useRef<string | null>(null)

  const statusMeta: Record<LocalApiStatus, { label: string; dotClass: string; textClass: string }> = {
    checking: { label: '確認中', dotClass: 'bg-[#555] animate-pulse', textClass: 'text-[#555]' },
    healthy: { label: '稼働中', dotClass: 'bg-[#4ade80]', textClass: 'text-[#4ade80]' },
    degraded: { label: '不安定', dotClass: 'bg-[#facc15]', textClass: 'text-[#facc15]' },
    unhealthy: { label: '停止中', dotClass: 'bg-[#f87171]', textClass: 'text-[#f87171]' },
    unknown: { label: '確認不可', dotClass: 'bg-[#9ca3af]', textClass: 'text-[#9ca3af]' },
  }

  const isApiUnavailable = localApiStatus === 'unhealthy' || localApiStatus === 'unknown'
  const effectivePeriodError = periodErrorMessage || (!isPeriodValid ? periodValidation.message || '' : '')
  const transientUncertaintyKind: UncertaintyFailureKind | null = failureKind === 'monitoring' || failureKind === 'client_stop'
    ? failureKind
    : null
  const effectiveTransientUncertainty = transientUncertaintyDismissed ? null : transientUncertaintyKind
  const effectiveUncertaintyKind = persistedUncertainty?.failureKind ?? effectiveTransientUncertainty
  const shouldShowUncertaintyWarning = effectiveUncertaintyKind === 'monitoring' || effectiveUncertaintyKind === 'client_stop'
  const effectiveUncertaintyJobId = persistedUncertainty?.jobId ?? activeJobId ?? null
  const joblessReviewLock = persistedUncertainty && !persistedUncertainty.jobId ? persistedUncertainty : null
  const reviewReasonIsValid = validateReviewReason(reviewReason) !== null
  const canRecordPendingReview = Boolean(
    uncertaintyHydrated
    && reviewHydrated
    && !uncertaintyStorageBlocked
    && joblessReviewLock
    && !pendingReview
    && reviewReasonIsValid
    && ackServerStateUnverified
    && ackNoUnlockOrRetry,
  )
  const canSubmitServerReview = Boolean(
    uncertaintyHydrated
    && reviewHydrated
    && serverReviewHydrated
    && !uncertaintyStorageBlocked
    && joblessReviewLock
    && pendingReview
    && reviewMatchesLock(pendingReview, joblessReviewLock)
    && !serverReviewLocator
    && serverReviewSubmissionReceipt?.client_request_id !== pendingReview.requestId
    && !serverReviewSubmitLoading
    && !serverReviewStatusLoading,
  )
  const executeBlockedByUncertainty = !uncertaintyHydrated
    || !reviewHydrated
    || !serverReviewHydrated
    || uncertaintyStorageBlocked
    || isExecutionLocked
    || shouldShowUncertaintyWarning

  const persistUncertaintyLock = useCallback((kind: UncertaintyFailureKind, jobId: string | null, request: BatchRequestSnapshot | null) => {
    if (!request) return
    const lock: PersistedUncertaintyLock = {
      version: 1,
      failureKind: kind,
      occurredAt: new Date().toISOString(),
      request,
      ...(jobId && jobId.trim().length > 0 ? { jobId: jobId.trim() } : {}),
    }
    try {
      localStorage.setItem(UNCERTAINTY_STORAGE_KEY, JSON.stringify(lock))
      localStorage.removeItem(UNCERTAINTY_REVIEW_STORAGE_KEY)
      localStorage.removeItem(UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY)
      setUncertaintyStorageBlocked(false)
    } catch {
      setUncertaintyStorageBlocked(true)
    }
    setPersistedUncertainty(lock)
    setPendingReview(null)
    setServerReviewSubmissionReceipt(null)
    replaceActiveServerReview(null)
    setServerReviewError('')
    setReviewReason('')
    setAckServerStateUnverified(false)
    setAckNoUnlockOrRetry(false)
    setTransientUncertaintyDismissed(false)
  }, [replaceActiveServerReview])

  const clearPersistedUncertainty = (): boolean => {
    try {
      localStorage.removeItem(UNCERTAINTY_STORAGE_KEY)
      localStorage.removeItem(UNCERTAINTY_REVIEW_STORAGE_KEY)
      localStorage.removeItem(UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY)
    } catch {
      setUncertaintyStorageBlocked(true)
      return false
    }
    setPersistedUncertainty(null)
    setPendingReview(null)
    setServerReviewSubmissionReceipt(null)
    replaceActiveServerReview(null)
    setServerReviewError('')
    setReviewPersistError('')
    setUncertaintyStorageBlocked(false)
    setTransientUncertaintyDismissed(true)
    return true
  }

  const handleRecordPendingReview = () => {
    if (!joblessReviewLock || !canRecordPendingReview) return
    setReviewPersistError('')
    if (!globalThis.crypto?.randomUUID) {
      setReviewPersistError('承認依頼IDを安全に生成できないため記録できません。lockを維持します。')
      return
    }
    try {
      const durableLockRaw = localStorage.getItem(UNCERTAINTY_STORAGE_KEY)
      const durableLock = parsePersistedUncertaintyLock(JSON.parse(durableLockRaw || 'null'))
      if (!durableLock || durableLock.jobId || fingerprintUncertaintyLock(durableLock) !== fingerprintUncertaintyLock(joblessReviewLock)) {
        throw new Error('durable lock verification failed')
      }
      const review = createPendingUncertaintyReview({
        lock: durableLock,
        requestId: globalThis.crypto.randomUUID(),
        requestedAt: new Date().toISOString(),
        reason: reviewReason,
        serverStateUnverified: ackServerStateUnverified,
        noUnlockOrRetry: ackNoUnlockOrRetry,
      })
      if (!review) throw new Error('review input validation failed')
      localStorage.setItem(UNCERTAINTY_REVIEW_STORAGE_KEY, JSON.stringify(review))
      const readBack = parsePendingUncertaintyReview(JSON.parse(localStorage.getItem(UNCERTAINTY_REVIEW_STORAGE_KEY) || 'null'))
      const lockReadBack = parsePersistedUncertaintyLock(JSON.parse(localStorage.getItem(UNCERTAINTY_STORAGE_KEY) || 'null'))
      if (!readBack || !lockReadBack || !reviewMatchesLock(readBack, lockReadBack)) {
        throw new Error('review persistence verification failed')
      }
      setPendingReview(readBack)
    } catch {
      try {
        localStorage.removeItem(UNCERTAINTY_REVIEW_STORAGE_KEY)
      } catch {
        setUncertaintyStorageBlocked(true)
      }
      setReviewPersistError('承認依頼を保存できません。pending扱いにはせず、lockを維持します。')
      setPendingReview(null)
    }
  }

  const loadServerReviewStatus = useCallback(async (
    locator: ScrapeUncertaintyReviewLocator,
    localReview: PendingUncertaintyReview,
  ) => {
    const generation = serverReviewLoadGenerationRef.current + 1
    serverReviewLoadGenerationRef.current = generation
    const requestIsCurrent = () => {
      const active = serverReviewLocatorRef.current
      return serverReviewLoadGenerationRef.current === generation
        && active !== null
        && active.requestId === locator.requestId
        && active.clientRequestId === locator.clientRequestId
        && active.requestPayloadHash === locator.requestPayloadHash
    }
    setServerReviewStatusLoading(true)
    setServerReviewError('')
    try {
      const response = await authFetch(
        `/api/scrape/uncertainty-review-requests/${encodeURIComponent(locator.requestId)}`,
        { signal: AbortSignal.timeout(8_000), cache: 'no-store' },
      )
      const payload = await response.json().catch(() => null)
      if (!response.ok) {
        const detail = payload && typeof payload.detail === 'string' ? payload.detail : `HTTP ${response.status}`
        throw new Error(detail)
      }
      const parsed = parseReviewResponseEnvelope(payload)
      if (!parsed.ok
        || !serverRecordMatchesLocator(parsed.value, locator)
        || !serverRecordMatchesLocalReview(parsed.value, localReview)) {
        throw new Error('server review response did not match the durable local evidence')
      }
      if (!requestIsCurrent()) return
      setServerReviewRecord(parsed.value)
      setServerReviewLastCheckedAt(new Date().toISOString())
    } catch {
      if (!requestIsCurrent()) return
      setServerReviewRecord(null)
      setServerReviewError('サーバー監査依頼の状態を確認できません。lockを維持し、自動再試行は行いません。')
    } finally {
      if (requestIsCurrent()) setServerReviewStatusLoading(false)
    }
  }, [])

  const handleSubmitServerReview = async () => {
    if (!canSubmitServerReview || !joblessReviewLock || !pendingReview) return
    setServerReviewSubmitLoading(true)
    setServerReviewError('')
    let acceptedRecord: ScrapeUncertaintyReviewRecord | null = null
    try {
      const lockRawBefore = localStorage.getItem(UNCERTAINTY_STORAGE_KEY)
      const reviewRawBefore = localStorage.getItem(UNCERTAINTY_REVIEW_STORAGE_KEY)
      const locatorRawBefore = localStorage.getItem(UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY)
      const locatorGenerationBefore = serverReviewLoadGenerationRef.current
      const durableLock = parsePersistedUncertaintyLock(JSON.parse(lockRawBefore || 'null'))
      const durableReview = parsePendingUncertaintyReview(JSON.parse(reviewRawBefore || 'null'))
      if (!durableLock
        || durableLock.jobId
        || !durableReview
        || !reviewMatchesLock(durableReview, durableLock)
        || durableReview.requestId !== pendingReview.requestId
        || reviewRawBefore !== JSON.stringify(pendingReview)
        || locatorRawBefore !== null
        || serverReviewLocatorRef.current !== null
        || fingerprintUncertaintyLock(durableLock) !== fingerprintUncertaintyLock(joblessReviewLock)) {
        throw new Error('durable local review verification failed')
      }

      const response = await authFetch('/api/scrape/uncertainty-review-requests', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildServerReviewSubmission(durableReview)),
        signal: AbortSignal.timeout(10_000),
      })
      const payload = await response.json().catch(() => null)
      if (!response.ok) {
        const detail = payload && typeof payload.detail === 'string' ? payload.detail : `HTTP ${response.status}`
        throw new Error(detail)
      }
      const parsed = parseReviewResponseEnvelope(payload)
      if (!parsed.ok || !serverRecordMatchesLocalReview(parsed.value, durableReview)) {
        throw new Error('server review response validation failed')
      }
      acceptedRecord = parsed.value
      setServerReviewSubmissionReceipt(parsed.value)
      const locator = createServerReviewLocator(parsed.value)

      if (!navigator.locks?.request) {
        setUncertaintyStorageBlocked(true)
        throw new Error('cross-tab storage serialization is unavailable')
      }

      await navigator.locks.request(UNCERTAINTY_SERVER_REVIEW_LOCATOR_WRITE_LOCK_NAME, { mode: 'exclusive' }, () => {
        if (localStorage.getItem(UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY) !== locatorRawBefore
          || serverReviewLocatorRef.current !== null
          || serverReviewLoadGenerationRef.current !== locatorGenerationBefore
          || localStorage.getItem(UNCERTAINTY_STORAGE_KEY) !== lockRawBefore
          || localStorage.getItem(UNCERTAINTY_REVIEW_STORAGE_KEY) !== reviewRawBefore) {
          setUncertaintyStorageBlocked(true)
          throw new Error('durable review evidence changed during server submission')
        }
        replaceActiveServerReview(locator, parsed.value)
        setServerReviewLastCheckedAt(new Date().toISOString())
        try {
          localStorage.setItem(UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY, JSON.stringify(locator))
          const locatorReadBack = parseServerReviewLocator(JSON.parse(
            localStorage.getItem(UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY) || 'null',
          ))
          if (!locatorReadBack
            || !serverRecordMatchesLocator(parsed.value, locatorReadBack)
            || localStorage.getItem(UNCERTAINTY_STORAGE_KEY) !== lockRawBefore
            || localStorage.getItem(UNCERTAINTY_REVIEW_STORAGE_KEY) !== reviewRawBefore) {
            throw new Error('server review locator persistence verification failed')
          }
        } catch {
          serverReviewInitialFetchRef.current = [
            locator.requestId,
            locator.clientRequestId,
            locator.requestPayloadHash,
          ].join(':')
          setUncertaintyStorageBlocked(true)
          throw new Error('server review was accepted but locator persistence failed')
        }
      })
    } catch {
      setServerReviewError(acceptedRecord
        ? `サーバーはrequest_id ${acceptedRecord.request_id}を受理しましたが、参照情報を確定保存できません。同一画面からの再送は停止しています。再読み込み後は同じclient_request_idによる冪等照合だけを行い、lockを維持してください。`
        : 'サーバー監査依頼を確定できません。lockを維持し、自動再送・取消は行いません。')
    } finally {
      setServerReviewSubmitLoading(false)
    }
  }

  const checkLocalApi = async () => {
    setLocalApiStatus('checking')
    setLocalApiReason('')
    try {
      const res = await authFetch('/api/scrape/health', { signal: AbortSignal.timeout(4000) })
      const data = await res.json().catch(() => ({}))
      const status = String(data?.status || '') as ScrapeHealthStatus
      const resolved: ScrapeHealthStatus = ['healthy', 'degraded', 'unhealthy', 'unknown'].includes(status)
        ? status
        : res.ok
          ? 'unknown'
          : 'unhealthy'
      setLocalApiStatus(resolved)
      setLocalApiReason(typeof data?.reason === 'string' ? data.reason : '')
    } catch {
      setLocalApiStatus('unknown')
      setLocalApiReason('health check request failed')
    }
  }

  useEffect(() => {
    loadStats()
    checkLocalApi()
  }, [])

  useEffect(() => {
    const syncFromStorage = () => {
      try {
        const raw = localStorage.getItem(UNCERTAINTY_STORAGE_KEY)
        if (!raw) {
          const orphanedReviewRaw = localStorage.getItem(UNCERTAINTY_REVIEW_STORAGE_KEY)
          const orphanedLocatorRaw = localStorage.getItem(UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY)
          const hasOrphanedReviewEvidence = orphanedReviewRaw !== null || orphanedLocatorRaw !== null
          setPersistedUncertainty(null)
          setPendingReview(null)
          replaceActiveServerReview(null)
          setUncertaintyStorageBlocked(hasOrphanedReviewEvidence)
          if (hasOrphanedReviewEvidence) {
            setReconcileMessage('lock本体が見つかりませんが承認・監査証跡が残っています。解除証拠として扱わず、新規実行を停止しています。')
          }
        } else {
          let decoded: unknown = null
          try {
            decoded = JSON.parse(raw)
          } catch {
            decoded = null
          }
          const parsed = parsePersistedUncertaintyLock(decoded)
          if (!parsed) {
            setPersistedUncertainty(null)
            setPendingReview(null)
            replaceActiveServerReview(null)
            setUncertaintyStorageBlocked(true)
            setReconcileMessage('保存済みlockの形式を確認できません。自動削除せず、新規実行を停止しています。')
          } else {
            setPersistedUncertainty(parsed)
            setUncertaintyStorageBlocked(false)
            setReconcileMessage('前回の実行は状態不明で終了しました。新規取得前に状態を再確認してください。')
            if (!parsed.jobId) {
              const reviewRaw = localStorage.getItem(UNCERTAINTY_REVIEW_STORAGE_KEY)
              let reviewDecoded: unknown = null
              try {
                reviewDecoded = reviewRaw ? JSON.parse(reviewRaw) : null
              } catch {
                reviewDecoded = null
              }
              const restored = parsePendingUncertaintyReview(reviewDecoded)
              if (restored && reviewMatchesLock(restored, parsed)) {
                setPendingReview(restored)
                setReviewPersistError('')
                const locatorRaw = localStorage.getItem(UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY)
                let locatorDecoded: unknown = null
                try {
                  locatorDecoded = locatorRaw ? JSON.parse(locatorRaw) : null
                } catch {
                  locatorDecoded = null
                }
                const restoredLocator = parseServerReviewLocator(locatorDecoded)
                if (restoredLocator && restoredLocator.clientRequestId === restored.requestId) {
                  replaceActiveServerReview(restoredLocator)
                  setServerReviewError('')
                } else {
                  replaceActiveServerReview(null)
                  if (locatorRaw) {
                    setServerReviewError('保存済みサーバー監査参照は現在の承認依頼と一致しません。lockを維持します。')
                  }
                }
              } else {
                setPendingReview(null)
                replaceActiveServerReview(null)
                if (reviewRaw) setReviewPersistError('保存済み承認依頼は現在のlockと一致しないため無効です。lockは維持します。')
              }
            } else {
              setPendingReview(null)
              replaceActiveServerReview(null)
            }
          }
        }
      } catch {
        setPersistedUncertainty(null)
        setPendingReview(null)
        replaceActiveServerReview(null)
        setUncertaintyStorageBlocked(true)
        setReconcileMessage('lock保存領域を確認できません。新規実行を停止しています。')
      } finally {
        setUncertaintyHydrated(true)
        setReviewHydrated(true)
        setServerReviewHydrated(true)
      }
    }

    const handleStorage = (event: StorageEvent) => {
      if (event.key !== null
        && event.key !== UNCERTAINTY_STORAGE_KEY
        && event.key !== UNCERTAINTY_REVIEW_STORAGE_KEY
        && event.key !== UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY) return
      if (
        event.key === null
        || (event.key === UNCERTAINTY_STORAGE_KEY && event.oldValue !== null && event.newValue === null)
      ) {
        setUncertaintyStorageBlocked(true)
        setPendingReview(null)
        replaceActiveServerReview(null)
        setReconcileMessage('別タブでlock保存領域が削除されました。解除証拠として扱わず、新規実行を停止しています。')
        setUncertaintyHydrated(true)
        setReviewHydrated(true)
        setServerReviewHydrated(true)
        let previousLock: ReturnType<typeof parsePersistedUncertaintyLock> = null
        if (event.oldValue) {
          try {
            previousLock = parsePersistedUncertaintyLock(JSON.parse(event.oldValue))
          } catch {
            previousLock = null
          }
        }
        if (previousLock) setPersistedUncertainty(previousLock)
        return
      }
      setUncertaintyHydrated(false)
      setReviewHydrated(false)
      setServerReviewHydrated(false)
      if (event.key === UNCERTAINTY_SERVER_REVIEW_LOCATOR_STORAGE_KEY && event.newValue === null) {
        replaceActiveServerReview(null)
        setServerReviewError('別タブでサーバー監査参照が削除されました。解除証拠として扱わず、lockを維持します。')
      }
      syncFromStorage()
    }

    syncFromStorage()
    window.addEventListener('storage', handleStorage)
    return () => window.removeEventListener('storage', handleStorage)
  }, [replaceActiveServerReview])

  useEffect(() => {
    if (!serverReviewHydrated || !serverReviewLocator || !pendingReview) return
    const locatorKey = [
      serverReviewLocator.requestId,
      serverReviewLocator.clientRequestId,
      serverReviewLocator.requestPayloadHash,
    ].join(':')
    if (serverReviewInitialFetchRef.current === locatorKey) return
    serverReviewInitialFetchRef.current = locatorKey
    void loadServerReviewStatus(serverReviewLocator, pendingReview)
  }, [serverReviewHydrated, serverReviewLocator, pendingReview, loadServerReviewStatus])

  useEffect(() => {
    if (!transientUncertaintyKind) return
    persistUncertaintyLock(transientUncertaintyKind, activeJobId, lastRequestSnapshotRef.current)
    if (transientUncertaintyKind === 'monitoring') {
      setReconcileMessage(COMPLETED_CONTRACT_MESSAGE)
    } else {
      setReconcileMessage('ブラウザ側の監視停止が発生しました。サーバージョブ継続の可能性があります。')
    }
  }, [activeJobId, transientUncertaintyKind, persistUncertaintyLock])

  const loadFetchSummaryHistory = useCallback(async () => {
    setFetchHistoryLoading(true)
    try {
      const res = await authFetch('/api/scrape/history?limit=10')
      if (!res.ok) return
      const data = await res.json().catch(() => ({}))
      const jobs = Array.isArray(data?.jobs) ? data.jobs : []
      setFetchHistory(jobs.filter((j: any) => !!j?.fetch_summary))
    } catch (error) {
      console.error('fetch history load error:', error)
    } finally {
      setFetchHistoryLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadFetchSummaryHistory()
  }, [loadFetchSummaryHistory])

  const pollDryRunJob = useCallback(async (stored: StoredDryRunJob) => {
    if (dryRunTrackingRef.current === stored.jobId) return
    dryRunTrackingRef.current = stored.jobId
    setDryRunLoading(true)
    setDryRunStartedAt(stored.startedAt)
    setDryRunElapsedSeconds(Math.floor(Math.max(0, Date.now() - stored.startedAt) / 1000))
    setDryRunError('')
    setDryRunResultReady(false)
    setDryRunResult(null)

    let consecutiveFailures = 0
    try {
      while (Date.now() - stored.startedAt <= DRY_RUN_TIMEOUT_MS) {
        const statusRes = await authFetch(`/api/scrape/status/${stored.jobId}`)
        if (!statusRes.ok) {
          consecutiveFailures += 1
          if (consecutiveFailures >= 10) throw new Error(`Dry-runステータス取得失敗 (job_id: ${stored.jobId})`)
          await new Promise(resolve => setTimeout(resolve, 3000))
          continue
        }

        consecutiveFailures = 0
        const statusData = await statusRes.json().catch(() => ({}))
        if (statusData?.status === 'completed') {
          const normalized = normalizeDryRunResult(statusData?.result)
          localStorage.removeItem(ACTIVE_DRY_RUN_JOB_KEY)
          setDryRunResult(normalized)
          setDryRunResultReady(true)
          setDryRunExecuted(true)
          setToast({ visible: true, message: 'Dry-run完了（HTTPアクセスなし）', type: 'success' })
          await loadFetchSummaryHistory()
          return
        }
        if (statusData?.status === 'error' || statusData?.status === 'not_found') {
          localStorage.removeItem(ACTIVE_DRY_RUN_JOB_KEY)
          throw new Error(statusData?.error || `Dry-runジョブが見つかりません (job_id: ${stored.jobId})`)
        }
        await new Promise(resolve => setTimeout(resolve, 1000))
      }
      throw new Error('Dry-runが24時間以内に完了しませんでした。バックエンド状態を確認してください。')
    } catch (error: any) {
      localStorage.removeItem(ACTIVE_DRY_RUN_JOB_KEY)
      setDryRunResult(null)
      setDryRunResultReady(false)
      setDryRunExecuted(false)
      const message = typeof error?.message === 'string' ? error.message : 'Dry-run結果を取得できませんでした。'
      setDryRunError(message)
      setToast({ visible: true, message: `Dry-runエラー: ${message}`, type: 'error' })
    } finally {
      if (dryRunTrackingRef.current === stored.jobId) dryRunTrackingRef.current = null
      setDryRunLoading(false)
      setDryRunStartedAt(null)
    }
  }, [loadFetchSummaryHistory])

  useEffect(() => {
    try {
      const raw = localStorage.getItem(ACTIVE_DRY_RUN_JOB_KEY)
      const saved = raw ? JSON.parse(raw) : null
      if (saved && typeof saved.jobId === 'string' && typeof saved.startedAt === 'number') {
        void pollDryRunJob(saved as StoredDryRunJob)
      }
    } catch {
      localStorage.removeItem(ACTIVE_DRY_RUN_JOB_KEY)
    }
  }, [pollDryRunJob])

  const loadStats = async () => {
    try {
      const res = await authFetch('/api/data-stats?ultimate=true')
      if (!res.ok) return
      const stats = await res.json()
      setDataStats({
        totalRaces: stats.total_races || 0,
        totalResults: stats.total_horses || 0,
        latestDate: stats.latest_date || ''
      })
    } catch (error) {
      console.error('統計取得エラー:', error)
    }
  }

  // 🚀 期間指定バッチスクレイピング（バリデーション・確認ダイアログのみ担当）
  const handlePeriodBatchScrape = async (override?: BatchRequestSnapshot) => {
    if (isOperationBusy) return
    if (executeBlockedByUncertainty) {
      showToast('実行状態を確認できません。履歴またはjob statusを確認するまで新規実行しないでください。', 'error')
      return
    }

    const target = override ?? { startPeriod, endPeriod, forceRescrape }
    setTransientUncertaintyDismissed(false)
    lastRequestSnapshotRef.current = target
    const validation = validatePeriodRange(target.startPeriod, target.endPeriod)
    if (!validation.ok) {
      const message = validation.message || '期間指定が不正です'
      setPeriodErrorMessage(message)
      showToast(message, 'error')
      return
    }
    setPeriodErrorMessage('')
    setDryRunErrorMessage('')

    const [startYearStr, startMonthStr] = target.startPeriod.split('-')
    const [endYearStr, endMonthStr] = target.endPeriod.split('-')
    const startYear = parseInt(startYearStr, 10)
    const startMonth = parseInt(startMonthStr, 10)
    const endYear = parseInt(endYearStr, 10)
    const endMonth = parseInt(endMonthStr, 10)

    // 月数カウント（確認ダイアログ用）
    let totalMonths = 0
    let y = startYear, m = startMonth
    while (y < endYear || (y === endYear && m <= endMonth)) {
      totalMonths++; m++; if (m > 12) { m = 1; y++ }
    }

    if (!dryRunExecuted) {
      setExecuteWarn('Dry-run未実行です。本実行は可能ですが、推定アクセス数の確認を推奨します。')
    } else {
      setExecuteWarn('')
    }

    const _dryRunState = dryRunExecuted ? 'Dry-run実行済み' : 'Dry-run未実行（推奨）'
    if (!confirm(`${startYear}年${startMonth}月 ～ ${endYear}年${endMonth}月（${totalMonths}ヶ月分）を月単位で順次取得します。\nブラウザ側で停止しても開始済みのサーバージョブは継続している可能性があります。\n\n${_dryRunState}\n続行しますか？`)) return

    try {
      const result = await startBatchScrape(target.startPeriod, target.endPeriod, target.forceRescrape)
      showToast(`取得完了 — ${result.stats.total_months}ヶ月 / ${result.races_collected}レース / 所要: ${result.elapsed_time}秒`)
      setRetrySnapshot(null)
      setReconcileMessage('')
      loadStats()
      loadFetchSummaryHistory()
    } catch (error: any) {
      const safeToRetry = error instanceof BatchScrapeError ? error.safeToRetry : false
      if (error instanceof BatchScrapeError && (error.kind === 'monitoring' || error.kind === 'client_stop')) {
        persistUncertaintyLock(error.kind, activeJobId, target)
        if (error.kind === 'monitoring') {
          setReconcileMessage(COMPLETED_CONTRACT_MESSAGE)
        }
      }
      setRetrySnapshot(safeToRetry ? target : null)
      showToast(`取得エラー: ${error.message}`, 'error')
    }
  }

  const handleReconcileStatus = async () => {
    if (!effectiveUncertaintyJobId || reconcileLoading) return
    setReconcileLoading(true)
    try {
      const res = await authFetch(`/api/scrape/status/${effectiveUncertaintyJobId}`, { signal: AbortSignal.timeout(10000) })
      if (!res.ok) {
        setReconcileMessage('状態再確認に失敗しました。lockを維持します。')
        return
      }

      const payload = await res.json().catch(() => null)
      if (!isObject(payload) || typeof payload.status !== 'string') {
        setReconcileMessage('状態応答形式が不正です。lockを維持します。')
        return
      }

      if (payload.status === 'queued' || payload.status === 'running' || payload.status === 'not_found') {
        setReconcileMessage('対象jobは未終端です。lockを維持します。')
        return
      }

      if (payload.status === 'error') {
        const unlocked = clearExecutionLockAfterReconciliation()
        if (!unlocked) {
          setReconcileMessage('状態再確認は完了しましたが、処理中のためlock解除は保留されました。')
          return
        }
        if (!clearPersistedUncertainty()) {
          setReconcileMessage('終端状態は確認しましたが、lock保存領域を更新できないため新規実行を停止しています。')
          return
        }
        setReconcileMessage('対象jobは終端エラーでした。lockを解除しました。必要に応じて再実行してください。')
        return
      }

      if (payload.status === 'completed') {
        if (!hasStrictCompletedResult(payload.result)) {
          setReconcileMessage(COMPLETED_CONTRACT_MESSAGE)
          return
        }
        const unlocked = clearExecutionLockAfterReconciliation()
        if (!unlocked) {
          setReconcileMessage('対象jobは完了を確認しましたが、処理中のためlock解除は保留されました。')
          return
        }
        if (!clearPersistedUncertainty()) {
          setReconcileMessage('完了状態は確認しましたが、lock保存領域を更新できないため新規実行を停止しています。')
          return
        }
        setReconcileMessage('対象jobは完了。履歴で全体範囲を確認してください')
        return
      }

      setReconcileMessage('状態を判定できないためlockを維持します。')
    } catch {
      setReconcileMessage('状態再確認に失敗しました。lockを維持します。')
    } finally {
      setReconcileLoading(false)
    }
  }

  const periodMonthSpan = (startPeriodValue: string, endPeriodValue: string) => {
    const [startYearStr, startMonthStr] = startPeriodValue.split('-')
    const [endYearStr, endMonthStr] = endPeriodValue.split('-')
    const sy = parseInt(startYearStr, 10)
    const sm = parseInt(startMonthStr, 10)
    const ey = parseInt(endYearStr, 10)
    const em = parseInt(endMonthStr, 10)
    if (!Number.isFinite(sy) || !Number.isFinite(sm) || !Number.isFinite(ey) || !Number.isFinite(em)) return 0
    return Math.max(0, (ey - sy) * 12 + (em - sm) + 1)
  }

  const formatMaybeNumber = (value: unknown): string => {
    if (value == null) return '-'
    if (typeof value === 'number') {
      return Number.isFinite(value) ? String(value) : '-'
    }
    const parsed = Number(value)
    return Number.isFinite(parsed) ? String(parsed) : '-'
  }

  const formatMaybeSeconds = (value: unknown): string => {
    if (value == null) return '-'
    const parsed = Number(value)
    if (!Number.isFinite(parsed)) return '-'
    return `${Math.ceil(parsed)} sec`
  }

  const formatSummaryDate = (value: unknown): string => {
    const text = typeof value === 'string' ? value.trim() : ''
    if (/^\d{8}$/.test(text)) {
      return `${text.slice(0, 4)}/${text.slice(4, 6)}/${text.slice(6, 8)}`
    }
    return text || '-'
  }

  const formatSummaryTimestamp = (value: unknown): string => {
    if (typeof value !== 'string' || !value.trim()) return ''
    const parsed = new Date(value)
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('ja-JP')
  }

  const handleDryRun = async () => {
    const validation = validatePeriodRange(startPeriod, endPeriod)
    if (!validation.ok) {
      const message = validation.message || '期間指定が不正です'
      setPeriodErrorMessage(message)
      setDryRunResult(null)
      setDryRunExecuted(false)
      showToast(message, 'error')
      return
    }
    setPeriodErrorMessage('')
    setDryRunLoading(true)
    setDryRunStartedAt(Date.now())
    setDryRunElapsedSeconds(0)
    setDryRunResult(null)
    setDryRunResultReady(false)
    setExecuteWarn('')
    setDryRunPendingMessage('')
    setDryRunErrorMessage('')
    try {
      setExecuteWarn('')
      const months = enumerateMonthDateRanges(startPeriod, endPeriod)
      const monthlyResults: ScrapeDryRunResult[] = []

      for (const [index, month] of months.entries()) {
        setDryRunPendingMessage(`Dry-run見積もり中 (${index + 1}/${months.length}): ${month.label}`)
        const startRes = await authFetch('/api/scrape', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            start_date: month.startDateStr,
            end_date: month.endDateStr,
            force_rescrape: forceRescrape,
            dry_run: true,
          }),
        })

        if (!startRes.ok) {
          const err = await startRes.json().catch(() => ({}))
          throw new Error(`${month.label}: ${formatApiErrorDetail(err?.detail ?? err, `HTTP ${startRes.status}`)}`)
        }

        const startPayload = await startRes.json().catch(() => ({}))
        const jobId = startPayload?.job_id
        if (typeof jobId !== 'string' || !jobId) {
          throw new Error(`${month.label}: Dry-run開始応答にjob_idがありません`)
        }

        let resultPayload: unknown = null
        for (let attempt = 0; attempt < 120; attempt += 1) {
          await new Promise(resolve => setTimeout(resolve, 500))
          const statusRes = await authFetch(`/api/scrape/status/${jobId}`)
          if (!statusRes.ok) continue
          const statusData = await statusRes.json().catch(() => ({}))
          if (statusData?.status === 'completed') {
            resultPayload = statusData?.result
            break
          }
          if (statusData?.status === 'error' || statusData?.status === 'not_found') {
            throw new Error(`${month.label}: ${formatApiErrorDetail(statusData?.error, 'Dry-run failed')}`)
          }
        }

        if (!resultPayload) {
          throw new Error(`${month.label}: Dry-runが60秒以内に完了しませんでした`)
        }
        monthlyResults.push(normalizeDryRunResult(resultPayload))
      }

      setDryRunResult(aggregateDryRunResults(monthlyResults))
      setDryRunResultReady(true)
      setDryRunExecuted(true)
      setDryRunPendingMessage('')
      setDryRunErrorMessage('')
      showToast(`Dry-run完了（${months.length}ヶ月、HTTPアクセスなし）`)
      loadFetchSummaryHistory()
    } catch (error: any) {
      setDryRunResult(null)
      setDryRunResultReady(false)
      setDryRunExecuted(false)
      setDryRunPendingMessage('')
      setDryRunErrorMessage(error?.message || 'Dry-run failed')
      showToast(`Dry-runエラー: ${error.message}`, 'error')
    } finally {
      setDryRunLoading(false)
      setDryRunStartedAt(null)
    }
  }

  useEffect(() => {
    if (!batchResult) return
    void loadStats()
    void loadFetchSummaryHistory()
  }, [batchResult, loadFetchSummaryHistory])

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <header className="border-b border-[#1e1e1e] px-6 py-4 flex items-center justify-between">
        <Logo href="/home" />
        <div className="flex flex-wrap items-center justify-end gap-3 sm:gap-4">
          <Link href="/home" className="flex items-center gap-1 text-xs text-[#555] hover:text-white transition-colors">
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
            ホーム
          </Link>
          {/* コンパクトAPI状態 */}
          <div className="flex items-center gap-2 px-3 py-1.5 bg-[#111] border border-[#1e1e1e] rounded-full">
            <span className={`w-1.5 h-1.5 rounded-full ${statusMeta[localApiStatus].dotClass}`} />
            <span className={`text-xs font-medium ${statusMeta[localApiStatus].textClass}`}>
              バックエンドAPI {statusMeta[localApiStatus].label}
            </span>
            <button onClick={checkLocalApi} className="text-[#444] hover:text-[#888] transition-colors ml-1">
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
            </button>
          </div>
          <span className="text-sm text-[#888]">データ取得</span>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-10 space-y-5">
        {/* API停止中の案内 */}
        {isApiUnavailable && (
          <div className="bg-[#111] border border-[#332200] rounded-lg px-4 py-3 flex items-center gap-3">
            <span className="w-1.5 h-1.5 rounded-full bg-[#f87171] shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-xs text-[#f87171]">スクレイプ API の状態を確認できません</p>
              <p className="text-xs text-[#555] mt-0.5">
                Production利用時は管理者へ連絡してください。ローカル利用時はリポジトリ直下の
                <code className="text-[#7dd3fc] font-mono mx-1">start-keiba-ai-pro.bat</code>
                を実行してください。
              </p>
              {localApiReason && <p className="text-xs text-[#666] mt-1">reason: {localApiReason}</p>}
            </div>
          </div>
        )}

        {/* データ取得フォーム */}
        <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-6 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium text-white">期間指定一括取得</h2>
            <p className="text-xs text-[#555]">月単位で自動分割して順次取得</p>
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label className="block text-xs text-[#666] mb-2">開始年月</label>
              <input
                type="month"
                value={startPeriod}
                data-testid="start-period-input"
                disabled={isOperationBusy || executeBlockedByUncertainty}
                onChange={e => {
                  setStartPeriod(e.target.value)
                  setPeriodErrorMessage('')
                }}
                max={endPeriod}
                className="w-full px-4 py-3 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg text-white focus:outline-none focus:border-[#333] transition-colors"
              />
            </div>
            <div>
              <label className="block text-xs text-[#666] mb-2">終了年月</label>
              <input
                type="month"
                value={endPeriod}
                data-testid="end-period-input"
                disabled={isOperationBusy || executeBlockedByUncertainty}
                onChange={e => {
                  setEndPeriod(e.target.value)
                  setPeriodErrorMessage('')
                }}
                min={startPeriod}
                className="w-full px-4 py-3 bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg text-white focus:outline-none focus:border-[#333] transition-colors"
              />
            </div>
          </div>

          <div className="rounded border border-[#1e1e1e] bg-[#0b0f14] px-3 py-2.5 text-xs text-[#9db4cc]">
            Dry-run は HTTPアクセスを実行しません。取得件数と推定時間を事前確認するためのプレビューです。
          </div>

          <div className="flex flex-wrap items-center justify-end gap-2 pt-1">
            <button
              onClick={handleDryRun}
              data-testid="dry-run-button"
              disabled={isOperationBusy || isApiUnavailable || !isPeriodValid || executeBlockedByUncertainty}
              className={`flex items-center gap-2 px-4 py-2.5 rounded-lg font-medium text-sm transition-colors ${
                isOperationBusy || isApiUnavailable || !isPeriodValid || executeBlockedByUncertainty
                  ? 'bg-[#222] text-[#555] cursor-not-allowed'
                  : 'bg-[#1e293b] text-[#dbeafe] hover:bg-[#334155]'
              }`}
            >
              {dryRunLoading ? 'Dry-run中...' : 'Dry-run'}
            </button>

            <button
              onClick={() => handlePeriodBatchScrape()}
              data-testid="execute-button"
              disabled={isOperationBusy || isApiUnavailable || !isPeriodValid || executeBlockedByUncertainty}
              className={`flex items-center gap-2 px-6 py-2.5 rounded-lg font-medium text-sm transition-colors ${
                isOperationBusy || isApiUnavailable || !isPeriodValid || executeBlockedByUncertainty
                  ? 'bg-[#222] text-[#555] cursor-not-allowed'
                  : 'bg-white text-black hover:bg-[#eee]'
              }`}
            >
              {isBatchBusy ? (
                <>
                  <svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  取得中...
                </>
              ) : !uncertaintyHydrated || !reviewHydrated ? '状態確認中' : isApiUnavailable ? 'API確認不可' : !isPeriodValid ? '期間不正' : executeBlockedByUncertainty ? '実行確認待ち' : '取得開始'}
            </button>
          </div>

          {uncertaintyStorageBlocked && (
            <div className="rounded border border-[#4a1d1d] bg-[#220d0d] px-3 py-2 text-xs text-[#fca5a5] space-y-1" role="alert" data-testid="uncertainty-storage-blocked">
              <div>保存済みlockを安全に確認できません</div>
              <div>lockを自動削除せず、Dry-runと取得開始を停止しています。サーバー状態と監査証跡を確認してください。</div>
              {reconcileMessage && <div>{reconcileMessage}</div>}
            </div>
          )}

          {shouldShowUncertaintyWarning && (
            <div className="rounded border border-[#4a1d1d] bg-[#220d0d] px-3 py-2 text-xs text-[#fca5a5] space-y-1" role="alert" data-testid="uncertainty-panel">
              <div>実行状態不明</div>
              {effectiveUncertaintyJobId && <div>job_id: {effectiveUncertaintyJobId}</div>}
              <div>{COMPLETED_CONTRACT_MESSAGE}</div>
              <div>開始済みのサーバージョブは継続している可能性があります</div>
              {!effectiveUncertaintyJobId && <div>job_idがないため自動解除できません。Phase 3Eでは承認依頼の記録だけを行い、lockは解除しません。</div>}
              {reconcileMessage && <div>{reconcileMessage}</div>}
              {!effectiveUncertaintyJobId && joblessReviewLock && !pendingReview && (
                <div className="mt-3 space-y-2 rounded border border-[#713f12] bg-[#1c1206] p-3 text-[#fde68a]" data-testid="phase3e-review-form">
                  <div className="font-medium">非実行型の承認依頼（pending review）</div>
                  <div>この記録はローカル・非権威です。承認、再実行許可、lock解除のいずれにもなりません。</div>
                  <textarea
                    data-testid="phase3e-review-reason"
                    value={reviewReason}
                    onChange={event => setReviewReason(event.target.value)}
                    maxLength={500}
                    rows={3}
                    placeholder="調査理由を20文字以上で入力"
                    className="w-full rounded border border-[#713f12] bg-[#0a0a0a] px-2 py-1.5 text-xs text-white"
                  />
                  <label className="flex items-start gap-2">
                    <input
                      type="checkbox"
                      data-testid="phase3e-ack-unverified"
                      checked={ackServerStateUnverified}
                      onChange={event => setAckServerStateUnverified(event.target.checked)}
                    />
                    <span>サーバー実行状態は未確認であり、処理が継続中の可能性があることを確認しました。</span>
                  </label>
                  <label className="flex items-start gap-2">
                    <input
                      type="checkbox"
                      data-testid="phase3e-ack-no-unlock"
                      checked={ackNoUnlockOrRetry}
                      onChange={event => setAckNoUnlockOrRetry(event.target.checked)}
                    />
                    <span>この依頼ではlock解除・retry・scrape開始を行わないことを確認しました。</span>
                  </label>
                  <button
                    type="button"
                    data-testid="phase3e-record-review"
                    onClick={handleRecordPendingReview}
                    disabled={!canRecordPendingReview}
                    className={`rounded px-3 py-1.5 text-xs font-medium ${canRecordPendingReview ? 'bg-[#f59e0b] text-black' : 'bg-[#222] text-[#555] cursor-not-allowed'}`}
                  >
                    承認依頼を記録（lock維持）
                  </button>
                  {reviewPersistError && <div role="alert" data-testid="phase3e-review-error">{reviewPersistError}</div>}
                </div>
              )}
              {!effectiveUncertaintyJobId && pendingReview && (
                <div className="mt-3 space-y-1 rounded border border-[#854d0e] bg-[#1c1206] p-3 text-[#fde68a]" data-testid="phase3e-pending-review">
                  <div className="font-medium">status: pending_review</div>
                  <div>request_id: {pendingReview.requestId}</div>
                  <div>lock_fingerprint: {pendingReview.lockFingerprint}</div>
                  <div>authoritative=false / execution_enabled=false / lock_release_allowed=false</div>
                  <div>サーバー側の承認記録ではありません。lockは維持されています。</div>
                  {!serverReviewLocator && (
                    <button
                      type="button"
                      data-testid="phase3f-submit-review-request"
                      onClick={() => void handleSubmitServerReview()}
                      disabled={!canSubmitServerReview}
                      className={`mt-2 rounded px-3 py-1.5 text-xs font-medium ${canSubmitServerReview ? 'bg-[#38bdf8] text-black' : 'bg-[#222] text-[#555] cursor-not-allowed'}`}
                    >
                      {serverReviewSubmitLoading ? 'サーバー記録中...' : 'サーバー監査台帳へ提出（lock維持）'}
                    </button>
                  )}
                  {!serverReviewLocator && serverReviewError && (
                    <div role="alert" data-testid="phase3f-server-review-error">{serverReviewError}</div>
                  )}
                </div>
              )}
              {!effectiveUncertaintyJobId && pendingReview && serverReviewLocator && (
                <div className="mt-3 space-y-1 rounded border border-[#075985] bg-[#071a24] p-3 text-[#bae6fd]" data-testid="phase3f-server-review-panel">
                  <div className="font-medium">サーバー権威の監査記録（review-only）</div>
                  <div data-testid="phase3f-server-review-request-id">request_id: {serverReviewLocator.requestId}</div>
                  {serverReviewRecord ? (
                    <>
                      <div data-testid="phase3f-server-review-status">status: {serverReviewRecord.status}</div>
                      <div>authoritative_record=true / approval_granted={String(serverReviewRecord.approval_granted)}</div>
                      <div>execution_enabled=false / lock_release_allowed=false / automatic_action_taken=false</div>
                      <div>approval_scope=review_only</div>
                    </>
                  ) : (
                    <div data-testid="phase3f-server-review-status">status: unknown（lock維持）</div>
                  )}
                  <div>approvedを含むどの状態でも、Phase 3Fでは取得開始・Dry-run・retry・lock解除を許可しません。</div>
                  {serverReviewLastCheckedAt && <div>last_checked_at: {serverReviewLastCheckedAt}</div>}
                  <button
                    type="button"
                    data-testid="phase3f-refresh-review-status"
                    onClick={() => void loadServerReviewStatus(serverReviewLocator, pendingReview)}
                    disabled={serverReviewStatusLoading}
                    className={`mt-2 rounded px-3 py-1.5 text-xs font-medium ${serverReviewStatusLoading ? 'bg-[#222] text-[#555] cursor-not-allowed' : 'bg-white text-black hover:bg-[#eee]'}`}
                  >
                    {serverReviewStatusLoading ? '確認中...' : 'サーバー状態を再確認（read-only）'}
                  </button>
                  {serverReviewError && (
                    <div role="alert" data-testid="phase3f-server-review-error">{serverReviewError}</div>
                  )}
                </div>
              )}
              {effectiveUncertaintyJobId && (
                <button
                  type="button"
                  data-testid="reconcile-status-button"
                  onClick={handleReconcileStatus}
                  disabled={reconcileLoading}
                  className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                    reconcileLoading
                      ? 'bg-[#222] text-[#555] cursor-not-allowed'
                      : 'bg-white text-black hover:bg-[#eee]'
                  }`}
                >
                  {reconcileLoading ? '確認中...' : '状態を再確認'}
                </button>
              )}
            </div>
          )}

          {effectivePeriodError && (
            <div className="rounded border border-[#4a1d1d] bg-[#220d0d] px-3 py-2 text-xs text-[#fca5a5]" role="alert">
              期間エラー: {effectivePeriodError}
            </div>
          )}

          {dryRunPendingMessage && (
            <div className="rounded border border-[#1e3a8a] bg-[#0b1220] px-3 py-2 text-xs text-[#93c5fd]" role="status" aria-live="polite">
              {dryRunPendingMessage}
            </div>
          )}

          {dryRunErrorMessage && (
            <div
              className="rounded border border-[#4a1d1d] bg-[#220d0d] px-3 py-2 text-xs text-[#fca5a5]"
              role="alert"
              data-testid="dry-run-error"
            >
              Dry-run失敗: {dryRunErrorMessage}
            </div>
          )}

          {executeWarn && (
            <div className="rounded border border-[#4a3b0f] bg-[#201a08] px-3 py-2 text-xs text-[#facc15]">
              {executeWarn}
            </div>
          )}

          {dryRunLoading && (
            <div className="rounded-lg border border-[#1e1e1e] bg-[#0a0a0a] p-4 space-y-2">
              <div className="flex items-center justify-between">
                <h3 className="text-xs font-medium text-[#9db4cc]">Dry-run 実行中</h3>
                <span className="text-[11px] text-[#6b7280]">経過秒: {dryRunElapsedSeconds} sec</span>
              </div>
              <div className="flex items-center justify-between">
                <h3 className="text-xs font-medium text-white">見積もり生成中</h3>
              </div>
              <div className="text-xs text-[#9db4cc]">
                HTTPアクセスは実行していません
              </div>
              {periodMonthSpan(startPeriod, endPeriod) >= 6 && (
                <div className="text-xs text-[#facc15]">
                  長期間の場合、月次カレンダー確認により数十秒かかる場合があります
                </div>
              )}
            </div>
          )}

          {!dryRunLoading && dryRunError && (
            <div className="rounded border border-[#5b1e1e] bg-[#1f0d0d] px-3 py-2 text-xs text-[#fca5a5]">
              {dryRunError}
            </div>
          )}

          {!dryRunLoading && dryRunResultReady && dryRunResult && (
            <div
              className="rounded-lg border border-[#1e1e1e] bg-[#0a0a0a] p-4 space-y-3"
              data-testid="dry-run-result"
            >
              <div className="flex items-center justify-between">
                <h3 className="text-xs font-medium text-white">Dry-run 結果（実取得なし）</h3>
                <span className="text-[11px] text-[#6b7280]">HTTPアクセスしないプレビュー</span>
              </div>

              <div className="grid grid-cols-2 gap-3 text-xs md:grid-cols-4">
                <div className="rounded border border-[#1e1e1e] bg-[#0b0f14] p-3">
                  <div className="text-[#666]">新規取得</div>
                  <div className="mt-1 text-lg font-semibold text-white">{dryRunResult.dry_run.new_fetch_required_count.toLocaleString()}</div>
                </div>
                <div className="rounded border border-[#1e1e1e] bg-[#0b0f14] p-3">
                  <div className="text-[#666]">既存データ</div>
                  <div className="mt-1 text-lg font-semibold text-white">{dryRunResult.dry_run.already_covered_count.toLocaleString()}</div>
                </div>
                <div className="rounded border border-[#1e1e1e] bg-[#0b0f14] p-3">
                  <div className="text-[#666]">HTTP予定</div>
                  <div className="mt-1 text-lg font-semibold text-white">{dryRunResult.dry_run.estimated_request_count.toLocaleString()}</div>
                </div>
                <div className="rounded border border-[#1e1e1e] bg-[#0b0f14] p-3">
                  <div className="text-[#666]">推定時間</div>
                  <div className="mt-1 text-lg font-semibold text-white">{formatMaybeSeconds(dryRunResult.dry_run.estimated_runtime_sec)}</div>
                </div>
              </div>
            </div>
          )}

          {/* 実行状態パネル */}
          {batchStatus !== 'idle' && (
            <div className="rounded-lg border border-[#1e1e1e] bg-[#0a0a0a] p-4 space-y-2" data-testid="batch-status-panel">
              {(batchStatus === 'queued' || batchStatus === 'running') && (
                <div className="space-y-1.5" role="status" aria-live="polite">
                  <div className="flex justify-between text-xs text-[#888]">
                    <span>
                      {batchStatus === 'queued' ? '開始待ち' : '取得実行中'}
                      {activeJobId ? ` · job_id: ${activeJobId}` : ''}
                    </span>
                    <span className="flex gap-3">
                      {batchProgress.eta && <span className="text-yellow-400">{batchProgress.eta}</span>}
                      <span>{batchProgress.current}%</span>
                    </span>
                  </div>
                  <div className="text-xs text-[#666]">{batchProgress.message || (batchStatus === 'queued' ? '開始待ち' : '取得実行中')}</div>
                  <div className="grid grid-cols-2 gap-2 pt-1 text-[11px] md:grid-cols-4" data-testid="scrape-progress-counters">
                    <div className="rounded border border-[#1e1e1e] px-2 py-1.5 text-[#aaa]">
                      新規保存レース <span className="text-white">{batchProgress.newSavedRaces ?? 0}</span>
                    </div>
                    <div className="rounded border border-[#1e1e1e] px-2 py-1.5 text-[#aaa]">
                      新規保存頭数 <span className="text-white">{batchProgress.newSavedHorses ?? 0}</span>
                    </div>
                    <div className="rounded border border-[#1e1e1e] px-2 py-1.5 text-[#aaa]">
                      既存品質合格スキップ <span className="text-white">{batchProgress.existingRacesSkipped ?? 0}</span>
                    </div>
                    <div className="rounded border border-[#1e1e1e] px-2 py-1.5 text-[#aaa]">
                      正常非開催日 <span className="text-white">{batchProgress.verifiedNoRaceDates ?? 0}</span>
                    </div>
                  </div>
                  <div className="w-full bg-[#1e1e1e] rounded-full h-1.5 overflow-hidden">
                    <div className="bg-white h-1.5 rounded-full transition-all duration-500" style={{ width: `${batchProgress.current}%` }} />
                  </div>
                </div>
              )}

              {batchStatus === 'completed' && batchResult && (
                <div className="text-xs text-[#4ade80]" role="status" aria-live="polite">
                  {batchResult.races_collected === 0
                    ? '取得完了: 0レース（0レース・正常完了） / '
                    : '取得完了: '}
                  新規{batchResult.races_collected}レース・{batchResult.saved_horses}頭 / 既存品質合格{batchResult.existing_races_skipped}レースをスキップ / 正常非開催日{batchResult.verified_no_race_dates}日
                </div>
              )}

              {batchStatus === 'error' && (
                <div className="space-y-2" role="alert">
                  <div className="text-xs text-[#fca5a5]">
                    {(() => {
                      const titles: Record<string, string> = {
                        execution: '取得失敗',
                        start_rejected: '開始拒否',
                        monitoring: '実行状態不明',
                        client_stop: 'ブラウザ側の監視停止',
                        validation: '入力エラー',
                        busy: '取得処理中',
                      }
                      const key = typeof failureKind === 'string' ? failureKind : 'execution'
                      const title = titles[key] || '取得失敗'
                      return `${title}: ${batchError || '不明なエラー'}`
                    })()}
                  </div>
                  {canRetry && retrySnapshot && (failureKind === 'execution' || failureKind === 'start_rejected') && (
                    <button
                      data-testid="retry-button"
                      onClick={() => handlePeriodBatchScrape(retrySnapshot)}
                      disabled={isOperationBusy || isApiUnavailable}
                      className={`px-4 py-2 rounded text-xs font-medium transition-colors ${
                        isOperationBusy || isApiUnavailable
                          ? 'bg-[#222] text-[#555] cursor-not-allowed'
                          : 'bg-white text-black hover:bg-[#eee]'
                      }`}
                    >
                      再実行
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* 取得完了サマリー */}
        {batchStatus === 'completed' && batchResult && batchResult.stats?.period && (
          <div className="bg-[#0a1a0a] border border-[#1a3a1a] rounded-lg px-5 py-4 flex flex-wrap gap-5 items-center">
            <span className="text-xs text-[#4ade80] font-medium">✓ 取得完了</span>
            <span className="text-xs text-[#888]">{batchResult.stats.period} · {batchResult.stats.total_months}ヶ月</span>
            <span className="text-xs text-white font-medium">新規{batchResult.races_collected}レース</span>
            <span className="text-xs text-[#888]">既存{batchResult.existing_races_skipped}レースをスキップ</span>
            <span className="text-xs text-[#555]">{batchResult.elapsed_time}秒</span>
            {batchResult.races_collected === 0 && <span className="text-xs text-[#93c5fd]">0レース・正常完了</span>}
          </div>
        )}

        {/* 通常運用では最新の実行結果だけを表示する */}
        {fetchHistory.length > 0 && (
          <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5" data-testid="latest-fetch-summary">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-medium text-white">最新の実行結果</h2>
              <button
                data-testid="refresh-history-button"
                onClick={loadFetchSummaryHistory}
                className="text-xs text-[#555] hover:text-[#888] transition-colors"
              >
                {fetchHistoryLoading ? '更新中...' : '更新'}
              </button>
            </div>

            {(() => {
              const item = fetchHistory[0]
              const summary = item.fetch_summary || {}
              const dry = summary.dry_run || {}
              const isDryRun = summary.mode === 'dry-run'
              return (
                <div className="rounded border border-[#1e1e1e] bg-[#0a0a0a] p-3">
                  <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px] text-[#666]">
                    <span className="rounded bg-[#1f2937] px-2 py-0.5 text-[#dbeafe]">{isDryRun ? '事前確認' : '取得'}</span>
                    <span>{formatSummaryDate(summary.start_date)} ～ {formatSummaryDate(summary.end_date)}</span>
                    {item.updated_at && <span>{formatSummaryTimestamp(item.updated_at)}</span>}
                  </div>
                  {isDryRun ? (
                    <div className="grid grid-cols-1 gap-3 text-xs sm:grid-cols-3">
                      <div className="text-[#888]">新規取得 <span className="text-white">{formatMaybeNumber(dry.new_fetch_required_count)}</span></div>
                      <div className="text-[#888]">HTTP予定 <span className="text-white">{formatMaybeNumber(dry.estimated_request_count)}</span></div>
                      <div className="text-[#888]">推定時間 <span className="text-white">{formatMaybeSeconds(dry.estimated_runtime_sec)}</span></div>
                    </div>
                  ) : (
                    <div className="grid grid-cols-1 gap-3 text-xs sm:grid-cols-3">
                      <div className="text-[#888]">保存レース <span className="text-white">{formatMaybeNumber(summary.saved_races)}</span></div>
                      <div className="text-[#888]">保存出走馬 <span className="text-white">{formatMaybeNumber(summary.saved_horses)}</span></div>
                      <div className="text-[#888]">所要時間 <span className="text-white">{formatMaybeSeconds(summary.elapsed_time_sec)}</span></div>
                    </div>
                  )}
                </div>
              )
            })()}
          </div>
        )}

        {/* 取得済みデータ統計 */}
        <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5">
          <div className="mb-4">
            <h2 className="text-sm font-medium text-white">取得済みデータ</h2>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg p-3">
              <div className="text-xs text-[#666] mb-1">総レース数</div>
              <div className="text-xl font-bold text-white">{dataStats.totalRaces.toLocaleString()}</div>
            </div>
            <div className="bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg p-3">
              <div className="text-xs text-[#666] mb-1">総出走馬数</div>
              <div className="text-xl font-bold text-white">{dataStats.totalResults.toLocaleString()}</div>
            </div>
            <div className="bg-[#0a0a0a] border border-[#1e1e1e] rounded-lg p-3">
              <div className="text-xs text-[#666] mb-1">最終取得日</div>
              <div className="text-sm font-medium text-[#aaa]">
                {dataStats.latestDate ? new Date(dataStats.latestDate).toLocaleDateString('ja-JP') : '未取得'}
              </div>
            </div>
          </div>

        </div>
      </main>

      <Toast
        message={toast.message}
        type={toast.type}
        isVisible={toast.visible}
        onClose={() => setToast(t => ({ ...t, visible: false }))}
      />
    </div>
  )
}

