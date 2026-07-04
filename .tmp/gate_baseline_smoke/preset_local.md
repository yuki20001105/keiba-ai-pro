# Scenario Router Audit Gate

- gate_status: PASS
- exit_code: 0
- latest_status: PASS
- last_10_success_rate: 100.00%
- flaky_warning: False
- suggested_next_action: Quality gate passed. Continue normal audit cadence.
- baseline_window: 5
- duration_warn_multiplier: 3.00
- duration_fail_multiplier: 6.00
- min_baseline_samples: 3
- min_last_10_success_rate: 70.00%
- strict: False
- fail_on_flaky: False
- fail_on_duration_spike: False

## Applied Preset

- applied_preset: local
- preset_file: scripts\scenario_router_audit_gate_presets.yaml
- required_steps:
  - health_check
  - scenario_router_e2e
  - scenario_router_auto_recovery_e2e
  - py_compile
  - import_smoke

## Reasons

- none

## Warnings

- none

## Baseline Evaluation

| step_name | status | current_sec | sample_count | median_sec | mean_sec | p95_sec | warn_threshold_sec | fail_threshold_sec | note |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| health_check | OK | 1.00 | 5 | 1.14 | 1.14 | 1.18 | 3.42 | 6.84 |  |
| import_smoke | OK | 1.50 | 5 | 1.57 | 1.57 | 1.59 | 4.71 | 9.42 |  |
| py_compile | OK | 2.00 | 5 | 2.07 | 2.07 | 2.09 | 6.21 | 12.42 |  |
| scenario_router_auto_recovery_e2e | OK | 12.00 | 5 | 8.70 | 8.70 | 8.88 | 26.10 | 52.20 |  |
| scenario_router_e2e | OK | 6.00 | 5 | 6.21 | 6.21 | 6.26 | 18.63 | 37.26 |  |
