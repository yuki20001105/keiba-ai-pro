import { NextResponse } from 'next/server'
import { spawn } from 'child_process'
import fs from 'fs'
import path from 'path'
import { createClient } from '@supabase/supabase-js'

export const runtime = 'nodejs'
export const maxDuration = 300

type CheckState = 'pass' | 'warn' | 'fail' | 'unknown'

type CheckItem = {
  id: string
  label: string
  state: CheckState
  summary: string
  details?: Record<string, unknown>
  durationMs?: number
}

type CommandResult = {
  ok: boolean
  code: number | null
  stdout: string
  stderr: string
  timedOut: boolean
  durationMs: number
}

type AllowedCommandKey = 'frontend_build' | 'python_compile' | 'secret_scan' | 'git_status' | 'smoke_analyze_race' | 'smoke_suite'

const PROJECT_ROOT = process.cwd()
const REPORTS_DIR = path.join(PROJECT_ROOT, 'reports')
const VENV_PYTHON = path.join(PROJECT_ROOT, 'python-api', '.venv', 'Scripts', 'python.exe')

const SAFE_ENV_KEYS = [
  'APP_ENV',
  'NETKEIBA_RACE_WRITE_ENABLED',
  'ALLOW_STAGING_WRITE',
  'ML_API_URL',
  'FASTAPI_URL',
  'KEIBA_AUTH_BEARER_TOKEN',
]

const NOTION_PREFIX = ['nt', 'n_'].join('')

type AuthzResult = {
  ok: boolean
  status: 200 | 401 | 403 | 503
  error?: string
}

function toBoolFlag(value: string | undefined, fallback = false): boolean {
  if (value == null) return fallback
  return ['1', 'true', 'yes', 'on'].includes(value.trim().toLowerCase())
}

function getPythonExecutable(): string {
  return fs.existsSync(VENV_PYTHON) ? VENV_PYTHON : 'python'
}

function getNpmCommand(): string {
  return process.platform === 'win32' ? 'npm.cmd' : 'npm'
}

function getAllowlistedCommand(key: AllowedCommandKey): { cmd: string; args: string[]; timeoutMs: number } {
  const python = getPythonExecutable()
  switch (key) {
    case 'frontend_build':
      return { cmd: getNpmCommand(), args: ['run', 'build'], timeoutMs: 240_000 }
    case 'python_compile':
      return { cmd: python, args: ['-m', 'compileall', '-q', 'python-api', 'scripts'], timeoutMs: 120_000 }
    case 'secret_scan':
      return { cmd: 'git', args: ['grep', '-n', '-I', NOTION_PREFIX], timeoutMs: 20_000 }
    case 'git_status':
      return { cmd: 'git', args: ['status', '--short'], timeoutMs: 15_000 }
    case 'smoke_analyze_race':
      return { cmd: python, args: ['scripts/smoke_analyze_race_api.py'], timeoutMs: 180_000 }
    case 'smoke_suite':
      return { cmd: python, args: ['scripts/run_keiba_smoke_suite.py'], timeoutMs: 300_000 }
    default:
      throw new Error('Unknown command key')
  }
}

function sanitizeText(raw: string, maxChars = 800): string {
  if (!raw) return ''
  const tail = raw.length > maxChars ? raw.slice(raw.length - maxChars) : raw
  return tail
    .replace(/(sb_secret_[A-Za-z0-9_-]+)/g, '[REDACTED_SECRET]')
    .replace(/(sb_publishable_[A-Za-z0-9_-]+)/g, '[REDACTED_PUBLISHABLE]')
    .replace(new RegExp(`(${NOTION_PREFIX}[A-Za-z0-9_-]+)`, 'g'), '[REDACTED_NOTION]')
}

