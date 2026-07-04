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
print('s1_html_200', r1.status_code == 200)
print('s2_api_operations_visible', 'API Operations' in h1)
print('s3_get_curl_visible', ('curl -sS' in h1) and ('/api/mlops/research/scenario-router/ops/dashboard?' in h1))

r4 = c.get('/api/mlops/research/scenario-router/ops/dashboard.html?target=win&limit=20')
h4 = r4.text if r4.status_code == 200 else ''
print('s4_target_limit_reflected', ('target=win' in h4) and ('limit=20' in h4))

forbidden = [' -X POST', '/resolve?', '/rollback', '/stop-canary']
print('s5_no_forbidden_snippets', not any(x.lower() in h1.lower() for x in forbidden))

r6 = c.get('/api/mlops/research/scenario-router/ops/dashboard.html?target=__empty_target__&limit=5')
h6 = r6.text if r6.status_code == 200 else ''
print('s6_empty_stable_200', r6.status_code == 200)
print('s6_empty_stable_sections', ('Latest Alerts' in h6) and ('Latest Incident Responses' in h6) and ('Latest Notification Deliveries' in h6))

store = MLOpsStore()
db = sqlite3.connect(str(store.db_path))
tables = ['scenario_router_alerts','scenario_router_runbooks','scenario_router_incident_responses','scenario_router_incident_actions','scenario_router_auto_recovery_executions','scenario_router_notification_deliveries','scenario_router_rollouts']
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
db.close()
print('s7_db_unchanged', pre == post)
