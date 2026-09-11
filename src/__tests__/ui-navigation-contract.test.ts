import { readFileSync } from 'node:fs'
import { describe, expect, test } from 'vitest'

const readSource = (path: string) => readFileSync(path, 'utf8')

describe('UI navigation contract', () => {
  test('keeps the public home focused on prediction and performance', () => {
    const source = readSource('src/app/home/page.tsx')

    expect(source).toContain("{ href: '/predict-batch'")
    expect(source).toContain("{ href: '/dashboard'")
    expect(source.match(/step: '0\d'/g)).toHaveLength(2)

    for (const route of [
      '/data-collection',
      '/train',
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

  test('groups operational tools under the Admin menu', () => {
    const source = readSource('src/app/admin/page.tsx')

    expect(source).toContain('href="/data-collection"')
    expect(source).toContain('href="/train"')
    expect(source).toContain('href="/production-readiness"')
    expect(source).toContain('モデル管理')
    expect(source).toContain('学習実行は準備中')
  })

  test('exposes advanced analysis only from its related main screen', () => {
    const dashboardSource = readSource('src/app/dashboard/page.tsx')
    const predictSource = readSource('src/app/predict-batch/page.tsx')
    const historySource = readSource('src/app/prediction-history/page.tsx')
    const analysisSource = readSource('src/app/race-analysis/page.tsx')

    expect(dashboardSource).toContain('href="/prediction-history"')
    expect(dashboardSource).not.toContain('href="/data-collection"')
    expect(predictSource).toContain('予測結果・スコア詳細')
    expect(predictSource).toContain('/race-analysis?date=${date}&race_id=${encodeURIComponent(r.race_id)}&tab=features')
    expect(historySource).toContain('/race-analysis?date=${encodeURIComponent(race.race_date)}&race_id=${encodeURIComponent(race.race_id)}')
    expect(analysisSource).toContain("searchParams.get('tab') === 'features'")
    expect(analysisSource).toContain('if (isPremium && !cached.featData)')
  })
})
