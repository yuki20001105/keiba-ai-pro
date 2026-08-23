'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { Logo } from '@/components/Logo'
import { PremiumRequiredNotice } from '@/components/PremiumRequiredNotice'
import { useAuth } from '@/contexts/AuthContext'
import { authFetch } from '@/lib/auth-fetch'

type UiState = 'pass' | 'warn' | 'fail'
type ReportType = 'feature_analysis' | 'smoke_suite_summary' | 'production_readiness_summary' | 'model_evaluation_summary'

type ReportTypeMeta = {
  id: ReportType
  label: string
  description: string
}

type ConfigResponse = {
  success: boolean
  state: UiState
  code: string
  report_types: ReportTypeMeta[]
  notion_configured: boolean
  message: string
}

type PreviewResponse = {
  success: boolean
  state: UiState
  code: string
  report_type: ReportType
  title: string
  source: string
  preview: string
  generated_at: string
  error?: string
}

type SendResponse = {
  success: boolean
  state: UiState
  code: string
  report_type: ReportType
  title: string
  source: string
  notion_page_id?: string
  notion_url?: string | null
  generated_at?: string
  error?: string
}

function badgeStyle(state: UiState): string {
  if (state === 'pass') return 'text-emerald-300 border-emerald-800/50'
  if (state === 'warn') return 'text-yellow-300 border-yellow-800/50'
  return 'text-red-300 border-red-800/50'
}

