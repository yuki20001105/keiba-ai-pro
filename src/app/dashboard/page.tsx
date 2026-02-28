'use client'

import { useState, useEffect } from 'react'
import { supabase } from '@/lib/supabase'
import Link from 'next/link'
import { Logo } from '@/components/Logo'

export default function DashboardPage() {
  const [loading, setLoading] = useState(true)
  const [dataStats, setDataStats] = useState({ totalRaces: 0, totalHorses: 0, totalModels: 0 })
  const [bets, setBets] = useState<any[]>([])

  useEffect(() => {
    const init = async () => {
      await Promise.all([loadStats(), loadBets()])
      setLoading(false)
    }
    init()
  }, [])

  const loadStats = async () => {
    const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
    try {
      const res = await fetch(`/api/data-stats?ultimate=true`)
      if (res.ok) {
        const d = await res.json()
        setDataStats({ totalRaces: d.total_races || 0, totalHorses: d.total_horses || 0, totalModels: d.total_models || 0 })
      }
    } catch {}
  }

  const loadBets = async () => {
    try {
      const { data } = await supabase
        .from('bets')
        .select('*')
        .order('created_at', { ascending: false })
        .limit(20)
      if (data) setBets(data)
    } catch {}
  }

  const totalBets = bets.length
  const wins = bets.filter(b => (b.profit_loss ?? 0) > 0).length
  const winRate = totalBets > 0 ? ((wins / totalBets) * 100).toFixed(1) : '-'
  const totalPL = bets.reduce((s, b) => s + (b.profit_loss ?? 0), 0)

  const STATS = [
    { label: 'レース数', value: dataStats.totalRaces.toLocaleString() },
    { label: '馬数',     value: dataStats.totalHorses.toLocaleString() },
    { label: 'モデル数', value: String(dataStats.totalModels) },
    { label: '購入回数', value: String(totalBets) },
  ]

  if (loading) {
    return (
      <div className="min-h-screen bg-[#0a0a0a] flex items-center justify-center">
        <div className="w-5 h-5 rounded-full border-2 border-white border-t-transparent animate-spin" />
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <header className="border-b border-[#1e1e1e] px-6 py-4 flex items-center justify-between">
        <Logo href="/home" />
        <div className="flex items-center gap-4">
          <Link href="/home" className="flex items-center gap-1 text-xs text-[#555] hover:text-white transition-colors">
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
            ホーム
          </Link>
          <span className="text-sm text-[#888]">ダッシュボード</span>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-10 space-y-8">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {STATS.map((s) => (
            <div key={s.label} className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5">
              <div className="text-xs text-[#666] mb-2">{s.label}</div>
              <div className="text-2xl font-bold">{s.value}</div>
            </div>
          ))}
        </div>

        {totalBets > 0 && (
          <div className="grid grid-cols-2 gap-3">
            <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5">
              <div className="text-xs text-[#666] mb-2">勝率</div>
              <div className="text-2xl font-bold">{winRate}%</div>
            </div>
            <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5">
              <div className="text-xs text-[#666] mb-2">損益合計</div>
              <div className={`text-2xl font-bold ${totalPL >= 0 ? 'text-[#4ade80]' : 'text-[#f87171]'}`}>
                {totalPL >= 0 ? '+' : ''}{totalPL.toLocaleString()}
              </div>
            </div>
          </div>
        )}

        <div>
          <h2 className="text-sm font-medium text-[#888] mb-3">購入履歴</h2>
          <div className="bg-[#111] border border-[#1e1e1e] rounded-lg overflow-hidden">
            {bets.length === 0 ? (
              <div className="p-8 text-center text-[#555] text-sm">購入履歴がありません</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-[#1e1e1e]">
                      {['日時', 'レースID', '馬番', '券種', '金額', '損益'].map(h => (
                        <th key={h} className="px-4 py-3 text-left text-xs text-[#555] font-normal first:pl-5 last:text-right last:pr-5">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {bets.map((bet, i) => (
                      <tr key={i} className="border-b border-[#1a1a1a] hover:bg-[#161616] transition-colors">
                        <td className="px-4 py-3 pl-5 text-[#888]">{new Date(bet.created_at).toLocaleDateString('ja-JP')}</td>
                        <td className="px-4 py-3 text-[#888]">{bet.race_id}</td>
                        <td className="px-4 py-3 font-medium">{bet.horse_no}</td>
                        <td className="px-4 py-3 text-[#888]">{bet.bet_type}</td>
                        <td className="px-4 py-3 text-[#888]">{bet.amount?.toLocaleString()}</td>
                        <td className={`px-4 py-3 pr-5 text-right font-medium ${(bet.profit_loss ?? 0) >= 0 ? 'text-[#4ade80]' : 'text-[#f87171]'}`}>
                          {(bet.profit_loss ?? 0) >= 0 ? '+' : ''}{(bet.profit_loss ?? 0).toLocaleString()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>

        <div className="p-5 bg-[#111] border border-[#1e1e1e] rounded-lg flex items-center justify-between gap-4">
          <div>
            <div className="text-xs text-[#666] mb-0.5">新しいデータを追加する</div>
            <div className="text-sm font-medium">データ取得</div>
            <div className="text-xs text-[#555] mt-0.5">最新のレース情報を収集してモデルを更新します</div>
          </div>
          <Link
            href="/data-collection"
            className="shrink-0 flex items-center gap-1.5 bg-white text-black text-sm font-medium px-5 py-2.5 rounded hover:bg-[#eee] transition-colors"
          >
            データ取得へ
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </Link>
        </div>
      </main>
    </div>
  )
}
