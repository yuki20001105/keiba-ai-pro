'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { useAuth } from '@/contexts/AuthContext'
import { Logo } from '@/components/Logo'
import { supabase } from '@/lib/supabase'
import { authFetch } from '@/lib/auth-fetch'

const FLOW_STEPS = [
  { href: '/predict-batch', label: '予測実行', desc: 'レースを選び、予測・購入推奨を確認', step: '01' },
  { href: '/dashboard', label: '成績確認', desc: '購入履歴と損益・回収率を分析', step: '02' },
]

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

    const loadStatus = async () => {
      Promise.all([
        fetch('/api/health', { signal: controller.signal }).then(r => r.ok).catch(() => false),
        authFetch('/api/data-stats', { signal: controller.signal }).then(r => r.ok ? r.json() : null).catch(() => null),
      ]).then(([online, stats]) => {
        clearTimeout(timeout)
        setStatus({
          apiOnline: online as boolean,
          totalRaces: stats?.total_races ?? null,
          totalModels: stats?.total_models ?? null,
        })
      })
    }

    void loadStatus()

    return () => { clearTimeout(timeout); controller.abort() }
  }, [])

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      {/* Header */}
      <header className="border-b border-[#1e1e1e] px-6 py-4 flex items-center justify-between">
        <Logo href="/home" />
        <div className="flex items-center gap-4">
          {isAdmin && (
            <Link
              href="/admin"
              className="text-xs text-[#888] hover:text-white transition-colors"
            >
              管理者
            </Link>
          )}
          <button
            onClick={async () => { await supabase.auth.signOut(); window.location.href = '/login' }}
            className="text-xs text-[#888] hover:text-white transition-colors"
          >
            ログアウト
          </button>
        </div>
      </header>

      {/* Main */}
      <main className="max-w-2xl mx-auto px-6 py-16">
        <h1 className="text-3xl font-bold mb-2">AI競馬予測</h1>
        <p className="text-[#888] mb-8 text-sm">機械学習による競馬予測・資金管理システム</p>

        {/* System status */}
        <div className="grid grid-cols-3 gap-3 mb-10">
          <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-4 flex flex-col gap-1">
            <div className="text-xs text-[#666]">API</div>
            <div className="flex items-center gap-1.5">
              {status.apiOnline === null
                ? <span className="text-[#444] text-sm">確認中…</span>
                : status.apiOnline
                  ? <><span className="w-2 h-2 rounded-full bg-green-500 shrink-0" /><span className="text-sm text-green-400">オンライン</span></>
                  : <><span className="w-2 h-2 rounded-full bg-red-500 shrink-0" /><span className="text-sm text-red-400">オフライン</span></>
              }
            </div>
          </div>
          <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-4 flex flex-col gap-1">
            <div className="text-xs text-[#666]">レース数</div>
            <div className="text-sm text-white font-medium">
              {status.totalRaces === null ? '…' : status.totalRaces.toLocaleString()}
            </div>
          </div>
          <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-4 flex flex-col gap-1">
            <div className="text-xs text-[#666]">モデル数</div>
            <div className="text-sm text-white font-medium">
              {status.totalModels === null ? '…' : status.totalModels}
            </div>
          </div>
        </div>

        {/* ── 2ステップ フロー ── */}
        <div className="mb-2">
          <p className="text-xs text-[#555] mb-4 tracking-wider uppercase">基本的な使い方 — 2ステップ</p>
          <div className="space-y-2">
            {FLOW_STEPS.map((item, idx) => (
              <div key={item.href}>
                <Link href={item.href}>
                  <div className="group flex items-center justify-between p-4 bg-[#111] border border-[#1e1e1e] rounded-lg hover:border-[#333] hover:bg-[#161616] transition-all cursor-pointer">
                    <div className="flex items-center gap-4">
                      <span className="text-xs text-[#4ade80] font-mono font-bold w-5 shrink-0">{item.step}</span>
                      <div>
                        <div className="font-medium text-white">{item.label}</div>
                        <div className="text-xs text-[#666] mt-0.5">{item.desc}</div>
                      </div>
                    </div>
                    <svg className="w-4 h-4 text-[#444] group-hover:text-[#888] transition-colors shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                    </svg>
                  </div>
                </Link>
                {idx < FLOW_STEPS.length - 1 && (
                  <div className="flex items-center justify-start pl-[22px] py-0.5">
                    <svg className="w-3 h-3 text-[#333]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                    </svg>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        <div className="mt-6 pt-6 border-t border-[#1e1e1e]">
          <Link
            href="/predict-batch"
            className="flex items-center justify-center gap-2 w-full py-3 bg-white text-black text-sm font-medium rounded-lg hover:bg-[#eee] transition-colors"
          >
            予測を始める
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </Link>
        </div>
      </main>
    </div>
  )
}
