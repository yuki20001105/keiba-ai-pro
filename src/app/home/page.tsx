'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useUserRole } from '@/hooks/useUserRole'
import { Logo } from '@/components/Logo'

const NAV_ITEMS = [
  { href: '/data-collection', label: 'データ取得',  desc: 'レース情報を自動収集',      step: '01' },
  { href: '/train',           label: 'モデル学習',  desc: 'AIモデルをトレーニング',    step: '02' },
  { href: '/predict-batch',   label: '予測実行',    desc: 'レース結果を予測・購入推奨', step: '03' },
  { href: '/dashboard',       label: '履歴・統計',  desc: '購入履歴と成績',            step: '04' },
]

export default function HomePage() {
  const [loading, setLoading] = useState(true)
  const { isAdmin } = useUserRole()

  useEffect(() => {
    setLoading(false)
  }, [])

  if (loading) {
    return (
      <div className="min-h-screen bg-[#0a0a0a] flex items-center justify-center">
        <div className="w-5 h-5 rounded-full border-2 border-white border-t-transparent animate-spin" />
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      {/* Header */}
      <header className="border-b border-[#1e1e1e] px-6 py-4 flex items-center justify-between">
        <Logo href="/home" />
        {isAdmin && (
          <Link
            href="/admin"
            className="text-xs text-[#888] hover:text-white transition-colors"
          >
            管理者
          </Link>
        )}
      </header>

      {/* Main */}
      <main className="max-w-2xl mx-auto px-6 py-16">
        <h1 className="text-3xl font-bold mb-2">AI競馬予測</h1>
        <p className="text-[#888] mb-12 text-sm">機械学習による競馬予測・資金管理システム</p>

        <div className="space-y-3">
          {NAV_ITEMS.map((item) => (
            <Link key={item.href} href={item.href}>
              <div className="group flex items-center justify-between p-5 bg-[#111] border border-[#1e1e1e] rounded-lg hover:border-[#333] hover:bg-[#161616] transition-all cursor-pointer">
                <div className="flex items-center gap-4">
                  <span className="text-xs text-[#444] font-mono w-5 shrink-0">{item.step}</span>
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
          ))}
        </div>

        <div className="mt-8 pt-8 border-t border-[#1e1e1e]">
          <Link
            href="/data-collection"
            className="flex items-center justify-center gap-2 w-full py-3 bg-white text-black text-sm font-medium rounded-lg hover:bg-[#eee] transition-colors"
          >
            Step 01 から始める
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </Link>
        </div>
      </main>
    </div>
  )
}
