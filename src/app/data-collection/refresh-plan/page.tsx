'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import { Logo } from '@/components/Logo'
import { authFetch } from '@/lib/auth-fetch'

type Target = 'all' | 'race' | 'horse' | 'result' | 'pedigree' | 'odds'
type Policy = 'repair-missing' | 'refresh-stale' | 'force-refresh' | 'reparse-cache' | 'skip-existing' | 'dry-run'

type Decision = {
  key: string
  action: string
  reason: string
  quality_score?: number
  missing_fields?: string[]
  parser_version?: string | null
  fetched_at?: string | null
}

type PlanPayload = {
  policy: string
  target: string
  start_date?: string | null
  end_date?: string | null
  target_count: number
  existing_count: number
  missing_count: number
  skip_count: number
  repair_count: number
  reparse_count: number
  refetch_count: number
  update_candidate_count: number
  quarantine_count: number
  no_downgrade_skip_count: number
  estimated_http_request_count: number
  estimated_runtime: number
  verdict: string
  warnings?: string[]
  reasons?: Record<string, number>
  decisions: Decision[]
}

const TARGET_OPTIONS: Target[] = ['all', 'race', 'horse', 'result', 'pedigree', 'odds']
const POLICY_OPTIONS: Policy[] = ['repair-missing', 'refresh-stale', 'force-refresh', 'reparse-cache', 'skip-existing', 'dry-run']

function formatSeconds(v: number): string {
  const s = Math.max(0, Math.round(v))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  if (h > 0) return `${h}h ${m}m ${sec}s`
  if (m > 0) return `${m}m ${sec}s`
  return `${sec}s`
}

