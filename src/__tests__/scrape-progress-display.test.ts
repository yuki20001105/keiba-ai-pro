import { describe, expect, test } from 'vitest'
import { buildScrapeProgressDisplay } from '@/lib/scrape-progress-display'

describe('buildScrapeProgressDisplay', () => {
  test('shows healthy protected pacing with concrete throughput and ETA', () => {
    const value = buildScrapeProgressDisplay({
      created_at: '2026-10-10T03:53:00Z',
      progress: {
        saved_races: 4,
        saved_horses: 61,
        current_date_processed_races: 4,
        current_date_total_races: 24,
        attempt_elapsed_sec: 600,
        network_requests_per_min: 12,
        fetch_control: { interval: 4.785, cooldown_until: 0, failures: 0, blocked_reason: '' },
        fetch_metrics: { network_requests: 120, http_success_count: 119 },
      },
    }, Date.parse('2026-10-10T04:03:00Z'))

    expect(value).toMatchObject({
      tone: 'protected', title: '保護速度で正常取得中', interval: '4.8秒/回',
      requestSpeed: '12.0通信/分', httpStatus: '119/120 正常 (99%)',
      saved: '4レース・61頭', raceProgress: '4/24レース', eta: '残り約50分',
    })
  })

  test('prioritizes an active cooldown over the interval label', () => {
    const value = buildScrapeProgressDisplay({
      progress: {
        fetch_control: { interval: 8, cooldown_until: 2_000, failures: 0, blocked_reason: '' },
        fetch_metrics: {},
      },
    }, 1_000_000)
    expect(value?.tone).toBe('waiting')
    expect(value?.title).toBe('アクセス制限で待機中')
  })
})
