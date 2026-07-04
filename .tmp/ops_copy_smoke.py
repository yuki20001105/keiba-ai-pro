import os
import sys
import sqlite3

os.environ['SUPABASE_URL'] = ''
sys.path.insert(0, 'python-api')

from fastapi.testclient import TestClient
from main import app
from mlops import MLOpsStore

c = TestClient(app)

# 1) html 200
r1 = c.get('/api/mlops/research/scenario-router/ops/dashboard.html')
h1 = r1.text if r1.status_code == 200 else ''
print('c1_html_200', r1.status_code == 200)

# 2) API Operations + Copy button visible
print('c2_api_operations', 'API Operations' in h1)
print('c2_copy_button_visible', "class='copy-btn'" in h1)

# 3) GET snippets still visible
print('c3_get_snippet_dashboard', '/api/mlops/research/scenario-router/ops/dashboard?' in h1)
print('c3_get_snippet_alerts', '/api/mlops/research/scenario-router/alerts?' in h1)

# 4) target/limit reflected
r4 = c.get('/api/mlops/research/scenario-router/ops/dashboard.html?target=win&limit=20')
h4 = r4.text if r4.status_code == 200 else ''
print('c4_reflect_target_limit', ('target=win' in h4) and ('limit=20' in h4))

# 5) no execute/rollback/stop style snippets rendered
forbidden = [' -X POST', '/execute', '/resolve?', '/rollback', '/stop-canary']
print('c5_no_forbidden', not any(x.lower() in h1.lower() for x in forbidden))

# 6) copy javascript included
print('c6_js_clipboard', 'navigator.clipboard.writeText' in h1)
print('c6_js_fallback', 'document.execCommand(\'copy\')' in h1)

# 7) empty alerts/incidents still stable
r7 = c.get('/api/mlops/research/scenario-router/ops/dashboard.html?target=__empty_target__&limit=5')
h7 = r7.text if r7.status_code == 200 else ''
print('c7_empty_200', r7.status_code == 200)
print('c7_empty_sections', ('Latest Alerts' in h7) and ('Latest Incident Responses' in h7) and ('Latest Notification Deliveries' in h7))

# 8) DB unchanged
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
print('c8_db_unchanged', pre == post)
