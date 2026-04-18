'use client'

import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { Logo } from '@/components/Logo'
import { supabase } from '@/lib/supabase'

// ── 型定義 ────────────────────────────────────────────────────────────
type PredictionEntry = {
  horse_id: string
  horse_name: string
  horse_number: number
  predicted_rank: number
  win_probability: number
  p_raw: number
  odds: number | null
  actual_finish: number | null
  finish_time: string | null
  actual_odds: number | null
}

type RaceHistory = {
  race_id: string
  race_name: string
  venue: string
  race_date: string
  model_id: string
  predicted_at: string
  predictions: PredictionEntry[]
}

type Stats = {
  total_races: number
  decided_races?: number
  top1_win_rate?: number
  top1_place3_rate?: number
}

// ── ユーティリティ ───────────────────────────────────────────────────
function formatDate(d: string) {
  if (!d || d.length < 8) return d
  return `${d.slice(0, 4)}/${d.slice(4, 6)}/${d.slice(6, 8)}`
}

function finishLabel(n: number | null) {
  if (n === null) return '—'
  if (n === 1) return '1着'
  if (n === 2) return '2着'
  if (n === 3) return '3着'
  return `${n}着`
}

function HitBadge({ predicted, actual }: { predicted: number; actual: number | null }) {
  if (actual === null) return <span className="text-[#555] text-xs">結果待ち</span>
  if (predicted === 1 && actual === 1)
    return <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-500/20 text-yellow-400 font-medium">WIN</span>
  if (predicted === 1 && actual <= 3)
    return <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-500/20 text-green-400 font-medium">複勝</span>
  if (predicted <= 3 && actual <= 3)
    return <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-400 font-medium">圏内</span>
  return null
}

