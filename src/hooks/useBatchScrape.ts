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
const ABORT_MESSAGE = '取得を中断しました'

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

/**
 * 期間指定バッチスクレイピングフック。
 * 月単位でジョブを順次投入し、各ジョブが完了するまで 3 秒間隔でポーリングする。
 * `start()` は完了時に BatchResult を返し、エラー時はスローする。
 */
export function useBatchScrape(hookOptions?: UseBatchScrapeOptions) {
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState<JobStatus>('idle')
  const [error, setError] = useState<string | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [progress, setProgress] = useState<BatchProgress>({ current: 0, total: 100, message: '', eta: '' })
  const [result, setResult] = useState<BatchResult | null>(null)
  const abortRef = useRef(false)
  const startTimeRef = useRef(0)
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
    if (loading || status === 'queued' || status === 'running') {
      const message = '前回の取得処理が進行中です'
      setStatus('error')
      setError(message)
      setProgress({ current: 0, total: 100, message, eta: '' })
      throw new Error(message)
    }

    const parsedStart = parsePeriod(startPeriod)
    const parsedEnd = parsePeriod(endPeriod)
    if (!parsedStart || !parsedEnd) {
      const message = '期間指定が不正です（YYYY-MM, 月は01-12）'
      setStatus('error')
      setError(message)
      setProgress({ current: 0, total: 100, message, eta: '' })
      throw new Error(message)
    }

    const startYear = parsedStart.year
    const startMonth = parsedStart.month
    const endYear = parsedEnd.year
    const endMonth = parsedEnd.month

    if (new Date(startYear, startMonth - 1, 1) > new Date(endYear, endMonth - 1, 1)) {
      const message = '期間指定が不正です（開始年月は終了年月以前にしてください）'
      setStatus('error')
      setError(message)
      setProgress({ current: 0, total: 100, message, eta: '' })
      throw new Error(message)
    }

    // 取得対象の月リストを生成
    const months: { year: number; month: number }[] = []
    let y = startYear, m = startMonth
    while (y < endYear || (y === endYear && m <= endMonth)) {
      months.push({ year: y, month: m })
      m++
      if (m > 12) { m = 1; y++ }
    }
    const totalMonths = months.length
    if (totalMonths <= 0) {
      const message = '期間指定が不正です（対象月が0件です）'
      setStatus('error')
      setError(message)
      setProgress({ current: 0, total: 100, message, eta: '' })
      throw new Error(message)
    }

    setLoading(true)
    setStatus('queued')
    setError(null)
    setJobId(null)
    setResult(null)
    abortRef.current = false
    startTimeRef.current = Date.now()
    let totalRaces = 0
    let completedMonths = 0

    try {
      for (const { year, month } of months) {
        if (abortRef.current) {
          throw new Error('取得が中止されました')
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
          const err = await startRes.json()
          throw new Error(err.detail || `HTTP ${startRes.status}`)
        }
        const { job_id } = await startRes.json()
        if (typeof job_id !== 'string' || job_id.trim().length === 0) {
          throw new Error('ジョブ開始応答が不正です（job_id）')
        }
        setJobId(job_id)
        setStatus('queued')

        // ジョブ完了までポーリング（3秒間隔）
        let done = false
        let pollAttempts = 0
        let consecutiveTransportFailures = 0
        let consecutiveNotFound = 0
        while (!done && !abortRef.current) {
          pollAttempts += 1
          if (pollAttempts > maxPollAttempts) {
            throw new Error(`ステータス取得が上限回数に達しました (job_id: ${job_id})`)
          }
          await new Promise(resolve => setTimeout(resolve, pollIntervalMs))
          const statusRes = await authFetch(`/api/scrape/status/${job_id}`)
          if (!statusRes.ok) {
            consecutiveTransportFailures += 1
            if (consecutiveTransportFailures >= maxConsecutiveStatusFailures) {
              throw new Error(`ステータス取得失敗 (job_id: ${job_id})`)
            }
            continue
          }

          consecutiveTransportFailures = 0
          const status = await statusRes.json()
          if (status.status === 'not_found') {
            consecutiveNotFound += 1
            if (consecutiveNotFound >= maxConsecutiveStatusFailures) {
              throw new Error(`ジョブが見つかりません (job_id: ${job_id})`)
            }
            continue
          }

          const prog = status.progress || {}
          const monthPct = prog.total > 0 ? prog.done / prog.total : 0
          const overallPct = Math.round(((completedMonths + monthPct) / totalMonths) * 95)
          let eta = ''
          if (completedMonths > 0) {
            const elapsed = Date.now() - startTimeRef.current
            const msPerMonth = elapsed / completedMonths
            const remainingSec = Math.round(msPerMonth * (totalMonths - completedMonths) / 1000)
            eta = remainingSec >= 60 ? `残り約${Math.ceil(remainingSec / 60)}分` : `残り約${remainingSec}秒`
          }

          const rawStatus = typeof status.status === 'string' ? status.status : ''
          if (rawStatus === 'queued') {
            consecutiveNotFound = 0
            setStatus('queued')
          } else if (rawStatus === 'running') {
            consecutiveNotFound = 0
            setStatus('running')
          }

          setProgress({
            current: overallPct,
            total: 100,
            message: rawStatus === 'queued'
              ? `${year}年${month}月 (${completedMonths + 1}/${totalMonths}ヶ月): 開始待ち`
              : `${year}年${month}月 (${completedMonths + 1}/${totalMonths}ヶ月): ${prog.message || '取得実行中...'}`,
            eta,
          })

          if (rawStatus === 'completed') {
            done = true
            totalRaces += status.result?.races_collected || 0
            completedMonths++
          } else if (rawStatus === 'error') {
            const message = status.error || `${year}年${month}月のスクレイピングが失敗しました`
            setStatus('error')
            setError(message)
            throw new Error(message)
          }
        }
      }

      if (abortRef.current || completedMonths !== totalMonths) {
        throw new Error(ABORT_MESSAGE)
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
      return batchResult
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : 'エラーが発生しました'
      setProgress({ current: 0, total: 100, message: 'エラーが発生しました', eta: '' })
      setStatus('error')
      setError(message)
      throw error
    } finally {
      setLoading(false)
    }
  }, [loading, maxConsecutiveStatusFailures, maxPollAttempts, pollIntervalMs, status])

  const abort = useCallback(() => { abortRef.current = true }, [])

  return { loading, status, error, jobId, progress, result, start, abort }
}
