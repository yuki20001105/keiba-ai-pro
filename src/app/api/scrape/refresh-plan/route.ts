import { NextRequest, NextResponse } from 'next/server'
import { spawn } from 'child_process'
import fs from 'fs'
import path from 'path'
import { createClient } from '@supabase/supabase-js'
import { verifyRequestAuth } from '@/lib/server-auth'

export const runtime = 'nodejs'
export const maxDuration = 300

type AuthzResult = {
  ok: boolean
  status: 200 | 401 | 403 | 503
  detail?: string
}

type AllowedTarget = 'all' | 'race' | 'horse' | 'result' | 'pedigree' | 'odds'
type AllowedPolicy = 'repair-missing' | 'refresh-stale' | 'force-refresh' | 'reparse-cache' | 'skip-existing' | 'dry-run'

const PROJECT_ROOT = process.cwd()
const SCRIPT_PATH = path.join(PROJECT_ROOT, 'scripts', 'plan_scrape_refresh.py')
const VENV_PYTHON = path.join(PROJECT_ROOT, 'python-api', '.venv', 'Scripts', 'python.exe')
const REPORTS_DIR = path.join(PROJECT_ROOT, 'reports')
const FORBIDDEN_PATH_KEYS = new Set([
  'filePath',
  'reportPath',
  'dbPath',
  'inputDb',
  'inputCsv',
  'output',
  'sourcePath',
  'modelPath',
  'path',
])
const TARGETS: AllowedTarget[] = ['all', 'race', 'horse', 'result', 'pedigree', 'odds']
const POLICIES: AllowedPolicy[] = ['repair-missing', 'refresh-stale', 'force-refresh', 'reparse-cache', 'skip-existing', 'dry-run']
const NOTION_TOKEN_PREFIX = 'ntn' + '_'

function getPythonExecutable(): string {
  return fs.existsSync(VENV_PYTHON) ? VENV_PYTHON : 'python'
}

function isDateLike(v: unknown): boolean {
  if (typeof v !== 'string') return false
  const s = v.trim()
  if (!s) return false
  return /^\d{8}$/.test(s) || /^\d{4}-\d{2}-\d{2}$/.test(s)
}

function sanitizeError(text: string): string {
  if (!text) return 'request failed'
  return text
    .slice(0, 400)
    .replace(/(sb_secret_[A-Za-z0-9_-]+)/g, '[REDACTED_SECRET]')
    .replace(/(sb_publishable_[A-Za-z0-9_-]+)/g, '[REDACTED_PUBLISHABLE]')
    .replace(new RegExp(`(${NOTION_TOKEN_PREFIX}[A-Za-z0-9_-]+)`, 'g'), '[REDACTED_NOTION]')
}

async function authorizeRefreshPlanRequest(request: Request): Promise<AuthzResult> {
  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || ''
  const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || ''
  if (!supabaseUrl || !supabaseAnonKey) {
    return { ok: false, status: 503, detail: 'Supabase configuration missing' }
  }

  const authHeader = request.headers.get('Authorization') || ''
  if (!authHeader.startsWith('Bearer ')) {
    return { ok: false, status: 401, detail: 'Authentication required' }
  }

  const token = authHeader.slice('Bearer '.length).trim()
  if (!token) {
    return { ok: false, status: 401, detail: 'Authentication required' }
  }

  const supabase = createClient(supabaseUrl, supabaseAnonKey, {
    global: { headers: { Authorization: `Bearer ${token}` } },
  })

  const { data: userData, error: userError } = await supabase.auth.getUser()
  if (userError || !userData.user) {
    return { ok: false, status: 401, detail: 'Authentication required' }
  }

  const { data: profile, error: profileError } = await supabase
    .from('profiles')
    .select('role, subscription_tier')
    .eq('id', userData.user.id)
    .maybeSingle()

  if (profileError || !profile) {
    return { ok: false, status: 403, detail: 'Access denied' }
  }

  const role = String((profile as { role?: string }).role || '').toLowerCase()
  const tier = String((profile as { subscription_tier?: string }).subscription_tier || '').toLowerCase()
  if (role !== 'admin' && tier !== 'premium') {
    return { ok: false, status: 403, detail: 'Premium or admin role required' }
  }

  return { ok: true, status: 200 }
}