// ── 子コンポーネント: レースカード ───────────────────────────────────
function RaceCard({ race }: { race: RaceHistory }) {
  const top5 = race.predictions.filter(p => p.predicted_rank <= 5)
    .sort((a, b) => a.predicted_rank - b.predicted_rank)

  const decided = top5.some(p => p.actual_finish !== null)
  const top1 = top5.find(p => p.predicted_rank === 1)
  const top1Win = top1?.actual_finish === 1
  const top1Place = top1 && (top1.actual_finish ?? 99) <= 3

  return (
    <div className={`bg-[#111] border rounded-lg overflow-hidden mb-3 ${
      top1Win ? 'border-yellow-500/50' : top1Place ? 'border-green-500/30' : 'border-[#1e1e1e]'
    }`}>
      {/* ヘッダー */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-[#1e1e1e]">
        <div className="flex items-center gap-2">
          <span className="text-xs text-[#555]">{formatDate(race.race_date)}</span>
          <span className="text-xs text-[#555]">·</span>
          <span className="text-xs text-[#666]">{race.venue}</span>
          {top1Win && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-500/20 text-yellow-400 font-bold">
              予測1位 WIN ✓
            </span>
          )}
          {!top1Win && top1Place && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-500/20 text-green-400 font-medium">
              予測1位 複勝圏 ✓
            </span>
          )}
          {decided && !top1Place && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#1e1e1e] text-[#555]">外れ</span>
          )}
        </div>
        <Link href={`/race-analysis?race_id=${race.race_id}`}
          className="text-[10px] text-[#555] hover:text-[#888] transition-colors">
          詳細 →
        </Link>
      </div>

      {/* レース名 */}
      <div className="px-4 pt-2 pb-1">
        <span className="text-sm font-medium">{race.race_name || race.race_id}</span>
      </div>

      {/* 予測一覧 */}
      <div className="px-4 pb-3">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-[#444]">
              <th className="text-left py-1 font-normal w-6">予</th>
              <th className="text-left py-1 font-normal">馬名</th>
              <th className="text-right py-1 font-normal">確率</th>
              <th className="text-right py-1 font-normal">オッズ</th>
              <th className="text-right py-1 font-normal">実際</th>
              <th className="text-right py-1 font-normal w-16"></th>
            </tr>
          </thead>
          <tbody>
            {top5.map(p => (
              <tr key={p.horse_id || p.horse_number}
                className={`border-t border-[#1a1a1a] ${p.predicted_rank === 1 ? 'text-white' : 'text-[#888]'}`}>
                <td className="py-1 text-[#555]">{p.predicted_rank}</td>
                <td className="py-1">
                  <span className="text-[#999] mr-1">{p.horse_number}.</span>
                  {p.horse_name}
                </td>
                <td className="py-1 text-right text-[#4a9eff]">
                  {p.win_probability != null ? `${(p.win_probability * 100).toFixed(1)}%` : '—'}
                </td>
                <td className="py-1 text-right text-[#888]">
                  {p.odds != null ? `${p.odds.toFixed(1)}倍` : '—'}
                </td>
                <td className={`py-1 text-right font-medium ${
                  p.actual_finish === 1 ? 'text-yellow-400' :
                  (p.actual_finish ?? 99) <= 3 ? 'text-green-400' : 'text-[#666]'
                }`}>
                  {finishLabel(p.actual_finish)}
                </td>
                <td className="py-1 text-right">
                  <HitBadge predicted={p.predicted_rank} actual={p.actual_finish} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── メインページ ─────────────────────────────────────────────────────
export default function PredictionHistoryPage() {
  const [races, setRaces] = useState<RaceHistory[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const loadHistory = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const { data: { session } } = await supabase.auth.getSession()
      const authHeader = session?.access_token ? `Bearer ${session.access_token}` : ''

      const res = await fetch('/api/prediction-history?limit=200', {
        headers: { ...(authHeader ? { Authorization: authHeader } : {}) },
        signal: AbortSignal.timeout(30_000),
      })
      if (!res.ok) {
        const e = await res.json()
        throw new Error(e.detail || `HTTP ${res.status}`)
      }
      const json = await res.json()
      setRaces(json.races ?? [])
      setStats(json.stats ?? null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'エラーが発生しました')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadHistory() }, [loadHistory])

  const decidedRaces = races.filter(r => r.predictions.some(p => p.actual_finish !== null))
  const pendingRaces = races.filter(r => r.predictions.every(p => p.actual_finish === null))

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      {/* ヘッダー */}
      <header className="flex items-center justify-between px-6 py-4 border-b border-[#1e1e1e]">
        <div className="flex items-center gap-4">
          <Logo />
          <h1 className="text-base font-semibold text-[#ccc]">予測履歴</h1>
        </div>
        <button onClick={loadHistory} disabled={loading}
          className="text-xs px-3 py-1.5 rounded border border-[#333] text-[#888] hover:text-white hover:border-[#555] transition-colors disabled:opacity-40">
          {loading ? '読み込み中...' : '更新'}
        </button>
      </header>

      <div className="max-w-3xl mx-auto px-4 py-6">
        {/* エラー */}
        {error && (
          <div className="mb-4 text-sm text-red-400 bg-red-900/20 border border-red-900/40 rounded px-4 py-3">
            {error}
          </div>
        )}

        {/* サマリー統計 */}
        {stats && stats.total_races > 0 && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
            {[
              { label: '予測済みレース', value: `${stats.total_races}レース` },
              { label: '結果確定', value: stats.decided_races != null ? `${stats.decided_races}レース` : '—' },
              { label: '予測1位 単勝的中率', value: stats.top1_win_rate != null ? `${stats.top1_win_rate}%` : '—' },
              { label: '予測1位 複勝的中率', value: stats.top1_place3_rate != null ? `${stats.top1_place3_rate}%` : '—' },
            ].map(s => (
              <div key={s.label} className="bg-[#111] border border-[#1e1e1e] rounded-lg px-4 py-3">
                <div className="text-[10px] text-[#555] mb-1">{s.label}</div>
                <div className="text-lg font-semibold">{s.value}</div>
              </div>
            ))}
          </div>
        )}

        {/* ロード中 */}
        {loading && (
          <div className="text-center text-[#555] py-16 text-sm">読み込み中...</div>
        )}

        {/* データなし */}
        {!loading && races.length === 0 && !error && (
          <div className="text-center text-[#555] py-16">
            <p className="text-sm mb-2">予測履歴がありません</p>
            <p className="text-xs text-[#444]">
              <Link href="/race-analysis" className="text-[#4a9eff] hover:underline">予測スコア詳細</Link>
              {' '}でレースを分析すると自動的に保存されます
            </p>
          </div>
        )}

        {/* 結果確定済みレース */}
        {decidedRaces.length > 0 && (
          <section className="mb-8">
            <h2 className="text-xs font-semibold text-[#555] uppercase tracking-wider mb-3">
              結果確定 ({decidedRaces.length}レース)
            </h2>
            {decidedRaces.map(r => <RaceCard key={r.race_id} race={r} />)}
          </section>
        )}

        {/* 結果待ちレース */}
        {pendingRaces.length > 0 && (
          <section>
            <h2 className="text-xs font-semibold text-[#555] uppercase tracking-wider mb-3">
              結果待ち ({pendingRaces.length}レース)
            </h2>
            {pendingRaces.map(r => <RaceCard key={r.race_id} race={r} />)}
          </section>
        )}
      </div>
    </div>
  )
}
