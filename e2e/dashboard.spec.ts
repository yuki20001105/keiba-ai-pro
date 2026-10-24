import { test, expect, type Page } from '@playwright/test'
import { mockAuth } from './helpers/mock-api'
import type { PerformanceRecord, PerformanceSummary, SelectionDiagnostics, SelectionCalibrationMetrics } from '../src/lib/performance-records'

const emptySummary: PerformanceSummary = {
  settled_cost: 0, settled_return: 0, profit: 0, roi: null,
  settled_races: 0, hit_races: 0, pending_races: 0, pending_cost: 0, skipped_races: 0,
}

function selectionDiagnostics(mode: 'paper' | 'actual' = 'paper'): SelectionDiagnostics {
  const empty: SelectionCalibrationMetrics = {
    ticket_count: 0, race_count: 0, date_count: 0, mean_probability: null, hit_rate: null,
    bias_predicted_minus_actual: null, mean_probability_ci95: null, hit_rate_ci95: null,
    bias_ci95: null, bootstrap_valid_samples: 0, interval_status: 'no_observations',
  }
  const observed: SelectionCalibrationMetrics = {
    ticket_count: 4, race_count: 4, date_count: 3, mean_probability: .3, hit_rate: .25,
    bias_predicted_minus_actual: .05, mean_probability_ci95: [.25, .35],
    hit_rate_ci95: [0, .5], bias_ci95: [-.2, .3],
    bootstrap_valid_samples: 2000, interval_status: 'descriptive_interval',
  }
  const lower = [1, 3, 5, 10, 20, 30]
  return {
    schema: 'recorded-selection-calibration-v1', label: '観測済み買い目の校正診断',
    observational: true, unknown_holdout: false, purchase_authority: false,
    scope: { mode, date_from: null, date_to: null, record_count: 8, includes_full_filtered_set: true },
    status_counts: { settled: 4, pending: 1, payout_pending: 1, review: 1, skipped: 1, other: 0 },
    sample: { eligible_tickets: 4, eligible_races: 4, eligible_dates: 3, excluded_records: 4, excluded_tickets: 3 },
    excluded_counts: { pending: 1, payout_pending: 1, review: 1, no_selection: 1 },
    excluded_ticket_counts: { pending: 1, payout_pending: 1, review: 1 },
    groups: [{
      ...observed, model_id: 'fixture-calibrated-model', policy_version: 'fixture-policy-v1', bet_type: '単勝',
      odds_bands: lower.map((value, index) => ({
        ...(index === 1 ? observed : empty), band: `fixture-band-${index}`,
        label: `${value}–${lower[index + 1] ?? '+'}`, lower: value, upper: lower[index + 1] ?? null,
      })),
    }],
    bootstrap: {
      method: 'paired_whole_race_date_percentile', confidence_level: .95, repetitions: 2000,
      seed: 20261012, interval_scope: 'conditional_on_verified_settled_recorded_selection',
      simultaneous_intervals: false, multiple_comparisons_adjusted: false,
    },
  }
}

function record(overrides: Partial<PerformanceRecord> = {}): PerformanceRecord {
  return {
    id: 'paper-settled', race_id: '202610100101',
    race_info: { date: '2026-10-10', race_name: 'AI参考・確定レース', venue: '東京', race_no: 1 },
    model_id: 'release-fixture-model', policy_version: 'fixture-policy-v1',
    decision: 'bet', reason: '固定したAI選定', mode: 'paper',
    tickets: [{ bet_type: '単勝', combination: '1', horse_numbers: [1], stake: 1000, actual_return: 1800 }],
    total_cost: 1000, actual_return: 1800, profit: 800, status: 'settled',
    created_at: '2026-10-10T00:00:00+09:00', settled_at: '2026-10-10T16:00:00+09:00',
    ...overrides,
  }
}

