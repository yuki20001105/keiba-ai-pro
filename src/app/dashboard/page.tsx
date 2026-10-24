'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { Logo } from '@/components/Logo'
import { authFetch } from '@/lib/auth-fetch'
import { SelectedCalibrationDiagnostics } from '@/components/SelectedCalibrationDiagnostics'
import {
  EMPTY_PERFORMANCE_SUMMARY, performanceDateLabel, performanceStatusLabel,
  performanceRoiLabel, readSelectionDiagnostics,
  type PerformanceMode, type PerformanceRecord, type PerformanceSummary, type SelectionDiagnostics,
} from '@/lib/performance-records'

const yen = (amount: number) => `¥${amount.toLocaleString()}`
const profitLabel = (amount: number | null) => amount == null ? '—' : `${amount > 0 ? '+' : amount < 0 ? '−' : ''}${yen(Math.abs(amount))}`

function PerformanceRaceCard({ record }: { record: PerformanceRecord }) {
  const date = record.race_info.date || record.race_info.race_date
  const settled = record.status === 'settled'
  const label = performanceStatusLabel(record)
  return (
    <article className="rounded-lg border border-[#252525] bg-[#111] p-4 space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="text-xs text-[#888]">{performanceDateLabel(date)} · {record.race_info.venue || '開催未確認'}{record.race_info.race_no ? ` ${record.race_info.race_no}R` : ''}</div>
          <h2 className="mt-1 text-sm font-medium">{record.race_info.race_name || record.race_id}</h2>
        </div>
        <span className={`rounded px-2 py-1 text-xs ${settled && (record.actual_return ?? 0) > 0 ? 'bg-[#142b18] text-[#86efac]' : record.status === 'skipped' ? 'bg-[#222] text-[#aaa]' : 'bg-[#132032] text-[#93c5fd]'}`}>{label}</span>
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs">
        <span className="text-[#aaa]">投資 <span className="text-white">{yen(record.total_cost)}</span></span>
        <span className="text-[#aaa]">払戻 <span className="text-white">{settled && record.actual_return != null ? yen(record.actual_return) : '—'}</span></span>
        <span className="text-[#aaa]">収支 <span className={settled && (record.profit ?? 0) > 0 ? 'text-[#86efac]' : settled && (record.profit ?? 0) < 0 ? 'text-[#fca5a5]' : 'text-white'}>{settled ? profitLabel(record.profit) : '—'}</span></span>
      </div>
      {record.tickets.length > 0 ? (
        <div className="flex flex-wrap gap-2 text-xs">
          {record.tickets.map((ticket, index) => <span key={`${ticket.bet_type}-${ticket.combination}-${index}`} className="rounded border border-[#333] px-2 py-1 text-[#ddd]">{ticket.bet_type} {ticket.combination} · {yen(ticket.stake)}</span>)}
        </div>
      ) : <p className="text-xs text-[#888]">{record.reason || '買い目なし'}</p>}
      <details className="text-xs text-[#888]">
        <summary className="w-fit cursor-pointer hover:text-white">詳細</summary>
        <div className="mt-2 space-y-2 border-t border-[#252525] pt-2">
          {record.reason && <p>{record.reason}</p>}
          {record.settlement_reason && (record.status === 'review' || record.status === 'payout_pending') && <p>結果確認: {record.settlement_reason}</p>}
          <p>モデル: <span className="break-all">{record.model_id || '未確認'}</span></p>
          <p>選定: {record.policy_version || '未確認'} · {record.mode === 'actual' ? '購入済み' : 'AI参考'}</p>
          {record.tickets.map((ticket, index) => <p key={index}>{ticket.bet_type} {ticket.combination} / 払戻 {ticket.actual_return != null ? yen(ticket.actual_return) : '未確定'}</p>)}
          <Link href={`/race-analysis?date=${encodeURIComponent(date || '')}&race_id=${encodeURIComponent(record.race_id)}`} className="inline-block text-[#7dd3fc] hover:underline">現在の予測を見る →</Link>
        </div>
      </details>
    </article>
  )
}

type LegacyRecord = {
  id: string | number; race_id: string; purchase_date?: string | null; created_at?: string
  venue?: string; bet_type: string; combinations?: string[]; total_cost: number
  actual_return?: number | null; is_hit?: boolean
}

function LegacyPurchaseRecords() {
  const [open, setOpen] = useState(false)
  const [records, setRecords] = useState<LegacyRecord[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!open) return
    let active = true
    setLoading(true)
    setError('')
    authFetch('/api/purchase-history?limit=200', { signal: AbortSignal.timeout(15_000) })
      .then(async response => {
        const data = await response.json()
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '以前の記録を読み込めませんでした。')
        if (active) setRecords(Array.isArray(data.history) ? data.history : [])
      })
      .catch(error => { if (active) setError(error instanceof Error ? error.message : '読み込みに失敗しました。') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [open])
  return (
    <details onToggle={event => setOpen(event.currentTarget.open)} className="rounded-lg border border-[#252525] p-4 text-xs text-[#888]">
      <summary className="cursor-pointer hover:text-white">以前の購入記録</summary>
      <div className="mt-3 space-y-2">
        <p>新しい成績とは別集計です。以前の未確定／外れは区別できないため、ここでは判定しません。</p>
        {loading && <p>読み込み中...</p>}
        {error && <p role="alert" className="text-[#fca5a5]">{error}</p>}
        {!loading && !error && records.length === 0 && <p>記録がありません。</p>}
        {records.map(record => (
          <div key={record.id} className="flex flex-wrap justify-between gap-2 border-t border-[#252525] py-2">
            <span>記録日 {performanceDateLabel(record.purchase_date || record.created_at?.slice(0, 10))} · {record.venue || record.race_id} · {record.bet_type} {record.combinations?.join(' / ')}</span>
            <span>投資 {yen(record.total_cost)} · 払戻 {record.actual_return != null && record.actual_return > 0 ? yen(record.actual_return) : '未確認'}</span>
          </div>
        ))}
      </div>
    </details>
  )
}

export default function DashboardPage() {
  const [mode, setMode] = useState<PerformanceMode>('paper')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [records, setRecords] = useState<PerformanceRecord[]>([])
  const [summary, setSummary] = useState<PerformanceSummary>(EMPTY_PERFORMANCE_SUMMARY)
  const [selectionDiagnostics, setSelectionDiagnostics] = useState<SelectionDiagnostics | null>(null)
  const [totalCount, setTotalCount] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const requestGeneration = useRef(0)
  const invalidatePendingRequests = useCallback(() => { ++requestGeneration.current }, [])

  const loadPerformance = useCallback(async () => {
    const generation = ++requestGeneration.current
    setLoading(true)
    setError('')
    try {
      if (dateFrom && dateTo && dateFrom > dateTo) throw new Error('開始日は終了日より前にしてください。')
      const params = new URLSearchParams({ mode, limit: '100' })
      if (dateFrom) params.set('date_from', dateFrom.replace(/-/g, ''))
      if (dateTo) params.set('date_to', dateTo.replace(/-/g, ''))
      const res = await authFetch(`/api/performance?${params}`, { signal: AbortSignal.timeout(20_000) })
      const data = await res.json()
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '成績を読み込めませんでした。')
      if (generation !== requestGeneration.current) return
      setRecords(Array.isArray(data.records) ? data.records : [])
      setSummary({ ...EMPTY_PERFORMANCE_SUMMARY, ...data.summary })
      setSelectionDiagnostics(readSelectionDiagnostics(data.selection_diagnostics, mode, dateFrom, dateTo))
      setTotalCount(data.total_count ?? data.records?.length ?? 0)
    } catch (error) {
      if (generation === requestGeneration.current) {
        setError(error instanceof Error ? error.message : '読み込みに失敗しました。')
        setSelectionDiagnostics(null)
      }
    } finally { if (generation === requestGeneration.current) setLoading(false) }
  }, [mode, dateFrom, dateTo])

  useEffect(() => {
    setRecords([])
    setSummary(EMPTY_PERFORMANCE_SUMMARY)
    setSelectionDiagnostics(null)
    setTotalCount(0)
    void loadPerformance()
    return invalidatePendingRequests
  }, [loadPerformance, invalidatePendingRequests])

  useEffect(() => {
    if (summary.pending_races <= 0) return
    const timer = setInterval(() => { if (!document.hidden) void loadPerformance() }, 30_000)
    return () => clearInterval(timer)
  }, [summary.pending_races, loadPerformance])

  const cards = [
    { label: '投資', value: yen(summary.settled_cost) },
    { label: '払戻', value: yen(summary.settled_return) },
    { label: '収支', value: profitLabel(summary.settled_races ? summary.profit : null) },
    { label: '回収率', value: performanceRoiLabel(summary.roi) },
  ]
  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <header className="flex items-center justify-between border-b border-[#252525] px-6 py-4">
        <Logo href="/home" />
        <div className="flex gap-4 text-xs"><Link href="/home" className="text-[#888] hover:text-white">ホーム</Link><span>成績確認</span></div>
      </header>
      <main className="mx-auto max-w-4xl space-y-5 px-4 py-8">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div role="tablist" aria-label="成績の種類" className="flex rounded border border-[#333] p-1">
            {([{ key: 'paper', label: 'AI参考' }, { key: 'actual', label: '購入済み' }] as const).map(tab => <button key={tab.key} role="tab" aria-selected={mode === tab.key} onClick={() => setMode(tab.key)} className={`rounded px-4 py-2 text-sm ${mode === tab.key ? 'bg-white font-medium text-black' : 'text-[#aaa] hover:text-white'}`}>{tab.label}</button>)}
          </div>
          <button onClick={() => void loadPerformance()} disabled={loading} className="rounded border border-[#333] px-3 py-2 text-xs text-[#aaa] hover:text-white disabled:opacity-40">{loading ? '更新中...' : '更新'}</button>
        </div>
        <p className="text-xs text-[#888]">{mode === 'paper' ? 'AIが選んだ買い目のシミュレーションです。実購入とは別集計です。' : '購入済みとして記録した買い目の成績です。アプリから購入は行いません。'}</p>
        <div className="flex flex-wrap items-center gap-2 text-xs text-[#aaa]">
          <label htmlFor="performance-date-from">期間</label>
          <input id="performance-date-from" aria-label="開始日" type="date" value={dateFrom} onChange={event => setDateFrom(event.target.value)} className="rounded border border-[#333] bg-[#111] px-2 py-1.5 text-white" />
          <span>〜</span>
          <input aria-label="終了日" type="date" value={dateTo} onChange={event => setDateTo(event.target.value)} className="rounded border border-[#333] bg-[#111] px-2 py-1.5 text-white" />
          {(dateFrom || dateTo) && <button onClick={() => { setDateFrom(''); setDateTo('') }} className="px-2 py-1 text-[#888] hover:text-white">すべて</button>}
        </div>
        {error && <p role="alert" className="rounded border border-[#7f1d1d] bg-[#220b0b] p-3 text-sm text-[#fca5a5]">{error}</p>}
        <section aria-label="確定分の成績" className="space-y-2">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {cards.map(card => <div key={card.label} className="rounded-lg border border-[#252525] bg-[#111] p-4"><div className="mb-1 text-xs text-[#888]">{card.label}</div><div className="text-xl font-semibold tabular-nums">{card.value}</div></div>)}
          </div>
          <p className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-[#888]">確定 {summary.settled_races}レース · 的中 {summary.hit_races}
            {selectionDiagnostics ? <><span>結果待ち {selectionDiagnostics.status_counts.pending}レース</span><span>払戻待ち {selectionDiagnostics.status_counts.payout_pending}レース</span><span>要確認 {selectionDiagnostics.status_counts.review + selectionDiagnostics.status_counts.other}レース</span></> : <span>未確定 {summary.pending_races}レース（{yen(summary.pending_cost)}・内訳未取得）</span>}
            <span>見送り {summary.skipped_races}レース</span>
          </p>
          {summary.pending_races > 0 && <p className="text-xs text-[#777]">結果・払戻の取得後に反映</p>}
        </section>
        <SelectedCalibrationDiagnostics diagnostics={selectionDiagnostics} loading={loading} />
        <section aria-label="レース別の成績" className="space-y-3">
          {loading && records.length === 0 && <p role="status" className="py-8 text-center text-sm text-[#888]">読み込み中...</p>}
          {!loading && !error && records.length === 0 && <div className="rounded-lg border border-[#252525] bg-[#111] p-8 text-center"><p className="text-sm text-[#aaa]">まだ成績がありません</p><Link href="/predict-batch" className="mt-3 inline-block text-xs text-[#7dd3fc] hover:underline">予測実行へ →</Link></div>}
          {records.map(record => <PerformanceRaceCard key={record.id} record={record} />)}
          {totalCount > records.length && <p className="text-xs text-[#888]">最新 {records.length} / {totalCount}件を表示。期間を絞ると過去の記録を確認できます。</p>}
        </section>
        <LegacyPurchaseRecords />
        <nav aria-label="成績メニュー" className="flex flex-wrap justify-between gap-3 border-t border-[#252525] pt-4 text-xs"><Link href="/prediction-history" className="text-[#888] hover:text-white">予測履歴 →</Link><Link href="/predict-batch" className="text-[#7dd3fc] hover:underline">予測実行へ →</Link></nav>
      </main>
    </div>
  )
}
