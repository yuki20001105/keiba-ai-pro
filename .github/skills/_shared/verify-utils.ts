/**
 * keiba-ai-pro スキル検証ユーティリティ
 * 各スキルの verify.ts から import して使用する共通モジュール。
 *
 * 実行方法:
 *   npx tsx .github/skills/harvester/verify.ts
 *   KEIBA_AUTH_TOKEN=<token> npx tsx .github/skills/harvester/verify.ts
 */

// ── 環境変数 ──────────────────────────────────────────────────────────────
export const API_URL    = process.env.KEIBA_API_URL    ?? 'http://localhost:8000'
export const APP_URL    = process.env.KEIBA_APP_URL    ?? 'http://localhost:3000'
export const AUTH_TOKEN = process.env.KEIBA_AUTH_TOKEN ?? ''

// ── 型定義 ────────────────────────────────────────────────────────────────
export type CheckStatus = 'pass' | 'fail' | 'warn' | 'skip'

export interface Check {
  name: string
  status: CheckStatus
  message: string
  duration: number
}

export interface SkillVerifyResult {
  skill: string
  agent: string
  description: string
  checks: Check[]
  passed: number
  failed: number
  warned: number
  totalMs: number
}

// ── ターミナル カラー ──────────────────────────────────────────────────────
const C = {
  reset:  '\x1b[0m',
  bold:   '\x1b[1m',
  green:  '\x1b[32m',
  red:    '\x1b[31m',
  yellow: '\x1b[33m',
  gray:   '\x1b[90m',
  cyan:   '\x1b[36m',
  white:  '\x1b[97m',
} as const

// ── HTTP ヘルパー ──────────────────────────────────────────────────────────
/** FastAPI (:8000) への認証付き GET */
export async function apiGet(
  path: string,
  timeoutMs = 10_000,
): Promise<{ status: number; data: unknown }> {
  const headers: Record<string, string> = {}
  if (AUTH_TOKEN) headers['Authorization'] = `Bearer ${AUTH_TOKEN}`
  try {
    const res = await fetch(`${API_URL}${path}`, {
      headers,
      signal: AbortSignal.timeout(timeoutMs),
    })
    let data: unknown = null
    try { data = await res.json() } catch { /* non-JSON body */ }
    return { status: res.status, data }
  } catch (err) {
    throw new Error(`fetch ${API_URL}${path} → ${err instanceof Error ? err.message : String(err)}`)
  }
}

/** Next.js (:3000) へのシンプルな GET（健全性確認用） */
export async function appGet(
  path: string,
  timeoutMs = 10_000,
): Promise<{ status: number; ok: boolean }> {
  try {
    const res = await fetch(`${APP_URL}${path}`, {
      signal: AbortSignal.timeout(timeoutMs),
    })
    return { status: res.status, ok: res.ok }
  } catch (err) {
    throw new Error(`fetch ${APP_URL}${path} → ${err instanceof Error ? err.message : String(err)}`)
  }
}

// ── Check ビルダー ─────────────────────────────────────────────────────────
export function pass(name: string, message: string, duration: number): Check {
  return { name, status: 'pass', message, duration }
}
export function fail(name: string, message: string, duration: number): Check {
  return { name, status: 'fail', message, duration }
}
export function warn(name: string, message: string, duration: number): Check {
  return { name, status: 'warn', message, duration }
}
export function skip(name: string, message: string): Check {
  return { name, status: 'skip', message, duration: 0 }
}

/** try/catch でラップしたチェックを安全に実行する */
export async function runCheck(
  name: string,
  fn: () => Promise<Check>,
): Promise<Check> {
  try {
    return await fn()
  } catch (err) {
    return fail(name, err instanceof Error ? err.message : String(err), 0)
  }
}

// ── 結果集計 ──────────────────────────────────────────────────────────────
export function buildResult(
  skill: string,
  agent: string,
  description: string,
  checks: Check[],
  startMs: number,
): SkillVerifyResult {
  return {
    skill,
    agent,
    description,
    checks,
    passed:  checks.filter(c => c.status === 'pass').length,
    failed:  checks.filter(c => c.status === 'fail').length,
    warned:  checks.filter(c => c.status === 'warn').length,
    totalMs: Date.now() - startMs,
  }
}

// ── 表示 ──────────────────────────────────────────────────────────────────
const STATUS_ICON: Record<CheckStatus, string> = {
  pass: `${C.green}✔${C.reset}`,
  fail: `${C.red}✘${C.reset}`,
  warn: `${C.yellow}⚠${C.reset}`,
  skip: `${C.gray}─${C.reset}`,
}

export function printResult(r: SkillVerifyResult): void {
  const overall = r.failed > 0 ? `${C.red}FAIL${C.reset}` :
                  r.warned > 0 ? `${C.yellow}WARN${C.reset}` :
                                 `${C.green}PASS${C.reset}`

  console.log()
  console.log(`${C.bold}${C.cyan}【${r.agent}】${r.description}${C.reset}  ${overall}`)
  console.log(`${C.gray}${'─'.repeat(60)}${C.reset}`)

  for (const c of r.checks) {
    const icon = STATUS_ICON[c.status]
    const dur  = c.duration > 0 ? `${C.gray} (${c.duration}ms)${C.reset}` : ''
    console.log(`  ${icon}  ${c.name.padEnd(42)} ${c.message}${dur}`)
  }

  console.log(`${C.gray}${'─'.repeat(60)}${C.reset}`)
  console.log(
    `  ${C.green}通過: ${r.passed}${C.reset}  ` +
    `${r.failed > 0 ? C.red : C.gray}失敗: ${r.failed}${C.reset}  ` +
    `${r.warned > 0 ? C.yellow : C.gray}警告: ${r.warned}${C.reset}  ` +
    `${C.gray}${r.totalMs}ms${C.reset}`,
  )
}

/** `AUTH_TOKEN` が未設定のとき認証が必要なチェックをスキップする */
export function requireAuth(name: string): Check | null {
  if (!AUTH_TOKEN) {
    return skip(name, `KEIBA_AUTH_TOKEN 未設定のためスキップ`)
  }
  return null
}

/** スクリプトを単体実行したときのエントリポイント */
export async function runStandalone(
  verifyFn: () => Promise<SkillVerifyResult>,
): Promise<void> {
  const result = await verifyFn()
  printResult(result)
  // process.exit() は Windows の UV handle assertion を引き起こすため
  // exitCode を設定して自然終了させる
  process.exitCode = result.failed > 0 ? 1 : 0
}

/**
 * 当ファイルが直接実行されたときだけ verifyFn を呼び出すヘルパー。
 * Windows の `file:///C:/...` と `C:\...` のパス差異を吸収する。
 *
 * 使用例（各 verify.ts 末尾）:
 *   await runIfMain(import.meta.url, verify)
 */
export async function runIfMain(
  importMetaUrl: string,
  verifyFn: () => Promise<SkillVerifyResult>,
): Promise<void> {
  const { fileURLToPath } = await import('node:url')
  const thisFile = fileURLToPath(importMetaUrl).replace(/\\/g, '/')
  const argv1    = (process.argv[1] ?? '').replace(/\\/g, '/')
  if (argv1 === thisFile || argv1.endsWith(thisFile.replace(/^[A-Za-z]:/, ''))) {
    await runStandalone(verifyFn)
  }
}
