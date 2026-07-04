# Scenario Router Audit Gate

- gate_status: WARN
- exit_code: 0
- latest_status: PASS
- last_10_success_rate: 100.00%
- flaky_warning: False
- suggested_next_action: Review warnings and keep monitoring trend before tightening strict mode.
- baseline_window: 10
- duration_warn_multiplier: 2.00
- duration_fail_multiplier: 4.00
- min_baseline_samples: 5
- fail_on_duration_spike: False

## Reasons

- none

## Warnings

- duration baseline fail threshold exceeded: scenario_router_auto_recovery_e2e

## Baseline Evaluation

| step_name | status | current_sec | sample_count | median_sec | mean_sec | p95_sec | warn_threshold_sec | fail_threshold_sec | note |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| health_check | OK | 1.00 | 10 | 1.09 | 1.09 | 1.17 | 2.18 | 4.36 |  |
| import_smoke | OK | 1.50 | 10 | 1.54 | 1.54 | 1.59 | 3.09 | 6.18 |  |
| py_compile | OK | 2.00 | 10 | 2.04 | 2.04 | 2.09 | 4.09 | 8.18 |  |
| scenario_router_auto_recovery_e2e | FAIL | 34.50 | 10 | 8.45 | 8.45 | 8.86 | 16.90 | 33.80 |  |
| scenario_router_e2e | OK | 6.00 | 10 | 6.13 | 6.13 | 6.26 | 12.27 | 24.54 |  |
