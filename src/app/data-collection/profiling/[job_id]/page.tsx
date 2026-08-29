'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { AdminOnly } from '@/components/AdminOnly'
import { Logo } from '@/components/Logo'
import { authFetch } from '@/lib/auth-fetch'

const JOB_ID_PATTERN = /^[0-9a-f]{10}$/
const MAX_REPORT_HTML_CHARS = 20_000_000
const REPORT_CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; media-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'"

function isolateReportHtml(html: string): string {
  const meta = `<meta http-equiv="Content-Security-Policy" content="${REPORT_CSP}">`
  return /<head(?:\s[^>]*)?>/i.test(html)
    ? html.replace(/<head(?:\s[^>]*)?>/i, match => `${match}${meta}`)
    : `${meta}${html}`
}

async function responseDetail(response: Response): Promise<string> {
  try {
    const payload = await response.clone().json()
    if (payload && typeof payload.detail === 'string' && payload.detail.length <= 300) {
      return payload.detail
    }
  } catch {
    // HTML/plain-text failures use the bounded status fallback below.
  }
  return `レポートを取得できませんでした（HTTP ${response.status}）`
}

function ProfilingReportViewer() {
  const params = useParams<{ job_id: string }>()
  const jobId = typeof params.job_id === 'string' ? params.job_id : ''
  const [html, setHtml] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const validJobId = JOB_ID_PATTERN.test(jobId)

  const loadReport = useCallback(async () => {
    if (!validJobId) {
      setHtml('')
      setError('プロファイリングJob IDの形式が不正です。')
      return
    }

    setLoading(true)
    setError('')
    try {
      const response = await authFetch(`/api/profiling/html/${jobId}`, {
        cache: 'no-store',
        signal: AbortSignal.timeout(30_000),
      })
      if (!response.ok) {
        throw new Error(await responseDetail(response))
      }
      const contentType = response.headers.get('content-type') || ''
      if (!contentType.toLowerCase().includes('text/html')) {
        throw new Error('レポート応答の形式がHTMLではありません。')
      }
      const body = await response.text()
      if (!body.trim()) {
        throw new Error('レポートHTMLが空です。')
      }
      if (body.length > MAX_REPORT_HTML_CHARS) {
        throw new Error('レポートHTMLが表示上限を超えています。')
      }
      setHtml(isolateReportHtml(body))
    } catch (cause) {
      setHtml('')
      setError(cause instanceof Error ? cause.message : 'レポート取得に失敗しました。')
    } finally {
      setLoading(false)
    }
  }, [jobId, validJobId])

  useEffect(() => {
    void loadReport()
  }, [loadReport])

  const downloadUrl = useMemo(() => {
    if (!html || typeof URL.createObjectURL !== 'function') return ''
    const url = URL.createObjectURL(new Blob([html], { type: 'text/html;charset=utf-8' }))
    return url
  }, [html])

  useEffect(() => {
    return () => {
      if (downloadUrl && typeof URL.revokeObjectURL === 'function') URL.revokeObjectURL(downloadUrl)
    }
  }, [downloadUrl])

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <header className="border-b border-[#1e1e1e] px-6 py-4 flex items-center justify-between">
        <Logo href="/home" />
        <Link href="/data-collection" className="text-xs text-[#777] hover:text-white transition-colors">
          ← データ取得へ戻る
        </Link>
      </header>

      <main className="max-w-[1500px] mx-auto px-6 py-8 space-y-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 mb-2">
              <span className="text-[10px] uppercase tracking-[0.18em] text-[#f59e0b] border border-[#78350f] rounded px-2 py-0.5">
                Admin only
              </span>
              <span className="text-xs text-[#555]">Job {validJobId ? jobId : 'invalid'}</span>
            </div>
            <h1 className="text-xl font-semibold">特徴量プロファイリングレポート</h1>
            <p className="text-xs text-[#666] mt-1">
              認証付きAPIから取得したHTMLを、分離されたsandbox内で表示しています。
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void loadReport()}
              disabled={loading || !validJobId}
              className="px-3 py-2 rounded border border-[#333] text-xs text-[#bbb] hover:text-white hover:border-[#555] disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {loading ? '取得中...' : '再読込'}
            </button>
            {downloadUrl && (
              <a
                href={downloadUrl}
                download={`profiling-${jobId}.html`}
                className="px-3 py-2 rounded bg-white text-black text-xs font-medium hover:bg-[#ddd]"
              >
                HTMLを保存
              </a>
            )}
          </div>
        </div>

        {loading && !html && (
          <div role="status" className="border border-[#222] bg-[#111] rounded-lg p-6 text-sm text-[#888]">
            プロファイリングレポートを取得しています...
          </div>
        )}

        {error && (
          <div role="alert" className="border border-[#7f1d1d] bg-[#1f1010] rounded-lg p-5">
            <p className="text-sm text-[#fca5a5]">{error}</p>
            <p className="text-xs text-[#8f6666] mt-2">
              レポートはFastAPIプロセス内の一時Jobです。サーバー再起動後は再生成が必要です。
            </p>
          </div>
        )}

        {html && (
          <div className="border border-[#282828] rounded-lg overflow-hidden bg-white">
            <iframe
              title={`特徴量プロファイリング ${jobId}`}
              srcDoc={html}
              sandbox="allow-scripts"
              referrerPolicy="no-referrer"
              className="w-full min-h-[78vh] bg-white"
            />
          </div>
        )}
      </main>
    </div>
  )
}

export default function ProfilingReportPage() {
  return (
    <AdminOnly>
      <ProfilingReportViewer />
    </AdminOnly>
  )
}
