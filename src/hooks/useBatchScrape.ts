'use client'
import { useState, useRef, useCallback } from 'react'

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

/**
 * 期間指定バッチスクレイピングフック。
 * 月単位でジョブを順次投入し、各ジョブが完了するまで 3 秒間隔でポーリングする。
 * `start()` は完了時に BatchResult を返し、エラー時はスローする。
 */
export function useBatchScrape() {
  const [loading, setLoading] = useState(false)
  const [progress, setProgress] = useState<BatchProgress>({ current: 0, total: 100, message: '', eta: '' })
  const [result, setResult] = useState<BatchResult | null>(null)
  const abortRef = useRef(false)
  const startTimeRef = useRef(0)

  const start = useCallback(async (
    startPeriod: string,
    endPeriod: string,
    forceRescrape: boolean,
  ): Promise<BatchResult> => {
    const [startYearStr, startMonthStr] = startPeriod.split('-')
    const [endYearStr, endMonthStr] = endPeriod.split('-')
    const startYear = parseInt(startYearStr, 10)
    const startMonth = parseInt(startMonthStr, 10)
    const endYear = parseInt(endYearStr, 10)
    const endMonth = parseInt(endMonthStr, 10)

    // 取得対象の月リストを生成
    const months: { year: number; month: number }[] = []
    let y = startYear, m = startMonth
    while (y < endYear || (y === endYear && m <= endMonth)) {
      months.push({ year: y, month: m })
      m++
      if (m > 12) { m = 1; y++ }
    }
    const totalMonths = months.length

    setLoading(true)
    setResult(null)
    abortRef.current = false
    startTimeRef.current = Date.now()
    let totalRaces = 0
    let completedMonths = 0

    try {
      for (const { year, month } of months) {
        if (abortRef.current) break

        const pad = (n: number) => String(n).padStart(2, '0')
        const startDateStr = `${year}${pad(month)}01`
        const lastDay = new Date(year, month, 0).getDate()
        const endDateStr = `${year}${pad(month)}${pad(lastDay)}`

        setProgress({
          current: Math.round((completedMonths / totalMonths) * 95),
          total: 100,
          message: `${year}年${month}月を取得中… (${completedMonths + 1}/${totalMonths}ヶ月)`,
          eta: '',
        })

        const startRes = await fetch('/api/scrape', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ start_date: startDateStr, end_date: endDateStr, force_rescrape: forceRescrape }),
        })
        if (!startRes.ok) {
          const err = await startRes.json()
          throw new Error(err.detail || `HTTP ${startRes.status}`)
        }
        const { job_id } = await startRes.json()

        // ジョブ完了までポーリング（3秒間隔）
        let done = false
        let failCount = 0
        const MAX_FAIL = 10
        while (!done && !abortRef.current) {
          await new Promise(resolve => setTimeout(resolve, 3000))
          const statusRes = await fetch(`/api/scrape/status/${job_id}`)
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
          const monthPct = prog.total > 0 ? prog.done / prog.total : 0
          const overallPct = Math.round(((completedMonths + monthPct) / totalMonths) * 95)
          let eta = ''
          if (completedMonths > 0) {
            const elapsed = Date.now() - startTimeRef.current
            const msPerMonth = elapsed / completedMonths
            const remainingSec = Math.round(msPerMonth * (totalMonths - completedMonths) / 1000)
            eta = remainingSec >= 60 ? `残り約${Math.ceil(remainingSec / 60)}分` : `残り約${remainingSec}秒`
          }
          setProgress({
            current: overallPct,
            total: 100,
            message: `${year}年${month}月 (${completedMonths + 1}/${totalMonths}ヶ月): ${prog.message || '処理中...'}`,
            eta,
          })

          if (status.status === 'completed') {
            done = true
            totalRaces += status.result?.races_collected || 0
            completedMonths++
          } else if (status.status === 'error') {
            throw new Error(status.error || `${year}年${month}月のスクレイピングが失敗しました`)
          }
        }
      }

      const elapsed = Math.round((Date.now() - startTimeRef.current) / 1000)
      setProgress({ current: 100, total: 100, message: `完了: ${totalRaces}レース取得`, eta: '' })
      const batchResult: BatchResult = {
        races_collected: totalRaces,
        elapsed_time: elapsed,
        stats: { period: `${startYear}年${startMonth}月〜${endYear}年${endMonth}月`, total_months: totalMonths },
      }
      setResult(batchResult)
      return batchResult
    } catch (error: unknown) {
      setProgress({ current: 0, total: 100, message: 'エラーが発生しました', eta: '' })
      throw error
    } finally {
      setLoading(false)
    }
  }, [])

  const abort = useCallback(() => { abortRef.current = true }, [])

  return { loading, progress, result, start, abort }
}
