/**
 * Sysop スキル検証スクリプト
 * 担当: 認証・Supabase・デプロイ・スケジューラ・環境変数
 *
 * 実行: npx tsx .github/skills/sysop/verify.ts
 *   or: KEIBA_AUTH_TOKEN=<token> npx tsx .github/skills/sysop/verify.ts
 */
import {
  apiGet, appGet,
  pass, fail, warn, skip, runCheck, requireAuth, buildResult, runIfMain,
  API_URL, APP_URL, AUTH_TOKEN,
} from '../_shared/verify-utils.ts'
import type { Check, SkillVerifyResult } from '../_shared/verify-utils.ts'

export const SKILL       = 'sysop'
export const AGENT       = 'Sysop（システムオプ）'
export const DESCRIPTION = '認証・Supabase・デプロイ・スケジューラ・環境変数'

export async function verify(): Promise<SkillVerifyResult> {
  const startMs = Date.now()
  const checks: Check[] = []

  // ── 1. FastAPI サーバー起動確認 ───────────────────────────────────────
  checks.push(await runCheck('FastAPI 起動確認 (GET /)', async () => {
    const t = Date.now()
    const { status } = await apiGet('/')
    return status === 200
      ? pass('FastAPI 起動確認 (GET /)', `HTTP ${status} — ${API_URL}`, Date.now() - t)
      : fail('FastAPI 起動確認 (GET /)', `HTTP ${status}`, Date.now() - t)
  }))

  // ── 2. FastAPI ヘルスチェック ──────────────────────────────────────────
  checks.push(await runCheck('FastAPI ヘルスチェック (GET /health)', async () => {
    const t = Date.now()
    const { status, data } = await apiGet('/health')
    if (status !== 200) return fail('FastAPI ヘルスチェック (GET /health)', `HTTP ${status}`, Date.now() - t)
    const d = data as Record<string, unknown>
    const dbStatus = d?.database ?? d?.db ?? d?.status ?? 'ok'
    return pass('FastAPI ヘルスチェック (GET /health)', `status=${dbStatus}`, Date.now() - t)
  }))

  // ── 3. Next.js サーバー起動確認 ───────────────────────────────────────
  checks.push(await runCheck('Next.js 起動確認 (GET /)', async () => {
    const t = Date.now()
    const { status, ok } = await appGet('/')
    return ok
      ? pass('Next.js 起動確認 (GET /)', `HTTP ${status} — ${APP_URL}`, Date.now() - t)
      : fail('Next.js 起動確認 (GET /)', `HTTP ${status}`, Date.now() - t)
  }))

  // ── 4. 環境変数確認 ──────────────────────────────────────────────────
  checks.push(await runCheck('環境変数 KEIBA_API_URL 設定確認', async () => {
    const t = Date.now()
    if (!process.env.KEIBA_API_URL)
      return warn('環境変数 KEIBA_API_URL 設定確認', `未設定 — デフォルト(http://localhost:8000)を使用`, Date.now() - t)
    return pass('環境変数 KEIBA_API_URL 設定確認', process.env.KEIBA_API_URL, Date.now() - t)
  }))

  checks.push(await runCheck('環境変数 KEIBA_AUTH_TOKEN 設定確認', async () => {
    const t = Date.now()
    if (!AUTH_TOKEN)
      return warn('環境変数 KEIBA_AUTH_TOKEN 設定確認', `未設定 — 認証が必要なエンドポイントのテストをスキップ`, Date.now() - t)
    return pass('環境変数 KEIBA_AUTH_TOKEN 設定確認', `設定済み (${AUTH_TOKEN.slice(0, 8)}...)`, Date.now() - t)
  }))

  // ── 5. 認証エンドポイント疎通 ─────────────────────────────────────────
  // 認証なしで /api/* にアクセスすると 401/403 が返ることを確認
  checks.push(await runCheck('認証ミドルウェア動作確認 (401/403)', async () => {
    const t = Date.now()
    // 認証ヘッダーなしで保護エンドポイントにアクセス
    const res = await fetch(`${API_URL}/api/models`, { signal: AbortSignal.timeout(10_000) })
    const status = res.status
    if (status === 401 || status === 403)
      return pass('認証ミドルウェア動作確認 (401/403)', `HTTP ${status} — 認証ミドルウェア正常動作`, Date.now() - t)
    if (status === 200)
      return warn('認証ミドルウェア動作確認 (401/403)', `HTTP ${status} — 認証不要（開発モードまたは未設定）`, Date.now() - t)
    return warn('認証ミドルウェア動作確認 (401/403)', `HTTP ${status}`, Date.now() - t)
  }))

  const authSkip = requireAuth('スケジューラ状態確認 (GET /api/scheduler/status)')
  if (authSkip) {
    checks.push(authSkip)
    checks.push(skip('Supabase 接続確認', 'KEIBA_AUTH_TOKEN 未設定のためスキップ'))
  } else {
    // ── 6. スケジューラ状態確認 ──────────────────────────────────────────
    checks.push(await runCheck('スケジューラ状態確認 (GET /api/scheduler/status)', async () => {
      const t = Date.now()
      const { status, data } = await apiGet('/api/scheduler/status')
      if (status === 404) return warn('スケジューラ状態確認 (GET /api/scheduler/status)', `エンドポイント未実装`, Date.now() - t)
      if (status !== 200) return fail('スケジューラ状態確認 (GET /api/scheduler/status)', `HTTP ${status}`, Date.now() - t)
      const d = data as Record<string, unknown>
      const running = d?.running ?? d?.is_running ?? d?.status
      return pass('スケジューラ状態確認 (GET /api/scheduler/status)', `running=${running}`, Date.now() - t)
    }))

    // ── 7. Supabase 接続確認（認証エンドポイント経由） ───────────────────
    checks.push(await runCheck('Supabase 接続確認 (GET /api/auth/me)', async () => {
      const t = Date.now()
      const { status, data } = await apiGet('/api/auth/me')
      if (status === 401) return warn('Supabase 接続確認 (GET /api/auth/me)', `HTTP 401 — トークン無効または期限切れ`, Date.now() - t)
      if (status === 404) return warn('Supabase 接続確認 (GET /api/auth/me)', `エンドポイント未実装`, Date.now() - t)
      if (status !== 200) return fail('Supabase 接続確認 (GET /api/auth/me)', `HTTP ${status}`, Date.now() - t)
      const d = data as Record<string, unknown>
      const email = d?.email ?? d?.user?.email ?? '?'
      return pass('Supabase 接続確認 (GET /api/auth/me)', `email=${email}  Supabase接続OK`, Date.now() - t)
    }))
  }

  return buildResult(SKILL, AGENT, DESCRIPTION, checks, startMs)
}

// 単体実行
await runIfMain(import.meta.url, verify)
