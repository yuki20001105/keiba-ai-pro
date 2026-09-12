'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/contexts/AuthContext'
import { Logo } from '@/components/Logo'
import { supabase } from '@/lib/supabase'

const FLOW_STEPS = [
  {
    href: '/data-collection',
    label: 'データ取得',
    desc: '期間を指定してレース情報を取得',
    step: '01',
    adminOnly: true,
  },
  {
    href: '/train',
    label: 'モデル作成',
    desc: '学習条件を設定して予測モデルを作成',
    step: '02',
    adminOnly: true,
  },
  {
    href: '/predict-batch',
    label: '予測実行',
    desc: 'レースを選び、予測・購入推奨を確認',
    step: '03',
    adminOnly: false,
  },
  {
    href: '/dashboard',
    label: '成績確認',
    desc: '購入履歴と損益・回収率を分析',
    step: '04',
    adminOnly: false,
  },
  {
    href: '/user-management',
    label: 'ユーザー管理',
    desc: '登録ユーザーと利用状況を確認',
    step: '05',
    adminOnly: true,
  },
] as const

export default function HomePage() {
  const router = useRouter()
  const { isAdmin, loading: authLoading } = useAuth()

  const visibleSteps = FLOW_STEPS.filter(step => !step.adminOnly || isAdmin)

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <header className="flex items-center justify-between border-b border-[#1e1e1e] px-4 py-4 sm:px-6">
        <Logo href="/home" />
        <button
          type="button"
          onClick={async () => {
            await supabase.auth.signOut()
            router.replace('/login')
          }}
          className="text-xs text-[#888] transition-colors hover:text-white"
        >
          ログアウト
        </button>
      </header>

      <main className="mx-auto max-w-2xl px-6 py-16">
        <h1 className="mb-2 text-3xl font-bold">AI競馬予測</h1>
        <p className="mb-8 text-sm text-[#888]">必要な機能を順番に操作できます</p>

        <div className="mb-2">
          <p className="mb-4 text-xs uppercase tracking-wider text-[#555]">
            {authLoading
              ? '利用権限を確認中'
              : isAdmin
                ? '基本的な使い方 — 5ステップ'
                : '利用できる機能 — 2件'}
          </p>
          {authLoading ? (
            <div role="status" aria-live="polite" className="rounded-lg border border-[#1e1e1e] bg-[#111] px-4 py-8 text-center text-sm text-[#888]">
              利用できる機能を確認しています…
            </div>
          ) : (
            <div className="space-y-2">
              {visibleSteps.map((item, index) => (
                <div key={item.href}>
                  <Link href={item.href}>
                    <div className="group flex cursor-pointer items-center justify-between rounded-lg border border-[#1e1e1e] bg-[#111] p-4 transition-all hover:border-[#333] hover:bg-[#161616]">
                      <div className="flex items-center gap-4">
                        <span className="w-5 shrink-0 font-mono text-xs font-bold text-[#4ade80]">{item.step}</span>
                        <div>
                          <div className="font-medium text-white">{item.label}</div>
                          <div className="mt-0.5 text-xs text-[#666]">{item.desc}</div>
                        </div>
                      </div>
                      <svg className="h-4 w-4 shrink-0 text-[#444] transition-colors group-hover:text-[#888]" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                      </svg>
                    </div>
                  </Link>
                  {index < visibleSteps.length - 1 && (
                    <div className="flex items-center justify-start py-0.5 pl-[22px]" aria-hidden="true">
                      <svg className="h-3 w-3 text-[#333]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                      </svg>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </main>
    </div>
  )
}
