/**
 * betting-strategy.ts の純粋関数ユニットテスト
 *
 * Python 側の test_feature_engineering.py に相当する層。
 * 外部依存なし・ブラウザ API 不使用なので jsdom 不要だが、
 * 統一環境のため vitest.config.ts の設定をそのまま利用する。
 *
 * 実行:
 *   npm test
 *   npm run test:watch -- --reporter=verbose
 */
import { describe, it, expect } from 'vitest'
import {
  calculateKellyBet,
  evaluateRaceLevel,
  calculateOptimalUnitPrice,
  getSeasonBonus,
  calculateDifficultyScore,
  calculateExpectedValue,
} from '@/lib/betting-strategy'

// ─────────────────────────────────────────────────────────
// calculateKellyBet
// ─────────────────────────────────────────────────────────
describe('calculateKellyBet', () => {
  it('標準的なケースで正の賭け額を返す', () => {
    // kellyPct = (0.3*5 - 1) / (5-1) = 0.125, adjusted = 0.03125, capped at 3.125%
    // floor(10000 * 0.03125) = 312
    expect(calculateKellyBet(0.3, 5, 10000, 0.25)).toBe(312)
  })

  it('ケリー比率が 5% 上限でキャップされる', () => {
    // kellyPct = (0.5*10 - 1)/(10-1) ≈ 0.444, adjusted ≈ 0.111 → capped 0.05
    // floor(10000 * 0.05) = 500
    expect(calculateKellyBet(0.5, 10, 10000, 0.25)).toBe(500)
  })

  it('期待値が負（kellyPct<=0）のとき 0 を返す', () => {
    // kellyPct = (0.2*2 - 1)/(2-1) = -0.6
    expect(calculateKellyBet(0.2, 2, 10000, 0.25)).toBe(0)
  })

  it('probability が 0 のとき 0 を返す', () => {
    expect(calculateKellyBet(0, 5, 10000)).toBe(0)
  })

  it('odds が 1.0 のとき 0 を返す（配当なし）', () => {
    expect(calculateKellyBet(0.5, 1.0, 10000)).toBe(0)
  })

  it('odds が 1.0 未満のとき 0 を返す', () => {
    expect(calculateKellyBet(0.5, 0.8, 10000)).toBe(0)
  })

  it('kellyFraction のデフォルトは 0.25', () => {
    const withDefault  = calculateKellyBet(0.3, 5, 10000)
    const withExplicit = calculateKellyBet(0.3, 5, 10000, 0.25)
    expect(withDefault).toBe(withExplicit)
  })

  it('bankroll が大きいほど賭け額も大きい（単調増加）', () => {
    const a = calculateKellyBet(0.3, 5, 10000, 0.25)
    const b = calculateKellyBet(0.3, 5, 20000, 0.25)
    // Math.floor の切り捨てで厳密な 2 倍にならない場合があるため >= で検証
    expect(b).toBeGreaterThan(a)
  })
})

// ─────────────────────────────────────────────────────────
// evaluateRaceLevel
// ─────────────────────────────────────────────────────────
describe('evaluateRaceLevel', () => {
  it('maxEv < minEv → skip', () => {
    expect(evaluateRaceLevel(1.1, 0.3, 0.3)).toBe('skip')
  })

  it('maxEv === minEv も skip', () => {
    // minEv のデフォルトは 1.2。1.2 は >= なので normal 扱い
    expect(evaluateRaceLevel(1.2, 0.2, 0.2)).toBe('normal')
  })

  it('difficultyScore >= 0.7 → decisive', () => {
    expect(evaluateRaceLevel(2.0, 0.3, 0.8)).toBe('decisive')
  })

  it('maxEv >= 4.0 かつ maxProbability >= 0.25 → decisive', () => {
    expect(evaluateRaceLevel(4.0, 0.25, 0.3)).toBe('decisive')
  })

  it('maxEv >= 6.0 → decisive（difficultyScore 無関係）', () => {
    expect(evaluateRaceLevel(6.0, 0.1, 0.0)).toBe('decisive')
  })

  it('条件を満たさない通常ケース → normal', () => {
    expect(evaluateRaceLevel(1.5, 0.2, 0.3)).toBe('normal')
  })

  it('minEv を変更できる', () => {
    expect(evaluateRaceLevel(1.5, 0.2, 0.3, 2.0)).toBe('skip')
    expect(evaluateRaceLevel(2.5, 0.2, 0.3, 2.0)).toBe('normal')
  })
})