function rejectForbiddenPathInputs(input: Record<string, unknown>): string | null {
  for (const key of Object.keys(input)) {
    if (FORBIDDEN_PATH_KEYS.has(key)) {
      return key
    }
  }
  return null
}

async function runRefreshPlanner(args: {
  startDate?: string
  endDate?: string
  target: AllowedTarget
  policy: AllowedPolicy
  staleDays: number
  currentParserVersion: string
}): Promise<{ ok: boolean; payload?: Record<string, unknown>; error?: string; code?: number | null }> {
  const outPath = path.join(REPORTS_DIR, `scrape_refresh_plan_api_${Date.now()}_${Math.floor(Math.random() * 10000)}.json`)
  const pyArgs = [
    SCRIPT_PATH,
    '--target',
    args.target,
    '--policy',
    args.policy,
    '--stale-days',
    String(args.staleDays),
    '--current-parser-version',
    args.currentParserVersion,
    '--output',
    outPath,
  ]
  if (args.startDate) pyArgs.push('--start-date', args.startDate)
  if (args.endDate) pyArgs.push('--end-date', args.endDate)

  return await new Promise((resolve) => {
    const child = spawn(getPythonExecutable(), pyArgs, {
      cwd: PROJECT_ROOT,
      env: process.env,
      shell: false,
      windowsHide: true,
    })

    let stdout = ''
    let stderr = ''
    let timedOut = false

    const timer = setTimeout(() => {
      timedOut = true
      try {
        child.kill()
      } catch {
        // no-op
      }
    }, 120_000)

    child.stdout.on('data', (d) => {
      stdout += String(d)
    })

    child.stderr.on('data', (d) => {
      stderr += String(d)
    })

    child.on('close', (code) => {
      clearTimeout(timer)
      if (timedOut) {
        resolve({ ok: false, error: 'planner timeout', code })
        return
      }

      if (code !== 0) {
        resolve({ ok: false, error: sanitizeError(stderr || stdout || 'planner failed'), code })
        return
      }

      try {
        const raw = fs.readFileSync(outPath, 'utf-8')
        const parsed = JSON.parse(raw)
        resolve({ ok: true, payload: parsed as Record<string, unknown>, code })
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : 'failed to parse output'
        resolve({ ok: false, error: sanitizeError(msg), code })
      } finally {
        try {
          fs.unlinkSync(outPath)
        } catch {
          // no-op
        }
      }
    })

    child.on('error', (e) => {
      clearTimeout(timer)
      resolve({ ok: false, error: sanitizeError(e.message), code: null })
    })
  })
}

