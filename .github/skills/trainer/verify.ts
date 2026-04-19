/**
 * Trainer スキル検証スクリプト
 * 担当: LightGBM モデル学習・特徴量エンジニアリング・Optuna最適化
 *
 * 実行: npx tsx .github/skills/trainer/verify.ts
 *   or: KEIBA_AUTH_TOKEN=<token> npx tsx .github/skills/trainer/verify.ts
 */
import {
  apiGet,
  pass, fail, warn, skip, runCheck, requireAuth, buildResult, printResult, runIfMain,
} from '../_shared/verify-utils.ts'
import type { Check, SkillVerifyResult } from '../_shared/verify-utils.ts'

export const SKILL       = 'trainer'
export const AGENT       = 'Trainer（トレーナー）'
export const DESCRIPTION = 'モデル学習・特徴量エンジニアリング・Optuna最適化'

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

  // ── 認証要求チェック ──────────────────────────────────────────────────
  const authSkip = requireAuth('モデル一覧取得 (GET /api/models)')
  if (authSkip) {
    checks.push(authSkip)
    checks.push(skip('アクティブモデル確認', 'KEIBA_AUTH_TOKEN 未設定のためスキップ'))
    checks.push(skip('特徴量サマリー確認', 'KEIBA_AUTH_TOKEN 未設定のためスキップ'))
    checks.push(skip('特徴量重要度確認', 'KEIBA_AUTH_TOKEN 未設定のためスキップ'))
    checks.push(skip('INV-01 特徴量リーク防止確認', 'KEIBA_AUTH_TOKEN 未設定のためスキップ'))
  } else {
    // ── 2. モデル一覧 ────────────────────────────────────────────────────
    checks.push(await runCheck('モデル一覧取得 (GET /api/models)', async () => {
      const t = Date.now()
      const { status, data } = await apiGet('/api/models')
      if (status !== 200) return fail('モデル一覧取得 (GET /api/models)', `HTTP ${status}`, Date.now() - t)
      const models = (data as Record<string, unknown>)?.models
      const cnt = Array.isArray(models) ? models.length : 0
      if (cnt === 0)
        return warn('モデル一覧取得 (GET /api/models)', `モデルが0件 — 学習未実施の可能性`, Date.now() - t)
      // ターゲット種別を集計
      const targets = (models as Record<string, unknown>[]).map(m => m.target as string).join(', ')
      return pass('モデル一覧取得 (GET /api/models)', `${cnt}件 [${targets}]`, Date.now() - t)
    }))

    // ── 3. アクティブモデル確認 ───────────────────────────────────────────
    checks.push(await runCheck('アクティブモデル確認 (GET /api/models/active/info)', async () => {
      const t = Date.now()
      const { status, data } = await apiGet('/api/models/active/info')
      if (status === 404) return warn('アクティブモデル確認 (GET /api/models/active/info)', `モデル未設定`, Date.now() - t)
      if (status !== 200) return fail('アクティブモデル確認 (GET /api/models/active/info)', `HTTP ${status}`, Date.now() - t)
      const d = data as Record<string, unknown>
      const auc = Number(d?.auc ?? 0)
      const target = String(d?.target ?? '?')
      const feats  = Number(d?.feature_count ?? 0)
      const aucStatus = auc >= 0.80 ? pass : auc >= 0.70 ? warn : fail
      return aucStatus(
        'アクティブモデル確認 (GET /api/models/active/info)',
        `target=${target}  AUC=${auc.toFixed(4)}  特徴量=${feats}個`,
        Date.now() - t,
      )
    }))

    // ── 4. 特徴量サマリー ────────────────────────────────────────────────
    checks.push(await runCheck('特徴量サマリー (GET /api/features/summary)', async () => {
      const t = Date.now()
      const { status, data } = await apiGet('/api/features/summary')
      if (status !== 200) return fail('特徴量サマリー (GET /api/features/summary)', `HTTP ${status}`, Date.now() - t)
      return pass('特徴量サマリー (GET /api/features/summary)', `HTTP 200 — 特徴量情報取得OK`, Date.now() - t)
    }))

    // ── 5. 特徴量重要度 ──────────────────────────────────────────────────
    checks.push(await runCheck('特徴量重要度 (GET /api/features/importance)', async () => {
      const t = Date.now()
      const { status, data } = await apiGet('/api/features/importance?top_n=5')
      if (status !== 200) return fail('特徴量重要度 (GET /api/features/importance)', `HTTP ${status}`, Date.now() - t)
      const items = (data as Record<string, unknown>)?.features
      const cnt = Array.isArray(items) ? items.length : 0
      // Top特徴量を抽出
      const top1 = Array.isArray(items) ? (items[0] as Record<string, unknown>)?.feature ?? '?' : '?'
      return pass('特徴量重要度 (GET /api/features/importance)', `Top特徴量: ${top1}  (${cnt}件取得)`, Date.now() - t)
    }))

    // ── 6. INV-01 確認: POST_RACE_FIELDS が feature_columns に含まれないこと ──
    checks.push(await runCheck('INV-01 データリーク防止確認', async () => {
      const t = Date.now()
      const { status, data } = await apiGet('/api/features/importance')
      if (status !== 200) return skip('INV-01 データリーク防止確認', `特徴量取得失敗 HTTP ${status}`)
      const items = ((data as Record<string, unknown>)?.features ?? []) as Record<string, unknown>[]
      const POST_RACE = ['finish', 'time', 'margin', 'last_3f_time', 'time_seconds', 'corner_1', 'corner_2']
      const leaked = items.filter(f => POST_RACE.includes(String(f?.feature ?? '')))
      if (leaked.length > 0)
        return fail('INV-01 データリーク防止確認', `未来情報リーク: ${leaked.map(f => f.feature).join(', ')}`, Date.now() - t)
      return pass('INV-01 データリーク防止確認', `POST_RACE_FIELDS 混入なし — リーク検出なし`, Date.now() - t)
    }))
  }

  return buildResult(SKILL, AGENT, DESCRIPTION, checks, startMs)
}

// 単体実行
await runIfMain(import.meta.url, verify)
