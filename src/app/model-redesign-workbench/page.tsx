'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { Logo } from '@/components/Logo'
import { PremiumRequiredNotice } from '@/components/PremiumRequiredNotice'
import { useAuth } from '@/contexts/AuthContext'
import { authFetch } from '@/lib/auth-fetch'

type UiState = 'pass' | 'warn' | 'fail'

type NumericMetric = {
  value: number | null
  status: UiState
  note: string
}

type SummaryResponse = {
  success: boolean
  state: UiState
  code: string
  generated_at: string
  warnings: string[]
  active_model: {
    model_id: string | null
    model_file_exists: boolean
    model_file_size_bytes: number | null
    model_file_updated_at: string | null
    active_model_path: string
  }
  metrics: {
    rmse: NumericMetric
    auc: NumericMetric
    spearman: NumericMetric
    hit_rate: NumericMetric
    roi: NumericMetric
  }
  feature_importance: {
    source: string
    top_features: Array<{
      feature: string
      total_score: number | null
      spearman: number | null
      vif: number | null
      op_class: string
    }>
  }
  correlation_warnings: {
    high_vif: Array<{ feature: string; total_score: number | null; spearman: number | null; vif: number | null; op_class: string }>
    duplicate_pairs: Array<{ pair: string; reason: string }>
  }
  removal_candidates: Array<{ feature: string; total_score: number | null; spearman: number | null; vif: number | null; op_class: string }>
  improvement_preview: {
    source: string
    recommendations: string[]
  }
  guard: {
    read_only_mode: boolean
    retrain_execution: string
    active_model_switch: string
    production_write: boolean
  }
}

function stateClass(state: UiState): string {
  if (state === 'pass') return 'text-emerald-300 border-emerald-800/50'
  if (state === 'warn') return 'text-yellow-300 border-yellow-800/50'
  return 'text-red-300 border-red-800/50'
}

function fmtMetric(value: number | null, kind: 'ratio' | 'number' = 'number'): string {
  if (value == null) return 'N/A'
  if (kind === 'ratio') return `${(value * 100).toFixed(2)}%`
  return Number.isFinite(value) ? value.toFixed(4) : 'N/A'
}

