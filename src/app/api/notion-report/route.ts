import { NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'
import { createClient } from '@supabase/supabase-js'

export const runtime = 'nodejs'
export const maxDuration = 180

type UiState = 'pass' | 'warn' | 'fail'
type ReportType = 'feature_analysis' | 'smoke_suite_summary' | 'production_readiness_summary' | 'model_evaluation_summary'

type AuthzResult = {
  ok: boolean
  status: 200 | 401 | 403 | 503
  error?: string
}

type BuiltReport = {
  reportType: ReportType
  title: string
  preview: string
  state: UiState
  source: string
  code: string
}

const PROJECT_ROOT = process.cwd()
const REPORTS_DIR = path.join(PROJECT_ROOT, 'reports')
const DOCS_REPORTS_DIR = path.join(PROJECT_ROOT, 'docs', 'reports')
const NOTION_VERSION = '2022-06-28'
const NOTION_TOKEN_PREFIX = ['nt', 'n_'].join('')

const REPORT_TYPE_META: Array<{ id: ReportType; label: string; description: string }> = [
  { id: 'feature_analysis', label: 'feature analysis', description: '特徴量分析レポート（markdown/json）' },
  { id: 'smoke_suite_summary', label: 'smoke suite summary', description: 'smoke suite 集約結果' },
  { id: 'production_readiness_summary', label: 'production readiness summary', description: '本番前チェック要約（read-only）' },
  { id: 'model_evaluation_summary', label: 'model evaluation summary', description: 'モデル評価メトリクス要約' },
]

function sanitizeText(raw: string, maxChars = 500): string {
  if (!raw) return ''
  const tail = raw.length > maxChars ? raw.slice(raw.length - maxChars) : raw
  return tail
    .replace(/(sb_secret_[A-Za-z0-9_-]+)/g, '[REDACTED_SECRET]')
    .replace(/(sb_publishable_[A-Za-z0-9_-]+)/g, '[REDACTED_PUBLISHABLE]')
    .replace(new RegExp(`(${NOTION_TOKEN_PREFIX}[A-Za-z0-9_-]+)`, 'g'), '[REDACTED_NOTION]')
}

function readJson(filePath: string): Record<string, unknown> | null {
  try {
    if (!fs.existsSync(filePath)) return null
    const raw = fs.readFileSync(filePath, 'utf-8')
    const parsed = JSON.parse(raw)
    return typeof parsed === 'object' && parsed != null ? parsed as Record<string, unknown> : null
  } catch {
    return null
  }
}

function readText(filePath: string): string | null {
  try {
    if (!fs.existsSync(filePath)) return null
    return fs.readFileSync(filePath, 'utf-8')
  } catch {
    return null
  }
}

function toPreview(raw: string, maxChars = 12000): string {
  const cleaned = sanitizeText(raw, Math.max(maxChars, raw.length))
  return cleaned.length > maxChars ? `${cleaned.slice(0, maxChars)}\n\n... (truncated)` : cleaned
}

function pickLatestIterMetricsFile(): string | null {
  try {
    if (!fs.existsSync(DOCS_REPORTS_DIR)) return null
    const names = fs.readdirSync(DOCS_REPORTS_DIR)
    const candidates = names.filter((n) => /^iter_\d+_metrics\.json$/i.test(n)).sort()
    if (candidates.length === 0) return null
    return path.join(DOCS_REPORTS_DIR, candidates[candidates.length - 1])
  } catch {
    return null
  }
}

function buildFeatureAnalysisReport(): BuiltReport {
  const mdPath = path.join(DOCS_REPORTS_DIR, 'feature_llm_report.md')
  const jsonPath = path.join(DOCS_REPORTS_DIR, 'feature_analysis.json')
  const md = readText(mdPath)
  if (md) {
    return {
      reportType: 'feature_analysis',
      title: 'Feature Analysis Report',
      preview: toPreview(md),
      state: 'pass',
      source: 'docs/reports/feature_llm_report.md',
      code: 'preview-ready',
    }
  }

  const report = readJson(jsonPath)
  if (report) {
    const keys = Object.keys(report)
    const markdown = [
      '# Feature Analysis Summary',
      '',
      '- source: docs/reports/feature_analysis.json',
      `- keys: ${keys.length}`,
      '',
      '```json',
      JSON.stringify(report, null, 2).slice(0, 6000),
      '```',
    ].join('\n')

    return {
      reportType: 'feature_analysis',
      title: 'Feature Analysis Summary',
      preview: toPreview(markdown),
      state: 'warn',
      source: 'docs/reports/feature_analysis.json',
      code: 'fallback-json',
    }
  }

  return {
    reportType: 'feature_analysis',
    title: 'Feature Analysis Summary',
    preview: toPreview('# Feature Analysis Summary\n\nNo report file found. Generate report first.'),
    state: 'warn',
    source: 'missing',
    code: 'report-missing',
  }
}

function buildSmokeSuiteSummaryReport(): BuiltReport {
  const jsonPath = path.join(REPORTS_DIR, 'keiba_smoke_suite_result.json')
  const report = readJson(jsonPath)
  if (!report) {
    return {
      reportType: 'smoke_suite_summary',
      title: 'Smoke Suite Summary',
      preview: toPreview('# Smoke Suite Summary\n\nNo smoke suite report found. Run scripts/run_keiba_smoke_suite.py first.'),
      state: 'warn',
      source: 'missing',
      code: 'report-missing',
    }
  }

  const summary = String(report.summary ?? 'unknown')
  const success = String(report.success ?? 'unknown')
  const generatedAt = String(report.generated_at ?? report.timestamp ?? new Date().toISOString())
  const steps = report.steps && typeof report.steps === 'object' ? report.steps as Record<string, unknown> : {}
  const stepLines = Object.entries(steps).slice(0, 12).map(([k, v]) => {
    const state = v && typeof v === 'object' ? String((v as Record<string, unknown>).state ?? (v as Record<string, unknown>).summary ?? 'unknown') : String(v)
    return `- ${k}: ${state}`
  })

  const markdown = [
    '# Smoke Suite Summary',
    '',
    `- generated_at: ${generatedAt}`,
    `- success: ${success}`,
    `- summary: ${summary}`,
    '',
    '## Steps',
    ...stepLines,
  ].join('\n')

  return {
    reportType: 'smoke_suite_summary',
    title: 'Smoke Suite Summary',
    preview: toPreview(markdown),
    state: summary === 'fail' ? 'fail' : summary === 'warn' ? 'warn' : 'pass',
    source: 'reports/keiba_smoke_suite_result.json',
    code: 'preview-ready',
  }
}

function buildProductionReadinessSummaryReport(): BuiltReport {
  const smoke = readJson(path.join(REPORTS_DIR, 'keiba_smoke_suite_result.json'))
  const smokeSummary = String(smoke?.summary ?? 'unknown')
  const markdown = [
    '# Production Readiness Summary',
    '',
    `- generated_at: ${new Date().toISOString()}`,
    '- source: UI route /api/production-readiness (read-only checks)',
    `- latest_smoke_summary: ${smokeSummary}`,
    '',
    '## Notes',
    '- production/base table write is prohibited',
    '- sandbox write-readback is out of default readiness scope',
    '- secrets are never included in this report body',
  ].join('\n')

  return {
    reportType: 'production_readiness_summary',
    title: 'Production Readiness Summary',
    preview: toPreview(markdown),
    state: smoke ? (smokeSummary === 'fail' ? 'fail' : smokeSummary === 'warn' ? 'warn' : 'pass') : 'warn',
    source: smoke ? 'reports/keiba_smoke_suite_result.json' : 'derived-template',
    code: smoke ? 'preview-ready' : 'derived-without-latest-readiness-json',
  }
}

function buildModelEvaluationSummaryReport(): BuiltReport {
  const metricsPath = pickLatestIterMetricsFile()
  if (!metricsPath) {
    return {
      reportType: 'model_evaluation_summary',
      title: 'Model Evaluation Summary',
      preview: toPreview('# Model Evaluation Summary\n\nNo iter_*_metrics.json found under docs/reports.'),
      state: 'warn',
      source: 'missing',
      code: 'report-missing',
    }
  }

  const report = readJson(metricsPath)
  if (!report) {
    return {
      reportType: 'model_evaluation_summary',
      title: 'Model Evaluation Summary',
      preview: toPreview('# Model Evaluation Summary\n\nMetrics file exists but JSON parse failed.'),
      state: 'fail',
      source: path.relative(PROJECT_ROOT, metricsPath).replace(/\\/g, '/'),
      code: 'json-parse-failed',
    }
  }

  const tm = report.train_metrics && typeof report.train_metrics === 'object'
    ? report.train_metrics as Record<string, unknown>
    : {}
  const recommendations = Array.isArray(report.recommendations) ? report.recommendations : []

  const markdown = [
    '# Model Evaluation Summary',
    '',
    `- source: ${path.relative(PROJECT_ROOT, metricsPath).replace(/\\/g, '/')}`,
    `- iteration: ${String(report.iteration ?? 'unknown')}`,
    `- timestamp: ${String(report.timestamp ?? 'unknown')}`,
    '',
    '## Train Metrics',
    `- model_id: ${String(tm.model_id ?? 'unknown')}`,
    `- auc: ${String(tm.auc ?? 'unknown')}`,
    `- cv_auc_mean: ${String(tm.cv_auc_mean ?? 'unknown')}`,
    `- cv_auc_std: ${String(tm.cv_auc_std ?? 'unknown')}`,
    `- logloss: ${String(tm.logloss ?? 'unknown')}`,
    `- feature_count: ${String(tm.feature_count ?? 'unknown')}`,
    '',
    `## Recommendations (${recommendations.length})`,
    ...recommendations.slice(0, 5).map((r) => `- ${String(r).slice(0, 240)}`),
  ].join('\n')

  return {
    reportType: 'model_evaluation_summary',
    title: 'Model Evaluation Summary',
    preview: toPreview(markdown),
    state: 'pass',
    source: path.relative(PROJECT_ROOT, metricsPath).replace(/\\/g, '/'),
    code: 'preview-ready',
  }
}

function buildReport(reportType: ReportType): BuiltReport {
  switch (reportType) {
    case 'feature_analysis':
      return buildFeatureAnalysisReport()
    case 'smoke_suite_summary':
      return buildSmokeSuiteSummaryReport()
    case 'production_readiness_summary':
      return buildProductionReadinessSummaryReport()
    case 'model_evaluation_summary':
      return buildModelEvaluationSummaryReport()
    default:
      return {
        reportType,
        title: 'Unknown report',
        preview: toPreview('# Unknown report type'),
        state: 'fail',
        source: 'n/a',
        code: 'invalid-report-type',
      }
  }
}

function chunkText(text: string, size = 1800): string[] {
  const out: string[] = []
  for (let i = 0; i < text.length; i += size) {
    out.push(text.slice(i, i + size))
  }
  return out.length > 0 ? out : ['']
}

async function appendMarkdownToNotion(parentPageId: string, title: string, body: string, token: string): Promise<{ pageId: string; url: string }> {
  const createRes = await fetch('https://api.notion.com/v1/pages', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Notion-Version': NOTION_VERSION,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      parent: { page_id: parentPageId },
      properties: {
        title: {
          title: [{ type: 'text', text: { content: title.slice(0, 100) } }],
        },
      },
    }),
  })

  if (!createRes.ok) {
    throw new Error(`Notion page create failed (${createRes.status})`)
  }

  const createJson = await createRes.json() as Record<string, unknown>
  const pageId = String(createJson.id ?? '')
  const notionUrl = String(createJson.url ?? '')
  if (!pageId) {
    throw new Error('Notion page id is missing')
  }

  const children = chunkText(body).slice(0, 60).map((part) => ({
    object: 'block',
    type: 'paragraph',
    paragraph: {
      rich_text: [{ type: 'text', text: { content: part } }],
    },
  }))

  const appendRes = await fetch(`https://api.notion.com/v1/blocks/${pageId}/children`, {
    method: 'PATCH',
    headers: {
      Authorization: `Bearer ${token}`,
      'Notion-Version': NOTION_VERSION,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ children }),
  })

  if (!appendRes.ok) {
    throw new Error(`Notion append failed (${appendRes.status})`)
  }

  return { pageId, url: notionUrl }
}

