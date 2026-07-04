'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { useAuth } from '@/contexts/AuthContext'
import { Logo } from '@/components/Logo'
import { supabase } from '@/lib/supabase'

type Dot = 'active' | 'partial' | 'backend'
interface Card {
  num: string
  color: string
  icon: string
  title: string
  tagline: string
  href: string
  cta: string
  sub?: { href: string; label: string }[]
  dot: Dot
}

const CARDS: Card[] = [
  {
    num: '01', color: '#3b82f6',
    icon: 'M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4',
    title: 'データ取得', tagline: 'netkeiba.com から 2013〜 を自動収集',
    href: '/data-collection', cta: '収集を開始',
    sub: [{ href: '/data-view', label: 'データ確認' }], dot: 'active',
  },
  {
    num: '02', color: '#22c55e',
    icon: 'M3 4a1 1 0 011-1h16a1 1 0 011 1v2a1 1 0 01-.293.707L13 13.414V19a1 1 0 01-.553.894l-4 2A1 1 0 017 21v-7.586L3.293 6.707A1 1 0 013 6V4z',
    title: '特徴量作成', tagline: '180+ 特徴量を時系列リーク防止で生成',
    href: '/feature-lab', cta: '特徴量ラボ',
    dot: 'active',
  },
  {
    num: '03', color: '#a855f7',
    icon: 'M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17H3a2 2 0 01-2-2V5a2 2 0 012-2h14a2 2 0 012 2v10a2 2 0 01-2 2h-2',
    title: 'モデル学習', tagline: 'LightGBM + Optuna で精度を最大化',
    href: '/train', cta: '学習を開始',
    dot: 'active',
  },
  {
    num: '04', color: '#ef4444',
    icon: 'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z',
    title: 'シミュレーション', tagline: '過去データで回収率・的中率を検証',
    href: '/prediction-history', cta: '予測履歴',
    sub: [{ href: '/dashboard', label: '損益確認' }], dot: 'partial',
  },
  {
    num: '05', color: '#f59e0b',
    icon: 'M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
    title: 'オッズ取得', tagline: '単勝〜三連単をレース直前にリアルタイム取得',
    href: '/race-analysis', cta: 'レース分析',
    dot: 'active',
  },
  {
    num: '06', color: '#06b6d4',
    icon: 'M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18m0 0h10a2 2 0 002-2V9M9 21H5a2 2 0 01-2-2V9m0 0h18',
    title: '予測・自動投票', tagline: 'Kelly 基準で期待値プラスの馬券を自動推奨',
    href: '/predict-batch', cta: 'バッチ予測',
    sub: [{ href: '/race-analysis', label: 'レース分析' }, { href: '/win5', label: 'WIN5' }], dot: 'active',
  },
  {
    num: '07', color: '#6366f1',
    icon: 'M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2m-2-4h.01M17 16h.01',
    title: 'サーバー自動運用', tagline: 'スケジューラで 24h 無人稼働',
    href: '/admin', cta: '管理者パネル',
    dot: 'backend',
  },
  {
    num: '08', color: '#64748b',
    icon: 'M9 12l2 2 4-4M7.835 4.697a3.42 3.42 0 001.946-.806 3.42 3.42 0 014.438 0 3.42 3.42 0 001.946.806 3.42 3.42 0 013.138 3.138 3.42 3.42 0 00.806 1.946 3.42 3.42 0 010 4.438 3.42 3.42 0 00-.806 1.946 3.42 3.42 0 01-3.138 3.138 3.42 3.42 0 00-1.946.806 3.42 3.42 0 01-4.438 0 3.42 3.42 0 00-1.946-.806 3.42 3.42 0 01-3.138-3.138 3.42 3.42 0 00-.806-1.946 3.42 3.42 0 010-4.438 3.42 3.42 0 00.806-1.946 3.42 3.42 0 013.138-3.138z',
    title: 'テスト・品質管理', tagline: 'E2E・特徴量カバレッジ・ドリフト検出',
    href: '/feature-lab', cta: 'カバレッジ確認',
    sub: [{ href: '/data-view', label: 'データ確認' }], dot: 'partial',
  },
]

const DOT: Record<Dot, string> = { active: '#4ade80', partial: '#fbbf24', backend: '#60a5fa' }