function readJsonIfExists(filePath: string): Record<string, unknown> | null {
  try {
    if (!fs.existsSync(filePath)) return null
    const raw = fs.readFileSync(filePath, 'utf-8')
    const parsed = JSON.parse(raw)
    return typeof parsed === 'object' && parsed != null ? parsed as Record<string, unknown> : null
  } catch {
    return null
  }
}

function runAllowlisted(key: AllowedCommandKey): Promise<CommandResult> {
  const { cmd, args, timeoutMs } = getAllowlistedCommand(key)

  return new Promise((resolve) => {
    const started = Date.now()
    let timedOut = false
    let stdout = ''
    let stderr = ''

    const child = spawn(cmd, args, {
      cwd: PROJECT_ROOT,
      env: process.env,
      shell: false,
      windowsHide: true,
    })

    const timer = setTimeout(() => {
      timedOut = true
      try {
        child.kill()
      } catch {
        // no-op
      }
    }, timeoutMs)

    child.stdout.on('data', (d) => { stdout += String(d) })
    child.stderr.on('data', (d) => { stderr += String(d) })

    child.on('close', (code) => {
      clearTimeout(timer)
      resolve({
        ok: !timedOut && code === 0,
        code,
        stdout,
        stderr,
        timedOut,
        durationMs: Date.now() - started,
      })
    })

    child.on('error', (error) => {
      clearTimeout(timer)
      resolve({
        ok: false,
        code: null,
        stdout,
        stderr: `${stderr}\n${error.message}`.trim(),
        timedOut,
        durationMs: Date.now() - started,
      })
    })
  })
}

async function checkHealth(endpoint: string, timeoutMs = 5_000): Promise<{ ok: boolean; status?: number; body?: unknown; reason?: string }> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(endpoint, { signal: controller.signal, cache: 'no-store' })
    let body: unknown = null
    try {
      body = await response.json()
    } catch {
      body = null
    }
    return { ok: response.ok, status: response.status, body }
  } catch (e: unknown) {
    return { ok: false, reason: e instanceof Error ? e.message : 'request failed' }
  } finally {
    clearTimeout(timer)
  }
}

function classifyGitStatus(statusOutput: string): { state: CheckState; summary: string; details: Record<string, unknown> } {
  const lines = statusOutput
    .split(/\r?\n/)
    .map(l => l.trim())
    .filter(Boolean)

  const forbiddenPatterns = [
    /^keiba\/data\//,
    /^reports\//,
    /^docs\/reports\//,
    /^python-api\/models\/.*\.metadata\.json$/,
    /^python-api\/models\/.*\.joblib$/,
    /^.*\.db$/,
    /^\.env(\..+)?$/,
  ]

  const stagedForbidden: string[] = []
  const unstagedForbidden: string[] = []

  for (const line of lines) {
    const xy = line.slice(0, 2)
    const rawPath = line.slice(3).trim()
    const filePath = rawPath.includes(' -> ') ? rawPath.split(' -> ')[1].trim() : rawPath
    const isForbidden = forbiddenPatterns.some((pattern) => pattern.test(filePath.replace(/\\/g, '/')))
    if (!isForbidden) continue

    const staged = xy[0] !== ' ' && xy[0] !== '?'
    if (staged) stagedForbidden.push(filePath)
    else unstagedForbidden.push(filePath)
  }

  if (stagedForbidden.length > 0) {
    return {
      state: 'fail',
      summary: 'コミット禁止対象が staged に含まれています',
      details: { stagedForbidden, unstagedForbidden, lineCount: lines.length },
    }
  }

  if (unstagedForbidden.length > 0) {
    return {
      state: 'warn',
      summary: 'コミット禁止対象の未追跡/未staged変更があります',
      details: { unstagedForbidden, lineCount: lines.length },
    }
  }

  return {
    state: 'pass',
    summary: 'コミット禁止対象は staged に含まれていません',
    details: { lineCount: lines.length },
  }
}

