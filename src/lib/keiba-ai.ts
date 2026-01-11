// 競馬AI予測エンジン - 機械学習ベース
// 既存のStreamlitコードの予測ロジックを移植

export interface HorseData {
  horseNumber: number
  horseName: string
  jockey: string
  weight: number
  recentResults: number[] // 最近5走の着順
  odds: number
  popularity: number
}

export interface PredictionResult {
  horseNumber: number
  horseName: string
  predictedRank: number
  winProbability: number
  confidenceScore: number
  reasoning: string[]
}

/**
 * 競馬予測AIのコア機能
 * ランダムフォレスト風の簡易的なモデル
 */
export class KeibaAI {
  /**
   * 馬のスコアを計算
   */
  private calculateHorseScore(horse: HorseData): number {
    let score = 0

    // 1. 最近の成績を評価 (最大30点)
    const avgRecentRank = horse.recentResults.reduce((a, b) => a + b, 0) / horse.recentResults.length
    score += Math.max(0, 30 - (avgRecentRank - 1) * 5)

    // 2. オッズを評価 (最大20点) - オッズが低いほど高得点
    if (horse.odds < 3) score += 20
    else if (horse.odds < 5) score += 15
    else if (horse.odds < 10) score += 10
    else score += 5

    // 3. 人気を評価 (最大20点)
    if (horse.popularity <= 3) score += 20
    else if (horse.popularity <= 6) score += 15
    else if (horse.popularity <= 9) score += 10
    else score += 5

    // 4. 馬体重の安定性 (最大15点)
    if (horse.weight >= 450 && horse.weight <= 500) score += 15
    else if (horse.weight >= 430 && horse.weight <= 520) score += 10
    else score += 5

    // 5. 連続好走ボーナス (最大15点)
    const consecutiveTopThree = horse.recentResults.filter(r => r <= 3).length
    score += consecutiveTopThree * 3

    return score
  }

  /**
   * 予測を実行
   */
  predict(horses: HorseData[]): PredictionResult[] {
    const scoredHorses = horses.map(horse => ({
      horse,
      score: this.calculateHorseScore(horse),
    }))

    // スコアでソート
    scoredHorses.sort((a, b) => b.score - a.score)

    // 予測結果を生成
    const totalScore = scoredHorses.reduce((sum, h) => sum + h.score, 0)
    
    return scoredHorses.map((item, index) => {
      const winProbability = (item.score / totalScore) * 100
      const avgRecentRank = item.horse.recentResults.reduce((a, b) => a + b, 0) / item.horse.recentResults.length

      const reasoning = []
      if (avgRecentRank <= 3) reasoning.push('最近の成績が良好')
      if (item.horse.odds < 5) reasoning.push('低オッズで人気')
      if (item.horse.popularity <= 3) reasoning.push('上位人気')
      if (item.horse.recentResults.filter(r => r <= 3).length >= 3) reasoning.push('連続好走中')

      return {
        horseNumber: item.horse.horseNumber,
        horseName: item.horse.horseName,
        predictedRank: index + 1,
        winProbability: parseFloat(winProbability.toFixed(2)),
        confidenceScore: parseFloat(((item.score / 100) * 100).toFixed(2)),
        reasoning: reasoning.length > 0 ? reasoning : ['データ不足'],
      }
    })
  }

  /**
   * 推奨賭け金を計算（ケリー基準ベース）
   */
  recommendBetAmount(
    bankroll: number,
    winProbability: number,
    odds: number,
    riskLevel: 'conservative' | 'moderate' | 'aggressive' = 'moderate'
  ): number {
    // ケリー基準: f = (bp - q) / b
    // f: 賭けるべき資金の割合
    // b: オッズ-1
    // p: 勝率
    // q: 負け率 (1-p)
    
    const p = winProbability / 100
    const q = 1 - p
    const b = odds - 1

    let kellyFraction = (b * p - q) / b

    // リスクレベルに応じて調整
    const riskMultiplier = {
      conservative: 0.25,
      moderate: 0.5,
      aggressive: 0.75,
    }

    kellyFraction = kellyFraction * riskMultiplier[riskLevel]

    // 最小・最大を設定
    kellyFraction = Math.max(0, Math.min(kellyFraction, 0.1)) // 最大10%

    return Math.floor(bankroll * kellyFraction / 100) * 100 // 100円単位
  }

  /**
   * 馬券タイプの推奨
   */
  recommendBetType(predictions: PredictionResult[]): {
    betType: string
    horses: number[]
    expectedReturn: number
  }[] {
    const recommendations = []

    // 単勝（最も確信度の高い馬）
    const topHorse = predictions[0]
    if (topHorse.confidenceScore > 70) {
      recommendations.push({
        betType: '単勝',
        horses: [topHorse.horseNumber],
        expectedReturn: topHorse.winProbability,
      })
    }

    // 馬連（上位2頭）
    if (predictions.length >= 2 && predictions[0].confidenceScore > 60 && predictions[1].confidenceScore > 60) {
      recommendations.push({
        betType: '馬連',
        horses: [predictions[0].horseNumber, predictions[1].horseNumber],
        expectedReturn: (predictions[0].winProbability + predictions[1].winProbability) / 2,
      })
    }

    // 三連複（上位3頭）
    if (predictions.length >= 3) {
      recommendations.push({
        betType: '三連複',
        horses: [predictions[0].horseNumber, predictions[1].horseNumber, predictions[2].horseNumber],
        expectedReturn: (predictions[0].winProbability + predictions[1].winProbability + predictions[2].winProbability) / 3,
      })
    }

    return recommendations
  }
}

/**
 * 資金管理クラス
 */
export class BankManager {
  private initialBank: number
  private currentBank: number
  private bets: Array<{ amount: number; payout: number; date: Date }> = []

  constructor(initialBank: number) {
    this.initialBank = initialBank
    this.currentBank = initialBank
  }

  placeBet(amount: number): boolean {
    if (amount > this.currentBank) return false
    this.currentBank -= amount
    return true
  }

  recordPayout(betAmount: number, payout: number): void {
    this.currentBank += payout
    this.bets.push({ amount: betAmount, payout, date: new Date() })
  }

  getStats() {
    const totalBet = this.bets.reduce((sum, b) => sum + b.amount, 0)
    const totalReturn = this.bets.reduce((sum, b) => sum + b.payout, 0)
    const profitLoss = this.currentBank - this.initialBank
    const roi = totalBet > 0 ? ((totalReturn - totalBet) / totalBet) * 100 : 0
    const recoveryRate = totalBet > 0 ? (totalReturn / totalBet) * 100 : 0

    return {
      initialBank: this.initialBank,
      currentBank: this.currentBank,
      totalBet,
      totalReturn,
      profitLoss,
      roi: parseFloat(roi.toFixed(2)),
      recoveryRate: parseFloat(recoveryRate.toFixed(2)),
      numberOfBets: this.bets.length,
    }
  }
}
