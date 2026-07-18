'use client'

import Link from 'next/link'
import { useMemo, useState } from 'react'
import { Logo } from '@/components/Logo'
import { authFetch } from '@/lib/auth-fetch'
import {
  LiveValidationApiResponse,
  LiveValidationTarget,
  LiveValidationUrlType,
  validateLiveValidationApiResponse,
} from '@/lib/targeted-refetch-live-validation-contract'

const TARGETS: LiveValidationTarget[] = ['all', 'race', 'horse', 'result', 'pedigree', 'odds']
const URL_TYPES: LiveValidationUrlType[] = ['all', 'race-result', 'race-detail', 'horse-detail', 'pedigree']

type UiState = 'idle' | 'loading' | 'pass' | 'warn' | 'partial' | 'busy' | 'error'

export default function TargetedRefetchLiveValidationPage() {
  const [target, setTarget] = useState<LiveValidationTarget>('all')
  const [urlType, setUrlType] = useState<LiveValidationUrlType>('all')
  const [maxUrlsInput, setMaxUrlsInput] = useState('1')
  const [confirmed, setConfirmed] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [errorStatus, setErrorStatus] = useState<number | null>(null)
  const [response, setResponse] = useState<LiveValidationApiResponse | null>(null)

  const maxUrls = Number(maxUrlsInput)
  const maxUrlsValid = Number.isInteger(maxUrls) && maxUrls >= 1 && maxUrls <= 3
  const state: UiState = useMemo(() => {
    if (loading) return 'loading'
    if (error) return errorStatus === 409 || errorStatus === 429 ? 'busy' : 'error'
    if (!response) return 'idle'
    if (response.result.http_error_count > 0 || response.result.parse_error_count > 0) return 'partial'
    return response.result.verdict
  }, [loading, error, errorStatus, response])

  const submit = async () => {
    if (loading) return
    if (!maxUrlsValid || !confirmed) {
      setError(!maxUrlsValid ? 'max_urls must be an integer between 1 and 3' : '実外部HTTPの確認が必要です')
      setErrorStatus(400)
      setResponse(null)
      return
    }

    const requestBody = {
      target,
      url_type: urlType,
      max_urls: maxUrls,
      confirm_live_fetch: true as const,
    }
    setLoading(true)
    setError('')
    setErrorStatus(null)
    setResponse(null)

    try {
      const res = await authFetch('/api/scrape/live-validation', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody),
      })
      const payload = await res.json().catch(() => null)
      if (!res.ok) {
        const detail = payload && typeof payload === 'object' && typeof payload.detail === 'string'
          ? payload.detail
          : `HTTP ${res.status}`
        setErrorStatus(res.status)
        throw new Error(detail)
      }
      const parsed = validateLiveValidationApiResponse(payload, requestBody)
      if (!parsed.ok) {
        setErrorStatus(502)
        throw new Error(parsed.error)
      }
      setResponse(parsed.value)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'live validation failed')
      setResponse(null)
    } finally {
      setLoading(false)
    }
  }

  const result = response?.result ?? null

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <header className="border-b border-[#1e1e1e] px-6 py-4 flex items-center justify-between">
        <Logo href="/home" />
        <div className="flex items-center gap-4">
          <Link href="/data-collection" className="text-xs text-[#666] hover:text-white">データ取得へ戻る</Link>
          <span className="text-sm text-[#888]">Targeted Refetch Live Validation</span>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-8 space-y-5">
        <section className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5 space-y-4">
          <div>
            <h1 className="text-base font-medium">限定ライブ検証（Admin）</h1>
            <p className="mt-1 text-xs text-[#9ca3af]">Netkeibaへ最大3件の外部HTTPを逐次送信します。間隔は1秒以上です。</p>
          </div>
          <div data-testid="phase3d-safety-notice" className="rounded border border-[#92400e] bg-[#271503] px-3 py-3 text-xs text-[#fde68a] space-y-1">
            <div>DB repair / upsert / production table write は行いません。</div>
            <div>サーバー生成済み候補のみを読み取り検証し、ブラウザからNetkeibaへ直接接続しません。</div>
          </div>

          <div className="grid md:grid-cols-3 gap-3">
            <label className="text-xs text-[#9ca3af]">Target
              <select data-testid="phase3d-target" value={target} disabled={loading} onChange={e => setTarget(e.target.value as LiveValidationTarget)} className="mt-1 w-full px-3 py-2 bg-[#0a0a0a] border border-[#27272a] rounded">
                {TARGETS.map(value => <option key={value} value={value}>{value}</option>)}
              </select>
            </label>
            <label className="text-xs text-[#9ca3af]">URL type
              <select data-testid="phase3d-url-type" value={urlType} disabled={loading} onChange={e => setUrlType(e.target.value as LiveValidationUrlType)} className="mt-1 w-full px-3 py-2 bg-[#0a0a0a] border border-[#27272a] rounded">
                {URL_TYPES.map(value => <option key={value} value={value}>{value}</option>)}
              </select>
            </label>
            <label className="text-xs text-[#9ca3af]">max URLs (1–3)
              <input data-testid="phase3d-max-urls" type="number" min={1} max={3} value={maxUrlsInput} disabled={loading} onChange={e => setMaxUrlsInput(e.target.value)} className="mt-1 w-full px-3 py-2 bg-[#0a0a0a] border border-[#27272a] rounded" />
            </label>
          </div>

          <label className="flex items-start gap-2 text-xs text-[#d1d5db] cursor-pointer">
            <input data-testid="phase3d-confirm" type="checkbox" checked={confirmed} disabled={loading} onChange={e => setConfirmed(e.target.checked)} className="mt-0.5 accent-white" />
            <span>最大{maxUrlsValid ? maxUrls : 3}件の実外部HTTPを実行し、DBへの書込みを行わないことを確認しました。</span>
          </label>

          {!maxUrlsValid && <div data-testid="phase3d-input-error" role="alert" className="text-xs text-red-300">max_urls must be an integer between 1 and 3</div>}
          <div className="flex items-center gap-3">
            <button data-testid="phase3d-run" type="button" onClick={submit} disabled={loading || !maxUrlsValid || !confirmed} className={`px-4 py-2 rounded text-sm font-medium ${loading || !maxUrlsValid || !confirmed ? 'bg-[#222] text-[#555] cursor-not-allowed' : 'bg-white text-black hover:bg-[#eee]'}`}>
              {loading ? 'Validating...' : 'Run bounded live validation'}
            </button>
            <span data-testid="phase3d-state" className="text-xs text-[#9ca3af]">state: {state}</span>
          </div>

          {error && <div data-testid="phase3d-error" role="alert" className="rounded border border-red-900 bg-red-950/40 px-3 py-2 text-xs text-red-200">{error}</div>}
        </section>

        {result && (
          <>
            <section data-testid="phase3d-summary" className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5 space-y-3">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-medium">Validation Summary</h2>
                <span className={`text-xs px-2 py-1 rounded ${state === 'pass' ? 'bg-emerald-950 text-emerald-300' : state === 'partial' ? 'bg-amber-950 text-amber-300' : 'bg-slate-800 text-slate-300'}`}>{state}</span>
              </div>
              {result.attempted_url_count === 0 && <div data-testid="phase3d-zero-targets" className="text-xs text-[#93c5fd]">対象URLは0件でした。外部HTTP・DB書込みはいずれも実行されていません。</div>}
              {state === 'partial' && <div data-testid="phase3d-partial" className="text-xs text-[#facc15]">一部URLでHTTPまたはparse失敗がありました。API成功と検証成功は別判定です。</div>}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
                <div className="border border-[#27272a] rounded p-2">attempted <b>{result.attempted_url_count}</b></div>
                <div className="border border-[#27272a] rounded p-2">HTTP success <b>{result.http_success_count}</b></div>
                <div className="border border-[#27272a] rounded p-2">HTTP error <b>{result.http_error_count}</b></div>
                <div className="border border-[#27272a] rounded p-2">parse success <b>{result.parse_success_count}</b></div>
                <div className="border border-[#27272a] rounded p-2">parse failed <b>{result.parse_error_count}</b></div>
                <div className="border border-[#27272a] rounded p-2">would fix <b>{result.would_fix_count}</b></div>
                <div className="border border-[#27272a] rounded p-2">no downgrade <b>{result.no_downgrade_count}</b></div>
                <div className="border border-[#27272a] rounded p-2">elapsed <b>{result.elapsed_seconds}s</b></div>
              </div>
            </section>

            <section data-testid="phase3d-samples" className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5 space-y-2">
              <h2 className="text-sm font-medium">Observed URLs ({result.sample_results.length})</h2>
              {result.sample_results.length === 0 ? <div className="text-xs text-[#666]">none</div> : result.sample_results.map((sample, index) => (
                <div key={`${sample.url}-${index}`} className="border border-[#27272a] rounded p-3 text-xs space-y-1">
                  <div className="break-all text-[#d1d5db]">{sample.url}</div>
                  <div className="text-[#9ca3af]">HTTP {sample.http_status} / {sample.parse_status} / {sample.action}</div>
                  <div className="text-[#6b7280]">would_fix: {sample.would_fix_columns.join(', ') || 'none'}</div>
                </div>
              ))}
            </section>

            <section data-testid="phase3d-runtime-policy" className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5 space-y-2 text-xs">
              <h2 className="text-sm font-medium">Bounded Runtime / Safety</h2>
              <div>parallelism: {result.rate_limit_policy.parallelism}</div>
              <div>min interval: {result.rate_limit_policy.min_interval_sec}s</div>
              <div>per request timeout: {result.rate_limit_policy.per_request_timeout_sec}s</div>
              <div>total timeout: {result.rate_limit_policy.total_timeout_sec}s</div>
              <div>max body: {result.rate_limit_policy.max_body_bytes} bytes</div>
              <div className="text-emerald-300">no_db_write=true / redirects_disabled=true / bounded_total_runtime=true</div>
            </section>
          </>
        )}
      </main>
    </div>
  )
}
