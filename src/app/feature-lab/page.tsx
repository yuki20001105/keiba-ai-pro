'use client'

import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { Logo } from '@/components/Logo'
import { authFetch } from '@/lib/auth-fetch'

type Summary = {
  version: string
  hash: string
  future_fields_count: number
  scraped_fields_count: number
  engineered_total: number
  engineered_enabled: number
  engineered_disabled: number
  unnecessary_columns_count: number
  by_stage: Record<string, { total: number; enabled: number; disabled: number }>
}

type ImportanceFeature = {
  name: string
  importance: number
  importance_pct: number
  description?: string
}

type ImportanceResult = {
  model_path: string
  target: string
  importance_type: string
  total_features: number
  features: ImportanceFeature[]
}

type CoverageFeature = { name: string; description: string }

type CoverageResult = {
  model: string
  target: string
  catalog_enabled: number
  model_total: number
  matched: number
  missing_from_model: CoverageFeature[]
  extra_in_model: CoverageFeature[]
}

const TARGETS = ['win', 'place3', 'speed_deviation'] as const
type Target = (typeof TARGETS)[number]

export default function FeatureLabPage() {
  const [tab, setTab] = useState<'summary' | 'importance' | 'coverage'>('summary')
  const [target, setTarget] = useState<Target>('win')
  const [importanceType, setImportanceType] = useState<'gain' | 'split'>('gain')
  const [topN, setTopN] = useState(30)

  const [summary, setSummary] = useState<Summary | null>(null)
  const [importance, setImportance] = useState<ImportanceResult | null>(null)
  const [coverage, setCoverage] = useState<CoverageResult | null>(null)

  const [loadingSummary, setLoadingSummary] = useState(false)
  const [loadingImportance, setLoadingImportance] = useState(false)
  const [loadingCoverage, setLoadingCoverage] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchSummary = useCallback(async () => {
    setLoadingSummary(true)
    setError(null)
    try {
      const res = await authFetch('/api/features/summary', { signal: AbortSignal.timeout(30000) })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setSummary(await res.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'エラー')
    } finally {
      setLoadingSummary(false)
    }
  }, [])

  const fetchImportance = useCallback(async () => {
    setLoadingImportance(true)
    setError(null)
    try {
      const url = `/api/features/importance?target=${target}&top_n=${topN}&importance_type=${importanceType}`
      const res = await fetch(url, { signal: AbortSignal.timeout(30000) })
      if (!res.ok) {
        const d = await res.json().catch(() => ({}))
        throw new Error(d.detail || `HTTP ${res.status}`)
      }
      setImportance(await res.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'エラー')
    } finally {
      setLoadingImportance(false)
    }
  }, [target, topN, importanceType])

  const fetchCoverage = useCallback(async () => {
    setLoadingCoverage(true)
    setError(null)
    try {
      const res = await authFetch(`/api/features/coverage?target=${target}`, { signal: AbortSignal.timeout(30000) })
      if (!res.ok) {
        const d = await res.json().catch(() => ({}))
        throw new Error(d.detail || `HTTP ${res.status}`)
      }
      setCoverage(await res.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'エラー')
    } finally {
      setLoadingCoverage(false)
    }
  }, [target])

  // 初回ロード
  useEffect(() => { fetchSummary() }, [fetchSummary])

  useEffect(() => {
    if (tab === 'importance') fetchImportance()
    if (tab === 'coverage') fetchCoverage()
  }, [tab, fetchImportance, fetchCoverage])

  const maxImp = importance?.features[0]?.importance ?? 1

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <header className="border-b border-[#1e1e1e] px-6 py-4 flex items-center justify-between">
        <Logo href="/home" />
        <div className="flex items-center gap-4">
          <Link href="/train" className="flex items-center gap-1 text-xs text-[#555] hover:text-white transition-colors">
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
            学習
          </Link>
          <span className="text-sm text-[#888]">特徴量ラボ</span>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-8 space-y-6">
        {/* ターゲット選択 */}
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-xs text-[#666]">ターゲット:</span>
          {TARGETS.map(t => (
            <button
              key={t}
              onClick={() => setTarget(t)}
              className={`px-3 py-1.5 rounded text-xs transition-colors ${
                target === t
                  ? 'bg-white text-black font-medium'
                  : 'bg-[#1a1a1a] text-[#888] hover:text-white border border-[#2a2a2a]'
              }`}
            >
              {t}
            </button>
          ))}
        </div>

        {/* タブ */}
        <div className="flex gap-1 border-b border-[#1e1e1e]">
          {([['summary', 'サマリー'], ['importance', '重要度'], ['coverage', 'カバレッジ']] as const).map(
            ([key, label]) => (
              <button
                key={key}
                onClick={() => setTab(key)}
                className={`px-4 py-2.5 text-xs transition-colors border-b-2 ${
                  tab === key
                    ? 'border-white text-white'
                    : 'border-transparent text-[#555] hover:text-[#888]'
                }`}
              >
                {label}
              </button>
            )
          )}
        </div>

        {error && (
          <div className="bg-red-900/20 border border-red-800 rounded-lg px-4 py-3 text-sm text-red-300">
            {error}
          </div>
        )}

        {/* ── サマリータブ ── */}
        {tab === 'summary' && (
          loadingSummary ? (
            <div className="text-sm text-[#555] py-8 text-center">読み込み中…</div>
          ) : summary ? (
            <div className="space-y-4">
              {/* 概要カード */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {[
                  { label: '未来フィールド', value: summary.future_fields_count, color: 'text-red-400' },
                  { label: 'スクレイプ列', value: summary.scraped_fields_count, color: 'text-blue-400' },
                  { label: 'FE特徴量 (有効)', value: `${summary.engineered_enabled} / ${summary.engineered_total}`, color: 'text-green-400' },
                  { label: '除外列', value: summary.unnecessary_columns_count, color: 'text-yellow-400' },
                ].map(card => (
                  <div key={card.label} className="bg-[#111] border border-[#1e1e1e] rounded-lg px-4 py-3">
                    <div className="text-xs text-[#555] mb-1">{card.label}</div>
                    <div className={`text-lg font-mono font-medium ${card.color}`}>{card.value}</div>
                  </div>
                ))}
              </div>

              {/* ステージ別 */}
              <div className="bg-[#111] border border-[#1e1e1e] rounded-lg overflow-hidden">
                <div className="px-5 py-3 border-b border-[#1e1e1e]">
                  <span className="text-xs font-medium text-[#888]">ステージ別 エンジニアリング特徴量</span>
                </div>
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-[#1a1a1a]">
                      <th className="px-5 py-2.5 text-left text-[#555] font-normal">ステージ</th>
                      <th className="px-5 py-2.5 text-right text-[#555] font-normal">合計</th>
                      <th className="px-5 py-2.5 text-right text-[#555] font-normal">有効</th>
                      <th className="px-5 py-2.5 text-right text-[#555] font-normal">無効</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(summary.by_stage).map(([stage, counts]) => (
                      <tr key={stage} className="border-b border-[#111] hover:bg-[#141414]">
                        <td className="px-5 py-2 text-[#bbb] font-mono">{stage}</td>
                        <td className="px-5 py-2 text-right text-[#888]">{counts.total}</td>
                        <td className="px-5 py-2 text-right text-green-400">{counts.enabled}</td>
                        <td className="px-5 py-2 text-right text-[#555]">{counts.disabled}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* カタログメタ */}
              <div className="text-xs text-[#444] font-mono">
                v{summary.version} · {summary.hash.slice(0, 12)}
              </div>
            </div>
          ) : null
        )}

        {/* ── 重要度タブ ── */}
        {tab === 'importance' && (
          <div className="space-y-4">
            <div className="flex items-center gap-4 flex-wrap">
              <div className="flex items-center gap-2">
                <span className="text-xs text-[#666]">タイプ:</span>
                {(['gain', 'split'] as const).map(t => (
                  <button
                    key={t}
                    onClick={() => setImportanceType(t)}
                    className={`px-2.5 py-1 rounded text-xs transition-colors ${
                      importanceType === t
                        ? 'bg-[#222] text-white border border-[#444]'
                        : 'text-[#555] hover:text-[#888]'
                    }`}
                  >
                    {t}
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-2">
                <span className="text-xs text-[#666]">上位:</span>
                {[20, 30, 50].map(n => (
                  <button
                    key={n}
                    onClick={() => setTopN(n)}
                    className={`px-2.5 py-1 rounded text-xs transition-colors ${
                      topN === n
                        ? 'bg-[#222] text-white border border-[#444]'
                        : 'text-[#555] hover:text-[#888]'
                    }`}
                  >
                    {n}
                  </button>
                ))}
              </div>
              <button
                onClick={fetchImportance}
                disabled={loadingImportance}
                className="px-3 py-1 text-xs bg-[#1a1a1a] border border-[#2a2a2a] rounded hover:border-[#444] disabled:opacity-40 transition-colors"
              >
                {loadingImportance ? '取得中…' : '更新'}
              </button>
            </div>

            {loadingImportance ? (
              <div className="text-sm text-[#555] py-8 text-center">モデルを読み込み中…</div>
            ) : importance ? (
              <div className="bg-[#111] border border-[#1e1e1e] rounded-lg overflow-hidden">
                <div className="px-5 py-3 border-b border-[#1e1e1e] flex items-center justify-between">
                  <span className="text-xs font-medium text-[#888]">{importance.model_path}</span>
                  <span className="text-xs text-[#555]">{importance.total_features} 特徴量合計</span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-[#1a1a1a]">
                        <th className="px-5 py-2.5 text-left text-[#555] font-normal w-8">#</th>
                        <th className="px-5 py-2.5 text-left text-[#555] font-normal">
                          特徴量
                          <span className="ml-1.5 text-[#333] text-[10px] font-normal">（名前にカーソルで説明表示）</span>
                        </th>
                        <th className="px-5 py-2.5 text-right text-[#555] font-normal">寄与率</th>
                        <th className="px-5 py-2.5 text-left text-[#555] font-normal w-40">バー</th>
                      </tr>
                    </thead>
                    <tbody>
                      {importance.features.map((f, i) => (
                        <tr key={f.name} className="border-b border-[#0d0d0d] hover:bg-[#141414] group">
                          <td className="px-5 py-1.5 text-[#444]">{i + 1}</td>
                          <td className="px-5 py-1.5">
                            <div className="relative inline-block">
                              <span
                                className={`font-mono cursor-default ${
                                  f.description ? 'text-[#ccc] underline decoration-dotted decoration-[#444] underline-offset-2' : 'text-[#ccc]'
                                }`}
                              >
                                {f.name}
                              </span>
                              {f.description && (
                                <div className="absolute left-0 top-full mt-1 z-50 hidden group-hover:block w-64 px-3 py-2 bg-[#1a1a1a] border border-[#333] rounded shadow-lg pointer-events-none">
                                  <p className="text-[11px] text-[#aaa] leading-relaxed">{f.description}</p>
                                </div>
                              )}
                            </div>
                          </td>
                          <td className="px-5 py-1.5 text-right text-[#888]">{f.importance_pct.toFixed(2)}%</td>
                          <td className="px-5 py-1.5">
                            <div
                              className="h-1.5 rounded-full bg-blue-500/60"
                              style={{ width: `${(f.importance / maxImp) * 100}%` }}
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : null}
          </div>
        )}

        {/* ── カバレッジタブ ── */}
        {tab === 'coverage' && (
          <div className="space-y-4">
            <div className="flex gap-2">
              <button
                onClick={fetchCoverage}
                disabled={loadingCoverage}
                className="px-3 py-1 text-xs bg-[#1a1a1a] border border-[#2a2a2a] rounded hover:border-[#444] disabled:opacity-40 transition-colors"
              >
                {loadingCoverage ? '確認中…' : '再確認'}
              </button>
            </div>

            {loadingCoverage ? (
              <div className="text-sm text-[#555] py-8 text-center">確認中…</div>
            ) : coverage ? (
              <div className="space-y-4">
                {/* サマリカード */}
                <div className="grid grid-cols-3 gap-3">
                  {[
                    { label: 'カタログ有効', value: coverage.catalog_enabled, color: 'text-blue-400' },
                    { label: 'モデル特徴量', value: coverage.model_total, color: 'text-white' },
                    { label: 'マッチ', value: `${coverage.matched} (${Math.round(coverage.matched / coverage.catalog_enabled * 100)}%)`, color: coverage.matched === coverage.catalog_enabled ? 'text-green-400' : 'text-yellow-400' },
                  ].map(c => (
                    <div key={c.label} className="bg-[#111] border border-[#1e1e1e] rounded-lg px-4 py-3">
                      <div className="text-xs text-[#555] mb-1">{c.label}</div>
                      <div className={`text-lg font-mono font-medium ${c.color}`}>{c.value}</div>
                    </div>
                  ))}
                </div>

                {/* モデルファイル */}
                <div className="text-xs text-[#444] font-mono">{coverage.model}</div>

                {/* 不足特徴量 */}
                {coverage.missing_from_model.length > 0 && (
                  <div className="bg-[#111] border border-yellow-900/40 rounded-lg overflow-hidden">
                    <div className="px-5 py-3 border-b border-yellow-900/30 flex items-center gap-2">
                      <span className="text-xs text-yellow-400 font-medium">カタログ有効だがモデルに不在</span>
                      <span className="text-xs text-[#555]">({coverage.missing_from_model.length})</span>
                    </div>
                    <div className="px-5 py-3">
                      <div className="flex flex-wrap gap-2">
                        {coverage.missing_from_model.filter(f => f?.name).map(f => (
                          <div key={f.name} className="relative group inline-block">
                            <span className={`px-2 py-0.5 bg-yellow-900/20 border border-yellow-900/40 rounded text-xs font-mono cursor-default ${f.description ? 'text-yellow-300 underline decoration-dotted decoration-yellow-700' : 'text-yellow-300'}`}>
                              {f.name}
                            </span>
                            {f.description && (
                              <div className="absolute bottom-full left-0 mb-1 z-10 hidden group-hover:block w-56 rounded bg-[#1a1a1a] border border-yellow-900/40 px-3 py-2 text-xs text-[#ccc] shadow-lg pointer-events-none">
                                {f.description}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                )}

                {/* 余剰特徴量 */}
                {coverage.extra_in_model.length > 0 && (
                  <div className="bg-[#111] border border-[#1e1e1e] rounded-lg overflow-hidden">
                    <div className="px-5 py-3 border-b border-[#1e1e1e] flex items-center gap-2">
                      <span className="text-xs text-[#888] font-medium">モデルにあるがカタログ未登録</span>
                      <span className="text-xs text-[#555]">({coverage.extra_in_model.length})</span>
                    </div>
                    <div className="px-5 py-3">
                      <div className="flex flex-wrap gap-2">
                        {coverage.extra_in_model.filter(f => f?.name).map(f => (
                          <div key={f.name} className="relative group inline-block">
                            <span className={`px-2 py-0.5 bg-[#1a1a1a] border border-[#2a2a2a] rounded text-xs font-mono cursor-default ${f.description ? 'text-[#888] underline decoration-dotted decoration-[#444]' : 'text-[#666]'}`}>
                              {f.name}
                            </span>
                            {f.description && (
                              <div className="absolute bottom-full left-0 mb-1 z-10 hidden group-hover:block w-56 rounded bg-[#1a1a1a] border border-[#2a2a2a] px-3 py-2 text-xs text-[#ccc] shadow-lg pointer-events-none">
                                {f.description}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                )}

                {coverage.missing_from_model.length === 0 && (
                  <div className="text-xs text-green-400">✓ カタログの全有効特徴量がモデルに存在します</div>
                )}
              </div>
            ) : null}
          </div>
        )}
      </main>
    </div>
  )
}
