export type ModelSummary = {
  model_id: string
  target?: string | null
  model_type?: string | null
  created_at?: string | null
  training_date_from?: string | null
  training_date_to?: string | null
  auc?: number | null
  cv_auc_mean?: number | null
  n_rows?: number | null
  feature_count?: number | null
  is_active?: boolean
}

const TARGET_LABELS: Record<string, string> = {
  win: '単勝',
  place3: '複勝',
  speed_deviation: '速度偏差',
  win_tie: 'タイム同着',
}

function compactCreatedAt(value?: string | null): string | null {
  if (!value || value === 'unknown') return null
  const match = value.match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(?:\d{2})?$/)
  if (!match) return null
  const [, year, month, day, hour, minute] = match
  return `${year}/${month}/${day} ${hour}:${minute}`
}

export function formatModelCreatedAt(model: Pick<ModelSummary, 'model_id' | 'created_at'>): string {
  const compact = compactCreatedAt(model.created_at)
  if (compact) return compact

  if (model.created_at && model.created_at !== 'unknown') {
    const date = new Date(model.created_at)
    if (!Number.isNaN(date.getTime())) {
      return new Intl.DateTimeFormat('ja-JP', {
        timeZone: 'Asia/Tokyo',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }).format(date)
    }
  }

  const filenameDate = model.model_id.match(
    /_(\d{8})_(\d{4})(?:_\d+)?(?:\.joblib)?$/,
  )
  if (!filenameDate) return '—'
  const [, date, time] = filenameDate
  return `${date.slice(0, 4)}/${date.slice(4, 6)}/${date.slice(6, 8)} ${time.slice(0, 2)}:${time.slice(2, 4)}`
}

function formatModelMonth(value?: string | null): string {
  if (!value) return '?'
  const digits = value.replace(/\D/g, '')
  if (digits.length < 6) return value
  return `${digits.slice(0, 4)}/${digits.slice(4, 6)}`
}

export function formatModelPeriod(model: Pick<ModelSummary, 'training_date_from' | 'training_date_to'>): string {
  if (!model.training_date_from && !model.training_date_to) return ''
  return `${formatModelMonth(model.training_date_from)}–${formatModelMonth(model.training_date_to)}`
}

export function formatModelOptionLabel(model: ModelSummary): string {
  const target = TARGET_LABELS[model.target || ''] || model.target || 'モデル'
  const created = formatModelCreatedAt(model)
  const period = formatModelPeriod(model)
  const auc = typeof model.auc === 'number' && Number.isFinite(model.auc)
    ? `AUC ${model.auc.toFixed(3)}`
    : ''
  return [target, created, period ? `学習 ${period}` : '', auc].filter(Boolean).join('｜')
}
