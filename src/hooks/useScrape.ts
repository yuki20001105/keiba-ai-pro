import { useState, useCallback, useRef } from 'react'
import { authFetch } from '@/lib/auth-fetch'
import type { ScrapeStatus } from '@/lib/types'
import { supportsCachedCardAcquisition } from '@/lib/scrape-safety-policy'

export interface ScrapeParams {
  startDate: string  // YYYYMMDD
  endDate: string    // YYYYMMDD
  force?: boolean
  horseDetails?: 'full' | 'cached_only'
}

export interface ScrapeState {
  status: ScrapeStatus
  message: string
  jobId: string | null
}

export interface UseScrapeResult extends ScrapeState {
  /** スクレイプジョブを開始し job_id を返す。ポーリングは呼び出し側で `useJobPoller` に委譲する。 */
  startScrape: (params: ScrapeParams) => Promise<string | null>
  setStatus: (s: ScrapeStatus) => void
  setMessage: (m: string) => void
  reset: () => void
}

/**
 * スクレイプジョブ起動フック。
 *
 * - Supabase セッションから Bearer token を付与する
 * - ジョブ開始（POST /api/scrape）だけを担当し、ポーリングは `useJobPoller` に委譲
 *
 * 使用例:
 * ```ts
 * const { startScrape, status, message, jobId } = useScrape()
 * const { status: pollStatus } = useJobPoller({
 *   jobId,
 *   getStatusUrl: id => `/api/scrape/status/${id}`,
 *   onCompleted: () => { scrape.setStatus('done'); loadRaces() },
 *   onError: msg => { scrape.setStatus('error'); scrape.setMessage(msg) },
 * })
 * ```
 */
export function useScrape(): UseScrapeResult {
  const generation = useRef(0)
  const [state, setState] = useState<ScrapeState>({
    status: 'idle',
    message: '',
    jobId: null,
  })

  const setStatus = useCallback((s: ScrapeStatus) => {
    setState(prev => ({ ...prev, status: s }))
  }, [])

  const setMessage = useCallback((m: string) => {
    setState(prev => ({ ...prev, message: m }))
  }, [])

  const reset = useCallback(() => {
    ++generation.current
    setState({ status: 'idle', message: '', jobId: null })
  }, [])

  const startScrape = useCallback(async ({ startDate, endDate, force = false, horseDetails = 'full' }: ScrapeParams): Promise<string | null> => {
    const currentGeneration = ++generation.current
    setState({ status: 'scraping', message: force ? 'オッズ更新中（強制再スクレイプ）...' : 'スクレイプ開始中...', jobId: null })

    try {
      if (horseDetails === 'cached_only') {
        const health = await authFetch('/api/scrape/health', { cache: 'no-store', signal: AbortSignal.timeout(10_000) })
        if (!health.ok || !supportsCachedCardAcquisition(await health.json())) {
          throw new Error('軽量一覧取得に対応するFastAPIを再起動してください。')
        }
      }
      if (currentGeneration !== generation.current) return null
      const res = await authFetch('/api/scrape', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ start_date: startDate, end_date: endDate, force_rescrape: force,
          ...(horseDetails === 'cached_only' ? { horse_details: 'cached_only' } : {}) }),
        signal: AbortSignal.timeout(180_000),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || `HTTP ${res.status}`)
      }
      const { job_id } = await res.json()
      setState(prev => ({ ...prev, message: horseDetails === 'cached_only'
        ? `ジョブ開始 (${job_id}) — レース一覧を軽量取得中（馬の履歴は追加収集しません）...`
        : `ジョブ開始 (${job_id}) — データ収集中...`, jobId: job_id }))
      return job_id as string
    } catch (e: any) {
      if (currentGeneration !== generation.current) return null
      setState({ status: 'error', message: e.message, jobId: null })
      return null
    }
  }, [])

  return { ...state, startScrape, setStatus, setMessage, reset }
}