async function handlePlanRequest(request: NextRequest, body: Record<string, unknown>) {
  const authz = await verifyRequestAuth(request, { requirePremiumOrAdmin: true })
  if (!authz.ok) {
    return NextResponse.json({ detail: authz.detail || 'forbidden' }, { status: authz.status })
  }

  const forbidden = rejectForbiddenPathInputs(body)
  if (forbidden) {
    return NextResponse.json(
      { error: `forbidden input key: ${forbidden}` },
      { status: 400 }
    )
  }

  const target = String(body.target || 'all') as AllowedTarget
  const policy = String(body.policy || 'repair-missing') as AllowedPolicy
  const startDate = body.startDate == null ? undefined : String(body.startDate)
  const endDate = body.endDate == null ? undefined : String(body.endDate)
  const staleDays = Number(body.staleDays ?? 30)
  const currentParserVersion = String(body.currentParserVersion || '2.0.0')

  if (!TARGETS.includes(target)) {
    return NextResponse.json({ error: 'invalid target' }, { status: 400 })
  }
  if (!POLICIES.includes(policy)) {
    return NextResponse.json({ error: 'invalid policy' }, { status: 400 })
  }
  if (startDate && !isDateLike(startDate)) {
    return NextResponse.json({ error: 'invalid startDate' }, { status: 400 })
  }
  if (endDate && !isDateLike(endDate)) {
    return NextResponse.json({ error: 'invalid endDate' }, { status: 400 })
  }
  if (!Number.isFinite(staleDays) || staleDays < 1 || staleDays > 3650) {
    return NextResponse.json({ error: 'invalid staleDays' }, { status: 400 })
  }

  const planner = await runRefreshPlanner({
    startDate,
    endDate,
    target,
    policy,
    staleDays,
    currentParserVersion,
  })

  if (!planner.ok || !planner.payload) {
    return NextResponse.json({ error: planner.error || 'planner failed' }, { status: 500 })
  }

  const payload = planner.payload
  const decisionsRaw = Array.isArray(payload.decisions) ? payload.decisions : []
  const decisionSamples = decisionsRaw.slice(0, 50)
  const warnings: string[] = []
  if (Number(payload.quarantine_count || 0) > 0) warnings.push('quarantine candidates detected')
  if (Number(payload.no_downgrade_skip_count || 0) > 0) warnings.push('no-downgrade skips detected')
  if (Number(payload.repair_count || 0) > 0) warnings.push('repair candidates detected')

  return NextResponse.json({
    dry_run: true,
    update_enabled: false,
    update_action: 'not-implemented',
    plan: {
      policy: payload.policy,
      target: payload.target,
      start_date: payload.start_date,
      end_date: payload.end_date,
      target_count: payload.target_count,
      existing_count: payload.existing_count,
      missing_count: payload.missing_count,
      skip_count: payload.skip_count,
      repair_count: payload.repair_count,
      reparse_count: payload.reparse_count,
      refetch_count: payload.refetch_count,
      update_candidate_count: payload.update_candidate_count,
      quarantine_count: payload.quarantine_count,
      no_downgrade_skip_count: payload.no_downgrade_skip_count,
      estimated_http_request_count: payload.estimated_http_request_count,
      estimated_runtime: payload.estimated_runtime,
      reasons: payload.reasons,
      warnings,
      verdict: warnings.length > 0 ? 'warn' : 'pass',
      decisions: decisionSamples,
    },
  })
}

export async function POST(request: NextRequest) {
  const authz = await verifyRequestAuth(request, { requirePremiumOrAdmin: true })
  if (!authz.ok) {
    return NextResponse.json({ detail: authz.detail || 'forbidden' }, { status: authz.status })
  }

  let body: Record<string, unknown>
  try {
    const raw = await request.json()
    body = (raw && typeof raw === 'object') ? (raw as Record<string, unknown>) : {}
  } catch {
    body = {}
  }

  return handlePlanRequest(request, body)
}

export async function GET(request: NextRequest) {
  const authz = await verifyRequestAuth(request, { requirePremiumOrAdmin: true })
  if (!authz.ok) {
    return NextResponse.json({ detail: authz.detail || 'forbidden' }, { status: authz.status })
  }

  const url = new URL(request.url)
  const payload: Record<string, unknown> = {
    startDate: url.searchParams.get('startDate') || undefined,
    endDate: url.searchParams.get('endDate') || undefined,
    target: url.searchParams.get('target') || 'all',
    policy: url.searchParams.get('policy') || 'repair-missing',
    staleDays: url.searchParams.get('staleDays') ? Number(url.searchParams.get('staleDays')) : 30,
    currentParserVersion: url.searchParams.get('currentParserVersion') || '2.0.0',
  }
  return handlePlanRequest(request, payload)
}

export async function PUT(request: NextRequest) {
  const authz = await verifyRequestAuth(request, { requirePremiumOrAdmin: true })
  if (!authz.ok) {
    return NextResponse.json({ detail: authz.detail || 'forbidden' }, { status: authz.status })
  }

  return NextResponse.json(
    {
      error: 'not-implemented',
      detail: 'Refresh execution is disabled in this phase. Use dry-run preview only.',
    },
    { status: 501 }
  )
}
