/**
 * keiba-ai-pro スキル検証マスタースクリプト
 * 全スキルの verify() を並列実行してサマリーを表示します。
 *
 * 実行:
 *   npx tsx scripts/verify-skills.ts
 *   KEIBA_AUTH_TOKEN=<token> npx tsx scripts/verify-skills.ts
 *
 * 特定スキルのみ実行:
 *   npx tsx scripts/verify-skills.ts harvester trainer
 *
 * 環境変数:
 *   KEIBA_API_URL      FastAPI URL (デフォルト: http://localhost:8000)
 *   KEIBA_APP_URL      Next.js URL (デフォルト: http://localhost:3000)
 *   KEIBA_AUTH_TOKEN   Bearer トークン（未設定時は認証不要チェックのみ実施）
 */
import { printResult } from '../.github/skills/_shared/verify-utils.ts'
import type { SkillVerifyResult } from '../.github/skills/_shared/verify-utils.ts'

import { verify as verifyHarvester, SKILL as HARVESTER_SKILL } from '../.github/skills/harvester/verify.ts'
import { verify as verifyTrainer,   SKILL as TRAINER_SKILL   } from '../.github/skills/trainer/verify.ts'
import { verify as verifyOracle,    SKILL as ORACLE_SKILL    } from '../.github/skills/oracle/verify.ts'
import { verify as verifyLedger,    SKILL as LEDGER_SKILL    } from '../.github/skills/ledger/verify.ts'
import { verify as verifySysop,     SKILL as SYSOP_SKILL     } from '../.github/skills/sysop/verify.ts'
import { verify as verifyJobs,      SKILL as JOBS_SKILL      } from '../.github/skills/jobs/verify.ts'

// ── カラー定数 ──────────────────────────────────────────────────────────────
const C = {
  reset:  '\x1b[0m',
  bold:   '\x1b[1m',
  green:  '\x1b[32m',
  red:    '\x1b[31m',
  yellow: '\x1b[33m',
  gray:   '\x1b[90m',
  cyan:   '\x1b[36m',
  blue:   '\x1b[34m',
  white:  '\x1b[97m',
} as const

// ── スキル登録 ──────────────────────────────────────────────────────────────
const ALL_SKILLS: Record<string, () => Promise<SkillVerifyResult>> = {
  [HARVESTER_SKILL]: verifyHarvester,
  [TRAINER_SKILL]:   verifyTrainer,
  [ORACLE_SKILL]:    verifyOracle,
  [LEDGER_SKILL]:    verifyLedger,
  [SYSOP_SKILL]:     verifySysop,
  [JOBS_SKILL]:      verifyJobs,
}

// ── フィルタリング（CLI 引数） ──────────────────────────────────────────────
const args = process.argv.slice(2).filter(a => !a.startsWith('-'))
const targetSkills = args.length > 0
  ? Object.fromEntries(
      Object.entries(ALL_SKILLS).filter(([name]) => args.includes(name)),
    )
  : ALL_SKILLS

if (Object.keys(targetSkills).length === 0) {
  console.error(`指定したスキルが見つかりません: ${args.join(', ')}`)
  console.error(`使用可能なスキル: ${Object.keys(ALL_SKILLS).join(', ')}`)
  process.exit(1)
}

// ── ヘッダー ────────────────────────────────────────────────────────────────
function printHeader(): void {
  const now = new Date().toLocaleString('ja-JP')
  console.log()
  console.log(`${C.bold}${C.blue}╔════════════════════════════════════════════════════════════╗${C.reset}`)
  console.log(`${C.bold}${C.blue}║        keiba-ai-pro スキル検証レポート                     ║${C.reset}`)
  console.log(`${C.bold}${C.blue}╚════════════════════════════════════════════════════════════╝${C.reset}`)
  console.log(`  ${C.gray}実行日時: ${now}${C.reset}`)
  console.log(`  ${C.gray}対象スキル: ${Object.keys(targetSkills).join(', ')}${C.reset}`)
  const tokenStatus = process.env.KEIBA_AUTH_TOKEN
    ? `${C.green}設定済み${C.reset}`
    : `${C.yellow}未設定（認証なしチェックのみ）${C.reset}`
  console.log(`  ${C.gray}KEIBA_AUTH_TOKEN: ${tokenStatus}`)
}

// ── サマリーテーブル ─────────────────────────────────────────────────────────
function printSummary(results: SkillVerifyResult[]): void {
  const totalPassed  = results.reduce((s, r) => s + r.passed, 0)
  const totalFailed  = results.reduce((s, r) => s + r.failed, 0)
  const totalWarned  = results.reduce((s, r) => s + r.warned, 0)
  const totalChecks  = results.reduce((s, r) => s + r.checks.length, 0)
  const totalMs      = results.reduce((s, r) => s + r.totalMs, 0)

  console.log()
  console.log(`${C.bold}${C.blue}── サマリー ${'─'.repeat(50)}${C.reset}`)
  console.log()

  // 各スキルの結果行
  console.log(`  ${'スキル'.padEnd(14)} ${'説明'.padEnd(28)} 通過  失敗  警告   時間`)
  console.log(`  ${'─'.repeat(75)}`)
  for (const r of results) {
    const overall = r.failed > 0 ? `${C.red}FAIL${C.reset}` :
                    r.warned > 0 ? `${C.yellow}WARN${C.reset}` :
                                   `${C.green}PASS${C.reset}`
    const name  = r.skill.padEnd(14)
    const desc  = r.description.slice(0, 26).padEnd(26)
    const p     = String(r.passed).padStart(4)
    const f     = String(r.failed).padStart(4)
    const w     = String(r.warned).padStart(4)
    const ms    = `${r.totalMs}ms`.padStart(7)
    const fColor = r.failed > 0 ? C.red : C.gray
    const wColor = r.warned > 0 ? C.yellow : C.gray
    console.log(`  ${overall} ${name} ${desc}  ${C.green}${p}${C.reset}  ${fColor}${f}${C.reset}  ${wColor}${w}${C.reset}  ${C.gray}${ms}${C.reset}`)
  }

  console.log(`  ${'─'.repeat(75)}`)
  const overallOk = totalFailed === 0
  const finalLabel = overallOk
    ? `${C.bold}${C.green}全チェック通過 ✔${C.reset}`
    : `${C.bold}${C.red}${totalFailed}件のエラーあり ✘${C.reset}`
  console.log(`  ${finalLabel}   通過=${totalPassed} 失敗=${totalFailed} 警告=${totalWarned} 合計=${totalChecks}件  ${totalMs}ms`)
  console.log()
}

// ── メイン実行 ──────────────────────────────────────────────────────────────
printHeader()

const entries = Object.entries(targetSkills)
const results: SkillVerifyResult[] = []

// 順次実行（FastAPI への負荷を分散）
for (const [name, verifyFn] of entries) {
  let result: SkillVerifyResult
  try {
    result = await verifyFn()
  } catch (err) {
    // スキル全体が例外で落ちた場合のフォールバック
    result = {
      skill: name,
      agent: name,
      description: '検証中にエラー発生',
      checks: [{ name: '予期せぬエラー', status: 'fail', message: String(err), duration: 0 }],
      passed: 0, failed: 1, warned: 0, totalMs: 0,
    }
  }
  printResult(result)
  results.push(result)
}

printSummary(results)

const anyFailed = results.some(r => r.failed > 0)
process.exitCode = anyFailed ? 1 : 0
