import { NextRequest, NextResponse } from 'next/server'
import { spawn } from 'child_process'
import fs from 'fs'
import path from 'path'
import { createClient } from '@supabase/supabase-js'

export const runtime = 'nodejs'
export const maxDuration = 300

type AuthzResult = {
  ok: boolean
  status: 200 | 401 | 403 | 503
  error?: string
}

type AllowedTarget = 'all' | 'race' | 'horse' | 'result' | 'pedigree' | 'odds'

const PROJECT_ROOT = process.cwd()
const SCRIPT_PATH = path.join(PROJECT_ROOT, 'scripts', 'plan_p0_scrape_repair.py')
const VENV_PYTHON = path.join(PROJECT_ROOT, 'python-api', '.venv', 'Scripts', 'python.exe')
const REPORTS_DIR = path.join(PROJECT_ROOT, 'reports')
const TARGETS: AllowedTarget[] = ['all', 'race', 'horse', 'result', 'pedigree', 'odds']
const NOTION_TOKEN_PREFIX = 'ntn' + '_'

const FORBIDDEN_PATH_KEYS = new Set([
  'filePath',
  'reportPath',
  'inputAudit',
  'inputRefreshPlan',
  'output',
  'dbPath',
  'inputDb',
  'inputCsv',
  'sourcePath',
  'modelPath',
  'path',
])

function getPythonExecutable(): string {
  return fs.existsSync(VENV_PYTHON) ? VENV_PYTHON : 'python'
}

function sanitizeError(text: string): string {
  if (!text) return 'request failed'
  return text
    .slice(0, 400)
    .replace(/(sb_secret_[A-Za-z0-9_-]+)/g, '[REDACTED_SECRET]')
    .replace(/(sb_publishable_[A-Za-z0-9_-]+)/g, '[REDACTED_PUBLISHABLE]')
    .replace(new RegExp(`(${NOTION_TOKEN_PREFIX}[A-Za-z0-9_-]+)`, 'g'), '[REDACTED_NOTION]')
}

