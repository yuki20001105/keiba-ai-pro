#!/usr/bin/env python3
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Set, Tuple

def _resolve_base_ref() -> str:
    value = os.environ.get("SCANNER_BASE_REF", "origin/develop").strip() or "origin/develop"
    proc = subprocess.run(
        ["git", "check-ref-format", f"refs/remotes/{value}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError("SCANNER_BASE_REF must be a valid remote-tracking branch")
    return value


BASE_REF = _resolve_base_ref()

PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    ("test.skip", re.compile(r"\btest\.skip\s*\(")),
    ("describe.skip", re.compile(r"\bdescribe\.skip\s*\(")),
    ("it.skip", re.compile(r"\bit\.skip\s*\(")),
    ("pytest.skip", re.compile(r"\bpytest\.skip\s*\(")),
    ("pytest.mark.skip", re.compile(r"@pytest\.mark\.skip\b")),
    ("pytest.mark.xfail", re.compile(r"@pytest\.mark\.xfail\b")),
    ("xfail", re.compile(r"\bxfail\s*\(")),
    ("assert_true", re.compile(r"\bassert\s+True\b")),
    ("except_pass", re.compile(r"except\s+Exception\s*:\s*pass\b")),
]

TEST_CODE_PATH = re.compile(r"(^e2e/)|(^src/__tests__/)|(^python-api/tests/)|(_test\.py$)|(\.test\.[jt]sx?$)|(\.spec\.[jt]sx?$)")

DELETED_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    ("deleted_js_test_call", re.compile(r"\b(?:test|it)\s*\(")),
    ("deleted_py_test_function", re.compile(r"^\s*def\s+test_[A-Za-z0-9_]*\s*\(")),
    ("deleted_expect", re.compile(r"\bexpect\s*\(")),
    ("deleted_assert", re.compile(r"\bassert\b")),
    ("deleted_pytest_raises", re.compile(r"\bpytest\.raises\s*\(")),
]

