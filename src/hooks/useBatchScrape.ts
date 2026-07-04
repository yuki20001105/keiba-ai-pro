'use client'
import { useState, useRef, useCallback } from 'react'
import { authFetch } from '@/lib/auth-fetch'

export type BatchProgress = {
  current: number
  total: number
  message: string
  eta: string
  savedRaces: number
  savedHorses: number
}

export type BatchResult = {
  races_collected: number
  elapsed_time: number
  stats: { period: string; total_months: number }
}

/**
 * 期間指定バッチスクレイピングフック。
 * 指定期間全体を 1 ジョブでバックエンドに投入し、完了まで 3 秒間隔でポーリングする。
 * バックエンド側がカレンダー絞り込み・再開ロジックを担当するため、
 * フロントは「開始 → ポーリング → 完了」のみを担当する。
 */
export function useBatchScrape() {
  const [loading, setLoading] = useState(false)
  const [progress, setProgress] = useState<BatchProgress>({
    current: 0, total: 100, message: '', eta: '', savedRaces: 0, savedHorses: 0,
  })
  const [result, setResult] = useState<BatchResult | null>(null)
  const abortRef = useRef(false)
  const jobIdRef = useRef<string | null>(null)
  const startTimeRef = useRef(0)
  // ETA: スライディングウィンドウ (done件数, 時刻) を保持して直近速度を使う
  const pollWindowRef = useRef<{ time: number; done: number }[]>([])

  const start = useCallback(async (
    startPeriod: string,  // 'YYYY-MM'
    endPeriod: string,    // 'YYYY-MM'
    forceRescrape: boolean,
  ): Promise<BatchResult> => {
    const pad = (n: number) => String(n).padStart(2, '0')
    const [sy, sm] = startPeriod.split('-').map(Number)
    const [ey, em] = endPeriod.split('-').map(Number)
    const startDate = `${sy}${pad(sm)}01`
    const lastDay = new Date(ey, em, 0).getDate()
    const endDate = `${ey}${pad(em)}${pad(lastDay)}`
    const totalMonths = (ey - sy) * 12 + (em - sm) + 1

    setLoading(true)
    setResult(null)
    abortRef.current = false
    jobIdRef.current = null
    pollWindowRef.current = []
    startTimeRef.current = Date.now()

    try {
      setProgress({ current: 3, total: 100, message: `${sy}年${sm}月〜${ey}年${em}月を取得開始...`, eta: '', savedRaces: 0, savedHorses: 0 })

      const startRes = await authFetch('/api/scrape', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ start_date: startDate, end_date: endDate, force_rescrape: forceRescrape }),
      })
      if (!startRes.ok) {
        const err = await startRes.json()
        throw new Error(err.detail || `HTTP ${startRes.status}`)
      }
      const { job_id } = await startRes.json()
      jobIdRef.current = job_id

      // ジョブ完了までポーリング（3秒間隔）
      let done = false
      let failCount = 0
      const MAX_FAIL = 10
      let lastResult: any = null
      while (!done && !abortRef.current) {
        await new Promise(resolve => setTimeout(resolve, 3000))
        const statusRes = await authFetch(`/api/scrape/status/${job_id}`)
        if (!statusRes.ok) {
          if (++failCount >= MAX_FAIL) throw new Error(`ステータス取得失敗 (job_id: ${job_id})`)
          continue
        }
        failCount = 0
        const status = await statusRes.json()
        if (status.status === 'not_found') {
          if (++failCount >= MAX_FAIL) throw new Error(`ジョブが見つかりません (job_id: ${job_id})`)
          continue
        }
        failCount = 0

        const prog = status.progress || {}
        const pct = prog.total > 0 ? Math.min(95, Math.round((prog.done / prog.total) * 90) + 5) : 10
        const savedRaces  = prog.saved_races  ?? 0
        const savedHorses = prog.saved_horses ?? 0

        // ETA: 直近5ポーリング分の移動平均で計算（スキップ日の歪みを軽減）
        let eta = ''
        if (typeof prog.done === 'number' && prog.done > 0) {
          const win = pollWindowRef.current
          win.push({ time: Date.now(), done: prog.done })
          if (win.length > 5) win.shift()
          if (win.length >= 2 && prog.total > prog.done) {
            const span = win[win.length - 1].done - win[0].done
            if (span > 0) {
              const msPerDay = (win[win.length - 1].time - win[0].time) / span
              const remainingSec = Math.round(msPerDay * (prog.total - prog.done) / 1000)
              if (remainingSec > 0 && remainingSec < 86400) {
                eta = remainingSec >= 60
                  ? `残り約${Math.ceil(remainingSec / 60)}分`
                  : `残り約${remainingSec}秒`
              }
            }
          }
        }

        setProgress({ current: pct, total: 100, message: prog.message || '処理中...', eta, savedRaces, savedHorses })

        if (status.status === 'completed') {
          done = true
          lastResult = status.result
        } else if (status.status === 'error') {
          throw new Error(status.error || 'スクレイピングが失敗しました')
        } else if (status.status === 'blocked') {
          throw new Error('IPブロックを検知しました。VPNのIP変更後に再実行してください。')
        }
      }

      const elapsed = Math.round((Date.now() - startTimeRef.current) / 1000)
      const batchResult: BatchResult = {
        races_collected: lastResult?.races_collected || 0,
        elapsed_time: elapsed,
        stats: { period: `${sy}年${sm}月〜${ey}年${em}月`, total_months: totalMonths },
      }
      setProgress({ current: 100, total: 100, message: `完了: ${batchResult.races_collected}レース取得`, eta: '', savedRaces: 0, savedHorses: 0 })
      setResult(batchResult)
      return batchResult
    } catch (error: unknown) {
      setProgress({ current: 0, total: 100, message: 'エラーが発生しました', eta: '', savedRaces: 0, savedHorses: 0 })
      throw error
    } finally {
      setLoading(false)
    }
  }, [])

  /** UIキャンセル: フロントループを止め、バックエンドにもキャンセル要求を送る */
  const cancel = useCallback(async () => {
    abortRef.current = true
    const jid = jobIdRef.current
    if (!jid) return
    try {
      await authFetch(`/api/scrape/cancel/${jid}`, { method: 'POST' })
    } catch {
      // キャンセルAPIエラーは無視（フロントは既に停止済み）
    }
  }, [])

  return { loading, progress, result, start, cancel }
}
