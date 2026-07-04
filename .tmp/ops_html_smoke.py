import os
import sys
import sqlite3

os.environ['SUPABASE_URL'] = ''
sys.path.insert(0, 'python-api')

from fastapi.testclient import TestClient
from main import app
from mlops import MLOpsStore

c = TestClient(app)

r1 = c.get('/api/mlops/research/scenario-router/ops/dashboard.html')
h1 = r1.text if r1.status_code == 200 else ''
print('t1_no_param_200', r1.status_code == 200)

r2 = c.get('/api/mlops/research/scenario-router/ops/dashboard.html?target=win')
print('t2_target_win_200', r2.status_code == 200)

r3 = c.get('/api/mlops/research/scenario-router/ops/dashboard.html?target=win&limit=20')
h3 = r3.text if r3.status_code == 200 else ''
print('t3_limit20_200', r3.status_code == 200)
print('t3_limit20_applied', 'Applied limit</div><div>20' in h3)

r4 = c.get('/api/mlops/research/scenario-router/ops/dashboard.html?target=win&limit=999')
h4 = r4.text if r4.status_code == 200 else ''
print('t4_limit999_200', r4.status_code == 200)
print('t4_limit999_clamped100', 'Applied limit</div><div>100' in h4)

r5 = c.get('/api/mlops/research/scenario-router/ops/dashboard.html?target=win&limit=20&refresh=30')
h5 = r5.text if r5.status_code == 200 else ''
print('t5_refresh30_200', r5.status_code == 200)
print('t5_refresh30_meta', "http-equiv='refresh' content='30'" in h5)

r6 = c.get('/api/mlops/research/scenario-router/ops/dashboard.html?target=win&limit=abc&refresh=-5')
h6 = r6.text if r6.status_code == 200 else ''
print('t6_invalid_params_not500', r6.status_code == 200)
print('t6_invalid_default_limit', 'Applied limit</div><div>10' in h6)

print('t7_has_json_link', '/api/mlops/research/scenario-router/ops/dashboard?' in h1)
print('t7_has_audit_latest_link', '/api/mlops/research/scenario-router/ops/audit/latest' in h1)
print('t7_has_history_link', '/api/mlops/research/scenario-router/ops/audit/history' in h1)
print('t7_has_incidents_link', '/api/mlops/research/scenario-router/ops/incidents/latest' in h1)

rj = c.get('/api/mlops/research/scenario-router/ops/dashboard?target=win&limit=999&refresh=99999&show_raw_links=true')
dj = rj.json() if rj.status_code == 200 else {}
af = dj.get('applied_filters') if isinstance(dj.get('applied_filters'), dict) else {}
print('t_json_200', rj.status_code == 200)
print('t_json_limit_clamped', af.get('limit') == 100)
print('t_json_refresh_clamped', af.get('refresh') == 3600)

store = MLOpsStore()
db = sqlite3.connect(str(store.db_path))
tables = [
    'scenario_router_alerts',
    'scenario_router_runbooks',
    'scenario_router_incident_responses',
    'scenario_router_incident_actions',
    'scenario_router_auto_recovery_executions',
    'scenario_router_notification_deliveries',
    'scenario_router_rollouts',
]
pre = {}
for t in tables:
    try:
        pre[t] = db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
    except Exception:
        pre[t] = None

_ = c.get('/api/mlops/research/scenario-router/ops/dashboard.html?target=all&limit=20&refresh=30&show_raw_links=true')

post = {}
for t in tables:
    try:
        post[t] = db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
    except Exception:
        post[t] = None

print('t8_db_unchanged', pre == post)
db.close()
