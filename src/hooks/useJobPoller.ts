import { useState, useEffect, useRef, useCallback } from 'react'
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
  /** ポーリング間隔 (ms) — デフォルト 3000 */
  intervalMs?: number
  /** タイムアウト上限 (ms) — デフォルト 10分 */
  maxMs?: number
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
  intervalMs = 3000,
  maxMs = 10 * 60 * 1000,
}: JobPollerOptions): JobPollerResult {
  const [status, setStatus] = useState<JobStatus>('idle')
  const [progress, setProgress] = useState('')
  const [pct, setPct] = useState(0)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const startRef = useRef(0)
  // stableな参照でコールバックを保持（re-render のたびに setInterval が再生成されないように）
  const onCompletedRef = useRef(onCompleted)
  const onErrorRef = useRef(onError)
  useEffect(() => { onCompletedRef.current = onCompleted }, [onCompleted])
  useEffect(() => { onErrorRef.current = onError }, [onError])

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
  }, [stop])

  useEffect(() => {
    if (!jobId) return
    setStatus('running')
    startRef.current = Date.now()

    timerRef.current = setInterval(async () => {
      // タイムアウト判定
      if (Date.now() - startRef.current > maxMs) {
        stop()
        setStatus('error')
        setProgress('タイムアウト')
        onErrorRef.current?.('ジョブがタイムアウトしました')
        return
      }

      try {
        const res = await fetch(getStatusUrl(jobId))
        if (!res.ok) return // 一時的エラーはスキップ
        const data = await res.json()

        const msg: string = data.progress || data.message || ''
        if (msg) setProgress(msg)
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
        }
      } catch {
        // ネットワーク一時エラーは無視
      }
    }, intervalMs)

    return stop
  // jobId が変わったときだけ再起動（getStatusUrl は純関数のため依存不要）
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId])

  return { status, progress, pct, reset }
}
