'use client'
import { useState, useRef, useCallback } from 'react'
import { authFetch } from '@/lib/auth-fetch'
import type { JobStatus } from '@/lib/types'

export type BatchProgress = {
  current: number
  total: number
  message: string
  eta: string
}

export type BatchResult = {
  races_collected: number
  elapsed_time: number
  stats: { period: string; total_months: number }
}

export type UseBatchScrapeOptions = {
  pollIntervalMs?: number
  maxPollAttempts?: number
  maxConsecutiveStatusFailures?: number
}

export type BatchFailureKind =
  | 'validation'
  | 'start_rejected'
  | 'execution'
  | 'monitoring'
  | 'client_stop'
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

const DEFAULT_OPTIONS = {
  pollIntervalMs: 3000,
  maxPollAttempts: 600,
  maxConsecutiveStatusFailures: 10,
} as const

const PERIOD_PATTERN = /^(\d{4})-(0[1-9]|1[0-2])$/
const CLIENT_STOP_MESSAGE = 'ブラウザ側の監視と次月投入を停止しました。開始済みのサーバージョブは継続している可能性があります。'
const COMPLETED_CONTRACT_MESSAGE = '完了応答の形式を確認できないため、サーバージョブの状態確認が必要'

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
  const startTimeRef = useRef(0)
  const inFlightRef = useRef(false)
  const executionLockedRef = useRef(false)

  const setExecutionLock = (locked: boolean) => {
    executionLockedRef.current = locked
    setIsExecutionLocked(locked)
  }

  const pollIntervalMs = Math.max(0, hookOptions?.pollIntervalMs ?? DEFAULT_OPTIONS.pollIntervalMs)
  const maxPollAttempts = Math.max(1, hookOptions?.maxPollAttempts ?? DEFAULT_OPTIONS.maxPollAttempts)
  const maxConsecutiveStatusFailures = Math.max(
    1,
    hookOptions?.maxConsecutiveStatusFailures ?? DEFAULT_OPTIONS.maxConsecutiveStatusFailures,
  )

  const start = useCallback(async (
    startPeriod: string,
    endPeriod: string,
    forceRescrape: boolean,
  ): Promise<BatchResult> => {
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
      startTimeRef.current = Date.now()

      let totalRaces = 0
      let completedMonths = 0

      for (const { year, month } of months) {
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

        const startRes = await authFetch('/api/scrape', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ start_date: startDateStr, end_date: endDateStr, force_rescrape: forceRescrape }),
        })

        if (!startRes.ok) {
          let detail = ''
          try {
            const err = await startRes.json()
            if (isRecord(err) && typeof err.detail === 'string') {
              detail = err.detail
            }
          } catch {
            // fall through to status code message
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

        setJobId(currentJobId)
        setStatus('queued')

        let done = false
        let pollAttempts = 0
        let consecutiveTransportFailures = 0
        let consecutiveNotFound = 0

        while (!done && !abortRef.current) {
          pollAttempts += 1
          if (pollAttempts > maxPollAttempts) {
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
          if (rawStatus === 'queued' || rawStatus === 'running') {
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

          let eta = ''
          if (completedMonths > 0) {
            const elapsed = Date.now() - startTimeRef.current
            const msPerMonth = elapsed / completedMonths
            const remainingSec = Math.round(msPerMonth * (totalMonths - completedMonths) / 1000)
            eta = remainingSec >= 60 ? `残り約${Math.ceil(remainingSec / 60)}分` : `残り約${remainingSec}秒`
          }

          if (rawStatus === 'queued') {
            setStatus('queued')
          } else if (rawStatus === 'running') {
            setStatus('running')
          }

          setProgress({
            current: overallPct,
            total: 100,
            message: rawStatus === 'queued'
              ? `${year}年${month}月 (${completedMonths + 1}/${totalMonths}ヶ月): 開始待ち`
              : `${year}年${month}月 (${completedMonths + 1}/${totalMonths}ヶ月): ${typeof progressPayload.message === 'string' ? progressPayload.message : '取得実行中...'}`,
            eta,
          })

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
            done = true
            totalRaces += racesCollectedNumber
            completedMonths += 1
          } else if (rawStatus === 'error') {
            const message = typeof statusPayload.error === 'string' && statusPayload.error.trim().length > 0
              ? statusPayload.error
              : `${year}年${month}月のスクレイピングが失敗しました`
            fail(message, 'execution', true)
          }
        }

        if (abortRef.current) {
          fail(CLIENT_STOP_MESSAGE, 'client_stop', false)
        }
      }

      if (completedMonths !== totalMonths) {
        fail('実行状態を確認できません。開始済みのサーバージョブは継続している可能性があります。', 'monitoring', false)
      }

      const elapsed = Math.round((Date.now() - startTimeRef.current) / 1000)
      setProgress({ current: 100, total: 100, message: `完了: ${totalRaces}レース取得`, eta: '' })
      setStatus('completed')
      const batchResult: BatchResult = {
        races_collected: totalRaces,
        elapsed_time: elapsed,
        stats: { period: `${startYear}年${startMonth}月〜${endYear}年${endMonth}月`, total_months: totalMonths },
      }
      setResult(batchResult)
      setFailureKind(null)
      setCanRetry(false)
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

      setProgress({ current: 0, total: 100, message: 'エラーが発生しました', eta: '' })
      setStatus('error')
      setError(normalized.message)
      setFailureKind(normalized.kind)
      setCanRetry(normalized.safeToRetry)
      if (normalized.kind === 'monitoring' || normalized.kind === 'client_stop') {
        setExecutionLock(true)
      }
      throw normalized
    } finally {
      setLoading(false)
      inFlightRef.current = false
    }
  }, [maxConsecutiveStatusFailures, maxPollAttempts, pollIntervalMs])

  const abort = useCallback(() => {
    abortRef.current = true
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
  }
}
