import { NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'
import { createClient } from '@supabase/supabase-js'

export const runtime = 'nodejs'
export const maxDuration = 120

type UiState = 'pass' | 'warn' | 'fail'

type AuthzResult = {
  ok: boolean
  status: 200 | 401 | 403 | 503
  error?: string
}

type NumericMetric = {
  value: number | null
  status: UiState
  note: string
}

const PROJECT_ROOT = process.cwd()
const MODELS_DIR = path.join(PROJECT_ROOT, 'python-api', 'models')
const ACTIVE_MODEL_PATH = path.join(MODELS_DIR, '.active_model.json')
const FEATURE_ANALYSIS_PATH = path.join(PROJECT_ROOT, 'docs', 'reports', 'feature_analysis.json')
const ROI_REPORT_PATH = path.join(PROJECT_ROOT, 'reports', 'roi_report.csv')
const DOCS_REPORTS_DIR = path.join(PROJECT_ROOT, 'docs', 'reports')

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

function parseFloatSafe(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '') {
    const n = Number(value)
    return Number.isFinite(n) ? n : null
  }
  return null
}

function pickLatestIterMetricsFile(): string | null {
  try {
    if (!fs.existsSync(DOCS_REPORTS_DIR)) return null
    const files = fs.readdirSync(DOCS_REPORTS_DIR)
    const candidates = files.filter((n) => /^iter_\d+_metrics\.json$/i.test(n)).sort()
    if (candidates.length === 0) return null
    return path.join(DOCS_REPORTS_DIR, candidates[candidates.length - 1])
  } catch {
    return null
  }
}

