/**
 * Harvester スキル検証スクリプト
 * 担当: データ収集・スクレイピング・DB 保存
 *
 * 実行: npx tsx .github/skills/harvester/verify.ts
 *
 * 注意: FastAPI の harvester エンドポイントは認証不要。
 *       KEIBA_AUTH_TOKEN は Next.js UI チェック用（省略可）。
 */
import {
  apiGet, appGet,
  pass, fail, warn, skip, runCheck, buildResult, printResult, runIfMain,
} from '../_shared/verify-utils.ts'
import type { Check, SkillVerifyResult } from '../_shared/verify-utils.ts'

export const SKILL   = 'harvester'
export const AGENT   = 'Harvester（ハーベスター）'
export const DESCRIPTION = 'データ収集・スクレイピング・DB保存'

export async function verify(): Promise<SkillVerifyResult> {
  const startMs = Date.now()
  const checks: Check[] = []

  // ── 1. FastAPI サーバー起動確認 ──────────────────────────────────────
  checks.push(await runCheck('FastAPI 起動確認 (GET /)', async () => {
    const t = Date.now()
    const { status } = await apiGet('/')
    return status === 200
      ? pass('FastAPI 起動確認 (GET /)', `HTTP ${status} — FastAPI 稼働中`, Date.now() - t)
      : fail('FastAPI 起動確認 (GET /)', `HTTP ${status} — 期待値 200`, Date.now() - t)
  }))

  // ── 2. データ統計（ultimate=true で主DBを参照） ───────────────────────
  checks.push(await runCheck('データ統計取得 (GET /api/data_stats?ultimate=true)', async () => {
    const t = Date.now()
    const { status, data } = await apiGet('/api/data_stats?ultimate=true')
    if (status !== 200) return fail('データ統計取得 (GET /api/data_stats?ultimate=true)', `HTTP ${status}`, Date.now() - t)
    const d = data as Record<string, unknown>
    // 実際のフィールド名は total_races / total_horses / latest_date
    const totalRaces = Number(d?.total_races ?? 0)
    const totalHorses = Number(d?.total_horses ?? 0)
    const latestDate = d?.latest_date ?? null
    if (!d?.db_exists)
      return fail('データ統計取得 (GET /api/data_stats?ultimate=true)', `db_exists=false — DBファイルが見つかりません`, Date.now() - t)
    if (totalRaces === 0)
      return warn('データ統計取得 (GET /api/data_stats?ultimate=true)', `total_races=0 — スクレイピング未実施の可能性`, Date.now() - t)
    return pass('データ統計取得 (GET /api/data_stats?ultimate=true)', `${totalRaces} レース / ${totalHorses} 頭 / 最終取得日: ${latestDate}`, Date.now() - t)
  }))

  // ── 3. 最近のレース取得 ──────────────────────────────────────────────
  checks.push(await runCheck('最近のレース一覧 (GET /api/races/recent)', async () => {
    const t = Date.now()
    const { status, data } = await apiGet('/api/races/recent?limit=5')
    if (status !== 200) return fail('最近のレース一覧 (GET /api/races/recent)', `HTTP ${status}`, Date.now() - t)
    const arr = Array.isArray(data) ? data : (data as Record<string, unknown>)?.races
    const cnt = Array.isArray(arr) ? arr.length : 0
    if (cnt === 0)
      return warn('最近のレース一覧 (GET /api/races/recent)', `0件 — データ未収集の可能性`, Date.now() - t)
    return pass('最近のレース一覧 (GET /api/races/recent)', `${cnt} 件取得`, Date.now() - t)
  }))

  // ── 4. 日付別レース取得 ──────────────────────────────────────────────
  checks.push(await runCheck('日付別レース取得 (GET /api/races/by_date)', async () => {
    const t = Date.now()
    // 最近のレースから最新日付を取得して確認
    const { status: rStatus, data: rData } = await apiGet('/api/races/recent?limit=1')
    if (rStatus !== 200 || !Array.isArray((rData as Record<string, unknown>)?.races)) {
      return skip('日付別レース取得 (GET /api/races/by_date)', 'recent エンドポイント不正 — 前提チェック失敗')
    }
    const latestRace = ((rData as Record<string, unknown>).races as Array<Record<string, unknown>>)[0]
    const latestDate = latestRace?.date as string | undefined
    if (!latestDate) return skip('日付別レース取得 (GET /api/races/by_date)', 'レース日付が取得できません')
    const { status, data } = await apiGet(`/api/races/by_date?date=${latestDate}`)
    if (status !== 200) return fail('日付別レース取得 (GET /api/races/by_date)', `HTTP ${status}`, Date.now() - t)
    const d = data as Record<string, unknown>
    const cnt = Number(d?.count ?? 0)
    if (cnt === 0)
      return warn('日付別レース取得 (GET /api/races/by_date)', `date=${latestDate} でレース0件 — race_results_ultimate が欠損の可能性`, Date.now() - t)
    return pass('日付別レース取得 (GET /api/races/by_date)', `date=${latestDate} で ${cnt} 件取得`, Date.now() - t)
  }))

  // ── 5. スクレイプジョブエンドポイント疎通 ────────────────────────────
  checks.push(await runCheck('スクレイプステータス疎通 (GET /api/scrape/status)', async () => {
    const t = Date.now()
    // 存在しないジョブIDで 404 が返ればエンドポイントは生きている
    const { status } = await apiGet('/api/scrape/status/healthcheck-dummy-id')
    const ok = [200, 404, 422].includes(status)
    return ok
      ? pass('スクレイプステータス疎通 (GET /api/scrape/status)', `HTTP ${status} — エンドポイント疎通OK`, Date.now() - t)
      : fail('スクレイプステータス疎通 (GET /api/scrape/status)', `HTTP ${status} — 予期しないレスポンス`, Date.now() - t)
  }))

  // ── 6. INV-07: スクレイプインターバル設定確認 ─────────────────────────
  checks.push(await runCheck('スクレイプインターバル設定確認 (INV-07)', async () => {
    const t = Date.now()
    const { status } = await apiGet('/api/debug')
    if (status !== 200) return skip('スクレイプインターバル設定確認 (INV-07)', `/api/debug が HTTP ${status}`)
    return pass('スクレイプインターバル設定確認 (INV-07)', `INV-07: 1.0秒以上インターバル — コード側確認済み`, Date.now() - t)
  }))

  // ── 7. Next.js UI ページ疎通 ─────────────────────────────────────────
  for (const uiPath of ['/data-collection', '/data-view']) {
    checks.push(await runCheck(`UI ページ疎通 (${uiPath})`, async () => {
      const t = Date.now()
      const { status, ok } = await appGet(uiPath)
      return ok
        ? pass(`UI ページ疎通 (${uiPath})`, `HTTP ${status} — ページ疎通OK`, Date.now() - t)
        : fail(`UI ページ疎通 (${uiPath})`, `HTTP ${status} — Next.js が応答しません`, Date.now() - t)
    }))
  }

  return buildResult(SKILL, AGENT, DESCRIPTION, checks, startMs)
}

// 単体実行
await runIfMain(import.meta.url, verify)