const paperRecords = [
  record(),
  record({
    id: 'paper-pending', race_id: '202610100102',
    race_info: { date: '2026-10-10', race_name: 'AI参考・結果待ちレース', venue: '東京', race_no: 2 },
    tickets: [{ bet_type: 'ワイド', combination: '1-2', horse_numbers: [1, 2], stake: 300 }],
    total_cost: 300, actual_return: null, profit: null, status: 'pending', settled_at: null,
  }),
  record({
    id: 'paper-skipped', race_id: '202610100103',
    race_info: { date: '2026-10-10', race_name: 'AI参考・見送りレース', venue: '東京', race_no: 3 },
    tickets: [], decision: 'skip', reason: '優位性未確認のため見送り', total_cost: 0,
    actual_return: null, profit: null, status: 'skipped', settled_at: null,
  }),
]
const actualRecords = [record({
  id: 'actual-settled', mode: 'actual',
  race_info: { date: '2026-10-10', race_name: '購入済み・確定レース', venue: '京都', race_no: 1 },
  tickets: [{ bet_type: '単勝', combination: '1', horse_numbers: [1], stake: 200, actual_return: 600 }],
  total_cost: 200, actual_return: 600, profit: 400,
})]

async function mockDashboardApis(page: Page) {
  // Unknown application API requests are fulfilled locally, never forwarded to
  // the real server. Specific mocks registered below take precedence.
  await page.route('/api/**', route => route.fulfill({ status: 503, json: { detail: 'Unexpected fixture API' } }))
  await mockAuth(page, { role: 'user', tier: 'free' })
  await page.route('/api/performance**', route => {
    if (route.request().method() !== 'GET') return route.fulfill({ status: 405, json: { detail: 'Read-only fixture' } })
    const mode = new URL(route.request().url()).searchParams.get('mode') || 'paper'
    return route.fulfill({ json: mode === 'actual'
      ? { records: actualRecords, total_count: 1, summary: { ...emptySummary, settled_cost: 200, settled_return: 600, profit: 400, roi: 300, settled_races: 1, hit_races: 1 } }
      : { records: paperRecords, total_count: 3, summary: { ...emptySummary, settled_cost: 1000, settled_return: 1800, profit: 800, roi: 180, settled_races: 1, hit_races: 1, pending_races: 1, pending_cost: 300, skipped_races: 1 } },
    })
  })
  await page.route('/api/purchase-history**', route => route.fulfill({ json: { success: true, history: [{
    id: 'legacy-001', race_id: '202604070101', venue: '以前の東京', bet_type: '単勝',
    total_cost: 4000, actual_return: null, purchase_date: '2026-04-07', combinations: ['1'],
  }], count: 1 } }))
}

