# Scenario Router Audit Gate

- gate_status: PASS
- exit_code: 0
- latest_status: PASS
- last_10_success_rate: 100.00%
- flaky_warning: False
- suggested_next_action: Quality gate passed. Continue normal audit cadence.
- baseline_window: 10
- duration_warn_multiplier: 3.00
- duration_fail_multiplier: 6.00
- min_baseline_samples: 5
- min_last_10_success_rate: 80.00%
- strict: False
- fail_on_flaky: False
- fail_on_duration_spike: False

## Applied Preset

- applied_preset: pr
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
| health_check | OK | 1.00 | 10 | 1.09 | 1.09 | 1.17 | 3.27 | 6.54 |  |
| import_smoke | OK | 1.50 | 10 | 1.54 | 1.54 | 1.59 | 4.63 | 9.27 |  |
| py_compile | OK | 2.00 | 10 | 2.04 | 2.04 | 2.09 | 6.13 | 12.27 |  |
| scenario_router_auto_recovery_e2e | OK | 12.00 | 10 | 8.45 | 8.45 | 8.86 | 25.35 | 50.70 |  |
| scenario_router_e2e | OK | 6.00 | 10 | 6.13 | 6.13 | 6.26 | 18.41 | 36.81 |  |
