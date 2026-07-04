# Scenario Router Audit Report

- started_at: 2026-07-04T15:46:13.175899+00:00
- finished_at: 2026-07-04T15:48:24.911599+00:00
- overall_status: PASS
- passed: 7
- failed: 0
- duration_sec: 131.74
- fastapi_mode: started
- sandbox_enabled: True
- sandbox_db_path: C:\Users\yuki2\Documents\ws\keiba-ai-pro\.tmp\scenario_router_audit\sr_audit_wqy36l_r\mlops.sandbox.db
- sandbox_race_db_path: C:\Users\yuki2\Documents\ws\keiba-ai-pro\.tmp\scenario_router_audit\sr_audit_wqy36l_r\keiba_ultimate.sandbox.db
- cleanup_status: deleted

## Failure Triage

- failure_type: NONE
- likely_cause: 
- evidence: 
- suggested_fix:
  1. (none)
- rerun_command: python scripts/run_scenario_router_audit.py --sandbox --enable-gate --gate-preset ci

## Steps

| step | status | duration_sec | error_message |
|---|---|---:|---|
| sandbox_fixture_create | PASS | 10.87 |  |
| fastapi_start_or_reuse | PASS | 6.91 |  |
| health_check | PASS | 0.25 |  |
| scenario_router_e2e | PASS | 111.59 |  |
| scenario_router_auto_recovery_e2e | PASS | 0.81 |  |
| py_compile | PASS | 0.12 |  |
| import_smoke | PASS | 1.18 |  |

## Output Tail

### sandbox_fixture_create

```
{"sandbox_dir": "C:\\Users\\yuki2\\Documents\\ws\\keiba-ai-pro\\.tmp\\scenario_router_audit\\sr_audit_wqy36l_r", "mlops_db_path": "C:\\Users\\yuki2\\Documents\\ws\\keiba-ai-pro\\.tmp\\scenario_router_audit\\sr_audit_wqy36l_r\\mlops.sandbox.db", "race_db_path": "C:\\Users\\yuki2\\Documents\\ws\\keiba-ai-pro\\.tmp\\scenario_router_audit\\sr_audit_wqy36l_r\\keiba_ultimate.sandbox.db", "fixture_minimal": false}
```

### fastapi_start_or_reuse

```
{"mode": "started", "pid": 55608}
```

### health_check

```
url=http://127.0.0.1:8000/health
```

### scenario_router_e2e

```
base_url http://127.0.0.1:8000
race_candidates 59
request_model_id model_speed_deviation_lightgbm_20130101_20180128_20260612_2207
health_ok True
off_probe_202602010101 True
race_id 202602010101
off_api_200 True
off_prediction_runs_saved True
off_prediction_results_saved True
off_router_mode_saved True
off_effective_router_mode_saved True
off_actual_model_id_correct True
off_selected_model_id_saved True
off_shadow_selected_model_id_saved True
shadow_api_200 True
shadow_prediction_runs_saved True
shadow_prediction_results_saved True
shadow_router_mode_saved True
shadow_effective_router_mode_saved True
shadow_actual_model_id_correct True
shadow_selected_model_id_saved True
shadow_shadow_selected_model_id_saved True
shadow_actual_not_switched True
canary0_api_200 True
canary0_prediction_runs_saved True
canary0_prediction_results_saved True
canary0_router_mode_saved True
canary0_effective_router_mode_saved True
canary0_actual_model_id_correct True
canary0_selected_model_id_saved True
canary0_shadow_selected_model_id_saved True
canary0_canary_percent_saved True
canary0_canary_bucket_saved True
canary0_canary_selected_saved True
canary100_api_200 True
canary100_prediction_runs_saved True
canary100_prediction_results_saved True
canary100_router_mode_saved True
canary100_effective_router_mode_saved True
canary100_actual_model_id_correct True
canary100_selected_model_id_saved True
canary100_shadow_selected_model_id_saved True
canary100_canary_percent_saved True
canary100_canary_bucket_saved True
canary100_canary_selected_saved True
active_api_200 True
active_prediction_runs_saved True
active_prediction_results_saved True
active_router_mode_saved True
active_effective_router_mode_saved True
active_actual_model_id_correct True
active_selected_model_id_saved True
active_shadow_selected_model_id_saved True
active_selected_model_id_non_empty True
canary_bucket_same_for_same_race True
list_api_200 True
list_api_contains_legacy True
list_api_contains_new True
get_api_200 True
get_api_legacy_id_match True
get_api_legacy_results_present True
cleanup_done True

```

### scenario_router_auto_recovery_e2e

```
[PASS] health: status=200
[PASS] admin_auth: probe_status=404
[PASS] setup_alert: sra_e2e_auto_247e4ac5cf
[PASS] setup_policy: srarp_e2e_11c7e7b944
[PASS] prepare_response_api: status=200
[PASS] prepare_response_id: srir_69a8bf907815
[PASS] evaluate_api: status=200
[PASS] 1_plan_generated: plan_size=10
[PASS] 2_run_canary_auto_execute
[PASS] 3_danger_manual_required
[PASS] execute_dry_http: status=200
[PASS] 4_dry_run_no_state_change: statuses=['DRY_RUN']
[PASS] execute_apply_http: status=200
[PASS] 5_safe_only_execute
[PASS] 6_danger_not_executed_without_confirm
[PASS] executions_api: status=200
[PASS] 7_history_saved: count=20 statuses=['DRY_RUN', 'EXECUTED', 'SKIPPED']
[PASS] 8_cleanup
RESULT: PASS

```

### py_compile

```
(no output)
```

### import_smoke

```
import_smoke_ok

2026-07-05 00:48:24,221 - INFO - ================================================================================
2026-07-05 00:48:24,221 - INFO - app_config ロード開始
2026-07-05 00:48:24,221 - INFO - ログファイル: C:\Users\yuki2\Documents\ws\keiba-ai-pro\python-api\optuna_debug.log
2026-07-05 00:48:24,221 - INFO - ================================================================================
2026-07-05 00:48:24,239 - INFO - Supabase クライアント読み込み成功
2026-07-05 00:48:24,239 - INFO - Supabase データ操作: 無効（認証専用モード）

```
