'use client'

import Link from 'next/link'
import { useMemo, useState } from 'react'
import { Logo } from '@/components/Logo'
import { authFetch } from '@/lib/auth-fetch'

type Target = 'all' | 'race' | 'horse' | 'result' | 'pedigree' | 'odds'

type BreakdownItem = {
  action?: string
  reason?: string
  column: string
  count: number
}

type SampleTarget = {
  race_id?: string | null
  horse_id?: string | null
  column: string
  reason: string
  action: string
  priority: string
  source_hint: string
  recommended_next_action: string
}

type PlanPayload = {
  verdict: string
  target: string
  p0_total_count: number
  refetch_required_count: number
  reparse_cache_count: number
  repair_from_metadata_count: number
  schema_review_count: number
  manual_review_count: number
  no_action_count: number
  estimated_http_request_count: number
  estimated_runtime_seconds: number
  p0_action_breakdown: BreakdownItem[]
  p0_reason_breakdown: BreakdownItem[]
  sample_targets: SampleTarget[]
  recommended_next_actions: string[]
  safeguards?: Record<string, unknown>
}

const TARGET_OPTIONS: Target[] = ['all', 'race', 'horse', 'result', 'pedigree', 'odds']
const ACTION_ORDER = [
  'repair-from-existing-metadata',
  'reparse-cache',
  'refetch-required',
  'schema-review',
  'manual-review',
  'no-action-domain-allowed',
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

export default function P0RepairPlanPage() {
  const [target, setTarget] = useState<Target>('all')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [plan, setPlan] = useState<PlanPayload | null>(null)

  const groupedSamples = useMemo(() => {
    if (!plan) return {}
    const groups: Record<string, SampleTarget[]> = {}
    for (const action of ACTION_ORDER) {
      groups[action] = []
    }
    for (const item of plan.sample_targets || []) {
      const action = item.action || 'manual-review'
      if (!groups[action]) groups[action] = []
      groups[action].push(item)
    }
    return groups
  }, [plan])

  const handlePreview = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await authFetch('/api/scrape/p0-repair-plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(typeof data?.error === 'string' ? data.error : `HTTP ${res.status}`)
      }
      setPlan((data?.plan || null) as PlanPayload | null)
    } catch (e: unknown) {
      setPlan(null)
      setError(e instanceof Error ? e.message : 'p0 repair plan request failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <header className="border-b border-[#1e1e1e] px-6 py-4 flex items-center justify-between">
        <Logo href="/home" />
        <div className="flex items-center gap-4">
          <Link href="/data-collection" className="text-xs text-[#666] hover:text-white transition-colors">データ取得へ戻る</Link>
          <Link href="/data-collection/refresh-plan" className="text-xs text-[#666] hover:text-white transition-colors">Refresh Plan</Link>
          <span className="text-sm text-[#888]">P0 Repair Plan (Read-only)</span>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-8 space-y-5">
        <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5 space-y-3">
          <h2 className="text-sm font-medium">P0 Scrape Repair Plan Preview</h2>
          <div className="rounded border border-[#334155] bg-[#0b1220] px-3 py-2 text-xs text-[#bfdbfe]">
            この画面は read-only preview のみです。実DB更新・実スクレイピング・upsert・force refresh・repair 実行は行いません。
          </div>

          <div className="grid md:grid-cols-3 gap-3">
            <label className="text-xs text-[#777]">Target
              <select value={target} onChange={(e) => setTarget(e.target.value as Target)} className="mt-1 w-full px-3 py-2 bg-[#0a0a0a] border border-[#1e1e1e] rounded">
                {TARGET_OPTIONS.map((x) => <option key={x} value={x}>{x}</option>)}
              </select>
            </label>
          </div>

          <div className="flex items-center gap-3">
            <button onClick={handlePreview} disabled={loading} className={`px-4 py-2 rounded text-sm font-medium ${loading ? 'bg-[#222] text-[#555]' : 'bg-white text-black hover:bg-[#eee]'}`}>
              {loading ? 'Planning...' : 'Generate P0 Repair Plan'}
            </button>
            <button disabled className="px-4 py-2 rounded text-sm font-medium bg-[#222] text-[#555] cursor-not-allowed" title="Not implemented in this phase">
              Execute P0 Repair (Disabled)
            </button>
            <button disabled className="px-4 py-2 rounded text-sm font-medium bg-[#222] text-[#555] cursor-not-allowed" title="Not implemented in this phase">
              Execute Refetch (Disabled)
            </button>
          </div>

          {error && <div className="text-xs text-[#f87171]">{error}</div>}
        </div>

        {plan && (
          <>
            <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5 space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium">Plan Summary</h3>
                <span className={`text-xs px-2 py-0.5 rounded ${plan.verdict === 'warn' ? 'bg-[#3f2d00] text-[#facc15]' : 'bg-[#0f2a1f] text-[#4ade80]'}`}>{plan.verdict}</span>
              </div>
              <div className="grid md:grid-cols-4 gap-2 text-xs">
                <div className="border border-[#1e1e1e] rounded p-2">target: <span className="text-white">{plan.target}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">p0_total_count: <span className="text-white">{plan.p0_total_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">refetch_required_count: <span className="text-white">{plan.refetch_required_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">reparse_cache_count: <span className="text-white">{plan.reparse_cache_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">repair_from_metadata_count: <span className="text-white">{plan.repair_from_metadata_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">schema_review_count: <span className="text-white">{plan.schema_review_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">manual_review_count: <span className="text-white">{plan.manual_review_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">no_action_count: <span className="text-white">{plan.no_action_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">estimated_http_request_count: <span className="text-white">{plan.estimated_http_request_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">estimated_runtime_seconds: <span className="text-white">{formatSeconds(plan.estimated_runtime_seconds)}</span></div>
              </div>

              <div className="grid md:grid-cols-2 gap-4 text-xs">
                <div className="border border-[#1e1e1e] rounded p-3 bg-[#0a0a0a]">
                  <div className="text-[#9ca3af] mb-1">p0_action_breakdown</div>
                  <div className="space-y-1">
                    {(plan.p0_action_breakdown || []).slice(0, 30).map((x, idx) => (
                      <div key={`ab-${idx}`} className="text-[#d1d5db]">{x.action || '-'} / {x.column}: {x.count}</div>
                    ))}
                  </div>
                </div>
                <div className="border border-[#1e1e1e] rounded p-3 bg-[#0a0a0a]">
                  <div className="text-[#9ca3af] mb-1">p0_reason_breakdown</div>
                  <div className="space-y-1">
                    {(plan.p0_reason_breakdown || []).slice(0, 30).map((x, idx) => (
                      <div key={`rb-${idx}`} className="text-[#d1d5db]">{x.reason || '-'} / {x.column}: {x.count}</div>
                    ))}
                  </div>
                </div>
              </div>
            </div>

            <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5 space-y-4">
              <h3 className="text-sm font-medium">Sample Targets (action grouped)</h3>
              {ACTION_ORDER.map((action) => {
                const items = groupedSamples[action] || []
                return (
                  <div key={action} className="space-y-2">
                    <div className="text-xs text-[#9ca3af]">{action} ({items.length})</div>
                    <div className="space-y-1">
                      {items.slice(0, 10).map((d, idx) => (
                        <div key={`${action}-${idx}-${d.race_id || '-'}-${d.horse_id || '-'}`} className="text-xs border border-[#1e1e1e] rounded px-3 py-2 bg-[#0a0a0a]">
                          <div className="text-white">race_id: {d.race_id || '-'} / horse_id: {d.horse_id || '-'}</div>
                          <div className="text-[#9ca3af]">column: {d.column} / reason: {d.reason} / priority: {d.priority}</div>
                          <div className="text-[#6b7280]">source_hint: {d.source_hint}</div>
                          <div className="text-[#6b7280]">recommended_next_action: {d.recommended_next_action}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                )
              })}
            </div>

            <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5 space-y-2">
              <h3 className="text-sm font-medium">Recommended Next Actions</h3>
              <ul className="list-disc pl-5 text-xs text-[#d1d5db] space-y-1">
                {(plan.recommended_next_actions || []).map((a, idx) => (
                  <li key={`next-${idx}`}>{a}</li>
                ))}
              </ul>
            </div>
          </>
        )}
      </main>
    </div>
  )
}