test.describe('成績確認・ローカルfixture', () => {
  test.beforeEach(async ({ page }) => { await mockDashboardApis(page) })

  test('AI参考を初期表示し、実購入と別集計であることが分かる', async ({ page }) => {
    await page.goto('/dashboard')
    await expect(page.getByRole('tab', { name: 'AI参考' })).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByRole('tab', { name: '購入済み' })).toHaveAttribute('aria-selected', 'false')
    await expect(page.getByText('AIが選んだ買い目のシミュレーションです。実購入とは別集計です。')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'AI参考・確定レース' })).toBeVisible()
    await expect(page.getByRole('heading', { name: '購入済み・確定レース' })).toHaveCount(0)
    await expect(page.getByRole('link', { name: /予測履歴/ })).toHaveAttribute('href', '/prediction-history')
  })

  test('４指標は精算済みだけを表示し、結果待ちの投資を混ぜない', async ({ page }) => {
    await page.goto('/dashboard')
    const summary = page.getByRole('region', { name: '確定分の成績' })
    await expect(summary.getByText('¥1,000', { exact: true })).toBeVisible()
    await expect(summary.getByText('¥1,800', { exact: true })).toBeVisible()
    await expect(summary.getByText('+¥800', { exact: true })).toBeVisible()
    await expect(summary.getByText('180.0%', { exact: true })).toBeVisible()
    await expect(summary.getByText('未確定 1レース（¥300・内訳未取得）')).toBeVisible()
    await expect(summary.getByText('見送り 1レース')).toBeVisible()
    await expect(summary.getByText('¥1,300', { exact: true })).toHaveCount(0)
    const pending = page.locator('article').filter({ has: page.getByRole('heading', { name: 'AI参考・結果待ちレース' }) })
    await expect(pending.getByText('結果待ち', { exact: true })).toBeVisible()
    await expect(pending.getByText('外れ', { exact: true })).toHaveCount(0)
  })

  test('購入済みタブは独立した成績だけを表示し、購入操作はしない', async ({ page }) => {
    await page.goto('/dashboard')
    await page.getByRole('tab', { name: '購入済み' }).click()
    await expect(page.getByRole('tab', { name: '購入済み' })).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByText('購入済みとして記録した買い目の成績です。アプリから購入は行いません。')).toBeVisible()
    await expect(page.getByRole('heading', { name: '購入済み・確定レース' })).toBeVisible()
    await expect(page.getByRole('heading', { name: /AI参考・/ })).toHaveCount(0)
    const summary = page.getByRole('region', { name: '確定分の成績' })
    await expect(summary.getByText('¥200', { exact: true })).toBeVisible()
    await expect(summary.getByText('300.0%', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: /購入する|結果を入力/ })).toHaveCount(0)
  })

  test('結果待ちしかない場合の回収率と収支は0ではなく未確定', async ({ page }) => {
    await page.route('/api/performance**', route => route.fulfill({ json: {
      records: [paperRecords[1]], total_count: 1,
      summary: { ...emptySummary, pending_races: 1, pending_cost: 300 },
    } }))
    await page.goto('/dashboard')
    const summary = page.getByRole('region', { name: '確定分の成績' })
    await expect(summary.getByText('回収率', { exact: true }).locator('..').getByText('未算出', { exact: true })).toBeVisible()
    await expect(summary.getByText('収支', { exact: true }).locator('..').getByText('—', { exact: true })).toBeVisible()
    await expect(summary.getByText('0.0%', { exact: true })).toHaveCount(0)
  })

  test('以前の購入記録は開いた時だけ読み込み、現行成績とは混ぜない', async ({ page }) => {
    let legacyRequests = 0
    await page.route('/api/purchase-history**', route => {
      legacyRequests++
      return route.fulfill({ json: { history: [{ id: 'legacy', race_id: '202604070101', venue: '以前の東京', bet_type: '単勝', total_cost: 4000, actual_return: null, purchase_date: '2026-04-07' }] } })
    })
    await page.goto('/dashboard')
    await expect(page.getByRole('heading', { name: 'AI参考・確定レース' })).toBeVisible()
    expect(legacyRequests).toBe(0)
    await page.getByText('以前の購入記録', { exact: true }).click()
    await expect.poll(() => legacyRequests).toBe(1)
    await expect(page.getByText('新しい成績とは別集計です。以前の未確定／外れは区別できないため、ここでは判定しません。')).toBeVisible()
    await expect(page.getByText(/以前の東京/)).toBeVisible()
    await expect(page.getByRole('region', { name: '確定分の成績' }).getByText('¥1,000', { exact: true })).toBeVisible()
  })

  test('レース詳細は折り畳み、必要な時だけモデルと選定理由を見られる', async ({ page }) => {
    await page.goto('/dashboard')
    const card = page.locator('article').filter({ has: page.getByRole('heading', { name: 'AI参考・確定レース' }) })
    await expect(card.getByText('release-fixture-model', { exact: true })).not.toBeVisible()
    await card.getByText('詳細', { exact: true }).click()
    await expect(card.getByText('release-fixture-model', { exact: true })).toBeVisible()
    await expect(card.getByText('固定したAI選定', { exact: true })).toBeVisible()
  })

  test('記録なしでは予測へ進める空状態を表示する', async ({ page }) => {
    await page.route('/api/performance**', route => route.fulfill({ json: { records: [], total_count: 0, summary: emptySummary } }))
    await page.goto('/dashboard')
    await expect(page.getByText('まだ成績がありません')).toBeVisible()
    await expect(page.getByRole('region', { name: 'レース別の成績' }).getByRole('link', { name: /予測実行へ/ })).toHaveAttribute('href', '/predict-batch')
  })

  test('取得失敗は理由を表示し、更新で再確認できる', async ({ page }) => {
    let available = false
    await page.route('/api/performance**', route => {
      return !available
        ? route.fulfill({ status: 503, json: { detail: '成績サーバーの一時停止（fixture）' } })
        : route.fulfill({ json: { records: [paperRecords[0]], total_count: 1, summary: { ...emptySummary, settled_cost: 1000, settled_return: 1800, profit: 800, roi: 180, settled_races: 1, hit_races: 1 } } })
    })
    await page.goto('/dashboard')
    const error = page.locator('main').getByRole('alert')
    await expect(error).toHaveText('成績サーバーの一時停止（fixture）')
    available = true
    await page.getByRole('button', { name: '更新', exact: true }).click()
    await expect(error).toHaveCount(0)
    await expect(page.getByRole('heading', { name: 'AI参考・確定レース' })).toBeVisible()
  })

  test('購入対象の校正と精算状態を分け、オッズ帯0件は未算出', async ({ page }, testInfo) => {
    const writes: string[] = []
    page.on('request', request => {
      if (request.url().includes('/api/') && request.method() !== 'GET') writes.push(request.url())
    })
    await page.route('/api/performance**', route => {
      const mode = new URL(route.request().url()).searchParams.get('mode') === 'actual' ? 'actual' : 'paper'
      return route.fulfill({ json: {
        records: mode === 'paper' ? paperRecords : actualRecords, total_count: 8,
        summary: { ...emptySummary, settled_races: 4, hit_races: 1, settled_cost: 400,
          settled_return: 100, profit: -300, roi: 25, pending_races: 3, pending_cost: 300, skipped_races: 1 },
        selection_diagnostics: selectionDiagnostics(mode),
      } })
    })
    await page.goto('/dashboard')
    const summary = page.getByRole('region', { name: '確定分の成績' })
    await expect(summary.getByText('結果待ち 1レース', { exact: true })).toBeVisible()
    await expect(summary.getByText('払戻待ち 1レース', { exact: true })).toBeVisible()
    await expect(summary.getByText('要確認 1レース', { exact: true })).toBeVisible()
    const diagnostics = page.getByRole('region', { name: '購入対象の校正診断' })
    await expect(diagnostics.getByText('対象 4買い目 · 4レース · 3日')).toBeVisible()
    await diagnostics.getByText('モデル・選定ルール・オッズ帯を見る', { exact: true }).click()
    await expect(diagnostics.locator('dl').getByText('30.0%', { exact: true })).toBeVisible()
    await expect(diagnostics.locator('dl').getByText('25.0%', { exact: true })).toBeVisible()
    await expect(diagnostics.locator('dl').getByText('+5.0pt', { exact: true })).toBeVisible()
    await expect(diagnostics.getByText('95%区間 -20.0pt〜+30.0pt', { exact: true })).toBeVisible()
    await diagnostics.getByText('オッズ帯別の詳細', { exact: true }).click()
    await expect(diagnostics.locator('tbody tr').first().getByText('0 / 0 / 0', { exact: true })).toBeVisible()
    await expect(diagnostics.locator('tbody tr').first().getByText('未算出', { exact: true }).first()).toBeVisible()
    await page.screenshot({ path: testInfo.outputPath('diagnostics-desktop-fixture.png'), fullPage: true })
    await page.getByRole('tab', { name: '購入済み' }).click()
    await expect(diagnostics.getByText(/購入済みの記録を選定時の確率で確認します/)).toBeVisible()
    expect(writes).toEqual([])
  })

  test('モバイルでも診断全体は横に溢れず、表だけ横スクロールできる', async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.route('/api/performance**', route => route.fulfill({ json: {
      records: paperRecords, total_count: 8, summary: emptySummary,
      selection_diagnostics: selectionDiagnostics(),
    } }))
    await page.goto('/dashboard')
    const diagnostics = page.getByRole('region', { name: '購入対象の校正診断' })
    await diagnostics.getByText('モデル・選定ルール・オッズ帯を見る', { exact: true }).click()
    await diagnostics.getByText('オッズ帯別の詳細', { exact: true }).click()
    await expect(diagnostics.locator('table')).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath('diagnostics-mobile-fixture.png'), fullPage: true })
  })
})
