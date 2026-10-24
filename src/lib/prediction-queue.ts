/** Bound browser requests as well as server work; a free slot starts the next race immediately. */
export const PREDICTION_CONCURRENCY = 4

export type PredictionQueueProgress = { completed: number; total: number; active: number }

/** Old servers do not isolate CPU work; only the advertised bounded runtime may run concurrently. */
export function hasBoundedPredictionRuntime(payload: unknown): boolean {
  if (!payload || typeof payload !== 'object') return false
  const health = payload as { status?: unknown; prediction_runtime?: unknown }
  if (health.status !== 'ok' || !health.prediction_runtime || typeof health.prediction_runtime !== 'object') return false
  const runtime = health.prediction_runtime as { version?: unknown; max_workers?: unknown }
  return runtime.version === 'bounded-cpu-v1' && typeof runtime.max_workers === 'number'
    && Number.isInteger(runtime.max_workers) && runtime.max_workers >= 1
}

export function resolvePredictionConcurrency(payload: unknown): number {
  if (!hasBoundedPredictionRuntime(payload)) return 1
  const runtime = (payload as { prediction_runtime: { max_workers: number } }).prediction_runtime
  return Math.min(PREDICTION_CONCURRENCY, runtime.max_workers)
}

/** Auth/quota errors use a structured detail; never render an arbitrary object as React text. */
export function predictionErrorMessage(payload: unknown, status: number): string {
  if (payload && typeof payload === 'object') {
    const { detail } = payload as { detail?: unknown }
    if (typeof detail === 'string' && detail.trim()) return detail
    if (detail && typeof detail === 'object') {
      const message = (detail as { message?: unknown }).message
      if (typeof message === 'string' && message.trim()) return message
    }
  }
  return `HTTP ${status}`
}

export async function runPredictionQueue<T, R>(
  items: readonly T[],
  task: (item: T, index: number) => Promise<R>,
  options: {
    concurrency?: number
    signal?: AbortSignal
    onSettled?: (outcome: PromiseSettledResult<R>, item: T, index: number) => void
    onProgress?: (progress: PredictionQueueProgress) => void
  } = {},
): Promise<{ outcomes: Array<PromiseSettledResult<R> | undefined>; cancelled: boolean }> {
  const requested = options.concurrency ?? PREDICTION_CONCURRENCY
  const concurrency = Number.isFinite(requested) ? Math.max(1, Math.floor(requested)) : 1
  const outcomes: Array<PromiseSettledResult<R> | undefined> = new Array(items.length)
  let next = 0
  let completed = 0
  let active = 0
  const notify = () => {
    if (!options.signal?.aborted) options.onProgress?.({ completed, total: items.length, active })
  }
  async function worker() {
    while (!options.signal?.aborted && next < items.length) {
      const index = next++
      const item = items[index]
      active++
      notify()
      let outcome: PromiseSettledResult<R>
      try {
        outcome = { status: 'fulfilled', value: await task(item, index) }
      } catch (reason) {
        outcome = { status: 'rejected', reason }
      }
      outcomes[index] = outcome
      active--
      completed++
      // A late response must not publish results or launch another request after cancellation.
      if (options.signal?.aborted) continue
      options.onSettled?.(outcome, item, index)
      notify()
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, worker))
  return { outcomes, cancelled: Boolean(options.signal?.aborted) }
}

function durationLabel(seconds: number): string {
  const rounded = Math.max(0, Math.floor(seconds))
  return rounded < 60 ? `${rounded}秒` : `${Math.floor(rounded / 60)}分${rounded % 60}秒`
}

/** Observed throughput only, not a promise based on the requested parallelism. */
export function predictionTimingLabel(progress: PredictionQueueProgress, elapsedMs: number): string {
  const elapsed = Math.max(0, elapsedMs) / 1000
  const measured = progress.completed > 0 && elapsed > 0
  const speed = measured ? `${(progress.completed / elapsed * 60).toFixed(1)}レース/分` : '速度を計測中'
  const remaining = measured && progress.completed < progress.total
    ? ` · 残り約${durationLabel(Math.ceil(elapsed / progress.completed * (progress.total - progress.completed)))}` : ''
  return `経過 ${durationLabel(elapsed)} · ${speed}${remaining}`
}
