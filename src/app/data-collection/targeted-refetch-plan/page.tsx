'use client'

import Link from 'next/link'
import { useMemo, useState } from 'react'
import { Logo } from '@/components/Logo'
import { authFetch } from '@/lib/auth-fetch'
import {
  TargetedRefetchApiResponse,
  TargetedRefetchTarget,
  validateTargetedRefetchPlanReport,
} from '@/lib/targeted-refetch-plan-contract'

const TARGETS: TargetedRefetchTarget[] = ['all', 'race', 'horse', 'result', 'pedigree', 'odds']
const SAMPLE_BUCKETS: Array<keyof TargetedRefetchApiResponse['plan']['sample_urls']> = [
  'result_page',
  'race_detail',
  'horse_detail',
  'pedigree',
]

function formatSeconds(v: number): string {
  const s = Math.max(0, Math.round(v || 0))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  if (h > 0) return `${h}h ${m}m ${sec}s`
  if (m > 0) return `${m}m ${sec}s`
  return `${sec}s`
}

export default function TargetedRefetchPlanPage() {
  const [target, setTarget] = useState<TargetedRefetchTarget>('all')
  const [maxTargetsInput, setMaxTargetsInput] = useState('10')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [plan, setPlan] = useState<TargetedRefetchApiResponse['plan'] | null>(null)

  const parsedMaxTargets = Number(maxTargetsInput)
  const maxTargetsValid =
    Number.isInteger(parsedMaxTargets) && parsedMaxTargets >= 1 && parsedMaxTargets <= 50

  const statusText = useMemo(() => {
    if (loading) return 'loading'
    if (error) return 'error'
    if (plan) return 'success'
    return 'idle'
  }, [loading, error, plan])

  const handleGenerate = async () => {
    if (loading) return
    if (!maxTargetsValid) {
      setError('max_targets must be an integer between 1 and 50')
      setPlan(null)
      return
    }

    setLoading(true)
    setError('')
    setPlan(null)

    try {
      const response = await authFetch('/api/scrape/targeted-refetch-plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target,
          max_targets: parsedMaxTargets,
        }),
      })

      const payload = await response.json().catch(() => ({}))
      if (!response.ok) {
        const detail = typeof payload?.detail === 'string' ? payload.detail : ''
        const routeError = typeof payload?.error === 'string' ? payload.error : ''
        throw new Error(detail || routeError || `HTTP ${response.status}`)
      }

      const dryRun = payload?.dry_run === true
      const readOnly = payload?.read_only === true
      const executionDisabled = payload?.execution_enabled === false
      if (!dryRun || !readOnly || !executionDisabled) {
        throw new Error('response contract invalid')
      }

      const parsed = validateTargetedRefetchPlanReport(payload?.plan, {
        target,
        max_targets: parsedMaxTargets,
      })
      if (!parsed.ok) {
        throw new Error(parsed.error)
      }

      setPlan(parsed.plan)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'request failed')
      setPlan(null)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <header className="border-b border-[#1e1e1e] px-6 py-4 flex items-center justify-between">
        <Logo href="/home" />
        <div className="flex items-center gap-4">
          <Link href="/data-collection" className="text-xs text-[#666] hover:text-white transition-colors">
            データ取得へ戻る
          </Link>
          <span className="text-sm text-[#888]">Targeted Refetch Plan (Read-only)</span>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-8 space-y-5">
        <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5 space-y-3">
          <h2 className="text-sm font-medium">Targeted Refetch Planning</h2>
          <div className="rounded border border-[#334155] bg-[#0b1220] px-3 py-2 text-xs text-[#bfdbfe]">
            read-only / HTTPなし / DB writeなし。repair/refetch/live validation 実行はこの画面から行いません。
          </div>

          <div className="grid md:grid-cols-3 gap-3">
            <label className="text-xs text-[#777]">Target
              <select
                data-testid="phase3c-target-select"
                value={target}
                onChange={e => setTarget(e.target.value as TargetedRefetchTarget)}
                disabled={loading}
                className="mt-1 w-full px-3 py-2 bg-[#0a0a0a] border border-[#1e1e1e] rounded"
              >
                {TARGETS.map(t => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </label>

            <label className="text-xs text-[#777]">max targets
              <input
                data-testid="phase3c-max-targets-input"
                type="number"
                min={1}
                max={50}
                value={maxTargetsInput}
                disabled={loading}
                onChange={e => setMaxTargetsInput(e.target.value)}
                className="mt-1 w-full px-3 py-2 bg-[#0a0a0a] border border-[#1e1e1e] rounded"
              />
            </label>
          </div>

          <div className="flex items-center gap-3">
            <button
              data-testid="phase3c-generate-button"
              type="button"
              onClick={handleGenerate}
              disabled={loading || !maxTargetsValid}
              className={`px-4 py-2 rounded text-sm font-medium ${loading ? 'bg-[#222] text-[#555] cursor-not-allowed' : 'bg-white text-black hover:bg-[#eee]'}`}
            >
              {loading ? 'Generating...' : 'Generate Read-only Plan'}
            </button>
            <span data-testid="phase3c-status" className="text-xs text-[#666]">state: {statusText}</span>
          </div>

          {!maxTargetsValid && (
            <div data-testid="phase3c-input-error" className="rounded border border-[#4a1d1d] bg-[#220d0d] px-3 py-2 text-xs text-[#fca5a5]">
              max_targets must be an integer between 1 and 50
            </div>
          )}

          {error && (
            <div data-testid="phase3c-error" className="rounded border border-[#4a1d1d] bg-[#220d0d] px-3 py-2 text-xs text-[#fca5a5]">
              {error}
            </div>
          )}
        </div>

        {plan && (
          <>
            <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5 space-y-3" data-testid="phase3c-plan-summary">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium">Plan Summary</h3>
                <span className={`text-xs px-2 py-0.5 rounded ${plan.verdict === 'warn' ? 'bg-[#3f2d00] text-[#facc15]' : 'bg-[#0f2a1f] text-[#4ade80]'}`}>{plan.verdict}</span>
              </div>

              <div className="grid md:grid-cols-4 gap-2 text-xs">
                <div className="border border-[#1e1e1e] rounded p-2">p0_total_count: <span className="text-white">{plan.p0_total_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">refetch_candidate_count: <span className="text-white">{plan.refetch_candidate_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">unique_url_count: <span className="text-white">{plan.unique_url_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">reparse_candidate_count: <span className="text-white">{plan.reparse_candidate_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">race_result_url_count: <span className="text-white">{plan.race_result_url_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">race_detail_url_count: <span className="text-white">{plan.race_detail_url_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">horse_detail_url_count: <span className="text-white">{plan.horse_detail_url_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">pedigree_url_count: <span className="text-white">{plan.pedigree_url_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">excluded_schema_review_count: <span className="text-white">{plan.excluded_schema_review_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">excluded_domain_allowed_count: <span className="text-white">{plan.excluded_domain_allowed_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">excluded_metadata_repair_count: <span className="text-white">{plan.excluded_metadata_repair_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">excluded_cache_available_count: <span className="text-white">{plan.excluded_cache_available_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">estimated_http_request_count: <span className="text-white">{plan.estimated_http_request_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">estimated_runtime_seconds: <span className="text-white">{formatSeconds(plan.estimated_runtime_seconds)}</span></div>
              </div>
            </div>

            <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5 space-y-3" data-testid="phase3c-samples">
              <h3 className="text-sm font-medium">Sample URLs</h3>
              {SAMPLE_BUCKETS.map(bucket => (
                <div key={bucket} className="space-y-1">
                  <div className="text-xs text-[#9ca3af]">{bucket} ({plan.sample_urls[bucket].length})</div>
                  {plan.sample_urls[bucket].length === 0 ? (
                    <div className="text-xs text-[#666]">none</div>
                  ) : (
                    <div className="space-y-1">
                      {plan.sample_urls[bucket].map((item, idx) => (
                        <div key={`${bucket}-${idx}-${item.url}`} className="text-xs border border-[#1e1e1e] rounded px-3 py-2 bg-[#0a0a0a]">
                          <div className="text-white break-all">{item.url}</div>
                          <div className="text-[#9ca3af]">reason: {item.reason} / column: {item.column} / priority: {item.priority}</div>
                          <div className="text-[#6b7280]">race_id: {item.race_id || '-'} / horse_id: {item.horse_id || '-'}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>

            <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5 space-y-3" data-testid="phase3c-next-actions">
              <h3 className="text-sm font-medium">Recommended Next Actions</h3>
              {plan.recommended_next_actions.length === 0 ? (
                <div className="text-xs text-[#666]">候補0件のため追加アクションはありません（正常完了）。</div>
              ) : (
                <ul className="list-disc pl-5 text-xs text-[#d1d5db] space-y-1">
                  {plan.recommended_next_actions.map((action, idx) => (
                    <li key={`next-${idx}`}>{action}</li>
                  ))}
                </ul>
              )}
            </div>

            <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5 space-y-2" data-testid="phase3c-safety-flags">
              <h3 className="text-sm font-medium">Safety Flags</h3>
              <div className="grid md:grid-cols-3 gap-2 text-xs">
                <div className="border border-[#1e1e1e] rounded p-2">read_only: <span className="text-[#4ade80]">{String(plan.safety_flags.read_only)}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">no_db_write: <span className="text-[#4ade80]">{String(plan.safety_flags.no_db_write)}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">no_http_access: <span className="text-[#4ade80]">{String(plan.safety_flags.no_http_access)}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">no_scrape_execute: <span className="text-[#4ade80]">{String(plan.safety_flags.no_scrape_execute)}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">no_upsert: <span className="text-[#4ade80]">{String(plan.safety_flags.no_upsert)}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">no_force_refresh_execute: <span className="text-[#4ade80]">{String(plan.safety_flags.no_force_refresh_execute)}</span></div>
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  )
}