export default function RefreshPlanPage() {
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [target, setTarget] = useState<Target>('all')
  const [policy, setPolicy] = useState<Policy>('repair-missing')
  const [staleDays, setStaleDays] = useState(30)
  const [currentParserVersion, setCurrentParserVersion] = useState('2.0.0')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [plan, setPlan] = useState<PlanPayload | null>(null)

  const grouped = useMemo(() => {
    if (!plan) return {}
    const out: Record<string, Decision[]> = {
      skip: [],
      repair: [],
      'reparse-cache': [],
      refetch: [],
      quarantine: [],
      'no-downgrade-skip': [],
      'update-candidate': [],
    }
    for (const d of plan.decisions || []) {
      const key = d.action || 'skip'
      if (!out[key]) out[key] = []
      out[key].push(d)
    }
    return out
  }, [plan])

  const handlePreview = async () => {
    setLoading(true)
    setError('')
    try {
      const payload = {
        startDate: startDate || undefined,
        endDate: endDate || undefined,
        target,
        policy,
        staleDays,
        currentParserVersion,
      }
      const res = await authFetch('/api/scrape/refresh-plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(typeof data?.error === 'string' ? data.error : `HTTP ${res.status}`)
      }
      setPlan((data?.plan || null) as PlanPayload | null)
    } catch (e: unknown) {
      setPlan(null)
      setError(e instanceof Error ? e.message : 'refresh plan request failed')
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
          <Link href="/data-collection/p0-repair-plan" className="text-xs text-[#666] hover:text-white transition-colors">P0 Repair Plan</Link>
          <span className="text-sm text-[#888]">Refresh Plan (Dry-run)</span>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-8 space-y-5">
        <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5 space-y-3">
          <h2 className="text-sm font-medium">Scrape Refresh Plan Preview</h2>
          <div className="rounded border border-[#334155] bg-[#0b1220] px-3 py-2 text-xs text-[#bfdbfe]">
            この画面は dry-run preview のみです。実DB更新・実スクレイピング・upsert・force refresh 実行は行いません。
          </div>

          <div className="grid md:grid-cols-3 gap-3">
            <label className="text-xs text-[#777]">Start Date
              <input value={startDate} onChange={(e) => setStartDate(e.target.value)} placeholder="YYYYMMDD or YYYY-MM-DD" className="mt-1 w-full px-3 py-2 bg-[#0a0a0a] border border-[#1e1e1e] rounded" />
            </label>
            <label className="text-xs text-[#777]">End Date
              <input value={endDate} onChange={(e) => setEndDate(e.target.value)} placeholder="YYYYMMDD or YYYY-MM-DD" className="mt-1 w-full px-3 py-2 bg-[#0a0a0a] border border-[#1e1e1e] rounded" />
            </label>
            <label className="text-xs text-[#777]">Target
              <select value={target} onChange={(e) => setTarget(e.target.value as Target)} className="mt-1 w-full px-3 py-2 bg-[#0a0a0a] border border-[#1e1e1e] rounded">
                {TARGET_OPTIONS.map((x) => <option key={x} value={x}>{x}</option>)}
              </select>
            </label>

            <label className="text-xs text-[#777]">Policy
              <select value={policy} onChange={(e) => setPolicy(e.target.value as Policy)} className="mt-1 w-full px-3 py-2 bg-[#0a0a0a] border border-[#1e1e1e] rounded">
                {POLICY_OPTIONS.map((x) => <option key={x} value={x}>{x}</option>)}
              </select>
            </label>
            <label className="text-xs text-[#777]">Stale Days
              <input type="number" min={1} max={3650} value={staleDays} onChange={(e) => setStaleDays(Number(e.target.value || 30))} className="mt-1 w-full px-3 py-2 bg-[#0a0a0a] border border-[#1e1e1e] rounded" />
            </label>
            <label className="text-xs text-[#777]">Current Parser Version
              <input value={currentParserVersion} onChange={(e) => setCurrentParserVersion(e.target.value)} className="mt-1 w-full px-3 py-2 bg-[#0a0a0a] border border-[#1e1e1e] rounded" />
            </label>
          </div>

          <div className="flex items-center gap-3">
            <button onClick={handlePreview} disabled={loading} className={`px-4 py-2 rounded text-sm font-medium ${loading ? 'bg-[#222] text-[#555]' : 'bg-white text-black hover:bg-[#eee]'}`}>
              {loading ? 'Planning...' : 'Generate Dry-run Plan'}
            </button>
            <button disabled className="px-4 py-2 rounded text-sm font-medium bg-[#222] text-[#555] cursor-not-allowed" title="Not implemented in this phase">
              Execute Refresh (Disabled)
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
                <div className="border border-[#1e1e1e] rounded p-2">policy: <span className="text-white">{plan.policy}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">target: <span className="text-white">{plan.target}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">date range: <span className="text-white">{plan.start_date || '-'} ~ {plan.end_date || '-'}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">target_count: <span className="text-white">{plan.target_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">existing_count: <span className="text-white">{plan.existing_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">missing_count: <span className="text-white">{plan.missing_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">skip_count: <span className="text-white">{plan.skip_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">repair_count: <span className="text-white">{plan.repair_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">reparse_count: <span className="text-white">{plan.reparse_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">refetch_count: <span className="text-white">{plan.refetch_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">update_candidate_count: <span className="text-white">{plan.update_candidate_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">quarantine_count: <span className="text-white">{plan.quarantine_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">no_downgrade_skip_count: <span className="text-white">{plan.no_downgrade_skip_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">estimated_http_request_count: <span className="text-white">{plan.estimated_http_request_count}</span></div>
                <div className="border border-[#1e1e1e] rounded p-2">estimated_runtime: <span className="text-white">{formatSeconds(plan.estimated_runtime || 0)}</span></div>
              </div>
              {!!plan.warnings?.length && (
                <div className="text-xs text-[#facc15]">warnings: {plan.warnings.join(', ')}</div>
              )}
            </div>

            <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5 space-y-4">
              <h3 className="text-sm font-medium">Decision Samples (action grouped)</h3>
              {Object.entries(grouped).map(([action, items]) => (
                <div key={action} className="space-y-2">
                  <div className="text-xs text-[#9ca3af]">{action} ({items.length})</div>
                  <div className="space-y-1">
                    {items.slice(0, 8).map((d, idx) => (
                      <div key={`${action}-${idx}-${d.key}`} className="text-xs border border-[#1e1e1e] rounded px-3 py-2 bg-[#0a0a0a]">
                        <div className="text-white">{d.key}</div>
                        <div className="text-[#9ca3af]">reason: {d.reason}</div>
                        <div className="text-[#6b7280]">quality_score: {d.quality_score ?? '-'} / missing_fields: {(d.missing_fields || []).join(', ') || '-'}</div>
                        <div className="text-[#6b7280]">parser_version: {d.parser_version || '-'} / fetched_at: {d.fetched_at || '-'}</div>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </main>
    </div>
  )
}
