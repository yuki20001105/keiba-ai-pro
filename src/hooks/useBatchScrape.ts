'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { authFetch } from '@/lib/auth-fetch'
import { formatApiErrorDetail } from '@/lib/api-error'
import type { JobStatus } from '@/lib/types'

export type BatchProgress = {
  current: number
  total: number
  message: string
  eta: string
  newSavedRaces?: number
  newSavedHorses?: number
  existingRacesSkipped?: number
  verifiedNoRaceDates?: number
}

export type BatchResult = {
  races_collected: number
  saved_horses: number
  existing_races_skipped: number
  verified_no_race_dates: number
  elapsed_time: number
  stats: { period: string; total_months: number }
}

export type BatchAcceptedJob = {
  jobId: string
  status: 'queued' | 'running'
  startDate: string
  endDate: string
  acceptedAt: string
}

export type UseBatchScrapeOptions = {
  pollIntervalMs?: number
  maxPollAttempts?: number
  maxPollDurationMs?: number
  maxConsecutiveStatusFailures?: number
  onJobAccepted?: (job: BatchAcceptedJob) => void
}

export type BatchFailureKind =
  | 'validation'
  | 'start_rejected'
  | 'execution'
  | 'monitoring'
  | 'client_stop'
  | 'cancelled'
  | 'busy'
  | null

export class BatchScrapeError extends Error {
  readonly kind: Exclude<BatchFailureKind, null>
  readonly safeToRetry: boolean

  constructor(message: string, kind: Exclude<BatchFailureKind, null>, safeToRetry: boolean) {
    super(message)
    this.name = 'BatchScrapeError'
    this.kind = kind
    this.safeToRetry = safeToRetry
  }
}

type ParsedPeriod = {
  year: number
  month: number
}

export const BATCH_SCRAPE_MAX_POLL_DURATION_MS = 24 * 60 * 60 * 1000

const DEFAULT_OPTIONS = {
  pollIntervalMs: 3000,
  maxPollDurationMs: BATCH_SCRAPE_MAX_POLL_DURATION_MS,
  maxConsecutiveStatusFailures: 10,
} as const

const PERIOD_PATTERN = /^(\d{4})-(0[1-9]|1[0-2])$/
const CLIENT_STOP_MESSAGE = 'ブラウザ側の監視と次月投入を停止しました。開始済みのサーバージョブは継続している可能性があります。'
const SERVER_CANCELLED_MESSAGE = 'データ取得を停止しました。停止前に保存済みのデータは保持されています。'
const COMPLETED_CONTRACT_MESSAGE = '完了応答の形式を確認できないため、サーバージョブの状態確認が必要'
const OWNER_ACTIVE_JOB_MESSAGE = '別のデータ取得が実行中です。完了までお待ちください。'

function parsePeriod(input: string): ParsedPeriod | null {
  if (typeof input !== 'string') return null
  const trimmed = input.trim()
  const match = PERIOD_PATTERN.exec(trimmed)
  if (!match) return null
  const year = Number(match[1])
  const month = Number(match[2])
  if (!Number.isFinite(year) || !Number.isFinite(month) || month < 1 || month > 12) {
    return null
  }
  return { year, month }
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null
}

/**
 * 期間指定バッチスクレイピングフック。
 * 月単位でジョブを順次投入し、各ジョブが完了するまでポーリングする。
 * `start()` は完了時に BatchResult を返し、エラー時は BatchScrapeError をスローする。
 */
