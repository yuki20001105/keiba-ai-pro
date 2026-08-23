import { NextRequest, NextResponse } from 'next/server'
import { spawn } from 'child_process'
import { existsSync } from 'fs'
import fs from 'fs/promises'
import os from 'os'
import path from 'path'
import {
  TargetedRefetchRequest,
  validateTargetedRefetchPlanReport,
  validateTargetedRefetchRequestBody,
} from '@/lib/targeted-refetch-plan-contract'
import { verifyRequestAuth } from '@/lib/server-auth'

export const runtime = 'nodejs'
export const maxDuration = 300

const PROJECT_ROOT = process.cwd()
const SCRIPT_PATH = path.join(PROJECT_ROOT, 'scripts', 'plan_p0_targeted_refetch.py')
const VENV_PYTHON = path.join(PROJECT_ROOT, 'python-api', '.venv', 'Scripts', 'python.exe')
const PY_TIMEOUT_MS = 120_000
const MAX_STDOUT_BYTES = 128 * 1024
const MAX_STDERR_BYTES = 64 * 1024
const MAX_REPORT_BYTES = 2 * 1024 * 1024

let plannerInFlight = false

function getPythonExecutable(): string {
  return existsSync(VENV_PYTHON) ? VENV_PYTHON : 'python'
}

function noStoreJson(body: unknown, status: number): NextResponse {
  return NextResponse.json(body, {
    status,
    headers: {
      'Cache-Control': 'no-store',
    },
  })
}

function maskErrorMessage(msg: string): string {
  if (!msg) return 'request failed'
  return msg
    .slice(0, 200)
    .replace(/[A-Za-z]:\\[^\s]+/g, '[REDACTED_PATH]')
    .replace(/\/[^\s]+/g, '[REDACTED_PATH]')
    .replace(/(sb_secret_[A-Za-z0-9_-]+)/g, '[REDACTED_SECRET]')
}

async function safeUnlink(filePath: string | null): Promise<void> {
  if (!filePath) return
  try {
    await fs.unlink(filePath)
  } catch {
    // best effort cleanup
  }
}

async function safeRemoveDir(dirPath: string | null): Promise<void> {
  if (!dirPath) return
  try {
    await fs.rm(dirPath, { recursive: true, force: true })
  } catch {
    // best effort cleanup
  }
}

async function runPlanner(requestBody: TargetedRefetchRequest): Promise<
  | { ok: true; payload: unknown }
  | { ok: false; status: 500 | 502; error: string }
> {
  let outputPath: string | null = null
  let tmpDir: string | null = null

  try {
    tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'targeted-refetch-plan-'))
    outputPath = path.join(tmpDir, `${Date.now()}-${Math.floor(Math.random() * 10000)}.json`)

    const pyArgs = [
      SCRIPT_PATH,
      '--target',
      requestBody.target,
      '--max-targets',
      String(requestBody.max_targets),
      '--output',
      outputPath,
    ]

    const child = spawn(getPythonExecutable(), pyArgs, {
      cwd: PROJECT_ROOT,
      env: process.env,
      shell: false,
      windowsHide: true,
    })

    let stdoutBytes = 0
    let stderrBytes = 0
    let timedOut = false
    let oversize = false

    child.stdout.on('data', chunk => {
      if (oversize) return
      stdoutBytes += Buffer.byteLength(chunk)
      if (stdoutBytes > MAX_STDOUT_BYTES) {
        oversize = true
        child.kill()
      }
    })

    child.stderr.on('data', chunk => {
      if (oversize) return
      stderrBytes += Buffer.byteLength(chunk)
      if (stderrBytes > MAX_STDERR_BYTES) {
        oversize = true
        child.kill()
      }
    })

    const timeoutId = setTimeout(() => {
      timedOut = true
      child.kill()
    }, PY_TIMEOUT_MS)

    const exitCode = await new Promise<number | null>(resolve => {
      child.on('close', code => resolve(code))
      child.on('error', () => resolve(null))
    })
    clearTimeout(timeoutId)

    if (timedOut) {
      return { ok: false, status: 502, error: 'planner timeout' }
    }
    if (oversize) {
      return { ok: false, status: 502, error: 'planner output too large' }
    }
    if (exitCode !== 0) {
      return { ok: false, status: 500, error: 'planner execution failed' }
    }

    const stat = await fs.stat(outputPath)
    if (!Number.isFinite(stat.size) || stat.size <= 0) {
      return { ok: false, status: 502, error: 'planner report is empty' }
    }
    if (stat.size > MAX_REPORT_BYTES) {
      return { ok: false, status: 502, error: 'planner report too large' }
    }

    const raw = await fs.readFile(outputPath, 'utf-8')
    let payload: unknown
    try {
      payload = JSON.parse(raw)
    } catch {
      return { ok: false, status: 502, error: 'planner output parse failed' }
    }

    return { ok: true, payload }
  } catch {
    return { ok: false, status: 500, error: 'planner unavailable' }
  } finally {
    await safeUnlink(outputPath)
    await safeRemoveDir(tmpDir)
  }
}

export async function POST(request: NextRequest) {
  const authz = await verifyRequestAuth(request, { requirePremiumOrAdmin: true })
  if (!authz.ok) {
    return noStoreJson({ detail: authz.detail || 'forbidden' }, authz.status)
  }

  let rawBody: unknown
  try {
    rawBody = await request.json()
  } catch {
    return noStoreJson({ error: 'invalid JSON body' }, 400)
  }

  if (!rawBody || typeof rawBody !== 'object' || Array.isArray(rawBody)) {
    return noStoreJson({ error: 'request body must be an object' }, 400)
  }

  const validated = validateTargetedRefetchRequestBody(rawBody)
  if (!validated.ok) {
    return noStoreJson({ error: validated.error }, 400)
  }

  if (plannerInFlight) {
    return noStoreJson({ error: 'planner is busy' }, 429)
  }

  plannerInFlight = true
  try {
    const planner = await runPlanner(validated.value)
    if (!planner.ok) {
      return noStoreJson({ error: maskErrorMessage(planner.error) }, planner.status)
    }

    const parsed = validateTargetedRefetchPlanReport(planner.payload, validated.value)
    if (!parsed.ok) {
      return noStoreJson({ error: parsed.error }, 502)
    }

    return noStoreJson(
      {
        dry_run: true,
        read_only: true,
        execution_enabled: false,
        plan: parsed.plan,
      },
      200
    )
  } finally {
    plannerInFlight = false
  }
}
