/**
 * ケリー基準による最適賭け額計算
 */
export function calculateKellyBet(
  probability: number,
  odds: number,
  bankroll: number,
  kellyFraction: number = 0.25
): number {
  if (probability <= 0 || odds <= 1.0) {
    return 0
  }

  const kellyPercentage = (probability * odds - 1) / (odds - 1)
  
  if (kellyPercentage <= 0) {
    return 0
  }

  // フラクショナルケリー適用
  const adjustedKelly = kellyPercentage * kellyFraction
  
  // 上限5%で破産リスク回避
  const finalKelly = Math.min(adjustedKelly, 0.05)
  
  return Math.floor(bankroll * finalKelly)
}

/**
 * レースレベル判定（skip/normal/decisive）
 */
export function evaluateRaceLevel(
  maxExpectedValue: number,
  maxProbability: number,
  difficultyScore: number,
  minEv: number = 1.2
): 'skip' | 'normal' | 'decisive' {
  if (maxExpectedValue < minEv) {
    return 'skip'
  }

  const isDecisive = (
    difficultyScore >= 0.7 ||
    (maxExpectedValue >= 4.0 && maxProbability >= 0.25) ||
    maxExpectedValue >= 6.0
  )

  return isDecisive ? 'decisive' : 'normal'
}

/**
 * レベル別最適単価計算
 */
export function calculateOptimalUnitPrice(
  raceLevel: 'skip' | 'normal' | 'decisive',
  perRaceLimit: number,
  dynamicUnit: boolean = true
): number {
  if (!dynamicUnit) {
    return 100
  }

  if (raceLevel === 'skip') {
    return 100
  } else if (raceLevel === 'decisive') {
    if (perRaceLimit >= 5000) return 1000
    if (perRaceLimit >= 3000) return 500
    return 200
  } else {
    return perRaceLimit >= 3000 ? 200 : 100
  }
}

/**
 * シーズン分析ボーナス
 */
export function getSeasonBonus(date: Date): number {
  const month = date.getMonth() + 1

  if (month >= 3 && month <= 5) {
    return 1.10 // 春競馬 +10%
  } else if (month >= 6 && month <= 8) {
    return 0.90 // 夏競馬 -10%
  } else if (month >= 9 && month <= 11) {
    return 1.05 // 秋競馬 +5%
  } else {
    return 1.0 // 冬競馬 標準
  }
}

/**
 * レース難易度スコア計算（予測分布の偏り度）
 */
export function calculateDifficultyScore(probabilities: number[]): number {
  if (probabilities.length === 0) return 0

  // 標準偏差を使用
  const mean = probabilities.reduce((a, b) => a + b, 0) / probabilities.length
  const variance = probabilities.reduce((sum, p) => sum + Math.pow(p - mean, 2), 0) / probabilities.length
  const stdDev = Math.sqrt(variance)

  // 正規化（0-1の範囲）
  return Math.min(stdDev * 5, 1)
}

/**
 * 期待値計算
 */
export function calculateExpectedValue(probability: number, odds: number): number {
  return probability * odds
}

/**
 * 馬券組み合わせ生成
 */
export function generateCombinations(
  horses: number[],
  size: number,
  ordered: boolean = false
): number[][] {
  const results: number[][] = []

  function combine(start: number, current: number[]) {
    if (current.length === size) {
      results.push([...current])
      return
    }

    for (let i = start; i < horses.length; i++) {
      current.push(horses[i])
      combine(ordered ? 0 : i + 1, current)
      current.pop()
    }
  }

  combine(0, [])
  return results
}

/**
 * 馬連・ワイド組み合わせ（C(n,2)）
 */
export function generateUmarenCombinations(horses: number[]): number[][] {
  return generateCombinations(horses, 2, false)
}

/**
 * 三連複組み合わせ（C(n,3)）
 */
export function generateSanrenpukuCombinations(horses: number[]): number[][] {
  return generateCombinations(horses, 3, false)
}

/**
 * 馬単組み合わせ（P(n,2)）
 */
export function generateUmatanCombinations(horses: number[]): number[][] {
  const results: number[][] = []
  
  for (let i = 0; i < horses.length; i++) {
    for (let j = 0; j < horses.length; j++) {
      if (i !== j) {
        results.push([horses[i], horses[j]])
      }
    }
  }
  
  return results
}

/**
 * 三連単組み合わせ（P(n,3)）
 */
export function generateSanrentanCombinations(horses: number[]): number[][] {
  const results: number[][] = []
  
  for (let i = 0; i < horses.length; i++) {
    for (let j = 0; j < horses.length; j++) {
      if (i === j) continue
      for (let k = 0; k < horses.length; k++) {
        if (k === i || k === j) continue
        results.push([horses[i], horses[j], horses[k]])
      }
    }
  }
  
  return results
}

/**
 * 複勝組み合わせ
 */
export function generateFukushoSingle(horses: number[]): number[][] {
  return horses.map(h => [h])
}

/**
 * 予算内に収まる最大点数計算
 */
export function calculateMaxCount(
  budget: number,
  unitPrice: number
): number {
  return Math.floor(budget / unitPrice)
}

/**
 * 予算配分比率取得
 */
export function getBudgetAllocationRatio(raceLevel: 'skip' | 'normal' | 'decisive'): number {
  switch (raceLevel) {
    case 'skip': return 0
    case 'normal': return 0.4
    case 'decisive': return 0.8
    default: return 0.4
  }
}

/**
 * リスクモード別の資金比率
 */
export function getRiskModePercentage(mode: 'conservative' | 'balanced' | 'aggressive'): number {
  switch (mode) {
    case 'conservative': return 0.02
    case 'balanced': return 0.035
    case 'aggressive': return 0.05
    default: return 0.035
  }
}