async function authorizePremiumOrAdmin(request: Request): Promise<AuthzResult> {
  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || ''
  const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || ''
  if (!supabaseUrl || !supabaseAnonKey) {
    return { ok: false, status: 503, error: 'Supabase設定が不足しています' }
  }

  const authHeader = request.headers.get('Authorization') || ''
  if (!authHeader.startsWith('Bearer ')) {
    return { ok: false, status: 401, error: '認証が必要です' }
  }

  const token = authHeader.slice('Bearer '.length).trim()
  if (!token) {
    return { ok: false, status: 401, error: '認証が必要です' }
  }

  const supabase = createClient(supabaseUrl, supabaseAnonKey, {
    global: {
      headers: { Authorization: `Bearer ${token}` },
    },
  })

  const { data: userData, error: userError } = await supabase.auth.getUser()
  if (userError || !userData.user) {
    return { ok: false, status: 401, error: '認証が必要です' }
  }

  const { data: profile, error: profileError } = await supabase
    .from('profiles')
    .select('role, subscription_tier')
    .eq('id', userData.user.id)
    .single()

  if (profileError || !profile) {
    return { ok: false, status: 403, error: '権限がありません' }
  }

  const role = String((profile as Record<string, unknown>).role || '').toLowerCase()
  const tier = String((profile as Record<string, unknown>).subscription_tier || '').toLowerCase()
  const isAdmin = role === 'admin'
  const isPremium = isAdmin || tier === 'premium'

  if (!isPremium) {
    return { ok: false, status: 403, error: '権限がありません' }
  }

  return { ok: true, status: 200 }
}