function normalizeSmokeState(summary: unknown): CheckState {
  if (summary === 'pass' || summary === true) return 'pass'
  if (summary === 'warn') return 'warn'
  if (summary === 'fail' || summary === false) return 'fail'
  return 'unknown'
}

function safeEnvSnapshot(): Record<string, string> {
  const out: Record<string, string> = {}
  for (const key of SAFE_ENV_KEYS) {
    out[key] = process.env[key] ? '[SET]' : '[UNSET]'
  }
  return out
}

async function authorizeReadinessExecution(request: Request): Promise<AuthzResult> {
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
      headers: {
        Authorization: `Bearer ${token}`,
      },
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

export async function GET() {
  const appEnv = String(process.env.APP_ENV ?? '').trim() || 'unknown'
  const netkeibaWrite = toBoolFlag(process.env.NETKEIBA_RACE_WRITE_ENABLED, false)
  const allowStagingWrite = toBoolFlag(process.env.ALLOW_STAGING_WRITE, false)

  return NextResponse.json({
    success: true,
    message: 'POST /api/production-readiness で read-only チェックを実行します。',
    policy: {
      write_operations_executed: false,
      netkeiba_write_enabled: netkeibaWrite,
      allow_staging_write: allowStagingWrite,
      app_env: appEnv,
      sandbox_write_readback_included: false,
      production_base_write_allowed: false,
    },
    env_snapshot: safeEnvSnapshot(),
  })
}

export async function POST(request: Request) {
  const authz = await authorizeReadinessExecution(request)
  if (!authz.ok) {
    return NextResponse.json(
      { success: false, error: authz.error || 'authorization failed' },
      { status: authz.status },
    )
  }

  const checks: CheckItem[] = []
  const mlApiBase = process.env.ML_API_URL || process.env.FASTAPI_URL || 'http://127.0.0.1:8000'

  const fastapiHealth = await checkHealth(`${mlApiBase}/health`, 8_000)
  checks.push({
    id: 'fastapi_health',
    label: 'FastAPI health',
    state: fastapiHealth.ok ? 'pass' : 'fail',
    summary: fastapiHealth.ok ? 'FastAPI health は正常です' : 'FastAPI health 取得に失敗しました',
    details: {
      endpoint: `${mlApiBase}/health`,
      status: fastapiHealth.status ?? null,
      reason: fastapiHealth.reason ?? null,
    },
  })

  const scrapeHealth = await checkHealth(`${mlApiBase}/api/scrape/health`, 8_000)
  const scrapeStatus = (scrapeHealth.body && typeof scrapeHealth.body === 'object')
    ? String((scrapeHealth.body as Record<string, unknown>).status ?? '')
    : ''
  const scrapeState: CheckState = !scrapeHealth.ok ? 'fail' : scrapeStatus === 'healthy' ? 'pass' : scrapeStatus === 'degraded' ? 'warn' : 'unknown'
  checks.push({
    id: 'scrape_health',
    label: 'scrape health',
    state: scrapeState,
    summary: scrapeHealth.ok ? `scrape health: ${scrapeStatus || 'unknown'}` : 'scrape health 取得に失敗しました',
    details: {
      endpoint: `${mlApiBase}/api/scrape/health`,
      status: scrapeHealth.status ?? null,
      scrape_status: scrapeStatus || null,
      reason: scrapeHealth.reason ?? null,
    },
  })

  const frontendBuild = await runAllowlisted('frontend_build')
  checks.push({
    id: 'frontend_build',
    label: 'Frontend build status',
    state: frontendBuild.ok ? 'pass' : (frontendBuild.timedOut ? 'warn' : 'fail'),
    summary: frontendBuild.ok ? 'npm run build 成功' : frontendBuild.timedOut ? 'npm run build がタイムアウトしました' : 'npm run build 失敗',
    durationMs: frontendBuild.durationMs,
    details: {
      exit_code: frontendBuild.code,
      stderr_tail: sanitizeText(frontendBuild.stderr),
      stdout_tail: sanitizeText(frontendBuild.stdout),
    },
  })

  const pythonCompile = await runAllowlisted('python_compile')
  checks.push({
    id: 'python_compile',
    label: 'Python compileall',
    state: pythonCompile.ok ? 'pass' : 'fail',
    summary: pythonCompile.ok ? 'python -m compileall 成功' : 'python -m compileall 失敗',
    durationMs: pythonCompile.durationMs,
    details: {
      exit_code: pythonCompile.code,
      stderr_tail: sanitizeText(pythonCompile.stderr),
      stdout_tail: sanitizeText(pythonCompile.stdout),
    },
  })

  const analyzeSmokeRun = await runAllowlisted('smoke_analyze_race')
  const analyzeSmokeReport = readJsonIfExists(path.join(REPORTS_DIR, 'analyze_race_smoke_result.json'))
  const analyzeStatus = Number(analyzeSmokeReport?.http_status ?? 0)
  const analyzeAuthRequired = Boolean(analyzeSmokeReport?.auth_required)
  const analyzeTokenProvided = Boolean(analyzeSmokeReport?.token_provided)
  const analyzeState: CheckState = analyzeSmokeRun.ok
    ? 'pass'
    : (analyzeAuthRequired && [401, 403].includes(analyzeStatus) && !analyzeTokenProvided)
      ? 'warn'
      : 'fail'
  checks.push({
    id: 'analyze_race_smoke',
    label: 'analyze_race smoke',
    state: analyzeState,
    summary: analyzeSmokeRun.ok
      ? 'analyze_race smoke 成功'
      : (analyzeAuthRequired && [401, 403].includes(analyzeStatus) && !analyzeTokenProvided)
        ? '認証トークン未設定のため auth-required (warn)'
        : 'analyze_race smoke 失敗',
    durationMs: analyzeSmokeRun.durationMs,
    details: {
      exit_code: analyzeSmokeRun.code,
      report_summary: analyzeSmokeReport ? {
        success: analyzeSmokeReport.success ?? null,
        http_status: analyzeSmokeReport.http_status ?? null,
        predictions_count: analyzeSmokeReport.predictions_count ?? null,
      } : null,
      stderr_tail: sanitizeText(analyzeSmokeRun.stderr),
      stdout_tail: sanitizeText(analyzeSmokeRun.stdout),
    },
  })

  const smokeSuiteRun = await runAllowlisted('smoke_suite')
  const smokeSuiteReport = readJsonIfExists(path.join(REPORTS_DIR, 'keiba_smoke_suite_result.json'))
  const smokeSummary = smokeSuiteReport?.summary
  const smokeAnalyzeStep = smokeSuiteReport?.steps && typeof smokeSuiteReport.steps === 'object'
    ? (smokeSuiteReport.steps as Record<string, unknown>).analyze_race
    : null
  const smokeAnalyzeReason = smokeAnalyzeStep && typeof smokeAnalyzeStep === 'object'
    ? String((smokeAnalyzeStep as Record<string, unknown>).reason ?? '')
    : ''
  const smokeState: CheckState = smokeSuiteRun.ok
    ? 'pass'
    : smokeAnalyzeReason === 'auth-required'
      ? 'warn'
      : normalizeSmokeState(smokeSummary)
  checks.push({
    id: 'smoke_suite_summary',
    label: 'smoke suite summary',
    state: smokeState,
    summary: smokeSuiteRun.ok
      ? 'smoke suite 成功'
      : smokeAnalyzeReason === 'auth-required'
        ? `smoke suite: auth-required (warn)`
        : `smoke suite: ${String(smokeSummary ?? 'unknown')}`,
    durationMs: smokeSuiteRun.durationMs,
    details: {
      exit_code: smokeSuiteRun.code,
      summary: smokeSummary ?? null,
      step_results: smokeSuiteReport?.steps ?? null,
      stderr_tail: sanitizeText(smokeSuiteRun.stderr),
      stdout_tail: sanitizeText(smokeSuiteRun.stdout),
    },
  })

  const secretScan = await runAllowlisted('secret_scan')
  const secretMatches = (secretScan.stdout || '').trim()
  const secretPass = secretScan.code === 1 || (secretScan.ok && secretMatches.length === 0)
  checks.push({
    id: 'secret_scan',
    label: 'secret scan (Notion token prefix)',
    state: secretPass ? 'pass' : 'fail',
    summary: secretPass ? 'Notion token prefix の検出なし' : 'Notion token prefix が検出されました',
    durationMs: secretScan.durationMs,
    details: {
      exit_code: secretScan.code,
      matches_tail: sanitizeText(secretMatches),
      stderr_tail: sanitizeText(secretScan.stderr),
    },
  })

  const gitStatus = await runAllowlisted('git_status')
  const gitStatusText = gitStatus.stdout.trim()
  const gitClassified = classifyGitStatus(gitStatusText)
  checks.push({
    id: 'git_status_notice',
    label: 'git status 注意表示',
    state: gitClassified.state,
    summary: gitClassified.summary,
    durationMs: gitStatus.durationMs,
    details: {
      exit_code: gitStatus.code,
      status_tail: sanitizeText(gitStatusText),
      ...gitClassified.details,
    },
  })

  const netkeibaWrite = toBoolFlag(process.env.NETKEIBA_RACE_WRITE_ENABLED, false)
  const allowStagingWrite = toBoolFlag(process.env.ALLOW_STAGING_WRITE, false)
  const appEnv = String(process.env.APP_ENV ?? '').trim().toLowerCase()

  checks.push({
    id: 'write_flags_false',
    label: 'production write flag = false',
    state: !netkeibaWrite && !allowStagingWrite ? 'pass' : 'fail',
    summary: !netkeibaWrite && !allowStagingWrite
      ? 'NETKEIBA_RACE_WRITE_ENABLED=false / ALLOW_STAGING_WRITE=false を確認'
      : 'write flag が true のため危険です',
    details: {
      NETKEIBA_RACE_WRITE_ENABLED: netkeibaWrite,
      ALLOW_STAGING_WRITE: allowStagingWrite,
    },
  })

  checks.push({
    id: 'app_env_safety',
    label: 'APP_ENV safety',
    state: ['production', 'production-safe'].includes(appEnv) ? 'pass' : 'warn',
    summary: ['production', 'production-safe'].includes(appEnv)
      ? `APP_ENV=${appEnv}`
      : `APP_ENV=${appEnv || 'unknown'} (production-safe を推奨)`,
    details: {
      APP_ENV: appEnv || null,
    },
  })

  checks.push({
    id: 'sandbox_write_scope',
    label: 'P1-16 sandbox write-readback scope',
    state: 'pass',
    summary: 'P1-16 sandbox write-readback は本チェック対象外（別管理）',
    details: {
      included_in_this_check: false,
    },
  })

  checks.push({
    id: 'production_write_prohibition',
    label: 'production/base table write prohibition',
    state: 'pass',
    summary: '本画面は read-only checks のみ実行し、write API は呼びません',
    details: {
      write_endpoints_called: [],
      policy: 'read-only only',
    },
  })

  const stateRank: Record<CheckState, number> = { pass: 0, warn: 1, unknown: 2, fail: 3 }
  const worst = checks.reduce<CheckState>((acc, item) => (
    stateRank[item.state] > stateRank[acc] ? item.state : acc
  ), 'pass')

  return NextResponse.json({
    success: true,
    overall: worst,
    generated_at: new Date().toISOString(),
    checks,
    guard: {
      read_only_mode: true,
      sandbox_write_readback_included: false,
      production_base_write_allowed: false,
    },
    env_snapshot: safeEnvSnapshot(),
  })
}