function computeHitAndRoiFromCsv(filePath: string): { hitRate: number | null; roi: number | null; rows: number } {
  try {
    if (!fs.existsSync(filePath)) return { hitRate: null, roi: null, rows: 0 }
    const raw = fs.readFileSync(filePath, 'utf-8')
    const lines = raw.split(/\r?\n/).map((l) => l.trim()).filter(Boolean)
    if (lines.length <= 1) return { hitRate: null, roi: null, rows: 0 }

    const header = lines[0].split(',')
    const isWinIdx = header.indexOf('is_win')
    const roiIdx = header.indexOf('roi_raw')
    if (isWinIdx < 0 || roiIdx < 0) return { hitRate: null, roi: null, rows: 0 }

    let rows = 0
    let hitCount = 0
    let roiSum = 0
    for (const line of lines.slice(1)) {
      const cols = line.split(',')
      if (cols.length <= Math.max(isWinIdx, roiIdx)) continue
      const isWin = Number(cols[isWinIdx])
      const roi = Number(cols[roiIdx])
      if (!Number.isFinite(isWin) || !Number.isFinite(roi)) continue
      rows += 1
      if (isWin > 0) hitCount += 1
      roiSum += roi
    }

    if (rows === 0) return { hitRate: null, roi: null, rows: 0 }
    return {
      hitRate: hitCount / rows,
      roi: roiSum / rows,
      rows,
    }
  } catch {
    return { hitRate: null, roi: null, rows: 0 }
  }
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

function toMetric(value: number | null, missingNote: string): NumericMetric {
  if (value == null) return { value: null, status: 'warn', note: missingNote }
  return { value, status: 'pass', note: 'ok' }
}

export async function GET(request: Request) {
  const authz = await authorizePremiumOrAdmin(request)
  if (!authz.ok) {
    return NextResponse.json({ success: false, error: authz.error || 'authorization failed' }, { status: authz.status })
  }

  const searchParams = new URL(request.url).searchParams
  const forbiddenPathKeys = ['path', 'filePath', 'reportPath', 'sourcePath']
  const foundForbidden = forbiddenPathKeys.find((k) => searchParams.get(k))
  if (foundForbidden) {
    return NextResponse.json({
      success: false,
      state: 'fail',
      code: 'path-input-forbidden',
      error: `${foundForbidden} は指定できません`,
    }, { status: 400 })
  }

  const warnings: string[] = []
  const activeModelJson = readJson(ACTIVE_MODEL_PATH)
  const featureAnalysis = readJson(FEATURE_ANALYSIS_PATH)
  const iterMetricsPath = pickLatestIterMetricsFile()
  const iterMetrics = iterMetricsPath ? readJson(iterMetricsPath) : null

  if (!activeModelJson) warnings.push('active-model-missing')
  if (!featureAnalysis) warnings.push('feature-analysis-missing')
  if (!iterMetrics) warnings.push('iter-metrics-missing')

  const activeModelId = String(activeModelJson?.model_id || '')
  const activeModelFile = activeModelId ? path.join(MODELS_DIR, `${activeModelId}.joblib`) : ''
  const activeModelExists = Boolean(activeModelFile && fs.existsSync(activeModelFile))
  if (activeModelId && !activeModelExists) warnings.push('active-model-file-missing')

  const activeModelStat = activeModelExists ? fs.statSync(activeModelFile) : null

  const faMeta = featureAnalysis?.meta && typeof featureAnalysis.meta === 'object'
    ? featureAnalysis.meta as Record<string, unknown>
    : {}
  const trainMetrics = iterMetrics?.train_metrics && typeof iterMetrics.train_metrics === 'object'
    ? iterMetrics.train_metrics as Record<string, unknown>
    : {}

  const csvMetrics = computeHitAndRoiFromCsv(ROI_REPORT_PATH)
  if (csvMetrics.rows === 0) warnings.push('roi-report-missing-or-empty')

  const metrics = {
    rmse: toMetric(parseFloatSafe(faMeta.baseline_rmse), 'data-missing'),
    auc: toMetric(parseFloatSafe(trainMetrics.auc), 'data-missing'),
    spearman: toMetric(parseFloatSafe(faMeta.baseline_spearman), 'data-missing'),
    hit_rate: toMetric(csvMetrics.hitRate, 'data-missing'),
    roi: toMetric(csvMetrics.roi, 'data-missing'),
  }

  const featureRows = Array.isArray(featureAnalysis?.features)
    ? featureAnalysis.features.filter((x) => typeof x === 'object' && x != null) as Array<Record<string, unknown>>
    : []

  const importanceTop = featureRows
    .map((row) => ({
      feature: String(row.feature || ''),
      total_score: parseFloatSafe(row.total_score),
      spearman: parseFloatSafe(row.spearman),
      vif: parseFloatSafe(row.vif),
      op_class: String(row.op_class || ''),
    }))
    .filter((row) => row.feature)
    .slice(0, 30)

  const highCorrWarnings = importanceTop
    .filter((r) => (r.vif ?? 0) >= 50)
    .sort((a, b) => (b.vif ?? 0) - (a.vif ?? 0))
    .slice(0, 12)

  const duplicateWarnings = (() => {
    const names = new Set(importanceTop.map((r) => r.feature))
    const out: Array<{ pair: string; reason: string }> = []
    for (const name of names) {
      if (name.startsWith('prev2_')) {
        const pair = name.replace(/^prev2_/, 'prev_')
        if (names.has(pair)) {
          out.push({ pair: `${pair} / ${name}`, reason: 'prefix-pair' })
        }
      }
      if (name.endsWith('_zscore')) {
        const pair = name.replace(/_zscore$/, '_index')
        if (names.has(pair)) {
          out.push({ pair: `${pair} / ${name}`, reason: 'index-vs-zscore' })
        }
      }
    }
    return out.slice(0, 10)
  })()

  const removalCandidates = importanceTop
    .filter((r) => {
      const removeByClass = r.op_class.includes('削除')
      const highVifLowSignal = (r.vif ?? 0) >= 100 && Math.abs(r.spearman ?? 0) < 0.08
      const lowScoreHighVif = (r.total_score ?? 0) < 0.2 && (r.vif ?? 0) >= 50
      return removeByClass || highVifLowSignal || lowScoreHighVif
    })
    .slice(0, 15)

  const recommendations = [
    highCorrWarnings.length > 0
      ? `高VIF特徴量が ${highCorrWarnings.length} 件あります。上位候補を優先的に整理してください。`
      : 'VIF由来の高相関警告は検出されませんでした。',
    removalCandidates.length > 0
      ? `削除候補特徴量を ${removalCandidates.length} 件検出しました。dry-run で影響評価を推奨します。`
      : '削除候補は自動判定で見つかりませんでした。',
    duplicateWarnings.length > 0
      ? `重複候補ペアを ${duplicateWarnings.length} 件検出しました。表現統一を検討してください。`
      : '重複候補ペアは検出されませんでした。',
    '再学習実行・active model切替は MVP では未実装です。',
  ]

  const state: UiState = warnings.length > 0 ? 'warn' : 'pass'
  const code = warnings.length === 0
    ? 'preview-ready'
    : (warnings.length >= 3 ? 'config-missing' : 'data-missing')

  return NextResponse.json({
    success: true,
    state,
    code,
    generated_at: new Date().toISOString(),
    warnings,
    active_model: {
      model_id: activeModelId || null,
      model_file_exists: activeModelExists,
      model_file_size_bytes: activeModelStat?.size ?? null,
      model_file_updated_at: activeModelStat ? new Date(activeModelStat.mtimeMs).toISOString() : null,
      active_model_path: 'python-api/models/.active_model.json',
    },
    metrics,
    feature_importance: {
      source: 'docs/reports/feature_analysis.json',
      top_features: importanceTop,
    },
    correlation_warnings: {
      high_vif: highCorrWarnings,
      duplicate_pairs: duplicateWarnings,
    },
    removal_candidates: removalCandidates,
    improvement_preview: {
      source: iterMetricsPath ? path.relative(PROJECT_ROOT, iterMetricsPath).replace(/\\/g, '/') : 'missing',
      recommendations,
    },
    guard: {
      read_only_mode: true,
      retrain_execution: 'not-implemented',
      active_model_switch: 'not-implemented',
      production_write: false,
    },
  })
}
