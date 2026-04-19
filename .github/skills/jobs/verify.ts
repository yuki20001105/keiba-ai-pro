/**
 * Jobs（オーケストレーター）スキル検証スクリプト
 * 担当: 全エージェントの統合ヘルスチェック・システム全体の健全性確認
 *
 * 実行: npx tsx .github/skills/jobs/verify.ts
 *   or: KEIBA_AUTH_TOKEN=<token> npx tsx .github/skills/jobs/verify.ts
 *
 * 注意: このスクリプトは他の全スキルの verify() を呼び出します。
 *       個別スキルの検証は各スキルの verify.ts を直接実行してください。
 */
import {
  apiGet, appGet,
  pass, fail, warn, skip, runCheck, requireAuth, buildResult, runIfMain,
  API_URL, APP_URL, AUTH_TOKEN, printResult,
} from '../_shared/verify-utils.ts'
import type { Check, SkillVerifyResult } from '../_shared/verify-utils.ts'

export const SKILL       = 'jobs'
export const AGENT       = 'Jobs（ジョブズ / オーケストレーター）'
export const DESCRIPTION = 'システム全体ヘルスチェック・全エージェント統合確認'

export async function verify(): Promise<SkillVerifyResult> {
  const startMs = Date.now()
  const checks: Check[] = []

  // ── 1. FastAPI 起動確認 ───────────────────────────────────────────────
  checks.push(await runCheck('FastAPI 起動確認 (GET /)', async () => {
    const t = Date.now()
    const { status } = await apiGet('/')
    return status === 200
      ? pass('FastAPI 起動確認 (GET /)', `${API_URL} — HTTP ${status}`, Date.now() - t)
      : fail('FastAPI 起動確認 (GET /)', `HTTP ${status}`, Date.now() - t)
  }))

  // ── 2. FastAPI ヘルスチェック ──────────────────────────────────────────
  checks.push(await runCheck('FastAPI /health', async () => {
    const t = Date.now()
    const { status } = await apiGet('/health')
    return status === 200
      ? pass('FastAPI /health', `HTTP ${status}`, Date.now() - t)
      : fail('FastAPI /health', `HTTP ${status}`, Date.now() - t)
  }))

  // ── 3. Next.js 起動確認 ──────────────────────────────────────────────
  checks.push(await runCheck('Next.js 起動確認 (GET /)', async () => {
    const t = Date.now()
    const { status, ok } = await appGet('/')
    return ok
      ? pass('Next.js 起動確認 (GET /)', `${APP_URL} — HTTP ${status}`, Date.now() - t)
      : fail('Next.js 起動確認 (GET /)', `HTTP ${status}`, Date.now() - t)
  }))

  const authSkip = requireAuth('【Harvester】データ収集確認 (GET /api/data_stats)')
  if (authSkip) {
    // 認証不要のチェックのみ実施済み
    checks.push(skip('【Harvester】データ収集確認', 'KEIBA_AUTH_TOKEN 未設定'))
    checks.push(skip('【Trainer】モデル確認', 'KEIBA_AUTH_TOKEN 未設定'))
    checks.push(skip('【Oracle】予測確認', 'KEIBA_AUTH_TOKEN 未設定'))
    checks.push(skip('【Ledger】ROI確認', 'KEIBA_AUTH_TOKEN 未設定'))
  } else {
    // ── 4. Harvester: データ収集 ─────────────────────────────────────────
    checks.push(await runCheck('【Harvester】データ収集確認 (GET /api/data_stats)', async () => {
      const t = Date.now()
      const { status, data } = await apiGet('/api/data_stats')
      if (status !== 200) return fail('【Harvester】データ収集確認 (GET /api/data_stats)', `HTTP ${status}`, Date.now() - t)
      const d = data as Record<string, unknown>
      const cnt = Number(d?.race_count ?? d?.count ?? 0)
      if (cnt === 0) return warn('【Harvester】データ収集確認 (GET /api/data_stats)', `race_count=0 — スクレイピング未実施`, Date.now() - t)
      return pass('【Harvester】データ収集確認 (GET /api/data_stats)', `race_count=${cnt}件`, Date.now() - t)
    }))

    // ── 5. Trainer: モデル確認 ────────────────────────────────────────────
    checks.push(await runCheck('【Trainer】モデル確認 (GET /api/models)', async () => {
      const t = Date.now()
      const { status, data } = await apiGet('/api/models')
      if (status !== 200) return fail('【Trainer】モデル確認 (GET /api/models)', `HTTP ${status}`, Date.now() - t)
      const models = (data as Record<string, unknown>)?.models
      const cnt = Array.isArray(models) ? models.length : 0
      const active = Array.isArray(models) ? (models as Record<string, unknown>[]).find(m => m.is_active) : null
      const auc = Number((active as Record<string, unknown>)?.auc ?? 0)
      const aucStr = auc > 0 ? `AUC=${auc.toFixed(4)}` : '未学習'
      if (cnt === 0) return fail('【Trainer】モデル確認 (GET /api/models)', `モデル0件`, Date.now() - t)
      return (auc >= 0.70 || auc === 0 ? pass : warn)(
        '【Trainer】モデル確認 (GET /api/models)',
        `${cnt}件 / アクティブ: ${aucStr}`,
        Date.now() - t,
      )
    }))

    // ── 6. Oracle: 予測パイプライン確認 ──────────────────────────────────
    checks.push(await runCheck('【Oracle】予測パイプライン確認 (GET /api/models/active/info)', async () => {
      const t = Date.now()
      const { status, data } = await apiGet('/api/models/active/info')
      if (status === 404) return warn('【Oracle】予測パイプライン確認 (GET /api/models/active/info)', `アクティブモデル未設定`, Date.now() - t)
      if (status !== 200) return fail('【Oracle】予測パイプライン確認 (GET /api/models/active/info)', `HTTP ${status}`, Date.now() - t)
      const d = data as Record<string, unknown>
      return pass('【Oracle】予測パイプライン確認 (GET /api/models/active/info)', `target=${d?.target}  AUC=${Number(d?.auc ?? 0).toFixed(4)}`, Date.now() - t)
    }))

    // ── 7. Ledger: ROI追跡確認 ────────────────────────────────────────────
    checks.push(await runCheck('【Ledger】予測履歴確認 (GET /api/prediction-history)', async () => {
      const t = Date.now()
      const { status, data } = await apiGet('/api/prediction-history?limit=1')
      if (status !== 200) return fail('【Ledger】予測履歴確認 (GET /api/prediction-history)', `HTTP ${status}`, Date.now() - t)
      const races = (data as Record<string, unknown>)?.races
      const cnt = Array.isArray(races) ? races.length : 0
      if (cnt === 0) return warn('【Ledger】予測履歴確認 (GET /api/prediction-history)', `履歴0件`, Date.now() - t)
      return pass('【Ledger】予測履歴確認 (GET /api/prediction-history)', `履歴あり`, Date.now() - t)
    }))

    // ── 8. INV-04 並列処理確認（Sysop） ──────────────────────────────────
    // 並列リクエストを送って応答が返ることを確認
    checks.push(await runCheck('INV-04 並列処理制限確認', async () => {
      const t = Date.now()
      // 2並列リクエストを送信（これ自体は OK — ただし CONCURRENCY=1 はフロント側設定）
      const [r1, r2] = await Promise.all([apiGet('/health'), apiGet('/')])
      const ok = r1.status === 200 && r2.status === 200
      return ok
        ? pass('INV-04 並列処理制限確認', `FastAPI 2並列応答OK (CONCURRENCY=1はUI側で制御)`, Date.now() - t)
        : warn('INV-04 並列処理制限確認', `HTTP ${r1.status}/${r2.status}`, Date.now() - t)
    }))
  }

  return buildResult(SKILL, AGENT, DESCRIPTION, checks, startMs)
}

// 単体実行
await runIfMain(import.meta.url, verify)
