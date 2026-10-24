export type ScrapeAcquisitionDisplay = {
  parallelism: string
  speed: string | null
  interval: string | null
}

const record = (value: unknown): Record<string, unknown> | null =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown> : null

const nonNegativeNumber = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0

/** Only display a policy reported by the running server job, never UI defaults. */
export function buildScrapeAcquisitionDisplay(statusData: unknown): ScrapeAcquisitionDisplay | null {
  const status = record(statusData)
  const progress = record(status?.progress)
  if (status?.status !== 'running' || !progress) return null
  // A running multi-month parent can have a queued/recovering current child.
  if (progress.current_status !== undefined && progress.current_status !== 'running') return null
  const concurrency = progress.race_task_concurrency
  if (typeof concurrency !== 'number' || !Number.isInteger(concurrency)
    || concurrency < 1 || concurrency > 4
    || progress.max_concurrent_http_requests !== 1
    || progress.http_pacing_scope !== 'provider-family') return null

  let speed: string | null = null
  const metrics = record(progress.fetch_metrics)
  const requests = metrics?.network_requests
  const elapsed = progress.attempt_elapsed_sec
  // Rates are current worker-attempt measurements, not whole-period ETA.
  if (progress.fetch_metrics_scope === 'current_worker_attempt'
    && nonNegativeNumber(requests) && Number.isInteger(requests)
    && nonNegativeNumber(elapsed) && elapsed > 0) {
    speed = elapsed < 10
      ? `${requests}通信 · 速度計測中（現作業枠）`
      : `${requests}通信 · ${(requests * 60 / elapsed).toFixed(1)}通信/分（現作業枠）`
  }
  const control = record(progress.fetch_control)
  const interval = control?.interval
  return {
    parallelism: `解析・保存 最大${concurrency}並列 / 外部通信は共通制限`,
    speed,
    interval: nonNegativeNumber(interval) && interval > 0 ? `${interval.toFixed(1)}秒/回` : null,
  }
}

export function scrapeQueueTitle(accepted: boolean): string {
  return accepted ? 'サーバー受付済み・開始待ち' : 'サーバー受付を確認中（取得開始は未確認）'
}