async function authorizeRequest(request: Request): Promise<AuthzResult> {
  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || ''
  const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || ''
  if (!supabaseUrl || !supabaseAnonKey) {
    return { ok: false, status: 503, error: 'Supabase configuration missing' }
  }

  const authHeader = request.headers.get('Authorization') || ''
  if (!authHeader.startsWith('Bearer ')) {
    return { ok: false, status: 401, error: 'Authentication required' }
  }

  const token = authHeader.slice('Bearer '.length).trim()
  if (!token) {
    return { ok: false, status: 401, error: 'Authentication required' }
  }

  const supabase = createClient(supabaseUrl, supabaseAnonKey, {
    global: { headers: { Authorization: `Bearer ${token}` } },
  })

  const { data: userData, error: userError } = await supabase.auth.getUser()
  if (userError || !userData.user) {
    return { ok: false, status: 401, error: 'Authentication required' }
  }

  const { data: profile, error: profileError } = await supabase
    .from('profiles')
    .select('role, subscription_tier')
    .eq('id', userData.user.id)
    .maybeSingle()

  if (profileError || !profile) {
    return { ok: false, status: 403, error: 'Access denied' }
  }

  const role = String((profile as { role?: string }).role || '').toLowerCase()
  const tier = String((profile as { subscription_tier?: string }).subscription_tier || '').toLowerCase()
  if (role !== 'admin' && tier !== 'premium') {
    return { ok: false, status: 403, error: 'Premium or admin role required' }
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

function rejectForbiddenQuery(url: URL): string | null {
  for (const k of url.searchParams.keys()) {
    if (FORBIDDEN_PATH_KEYS.has(k)) {
      return k
    }
  }
  return null
}

async function runP0Planner(args: { target: AllowedTarget }): Promise<{ ok: boolean; payload?: Record<string, unknown>; error?: string; code?: number | null }> {
  const outPath = path.join(REPORTS_DIR, `p0_scrape_repair_plan_api_${Date.now()}_${Math.floor(Math.random() * 10000)}.json`)
  const pyArgs = [
    SCRIPT_PATH,
    '--target',
    args.target,
    '--output',
    outPath,
  ]

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

function normalizePlan(plan: Record<string, unknown>): Record<string, unknown> {
  const sampleTargets = Array.isArray(plan.sample_targets) ? plan.sample_targets.slice(0, 120) : []
  const actionBreakdown = Array.isArray(plan.p0_action_breakdown) ? plan.p0_action_breakdown : []
  const reasonBreakdown = Array.isArray(plan.p0_reason_breakdown) ? plan.p0_reason_breakdown : []
  const recommended = Array.isArray(plan.recommended_next_actions) ? plan.recommended_next_actions : []

  return {
    verdict: String(plan.verdict || 'warn'),
    target: String(plan.target || 'all'),
    p0_total_count: Number(plan.p0_total_count || 0),
    refetch_required_count: Number(plan.refetch_required_count || 0),
    reparse_cache_count: Number(plan.reparse_cache_count || 0),
    repair_from_metadata_count: Number(plan.repair_from_metadata_count || 0),
    schema_review_count: Number(plan.schema_review_count || 0),
    manual_review_count: Number(plan.manual_review_count || 0),
    no_action_count: Number(plan.no_action_count || 0),
    estimated_http_request_count: Number(plan.estimated_http_request_count || 0),
    estimated_runtime_seconds: Number(plan.estimated_runtime_seconds || 0),
    p0_action_breakdown: actionBreakdown,
    p0_reason_breakdown: reasonBreakdown,
    sample_targets: sampleTargets,
    recommended_next_actions: recommended,
    safeguards: {
      read_only: true,
      no_db_write: true,
      no_scrape_execute: true,
      no_upsert: true,
      no_force_refresh_execute: true,
      ...(typeof plan.safeguards === 'object' && plan.safeguards ? (plan.safeguards as Record<string, unknown>) : {}),
    },
  }
}

async function handleRequest(request: NextRequest, body: Record<string, unknown>) {
  const authz = await authorizeRequest(request)
  if (!authz.ok) {
    return NextResponse.json({ error: authz.error || 'forbidden' }, { status: authz.status })
  }

  const forbidden = rejectForbiddenPathInputs(body)
  if (forbidden) {
    return NextResponse.json({ error: `forbidden input key: ${forbidden}` }, { status: 400 })
  }

  const target = String(body.target || 'all') as AllowedTarget
  if (!TARGETS.includes(target)) {
    return NextResponse.json({ error: 'invalid target' }, { status: 400 })
  }

  const planner = await runP0Planner({ target })
  if (!planner.ok || !planner.payload) {
    return NextResponse.json({ error: planner.error || 'planner failed' }, { status: 500 })
  }

  const plan = normalizePlan(planner.payload)
  return NextResponse.json({
    dry_run: true,
    read_only: true,
    update_enabled: false,
    update_action: 'not-implemented',
    plan,
  })
}

export async function POST(request: NextRequest) {
  let body: Record<string, unknown>
  try {
    const raw = await request.json()
    body = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  } catch {
    body = {}
  }
  return handleRequest(request, body)
}

export async function GET(request: NextRequest) {
  const url = new URL(request.url)
  const forbidden = rejectForbiddenQuery(url)
  if (forbidden) {
    return NextResponse.json({ error: `forbidden input key: ${forbidden}` }, { status: 400 })
  }
  const payload: Record<string, unknown> = {
    target: url.searchParams.get('target') || 'all',
  }
  return handleRequest(request, payload)
}

export async function PUT() {
  return NextResponse.json(
    {
      error: 'not-implemented',
      detail: 'P0 repair execution is disabled in this phase. Preview only.',
    },
    { status: 501 }
  )
}
