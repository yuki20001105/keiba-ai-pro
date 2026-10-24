import { describe, expect, it } from 'vitest'
import { buildScrapeAcquisitionDisplay, scrapeQueueTitle } from '@/lib/scrape-acquisition-display'

const job = (progress: Record<string, unknown> = {}, status = 'running') => ({
  status,
  progress: {
    race_task_concurrency: 3,
    max_concurrent_http_requests: 1,
    http_pacing_scope: 'provider-family',
    ...progress,
  },
})

describe('server-reported acquisition presentation', () => {
  it('does not infer server acceptance or execution from a locally queued ID', () => {
    expect(scrapeQueueTitle(false)).toBe('サーバー受付を確認中（取得開始は未確認）')
    expect(scrapeQueueTitle(true)).toBe('サーバー受付済み・開始待ち')
    expect(buildScrapeAcquisitionDisplay(job({}, 'queued'))).toBeNull()
  })

  it('shows only bounded server-reported local concurrency and shared HTTP policy', () => {
    expect(buildScrapeAcquisitionDisplay(job())).toEqual({
      parallelism: '解析・保存 最大3並列 / 外部通信は共通制限', speed: null, interval: null,
    })
    for (const concurrency of [undefined, '3', true, 0, -1, 5, 3.5, NaN, Infinity]) {
      expect(buildScrapeAcquisitionDisplay(job({ race_task_concurrency: concurrency }))).toBeNull()
    }
    expect(buildScrapeAcquisitionDisplay(job({ max_concurrent_http_requests: 3 }))).toBeNull()
    expect(buildScrapeAcquisitionDisplay(job({ http_pacing_scope: 'host' }))).toBeNull()
    expect(buildScrapeAcquisitionDisplay(job({ current_status: 'queued' }))).toBeNull()
    expect(buildScrapeAcquisitionDisplay(job({ current_status: 'waiting_resources' }))).toBeNull()
    expect(buildScrapeAcquisitionDisplay({ status: 'running' })).toBeNull()
  })

  it('reports measured current-attempt HTTP speed without claiming a whole-period ETA', () => {
    const display = buildScrapeAcquisitionDisplay(job({
      current_status: 'running', fetch_metrics_scope: 'current_worker_attempt',
      fetch_metrics: { network_requests: 20 }, attempt_elapsed_sec: 40,
      fetch_control: { interval: 1.5 },
    }))
    expect(display?.speed).toBe('20通信 · 30.0通信/分（現作業枠）')
    expect(display?.interval).toBe('1.5秒/回')
    expect(display).not.toHaveProperty('eta')
    expect(buildScrapeAcquisitionDisplay(job({
      fetch_metrics_scope: 'unknown', fetch_metrics: { network_requests: 20 }, attempt_elapsed_sec: 40,
    }))?.speed).toBeNull()
  })

  it('does not turn malformed or unmeasured counters into observed speed', () => {
    for (const requests of [undefined, '20', true, -1, 1.5, NaN, Infinity]) {
      expect(buildScrapeAcquisitionDisplay(job({
        fetch_metrics_scope: 'current_worker_attempt', fetch_metrics: { network_requests: requests },
        attempt_elapsed_sec: 40,
      }))?.speed).toBeNull()
    }
    for (const elapsed of [undefined, '40', 0, -1, NaN, Infinity]) {
      expect(buildScrapeAcquisitionDisplay(job({
        fetch_metrics_scope: 'current_worker_attempt', fetch_metrics: { network_requests: 20 },
        attempt_elapsed_sec: elapsed,
      }))?.speed).toBeNull()
    }
    expect(buildScrapeAcquisitionDisplay(job({
      fetch_metrics_scope: 'current_worker_attempt', fetch_metrics: { network_requests: 0 },
      attempt_elapsed_sec: 40,
    }))?.speed).toBe('0通信 · 0.0通信/分（現作業枠）')
  })

  it('does not show a misleading burst rate from the first few seconds of an attempt', () => {
    for (const elapsed of [0.6, 1, 5, 9.9]) {
      expect(buildScrapeAcquisitionDisplay(job({ fetch_metrics_scope: 'current_worker_attempt',
        fetch_metrics: { network_requests: 1 }, attempt_elapsed_sec: elapsed,
      }))?.speed).toBe('1通信 · 速度計測中（現作業枠）')
    }
    expect(buildScrapeAcquisitionDisplay(job({ fetch_metrics_scope: 'current_worker_attempt',
      fetch_metrics: { network_requests: 1 }, attempt_elapsed_sec: 10,
    }))?.speed).toBe('1通信 · 6.0通信/分（現作業枠）')
  })
})
