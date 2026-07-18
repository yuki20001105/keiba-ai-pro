'use client'

import { useState, useEffect, useRef } from 'react'
import Link from 'next/link'
import { Logo } from '@/components/Logo'
import { Toast } from '@/components/Toast'
import { authFetch } from '@/lib/auth-fetch'
import { useJobPoller } from '@/hooks/useJobPoller'
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

type ScrapeHealthStatus = 'healthy' | 'degraded' | 'unhealthy' | 'unknown'
type LocalApiStatus = 'checking' | ScrapeHealthStatus

type ScrapeDryRunSummary = {
  total_target_count: number
  unique_url_count: number
  estimated_request_count: number
  cache_hit_count: number
  cache_miss_count: number
  resume_hit_count: number
  skipped_count: number
  estimated_runtime_sec: number
}

type ScrapeDryRunResult = {
  dry_run: ScrapeDryRunSummary
  rate_limit_policy?: {
    min_interval_sec?: number
    scope?: string
    note?: string
  }
  retry_backoff_policy?: {
    max_retries?: number
    retry_statuses?: number[]
    backoff?: { type?: string; base_sec?: number; jitter_sec?: number }
    retry_after?: string
  }
  circuit_breaker_policy?: {
    failure_threshold?: number
    cooldown_sec?: number
    scope?: string
  }
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
    elapsed_time_sec?: number
    dry_run?: {
      estimated_request_count?: number
      cache_hit_count?: number
      cache_miss_count?: number
      resume_hit_count?: number
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
      estimated_runtime_sec: parseStrictNumber(dryRun.estimated_runtime_sec, 'estimated_runtime_sec', false),
    },
    rate_limit_policy: fetchSummary?.rate_limit_policy || {},
    retry_backoff_policy: fetchSummary?.retry_backoff_policy || {},
    circuit_breaker_policy: fetchSummary?.circuit_breaker_policy || {},
  }
}