function parseReportType(raw: unknown): ReportType | null {
  if (typeof raw !== 'string') return null
  const found = REPORT_TYPE_META.find((m) => m.id === raw)
  return found ? found.id : null
}

export async function GET(request: Request) {
  const authz = await authorizePremiumOrAdmin(request)
  if (!authz.ok) {
    return NextResponse.json({ success: false, error: authz.error || 'authorization failed' }, { status: authz.status })
  }

  const notionConfigured = Boolean(process.env.NOTION_TOKEN && process.env.NOTION_PARENT_PAGE_ID)
  return NextResponse.json({
    success: true,
    state: notionConfigured ? 'pass' : 'warn',
    code: notionConfigured ? 'ready' : 'config-missing',
    report_types: REPORT_TYPE_META,
    notion_configured: notionConfigured,
    message: notionConfigured
      ? 'Notion 送信設定は有効です。'
      : 'NOTION_TOKEN または NOTION_PARENT_PAGE_ID が未設定です。preview は利用できます。',
  })
}

export async function POST(request: Request) {
  const authz = await authorizePremiumOrAdmin(request)
  if (!authz.ok) {
    return NextResponse.json({ success: false, error: authz.error || 'authorization failed' }, { status: authz.status })
  }

  const body = await request.json().catch(() => ({})) as Record<string, unknown>

  const forbiddenPathKeys = ['filePath', 'reportPath', 'path', 'sourcePath']
  const foundForbiddenPathKey = forbiddenPathKeys.find((k) => typeof body[k] === 'string' && String(body[k]).trim() !== '')
  if (foundForbiddenPathKey) {
    return NextResponse.json(
      {
        success: false,
        state: 'fail',
        code: 'path-input-forbidden',
        error: `${foundForbiddenPathKey} は指定できません`,
      },
      { status: 400 },
    )
  }

  const action = String(body.action ?? 'preview')
  const reportType = parseReportType(body.reportType)
  if (!reportType) {
    return NextResponse.json({ success: false, state: 'fail', code: 'invalid-report-type', error: 'reportType が不正です' }, { status: 400 })
  }

  const report = buildReport(reportType)

  if (action === 'preview') {
    return NextResponse.json({
      success: true,
      state: report.state,
      code: report.code,
      report_type: report.reportType,
      title: report.title,
      source: report.source,
      preview: report.preview,
      generated_at: new Date().toISOString(),
    })
  }

  if (action !== 'send') {
    return NextResponse.json({ success: false, state: 'fail', code: 'invalid-action', error: 'action は preview または send のみです' }, { status: 400 })
  }

  const notionToken = process.env.NOTION_TOKEN || ''
  const notionParentPageId = process.env.NOTION_PARENT_PAGE_ID || ''
  if (!notionToken || !notionParentPageId) {
    return NextResponse.json({
      success: false,
      state: 'warn',
      code: 'config-missing',
      error: 'NOTION_TOKEN または NOTION_PARENT_PAGE_ID が未設定です',
      report_type: report.reportType,
      title: report.title,
      source: report.source,
      preview: report.preview,
    })
  }

  try {
    const now = new Date().toISOString().replace(/[:.]/g, '-')
    const pageTitle = `[keiba-ai-pro] ${report.title} ${now}`
    const notion = await appendMarkdownToNotion(notionParentPageId, pageTitle, report.preview, notionToken)
    return NextResponse.json({
      success: true,
      state: report.state,
      code: 'sent',
      report_type: report.reportType,
      title: report.title,
      source: report.source,
      notion_page_id: notion.pageId,
      notion_url: notion.url || null,
      generated_at: new Date().toISOString(),
    })
  } catch (e: unknown) {
    return NextResponse.json({
      success: false,
      state: 'fail',
      code: 'notion-send-failed',
      error: sanitizeText(e instanceof Error ? e.message : 'notion send failed'),
      report_type: report.reportType,
      title: report.title,
      source: report.source,
    }, { status: 502 })
  }
}
