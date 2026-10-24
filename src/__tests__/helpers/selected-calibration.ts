import type { PerformanceMode, SelectionCalibrationMetrics, SelectionDiagnostics } from '@/lib/performance-records'

export const emptyCalibrationMetrics = (): SelectionCalibrationMetrics => ({
  ticket_count: 0, race_count: 0, date_count: 0, mean_probability: null, hit_rate: null,
  bias_predicted_minus_actual: null, mean_probability_ci95: null, hit_rate_ci95: null,
  bias_ci95: null, bootstrap_valid_samples: 0, interval_status: 'no_observations',
})

export function selectedCalibrationFixture(mode: PerformanceMode = 'paper'): SelectionDiagnostics {
  const measured: SelectionCalibrationMetrics = {
    ticket_count: 4, race_count: 3, date_count: 3, mean_probability: .5, hit_rate: .25,
    bias_predicted_minus_actual: .25, mean_probability_ci95: [.4, .6], hit_rate_ci95: [0, .75],
    bias_ci95: [-.2, .7], bootstrap_valid_samples: 2000, interval_status: 'descriptive_interval',
  }
  const bounds = [1, 3, 5, 10, 20, 30]
  return {
    schema: 'recorded-selection-calibration-v1', label: '観測済み買い目の校正診断',
    observational: true, unknown_holdout: false, purchase_authority: false,
    scope: { mode, date_from: null, date_to: null, record_count: 10, includes_full_filtered_set: true },
    status_counts: { settled: 3, pending: 2, payout_pending: 1, review: 1, skipped: 3, other: 0 },
    sample: { eligible_tickets: 4, eligible_races: 3, eligible_dates: 3, excluded_records: 7, excluded_tickets: 3 },
    excluded_counts: { pending: 2, payout_pending: 1, review: 1, no_selection: 3 },
    excluded_ticket_counts: { pending: 2, payout_pending: 1 },
    groups: [{ ...measured, model_id: 'frozen-model-A', policy_version: 'value-selection-v2', bet_type: '単勝',
      odds_bands: bounds.map((lower, index) => ({ ...emptyCalibrationMetrics(), ...(index < 2 ? { ...measured, ticket_count: 2, race_count: 2, date_count: 2 } : {}),
        band: `band-${index}`, label: index === 5 ? '30+' : `${lower}–${bounds[index + 1]}`, lower, upper: bounds[index + 1] ?? null })) }],
    bootstrap: { method: 'paired_whole_race_date_percentile', confidence_level: .95, repetitions: 2000,
      seed: 20261012, interval_scope: 'conditional_on_verified_settled_recorded_selection',
      simultaneous_intervals: false, multiple_comparisons_adjusted: false },
  }
}