export default function ModelRedesignWorkbenchPage() {
  const { isPremium, isAdmin, loading: authLoading } = useAuth()
  const canView = isAdmin || isPremium

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [summary, setSummary] = useState<SummaryResponse | null>(null)

  useEffect(() => {
    if (authLoading || !canView) return
    let alive = true

    const load = async () => {
      setLoading(true)
      setError('')
      try {
        const response = await authFetch('/api/model-redesign/summary', {
          method: 'GET',
          signal: AbortSignal.timeout(120000),
        })
        const data = await response.json().catch(() => ({})) as Partial<SummaryResponse> & { error?: string }
        if (!response.ok || !data.success) {
          throw new Error(data.error || `HTTP ${response.status}`)
        }
        if (alive) setSummary(data as SummaryResponse)
      } catch (e: unknown) {
        if (alive) setError(e instanceof Error ? e.message : 'summary 読み込みに失敗しました')
      } finally {
        if (alive) setLoading(false)
      }
    }

    void load()
    return () => {
      alive = false
    }
  }, [authLoading, canView])

  const topImportance = useMemo(
    () => summary?.feature_importance.top_features.slice(0, 15) ?? [],
    [summary],
  )

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
          <span className="text-sm text-[#888]">モデル再設計ワークベンチ (MVP)</span>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-8 space-y-5">
        <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5">
          <h1 className="text-lg font-semibold">Model Redesign Workbench</h1>
          <p className="text-xs text-[#666] mt-2">
            現在は read-only / preview 中心の MVP です。再学習実行・active model切替・production write は未実装です。
          </p>

          {!authLoading && !canView && (
            <div className="mt-4">
              <PremiumRequiredNotice
                title="ワークベンチは Premium または Admin 専用です"
                message="権限不足時は summary API を呼び出しません。"
              />
            </div>
          )}

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button
              disabled
              className="px-4 py-2.5 rounded bg-[#2a2a2a] text-[#888] text-sm font-medium cursor-not-allowed"
              title="MVPでは未実装"
            >
              再学習を実行 (not-implemented)
            </button>
            <button
              disabled
              className="px-4 py-2.5 rounded bg-[#2a2a2a] text-[#888] text-sm font-medium cursor-not-allowed"
              title="MVPでは未実装"
            >
              active model を切替 (not-implemented)
            </button>
            <Link
              href="/notion-report"
              className="px-4 py-2.5 rounded bg-[#1f5eff] text-white text-sm font-medium hover:bg-[#3c71ff] transition-colors"
            >
              Notion出力UIへ
            </Link>
          </div>

          {error && (
            <div className="mt-4 p-3 bg-red-900/20 border border-red-800 rounded text-sm text-red-300">
              {error}
            </div>
          )}
        </div>

        {loading && <div className="text-sm text-[#666]">summary 読み込み中...</div>}

        {summary && (
          <>
            <div className={`bg-[#111] border rounded-lg p-4 ${stateClass(summary.state)}`}>
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <p className="text-sm font-medium">Summary state: {summary.state.toUpperCase()} / {summary.code}</p>
                <p className="text-xs text-[#888]">generated: {new Date(summary.generated_at).toLocaleString('ja-JP')}</p>
              </div>
              {summary.warnings.length > 0 && (
                <ul className="mt-2 text-xs text-[#bbb] space-y-1 list-disc list-inside">
                  {summary.warnings.map((w) => <li key={w}>{w}</li>)}
                </ul>
              )}
            </div>

            <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-4">
              <h2 className="text-sm font-medium">Active Model Summary</h2>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3 text-xs">
                <div className="bg-[#0f0f0f] border border-[#202020] rounded p-3">model_id: {summary.active_model.model_id || 'N/A'}</div>
                <div className="bg-[#0f0f0f] border border-[#202020] rounded p-3">file_exists: {String(summary.active_model.model_file_exists)}</div>
                <div className="bg-[#0f0f0f] border border-[#202020] rounded p-3">file_size: {summary.active_model.model_file_size_bytes ?? 'N/A'}</div>
                <div className="bg-[#0f0f0f] border border-[#202020] rounded p-3">updated_at: {summary.active_model.model_file_updated_at || 'N/A'}</div>
              </div>
            </div>

            <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-4">
              <h2 className="text-sm font-medium">Current Model Metrics</h2>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mt-3 text-xs">
                <div className="bg-[#0f0f0f] border border-[#202020] rounded p-3">RMSE: {fmtMetric(summary.metrics.rmse.value)}</div>
                <div className="bg-[#0f0f0f] border border-[#202020] rounded p-3">AUC: {fmtMetric(summary.metrics.auc.value)}</div>
                <div className="bg-[#0f0f0f] border border-[#202020] rounded p-3">Spearman: {fmtMetric(summary.metrics.spearman.value)}</div>
                <div className="bg-[#0f0f0f] border border-[#202020] rounded p-3">的中率: {fmtMetric(summary.metrics.hit_rate.value, 'ratio')}</div>
                <div className="bg-[#0f0f0f] border border-[#202020] rounded p-3">ROI: {fmtMetric(summary.metrics.roi.value, 'ratio')}</div>
              </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-4">
                <h2 className="text-sm font-medium">Feature Importance (Top)</h2>
                <div className="mt-2 text-[11px] text-[#666]">source: {summary.feature_importance.source}</div>
                <div className="mt-3 max-h-[360px] overflow-auto space-y-2">
                  {topImportance.map((f) => (
                    <div key={f.feature} className="bg-[#0f0f0f] border border-[#202020] rounded p-2 text-xs">
                      <div className="font-medium text-[#ddd]">{f.feature}</div>
                      <div className="text-[#888] mt-1">score: {fmtMetric(f.total_score)} / spearman: {fmtMetric(f.spearman)} / vif: {fmtMetric(f.vif)}</div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="space-y-4">
                <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-4">
                  <h2 className="text-sm font-medium">相関・重複特徴量の警告</h2>
                  <div className="mt-2 text-xs text-[#888]">高VIF: {summary.correlation_warnings.high_vif.length}件 / 重複候補: {summary.correlation_warnings.duplicate_pairs.length}件</div>
                  <ul className="mt-2 text-xs text-[#bbb] space-y-1 list-disc list-inside max-h-[200px] overflow-auto">
                    {summary.correlation_warnings.high_vif.slice(0, 8).map((w) => (
                      <li key={w.feature}>{w.feature}: VIF {fmtMetric(w.vif)}</li>
                    ))}
                    {summary.correlation_warnings.duplicate_pairs.slice(0, 8).map((d) => (
                      <li key={d.pair}>{d.pair} ({d.reason})</li>
                    ))}
                  </ul>
                </div>

                <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-4">
                  <h2 className="text-sm font-medium">削除候補特徴量</h2>
                  <ul className="mt-2 text-xs text-[#bbb] space-y-1 list-disc list-inside max-h-[160px] overflow-auto">
                    {summary.removal_candidates.length === 0
                      ? <li>候補なし</li>
                      : summary.removal_candidates.map((c) => (
                        <li key={c.feature}>{c.feature} (score {fmtMetric(c.total_score)}, vif {fmtMetric(c.vif)})</li>
                      ))}
                  </ul>
                </div>

                <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-4">
                  <h2 className="text-sm font-medium">改善提案 preview</h2>
                  <div className="mt-1 text-[11px] text-[#666]">source: {summary.improvement_preview.source}</div>
                  <ul className="mt-2 text-xs text-[#bbb] space-y-1 list-disc list-inside">
                    {summary.improvement_preview.recommendations.map((r, i) => <li key={`${i}-${r}`}>{r}</li>)}
                  </ul>
                </div>
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  )
}