export default function DataCollectionPage() {
  // 期間指定用
  const now = new Date()
  const [startPeriod, setStartPeriod] = useState(`${now.getFullYear() - 1}-01`)
  const [endPeriod, setEndPeriod] = useState(
    `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
  )
  const [forceRescrape, setForceRescrape] = useState(false)
  const [dryRunLoading, setDryRunLoading] = useState(false)
  const [dryRunResult, setDryRunResult] = useState<ScrapeDryRunResult | null>(null)
  const [dryRunExecuted, setDryRunExecuted] = useState(false)
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
  } = useBatchScrape()
  const lastRequestSnapshotRef = useRef<BatchRequestSnapshot | null>(null)
  const isBatchBusy = batchLoading || batchStatus === 'queued' || batchStatus === 'running'
  const isOperationBusy = isBatchBusy || dryRunLoading
  const periodValidation = validatePeriodRange(startPeriod, endPeriod)
  const isPeriodValid = periodValidation.ok

  // データ統計と表示
  const [dataStats, setDataStats] = useState({ totalRaces: 0, totalResults: 0, latestDate: '' })
  const [showCollectedData, setShowCollectedData] = useState(false)
  const [collectedRaces, setCollectedRaces] = useState<any[]>([])
  const [selectedRaceDetail, setSelectedRaceDetail] = useState<any>(null)

  // プロファイリング
  const [showProfiling, setShowProfiling] = useState(false)
  const [profilingJobId, setProfilingJobId] = useState<string | null>(null)
  const [useOptimized, setUseOptimized] = useState(true)

  // useJobPoller でプロファイリングのポーリングを管理
  const { status: profilingStatus, progress: profilingMessage } = useJobPoller({
    jobId: profilingJobId,
    getStatusUrl: id => `/api/profiling/status/${id}`,
    intervalMs: 5000,
  })

  const inferProgress = (msg: string): number => {
    if (msg.includes('読み込み')) return 15
    if (msg.includes('エンジニアリング')) return 35
    if (msg.includes('最適化')) return 55
    if (msg.includes('ydata-profiling') || msg.includes('生成中')) return 75
    if (msg.includes('完了')) return 100
    return 5
  }
  const profilingProgress =
    profilingStatus === 'completed' ? 100
    : profilingStatus === 'error' ? 0
    : profilingMessage ? inferProgress(profilingMessage) : 0

  // ローカルAPI稼働チェック
  const [localApiStatus, setLocalApiStatus] = useState<LocalApiStatus>('checking')
  const [localApiReason, setLocalApiReason] = useState('')

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
  const executeBlockedByUncertainty = !uncertaintyHydrated
    || !reviewHydrated
    || uncertaintyStorageBlocked
    || isExecutionLocked
    || shouldShowUncertaintyWarning

  const persistUncertaintyLock = (kind: UncertaintyFailureKind, jobId: string | null, request: BatchRequestSnapshot | null) => {
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
      setUncertaintyStorageBlocked(false)
    } catch {
      setUncertaintyStorageBlocked(true)
    }
    setPersistedUncertainty(lock)
    setPendingReview(null)
    setReviewReason('')
    setAckServerStateUnverified(false)
    setAckNoUnlockOrRetry(false)
    setTransientUncertaintyDismissed(false)
  }

  const clearPersistedUncertainty = (): boolean => {
    try {
      localStorage.removeItem(UNCERTAINTY_STORAGE_KEY)
      localStorage.removeItem(UNCERTAINTY_REVIEW_STORAGE_KEY)
    } catch {
      setUncertaintyStorageBlocked(true)
      return false
    }
    setPersistedUncertainty(null)
    setPendingReview(null)
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
    loadFetchSummaryHistory()
  }, [])

  useEffect(() => {
    const syncFromStorage = () => {
      try {
        const raw = localStorage.getItem(UNCERTAINTY_STORAGE_KEY)
        if (!raw) {
          setPersistedUncertainty(null)
          setPendingReview(null)
          setUncertaintyStorageBlocked(false)
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
              } else {
                setPendingReview(null)
                if (reviewRaw) setReviewPersistError('保存済み承認依頼は現在のlockと一致しないため無効です。lockは維持します。')
              }
            } else {
              setPendingReview(null)
            }
          }
        }
      } catch {
        setPersistedUncertainty(null)
        setPendingReview(null)
        setUncertaintyStorageBlocked(true)
        setReconcileMessage('lock保存領域を確認できません。新規実行を停止しています。')
      } finally {
        setUncertaintyHydrated(true)
        setReviewHydrated(true)
      }
    }

    const handleStorage = (event: StorageEvent) => {
      if (event.key !== null && event.key !== UNCERTAINTY_STORAGE_KEY && event.key !== UNCERTAINTY_REVIEW_STORAGE_KEY) return
      if (
        event.key === null
        || (event.key === UNCERTAINTY_STORAGE_KEY && event.oldValue !== null && event.newValue === null)
      ) {
        setUncertaintyStorageBlocked(true)
        setPendingReview(null)
        setReconcileMessage('別タブでlock保存領域が削除されました。解除証拠として扱わず、新規実行を停止しています。')
        setUncertaintyHydrated(true)
        setReviewHydrated(true)
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
      syncFromStorage()
    }

    syncFromStorage()
    window.addEventListener('storage', handleStorage)
    return () => window.removeEventListener('storage', handleStorage)
  }, [])

  useEffect(() => {
    if (!transientUncertaintyKind) return
    persistUncertaintyLock(transientUncertaintyKind, activeJobId, lastRequestSnapshotRef.current)
    if (transientUncertaintyKind === 'monitoring') {
      setReconcileMessage(COMPLETED_CONTRACT_MESSAGE)
    } else {
      setReconcileMessage('ブラウザ側の監視停止が発生しました。サーバージョブ継続の可能性があります。')
    }
  }, [activeJobId, transientUncertaintyKind])

  const loadFetchSummaryHistory = async () => {
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
  }

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

  const fetchCollectedData = async () => {
    try {
      const res = await authFetch('/api/races/recent?limit=50')
      if (!res.ok) return
      const data = await res.json()
      setCollectedRaces(data.races || [])
    } catch (error) {
      console.error('データ取得エラー:', error)
    }
  }

  const fetchRaceDetail = async (raceId: string) => {
    try {
      const res = await authFetch(`/api/races/${raceId}/horses`)
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.error || `HTTP ${res.status}`)
      }
      const data = await res.json()
      setSelectedRaceDetail({ raceId, results: data.horses || [] })
    } catch (error) {
      console.error('レース詳細取得エラー:', error)
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

  const periodToDateRange = (startPeriodValue: string, endPeriodValue: string) => {
    const [startYearStr, startMonthStr] = startPeriodValue.split('-')
    const [endYearStr, endMonthStr] = endPeriodValue.split('-')
    const sy = parseInt(startYearStr, 10)
    const sm = parseInt(startMonthStr, 10)
    const ey = parseInt(endYearStr, 10)
    const em = parseInt(endMonthStr, 10)
    const pad = (n: number) => String(n).padStart(2, '0')
    const startDateStr = `${sy}${pad(sm)}01`
    const endLastDay = new Date(ey, em, 0).getDate()
    const endDateStr = `${ey}${pad(em)}${pad(endLastDay)}`
    return { startDateStr, endDateStr }
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
    setExecuteWarn('')
    setDryRunPendingMessage('')
    setDryRunErrorMessage('')
    try {
      const { startDateStr, endDateStr } = periodToDateRange(startPeriod, endPeriod)
      const startRes = await authFetch('/api/scrape', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          start_date: startDateStr,
          end_date: endDateStr,
          force_rescrape: forceRescrape,
          dry_run: true,
        }),
      })

      if (!startRes.ok) {
        const err = await startRes.json().catch(() => ({}))
        throw new Error(err.detail || `HTTP ${startRes.status}`)
      }

      const { job_id } = await startRes.json()
      let resultPayload: any = null
      let reachedTerminal = false
      for (let i = 0; i < 10; i++) {
        await new Promise(resolve => setTimeout(resolve, 500))
        const statusRes = await authFetch(`/api/scrape/status/${job_id}`)
        if (!statusRes.ok) continue
        const statusData = await statusRes.json().catch(() => ({}))
        const dryRunStatus = statusData?.status
        if (dryRunStatus === 'completed') {
          reachedTerminal = true
          resultPayload = statusData?.result
          break
        }
        if (dryRunStatus === 'error') {
          reachedTerminal = true
          throw new Error(statusData?.error || 'Dry-run failed')
        }
      }

      if (!reachedTerminal || !resultPayload) {
        setDryRunResult(null)
        setDryRunExecuted(false)
        setDryRunPendingMessage('Dry-runはまだ処理中です。しばらく待って再実行してください。')
        showToast('Dry-runはまだ処理中です。しばらく待って再実行してください。', 'error')
        return
      }

      const normalized = normalizeDryRunResult(resultPayload)
      setDryRunResult(normalized)
      setDryRunExecuted(true)
      setDryRunPendingMessage('')
      setDryRunErrorMessage('')
      showToast('Dry-run完了（HTTPアクセスなし）')
      loadFetchSummaryHistory()
    } catch (error: any) {
      setDryRunResult(null)
      setDryRunExecuted(false)
      setDryRunPendingMessage('')
      setDryRunErrorMessage(error?.message || 'Dry-run failed')
      showToast(`Dry-runエラー: ${error.message}`, 'error')
    } finally {
      setDryRunLoading(false)
    }
  }

  const handleStartProfiling = async () => {
    setProfilingJobId(null)
    try {
      const res = await authFetch('/api/profiling', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ use_optimized: useOptimized }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const { job_id } = await res.json()
      setProfilingJobId(job_id)
    } catch (e: any) {
      showToast(e.message, 'error')
    }
  }

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <header className="border-b border-[#1e1e1e] px-6 py-4 flex items-center justify-between">
        <Logo href="/home" />
        <div className="flex items-center gap-4">
          <Link href="/data-collection/refresh-plan" className="text-xs text-[#666] hover:text-white transition-colors">
            Refresh Plan
          </Link>
          <Link href="/data-collection/p0-repair-plan" className="text-xs text-[#666] hover:text-white transition-colors">
            P0 Repair Plan
          </Link>
          <Link href="/data-collection/targeted-refetch-plan" className="text-xs text-[#666] hover:text-white transition-colors">
            Targeted Refetch Plan
          </Link>
          <Link href="/data-collection/live-validation" className="text-xs text-[#b45309] hover:text-white transition-colors" data-testid="phase3d-header-link">
            Live Validation
          </Link>
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
              ローカルAPI {statusMeta[localApiStatus].label}
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
              <p className="text-xs text-[#555] mt-0.5">VS Code タスク「Start FastAPI」を実行するか、<code className="text-[#7dd3fc] font-mono">cd python-api; python main.py</code> を実行してください</p>
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

          <div className="grid grid-cols-2 gap-4">
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

          {/* 期間情報 */}
          {(() => {
            const [sy, sm] = startPeriod.split('-').map(Number)
            const [ey, em] = endPeriod.split('-').map(Number)
            if (!sy || !ey) return null
            const months = (ey - sy) * 12 + (em - sm) + 1
            if (months <= 2) return null
            return null
          })()}

          <div className="rounded border border-[#1e1e1e] bg-[#0b0f14] px-3 py-2.5 text-xs text-[#9db4cc]">
            Dry-run は HTTPアクセスを実行しません。アクセス予定数とポリシーを事前確認するためのプレビューです。
          </div>

          <div className="flex items-center justify-between pt-1 gap-3">
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                data-testid="force-rescrape-input"
                checked={forceRescrape}
                disabled={isOperationBusy || executeBlockedByUncertainty}
                onChange={e => setForceRescrape(e.target.checked)}
                className="w-3.5 h-3.5 accent-white"
              />
              <span className="text-xs text-[#888]">強制再取得（取得済みを上書き）</span>
            </label>

            <div className="flex items-center gap-2">
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
            <div className="rounded border border-[#4a1d1d] bg-[#220d0d] px-3 py-2 text-xs text-[#fca5a5]" role="alert">
              Dry-run失敗: {dryRunErrorMessage}
            </div>
          )}

          {executeWarn && (
            <div className="rounded border border-[#4a3b0f] bg-[#201a08] px-3 py-2 text-xs text-[#facc15]">
              {executeWarn}
            </div>
          )}

          {dryRunResult && (
            <div className="rounded-lg border border-[#1e1e1e] bg-[#0a0a0a] p-4 space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-xs font-medium text-white">Dry-run 結果（実取得なし）</h3>
                <span className="text-[11px] text-[#6b7280]">HTTPアクセスしないプレビュー</span>
              </div>

              <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                <div className="rounded border border-[#1e1e1e] p-2"><div className="text-[10px] text-[#666]">total target count</div><div className="text-sm text-white font-medium">{dryRunResult.dry_run.total_target_count}</div></div>
                <div className="rounded border border-[#1e1e1e] p-2"><div className="text-[10px] text-[#666]">unique URL count</div><div className="text-sm text-white font-medium">{dryRunResult.dry_run.unique_url_count}</div></div>
                <div className="rounded border border-[#1e1e1e] p-2"><div className="text-[10px] text-[#666]">estimated request count</div><div className="text-sm text-white font-medium">{dryRunResult.dry_run.estimated_request_count}</div></div>
                <div className="rounded border border-[#1e1e1e] p-2"><div className="text-[10px] text-[#666]">cache hit count</div><div className="text-sm text-white font-medium">{dryRunResult.dry_run.cache_hit_count}</div></div>
                <div className="rounded border border-[#1e1e1e] p-2"><div className="text-[10px] text-[#666]">cache miss count</div><div className="text-sm text-white font-medium">{dryRunResult.dry_run.cache_miss_count}</div></div>
                <div className="rounded border border-[#1e1e1e] p-2"><div className="text-[10px] text-[#666]">resume hit count</div><div className="text-sm text-white font-medium">{dryRunResult.dry_run.resume_hit_count}</div></div>
                <div className="rounded border border-[#1e1e1e] p-2"><div className="text-[10px] text-[#666]">skipped count</div><div className="text-sm text-white font-medium">{dryRunResult.dry_run.skipped_count}</div></div>
                <div className="rounded border border-[#1e1e1e] p-2"><div className="text-[10px] text-[#666]">estimated runtime</div><div className="text-sm text-white font-medium">{Math.ceil(dryRunResult.dry_run.estimated_runtime_sec)} sec</div></div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-2 text-[11px]">
                <div className="rounded border border-[#1e1e1e] p-2">
                  <div className="text-[#7dd3fc] mb-1">rate limit policy</div>
                  <div className="text-[#aaa]">min_interval_sec: {dryRunResult.rate_limit_policy?.min_interval_sec ?? '-'}</div>
                  <div className="text-[#aaa]">scope: {dryRunResult.rate_limit_policy?.scope || '-'}</div>
                </div>
                <div className="rounded border border-[#1e1e1e] p-2">
                  <div className="text-[#7dd3fc] mb-1">retry/backoff policy</div>
                  <div className="text-[#aaa]">max_retries: {dryRunResult.retry_backoff_policy?.max_retries ?? '-'}</div>
                  <div className="text-[#aaa]">backoff: {dryRunResult.retry_backoff_policy?.backoff?.type || '-'}</div>
                  <div className="text-[#aaa]">retry_after: {dryRunResult.retry_backoff_policy?.retry_after || '-'}</div>
                </div>
                <div className="rounded border border-[#1e1e1e] p-2">
                  <div className="text-[#7dd3fc] mb-1">circuit breaker policy</div>
                  <div className="text-[#aaa]">failure_threshold: {dryRunResult.circuit_breaker_policy?.failure_threshold ?? '-'}</div>
                  <div className="text-[#aaa]">cooldown_sec: {dryRunResult.circuit_breaker_policy?.cooldown_sec ?? '-'}</div>
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
                  <div className="w-full bg-[#1e1e1e] rounded-full h-1.5 overflow-hidden">
                    <div className="bg-white h-1.5 rounded-full transition-all duration-500" style={{ width: `${batchProgress.current}%` }} />
                  </div>
                </div>
              )}

              {batchStatus === 'completed' && batchResult && (
                <div className="text-xs text-[#4ade80]" role="status" aria-live="polite">
                  取得完了: {batchResult.races_collected}レース（{batchResult.races_collected === 0 ? '0レース・正常完了' : '正常完了'}）
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
            <span className="text-xs text-white font-medium">{batchResult.races_collected}レース</span>
            <span className="text-xs text-[#555]">{batchResult.elapsed_time}秒</span>
            {batchResult.races_collected === 0 && <span className="text-xs text-[#93c5fd]">0レース・正常完了</span>}
          </div>
        )}

        {/* 完了後の品質確認ブリッジ（read-only導線のみ） */}
        {batchStatus === 'completed' && (
          <div className="bg-[#111827] border border-[#1f2937] rounded-lg px-5 py-4 space-y-2" data-testid="quality-bridge-card">
            <div className="text-xs text-[#93c5fd]">取得は完了しましたが、品質確認は未実施です</div>
            <div className="text-xs text-[#6b7280]">以下は read-only preview です（自動実行・自動遷移は行いません）。</div>
            <div className="flex flex-wrap gap-3 pt-1">
              <Link href="/data-collection/refresh-plan" className="text-xs text-[#bfdbfe] hover:text-white transition-colors" data-testid="quality-bridge-refresh-link">
                Refresh Plan（read-only preview）
              </Link>
              <Link href="/data-collection/p0-repair-plan" className="text-xs text-[#bfdbfe] hover:text-white transition-colors" data-testid="quality-bridge-p0-link">
                P0 Repair Plan（read-only preview）
              </Link>
              <Link href="/data-collection/targeted-refetch-plan" className="text-xs text-[#bfdbfe] hover:text-white transition-colors" data-testid="quality-bridge-targeted-refetch-link">
                Targeted Refetch Plan（read-only preview）
              </Link>
              <Link href="/data-collection/live-validation" className="text-xs text-[#fbbf24] hover:text-white transition-colors" data-testid="quality-bridge-live-validation-link">
                Live Validation（Admin・外部HTTP最大3件）
              </Link>
            </div>
          </div>
        )}

        {/* fetch summary 履歴 */}
        <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium text-white">fetch summary 履歴</h2>
            <button
              data-testid="refresh-history-button"
              onClick={loadFetchSummaryHistory}
              className="text-xs text-[#555] hover:text-[#888] transition-colors"
            >
              {fetchHistoryLoading ? '更新中...' : '更新'}
            </button>
          </div>

          {fetchHistory.length === 0 ? (
            <div className="text-xs text-[#555] py-3">履歴がありません（Dry-run または 取得実行後に表示されます）</div>
          ) : (
            <div className="space-y-2">
              {fetchHistory.map((item) => {
                const summary = item.fetch_summary || {}
                const metrics = summary.metrics || {}
                const dry = summary.dry_run || {}
                const mode = summary.mode || '-'
                return (
                  <div key={item.job_id} className="rounded border border-[#1e1e1e] bg-[#0a0a0a] p-3">
                    <div className="flex flex-wrap items-center gap-2 mb-2">
                      <span className="text-[11px] px-2 py-0.5 rounded bg-[#1f2937] text-[#dbeafe]">{mode}</span>
                      <span className="text-[11px] text-[#888]">job: {item.job_id}</span>
                      <span className="text-[11px] text-[#666]">{summary.start_date || '-'} ~ {summary.end_date || '-'}</span>
                      <span className="text-[11px] text-[#666]">updated: {item.updated_at || '-'}</span>
                    </div>
                    {mode === 'dry-run' ? (
                      <div className="grid grid-cols-2 md:grid-cols-5 gap-2 text-[11px]">
                        <div className="text-[#aaa]">est req: <span className="text-white">{dry.estimated_request_count ?? '-'}</span></div>
                        <div className="text-[#aaa]">cache hit: <span className="text-white">{dry.cache_hit_count ?? '-'}</span></div>
                        <div className="text-[#aaa]">cache miss: <span className="text-white">{dry.cache_miss_count ?? '-'}</span></div>
                        <div className="text-[#aaa]">resume hit: <span className="text-white">{dry.resume_hit_count ?? '-'}</span></div>
                        <div className="text-[#aaa]">est runtime: <span className="text-white">{Math.ceil(Number(dry.estimated_runtime_sec || 0))} sec</span></div>
                      </div>
                    ) : (
                      <div className="grid grid-cols-2 md:grid-cols-5 gap-2 text-[11px]">
                        <div className="text-[#aaa]">saved races: <span className="text-white">{summary.saved_races ?? '-'}</span></div>
                        <div className="text-[#aaa]">saved horses: <span className="text-white">{summary.saved_horses ?? '-'}</span></div>
                        <div className="text-[#aaa]">elapsed: <span className="text-white">{Math.ceil(Number(summary.elapsed_time_sec || 0))} sec</span></div>
                        <div className="text-[#aaa]">network req: <span className="text-white">{metrics.network_requests ?? '-'}</span></div>
                        <div className="text-[#aaa]">retries: <span className="text-white">{metrics.retry_count ?? '-'}</span></div>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* 取得済みデータ統計 */}
        <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-medium text-white">取得済みデータ</h2>
            <button
              onClick={() => { setShowCollectedData(v => !v); if (!showCollectedData) fetchCollectedData() }}
              className="text-xs text-[#555] hover:text-[#888] transition-colors"
            >
              {showCollectedData ? '閉じる ▲' : 'レース一覧を見る ▼'}
            </button>
          </div>

          <div className="grid grid-cols-3 gap-3">
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

          {/* レース一覧（折りたたみ） */}
          {showCollectedData && (
            <div className="mt-4 border-t border-[#1e1e1e] pt-4">
              <div className="flex justify-between items-center mb-3">
                <span className="text-xs text-[#555]">最近取得したレース（最新50件）</span>
                <button onClick={fetchCollectedData} className="text-xs text-[#555] hover:text-[#888] transition-colors">更新</button>
              </div>
              <div className="space-y-1 max-h-96 overflow-y-auto">
                {collectedRaces.length === 0 ? (
                  <div className="py-8 text-center text-xs text-[#444]">データがまだ取得されていません</div>
                ) : collectedRaces.map(race => (
                  <div key={race.race_id} className="flex items-center justify-between px-3 py-2.5 rounded bg-[#0a0a0a] hover:bg-[#161616] transition-colors group">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-xs font-medium text-white">{race.race_name || `${race.venue} ${race.race_no}R`}</span>
                        <span className="text-xs text-[#555]">{race.venue}</span>
                        <span className="text-xs text-[#444]">{race.track_type} {race.distance}m</span>
                      </div>
                      <div className="text-[10px] text-[#333] mt-0.5">{race.date || race.created_at?.slice(0, 10) || ''}</div>
                    </div>
                    <button
                      onClick={() => fetchRaceDetail(race.race_id)}
                      className="text-xs text-[#444] hover:text-[#888] transition-colors opacity-0 group-hover:opacity-100 ml-3 shrink-0"
                    >
                      詳細
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* レース詳細モーダル */}
        {selectedRaceDetail && (
          <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" onClick={() => setSelectedRaceDetail(null)}>
            <div className="bg-[#111] border border-[#1e1e1e] rounded-lg max-w-4xl w-full max-h-[85vh] overflow-hidden" onClick={e => e.stopPropagation()}>
              <div className="flex items-center justify-between px-5 py-4 border-b border-[#1e1e1e]">
                <h3 className="text-sm font-medium">レース詳細</h3>
                <button onClick={() => setSelectedRaceDetail(null)} className="text-[#555] hover:text-white text-xl leading-none">×</button>
              </div>
              <div className="overflow-auto max-h-[calc(85vh-56px)]">
                <table className="w-full text-xs">
                  <thead className="bg-[#0a0a0a] sticky top-0">
                    <tr>
                      {['着', '枠', '馬番', '馬名', '性齢', '斤量', '騎手', 'タイム', 'オッズ', '人気'].map(h => (
                        <th key={h} className="px-3 py-2.5 text-left font-medium text-[#555] first:pl-5">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {selectedRaceDetail.results.map((result: any, i: number) => (
                      <tr key={i} className={`border-b border-[#1a1a1a] hover:bg-[#161616] transition-colors ${result.finish_position === 1 ? 'bg-[#0a1500]' : ''}`}>
                        <td className={`px-3 py-2.5 pl-5 font-bold ${result.finish_position <= 3 ? 'text-[#4ade80]' : 'text-[#888]'}`}>{result.finish_position}</td>
                        <td className="px-3 py-2.5 text-[#666]">{result.bracket_number}</td>
                        <td className="px-3 py-2.5 font-bold">{result.horse_number}</td>
                        <td className="px-3 py-2.5 font-medium text-white">{result.horse_name}</td>
                        <td className="px-3 py-2.5 text-[#888]">{result.sex_age || `${result.sex || ''}${result.age || ''}`}</td>
                        <td className="px-3 py-2.5 text-[#888]">{result.jockey_weight}kg</td>
                        <td className="px-3 py-2.5 text-[#888]">{result.jockey_name}</td>
                        <td className="px-3 py-2.5 font-mono text-[#aaa]">{result.finish_time?.toFixed(1)}</td>
                        <td className="px-3 py-2.5 font-medium">{result.odds?.toFixed(1)}</td>
                        <td className="px-3 py-2.5 text-[#666]">{result.popularity}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* プロファイリング（折りたたみ） */}
        <div className="border border-[#1e1e1e] rounded-lg overflow-hidden">
          <button
            onClick={() => setShowProfiling(v => !v)}
            className="w-full flex items-center justify-between px-5 py-3.5 bg-[#111] hover:bg-[#161616] transition-colors"
          >
            <span className="text-xs text-[#555]">特徴量プロファイリングレポート（オプション）</span>
            <svg className={`w-3 h-3 text-[#444] transition-transform ${showProfiling ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          {showProfiling && (
            <div className="px-5 pb-5 pt-4 bg-[#0d0d0d] border-t border-[#1e1e1e] space-y-3">
              <label className="flex items-center gap-2 text-xs text-[#888] cursor-pointer select-none">
                <input type="checkbox" checked={useOptimized} onChange={e => setUseOptimized(e.target.checked)} className="w-3.5 h-3.5 accent-white" />
                LightGBM最適化済み（リーク除去・変換適用）
              </label>
              <div className="flex items-center gap-3">
                <button
                  onClick={handleStartProfiling}
                  disabled={profilingStatus === 'running'}
                  className={`px-4 py-2 rounded text-xs font-medium transition-colors ${profilingStatus === 'running' ? 'bg-[#1a1a1a] text-[#555] cursor-not-allowed' : 'bg-white text-black hover:bg-[#eee]'}`}
                >
                  {profilingStatus === 'running' ? '生成中...' : 'レポート生成'}
                </button>
                {profilingStatus === 'completed' && profilingJobId && (
                  <a href={`/api/profiling/html/${profilingJobId}`} target="_blank" rel="noopener noreferrer" className="text-xs text-[#4ade80] hover:underline">レポートを開く →</a>
                )}
              </div>
              {profilingStatus === 'running' && (
                <div className="space-y-1">
                  <div className="flex justify-between text-xs text-[#555]">
                    <span>{profilingMessage}</span>
                    <span>{profilingProgress}%</span>
                  </div>
                  <div className="w-full bg-[#1e1e1e] rounded-full h-1 overflow-hidden">
                    <div className="bg-[#555] h-1 rounded-full transition-all duration-700" style={{ width: `${profilingProgress}%` }} />
                  </div>
                </div>
              )}
              {profilingStatus === 'error' && <p className="text-xs text-red-400">{profilingMessage}</p>}
            </div>
          )}
        </div>

        {/* 次のステップ */}
        <div className="p-5 bg-[#111] border border-[#1e1e1e] rounded-lg flex items-center justify-between gap-4">
          <div>
            <div className="text-xs text-[#666] mb-0.5">次のステップ — 02</div>
            <div className="text-sm font-medium">モデル学習</div>
            <div className="text-xs text-[#555] mt-0.5">収集したデータでAIモデルをトレーニング</div>
          </div>
          <Link
            href="/train"
            className="shrink-0 flex items-center gap-1.5 bg-white text-black text-sm font-medium px-5 py-2.5 rounded hover:bg-[#eee] transition-colors"
          >
            モデル学習へ
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </Link>
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

