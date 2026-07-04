# Scenario Router Audit Trend

- latest_status: PASS
- last_10_success_rate: 83.33%
- last_30_success_rate: 83.33%
- flaky_warning: True
- suggested_next_action: Audit appears flaky. Preserve sandbox on next failure and compare step durations/evidence.

## Common Failure Types

- SCENARIO_ROUTER_E2E_FAILED: 1

## Slowest Steps (Avg)

- scenario_router_e2e: 93.86s (n=6)
- sandbox_fixture_create: 10.02s (n=6)
- fastapi_start_or_reuse: 7.02s (n=6)
- import_smoke: 1.61s (n=6)
- scenario_router_auto_recovery_e2e: 0.91s (n=6)

## Step Baselines (Last 10)

| step_name | latest_sec | median_sec | mean_sec | p95_sec | samples |
|---|---:|---:|---:|---:|---:|
| fastapi_start_or_reuse | 6.91 | 6.89 | 7.02 | 9.33 | 6 |
| health_check | 0.25 | 0.26 | 0.26 | 0.28 | 6 |
| import_smoke | 1.18 | 1.38 | 1.61 | 2.65 | 6 |
| py_compile | 0.12 | 0.12 | 0.13 | 0.18 | 6 |
| sandbox_fixture_create | 10.87 | 10.85 | 10.02 | 11.27 | 6 |
| scenario_router_auto_recovery_e2e | 0.81 | 0.92 | 0.91 | 0.99 | 6 |
| scenario_router_e2e | 111.59 | 111.57 | 93.86 | 113.45 | 6 |
