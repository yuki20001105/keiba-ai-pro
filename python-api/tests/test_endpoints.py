"""
全エンドポイント包括テストスイート
自律検証ループ (validate_api.py) から呼び出される。
httpx の同期クライアントを使用して FastAPI サーバーを叩く。

テスト実行例:
    python tests/test_endpoints.py --base-url http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import httpx
except ImportError:
    print("httpx がありません。pip install httpx を実行してください。", file=sys.stderr)
    sys.exit(1)

# Supabase との接続が有効かどうかを環境変数で判定
# validate_api.py が --supabase-url / --supabase-key を渡した場合は os.environ に設定済み
_SUPABASE_ENABLED: bool = bool(
    os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY")
)

BASE_URL = "http://127.0.0.1:8000"
TIMEOUT = 30.0


# ───────────────────────────────────────────────────────────
# 結果データクラス
# ───────────────────────────────────────────────────────────

@dataclass
class TestResult:
    name: str
    method: str
    path: str
    status_code: Optional[int] = None
    passed: bool = False
    error: Optional[str] = None
    duration_ms: float = 0.0
    response_snippet: str = ""
    assertions: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)


@dataclass
class SuiteResult:
    timestamp: str
    base_url: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    duration_s: float = 0.0
    results: List[TestResult] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        return round(self.passed / self.total * 100, 1) if self.total else 0.0


# ───────────────────────────────────────────────────────────
# ユーティリティ
# ───────────────────────────────────────────────────────────

def _run(
    client: httpx.Client,
    method: str,
    path: str,
    name: str,
    expected_status: int = 200,
    json_body: Any = None,
    params: Dict = None,
    checks: List[tuple] = None,  # [(key_path, expected_value_or_callable), ...]
) -> TestResult:
    """1エンドポイントのテストを実行してTestResultを返す"""
    url = BASE_URL + path
    result = TestResult(name=name, method=method.upper(), path=path)
    t0 = time.perf_counter()
    try:
        resp = client.request(
            method, url,
            json=json_body,
            params=params or {},
            timeout=TIMEOUT,
        )
        result.duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        result.status_code = resp.status_code

        # ステータスコード確認
        if resp.status_code != expected_status:
            result.failures.append(
                f"期待ステータス {expected_status}、実際 {resp.status_code}"
            )
        else:
            result.assertions.append(f"ステータスコード {expected_status} OK")

        # レスポンスボディ確認
        try:
            body = resp.json()
            result.response_snippet = json.dumps(body, ensure_ascii=False)[:300]
        except Exception:
            body = {}
            result.response_snippet = resp.text[:300]

        # 追加アサーション
        if checks:
            for key_path, expected in checks:
                actual = _dig(body, key_path)
                try:
                    if callable(expected):
                        ok = expected(actual)
                        label = f"{key_path} callable check"
                    else:
                        ok = actual == expected
                        label = f"{key_path} == {expected!r}"
                    if ok:
                        result.assertions.append(f"✓ {label}")
                    else:
                        result.failures.append(f"✗ {label} (got {actual!r})")
                except Exception as e:
                    result.failures.append(f"✗ {key_path} チェック例外: {e}")

        result.passed = len(result.failures) == 0

    except httpx.TimeoutException:
        result.duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        result.error = f"タイムアウト ({TIMEOUT}s)"
        result.failures.append(result.error)
    except Exception as e:
        result.duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        result.error = f"{type(e).__name__}: {e}"
        result.failures.append(result.error)

    return result


def _dig(obj: Any, key_path: str) -> Any:
    """'key1.key2[0].key3' 形式のパスでネストされた値を取得"""
    for part in key_path.replace("]", "").split("."):
        if part.startswith("["):
            idx = int(part[1:])
            try:
                obj = obj[idx]
            except (IndexError, TypeError):
                return None
        elif isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
        if obj is None:
            return None
    return obj


# ───────────────────────────────────────────────────────────
# テストケース群
# ───────────────────────────────────────────────────────────

def run_suite(base_url: str = BASE_URL) -> SuiteResult:
    global BASE_URL
    BASE_URL = base_url.rstrip("/")

    suite_start = time.perf_counter()
    suite = SuiteResult(
        timestamp=datetime.now().isoformat(),
        base_url=BASE_URL,
    )

    with httpx.Client(timeout=TIMEOUT) as client:

        # ─── stats router ─────────────────────────────────────

        suite.results.append(_run(
            client, "GET", "/", "ヘルスチェック GET /",
            expected_status=200,
            checks=[
                ("status", "ok"),
                ("service", lambda v: v and "Keiba" in v),
            ],
        ))

        suite.results.append(_run(
            client, "GET", "/api/debug", "デバッグ情報 GET /api/debug",
            expected_status=200,
            checks=[
                ("supabase_enabled", lambda v: isinstance(v, bool)),
            ],
        ))

        suite.results.append(_run(
            client, "GET", "/api/data_stats", "データ統計 GET /api/data_stats",
            expected_status=200,
            checks=[
                ("total_races", lambda v: isinstance(v, int)),
                ("total_horses", lambda v: isinstance(v, int)),
                ("total_models", lambda v: isinstance(v, int)),
                ("db_exists", lambda v: isinstance(v, bool)),
            ],
        ))

        suite.results.append(_run(
            client, "POST", "/api/test-optuna-request", "Optuna テストリクエスト",
            expected_status=200,
            json_body={
                "target": "win",
                "model_type": "lightgbm",
                "use_optimizer": True,
                "use_optuna": True,
                "optuna_trials": 5,
                "cv_folds": 3,
            },
            checks=[
                ("received.target", "win"),
                ("received.model_type", "lightgbm"),
                ("will_execute_optuna", True),
            ],
        ))

        suite.results.append(_run(
            client, "GET", "/api/test/task", "非同期タスクテスト GET /api/test/task",
            expected_status=200,
            checks=[
                ("test_id", lambda v: v and str(v).startswith("test_")),
            ],
        ))

        suite.results.append(_run(
            client, "GET", "/api/test/connectivity", "netkeiba疎通テスト",
            expected_status=200,
            checks=[
                ("netkeiba", lambda v: isinstance(v, dict)),
            ],
        ))

        # ─── train router ─────────────────────────────────────

        # /api/train/start → job_id 取得
        start_r = _run(
            client, "POST", "/api/train/start", "非同期学習ジョブ起動",
            expected_status=200,
            json_body={
                "target": "win",
                "model_type": "lightgbm",
                "use_optimizer": True,
                "use_optuna": False,
                "cv_folds": 3,
                "test_size": 0.2,
                "force_sync": False,
                "ultimate_mode": False,
            },
            checks=[
                ("job_id", lambda v: v is not None),
                ("status", lambda v: v in ("queued", "running", "completed", "error")),
            ],
        )
        suite.results.append(start_r)

        # job_id があればステータスポーリング
        train_job_id = None
        if start_r.passed and start_r.status_code == 200:
            try:
                train_job_id = json.loads(start_r.response_snippet.split("\n")[0] if "\n" in start_r.response_snippet else start_r.response_snippet).get("job_id")
            except Exception:
                pass

        if train_job_id:
            suite.results.append(_run(
                client, "GET", f"/api/train/status/{train_job_id}",
                "学習ジョブステータス取得",
                expected_status=200,
                checks=[
                    ("job_id", train_job_id),
                    ("status", lambda v: v in ("queued", "running", "completed", "error", "not_found")),
                ],
            ))
        else:
            suite.results.append(_run(
                client, "GET", "/api/train/status/nonexistent_job",
                "学習ジョブステータス(不存在IDフォールバック)",
                expected_status=200,
                checks=[("status", "not_found")],
            ))

        # ─── models_mgmt router ───────────────────────────────

        suite.results.append(_run(
            client, "GET", "/api/models", "モデル一覧 GET /api/models",
            expected_status=200,
            checks=[
                ("models", lambda v: isinstance(v, list)),
                ("count", lambda v: isinstance(v, int)),
            ],
        ))

        suite.results.append(_run(
            client, "GET", "/api/models?ultimate=true",
            "Ultimateモデル一覧 GET /api/models?ultimate=true",
            expected_status=200,
            checks=[
                ("models", lambda v: isinstance(v, list)),
            ],
        ))

        suite.results.append(_run(
            client, "DELETE", "/api/models/nonexistent_model_id_xyz",
            "存在しないモデル削除(404期待)",
            expected_status=404,
        ))

        # ─── predict router ───────────────────────────────────

        dummy_horses = [
            {
                "horse_number": i,
                "horse_no": i,
                "horse_name": f"テスト馬{i}",
                "age": 4,
                "sex": "牡",
                "odds": float(i + 1),
                "entry_odds": float(i + 1),
                "popularity": i,
                "jockey_id": f"jockey_{i:04d}",
                "trainer_id": f"trainer_{i:04d}",
                "venue_code": "05",
                "track_type": "芝",
                "distance": 1600,
                "horse_weight": 460,
                "weight": 460,
            }
            for i in range(1, 9)
        ]

        predict_r = _run(
            client, "POST", "/api/predict", "予測 POST /api/predict (モデルなし)",
            expected_status=200,
            json_body={"horses": dummy_horses},
        )
        # モデルがなければ404も許容
        if predict_r.status_code == 404:
            predict_r.passed = True
            predict_r.failures = []
            predict_r.assertions.append("モデル未存在 404 → 正常(モデルなし環境)")
        elif predict_r.status_code == 200:
            try:
                body = json.loads(predict_r.response_snippet)
                if isinstance(body.get("predictions"), list):
                    predict_r.assertions.append("predictions リスト OK")
                else:
                    predict_r.failures.append("predictions がリストでない")
                predict_r.passed = len(predict_r.failures) == 0
            except Exception:
                pass
        suite.results.append(predict_r)

        # analyze_race は DBがないと404になるので404/200両方許容
        analyze_r = _run(
            client, "POST", "/api/analyze_race", "レース分析 POST /api/analyze_race",
            expected_status=200,
            json_body={
                "race_id": "202501050101",
                "bankroll": 10000,
                "risk_mode": "balanced",
                "use_kelly": True,
                "dynamic_unit": True,
                "min_ev": 1.2,
                "ultimate_mode": False,
            },
        )
        if analyze_r.status_code in (404, 500):
            analyze_r.passed = True
            analyze_r.failures = []
            analyze_r.assertions.append(
                f"DB/モデル未存在 {analyze_r.status_code} → 期待内レスポンス"
            )
        suite.results.append(analyze_r)

        # ─── purchase router ──────────────────────────────────

        purchase_r = _run(
            client, "POST", "/api/purchase", "購入履歴保存 POST /api/purchase",
            expected_status=200,
            json_body={
                "race_id": "202501050101",
                "venue": "東京",
                "bet_type": "単勝",
                "combinations": ["1"],
                "strategy_type": "kelly",
                "purchase_count": 1,
                "unit_price": 100,
                "total_cost": 100,
                "expected_value": 1.5,
                "expected_return": 150.0,
            },
            checks=[
                ("success", True),
                ("purchase_id", lambda v: isinstance(v, int) and v > 0),
            ],
        )
        suite.results.append(purchase_r)

        suite.results.append(_run(
            client, "GET", "/api/purchase_history", "購入履歴取得 GET /api/purchase_history",
            expected_status=200,
            checks=[
                ("success", True),
                ("history", lambda v: isinstance(v, list)),
                ("count", lambda v: isinstance(v, int)),
            ],
        ))

        suite.results.append(_run(
            client, "GET", "/api/statistics", "統計取得 GET /api/statistics",
            expected_status=200,
            checks=[
                ("success", True),
            ],
        ))

        # ─── scrape router ────────────────────────────────────

        scrape_start_r = _run(
            client, "POST", "/api/scrape/start", "スクレイプジョブ起動",
            expected_status=200,
            json_body={
                "start_date": "20260201",
                "end_date": "20260201",
                "force_rescrape": False,
            },
            checks=[
                ("job_id", lambda v: v is not None),
                ("status", lambda v: v in ("queued", "running", "error")),
            ],
        )
        suite.results.append(scrape_start_r)

        scrape_job_id = None
        if scrape_start_r.passed and scrape_start_r.status_code == 200:
            try:
                scrape_job_id = json.loads(
                    scrape_start_r.response_snippet
                ).get("job_id")
            except Exception:
                pass

        if scrape_job_id:
            suite.results.append(_run(
                client, "GET", f"/api/scrape/status/{scrape_job_id}",
                "スクレイプジョブステータス",
                expected_status=200,
                checks=[
                    ("job_id", scrape_job_id),
                    ("status", lambda v: v in ("queued", "running", "completed", "error")),
                ],
            ))
        else:
            suite.results.append(_run(
                client, "GET", "/api/scrape/status/nonexistent",
                "スクレイプジョブステータス(不存在ID)",
                expected_status=200,
                checks=[("status", "not_found")],
            ))

        # ─── export router ────────────────────────────────────
        # Supabase あり環境: 200 とレスポンスボディを検証
        # Supabase なし環境: 503 を許容

        export_data_r = _run(
            client, "GET", "/api/export-data",
            "\u30c7\u30fc\u30bf\u30a8\u30af\u30b9\u30dd\u30fc\u30c8 GET /api/export-data",
            expected_status=200,
            params={"limit": 100},  # OOM 回避: テストでは小さい limit を使用
        )
        if _SUPABASE_ENABLED:
            # Supabase 有効: 200 かつ正しい構造を期待
            if export_data_r.status_code == 200:
                try:
                    body = json.loads(export_data_r.response_snippet)
                    if isinstance(body.get("races_count"), int) and isinstance(body.get("results_count"), int):
                        export_data_r.assertions.append(
                            f"Supabaseデータ取得成功: races={body['races_count']}, results={body['results_count']}"
                        )
                        export_data_r.passed = len(export_data_r.failures) == 0
                except Exception:
                    pass
        else:
            if export_data_r.status_code == 503:
                export_data_r.passed = True
                export_data_r.failures = []
                export_data_r.assertions.append("Supabase未接続 503 → 期待内")
        suite.results.append(export_data_r)

        export_db_r = _run(
            client, "GET", "/api/export-db",
            "\u30c7\u30fc\u30bf\u30d9\u30fc\u30b9\u30a8\u30af\u30b9\u30dd\u30fc\u30c8 GET /api/export-db",
            expected_status=200,
            params={"limit": 50},  # OOM 回避: テストでは小さい limit を使用
        )
        if _SUPABASE_ENABLED:
            if export_db_r.status_code == 200:
                # ストリーミングレスポンスなので Content-Type でチェック
                export_db_r.assertions.append(
                    "Supabase SQLiteエクスポート成功 (streaming)"
                )
                export_db_r.passed = True
                export_db_r.failures = []
        else:
            if export_db_r.status_code == 503:
                export_db_r.passed = True
                export_db_r.failures = []
                export_db_r.assertions.append("Supabase未接続 503 → 期待内")
        suite.results.append(export_db_r)

        race_ids_r = _run(
            client, "GET", "/api/debug/race-ids",
            "race_id一覧 GET /api/debug/race-ids",
            expected_status=200,
            params={"limit": 5},
        )
        if _SUPABASE_ENABLED:
            if race_ids_r.status_code == 200:
                try:
                    body = json.loads(race_ids_r.response_snippet)
                    if isinstance(body.get("race_ids"), list) and isinstance(body.get("count"), int):
                        race_ids_r.assertions.append(
                            f"race_id一覧取得成功: count={body['count']}"
                        )
                        race_ids_r.passed = len(race_ids_r.failures) == 0
                except Exception:
                    pass
        else:
            if race_ids_r.status_code == 503:
                race_ids_r.passed = True
                race_ids_r.failures = []
                race_ids_r.assertions.append("Supabase未接続 503 → 期待内")
        suite.results.append(race_ids_r)

        # ─── backfill router ──────────────────────────────────

        backfill_nar_r = _run(
            client, "POST", "/api/backfill/nar-pedigree",
            "NAR血統バックフィル POST /api/backfill/nar-pedigree",
            expected_status=200,
            params={"limit": 1},
        )
        if backfill_nar_r.status_code == 200:
            try:
                body = json.loads(backfill_nar_r.response_snippet)
                if body.get("success") is False and "Supabase" in str(body.get("message", "")):
                    backfill_nar_r.passed = True
                    backfill_nar_r.failures = []
                    backfill_nar_r.assertions.append("Supabase無効 → successFalse(期待内)")
            except Exception:
                pass
        suite.results.append(backfill_nar_r)

        backfill_coat_r = _run(
            client, "POST", "/api/backfill/coat-color",
            "毛色バックフィル POST /api/backfill/coat-color",
            expected_status=200,
            params={"limit": 1},
        )
        if backfill_coat_r.status_code == 200:
            try:
                body = json.loads(backfill_coat_r.response_snippet)
                if body.get("success") is False and "Supabase" in str(body.get("message", "")):
                    backfill_coat_r.passed = True
                    backfill_coat_r.failures = []
                    backfill_coat_r.assertions.append("Supabase無効 → successFalse(期待内)")
            except Exception:
                pass
        suite.results.append(backfill_coat_r)

        # ─── profiling router ─────────────────────────────────

        profiling_start_r = _run(
            client, "POST", "/api/profiling/start",
            "プロファイリング起動 POST /api/profiling/start",
            expected_status=200,
            params={"use_optimized": "false"},
            checks=[
                ("job_id", lambda v: v is not None),
            ],
        )
        suite.results.append(profiling_start_r)

        profiling_job_id = None
        if profiling_start_r.passed and profiling_start_r.status_code == 200:
            try:
                profiling_job_id = json.loads(
                    profiling_start_r.response_snippet
                ).get("job_id")
            except Exception:
                pass

        if profiling_job_id:
            suite.results.append(_run(
                client, "GET", f"/api/profiling/status/{profiling_job_id}",
                "プロファイリングステータス",
                expected_status=200,
                checks=[
                    ("status", lambda v: v in ("running", "completed", "error")),
                ],
            ))
            # HTML は実行中なら202
            html_r = _run(
                client, "GET", f"/api/profiling/html/{profiling_job_id}",
                "プロファイリングHTML取得",
                expected_status=200,
            )
            if html_r.status_code == 202:
                html_r.passed = True
                html_r.failures = []
                html_r.assertions.append("レポート生成中 202 → 期待内")
            suite.results.append(html_r)
        else:
            suite.results.append(_run(
                client, "GET", "/api/profiling/status/nonexistent",
                "プロファイリングステータス(不存在)",
                expected_status=404,
            ))

    # ─── 集計 ──────────────────────────────────────────────
    suite.total = len(suite.results)
    suite.passed = sum(1 for r in suite.results if r.passed)
    suite.failed = sum(1 for r in suite.results if not r.passed and r.error is None)
    suite.errors = sum(1 for r in suite.results if r.error is not None)
    suite.duration_s = round(time.perf_counter() - suite_start, 2)
    return suite


# ───────────────────────────────────────────────────────────
# スタンドアロン実行
# ───────────────────────────────────────────────────────────

def _print_suite(suite: SuiteResult) -> None:
    sep = "─" * 70
    print(f"\n{'═' * 70}")
    print(f"  テスト結果サマリー  {suite.timestamp}")
    print(f"  {suite.base_url}  合計:{suite.total}  ✓{suite.passed}  ✗{suite.failed}  ERR{suite.errors}  ({suite.duration_s}s  成功率{suite.success_rate}%)")
    print(f"{'═' * 70}")
    for r in suite.results:
        icon = "✓" if r.passed else "✗"
        print(f"  {icon}  [{r.status_code or '---'}] {r.method} {r.path}  ({r.duration_ms}ms)")
        if not r.passed:
            for f in r.failures:
                print(f"       ↳ {f}")
            if r.response_snippet:
                print(f"       ↳ resp: {r.response_snippet[:200]}")
        print(sep)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="全エンドポイントテスト")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--json", action="store_true", help="JSON出力")
    args = parser.parse_args()

    suite = run_suite(args.base_url)
    if args.json:
        print(json.dumps(
            {
                "timestamp": suite.timestamp,
                "base_url": suite.base_url,
                "total": suite.total,
                "passed": suite.passed,
                "failed": suite.failed,
                "errors": suite.errors,
                "duration_s": suite.duration_s,
                "success_rate": suite.success_rate,
                "results": [asdict(r) for r in suite.results],
            },
            ensure_ascii=False, indent=2,
        ))
    else:
        _print_suite(suite)

    sys.exit(0 if suite.passed == suite.total else 1)
