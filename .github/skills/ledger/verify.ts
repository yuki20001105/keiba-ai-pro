/**
 * Ledger スキル検証スクリプト
 * 担当: 購入履歴管理・的中/外れ結果入力・回収率計算・ダッシュボード
 *
 * 実行: npx tsx .github/skills/ledger/verify.ts
 *   or: KEIBA_AUTH_TOKEN=<token> npx tsx .github/skills/ledger/verify.ts
 */
import {
  apiGet,
  pass, fail, warn, skip, runCheck, requireAuth, buildResult, runIfMain,
} from '../_shared/verify-utils.ts'
import type { Check, SkillVerifyResult } from '../_shared/verify-utils.ts'

export const SKILL       = 'ledger'
export const AGENT       = 'Ledger（レジャー）'
export const DESCRIPTION = '購入履歴管理・的中追跡・ROI計算'

export async function verify(): Promise<SkillVerifyResult> {
  const startMs = Date.now()
  const checks: Check[] = []

  // ── 1. FastAPI 起動確認 ───────────────────────────────────────────────
  checks.push(await runCheck('FastAPI 起動確認 (GET /)', async () => {
    const t = Date.now()
    const { status } = await apiGet('/')
    return status === 200
      ? pass('FastAPI 起動確認 (GET /)', `HTTP ${status} — 稼働中`, Date.now() - t)
      : fail('FastAPI 起動確認 (GET /)', `HTTP ${status}`, Date.now() - t)
  }))

  const authSkip = requireAuth('予測履歴取得 (GET /api/prediction-history)')
  if (authSkip) {
    checks.push(authSkip)
    checks.push(skip('ROI/EV 統計確認', 'KEIBA_AUTH_TOKEN 未設定のためスキップ'))
    checks.push(skip('実結果照合確認', 'KEIBA_AUTH_TOKEN 未設定のためスキップ'))
    checks.push(skip('購入履歴エンドポイント疎通', 'KEIBA_AUTH_TOKEN 未設定のためスキップ'))
  } else {
    // ── 2. 予測履歴エンドポイント ─────────────────────────────────────────
    checks.push(await runCheck('予測履歴取得 (GET /api/prediction-history)', async () => {
      const t = Date.now()
      const { status, data } = await apiGet('/api/prediction-history?limit=10')
      if (status !== 200) return fail('予測履歴取得 (GET /api/prediction-history)', `HTTP ${status}`, Date.now() - t)
      const d = data as Record<string, unknown>
      const races = d?.races
      const cnt = Array.isArray(races) ? races.length : 0
      if (cnt === 0) return warn('予測履歴取得 (GET /api/prediction-history)', `0件 — 予測未実施`, Date.now() - t)
      return pass('予測履歴取得 (GET /api/prediction-history)', `${cnt}件の予測レース取得`, Date.now() - t)
    }))

    // ── 3. ROI/EV 統計確認 ───────────────────────────────────────────────
    checks.push(await runCheck('ROI・EV統計確認 (stats フィールド)', async () => {
      const t = Date.now()
      const { status, data } = await apiGet('/api/prediction-history?limit=50')
      if (status !== 200) return fail('ROI・EV統計確認 (stats フィールド)', `HTTP ${status}`, Date.now() - t)
      const d = data as Record<string, unknown>
      const stats = d?.stats as Record<string, unknown> | undefined
      if (!stats) return warn('ROI・EV統計確認 (stats フィールド)', `stats フィールドなし — API 修正が必要な可能性`, Date.now() - t)
      const roi = stats?.roi !== null && stats?.roi !== undefined ? `${Number(stats.roi).toFixed(1)}%` : '—'
      const avgEv = stats?.avg_ev !== null && stats?.avg_ev !== undefined ? Number(stats.avg_ev).toFixed(3) : '—'
      return pass('ROI・EV統計確認 (stats フィールド)', `ROI=${roi}  avg_ev=${avgEv}  EV率=${stats?.positive_ev_rate ?? '—'}`, Date.now() - t)
    }))

    // ── 4. 実結果照合確認 ────────────────────────────────────────────────
    // actual_finish が設定されているレコードが存在するかを確認
    checks.push(await runCheck('実結果照合確認 (actual_finish 存在確認)', async () => {
      const t = Date.now()
      const { status, data } = await apiGet('/api/prediction-history?limit=20')
      if (status !== 200) return skip('実結果照合確認 (actual_finish 存在確認)', `HTTP ${status}`)
      const races = (data as Record<string, unknown>)?.races ?? []
      if (!Array.isArray(races) || races.length === 0) return skip('実結果照合確認 (actual_finish 存在確認)', `履歴0件`)
      const allPreds = (races as Record<string, unknown>[]).flatMap(r => {
        const preds = r.predictions as Record<string, unknown>[] ?? []
        return preds
      })
      const withResult = allPreds.filter(p => p.actual_finish !== null && p.actual_finish !== undefined)
      const ratio = allPreds.length > 0 ? `${withResult.length}/${allPreds.length}` : '0/0'
      if (withResult.length === 0)
        return warn('実結果照合確認 (actual_finish 存在確認)', `実結果0件 — race_results_ultimate との照合未完了の可能性`, Date.now() - t)
      return pass('実結果照合確認 (actual_finish 存在確認)', `実結果あり: ${ratio}件`, Date.now() - t)
    }))

    // ── 5. 購入履歴エンドポイント疎通 ─────────────────────────────────────
    checks.push(await runCheck('購入履歴エンドポイント疎通 (GET /api/purchase_history)', async () => {
      const t = Date.now()
      const { status } = await apiGet('/api/purchase_history?limit=1')
      // 200 or 404(データなし) or 422(パラメータエラー) はエンドポイント疎通OK
      const ok = [200, 404, 422].includes(status)
      return ok
        ? pass('購入履歴エンドポイント疎通 (GET /api/purchase_history)', `HTTP ${status} — 疎通OK`, Date.now() - t)
        : fail('購入履歴エンドポイント疎通 (GET /api/purchase_history)', `HTTP ${status}`, Date.now() - t)
    }))

    // ── 6. 統計エンドポイント疎通 ─────────────────────────────────────────
    checks.push(await runCheck('統計エンドポイント疎通 (GET /api/statistics)', async () => {
      const t = Date.now()
      const { status } = await apiGet('/api/statistics')
      const ok = [200, 404, 422].includes(status)
      return ok
        ? pass('統計エンドポイント疎通 (GET /api/statistics)', `HTTP ${status} — 疎通OK`, Date.now() - t)
        : fail('統計エンドポイント疎通 (GET /api/statistics)', `HTTP ${status}`, Date.now() - t)
    }))
  }

  return buildResult(SKILL, AGENT, DESCRIPTION, checks, startMs)
}

// 単体実行
await runIfMain(import.meta.url, verify)
