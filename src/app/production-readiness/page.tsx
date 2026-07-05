'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import { Logo } from '@/components/Logo'
import { PremiumRequiredNotice } from '@/components/PremiumRequiredNotice'
import { useAuth } from '@/contexts/AuthContext'
import { authFetch } from '@/lib/auth-fetch'

type CheckState = 'pass' | 'warn' | 'fail' | 'unknown'

type CheckItem = {
  id: string
  label: string
  state: CheckState
  summary: string
  details?: Record<string, unknown>
  durationMs?: number
}

type ReadinessResponse = {
  success: boolean
  overall: CheckState
  generated_at: string
  checks: CheckItem[]
  guard: {
    read_only_mode: boolean
    sandbox_write_readback_included: boolean
    production_base_write_allowed: boolean
  }
}

function stateStyle(state: CheckState): { badge: string; border: string; text: string } {
  switch (state) {
    case 'pass':
      return { badge: 'PASS', border: 'border-emerald-800/50', text: 'text-emerald-300' }
    case 'warn':
      return { badge: 'WARN', border: 'border-yellow-800/50', text: 'text-yellow-300' }
    case 'fail':
      return { badge: 'FAIL', border: 'border-red-800/50', text: 'text-red-300' }
    default:
      return { badge: 'UNKNOWN', border: 'border-[#2a2a2a]', text: 'text-[#888]' }
  }
}

function renderCompactDetails(details?: Record<string, unknown>) {
  if (!details) return null
  const safeKeys = [
    'status',
    'reason',
    'summary',
    'exit_code',
    'http_status',
    'predictions_count',
    'lineCount',
    'APP_ENV',
    'NETKEIBA_RACE_WRITE_ENABLED',
    'ALLOW_STAGING_WRITE',
  ]

  const picked = Object.fromEntries(
    Object.entries(details).filter(([k]) => safeKeys.includes(k))
  )

  if (Object.keys(picked).length === 0) return null

  return (
    <pre className="mt-2 text-[11px] text-[#888] bg-[#0f0f0f] border border-[#202020] rounded p-2 overflow-x-auto">
      {JSON.stringify(picked, null, 2)}
    </pre>
  )
}

export default function ProductionReadinessPage() {
  const { isPremium, isAdmin, loading: authLoading } = useAuth()
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<ReadinessResponse | null>(null)
  const [error, setError] = useState('')

  const canRun = isAdmin || isPremium

  const grouped = useMemo(() => {
    if (!result) return [] as CheckItem[]
    return result.checks
  }, [result])

  const runChecks = async () => {
    setRunning(true)
    setError('')
    try {
      const response = await authFetch('/api/production-readiness', {
        method: 'POST',
        signal: AbortSignal.timeout(300000),
      })
      if (!response.ok) {
        const d = await response.json().catch(() => ({}))
        throw new Error(String(d?.error || `HTTP ${response.status}`))
      }
      const data = await response.json()
      setResult(data as ReadinessResponse)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'チェック実行に失敗しました')
    } finally {
      setRunning(false)
    }
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
          <span className="text-sm text-[#888]">本番前チェック</span>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-8 space-y-5">
        <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5">
          <h1 className="text-lg font-semibold">Production Readiness Check</h1>
          <p className="text-xs text-[#666] mt-2">
            予測・分析・read-only運用の安全確認のみを実行します。write系 API、production/base table write、sandbox write-readback は実行しません。
          </p>

          {!authLoading && !canRun && (
            <div className="mt-4">
              <PremiumRequiredNotice
                title="本番前チェックは Premium または Admin 専用です"
                message="非権限ユーザーは実行できません。チェック内容は read-only に限定されています。"
              />
            </div>
          )}

          <div className="mt-4 flex items-center gap-3">
            <button
              onClick={runChecks}
              disabled={!canRun || running}
              className="px-4 py-2.5 rounded bg-white text-black text-sm font-medium hover:bg-[#eee] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {running ? '実行中...' : '本番前チェックを実行'}
            </button>
            {running && <span className="text-xs text-[#888]">allowlist command + health checks を順次実行しています...</span>}
          </div>

          {error && (
            <div className="mt-4 p-3 bg-red-900/20 border border-red-800 rounded text-sm text-red-300">
              {error}
            </div>
          )}
        </div>

        {result && (
          <>
            <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-4 flex flex-wrap items-center gap-4">
              <span className="text-xs text-[#666]">overall</span>
              <span className={`text-sm font-semibold ${stateStyle(result.overall).text}`}>{stateStyle(result.overall).badge}</span>
              <span className="text-xs text-[#555]">generated: {new Date(result.generated_at).toLocaleString('ja-JP')}</span>
              <span className="text-xs text-[#666] ml-auto">read_only_mode: {String(result.guard.read_only_mode)}</span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {grouped.map((item) => {
                const style = stateStyle(item.state)
                return (
                  <div key={item.id} className={`bg-[#111] border ${style.border} rounded-lg p-4`}>
                    <div className="flex items-center justify-between gap-3">
                      <h2 className="text-sm font-medium">{item.label}</h2>
                      <span className={`text-[10px] px-2 py-0.5 rounded border border-[#333] ${style.text}`}>{style.badge}</span>
                    </div>
                    <p className="text-xs text-[#888] mt-2">{item.summary}</p>
                    {typeof item.durationMs === 'number' && (
                      <p className="text-[10px] text-[#555] mt-1">{item.durationMs} ms</p>
                    )}
                    {renderCompactDetails(item.details)}
                  </div>
                )
              })}
            </div>

            <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-4">
              <h3 className="text-sm font-medium">運用メモ</h3>
              <ul className="mt-2 text-xs text-[#777] space-y-1">
                <li>- smoke suite の認証失敗 (401) は環境依存で別管理してください。</li>
                <li>- secret 値は表示しません。出力は要約のみです。</li>
                <li>- secret scan は Notion token prefix を対象に実行します。</li>
                <li>- DB/reports/metadata を staged していないか、git status 注意カードで確認してください。</li>
              </ul>
            </div>
          </>
        )}
      </main>
    </div>
  )
}