ALLOWLIST_EXACT: Dict[str, str] = {
    # The authenticated Data Collection suites now use an isolated Admin
    # session, so their UI contracts run without operator credentials.
    "e2e/data-collection-dry-run.spec.ts:await expect(page.locator('input[type=\"email\"]')).toBeVisible({ timeout: 30000 })": "credential-dependent login was replaced by mockAuth(page), so the guarded Admin UI suite always runs",
    "e2e/data-collection-history.spec.ts:await expect(page.locator('input[type=\"email\"]')).toBeVisible({ timeout: 30000 })": "credential-dependent login was replaced by mockAuth(page), so the guarded Admin UI suite always runs",
    "e2e/data-collection-p0-repair-plan.spec.ts:await expect(page.locator('input[type=\"email\"]')).toBeVisible({ timeout: 30000 })": "credential-dependent login was replaced by mockAuth(page), so retained maintenance coverage always runs",
    "e2e/data-collection-refresh-plan.spec.ts:await expect(page.locator('input[type=\"email\"]')).toBeVisible({ timeout: 30000 })": "credential-dependent login was replaced by mockAuth(page), so retained maintenance coverage always runs",
    # The normal Data Collection surface was deliberately reduced to its safe,
    # essential workflow. Each removed assertion has an explicit replacement.
    "e2e/data-collection-dry-run.spec.ts:await expect(page.locator('input[type=\"checkbox\"]').first()).toBeDisabled()": "repair input removal is covered by exact absence and force_rescrape=false request assertions",
    "e2e/data-collection-dry-run.spec.ts:await expect(page.getByText('Dry-run 結果（実取得なし）')).toBeVisible()": "the same heading is asserted inside the dedicated dry-run-result region",
    "e2e/data-collection-dry-run.spec.ts:await expect(page.getByText('取得対象')).toBeVisible()": "verbose target details were replaced by four scoped compact summary assertions",
    "e2e/data-collection-dry-run.spec.ts:await expect(page.getByText('新規取得が必要')).toBeVisible()": "replaced by the compact 新規取得 card with an exact value assertion",
    "e2e/data-collection-dry-run.spec.ts:await expect(page.getByText('既存DBでカバー済み')).toBeVisible()": "replaced by the compact 既存データ card with an exact aggregate assertion",
    "e2e/data-collection-dry-run.spec.ts:await expect(page.getByText('HTTPキャッシュ / resume でスキップ')).toBeVisible()": "cache and resume internals were collapsed into the asserted covered-data aggregate",
    "e2e/data-collection-dry-run.spec.ts:await expect(page.getByText('推定HTTPリクエスト')).toBeVisible()": "replaced by the compact HTTP予定 card with an exact value assertion",
    "e2e/data-collection-dry-run.spec.ts:await expect(page.getByText('推定実行時間')).toBeVisible()": "replaced by the compact 推定時間 card with an exact seconds assertion",
    "e2e/data-collection-dry-run.spec.ts:await expect(estReqCard).toBeVisible()": "broad div search was replaced by a scoped HTTP予定 assertion",
    "e2e/data-collection-dry-run.spec.ts:await expect(estReqCard).toContainText('8')": "replaced by the scoped HTTP予定 exact-value assertion",
    "e2e/data-collection-dry-run.spec.ts:await expect(page.locator('div').filter({ hasText: 'estimated request count: 8' }).first()).toBeVisible()": "raw label was replaced by the localized HTTP予定 value assertion",
    "e2e/data-collection-dry-run.spec.ts:await expect(page.locator('div').filter({ hasText: 'DB existing skip count: 14' }).first()).toBeVisible()": "raw DB counter was replaced by the compact covered-data aggregate assertion",
    "e2e/data-collection-dry-run.spec.ts:await expect(page.locator('div').filter({ hasText: 'new fetch required count: 8' }).first()).toBeVisible()": "raw counter was replaced by the compact 新規取得 value assertion",
    "e2e/data-collection-dry-run.spec.ts:await expect(page.locator('div').filter({ hasText: 'already covered count: 26' }).first()).toBeVisible()": "raw counter was replaced by the compact 既存データ value assertion",
    "e2e/data-collection-dry-run.spec.ts:await expect(page.getByText('cache hit はHTTPキャッシュで再取得不要と判定された件数です。')).toBeVisible()": "technical cache prose was removed while the covered-data aggregate remains asserted",
    "e2e/data-collection-dry-run.spec.ts:await expect(page.getByText('resume hit は過去に成功済みのURLとして再実行をスキップできる件数です。')).toBeVisible()": "technical resume prose was removed while the covered-data aggregate remains asserted",
    "e2e/data-collection-dry-run.spec.ts:await expect(page.getByText('rate limit policy')).toBeVisible()": "advanced prose is explicitly absent while dry_run=true and force_rescrape=false are asserted",
    "e2e/data-collection-dry-run.spec.ts:await expect(": "multiline lookup was replaced by dry-run-error scoping with the same backend error text",
    "e2e/data-collection-history.spec.ts:test('履歴セクションと更新ボタン、dry-run/execute項目が表示される', async ({ page }) => {": "replaced by a two-record test proving only the newest compact result is rendered",
    "e2e/data-collection-history.spec.ts:await expect(page.getByText('fetch summary 履歴')).toBeVisible()": "replaced by latest-result visibility and explicit obsolete-history absence assertions",
    "e2e/data-collection-history.spec.ts:await expect(page.getByRole('button', { name: '更新' })).toBeVisible()": "refresh remains asserted inside the latest-fetch-summary region",
    "e2e/data-collection-history.spec.ts:await expect(page.getByText('hist-job-dry-001')).toBeVisible()": "older rows and job IDs were removed; an older sentinel value is asserted absent",
    "e2e/data-collection-history.spec.ts:await expect(page.getByText('hist-job-exec-001')).toBeVisible()": "latest execution identity is now asserted through its exact business summary values",
    "e2e/data-collection-history.spec.ts:await expect(page.getByText('dry-run', { exact: true })).toBeVisible()": "the older dry-run record is intentionally excluded and its sentinel is asserted absent",
    "e2e/data-collection-history.spec.ts:await expect(page.getByText('execute', { exact: true })).toBeVisible()": "raw mode text was replaced by the localized 取得 badge assertion",
    "e2e/data-collection-history.spec.ts:await expect(page.getByText('est req:')).toBeVisible()": "older dry-run details are excluded and the older-record sentinel is asserted absent",
    "e2e/data-collection-history.spec.ts:await expect(page.getByText('cache hit:')).toBeVisible()": "cache internals were removed from the compact latest-only contract",
    "e2e/data-collection-history.spec.ts:await expect(page.getByText('saved races:')).toBeVisible()": "replaced by the localized 保存レース exact summary assertion",
    "e2e/data-collection-history.spec.ts:await expect(page.getByText('network req:')).toBeVisible()": "network internals were replaced by exact latest business summary assertions",
    "e2e/data-collection-history.spec.ts:await expect(page.getByText('retries:')).toBeVisible()": "retry internals were replaced by exact latest business summary assertions",
    "e2e/data-collection-history.spec.ts:test('履歴が空のとき空状態メッセージが表示される', async ({ page }) => {": "replaced by a test proving no obsolete empty card while core statistics remain visible",
    "e2e/data-collection-history.spec.ts:await expect(page.getByText('履歴がありません（Dry-run または 取得実行後に表示されます）')).toBeVisible()": "replaced by exact absence of the obsolete empty-history panel plus core-stat visibility",
    "e2e/data-collection-p0-repair-plan.spec.ts:test('Data Collection から遷移し read-only p0 plan を表示できる', async ({ page }) => {": "renamed to verify the retained maintenance page directly without a normal-screen link",
    "e2e/data-collection-p0-repair-plan.spec.ts:await expect(p0PlanLink).toBeVisible()": "normal discoverability was replaced by direct maintenance URL and read-only assertions",
    "e2e/data-collection-p0-repair-plan.spec.ts:await expect(p0PlanLink).toHaveAttribute('href', '/data-collection/p0-repair-plan')": "replaced by direct navigation and an exact maintenance page URL assertion",
    "e2e/data-collection-phase3b.spec.ts:await expect(page.getByTestId('quality-bridge-card')).toBeVisible()": "obsolete bridge is now absent while zero-race completion remains explicitly asserted",
    "e2e/data-collection-phase3b.spec.ts:test('queued/running/multi-month-running中はcompleted/quality bridgeを表示しない', async ({ page, baseURL }) => {": "renamed while preserving queued, running, and premature-completion assertions",
    "e2e/data-collection-phase3b.spec.ts:test('実行中はフォーム入力と実行系ボタンをlockし、completed前にbridgeを表示しない', async ({ page, baseURL }) => {": "renamed while preserving input and execution-button locking assertions",
    "e2e/data-collection-phase3b.spec.ts:await expect(page.getByTestId('force-rescrape-input')).toBeDisabled()": "replaced by exact repair-input absence and a force_rescrape=false POST assertion",
    "e2e/data-collection-phase3b.spec.ts:test('quality bridge本文とリンク先を維持', async ({ page, baseURL }) => {": "replaced by successful completion, safe request body, and all maintenance-link absence checks",
    "e2e/data-collection-phase3b.spec.ts:await expect(bridge).toBeVisible()": "replaced by successful completion plus exact quality-bridge absence",
    "e2e/data-collection-phase3b.spec.ts:await expect(bridge).toContainText('取得は完了しましたが、品質確認は未実施です')": "obsolete bridge prose was replaced by completion and maintenance-link absence checks",
    "e2e/data-collection-phase3b.spec.ts:await expect(page.getByTestId('quality-bridge-refresh-link')).toHaveAttribute('href', '/data-collection/refresh-plan')": "link absence is asserted on the normal page while the maintenance page has direct E2E coverage",
    "e2e/data-collection-phase3b.spec.ts:await expect(page.getByTestId('quality-bridge-p0-link')).toHaveAttribute('href', '/data-collection/p0-repair-plan')": "link absence is asserted on the normal page while the maintenance page has direct E2E coverage",
    "e2e/data-collection-phase3c.spec.ts:await expect(link).toBeVisible()": "normal visibility was replaced by direct targeted-refetch navigation and a main-page absence test",
    "e2e/data-collection-phase3c.spec.ts:await expect(link).toHaveAttribute('href', '/data-collection/targeted-refetch-plan')": "replaced by direct navigation and the retained exact maintenance page URL",
    "e2e/data-collection-phase3d.spec.ts:test('navigation is explicit and no request runs before confirmation', async ({ page, baseURL }) => {": "renamed for direct maintenance navigation while preserving disabled and zero-request assertions",
    "e2e/data-collection-phase3d.spec.ts:await expect(link).toHaveAttribute('href', '/data-collection/live-validation')": "replaced by direct navigation and explicit no-request-before-confirmation assertions",
    "e2e/data-collection-refresh-plan.spec.ts:test('Data Collection から遷移し dry-run plan を表示できる', async ({ page }) => {": "renamed to verify the retained maintenance page directly without a normal-screen link",
    "e2e/data-collection-refresh-plan.spec.ts:await expect(refreshPlanLink).toBeVisible()": "normal discoverability was replaced by direct maintenance URL and dry-run assertions",
    "e2e/data-collection-refresh-plan.spec.ts:await expect(refreshPlanLink).toHaveAttribute('href', '/data-collection/refresh-plan')": "replaced by direct navigation and an exact maintenance page URL assertion",
    "e2e/data-collection.spec.ts:test('90日超えの期間では警告が表示される', async ({ page }) => {": "replaced by exact range retention, monthly splitting guidance, and enabled action assertions",
    "e2e/data-collection.spec.ts:await expect(page.getByText(/IPブロック/)).toBeVisible()": "obsolete wording was replaced by actionable monthly-splitting and readiness assertions",
    "e2e/data-collection.spec.ts:test('モデル学習へのリンクが表示される', async ({ page }) => {": "replaced by an essential-screen test proving every nonessential control is absent",
    "e2e/data-collection.spec.ts:await expect(page.getByRole('link', { name: 'モデル学習へ' })).toBeVisible()": "replaced by exact absence of the model-training and other nonessential links",
    "e2e/real-workflow.spec.ts:test('Step2: データ取得 2015-01〜2016-03 強制再取得', async ({ page }) => {": "renamed to incremental collection and strengthened with force_rescrape=false request verification",
    "e2e/real-workflow.spec.ts:await expect(page.locator('text=/ローカルAPI/')).toBeVisible({ timeout: 10_000 })": "replaced by the current backend API status badge assertion",
    "e2e/real-workflow.spec.ts:await expect(forceCheckbox).toBeChecked()": "replaced by repair-input absence and intercepted force_rescrape=false verification",
    "src/__tests__/profiling-report-viewer.test.tsx:test('data collection links to the authenticated viewer instead of the Bearer-less API URL', () => {": "replaced by a source contract proving profiling is absent from the essential screen",
    "src/__tests__/profiling-report-viewer.test.tsx:expect(source).toContain('href={`/data-collection/profiling/${profilingJobId}`}')": "replaced by exact absence of both profiling links; standalone viewer security tests remain",
    "src/__tests__/profiling-report-viewer.test.tsx:expect(source).toContain(\"if (!isAdmin)\")": "removed widget no longer needs a local branch; profiling calls and UI text are asserted absent",
    "src/__tests__/profiling-report-viewer.test.tsx:expect(source).toContain(\"disabled={!isAdmin || profilingStatus === 'running'}\")": "removed widget no longer has a disabled-state contract; calls, links, and text are asserted absent",
    # Public and Admin navigation were consolidated into a two-step user Home
    # and a password-unlocked Admin workspace without weakening coverage.
    "e2e/dashboard.spec.ts:await expect(page.getByRole('heading', { name: /購入履歴/ })).toBeVisible({ timeout: 5000 })": "pending-result heading and table visibility now verify the regrouped purchase history",
    "e2e/dashboard.spec.ts:await expect(page.getByRole('button', { name: '結果入力' }).first()).toBeVisible({ timeout: 5000 })": "renamed result-entry action remains asserted by accessible label",
    "e2e/dashboard.spec.ts:await expect(page.getByPlaceholder('0')).toBeVisible({ timeout: 3000 })": "hit/miss selection is asserted before the conditional payout field",
    "e2e/home.spec.ts:test('「アプリへ」でホームページへ遷移する', async ({ page }) => {": "landing CTA was renamed and its /home destination remains covered",
    "e2e/home.spec.ts:test('ホームページに4ステップのナビゲーションカードが表示される', async ({ page }) => {": "replaced by exact two-step public navigation and privileged-link absence",
    "e2e/home.spec.ts:await expect(page.getByText('データ取得')).toBeVisible()": "collection moved from public Home into the password-unlocked Admin workspace",
    "e2e/home.spec.ts:await expect(page.getByText('モデル学習')).toBeVisible()": "model management moved from public Home into the password-unlocked Admin workspace",
    "e2e/home.spec.ts:await expect(page.getByText('予測実行')).toBeVisible()": "prediction remains covered through its exact /predict-batch link",
    "e2e/home.spec.ts:await expect(page.getByText('成績確認')).toBeVisible()": "performance remains covered through its exact /dashboard link",
    "e2e/home.spec.ts:await expect(page.getByText('予測スコア詳細')).toBeVisible()": "advanced score analysis moved to the premium prediction-result flow",
    "e2e/home.spec.ts:test('Step 01から始めるボタンでデータ取得ページへ遷移', async ({ page }) => {": "public CTA now starts prediction while collection requires Admin mode",
    "e2e/phase2-authz.spec.ts:test('Admin ユーザーは管理画面を利用できる', async ({ page, baseURL }) => {": "replaced by legacy redirect, locked workspace, and Admin API bearer checks",
    "e2e/phase2-authz.spec.ts:await expect(page).toHaveURL(/\\/admin$/)": "legacy /admin intentionally redirects to the integrated /home",
    "e2e/phase2-authz.spec.ts:await expect(page.getByText('Admin 専用')).toBeVisible()": "legacy badge was replaced by the locked Admin-mode control and hidden workspace assertion",
    "e2e/predict-batch.spec.ts:test('レース一覧取得 → 全選択 → 予測実行で結果が表示される', async ({ page }) => {": "renamed test adds exact score-column and premium detail-link assertions",
    "e2e/workflow.spec.ts:test('1-4: 次のステップ（モデル学習）へのリンクが表示される', async ({ page }) => {": "collection no longer links to model management and the absence is asserted",
    "e2e/workflow.spec.ts:await expect(page.getByRole('link', { name: /モデル学習/ })).toBeVisible()": "replaced by an explicit no-model-learning-link assertion",
    "e2e/workflow.spec.ts:test('4-6: データ取得ページへのリンクが表示される（次サイクルへ）', async ({ page }) => {": "dashboard now links to history and the next prediction rather than privileged collection",
    "e2e/workflow.spec.ts:await expect(page.getByRole('link', { name: /データ取得/ })).toBeVisible({ timeout: 5000 })": "privileged link removal is covered by exact replacement dashboard links",
    "e2e/workflow.spec.ts:test('ホーム → データ取得 → 学習 → 予測 → 成績 まで全ページ正常遷移', async ({ page }) => {": "replaced by public prediction/performance flow plus separate Admin guard coverage",
    "e2e/workflow.spec.ts:await expect(page.getByText('データ取得')).toBeVisible()": "public Home now asserts privileged operational links are absent",
    "e2e/workflow.spec.ts:await expect(page.getByText('データ取得', { exact: true })).toBeVisible()": "collection rendering remains covered by its dedicated page and route-guard tests",
    "e2e/workflow.spec.ts:await expect(page.getByText('モデル学習')).toBeVisible()": "training rendering remains covered by dedicated tests and the Admin route guard",
    "e2e/workflow.spec.ts:await expect(page.getByText('予測結果確認')).toBeVisible()": "race-analysis rendering remains covered by result-detail tests",
    "e2e/workflow.spec.ts:test('ホームのStepカードから各ページへ正しくリンクされる', async ({ page }) => {": "replaced by exact two-link public navigation plus privileged-link absence",
    "e2e/workflow.spec.ts:await expect(page.getByRole('link', { name: 'データ取得' }).first()).toHaveAttribute('href', '/data-collection')": "collection moved to Admin workspace and its public absence is asserted",
    "e2e/workflow.spec.ts:await expect(page.getByRole('link', { name: 'モデル学習' }).first()).toHaveAttribute('href', '/train')": "model management moved to Admin workspace and its public absence is asserted",
    "e2e/workflow.spec.ts:await expect(page.getByRole('link', { name: '予測実行' }).first()).toHaveAttribute('href', '/predict-batch')": "the exact prediction destination remains asserted with its updated accessible name",
    "e2e/workflow.spec.ts:await expect(page.getByRole('link', { name: '成績確認' }).first()).toHaveAttribute('href', '/dashboard')": "the exact dashboard destination remains asserted with its updated accessible name",
    # Security and authorization assertions became narrower or stronger.
    "python-api/tests/test_live_validation.py:assert \"./reports:/app/keiba/data/live-validation-inputs:ro\" in compose": "broad reports mount was replaced by a dedicated read-only input directory",
    "python-api/tests/test_phase3m_admin_atomic_role.py:assert \"verifyRequestAuth(request, { requireAdmin: true })\" in route": "replaced by the stronger requireAdminMode guard assertion",
    "src/__tests__/admin-page-security.test.tsx:test('shows bounded server detail and does not render forged profile data', async () => {": "replaced by immediate lock-on-403 while forged profile data remains absent",
    "src/__tests__/admin-page-security.test.tsx:expect(await screen.findByRole('alert')).toHaveTextContent('Admin role required')": "authorization detail is no longer rendered; the workspace fails closed through its lock callback",
    "src/__tests__/admin-profiles-route.test.ts:expect(verifyRequestAuthMock).toHaveBeenCalledWith(expect.anything(), { requireAdmin: true })": "replaced by the stronger requireAdminMode call assertion",
    # The canonical Phase 3M chain gained one guarded forward migration; the
    # replacement assertion remains exact and the manifest-order gate is unchanged.
    "python-api/tests/test_phase3m_supabase_bootstrap_gate.py:assert len(manifest.migrations) == 11": "replaced by exact 19-migration assertion after adding guarded model approval, job, worker-lease, artifact, evaluation, execution-bundle, orphan-reconciliation, and dispatch-queue contracts",
    "python-api/tests/test_phase3m_supabase_bootstrap_gate.py:assert len(manifest.migrations) == 19": "replaced by exact 21-migration assertion after append-only shared outbox and immutable observation/HA contracts",
    "python-api/tests/test_phase3m_supabase_bootstrap_gate.py:assert len(manifest.migrations) == 21": "replaced by exact 22-migration assertion after appending owner-scoped operational scrape cancellation",
    "python-api/tests/test_phase3m_supabase_bootstrap_gate.py:assert [entry.version for entry in manifest.migrations[-2:]] == [": "expanded to an exact three-entry suffix assertion that preserves ordinals 20-21 and appends ordinal 22",
    "python-api/tests/test_phase3m_supabase_upgrade_tool.py:assert len(candidate.migrations) == 19": "replaced by exact 21-migration assertion for the same append-only extension",
    "python-api/tests/test_phase3m_supabase_upgrade_tool.py:assert len(candidate.migrations) == 21": "replaced by exact 22-migration assertion for the append-only cancellation extension",
    "python-api/tests/test_phase3m_supabase_upgrade_tool.py:assert sql.count(\"-- phase3m append migration \") == 8": "replaced by an exact 10-appended-migration assertion after adding ordinals 20 and 21",
    "python-api/tests/test_phase3m_supabase_upgrade_tool.py:assert sql.count(\"-- phase3m append migration \") == 10": "replaced by an exact 11-appended-migration assertion after adding ordinal 22",
    "python-api/tests/test_phase3m_supabase_upgrade_tool.py:assert len(rows) == 19": "replaced by exact 21-row history assertion after append-only extension",
    "python-api/tests/test_phase3m_supabase_upgrade_tool.py:assert len(rows) == 21": "replaced by exact 22-row history assertion after append-only cancellation extension",
    "python-api/tests/test_phase3m_supabase_upgrade_tool.py:assert {row[-1] for row in rows[11:]} == {current_commit}": "replaced by exact old/current/candidate history segment assertions for all 21 ordinals",
    "python-api/tests/test_phase3n_observation_ha.py:assert len(manifest[\"migrations\"]) == 21": "replaced by exact 22-migration assertion while retaining the immutable observation/HA ordinal",
    "python-api/tests/test_phase3n_observation_ha.py:assert [entry[\"version\"] for entry in manifest[\"migrations\"][-2:]] == [": "expanded to an exact three-entry suffix assertion that retains the two Phase 3N migrations and appends cancellation",
    "python-api/tests/test_phase3j_saga_outbox_runtime_gate.py:assert len(jobs) == 11": "replaced by exact 12-job assertion after adding the release-blocking Phase3N HA contract job",
    "python-api/tests/test_phase3j_saga_outbox_runtime_gate.py:assert len(all_jobs) == 12": "replaced by exact 13-job assertion after adding the release-blocking Phase3N HA contract job",
    # The dependency gate moved from permissive ranges/no override to stricter
    # exact patched versions plus explicit resolution checks in the same test.
    "python-api/tests/test_phase3k_dependency_security_contract.py:assert package_json[\"dependencies\"][\"next\"] == \"^16.2.10\"": "replaced by exact Next.js 16.2.12 security pin assertion",
    "python-api/tests/test_phase3k_dependency_security_contract.py:assert package_json[\"devDependencies\"][\"postcss\"] == \"^8.5.10\"": "replaced by exact PostCSS 8.5.25 security pin assertion",
    "python-api/tests/test_phase3k_dependency_security_contract.py:assert \"overrides\" not in package_json": "replaced by exact safe PostCSS and sharp override assertion",
    "python-api/tests/test_phase3k_dependency_security_contract.py:assert package_json[\"dependencies\"][\"next\"] == \"16.2.12\"": "replaced by exact Next.js 16.3.4 security pin assertion after the newer advisory",
    "python-api/tests/test_phase3k_dependency_security_contract.py:assert package_json[\"dependencies\"][\"sharp\"] == \"0.35.3\"": "replaced by exact Sharp 0.35.4 security pin assertion after the newer advisory",
    # Phase 3N replaces the in-memory thread start contract with a durable,
    # fenced Saga/outbox contract. Equivalent and stronger assertions live in
    # test_phase3n_operational_saga_runtime.py and the rewritten Phase 3E suite.
    "python-api/tests/test_phase3e_scrape_job_security.py:def test_start_uses_full_uuid_binds_owner_and_persists_before_thread(": "replaced by durable Phase3N enqueue/owner binding tests",
    "python-api/tests/test_phase3e_scrape_job_security.py:assert events == [\"persist\", \"thread-created\", \"thread-started\"]": "legacy thread ordering replaced by durable enqueue-before-dispatch checks",
    "python-api/tests/test_phase3e_scrape_job_security.py:def test_start_fails_closed_before_thread_when_initial_persistence_fails(": "replaced by fail-closed durable store tests",
    "python-api/tests/test_phase3e_scrape_job_security.py:assert thread_calls == []": "legacy thread bypass replaced by worker/outbox non-dispatch assertions",
    "python-api/tests/test_phase3e_scrape_job_security.py:def test_start_rejects_another_active_job_for_the_same_owner_without_thread(": "replaced by one-active-owner durable store tests",
    "python-api/tests/test_phase3e_scrape_job_security.py:assert jobs._persist_job(JOB_A, _job(OWNER_A, status=\"running\")) is True": "legacy persistence setup removed with in-memory start path",
    "python-api/tests/test_phase3e_scrape_job_security.py:assert visible[\"status\"] == \"running\"": "status visibility now asserted through operational store responses",
    "python-api/tests/test_phase3e_scrape_job_security.py:assert jobs._persist_job(JOB_A, _job(OWNER_A)) is True": "legacy persistence setup replaced by operational store fixture",
    "python-api/tests/test_phase3e_scrape_job_security.py:assert jobs._persist_job(JOB_B, _job(OWNER_B)) is True": "legacy persistence setup replaced by operational store fixture",
    # Phase 3L previously asserted an unconditional release tombstone. It is
    # replaced by stricter immutable-producer, signed-attestation and exact-main
    # authorization assertions in the same test module.
    "python-api/tests/test_phase3l_deployment_safety.py:def test_ci_blocks_not_ready_pushes_to_main_and_release() -> None:": "superseded by exact trusted-attestation promotion contract",
    "python-api/tests/test_phase3l_deployment_safety.py:assert len(blocking_steps) == 1": "superseded by immutable producer resolver assertions",
    "python-api/tests/test_phase3l_deployment_safety.py:assert \"github.event_name == 'push'\" in condition": "superseded by promotion event and candidate binding assertions",
    "python-api/tests/test_phase3l_deployment_safety.py:assert \"github.ref_name == 'main'\" in condition": "superseded by exact main merge correlation assertions",
    "python-api/tests/test_phase3l_deployment_safety.py:assert \"github.ref_name == 'release'\" in condition": "release remains fail-closed in the new resolver assertions",
    "python-api/tests/test_phase3l_deployment_safety.py:assert \"--require-ready\" in blocking[\"run\"]": "require-ready retained in trusted promotion assertions",
    "python-api/tests/test_phase3l_deployment_safety.py:def test_release_workflow_cannot_deploy_or_publish_a_release() -> None:": "superseded by exact attested main authorization and no-provider-command test",
    "python-api/tests/test_phase3l_deployment_safety.py:assert workflow[\"permissions\"] == {\"contents\": \"read\"}": "permissions assertion strengthened to include minimal actions read",
    "python-api/tests/test_phase3l_deployment_safety.py:assert set(jobs) == {\"production-release-blocked\"}": "tombstone replaced by single environment-gated authorization job",
    "python-api/tests/test_phase3l_deployment_safety.py:assert len(steps) == 1": "replacement authorization workflow has multiple independently asserted gates",
    "python-api/tests/test_phase3l_deployment_safety.py:assert \"exit 1\" in steps[0][\"run\"]": "unconditional tombstone replaced by multiple fail-closed exact-context gates",
    "python-api/tests/test_phase3l_deployment_safety.py:assert set(inputs) == {\"expected_commit\", \"trusted_producer_sha\", \"max_age_seconds\"}": "expanded by an exact evidence_scope input assertion whose only values preserve full validation or add the stricter automatic-betting-disabled limited-observation path",
    # The immutable v1 producer cannot be updated. Its exact assertions are
    # replaced by the same fail-closed assertions for the separately protected
    # v2 producer; no producer-ref check is removed or relaxed.
    "python-api/tests/test_phase3l_deployment_safety.py:assert '.head_branch == \"security/phase3n-trusted-producer-v1\"' in resolver[\"run\"]": "rotated to the exact immutable v2 producer assertion",
    "python-api/tests/test_phase3n_staging_evidence_gate.py:assert \"refs/heads/security/phase3n-trusted-producer-v1\" in workflow": "rotated to the exact immutable v2 producer assertion",
    # v2 remains immutable at its previously reviewed merge SHA. This change
    # rotates every exact producer assertion to the separately protected v3
    # branch that will be created only after this candidate reaches develop.
    "python-api/tests/test_phase3l_deployment_safety.py:trusted_ref = \"refs/heads/security/phase3n-trusted-producer-v2\"": "rotated to the exact immutable v3 producer assertion",
    "python-api/tests/test_phase3l_deployment_safety.py:assert '.head_branch == \"security/phase3n-trusted-producer-v2\"' in resolver[\"run\"]": "rotated to the exact immutable v3 producer assertion",
    "python-api/tests/test_phase3l_deployment_safety.py:'.head_branch == \"security/phase3n-trusted-producer-v2\"',": "rotated to the exact immutable v3 producer assertion",
    "python-api/tests/test_phase3n_staging_evidence_gate.py:assert \"refs/heads/security/phase3n-trusted-producer-v2\" in workflow": "rotated to the exact immutable v3 producer assertion",
    # v3 is locked by a no-bypass update ruleset. The replacement assertions
    # bind the same fail-closed producer checks to the separately reviewed v4.
    "python-api/tests/test_phase3l_deployment_safety.py:assert '.head_branch == \"security/phase3n-trusted-producer-v3\"' in resolver[\"run\"]": "rotated to the exact protected v4 producer assertion without weakening the immutable v3 branch",
    "python-api/tests/test_phase3n_staging_evidence_gate.py:assert \"refs/heads/security/phase3n-trusted-producer-v3\" in workflow": "rotated to the exact protected v4 producer assertion without weakening the immutable v3 branch",
    # Intentional removals in the local-only workflow, five-function UI, and
    # inline prediction-explanation redesign. Each removed contract is covered
    # by replacement behavior tests in the same change set.
    "e2e/data-collection-dry-run.spec.ts:await expect(page.getByRole('button', { name: 'Dry-run' })).toBeVisible()": "replaced by compact collection UI coverage and current job-safety assertions",
    "e2e/data-collection-dry-run.spec.ts:await expect(page.getByRole('button', { name: '取得開始' })).toBeDisabled()": "replaced by compact collection UI coverage and current job-safety assertions",
    "e2e/data-collection-dry-run.spec.ts:await expect(page.getByText('Dry-run は HTTPアクセスを実行しません')).toBeVisible()": "replaced by compact collection UI coverage and current job-safety assertions",
    "e2e/data-collection-dry-run.spec.ts:await expect(page.getByText('Dry-run 実行中')).toBeVisible()": "replaced by compact collection UI coverage and current job-safety assertions",
    "e2e/data-collection-dry-run.spec.ts:await expect(page.getByText('Dry-run 結果（実取得なし）')).not.toBeVisible()": "replaced by compact collection UI coverage and current job-safety assertions",
    "e2e/data-collection-dry-run.spec.ts:await expect(page.getByText('Dry-run 結果（実取得なし）')).not.toBeVisible()": "replaced by compact collection UI coverage and current job-safety assertions",
    "e2e/data-collection-dry-run.spec.ts:await expect(page.getByText('Dry-run未実行です。本実行は可能ですが、推定アクセス数の確認を推奨します。')).toBeVisible()": "replaced by compact collection UI coverage and current job-safety assertions",
    "e2e/data-collection-dry-run.spec.ts:await expect(page.getByText('HTTPアクセスは実行していません')).toBeVisible()": "replaced by compact collection UI coverage and current job-safety assertions",
    "e2e/data-collection-dry-run.spec.ts:await expect(page.getByText('見積もり生成中')).toBeVisible()": "replaced by compact collection UI coverage and current job-safety assertions",
    "e2e/data-collection-dry-run.spec.ts:await expect(page.getByText(/経過秒:\\s*\\d+\\s*sec/)).toBeVisible()": "replaced by compact collection UI coverage and current job-safety assertions",
    "e2e/data-collection-dry-run.spec.ts:test('Dry-run未実行で本実行するとwarnが表示される', async ({ page }) => {": "replaced by compact collection UI coverage and current job-safety assertions",
    "e2e/data-collection-phase3b.spec.ts:await expect(page.getByRole('alert').filter({ hasText: 'Dry-run失敗:' }).first()).toBeVisible()": "replaced by compact collection UI coverage and current job-safety assertions",
    "e2e/data-collection-phase3b.spec.ts:await expect(page.getByText('Dry-run 結果（実取得なし）')).toHaveCount(0)": "replaced by compact collection UI coverage and current job-safety assertions",
    "e2e/data-collection-phase3b.spec.ts:await expect(page.getByTestId('batch-status-panel')).not.toContainText('取得完了: 3レース')": "replaced by compact collection UI coverage and current job-safety assertions",
    "e2e/data-collection-phase3b.spec.ts:await expect(page.getByText('Dry-run 結果（実取得なし）')).toHaveCount(0)": "replaced by compact collection UI coverage and current job-safety assertions",
    "e2e/data-collection-phase3b.spec.ts:await expect(panel).toContainText('取得完了: 0レース（0レース・正常完了）')": "replaced by compact collection UI coverage and current job-safety assertions",
    "e2e/data-collection.spec.ts:await expect(btn).toBeVisible({ timeout: 5000 })": "replaced by compact collection UI coverage and current job-safety assertions",
    "e2e/data-collection.spec.ts:await expect(page.getByText('データ取得', { exact: true })).toBeVisible()": "replaced by compact collection UI coverage and current job-safety assertions",
    "e2e/data-collection.spec.ts:await expect(page.getByText('期間指定一括取得')).toBeVisible()": "replaced by compact collection UI coverage and current job-safety assertions",
    "e2e/data-collection.spec.ts:await expect(page.getByText('終了年月')).toBeVisible()": "replaced by compact collection UI coverage and current job-safety assertions",
    "e2e/data-collection.spec.ts:await expect(page.getByText('開始年月')).toBeVisible()": "replaced by compact collection UI coverage and current job-safety assertions",
    "e2e/data-collection.spec.ts:test('開始年月・終了年月のラベルが表示されている', async ({ page }) => {": "replaced by compact collection UI coverage and current job-safety assertions",
    "e2e/home.spec.ts:await expect(modelCard.locator('..').getByText('3')).toBeVisible({ timeout: 6000 })": "replaced by the unified essential-navigation and current route-guard coverage",
    "e2e/home.spec.ts:await expect(page.getByText('12,345')).toBeVisible({ timeout: 6000 })": "replaced by the unified essential-navigation and current route-guard coverage",
    "e2e/home.spec.ts:await expect(page.getByText('API')).toBeVisible()": "replaced by the unified essential-navigation and current route-guard coverage",
    "e2e/home.spec.ts:await expect(page.getByText('オフライン')).toBeVisible({ timeout: 6000 })": "replaced by the unified essential-navigation and current route-guard coverage",
    "e2e/home.spec.ts:await expect(page.getByText('オンライン')).toBeVisible({ timeout: 6000 })": "replaced by the unified essential-navigation and current route-guard coverage",
    "e2e/home.spec.ts:await expect(page.getByText('モデル数')).toBeVisible()": "replaced by the unified essential-navigation and current route-guard coverage",
    "e2e/home.spec.ts:await expect(page.getByText('レース数')).toBeVisible()": "replaced by the unified essential-navigation and current route-guard coverage",
    "e2e/home.spec.ts:test('APIオフライン時は赤色のステータスが表示される', async ({ page }) => {": "replaced by the unified essential-navigation and current route-guard coverage",
    "e2e/home.spec.ts:test('APIオンライン時は緑色のステータスが表示される', async ({ page }) => {": "replaced by the unified essential-navigation and current route-guard coverage",
    "e2e/home.spec.ts:test('システムステータスカードが3つ表示される', async ({ page }) => {": "replaced by the unified essential-navigation and current route-guard coverage",
    "e2e/home.spec.ts:test('データ統計が表示される', async ({ page }) => {": "replaced by the unified essential-navigation and current route-guard coverage",
    "e2e/predict-batch.spec.ts:await expect(page.getByText(/ダッシュボード/)).toBeVisible({ timeout: 5000 })": "replaced by inline explainability coverage using the cached prediction result",
    "e2e/predict-batch.spec.ts:test('購入後にダッシュボードへのリンクが表示される', async ({ page }) => {": "replaced by inline explainability coverage using the cached prediction result",
    "e2e/race-analysis.spec.ts:await expect(featTab).toBeVisible({ timeout: 5000 })": "replaced by inline explainability coverage using the cached prediction result",
    "e2e/race-analysis.spec.ts:await expect(page.getByRole('button', { name: /odds/ })).toBeVisible({ timeout: 5000 })": "replaced by inline explainability coverage using the cached prediction result",
    "e2e/race-analysis.spec.ts:await expect(page.getByRole('button', { name: /past/ })).toBeVisible({ timeout: 5000 })": "replaced by inline explainability coverage using the cached prediction result",
    "e2e/race-analysis.spec.ts:await expect(page.getByRole('columnheader', { name: 'odds_win' })).not.toBeVisible()": "replaced by inline explainability coverage using the cached prediction result",
    "e2e/race-analysis.spec.ts:await expect(page.getByRole('columnheader', { name: 'odds_win' })).toBeVisible({ timeout: 5000 })": "replaced by inline explainability coverage using the cached prediction result",
    "e2e/race-analysis.spec.ts:await expect(page.getByText(/列を表示/)).toBeVisible({ timeout: 3000 })": "replaced by inline explainability coverage using the cached prediction result",
    "e2e/race-analysis.spec.ts:test('グループチップをクリックすると列が非表示になる', async ({ page }) => {": "replaced by inline explainability coverage using the cached prediction result",
    "e2e/race-analysis.spec.ts:test('列名フィルターで絞り込みができる', async ({ page }) => {": "replaced by inline explainability coverage using the cached prediction result",
    "e2e/race-analysis.spec.ts:test('特徴量タブにグループチップが表示される', async ({ page }) => {": "replaced by inline explainability coverage using the cached prediction result",
    "e2e/race-analysis.spec.ts:test('特徴量タブに切り替えられる', async ({ page }) => {": "replaced by inline explainability coverage using the cached prediction result",
    "e2e/real-workflow.spec.ts:await expect(page.getByText('期間指定一括取得')).toBeVisible({ timeout: 10_000 })": "replaced by compact collection UI coverage and current job-safety assertions",
    "e2e/train.spec.ts:await expect(page.getByRole('button', { name: '削除' }).nth(1)).toBeVisible({ timeout: 1000 })": "replaced by local-admin durable training and capability coverage",
    "e2e/train.spec.ts:await expect(page.getByRole('button', { name: '学習開始' })).toBeVisible()": "replaced by local-admin durable training and capability coverage",
    "e2e/train.spec.ts:await expect(page.getByRole('heading', { name: 'モデルを削除' })).toBeVisible({ timeout: 3000 })": "replaced by local-admin durable training and capability coverage",
    "e2e/train.spec.ts:await expect(page.getByText('win | lightgbm')).toBeVisible({ timeout: 5000 })": "replaced by local-admin durable training and capability coverage",
    "e2e/train.spec.ts:await expect(page.getByText('モデル学習')).toBeVisible()": "replaced by local-admin durable training and capability coverage",
    "e2e/train.spec.ts:test('「学習開始」ボタンが存在する', async ({ page }) => {": "replaced by local-admin durable training and capability coverage",
    "e2e/train.spec.ts:test('モデルタイプをセレクトで変更できる', async ({ page }) => {": "replaced by local-admin durable training and capability coverage",
    "e2e/train.spec.ts:test('モデル削除ボタンをクリックすると確認ダイアログが出る', async ({ page }) => {": "replaced by local-admin durable training and capability coverage",
    "e2e/workflow.spec.ts:await expect(featTab).toBeVisible({ timeout: 5000 })": "replaced by the unified essential-navigation and current route-guard coverage",
    "e2e/workflow.spec.ts:await expect(oddsChip).toBeVisible({ timeout: 5000 })": "replaced by the unified essential-navigation and current route-guard coverage",
    "e2e/workflow.spec.ts:await expect(page.getByRole('columnheader', { name: 'odds_win' })).not.toBeVisible()": "replaced by the unified essential-navigation and current route-guard coverage",
    "e2e/workflow.spec.ts:await expect(page.getByRole('columnheader', { name: 'odds_win' })).toBeVisible({ timeout: 5000 })": "replaced by the unified essential-navigation and current route-guard coverage",
    "e2e/workflow.spec.ts:await expect(page.getByText('テスト馬A', { exact: false }).first()).toBeVisible({ timeout: 5000 })": "replaced by the unified essential-navigation and current route-guard coverage",
    "e2e/workflow.spec.ts:await expect(page.getByText('期間指定一括取得')).toBeVisible()": "replaced by the unified essential-navigation and current route-guard coverage",
    "e2e/workflow.spec.ts:await expect(page.getByText(/起動中|オンライン/)).toBeVisible({ timeout: 5000 })": "replaced by the unified essential-navigation and current route-guard coverage",
    "e2e/workflow.spec.ts:test('5-5: 特徴量タブで入力特徴量データが確認できる', async ({ page }) => {": "replaced by the unified essential-navigation and current route-guard coverage",
    "e2e/workflow.spec.ts:test('5-6: 特徴量グループチップで列フィルタリングができる', async ({ page }) => {": "replaced by the unified essential-navigation and current route-guard coverage",
    "python-api/tests/test_approved_training_execution.py:assert 'approved_execution is None\\n            and request.target not in (\"speed_deviation\", \"rank\")' in source": "replaced by stronger durable local-execution and authorization assertions",
    "python-api/tests/test_approved_training_execution.py:assert 'bundle[\"approved_execution\"]' in source": "replaced by stronger durable local-execution and authorization assertions",
    "python-api/tests/test_model_training_guard.py:assert train._train_jobs == before": "replaced by stronger durable local-execution and authorization assertions",
    "python-api/tests/test_phase2_authz.py:assert after == before": "replaced by stronger durable local-execution and authorization assertions",
    "src/__tests__/admin-page-security.test.tsx:expect(authFetchMock).toHaveBeenCalledWith(`/api/admin/profiles/${TARGET_ID}/role`, {": "replaced by integrated local-admin action guards and fail-closed coverage",
    "src/__tests__/admin-page-security.test.tsx:expect(authFetchMock).toHaveBeenCalledWith('/api/admin/profiles', {": "replaced by integrated local-admin action guards and fail-closed coverage",
    "src/__tests__/admin-page-security.test.tsx:test('contains no browser-side profiles select/update or service-role access', () => {": "replaced by integrated local-admin action guards and fail-closed coverage",
    "src/__tests__/admin-page-security.test.tsx:test('loads and updates profiles only through authFetch Admin routes', async () => {": "replaced by integrated local-admin action guards and fail-closed coverage",
    "src/__tests__/legacy-model-training-route-guard.test.ts:expect(body.code).toBe('approval-bound-model-training-required')": "replaced by integrated local-admin action guards and fail-closed coverage",
    "src/__tests__/legacy-model-training-route-guard.test.ts:expect(response.headers.get('Cache-Control')).toBe('no-store')": "replaced by integrated local-admin action guards and fail-closed coverage",
    "src/__tests__/legacy-model-training-route-guard.test.ts:expect(response.status).toBe(409)": "replaced by integrated local-admin action guards and fail-closed coverage",
    "src/__tests__/legacy-model-training-route-guard.test.ts:expect(source).toContain(\"'承認済みジョブ実行基盤を準備中'\")": "replaced by integrated local-admin action guards and fail-closed coverage",
    "src/__tests__/legacy-model-training-route-guard.test.ts:expect(source).toContain('title=\"モデル学習には永続的な承認と承認済みジョブ実行基盤が必要です\"')": "replaced by integrated local-admin action guards and fail-closed coverage",
    "src/__tests__/legacy-model-training-route-guard.test.ts:expect(source).toMatch(/<button\\s+onClick=\\{handleTrain\\}\\s+disabled/)": "replaced by integrated local-admin action guards and fail-closed coverage",
    "src/__tests__/legacy-model-training-route-guard.test.ts:it('does not forward local training without exact opt-in', async () => {": "replaced by integrated local-admin action guards and fail-closed coverage",
    "src/__tests__/legacy-model-training-route-guard.test.ts:it('keeps explicit local/test compatibility behind the FastAPI Premium boundary', async () => {": "replaced by integrated local-admin action guards and fail-closed coverage",
    "src/__tests__/legacy-model-training-route-guard.test.ts:it('keeps the normal training UI disabled until the durable runner exists', () => {": "replaced by integrated local-admin action guards and fail-closed coverage",
    "src/__tests__/model-activation-route-guard.test.ts:it('keeps explicit local/test compatibility behind the FastAPI Admin boundary', async () => {": "replaced by integrated local-admin action guards and fail-closed coverage",
    # Regression-model evaluation now uses regression/ranking terminology.
    # Every deleted assertion is replaced by an exact metric or SHAP wording check.
    "e2e/predict-batch.spec.ts:await expect(page.getByText('評価を上げた要素')).toBeVisible()": "replaced by the exact speed-score contribution heading assertion",
    "e2e/race-analysis.spec.ts:test('評価を上げた要素と下げた要素を分けて表示する', async ({ page }) => {": "renamed while preserving both positive and negative contribution assertions",
    "e2e/race-analysis.spec.ts:await expect(page.getByText('評価を上げた要素')).toBeVisible()": "replaced by the exact positive speed-score contribution assertion",
    "e2e/race-analysis.spec.ts:await expect(page.getByText('評価を下げた要素')).toBeVisible()": "replaced by the exact negative speed-score contribution assertion",
    "e2e/train.spec.ts:await expect(page.getByText('AUC 0.7234')).toBeVisible({ timeout: 5000 })": "replaced by exact regression rank-correlation display coverage",
    "e2e/train.spec.ts:await expect(page.getByText(/学習完了.*AUC/)).toBeVisible({ timeout: 15000 })": "replaced by the exact completed rank-correlation toast assertion",
    "e2e/train.spec.ts:await expect(page.getByText(/学習完了.*AUC/)).toBeVisible({ timeout: 10000 })": "replaced by completed rank-correlation assertions for reconnect paths",
    "e2e/workflow.spec.ts:test('2-2: 保存済みモデル一覧が表示される（AUC付き）', async ({ page }) => {": "renamed and strengthened to assert the five primary model-evaluation metrics",
    "e2e/workflow.spec.ts:await expect(page.getByText(/AUC.*0\\.7/)).toBeVisible({ timeout: 5000 })": "replaced by exact rank-correlation value and recovery-rate label assertions",
    "e2e/workflow.spec.ts:test('2-3: 学習実行 → プログレス → 完了とAUCが表示される', async ({ page }) => {": "renamed while preserving progress and completed-result assertions",
    "e2e/workflow.spec.ts:await expect(page.getByText('評価を上げた要素')).toBeVisible()": "replaced by the exact positive speed-score contribution assertion",
    "e2e/workflow.spec.ts:await expect(page.getByText('評価を上げた要素')).toBeVisible({ timeout: 5000 })": "replaced by the exact positive speed-score contribution assertion with the same timeout coverage",
    "src/__tests__/race-explanation-panel.test.tsx:expect(screen.getByText('評価を上げた要素')).toBeInTheDocument()": "replaced by an exact positive speed-score contribution assertion",
    "src/__tests__/race-explanation-panel.test.tsx:expect(screen.getByText('評価を下げた要素')).toBeInTheDocument()": "replaced by an exact negative speed-score contribution assertion",
}


