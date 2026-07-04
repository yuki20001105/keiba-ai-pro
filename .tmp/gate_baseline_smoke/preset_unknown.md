# Scenario Router Audit Gate

- gate_status: FAIL
- exit_code: 1
- latest_status: 
- last_10_success_rate: 0.00%
- flaky_warning: False
- suggested_next_action: Fix preset selection and retry quality gate.
- baseline_window: 10
- duration_warn_multiplier: 2.00
- duration_fail_multiplier: 4.00
- min_baseline_samples: 5
- min_last_10_success_rate: 80.00%
- strict: False
- fail_on_flaky: False
- fail_on_duration_spike: False

## Applied Preset

- applied_preset: does_not_exist
- preset_file: scripts/scenario_router_audit_gate_presets.yaml
- required_steps:
  - health_check
  - scenario_router_e2e
  - scenario_router_auto_recovery_e2e
  - py_compile
  - import_smoke

## Reasons

- unknown preset 'does_not_exist' (file: scripts\scenario_router_audit_gate_presets.yaml, available: ci, local, nightly, pr)

## Warnings

- none

## Baseline Evaluation

- none
