export type ScrapeProgressTone = 'normal' | 'protected' | 'waiting' | 'retrying'

export type ScrapeProgressDisplay = {
  tone: ScrapeProgressTone
  title: string
  interval: string
  requestSpeed: string
  httpStatus: string
  saved: string
  raceProgress: string | null
  eta: string | null
}

const record = (value: unknown): Record<string, unknown> | null =>
  typeof value === 'object' && value !== null ? value as Record<string, unknown> : null

const number = (value: unknown): number =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : 0

export function buildScrapeProgressDisplay(
  statusData: unknown,
  nowMs = Date.now(),
): ScrapeProgressDisplay | null {
  const status = record(statusData)
  const progress = record(status?.progress)
  if (!status || !progress) return null
  const control = record(progress.fetch_control) ?? {}
  const metrics = record(progress.fetch_metrics) ?? {}

  const intervalSec = number(control.interval)
  const cooldownUntil = number(control.cooldown_until) * 1000
  const blocked = typeof control.blocked_reason === 'string' && control.blocked_reason.length > 0
  const failures = number(control.failures)
  let tone: ScrapeProgressTone = 'normal'
  let title = '正常取得中'
  if (blocked || cooldownUntil > nowMs) {
    tone = 'waiting'; title = 'アクセス制限で待機中'
  } else if (failures > 0) {
    tone = 'retrying'; title = '再試行しながら取得中'
  } else if (intervalSec > 2) {
    tone = 'protected'; title = '保護速度で正常取得中'
  }

  const requests = number(metrics.network_requests)
  const successes = number(metrics.http_success_count)
  const reportedRate = number(progress.network_requests_per_min)
  const reportedElapsed = number(progress.attempt_elapsed_sec)
  const createdAt = typeof status.created_at === 'string' ? Date.parse(status.created_at) : NaN
  const elapsedSec = reportedElapsed > 0
    ? reportedElapsed
    : Number.isFinite(createdAt) ? Math.max(0, (nowMs - createdAt) / 1000) : 0
  const requestRate = reportedRate > 0
    ? reportedRate
    : elapsedSec > 0 ? requests * 60 / elapsedSec : 0
  const successRate = requests > 0 ? successes * 100 / requests : 0

  const savedRaces = number(progress.saved_races)
  const savedHorses = number(progress.saved_horses)
  const processed = number(progress.current_date_processed_races)
  const total = number(progress.current_date_total_races)
  const raceRate = processed > 0 && elapsedSec > 0 ? processed * 60 / elapsedSec : 0
  const remainingMinutes = total > processed && raceRate > 0
    ? Math.ceil((total - processed) / raceRate)
    : 0

  return {
    tone,
    title,
    interval: intervalSec > 0 ? `${intervalSec.toFixed(1)}秒/回` : '計測中',
    requestSpeed: requestRate > 0 ? `${requestRate.toFixed(1)}通信/分` : '計測中',
    httpStatus: requests > 0
      ? `${successes}/${requests} 正常 (${successRate.toFixed(0)}%)`
      : '応答待ち',
    saved: `${savedRaces}レース・${savedHorses}頭`,
    raceProgress: total > 0 ? `${Math.min(processed, total)}/${total}レース` : null,
    eta: remainingMinutes > 0 ? `残り約${remainingMinutes}分` : null,
  }
}
