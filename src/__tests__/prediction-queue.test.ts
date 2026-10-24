import { describe, expect, it, vi } from 'vitest'
import { hasBoundedPredictionRuntime, PREDICTION_CONCURRENCY, predictionErrorMessage, predictionTimingLabel, resolvePredictionConcurrency, runPredictionQueue } from '@/lib/prediction-queue'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

describe('rolling prediction queue', () => {
  it('trusts only a healthy versioned CPU runtime and caps advertised worker capacity at four', () => {
    const health = (max_workers: unknown, version = 'bounded-cpu-v1') => ({ status: 'ok', prediction_runtime: { version, max_workers } })
    for (const workers of [1, 2, 3, 4]) expect(resolvePredictionConcurrency(health(workers))).toBe(workers)
    expect(resolvePredictionConcurrency(health(99))).toBe(4)
    for (const payload of [null, undefined, {}, { status: 'ok' }, health(4, 'old'),
      { ...health(4), status: 'offline' }, ...[0, -1, 4.2, '4', true, null, Number.NaN, Number.POSITIVE_INFINITY].map(value => health(value))]) {
      expect(resolvePredictionConcurrency(payload)).toBe(1)
      expect(hasBoundedPredictionRuntime(payload)).toBe(false)
    }
    expect(hasBoundedPredictionRuntime(health(1))).toBe(true)
  })

  it('extracts structured quota/auth messages without rendering objects', () => {
    expect(predictionErrorMessage({ detail: { error: 'pred_limit_exceeded', message: '月間予測回数の上限に達しました。' } }, 429)).toBe('月間予測回数の上限に達しました。')
    expect(predictionErrorMessage({ detail: '認証が必要です。' }, 401)).toBe('認証が必要です。')
    expect(predictionErrorMessage({ detail: { message: { unexpected: 'object' } } }, 503)).toBe('HTTP 503')
    expect(predictionErrorMessage(undefined, 500)).toBe('HTTP 500')
  })

  it('fills a freed slot immediately without waiting for a slow chunk and caps concurrency at four', async () => {
    const pending = Array.from({ length: 7 }, () => deferred<number>())
    const started: number[] = []
    let maxActive = 0
    const run = runPredictionQueue([0, 1, 2, 3, 4, 5, 6], index => {
      started.push(index)
      return pending[index].promise
    }, { onProgress: progress => { maxActive = Math.max(maxActive, progress.active) } })
    expect(started).toEqual([0, 1, 2, 3])
    pending[1].resolve(1)
    await Promise.resolve()
    expect(started).toEqual([0, 1, 2, 3, 4])
    pending[4].resolve(4)
    await Promise.resolve()
    expect(started).toEqual([0, 1, 2, 3, 4, 5])
    pending[5].resolve(5)
    await Promise.resolve()
    expect(started).toEqual([0, 1, 2, 3, 4, 5, 6])
    pending.forEach((item, index) => item.resolve(index))
    const result = await run
    expect(maxActive).toBe(PREDICTION_CONCURRENCY)
    expect(result.cancelled).toBe(false)
    expect(result.outcomes).toEqual(Array.from({ length: 7 }, (_, value) => ({ status: 'fulfilled', value })))
  })

  it('continues other races after a rejection and reports every outcome once', async () => {
    const onSettled = vi.fn()
    const onProgress = vi.fn()
    const result = await runPredictionQueue([0, 1, 2, 3, 4], async value => {
      if (value === 1) throw new Error('race failed')
      return value
    }, { concurrency: 2, onSettled, onProgress })
    expect(onSettled).toHaveBeenCalledTimes(5)
    expect(result.outcomes[1]).toMatchObject({ status: 'rejected', reason: new Error('race failed') })
    expect(result.outcomes[4]).toEqual({ status: 'fulfilled', value: 4 })
    expect(onProgress).toHaveBeenLastCalledWith({ completed: 5, total: 5, active: 0 })
  })

  it('does not start queued tasks or publish late results after cancellation', async () => {
    const controller = new AbortController()
    const pending = deferred<number>()
    const task = vi.fn(() => pending.promise)
    const onSettled = vi.fn()
    const onProgress = vi.fn()
    const run = runPredictionQueue([0, 1, 2], task, { concurrency: 1, signal: controller.signal, onSettled, onProgress })
    controller.abort()
    const before = onProgress.mock.calls.length
    pending.resolve(0)
    const result = await run
    expect(task).toHaveBeenCalledTimes(1)
    expect(onSettled).not.toHaveBeenCalled()
    expect(onProgress).toHaveBeenCalledTimes(before)
    expect(result.cancelled).toBe(true)
    expect(result.outcomes[1]).toBeUndefined()
  })

  it('accepts an empty set and clamps invalid concurrency safely', async () => {
    const task = vi.fn(async () => 1)
    expect(await runPredictionQueue([], task)).toEqual({ outcomes: [], cancelled: false })
    const active: number[] = []
    await runPredictionQueue([1, 2], task, { concurrency: Number.NaN, onProgress: progress => active.push(progress.active) })
    expect(Math.max(...active)).toBe(1)
  })

  it('shows only measured speed and a explicitly approximate remaining time', () => {
    expect(predictionTimingLabel({ completed: 0, total: 24, active: 4 }, 5000)).toBe('経過 5秒 · 速度を計測中')
    expect(predictionTimingLabel({ completed: 6, total: 24, active: 4 }, 30000)).toBe('経過 30秒 · 12.0レース/分 · 残り約1分30秒')
    expect(predictionTimingLabel({ completed: 24, total: 24, active: 0 }, 120000)).toBe('経過 2分0秒 · 12.0レース/分')
  })
})
