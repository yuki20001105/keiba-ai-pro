'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { authFetch } from '@/lib/auth-fetch'

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

type StoredBatchJob = {
  jobId: string
  startPeriod: string
  endPeriod: string
  forceRescrape: boolean
  startedAt: number
}

export const ACTIVE_SCRAPE_JOB_KEY = 'keiba-ai-pro:active-scrape-job:v1'

const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

function monthSpan(startPeriod: string, endPeriod: string): number {
  const [sy, sm] = startPeriod.split('-').map(Number)
  const [ey, em] = endPeriod.split('-').map(Number)
  return Math.max(0, (ey - sy) * 12 + (em - sm) + 1)
}

function toDateRange(startPeriod: string, endPeriod: string) {
  const [sy, sm] = startPeriod.split('-').map(Number)
  const [ey, em] = endPeriod.split('-').map(Number)
  const pad = (value: number) => String(value).padStart(2, '0')
  const lastDay = new Date(ey, em, 0).getDate()
  return {
    startDate: `${sy}${pad(sm)}01`,
    endDate: `${ey}${pad(em)}${pad(lastDay)}`,
  }
}

function readStoredJob(): StoredBatchJob | null {
  try {
    const raw = localStorage.getItem(ACTIVE_SCRAPE_JOB_KEY)
    if (!raw) return null
    const value = JSON.parse(raw)
    if (!value || typeof value.jobId !== 'string' || !value.jobId) return null
    return value as StoredBatchJob
  } catch {
    localStorage.removeItem(ACTIVE_SCRAPE_JOB_KEY)
    return null
  }
}

function clearStoredJob(jobId: string) {
  const stored = readStoredJob()
  if (stored?.jobId === jobId) localStorage.removeItem(ACTIVE_SCRAPE_JOB_KEY)
}

/**
 * Tracks one backend-owned scrape job for the entire selected date range.
 * The active job metadata is stored in localStorage so a page reload can
 * reconnect without starting a duplicate job.
 */
export function useBatchScrape() {
  const [loading, setLoading] = useState(false)
  const [progress, setProgress] = useState<BatchProgress>({ current: 0, total: 100, message: '', eta: '' })
  const [result, setResult] = useState<BatchResult | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const abortRef = useRef(false)
  const trackingRef = useRef<string | null>(null)

  const trackJob = useCallback(async (stored: StoredBatchJob): Promise<BatchResult> => {
    if (trackingRef.current === stored.jobId) {
      throw new Error('This scrape job is already being tracked.')
    }
    trackingRef.current = stored.jobId
    abortRef.current = false
    setJobId(stored.jobId)
    setLoading(true)
    setResult(null)

    let consecutiveFailures = 0
    try {
      while (!abortRef.current) {
        const statusRes = await authFetch(`/api/scrape/status/${stored.jobId}`)
        if (!statusRes.ok) {
          consecutiveFailures += 1
          // The backend owns the durable job. Keep reconnecting for up to five
          // minutes so a FastAPI restart does not make the browser abandon it.
          if (consecutiveFailures >= 100) throw new Error(`ステータス取得失敗 (job_id: ${stored.jobId})`)
          await delay(3000)
          continue
        }

        consecutiveFailures = 0
        const status = await statusRes.json()
        if (status.status === 'not_found') {
          clearStoredJob(stored.jobId)
          throw new Error(`ジョブが見つかりません (job_id: ${stored.jobId})`)
        }

        const backendProgress = status.progress || {}
        const done = Number(backendProgress.done || 0)
        const total = Number(backendProgress.total || 0)
        const fraction = total > 0 ? Math.min(1, done / total) : 0
        const current = status.status === 'completed' ? 100 : Math.round(fraction * 95)
        const elapsedMs = Math.max(1, Date.now() - stored.startedAt)
        const remainingSeconds = done > 0 && total > done
          ? Math.round((elapsedMs / done) * (total - done) / 1000)
          : 0
        const eta = remainingSeconds > 0
          ? remainingSeconds >= 60
            ? `残り約${Math.ceil(remainingSeconds / 60)}分`
            : `残り約${remainingSeconds}秒`
          : ''

        const durableStatusMessage = status.status === 'recovering'
          ? 'バックエンド再起動後のジョブへ自動再接続しています...'
          : status.status === 'waiting_resources'
            ? 'メモリ空き容量を待機中です。ジョブは自動再開されます。'
            : backendProgress.message

        setProgress({
          current,
          total: 100,
          message: durableStatusMessage || `バックエンドジョブ ${stored.jobId} を実行中...`,
          eta,
        })

        if (status.status === 'completed') {
          const payload = status.result || {}
          const completed: BatchResult = {
            races_collected: Number(payload.races_collected || 0),
            elapsed_time: Number(payload.elapsed_time || 0),
            stats: {
              period: `${stored.startPeriod} ～ ${stored.endPeriod}`,
              total_months: monthSpan(stored.startPeriod, stored.endPeriod),
            },
          }
          clearStoredJob(stored.jobId)
          setProgress({ current: 100, total: 100, message: `完了: ${completed.races_collected}レース取得`, eta: '' })
          setResult(completed)
          return completed
        }

        if (status.status === 'error') {
          clearStoredJob(stored.jobId)
          throw new Error(status.error || 'スクレイピングジョブが失敗しました')
        }

        await delay(3000)
      }

      throw new Error('画面上の追跡を停止しました。バックエンドジョブは継続しています。')
    } finally {
      trackingRef.current = null
      setLoading(false)
      setJobId(null)
    }
  }, [])

  const start = useCallback(async (
    startPeriod: string,
    endPeriod: string,
    forceRescrape: boolean,
  ): Promise<BatchResult> => {
    const { startDate, endDate } = toDateRange(startPeriod, endPeriod)
    const startRes = await authFetch('/api/scrape', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        start_date: startDate,
        end_date: endDate,
        force_rescrape: forceRescrape,
        dry_run: false,
      }),
    })
    if (!startRes.ok) {
      const error = await startRes.json().catch(() => ({}))
      throw new Error(error.detail || `HTTP ${startRes.status}`)
    }

    const response = await startRes.json()
    const stored: StoredBatchJob = {
      jobId: String(response.job_id),
      startPeriod,
      endPeriod,
      forceRescrape,
      startedAt: Date.now(),
    }
    localStorage.setItem(ACTIVE_SCRAPE_JOB_KEY, JSON.stringify(stored))
    return trackJob(stored)
  }, [trackJob])

  useEffect(() => {
    const stored = readStoredJob()
    if (!stored || trackingRef.current) return
    trackJob(stored).catch(error => {
      setProgress({
        current: 0,
        total: 100,
        message: error instanceof Error ? error.message : '再接続に失敗しました',
        eta: '',
      })
    })
  }, [trackJob])

  const abort = useCallback(() => {
    abortRef.current = true
  }, [])

  return { loading, progress, result, jobId, start, abort }
}
