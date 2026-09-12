import { readFileSync } from 'node:fs'
import { describe, expect, test } from 'vitest'

const readSource = (path: string) => readFileSync(path, 'utf8')

describe('UI navigation contract', () => {
  test('keeps the Home menu focused on the exact five requested functions', () => {
    const source = readSource('src/app/home/page.tsx')

    expect(source).toContain("href: '/data-collection'")
    expect(source).toContain("href: '/train'")
    expect(source).toContain("href: '/predict-batch'")
    expect(source).toContain("href: '/dashboard'")
    expect(source).toContain("href: '/user-management'")
    expect(source).toContain("label: 'モデル作成'")
    expect(source.match(/step: '0\d'/g)).toHaveLength(5)

    for (const route of [
      '/race-analysis',
      '/prediction-history',
      '/production-readiness',
      '/notion-report',
      '/model-redesign-workbench',
    ]) {
      expect(source).not.toContain(`href: '${route}'`)
      expect(source).not.toContain(`href="${route}"`)
    }
  })

  test('keeps user management read-only and separate from role switching', () => {
    const source = readSource('src/app/user-management/page.tsx')

    expect(source).toContain("authFetch('/api/admin/profiles'")
    expect(source).not.toContain('/role')
    expect(source).not.toContain('<select')
    expect(source).not.toContain('onChange=')

    const legacyRoute = readSource('src/app/admin/page.tsx')
    expect(legacyRoute).toContain("redirect('/home')")
    expect(legacyRoute).not.toContain('管理者ダッシュボード')
  })

  test('guards every Admin-only page at the action boundary', () => {
    for (const layout of [
      'src/app/data-collection/layout.tsx',
      'src/app/train/layout.tsx',
      'src/app/user-management/layout.tsx',
    ]) {
      expect(readSource(layout)).toContain('<AdminActionRouteGuard>{children}</AdminActionRouteGuard>')
    }

    const guard = readSource('src/components/AdminActionRouteGuard.tsx')
    expect(guard).toContain('/api/admin/unlock')
    expect(guard).toContain('signInWithPassword')
    expect(readSource('src/app/production-readiness/layout.tsx'))
      .toContain('<AdminActionRouteGuard>{children}</AdminActionRouteGuard>')
  })

  test('keeps the data-collection screen focused on the essential workflow', () => {
    const source = readSource('src/app/data-collection/page.tsx')

    expect(source).toContain('data-testid="start-period-input"')
    expect(source).toContain('data-testid="end-period-input"')
    expect(source).toContain('data-testid="dry-run-button"')
    expect(source).toContain('data-testid="execute-button"')
    expect(source).toContain('data-testid="latest-fetch-summary"')
    expect(source).toContain('data-testid="uncertainty-panel"')
    expect(source).toContain('事前確認')
    expect(source).toContain('保存済み')

    for (const optionalUi of [
      'Refresh Plan',
      'P0 Repair Plan',
      'Targeted Refetch Plan',
      'Live Validation',
      'Review Queue',
      'force-rescrape-input',
      '特徴量プロファイリングレポート（オプション）',
      'モデル学習へ',
      'quality-bridge-card',
    ]) {
      expect(source).not.toContain(optionalUi)
    }
  })

  test('exposes advanced analysis only from its related main screen', () => {
    const dashboardSource = readSource('src/app/dashboard/page.tsx')
    const predictSource = readSource('src/app/predict-batch/page.tsx')
    const historySource = readSource('src/app/prediction-history/page.tsx')
    const analysisSource = readSource('src/app/race-analysis/page.tsx')

    expect(dashboardSource).toContain('href="/prediction-history"')
    expect(dashboardSource).not.toContain('href="/data-collection"')
    expect(predictSource).toContain('予測結果・スコア詳細')
    expect(predictSource).toContain("include_explanation: isPremium")
    expect(predictSource).toContain('<RaceExplanationPanel')
    expect(predictSource).not.toContain('特徴量・結果照合を詳しく見る')
    expect(historySource).toContain('/race-analysis?date=${encodeURIComponent(race.race_date)}&race_id=${encodeURIComponent(race.race_id)}')
    expect(analysisSource).toContain("['features', 'explain'].includes")
    expect(analysisSource).toContain('<RaceExplanationPanel')
    expect(analysisSource).not.toContain('/api/debug/race/')
  })
})
