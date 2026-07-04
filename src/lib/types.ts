// 共有型定義 — predict-batch / race-analysis / data-collection で共用

export type RaceItem = {
  race_id: string
  race_name: string
  venue: string
  venue_code: string
  race_no: number
  distance: number
  track_type: string
  num_horses: number
  post_time?: string
  race_class?: string
  kai?: number
  day?: number
}

export type HorsePrediction = {
  horse_number: number
  horse_name: string
  jockey_name: string
  win_probability: number
  p_norm: number
  expected_value: number | null
  predicted_rank: number
  odds: number | null
  quinella_ev?: number | null
  bracket_number?: number | null
  trainer_name?: string | null
  weight?: number | null
  weight_diff?: number | null
}

export type PredictResult = {
  race_id: string
  race_name?: string
  predictions: HorsePrediction[]
  model_id?: string
  created_at?: string
}

// ジョブポーリング共通ステータス
export type JobStatus = 'idle' | 'queued' | 'running' | 'completed' | 'error'

// スクレイプ専用ステータス（idle → scraping → done | error）
export type ScrapeStatus = 'idle' | 'scraping' | 'done' | 'error'

// JRA 10場コード一覧
export const JRA_VENUES = [
  // JRA
  { code: '01', name: '札幌' },
  { code: '02', name: '函館' },
  { code: '03', name: '福島' },
  { code: '04', name: '新潟' },
  { code: '05', name: '東京' },
  { code: '06', name: '中山' },
  { code: '07', name: '中京' },
  { code: '08', name: '京都' },
  { code: '09', name: '阪神' },
  { code: '10', name: '小倉' },
  // NAR（地方競馬）
  { code: '30', name: '門別' },
  { code: '35', name: '盛岡' },
  { code: '36', name: '水沢' },
  { code: '42', name: '浦和' },
  { code: '43', name: '船橋' },
  { code: '44', name: '大井' },
  { code: '45', name: '川崎' },
  { code: '46', name: '金沢' },
  { code: '47', name: '笠松' },
  { code: '48', name: '名古屋' },
  { code: '50', name: '園田' },
  { code: '51', name: '姫路' },
  { code: '54', name: '高知' },
  { code: '55', name: '佐賀' },
  { code: '65', name: '帯広' },
] as const

// 日付ユーティリティ
export function todayStr(): string {
  const d = new Date()
  return `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}`
}

export function toInputDate(yyyymmdd: string): string {
  return `${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`
}

export function fromInputDate(s: string): string {
  return s.replace(/-/g, '')
}