export function useBatchScrape(hookOptions?: UseBatchScrapeOptions) {
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState<JobStatus>('idle')
  const [error, setError] = useState<string | null>(null)
  const [failureKind, setFailureKind] = useState<BatchFailureKind>(null)
  const [canRetry, setCanRetry] = useState(false)
  const [isExecutionLocked, setIsExecutionLocked] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [progress, setProgress] = useState<BatchProgress>({ current: 0, total: 100, message: '', eta: '' })
  const [result, setResult] = useState<BatchResult | null>(null)
  const abortRef = useRef(false)
  const batchStopRequestedRef = useRef(false)
  const startTimeRef = useRef(0)
  const inFlightRef = useRef(false)
  const executionLockedRef = useRef(false)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      // Let the already accepted server job reach a durable terminal state, but
      // never allow this detached hook instance to enqueue another month.
      batchStopRequestedRef.current = true
    }
  }, [])

  const setExecutionLock = (locked: boolean) => {
    executionLockedRef.current = locked
    if (mountedRef.current) setIsExecutionLocked(locked)
  }

  const pollIntervalMs = Math.max(0, hookOptions?.pollIntervalMs ?? DEFAULT_OPTIONS.pollIntervalMs)
  // maxPollAttempts remains available for deterministic tests. Normal operation
  // uses an elapsed-time deadline so a slow but healthy job is not abandoned
  // merely because it needed more polling cycles.
  const maxPollAttempts = hookOptions?.maxPollAttempts == null
    ? null
    : Math.max(1, hookOptions.maxPollAttempts)
  const maxPollDurationMs = Math.max(1, hookOptions?.maxPollDurationMs ?? DEFAULT_OPTIONS.maxPollDurationMs)
  const maxConsecutiveStatusFailures = Math.max(
    1,
    hookOptions?.maxConsecutiveStatusFailures ?? DEFAULT_OPTIONS.maxConsecutiveStatusFailures,
  )
  const onJobAccepted = hookOptions?.onJobAccepted

  const start = useCallback(async (
    startPeriod: string,
    endPeriod: string,
    forceRescrape: boolean,
  ): Promise<BatchResult> => {
    if (!mountedRef.current) {
      throw new BatchScrapeError(CLIENT_STOP_MESSAGE, 'client_stop', false)
    }
    if (inFlightRef.current) {
      throw new BatchScrapeError('前回の取得処理が進行中です', 'busy', false)
    }
    if (executionLockedRef.current) {
      throw new BatchScrapeError(
        '実行状態を確認できません。履歴またはjob statusを確認するまで新規実行しないでください。',
        'busy',
        false,
      )
    }

    inFlightRef.current = true

    const fail = (message: string, kind: Exclude<BatchFailureKind, null>, safeToRetry: boolean): never => {
      throw new BatchScrapeError(message, kind, safeToRetry)
    }

    try {
      const parsedStart = parsePeriod(startPeriod)
      const parsedEnd = parsePeriod(endPeriod)
      if (!parsedStart || !parsedEnd) {
        fail('期間指定が不正です（YYYY-MM, 月は01-12）', 'validation', false)
      }
      const startParsed = parsedStart as ParsedPeriod
      const endParsed = parsedEnd as ParsedPeriod

      const startYear = startParsed.year
      const startMonth = startParsed.month
      const endYear = endParsed.year
      const endMonth = endParsed.month

      if (new Date(startYear, startMonth - 1, 1) > new Date(endYear, endMonth - 1, 1)) {
        fail('期間指定が不正です（開始年月は終了年月以前にしてください）', 'validation', false)
      }

      const months: { year: number; month: number }[] = []
      let y = startYear
      let m = startMonth
      while (y < endYear || (y === endYear && m <= endMonth)) {
        months.push({ year: y, month: m })
        m += 1
        if (m > 12) {
          m = 1
          y += 1
        }
      }

      const totalMonths = months.length
      if (totalMonths <= 0) {
        fail('期間指定が不正です（対象月が0件です）', 'validation', false)
      }

      setLoading(true)
      setStatus('queued')
      setError(null)
      setFailureKind(null)
      setCanRetry(false)
      setJobId(null)
      setResult(null)
      setExecutionLock(false)
      abortRef.current = false
      batchStopRequestedRef.current = false
      startTimeRef.current = Date.now()

      let totalRaces = 0
      let totalSavedHorses = 0
      let totalExistingRacesSkipped = 0
      let totalVerifiedNoRaceDates = 0
      let completedMonths = 0

      for (const { year, month } of months) {
        if (!mountedRef.current) {
          fail(CLIENT_STOP_MESSAGE, 'client_stop', false)
        }
        if (batchStopRequestedRef.current) {
          fail(SERVER_CANCELLED_MESSAGE, 'cancelled', false)
        }
        if (abortRef.current) {
          fail(CLIENT_STOP_MESSAGE, 'client_stop', false)
        }

        const pad = (n: number) => String(n).padStart(2, '0')
        const startDateStr = `${year}${pad(month)}01`
        const lastDay = new Date(year, month, 0).getDate()
        const endDateStr = `${year}${pad(month)}${pad(lastDay)}`

        setProgress({
          current: Math.round((completedMonths / totalMonths) * 95),
          total: 100,
          message: `${year}年${month}月の開始待ち (${completedMonths + 1}/${totalMonths}ヶ月)`,
          eta: '',
        })
        setStatus('queued')

        if (!mountedRef.current) {
          fail(CLIENT_STOP_MESSAGE, 'client_stop', false)
        }
        if (batchStopRequestedRef.current) {
          fail(SERVER_CANCELLED_MESSAGE, 'cancelled', false)
        }
        if (abortRef.current) {
          fail(CLIENT_STOP_MESSAGE, 'client_stop', false)
        }
        const startRes = await authFetch('/api/scrape', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ start_date: startDateStr, end_date: endDateStr, force_rescrape: forceRescrape }),
        })

        if (!startRes.ok) {
          let detail = ''
          let ownerActiveJob = false
          try {
            const err = await startRes.json()
            if (isRecord(err) && err.detail !== undefined) {
              ownerActiveJob = err.detail === 'owner-active-job'
              detail = formatApiErrorDetail(err.detail, '')
            }
          } catch {
            // fall through to status code message
          }
          if (startRes.status === 409 && ownerActiveJob) {
            fail(OWNER_ACTIVE_JOB_MESSAGE, 'busy', false)
          }
          fail(detail || `HTTP ${startRes.status}`, 'start_rejected', true)
        }

        let startPayload: unknown
        try {
          startPayload = await startRes.json()
        } catch {
          fail('ジョブ開始応答が不正です（JSON）', 'monitoring', false)
        }
        const currentJobIdRaw = isRecord(startPayload) ? startPayload.job_id : undefined
        if (typeof currentJobIdRaw !== 'string' || currentJobIdRaw.trim().length === 0) {
          fail('ジョブ開始応答が不正です（job_id）', 'monitoring', false)
        }
        const currentJobId = currentJobIdRaw as string
        const acceptedStatus = isRecord(startPayload) && startPayload.status === 'running'
          ? 'running'
          : 'queued'
        const acceptedAt = isRecord(startPayload)
          && typeof startPayload.created_at === 'string'
          && Number.isFinite(Date.parse(startPayload.created_at))
          ? startPayload.created_at
          : new Date().toISOString()

        if (mountedRef.current) {
          setJobId(currentJobId)
          setStatus(acceptedStatus)
          onJobAccepted?.({
            jobId: currentJobId,
            status: acceptedStatus,
            startDate: startDateStr,
            endDate: endDateStr,
            acceptedAt,
          })
        }

        let done = false
        let pollAttempts = 0
        const pollStartedAt = Date.now()
        let consecutiveTransportFailures = 0
        let consecutiveNotFound = 0

        while (!done && !abortRef.current) {
          pollAttempts += 1
          if (Date.now() - pollStartedAt >= maxPollDurationMs) {
            fail(`ステータス確認が24時間の監視期限に達しました (job_id: ${currentJobId})`, 'monitoring', false)
          }
          if (maxPollAttempts !== null && pollAttempts > maxPollAttempts) {
            fail(`ステータス取得が上限回数に達しました (job_id: ${currentJobId})`, 'monitoring', false)
          }

          await new Promise(resolve => setTimeout(resolve, pollIntervalMs))

          const statusRes = await authFetch(`/api/scrape/status/${currentJobId}`)
          if (!statusRes.ok) {
            consecutiveTransportFailures += 1
            if (consecutiveTransportFailures >= maxConsecutiveStatusFailures) {
              fail(`ステータス取得失敗 (job_id: ${currentJobId})`, 'monitoring', false)
            }
            continue
          }
          consecutiveTransportFailures = 0

          let statusPayloadUnknown: unknown
          try {
            statusPayloadUnknown = await statusRes.json()
          } catch {
            fail(`ステータス応答が不正です（JSON, job_id: ${currentJobId}）`, 'monitoring', false)
          }
          if (!isRecord(statusPayloadUnknown)) {
            fail(`ステータス応答が不正です（object, job_id: ${currentJobId}）`, 'monitoring', false)
          }
          const statusPayload = statusPayloadUnknown as Record<string, unknown>

          if (statusPayload.status === 'not_found') {
            consecutiveNotFound += 1
            if (consecutiveNotFound >= maxConsecutiveStatusFailures) {
              fail(`ジョブが見つかりません (job_id: ${currentJobId})`, 'monitoring', false)
            }
            continue
          }

          const rawStatus = typeof statusPayload.status === 'string' ? statusPayload.status : ''
          if (rawStatus === 'queued' || rawStatus === 'running' || rawStatus === 'cancelling') {
            consecutiveNotFound = 0
          }

          const progressPayload = isRecord(statusPayload.progress) ? statusPayload.progress : {}
          const doneCount = typeof progressPayload.done === 'number' && Number.isFinite(progressPayload.done)
            ? progressPayload.done
            : 0
          const totalCount = typeof progressPayload.total === 'number' && Number.isFinite(progressPayload.total)
            ? progressPayload.total
            : 0
          const monthPct = totalCount > 0 ? doneCount / totalCount : 0
          const overallPct = Math.round(((completedMonths + monthPct) / totalMonths) * 95)
          const progressNumber = (key: string): number => {
            const value = progressPayload[key]
            return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : 0
          }

          let eta = ''
          if (completedMonths > 0) {
            const elapsed = Date.now() - startTimeRef.current
            const msPerMonth = elapsed / completedMonths
            const remainingSec = Math.round(msPerMonth * (totalMonths - completedMonths) / 1000)
            eta = remainingSec >= 60 ? `残り約${Math.ceil(remainingSec / 60)}分` : `残り約${remainingSec}秒`
          }

          if (mountedRef.current) {
            if (rawStatus === 'queued') {
              setStatus('queued')
            } else if (rawStatus === 'running') {
              setStatus('running')
            } else if (rawStatus === 'cancelling') {
              setStatus('cancelling')
            }

            setProgress({
              current: overallPct,
              total: 100,
              message: rawStatus === 'queued'
                ? `${year}年${month}月 (${completedMonths + 1}/${totalMonths}ヶ月): 開始待ち`
                : rawStatus === 'cancelling'
                  ? `${year}年${month}月 (${completedMonths + 1}/${totalMonths}ヶ月): 安全な区切りで停止中...`
                  : `${year}年${month}月 (${completedMonths + 1}/${totalMonths}ヶ月): ${typeof progressPayload.message === 'string' ? progressPayload.message : '取得実行中...'}`,
              eta,
              newSavedRaces: progressNumber('saved_races'),
              newSavedHorses: progressNumber('saved_horses'),
              existingRacesSkipped: progressNumber('existing_races_skipped'),
              verifiedNoRaceDates: progressNumber('no_race_dates'),
            })
          }

          if (rawStatus === 'completed') {
            if (!isRecord(statusPayload.result)) {
              fail(COMPLETED_CONTRACT_MESSAGE, 'monitoring', false)
            }
            const resultPayload = statusPayload.result as Record<string, unknown>
            const racesCollected = resultPayload.races_collected
            if (
              typeof racesCollected !== 'number'
              || !Number.isFinite(racesCollected)
              || !Number.isInteger(racesCollected)
              || racesCollected < 0
            ) {
              fail(COMPLETED_CONTRACT_MESSAGE, 'monitoring', false)
            }
            const racesCollectedNumber = racesCollected as number
            const resultNumber = (key: string): number => {
              const value = resultPayload[key]
              return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : 0
            }
            done = true
            totalRaces += racesCollectedNumber
            totalSavedHorses += resultNumber('saved_horses')
            totalExistingRacesSkipped += resultNumber('existing_races_skipped')
            totalVerifiedNoRaceDates += resultNumber('verified_no_race_dates')
            completedMonths += 1
          } else if (rawStatus === 'error') {
            const message = typeof statusPayload.error === 'string' && statusPayload.error.trim().length > 0
              ? statusPayload.error
              : `${year}年${month}月のスクレイピングが失敗しました`
            fail(message, 'execution', true)
          } else if (rawStatus === 'cancelled') {
            fail(SERVER_CANCELLED_MESSAGE, 'cancelled', false)
          }
        }

        if (!mountedRef.current || abortRef.current) {
          fail(CLIENT_STOP_MESSAGE, 'client_stop', false)
        }
      }

      if (completedMonths !== totalMonths) {
        fail('実行状態を確認できません。開始済みのサーバージョブは継続している可能性があります。', 'monitoring', false)
      }

      const elapsed = Math.round((Date.now() - startTimeRef.current) / 1000)
      if (mountedRef.current) {
        setProgress({ current: 100, total: 100, message: `完了: ${totalRaces}レース取得`, eta: '' })
        setStatus('completed')
      }
      const batchResult: BatchResult = {
        races_collected: totalRaces,
        saved_horses: totalSavedHorses,
        existing_races_skipped: totalExistingRacesSkipped,
        verified_no_race_dates: totalVerifiedNoRaceDates,
        elapsed_time: elapsed,
        stats: { period: `${startYear}年${startMonth}月〜${endYear}年${endMonth}月`, total_months: totalMonths },
      }
      if (mountedRef.current) {
        setResult(batchResult)
        setFailureKind(null)
        setCanRetry(false)
      }
      setExecutionLock(false)
      return batchResult
    } catch (error: unknown) {
      const normalized = error instanceof BatchScrapeError
        ? error
        : new BatchScrapeError(
          error instanceof Error ? error.message : 'エラーが発生しました',
          'monitoring',
          false,
        )

      if (mountedRef.current) {
        if (normalized.kind === 'cancelled') {
          setProgress(current => ({ ...current, message: '停止しました', eta: '' }))
          setStatus('cancelled')
          setError(null)
          setExecutionLock(false)
        } else {
          setProgress({ current: 0, total: 100, message: 'エラーが発生しました', eta: '' })
          setStatus('error')
          setError(normalized.message)
        }
        setFailureKind(normalized.kind)
        setCanRetry(normalized.safeToRetry)
      }
      if (normalized.kind === 'monitoring' || normalized.kind === 'client_stop') {
        setExecutionLock(true)
      }
      throw normalized
    } finally {
      if (mountedRef.current) setLoading(false)
      inFlightRef.current = false
    }
  }, [maxConsecutiveStatusFailures, maxPollAttempts, maxPollDurationMs, onJobAccepted, pollIntervalMs])

  const abort = useCallback(() => {
    abortRef.current = true
  }, [])

  // Prevent a multi-month batch from enqueueing its next month after the user
  // has requested server-side cancellation. Unlike abort(), status polling for
  // the current server job continues until a durable terminal state is seen.
  const requestBatchStop = useCallback(() => {
    batchStopRequestedRef.current = true
  }, [])

  const clearExecutionLockAfterReconciliation = useCallback((): boolean => {
    if (inFlightRef.current) {
      return false
    }
    executionLockedRef.current = false
    setIsExecutionLocked(false)
    return true
  }, [])

  return {
    loading,
    status,
    error,
    failureKind,
    canRetry,
    isExecutionLocked,
    jobId,
    progress,
    result,
    start,
    abort,
    requestBatchStop,
    clearExecutionLockAfterReconciliation,
  }
}
