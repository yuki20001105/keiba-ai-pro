# Scenario Router Audit Gate

- gate_status: PASS
- exit_code: 0
- latest_status: PASS
- last_10_success_rate: 100.00%
- flaky_warning: False
- suggested_next_action: Quality gate passed. Continue normal audit cadence.
- baseline_window: 10
- duration_warn_multiplier: 2.00
- duration_fail_multiplier: 4.00
- min_baseline_samples: 5
- fail_on_duration_spike: False

## Reasons

- none

## Warnings

- none

## Baseline Evaluation

| step_name | status | current_sec | sample_count | median_sec | mean_sec | p95_sec | warn_threshold_sec | fail_threshold_sec | note |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| health_check | INFO | 1.00 | 3 | 1.02 | 1.02 | 1.04 | 2.04 | 4.08 | insufficient baseline samples |
| import_smoke | INFO | 1.50 | 3 | 1.51 | 1.51 | 1.52 | 3.02 | 6.04 | insufficient baseline samples |
| py_compile | INFO | 2.00 | 3 | 2.01 | 2.01 | 2.02 | 4.02 | 8.04 | insufficient baseline samples |
| scenario_router_auto_recovery_e2e | INFO | 12.00 | 3 | 8.10 | 8.10 | 8.19 | 16.20 | 32.40 | insufficient baseline samples |
| scenario_router_e2e | INFO | 6.00 | 3 | 6.03 | 6.03 | 6.06 | 12.06 | 24.12 | insufficient baseline samples |
