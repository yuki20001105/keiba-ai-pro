import { useState, useEffect, useRef, useCallback } from 'react'
import { authFetch } from '@/lib/auth-fetch'
import type { JobStatus } from '@/lib/types'

export interface JobPollerOptions {
  /** ジョブIDが設定されたらポーリング開始。null の間は停止。 */
  jobId: string | null
  /** ジョブIDを受け取りステータスエンドポイント URL を返す関数 */
  getStatusUrl: (jobId: string) => string
  /** 成功時コールバック。statusData 全体を受け取る。 */
  onCompleted?: (statusData: any) => void
  /** エラー時コールバック */
  onError?: (message: string) => void
  /** 進捗更新時コールバック */
  onProgress?: (message: string, statusData: any) => void
  /** ポーリング間隔 (ms) — デフォルト 3000 */
  intervalMs?: number
  /** 任意の監視上限。未指定/null はサーバーの終端状態まで監視する。 */
  maxMs?: number | null
}

export interface JobPollerResult {
  status: JobStatus
  progress: string
  pct: number
  /** ポーリングを強制停止して idle にリセット */
  reset: () => void
}

/**
 * 汎用ジョブポーリングフック。
 * train / data-collection(profiling) / predict-batch の3箇所で重複していた
 * setInterval ベースのポーリングロジックを一元化する。
 *
 * 使用例:
 * ```ts
 * const { status, progress } = useJobPoller({
 *   jobId,
 *   getStatusUrl: id => `/api/ml/train/status/${id}`,
 *   onCompleted: data => { setResult(data.result); loadModels() },
 *   onError: msg => showToast(msg, 'error'),
 * })
 * ```
 */
export function useJobPoller({
  jobId,
  getStatusUrl,
  onCompleted,
  onError,
  onProgress,
  intervalMs = 3000,
  maxMs = null,
}: JobPollerOptions): JobPollerResult {
  const [status, setStatus] = useState<JobStatus>('idle')
  const [progress, setProgress] = useState('')
  const [pct, setPct] = useState(0)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const startRef = useRef(0)
  // stableな参照でコールバックを保持（re-render のたびに setInterval が再生成されないように）
  const onCompletedRef = useRef(onCompleted)
  const onErrorRef = useRef(onError)
  const onProgressRef = useRef(onProgress)
  const requestInFlightRef = useRef(false)
  useEffect(() => { onCompletedRef.current = onCompleted }, [onCompleted])
  useEffect(() => { onErrorRef.current = onError }, [onError])
  useEffect(() => { onProgressRef.current = onProgress }, [onProgress])

  const stop = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const reset = useCallback(() => {
    stop()
    setStatus('idle')
    setProgress('')
    setPct(0)
    requestInFlightRef.current = false
  }, [stop])

  useEffect(() => {
    if (!jobId) return
    setStatus('running')
    startRef.current = Date.now()

    timerRef.current = setInterval(async () => {
      // Durable jobs are monitored until the backend reports a terminal state.
      // A finite cap remains available only for explicitly bounded callers.
      if (typeof maxMs === 'number' && maxMs > 0 && Date.now() - startRef.current > maxMs) {
        stop()
        setStatus('error')
        setProgress('タイムアウト')
        onErrorRef.current?.('ジョブがタイムアウトしました')
        return
      }
      if (requestInFlightRef.current) return
      requestInFlightRef.current = true

      try {
        const res = await authFetch(getStatusUrl(jobId))
        if (!res.ok) return // 一時的エラーはスキップ
        const data = await res.json()

        const rawProgress = data?.progress
        const msg = typeof rawProgress === 'string'
          ? rawProgress
          : rawProgress && typeof rawProgress === 'object' && typeof rawProgress.message === 'string'
            ? rawProgress.message
            : typeof data?.message === 'string'
              ? data.message
              : ''
        if (msg) {
          setProgress(msg)
          onProgressRef.current?.(msg, data)
        }
        if (typeof data.pct === 'number') setPct(data.pct)

        if (data.status === 'completed') {
          stop()
          setStatus('completed')
          onCompletedRef.current?.(data)
        } else if (data.status === 'error') {
          stop()
          setStatus('error')
          setProgress(data.error || 'エラーが発生しました')
          onErrorRef.current?.(data.error || 'エラーが発生しました')
        } else if (data.status === 'not_found') {
          stop()
          setStatus('error')
          setProgress('ジョブが見つかりません')
          onErrorRef.current?.('ジョブが見つかりません（サーバーが再起動した可能性があります）')
        } else if (data.status === 'waiting_resources') {
          setStatus('waiting_resources')
        } else if (data.status === 'recovering') {
          setStatus('recovering')
        } else if (data.status === 'queued') {
          setStatus('queued')
        } else if (data.status === 'running') {
          setStatus('running')
        }
      } catch {
        // ネットワーク一時エラーは無視
      } finally {
        requestInFlightRef.current = false
      }
    }, intervalMs)

    return stop
  // jobId が変わったときだけ再起動（getStatusUrl は純関数のため依存不要）
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId])

  return { status, progress, pct, reset }
}