def _run(cmd: List[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "command failed")
    return proc.stdout


def _git_has_changes() -> bool:
    return bool(_run(["git", "status", "--porcelain"]).strip())


def _git_status_porcelain_lines() -> List[str]:
    out = _run(["git", "status", "--porcelain"])
    return [line for line in out.splitlines() if line.strip()]


def _tracked_diff_text() -> str:
    return _run(["git", "diff", "--unified=0", BASE_REF, "--", "."])


def _changed_files_from_base() -> List[str]:
    out = _run(["git", "diff", "--name-only", BASE_REF, "--", "."])
    return [line.strip() for line in out.splitlines() if line.strip()]


def _deleted_files() -> List[str]:
    out = _run(["git", "diff", "--name-only", "--diff-filter=D", BASE_REF, "--", "."])
    return [line.strip() for line in out.splitlines() if line.strip()]


def _untracked_files() -> List[str]:
    out = _run(["git", "ls-files", "--others", "--exclude-standard"])
    return [line.strip() for line in out.splitlines() if line.strip()]


def _collect_diff_lines(diff_text: str) -> Tuple[List[Tuple[str, int, str]], List[Tuple[str, int, str]]]:
    added: List[Tuple[str, int, str]] = []
    deleted: List[Tuple[str, int, str]] = []
    current_file = ""
    current_new_line = 0
    current_old_line = 0
    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            current_file = ""
            current_new_line = 0
            current_old_line = 0
            continue
        if raw.startswith("+++ b/"):
            current_file = raw[6:]
            continue
        if raw.startswith("--- a/"):
            continue
        if raw.startswith("@@"):
            m_new = re.search(r"\+(\d+)", raw)
            m_old = re.search(r"-(\d+)", raw)
            current_new_line = int(m_new.group(1)) if m_new else 0
            current_old_line = int(m_old.group(1)) if m_old else 0
            continue
        if not current_file:
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            added.append((current_file, current_new_line, raw[1:]))
            current_new_line += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            deleted.append((current_file, current_old_line, raw[1:]))
            current_old_line += 1
        else:
            if not raw.startswith("\\"):
                current_new_line += 1
                current_old_line += 1
    return added, deleted


def _collect_untracked_lines(files: List[str]) -> List[Tuple[str, int, str]]:
    out: List[Tuple[str, int, str]] = []
    for file_path in files:
        path = Path(file_path)
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        for idx, line in enumerate(text.splitlines(), start=1):
            out.append((file_path, idx, line))
    return out


def _is_comment_or_doc(line: str) -> bool:
    s = line.strip()
    return (
        not s
        or s.startswith("//")
        or s.startswith("#")
        or s.startswith("*")
        or s.startswith("/*")
        or s.startswith("*/")
        or s.startswith("-")
    )


def _is_allowlisted(file_path: str, line: str) -> Tuple[bool, str]:
    key = f"{file_path}:{line.strip()}"
    reason = ALLOWLIST_EXACT.get(key)
    return (reason is not None, reason or "")


def _coverage_error(has_changes: bool, scanned_file_count: int, scanned_line_count: int) -> str | None:
    if has_changes and (scanned_file_count == 0 or scanned_line_count == 0):
        return "fail-closed: changes exist but scanner input coverage is zero"
    return None


def _normalize_status_path(raw_path: str) -> str:
    txt = raw_path.strip()
    if " -> " in txt:
        txt = txt.split(" -> ", 1)[1].strip()
    return txt


def _collect_test_scope_files(
    changed_from_base: List[str],
    status_lines: List[str],
    deleted_files: List[str],
    untracked_files: List[str],
) -> Set[str]:
    out: Set[str] = set()

    for path in changed_from_base:
        if TEST_CODE_PATH.search(path):
            out.add(path)

    for line in status_lines:
        if len(line) < 4:
            continue
        path = _normalize_status_path(line[3:])
        if path and TEST_CODE_PATH.search(path):
            out.add(path)

    for path in deleted_files:
        if TEST_CODE_PATH.search(path):
            out.add(path)

    for path in untracked_files:
        if TEST_CODE_PATH.search(path):
            out.add(path)

    return out


def _coverage_error_for_test_scope(has_test_scope_changes: bool, scanned_line_count: int) -> str | None:
    if has_test_scope_changes and scanned_line_count == 0:
        return "fail-closed: test scope changes exist but scanner input coverage is zero"
    return None


def main() -> int:
    has_changes = _git_has_changes()
    status_lines = _git_status_porcelain_lines()
    changed_from_base = _changed_files_from_base()
    tracked_diff = _tracked_diff_text()
    tracked_added, tracked_deleted = _collect_diff_lines(tracked_diff)
    untracked_files = _untracked_files()
    untracked_added = _collect_untracked_lines(untracked_files)
    deleted_files = _deleted_files()
    added = tracked_added + untracked_added
    test_scope_files = _collect_test_scope_files(
        changed_from_base,
        status_lines,
        deleted_files,
        untracked_files,
    )
    has_test_scope_changes = len(test_scope_files) > 0

    hits = []
    exclusions = []
    scanned_files: Set[str] = set()
    scanned_added_test_code_lines = 0
    scanned_deleted_test_code_lines = 0

    for file_path in deleted_files:
        if TEST_CODE_PATH.search(file_path):
            hits.append({
                "file": file_path,
                "line": 1,
                "kind": "deleted_test_file",
                "snippet": "<file deleted>",
            })

    for file_path, line_no, line in added:
        if not TEST_CODE_PATH.search(file_path):
            continue
        if _is_comment_or_doc(line):
            continue
        scanned_files.add(file_path)
        scanned_added_test_code_lines += 1
        for label, pattern in PATTERNS:
            if pattern.search(line):
                allow, reason = _is_allowlisted(file_path, line)
                if allow:
                    exclusions.append({
                        "file": file_path,
                        "line": line_no,
                        "kind": label,
                        "reason": reason,
                    })
                    break
                hits.append({
                    "file": file_path,
                    "line": line_no,
                    "kind": label,
                    "snippet": line[:180],
                })
                break

    for file_path, line_no, line in tracked_deleted:
        if not TEST_CODE_PATH.search(file_path):
            continue
        if _is_comment_or_doc(line):
            continue
        scanned_files.add(file_path)
        scanned_deleted_test_code_lines += 1
        for label, pattern in DELETED_PATTERNS:
            if pattern.search(line):
                allow, reason = _is_allowlisted(file_path, line)
                if allow:
                    exclusions.append({
                        "file": file_path,
                        "line": line_no,
                        "kind": label,
                        "reason": reason,
                    })
                    break
                hits.append({
                    "file": file_path,
                    "line": line_no,
                    "kind": label,
                    "snippet": line[:180],
                })
                break

    scanned_line_count = scanned_added_test_code_lines + scanned_deleted_test_code_lines
    coverage_error = _coverage_error_for_test_scope(
        has_test_scope_changes=has_test_scope_changes,
        scanned_line_count=scanned_line_count,
    )

    should_fail = len(hits) > 0 or coverage_error is not None

    report = {
        "base": BASE_REF,
        "tracked_added_line_count": len(tracked_added),
        "untracked_file_count": len(untracked_files),
        "untracked_line_count": len(untracked_added),
        "scanned_file_count": len(scanned_files),
        "scanned_line_count": scanned_line_count,
        "has_working_tree_changes": has_changes,
        "has_test_scope_changes": has_test_scope_changes,
        "test_scope_file_count": len(test_scope_files),
        "scanned_added_test_code_lines": scanned_added_test_code_lines,
        "scanned_deleted_test_code_lines": scanned_deleted_test_code_lines,
        "coverage_error": coverage_error,
        "allowlist_exact": ALLOWLIST_EXACT,
        "exclusions": exclusions,
        "weakening_count": len(hits),
        "hits": hits,
    }

    out = Path("reports") / "test_weakening_scan.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "tracked_added_line_count": len(tracked_added),
        "untracked_file_count": len(untracked_files),
        "untracked_line_count": len(untracked_added),
        "has_working_tree_changes": has_changes,
        "has_test_scope_changes": has_test_scope_changes,
        "test_scope_file_count": len(test_scope_files),
        "scanned_added_test_code_lines": scanned_added_test_code_lines,
        "scanned_deleted_test_code_lines": scanned_deleted_test_code_lines,
        "scanned_file_count": len(scanned_files),
        "scanned_line_count": scanned_line_count,
        "coverage_error": coverage_error,
        "weakening_count": len(hits),
        "report": str(out),
    }, ensure_ascii=False))
    return 0 if not should_fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