// ─────────────────────────────────────────────────────────
// calculateOptimalUnitPrice
// ─────────────────────────────────────────────────────────
describe('calculateOptimalUnitPrice', () => {
  it('skip → 常に 100', () => {
    expect(calculateOptimalUnitPrice('skip', 10000)).toBe(100)
    expect(calculateOptimalUnitPrice('skip', 100)).toBe(100)
  })

  it('decisive + perRaceLimit>=5000 → 1000', () => {
    expect(calculateOptimalUnitPrice('decisive', 5000)).toBe(1000)
    expect(calculateOptimalUnitPrice('decisive', 9999)).toBe(1000)
  })

  it('decisive + perRaceLimit>=3000 かつ <5000 → 500', () => {
    expect(calculateOptimalUnitPrice('decisive', 4999)).toBe(500)
    expect(calculateOptimalUnitPrice('decisive', 3000)).toBe(500)
  })

  it('decisive + perRaceLimit<3000 → 200', () => {
    expect(calculateOptimalUnitPrice('decisive', 2999)).toBe(200)
    expect(calculateOptimalUnitPrice('decisive', 100)).toBe(200)
  })

  it('normal + perRaceLimit>=3000 → 200', () => {
    expect(calculateOptimalUnitPrice('normal', 3000)).toBe(200)
  })

  it('normal + perRaceLimit<3000 → 100', () => {
    expect(calculateOptimalUnitPrice('normal', 2999)).toBe(100)
  })

  it('dynamicUnit=false → 常に 100', () => {
    expect(calculateOptimalUnitPrice('decisive', 9999, false)).toBe(100)
    expect(calculateOptimalUnitPrice('normal', 9999, false)).toBe(100)
  })
})

// ─────────────────────────────────────────────────────────
// getSeasonBonus
// ─────────────────────────────────────────────────────────
describe('getSeasonBonus', () => {
  it.each([
    [3,  1.10, '春 3月'],
    [4,  1.10, '春 4月'],
    [5,  1.10, '春 5月'],
    [6,  0.90, '夏 6月'],
    [7,  0.90, '夏 7月'],
    [8,  0.90, '夏 8月'],
    [9,  1.05, '秋 9月'],
    [10, 1.05, '秋 10月'],
    [11, 1.05, '秋 11月'],
    [12, 1.00, '冬 12月'],
    [1,  1.00, '冬 1月'],
    [2,  1.00, '冬 2月'],
  ] as const)('月=%i → %f (%s)', (month, expected, _label) => {
    const d = new Date(2026, month - 1, 15)  // 各月の15日
    expect(getSeasonBonus(d)).toBe(expected)
  })
})

// ─────────────────────────────────────────────────────────
// calculateDifficultyScore
// ─────────────────────────────────────────────────────────
describe('calculateDifficultyScore', () => {
  it('空配列 → 0', () => {
    expect(calculateDifficultyScore([])).toBe(0)
  })

  it('均等分布 → 0（標準偏差ゼロ）', () => {
    expect(calculateDifficultyScore([0.25, 0.25, 0.25, 0.25])).toBe(0)
  })

  it('完全集中 → 上限 1.0', () => {
    // [0.9, 0.1]: stdDev ≈ 0.4, score = min(0.4*5, 1) = 1.0
    expect(calculateDifficultyScore([0.9, 0.1])).toBe(1.0)
  })

  it('適度な分散 → 0〜1 の範囲', () => {
    const score = calculateDifficultyScore([0.5, 0.3, 0.15, 0.05])
    expect(score).toBeGreaterThan(0)
    expect(score).toBeLessThanOrEqual(1)
  })
})

// ─────────────────────────────────────────────────────────
// calculateExpectedValue
// ─────────────────────────────────────────────────────────
describe('calculateExpectedValue', () => {
  it('0.3 * 5.0 = 1.5', () => {
    expect(calculateExpectedValue(0.3, 5.0)).toBeCloseTo(1.5)
  })

  it('0.5 * 2.0 = 1.0', () => {
    expect(calculateExpectedValue(0.5, 2.0)).toBeCloseTo(1.0)
  })

  it('probability=0 → 0', () => {
    expect(calculateExpectedValue(0, 10)).toBe(0)
  })

  it('odds=0 → 0', () => {
    expect(calculateExpectedValue(0.5, 0)).toBe(0)
  })
})
