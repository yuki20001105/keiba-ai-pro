# Scenario Router Audit Gate

- gate_status: WARN
- exit_code: 0
- latest_status: PASS
- last_10_success_rate: 83.33%
- flaky_warning: True
- suggested_next_action: Review warnings and keep monitoring trend before tightening strict mode.
- baseline_window: 10
- duration_warn_multiplier: 2.50
- duration_fail_multiplier: 5.00
- min_baseline_samples: 5
- min_last_10_success_rate: 80.00%
- strict: False
- fail_on_flaky: False
- fail_on_duration_spike: False

## Applied Preset

- applied_preset: ci
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

- flaky_warning=true

## Baseline Evaluation

| step_name | status | current_sec | sample_count | median_sec | mean_sec | p95_sec | warn_threshold_sec | fail_threshold_sec | note |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| fastapi_start_or_reuse | OK | 6.91 | 5 | 6.88 | 7.04 | 9.49 | 17.20 | 34.40 |  |
| health_check | OK | 0.25 | 5 | 0.26 | 0.26 | 0.28 | 0.66 | 1.32 |  |
| import_smoke | OK | 1.18 | 5 | 1.55 | 1.70 | 2.72 | 3.89 | 7.77 |  |
| py_compile | OK | 0.12 | 5 | 0.12 | 0.13 | 0.18 | 0.29 | 0.59 |  |
| sandbox_fixture_create | OK | 10.87 | 5 | 10.84 | 9.85 | 11.29 | 27.09 | 54.18 |  |
| scenario_router_auto_recovery_e2e | OK | 0.81 | 5 | 0.95 | 0.93 | 0.99 | 2.38 | 4.76 |  |
| scenario_router_e2e | OK | 111.59 | 5 | 111.54 | 90.31 | 113.49 | 278.85 | 557.70 |  |
