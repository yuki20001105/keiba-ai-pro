import os
import sys
import sqlite3

os.environ['SUPABASE_URL'] = ''
sys.path.insert(0, 'python-api')

from fastapi.testclient import TestClient
from main import app
from mlops import MLOpsStore

c = TestClient(app)
r = c.get('/api/mlops/research/scenario-router/ops/dashboard.html?target=win&limit=20')
h = r.text if r.status_code == 200 else ''

# 1) html 200
print('a1_html_200', r.status_code == 200)

# 2) Action Preview section visible
print('a2_section_visible', 'Action Preview Snippets' in h)

# 3) details/summary visible
print('a3_details_summary', ('<details>' in h) and ('<summary>' in h))

# 4) preview/evaluate/generate/test post snippets shown
need = [
    '/api/mlops/research/scenario-router/incidents/actions/preview',
    '/api/mlops/research/scenario-router/incidents/response/prepare',
    '/api/mlops/research/scenario-router/auto-recovery/evaluate',
    '/api/mlops/research/scenario-router/runbooks/generate',
    '/api/mlops/research/scenario-router/notifications/test',
]
print('a4_post_snippets_present', all(x in h for x in need))

# 5) dangerous mutation snippets not shown
forbidden = ['/execute', '/resolve', '/rollback', '/stop', '/apply', '/promote', '/disable', '/delete']
print('a5_no_dangerous_snippets', not any(x in h.lower() for x in [f.lower() for f in forbidden]))

# 6) copy button on post snippets exists
print('a6_post_copy_button', "id='btn_100'" in h and "class='copy-btn'" in h)

# 7) javascript does not execute API
print('a7_js_no_fetch', ('fetch(' not in h) and ('XMLHttpRequest' not in h) and ('navigator.clipboard.writeText' in h))

# 8) DB unchanged after loading html
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
print('a8_db_unchanged', pre == post)
db.close()