export default function HomePage() {
  const { isAdmin } = useAuth()
  const [status, setStatus] = useState<{
    apiOnline: boolean | null
    totalRaces: number | null
    totalModels: number | null
  }>({ apiOnline: null, totalRaces: null, totalModels: null })

  useEffect(() => {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 5000)

    supabase.auth.getSession().then(({ data: { session } }) => {
      const authHeaders: Record<string, string> = session?.access_token
        ? { Authorization: `Bearer ${session.access_token}` }
        : {}

      Promise.all([
        fetch('/api/health', { signal: controller.signal }).then(r => r.ok).catch(() => false),
        fetch('/api/data-stats', { headers: authHeaders, signal: controller.signal }).then(r => r.ok ? r.json() : null).catch(() => null),
      ]).then(([online, stats]) => {
        clearTimeout(timeout)
        setStatus({
          apiOnline: online as boolean,
          totalRaces: stats?.total_races ?? null,
          totalModels: stats?.total_models ?? null,
        })
      })
    })

    return () => { clearTimeout(timeout); controller.abort() }
  }, [])

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <header className="border-b border-[#161616] px-6 py-4 flex items-center justify-between">
        <Logo href="/home" />
        <div className="flex items-center gap-4">
          {isAdmin && (
            <Link href="/admin" className="text-xs text-[#555] hover:text-white transition-colors">管理者</Link>
          )}
          <button
            onClick={async () => { await supabase.auth.signOut(); window.location.href = '/login' }}
            className="text-xs text-[#555] hover:text-white transition-colors"
          >
            ログアウト
          </button>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-6 py-10">

        {/* Hero + status */}
        <div className="flex items-end justify-between mb-8">
          <div>
            <h1 className="text-lg font-semibold text-white">完成した競馬AI</h1>
            <p className="text-xs text-[#444] mt-0.5">データ収集 → 特徴量 → 学習 → 予測 → 自動投票</p>
          </div>
          <div className="flex items-center gap-2 text-[11px]">
            <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-[#111] border border-[#1a1a1a]">
              {status.apiOnline === null
                ? <><span className="w-1.5 h-1.5 rounded-full bg-[#333]" /><span className="text-[#444]">確認中</span></>
                : status.apiOnline
                  ? <><span className="w-1.5 h-1.5 rounded-full bg-green-400" /><span className="text-[#777]">API オン</span></>
                  : <><span className="w-1.5 h-1.5 rounded-full bg-red-500" /><span className="text-[#777]">API オフ</span></>
              }
            </span>
            <span className="px-2.5 py-1 rounded-full bg-[#111] border border-[#1a1a1a] text-[#666]">
              {status.totalRaces === null ? '…' : status.totalRaces.toLocaleString()} レース
            </span>
            <span className="px-2.5 py-1 rounded-full bg-[#111] border border-[#1a1a1a] text-[#666]">
              {status.totalModels === null ? '…' : status.totalModels} モデル
            </span>
          </div>
        </div>

        {/* 8-card grid */}
        <div className="grid grid-cols-2 gap-3">
          {CARDS.map(card => (
            <div
              key={card.num}
              className="group bg-[#111] rounded-xl overflow-hidden border border-[#1a1a1a] hover:border-[#252525] hover:bg-[#121212] transition-all duration-200 flex flex-col"
            >
              {/* Colored top stripe */}
              <div className="h-px" style={{ background: `linear-gradient(90deg, ${card.color}88 0%, transparent 100%)` }} />

              <div className="p-4 flex flex-col gap-3 flex-1">
                {/* Icon + number + status dot */}
                <div className="flex items-center justify-between">
                  <div
                    className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0"
                    style={{ background: card.color + '18' }}
                  >
                    <svg width="15" height="15" fill="none" viewBox="0 0 24 24"
                      stroke={card.color} strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round"
                    >
                      <path d={card.icon} />
                    </svg>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="w-1.5 h-1.5 rounded-full" style={{ background: DOT[card.dot] }} />
                    <span className="text-[10px] font-mono text-[#2a2a2a]">{card.num}</span>
                  </div>
                </div>

                {/* Title + tagline */}
                <div className="flex-1">
                  <div className="text-sm font-semibold text-[#e8e8e8]">{card.title}</div>
                  <div className="text-[11px] text-[#4a4a4a] mt-0.5 leading-relaxed">{card.tagline}</div>
                </div>

                {/* Links */}
                <div className="flex items-center gap-3 flex-wrap pt-1 border-t border-[#161616]">
                  <Link
                    href={card.href}
                    className="flex items-center gap-0.5 text-[11px] font-medium transition-colors"
                    style={{ color: card.color + 'cc' }}
                  >
                    {card.cta}
                    <svg width="9" height="9" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round">
                      <path d="M9 5l7 7-7 7" />
                    </svg>
                  </Link>
                  {card.sub?.map(s => (
                    <Link key={s.href} href={s.href} className="text-[11px] text-[#333] hover:text-[#666] transition-colors">
                      {s.label}
                    </Link>
                  ))}
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Legend */}
        <div className="flex items-center gap-4 mt-6 text-[10px] text-[#333]">
          <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-[#4ade80]" />実装済み</span>
          <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-[#fbbf24]" />基本実装</span>
          <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-[#60a5fa]" />バックエンドのみ</span>
        </div>

      </main>
    </div>
  )
}