export default function NotionReportPage() {
  const { isPremium, isAdmin, loading: authLoading } = useAuth()
  const canRun = isAdmin || isPremium

  const [reportTypes, setReportTypes] = useState<ReportTypeMeta[]>([])
  const [selectedType, setSelectedType] = useState<ReportType>('feature_analysis')
  const [configState, setConfigState] = useState<UiState>('warn')
  const [configCode, setConfigCode] = useState('loading')
  const [configMessage, setConfigMessage] = useState('初期化中...')
  const [notionConfigured, setNotionConfigured] = useState(false)

  const [previewRes, setPreviewRes] = useState<PreviewResponse | null>(null)
  const [sendRes, setSendRes] = useState<SendResponse | null>(null)
  const [loadingPreview, setLoadingPreview] = useState(false)
  const [loadingSend, setLoadingSend] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!canRun || authLoading) return
    let alive = true
    const loadConfig = async () => {
      try {
        const response = await authFetch('/api/notion-report', {
          method: 'GET',
          signal: AbortSignal.timeout(30000),
        })
        const data = await response.json() as ConfigResponse
        if (!alive) return

        if (!response.ok || !data.success) {
          throw new Error(data?.message || data?.code || `HTTP ${response.status}`)
        }

        setReportTypes(data.report_types)
        if (data.report_types.length > 0) {
          setSelectedType(data.report_types[0].id)
        }
        setNotionConfigured(Boolean(data.notion_configured))
        setConfigState(data.state)
        setConfigCode(data.code)
        setConfigMessage(data.message)
      } catch (e: unknown) {
        if (!alive) return
        setConfigState('fail')
        setConfigCode('init-failed')
        setConfigMessage(e instanceof Error ? e.message : '初期化に失敗しました')
      }
    }
    void loadConfig()
    return () => { alive = false }
  }, [canRun, authLoading])

  const selectedMeta = useMemo(
    () => reportTypes.find((r) => r.id === selectedType),
    [reportTypes, selectedType],
  )

  const loadPreview = async () => {
    setLoadingPreview(true)
    setError('')
    setSendRes(null)
    try {
      const response = await authFetch('/api/notion-report', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'preview', reportType: selectedType }),
        signal: AbortSignal.timeout(120000),
      })
      const data = await response.json() as PreviewResponse
      if (!response.ok || !data.success) {
        throw new Error(data?.error || data?.code || `HTTP ${response.status}`)
      }
      setPreviewRes(data)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'preview 生成に失敗しました')
      setPreviewRes(null)
    } finally {
      setLoadingPreview(false)
    }
  }

  const sendToNotion = async () => {
    setLoadingSend(true)
    setError('')
    try {
      const response = await authFetch('/api/notion-report', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'send', reportType: selectedType }),
        signal: AbortSignal.timeout(120000),
      })
      const data = await response.json() as SendResponse
      setSendRes(data)
      if (!response.ok || !data.success) {
        throw new Error(data?.error || data?.code || `HTTP ${response.status}`)
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Notion 送信に失敗しました')
    } finally {
      setLoadingSend(false)
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
          <span className="text-sm text-[#888]">Notion出力</span>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-8 space-y-5">
        <div className="bg-[#111] border border-[#1e1e1e] rounded-lg p-5">
          <h1 className="text-lg font-semibold">Notion Report Output</h1>
          <p className="text-xs text-[#666] mt-2">
            Premium/Admin 向けに、allowlistされたレポートのみ preview -&gt; send を実行します。Notion token は server-side env のみで扱い、UI/レスポンスに実値は表示しません。
          </p>

          {!authLoading && !canRun && (
            <div className="mt-4">
              <PremiumRequiredNotice
                title="Notion出力は Premium または Admin 専用です"
                message="非権限ユーザーは実行できません。API 直叩きでも 403 を返します。"
              />
            </div>
          )}

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <div className="min-w-[280px]">
              <label className="block text-xs text-[#777] mb-1">レポート種別</label>
              <select
                value={selectedType}
                onChange={(e) => setSelectedType(e.target.value as ReportType)}
                disabled={!canRun || reportTypes.length === 0}
                className="w-full bg-[#0d0d0d] border border-[#2b2b2b] rounded px-3 py-2 text-sm text-white disabled:opacity-50"
              >
                {reportTypes.map((rt) => (
                  <option key={rt.id} value={rt.id}>{rt.label}</option>
                ))}
              </select>
              {selectedMeta && <p className="mt-1 text-[11px] text-[#666]">{selectedMeta.description}</p>}
            </div>

            <button
              onClick={loadPreview}
              disabled={!canRun || loadingPreview}
              className="px-4 py-2.5 rounded bg-white text-black text-sm font-medium hover:bg-[#eee] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {loadingPreview ? 'preview 生成中...' : 'preview 生成'}
            </button>

            <button
              onClick={sendToNotion}
              disabled={!canRun || loadingSend || !previewRes}
              className="px-4 py-2.5 rounded bg-[#1f5eff] text-white text-sm font-medium hover:bg-[#3c71ff] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {loadingSend ? '送信中...' : 'Notion へ送信'}
            </button>
          </div>

          <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-3">
            <div className={`border rounded p-3 ${badgeStyle(configState)}`}>
              <p className="text-xs font-medium">config status: {configCode}</p>
              <p className="text-[11px] mt-1 text-[#b8b8b8]">{configMessage}</p>
            </div>
            <div className={`border rounded p-3 ${badgeStyle(notionConfigured ? 'pass' : 'warn')}`}>
              <p className="text-xs font-medium">NOTION env: {notionConfigured ? 'configured' : 'config-missing'}</p>
              <p className="text-[11px] mt-1 text-[#b8b8b8]">preview は常に可能 / send は env 設定時のみ実行</p>
            </div>
          </div>

          {error && (
            <div className="mt-4 p-3 bg-red-900/20 border border-red-800 rounded text-sm text-red-300">
              {error}
            </div>
          )}
        </div>

        {previewRes && (
          <div className={`bg-[#111] border rounded-lg p-4 ${badgeStyle(previewRes.state)}`}>
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <h2 className="text-sm font-medium">Preview: {previewRes.title}</h2>
              <span className="text-[10px] border border-[#333] rounded px-2 py-0.5">{previewRes.state.toUpperCase()} / {previewRes.code}</span>
            </div>
            <p className="text-[11px] text-[#888] mt-1">source: {previewRes.source}</p>
            <pre className="mt-3 text-[11px] text-[#d0d0d0] bg-[#0f0f0f] border border-[#202020] rounded p-3 overflow-x-auto max-h-[460px]">
              {previewRes.preview}
            </pre>
          </div>
        )}

        {sendRes && (
          <div className={`bg-[#111] border rounded-lg p-4 ${badgeStyle(sendRes.state)}`}>
            <h2 className="text-sm font-medium">Send Result</h2>
            <p className="text-xs mt-2">state: {sendRes.state} / code: {sendRes.code}</p>
            <p className="text-xs mt-1">title: {sendRes.title}</p>
            <p className="text-xs mt-1">source: {sendRes.source}</p>
            {sendRes.notion_url && (
              <a
                href={sendRes.notion_url}
                target="_blank"
                rel="noreferrer"
                className="inline-block mt-2 text-xs text-blue-300 hover:text-blue-200 underline"
              >
                Notion ページを開く
              </a>
            )}
            {sendRes.error && <p className="text-xs mt-2 text-red-300">{sendRes.error}</p>}
          </div>
        )}
      </main>
    </div>
  )
}
