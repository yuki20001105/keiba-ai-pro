// レース分析ページ専用の型定義
// NOTE: 共有の PredictResult (lib/types.ts) とは別物。こちらはより詳細な /api/analyze-race レスポンスに対応。

export type Prediction = {
  horse_number: number
  horse_name: string
  jockey_name: string
  trainer_name: string
  sex: string
  age: number | null
  horse_weight: number | null
  odds: number | null
  popularity: number | null
  win_probability: number
  p_raw: number
  p_norm: number
  expected_value: number | null
  predicted_rank: number
}

export type RaceInfo = {
  race_id: string
  race_name: string
  venue: string
  date: string
  distance: number
  track_type: string
  num_horses: number
  weather?: string
  field_condition?: string
}

export type Recommendation = {
  action: string
  reason: string
  purchase_count: number
  unit_price: number
  total_cost: number
  expected_return: number
}

export type RacePredictResult = {
  success: boolean
  race_info: RaceInfo
  predictions: Prediction[]
  recommendation: Recommendation | null
  best_bet_type: string | null
  pro_evaluation: { race_level: string; confidence: number } | null
  bet_types?: Record<string, { combination: string }[]>
}

export type FeatureData = {
  feature_columns: string[]
  records: Record<string, unknown>[]
  horse_count: number
  feature_count: number
}

export type CacheEntry = {
  predictResult: RacePredictResult
  featData: FeatureData | null
  cachedAt: number
}
