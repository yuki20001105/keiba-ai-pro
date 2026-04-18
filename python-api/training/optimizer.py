"""
特徴量反復最適化パイプライン (ITR-01〜ITR-10)

概要:
  データ再取得 → モデル学習 → プロファイリング生成 → 相関分析 → 結果保存
  を N 回繰り返し、各イテレーションの指標変化を追跡する。

使い方:
  # FastAPI サーバーを起動した状態で実行
  python-api\.venv\Scripts\python.exe python-api/iterative_optimize.py

  # イテレーション数・開始番号を指定
  python-api\.venv\Scripts\python.exe python-api/iterative_optimize.py \
      --iterations 10 --start-iter 1 --skip-scrape --skip-profile

オプション:
  --iterations N     繰り返し回数 (デフォルト: 10)
  --start-iter N     開始イテレーション番号 (デフォルト: 1)
  --skip-scrape      スクレイピングをスキップ (DB に既存データがある場合)
  --skip-profile     プロファイリング HTML 生成をスキップ
  --scrape-full      2016-01〜2026-03 の全期間を強制再取得
  --base-url URL     FastAPI ベース URL (デフォルト: http://localhost:8000)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# ウィンドウ幅をプロジェクトルートに自動解決
# ─────────────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent.parent   # python-api/
PROJECT_ROOT = SCRIPT_DIR.parent                      # keiba-ai-pro/
REPORTS_DIR = PROJECT_ROOT / "docs" / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# Python インタープリタ（venv 内）
VENV_PYTHON = SCRIPT_DIR / ".venv" / "Scripts" / "python.exe"
if not VENV_PYTHON.exists():
    VENV_PYTHON = Path(sys.executable)


# ─────────────────────────────────────────────────────────────────────────────
# API ユーティリティ
# ─────────────────────────────────────────────────────────────────────────────
import urllib.request
import urllib.error


def api_post(base: str, path: str, body: dict | None = None, timeout: int = 30) -> dict:
    body_bytes = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        f"{base}{path}",
        data=body_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"__error__": e.code, "__body__": e.read().decode()}
    except Exception as e:
        return {"__error__": str(e)}


def api_get(base: str, path: str, timeout: int = 30) -> dict:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"__error__": e.code, "__body__": e.read().decode()}
    except Exception as e:
        return {"__error__": str(e)}


def poll_job(base: str, status_url: str, timeout: int = 7200, interval: int = 10, label: str = "") -> dict:
    """ジョブが completed / error になるまでポーリングする。"""
    start = time.time()
    last_msg = ""
    while time.time() - start < timeout:
        r = api_get(base, status_url)
        status = r.get("status", "unknown")
        prog = r.get("progress", {})
        msg = prog.get("message", "") if isinstance(prog, dict) else str(prog)
        if msg != last_msg:
            _log(f"  [{label}] {status}: {msg}")
            last_msg = msg
        if status in ("completed", "done"):
            return r
        if status in ("error", "failed"):
            _log(f"  [{label}] FAILED: {r.get('error', r.get('result', ''))}")
            return r
        time.sleep(interval)
    return {"status": "timeout"}


# ─────────────────────────────────────────────────────────────────────────────
# ログユーティリティ
# ─────────────────────────────────────────────────────────────────────────────
_LOG_LINES: list[str] = []


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    _LOG_LINES.append(line)


def _save_log(itr: int) -> Path:
    log_path = REPORTS_DIR / f"iter_{itr:02d}_log.txt"
    log_path.write_text("\n".join(_LOG_LINES), encoding="utf-8")
    return log_path


# ─────────────────────────────────────────────────────────────────────────────
# Step A: スクレイピング
# ─────────────────────────────────────────────────────────────────────────────
def step_scrape(base: str, force_full: bool = False) -> bool:
    """2016-01〜2026-03 のデータ取得。既存データがあれば差分のみ。"""
    _log("─" * 60)
    _log("STEP A: データ取得 (2016-01 〜 2026-03)")
    _log("─" * 60)
    body = {
        "start_date": "20160101",
        "end_date":   "20260331",
        "force_rescrape": force_full,
    }
    r = api_post(base, "/api/scrape/start", body, timeout=30)
    if "__error__" in r:
        _log(f"  ⚠ スクレイプ開始エラー: {r}")
        return False

    job_id = r.get("job_id")
    _log(f"  ✓ スクレイプジョブ開始: job_id={job_id}")
    _log("  ポーリング中 ... (最大12時間)")
    result = poll_job(base, f"/api/scrape/status/{job_id}", timeout=43200, interval=30, label="scrape")
    if result.get("status") == "completed":
        res = result.get("result", {})
        _log(f"  ✓ スクレイプ完了: {res.get('races_collected', '?')} レース / {res.get('horses_collected', '?')} 頭")
        return True
    else:
        _log(f"  ⚠ スクレイプ非完了 (status={result.get('status')}). 続行します。")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Step B: モデル学習
# ─────────────────────────────────────────────────────────────────────────────
def step_train(base: str, optuna_trials: int = 30) -> dict:
    """LightGBM モデルを学習し、メトリクスを返す。"""
    _log("─" * 60)
    _log("STEP B: モデル学習")
    _log("─" * 60)
    body = {
        "target": "speed_deviation",
        "model_type": "lightgbm",
        "test_size": 0.2,
        "cv_folds": 5,
        "use_sqlite": True,
        "use_optimizer": True,
        "use_optuna": (optuna_trials > 0),
        "optuna_trials": optuna_trials,
        "optuna_timeout": min(optuna_trials * 60, 1800),
        "ultimate_mode": True,
        "training_date_from": "2016-01",
        "training_date_to": "2026-03",
    }
    r = api_post(base, "/api/train/start", body, timeout=30)
    if "__error__" in r:
        _log(f"  ⚠ 学習開始エラー: {r}")
        return {}

    job_id = r.get("job_id")
    _log(f"  ✓ 学習ジョブ開始: job_id={job_id}")
    _log("  ポーリング中 ... (最大90分)")
    result = poll_job(base, f"/api/train/status/{job_id}", timeout=5400, interval=15, label="train")

    if result.get("status") != "completed":
        _log(f"  ⚠ 学習失敗/タイムアウト: {result.get('status')}")
        return {}

    res = result.get("result", {})
    metrics = res.get("metrics", {})
    _log(f"  ✓ 学習完了!")
    _log(f"    AUC:      {metrics.get('auc', '?')}")
    _log(f"    CV AUC:   {metrics.get('cv_auc_mean', '?')} ± {metrics.get('cv_auc_std', '?')}")
    _log(f"    LogLoss:  {metrics.get('logloss', '?')}")
    _log(f"    データ数: {res.get('data_count', '?')}")
    _log(f"    特徴量数: {res.get('feature_count', '?')}")
    return {
        "model_id":      res.get("model_id"),
        "auc":           metrics.get("auc"),
        "cv_auc_mean":   metrics.get("cv_auc_mean"),
        "cv_auc_std":    metrics.get("cv_auc_std"),
        "logloss":       metrics.get("logloss"),
        "logloss_cal":   metrics.get("logloss_calibrated"),
        "data_count":    res.get("data_count"),
        "feature_count": res.get("feature_count"),
        "timestamp":     datetime.now().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Step C: 特徴量重要度レポート生成
# ─────────────────────────────────────────────────────────────────────────────
def step_importance_report(itr: int) -> Path | None:
    """feature_importance_report スキルのスクリプトを実行してレポートを生成する。"""
    _log("─" * 60)
    _log("STEP C: 特徴量重要度レポート生成")
    _log("─" * 60)
    script = PROJECT_ROOT / ".github" / "skills" / "feature-importance-report" / "scripts" / "analyze_feature_importance.py"
    if not script.exists():
        _log(f"  ⚠ スクリプトが見つかりません: {script}")
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = REPORTS_DIR / f"feature_importance_ITR-{itr:02d}_{timestamp}.html"
    import os as _os
    _env = dict(_os.environ)
    _env["PYTHONIOENCODING"] = "utf-8"
    try:
        result = subprocess.run(
            [str(VENV_PYTHON), str(script), "--out", str(out_path)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=_env,
            timeout=300,
        )
        if result.returncode == 0 and out_path.exists():
            _log(f"  OK 重要度レポート生成: {out_path.name}")
            return out_path
        else:
            _log(f"  WARN 重要度レポート生成失敗\n{result.stderr[-500:]}")
            return None
    except subprocess.TimeoutExpired:
        _log("  WARN 重要度レポート生成タイムアウト(5分)")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Step D: プロファイリングレポート生成
# ─────────────────────────────────────────────────────────────────────────────
def step_profiling(itr: int) -> Path | None:
    """ydata-profiling レポートを生成してタイムスタンプ付きで保存する。"""
    _log("─" * 60)
    _log("STEP D: プロファイリングレポート生成")
    _log("─" * 60)
    script = PROJECT_ROOT / "generate_profiling_report.py"
    if not script.exists():
        _log(f"  ⚠ {script} が見つかりません")
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_name  = f"profiling_ITR-{itr:02d}_{timestamp}.html"
    out_path  = REPORTS_DIR / out_name

    # generate_profiling_report.py の out_path をパッチして実行
    try:
        src = script.read_text(encoding="utf-8")
        patched = src.replace(
            'out_path = "profiling_report.html"',
            f'out_path = r"{out_path}"',
        )
        # メインDBを使用（validate DBではなく学習データDB）
        patched = patched.replace(
            "'keiba/data/keiba_local_validate.db'",
            "'keiba/data/keiba_ultimate.db'",
        )
        tmp_script = PROJECT_ROOT / "_tmp_profiling_run.py"
        tmp_script.write_text(patched, encoding="utf-8")

        import os as _os2
        _env2 = dict(_os2.environ)
        _env2["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [str(VENV_PYTHON), str(tmp_script)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=_env2,
            timeout=1800,  # 30分（メインDB は大きいため延長）
        )
        tmp_script.unlink(missing_ok=True)

        if result.returncode == 0 and out_path.exists():
            _log(f"  OK プロファイリングレポート生成: {out_path.name}")
            return out_path
        else:
            _log(f"  WARN プロファイリング生成失敗\n{result.stderr[-800:]}")
            return None
    except subprocess.TimeoutExpired:
        _log("  WARN プロファイリング生成タイムアウト(30分)")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Step E: プロファイリング HTML を解析して改善ヒントを抽出
# ─────────────────────────────────────────────────────────────────────────────
def step_analyze_profiling(html_path: Path) -> dict:
    """
    プロファイリング HTML を BeautifulSoup で解析し、以下を返す:
      - high_corr: 相関 ≥ 0.85 のペアリスト [(col_a, col_b, r), ...]
      - high_missing: 欠損率 ≥ 20% の列リスト [(col, rate), ...]
      - zero_dominated: ゼロ率 ≥ 90% の列リスト [(col, rate), ...]
      - n_vars / n_rows
    """
    _log("─" * 60)
    _log("STEP E: プロファイリング解析")
    _log("─" * 60)

    result: dict = {
        "high_corr": [],
        "high_missing": [],
        "zero_dominated": [],
        "n_vars": 0,
        "n_rows": 0,
        "alerts": [],
    }

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        _log("  ⚠ BeautifulSoup が未インストール。pip install beautifulsoup4")
        return result

    if html_path is None or not html_path.exists():
        _log("  ⚠ HTML ファイルが存在しません")
        return result

    try:
        with open(html_path, encoding="utf-8") as f:
            soup = BeautifulSoup(f.read(), "html.parser")
    except Exception as e:
        _log(f"  ⚠ HTML 読み込みエラー: {e}")
        return result

    # ── 1. 統計サマリー ────────────────────────────────────────────────────
    for tag in soup.find_all(["td", "th", "span"], string=re.compile(r"Variables")):
        try:
            val = tag.find_next_sibling().get_text(strip=True).replace(",", "")
            result["n_vars"] = int(val)
            break
        except Exception:
            pass

    for tag in soup.find_all(["td", "th", "span"], string=re.compile(r"Number of rows")):
        try:
            val = tag.find_next_sibling().get_text(strip=True).replace(",", "")
            result["n_rows"] = int(val)
            break
        except Exception:
            pass

    # ── 2. アラートセクション ─────────────────────────────────────────────
    alerts_div = soup.find(id="alerts")
    if alerts_div:
        result["alerts"] = alerts_div.get_text(" ").split("\n")
        for alert_line in result["alerts"]:
            alert_line = alert_line.strip()
            # 高欠損アラート
            m = re.search(r"(\w+)\s+has.*?(\d+\.?\d*)%\s+missing", alert_line, re.IGNORECASE)
            if m:
                col, rate = m.group(1), float(m.group(2))
                if rate >= 20:
                    result["high_missing"].append((col, rate))
            # ゼロ支配アラート（欠損フラグ・is_first_race は除外）
            m2 = re.search(r"(\w+)\s+has.*?(\d+\.?\d*)%\s+zeros", alert_line, re.IGNORECASE)
            if m2:
                col, rate = m2.group(1), float(m2.group(2))
                if rate >= 90 and not col.endswith("_is_missing") and col != "is_first_race":
                    result["zero_dominated"].append((col, rate))

    # ── 3. 相関テーブル ───────────────────────────────────────────────────
    ct = soup.find(id="correlation-table-container")
    if ct:
        rows = ct.find_all("tr")
        if rows:
            headers = [td.get_text(strip=True) for td in rows[0].find_all(["th", "td"])]
            for row in rows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all(["th", "td"])]
                if not cells:
                    continue
                col_a = cells[0]
                for i, val in enumerate(cells[1:], 1):
                    if i >= len(headers):
                        break
                    col_b = headers[i]
                    if col_a == col_b:
                        continue
                    try:
                        r_val = float(val)
                        if abs(r_val) >= 0.85:
                            result["high_corr"].append((col_a, col_b, r_val))
                    except ValueError:
                        pass

    # ── 4. 結果をログ出力 ─────────────────────────────────────────────────
    _log(f"  変数数: {result['n_vars']}, 行数: {result['n_rows']}")

    if result["high_corr"]:
        _log(f"  高相関ペア ({len(result['high_corr'])} 件, r ≥ 0.85):")
        seen = set()
        for a, b, r in sorted(result["high_corr"], key=lambda x: -abs(x[2])):
            pair = tuple(sorted([a, b]))
            if pair not in seen:
                seen.add(pair)
                _log(f"    {a} <-> {b}: r={r:.3f}")

    if result["high_missing"]:
        _log(f"  高欠損列 ({len(result['high_missing'])} 件, rate ≥ 20%):")
        for col, rate in sorted(result["high_missing"], key=lambda x: -x[1])[:10]:
            _log(f"    {col}: {rate:.1f}%")

    if result["zero_dominated"]:
        _log(f"  ゼロ支配列 ({len(result['zero_dominated'])} 件, rate ≥ 90%):")
        for col, rate in result["zero_dominated"]:
            _log(f"    {col}: {rate:.1f}%")

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Step F: 改善レコメンデーションを JSON に保存
# ─────────────────────────────────────────────────────────────────────────────
def step_save_metrics(itr: int, train_metrics: dict, profile_analysis: dict) -> Path:
    """イテレーション結果を JSON ファイルに保存する。"""
    _log("─" * 60)
    _log("STEP F: メトリクス保存")
    _log("─" * 60)

    # 高相関ペアから改善推奨を生成
    recommendations: list[str] = []
    seen_pairs: set = set()

    # UNNECESSARY_COLUMNS に既に入っているものは除外
    try:
        sys.path.insert(0, str(PROJECT_ROOT / "keiba"))
        from keiba_ai.constants import UNNECESSARY_COLUMNS  # type: ignore
        unnecessary_set = set(UNNECESSARY_COLUMNS)
    except Exception:
        unnecessary_set = set()

    for col_a, col_b, r_val in sorted(profile_analysis.get("high_corr", []), key=lambda x: -abs(x[2])):
        pair = tuple(sorted([col_a, col_b]))
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        # どちらかが既に UNNECESSARY_COLUMNS に入っている → スキップ
        if col_a in unnecessary_set or col_b in unnecessary_set:
            continue
        if abs(r_val) >= 0.90:
            recommendations.append(f"[HIGH_CORR r={r_val:.3f}] {col_a} <-> {col_b}: 低重要度側をUNNECESSARY_COLUMNSへ")

    for col, rate in profile_analysis.get("zero_dominated", []):
        if col not in unnecessary_set:
            recommendations.append(f"[ZEROS {rate:.0f}%] {col}: 定数列の可能性あり → 削除検討")

    out_data = {
        "iteration": itr,
        "timestamp": datetime.now().isoformat(),
        "train_metrics": train_metrics,
        "profile_summary": {
            "n_vars":       profile_analysis.get("n_vars"),
            "n_rows":       profile_analysis.get("n_rows"),
            "high_corr_count": len(set(
                tuple(sorted([a, b]))
                for a, b, _ in profile_analysis.get("high_corr", [])
            )),
            "high_missing_count": len(profile_analysis.get("high_missing", [])),
        },
        "recommendations": recommendations,
    }

    path = REPORTS_DIR / f"iter_{itr:02d}_metrics.json"
    path.write_text(json.dumps(out_data, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"  ✓ メトリクス保存: {path.name}")
    if recommendations:
        _log(f"  推奨変更 ({len(recommendations)} 件):")
        for rec in recommendations[:8]:
            _log(f"    {rec}")
        if len(recommendations) > 8:
            _log(f"    ... 他 {len(recommendations)-8} 件 → {path.name} を確認")
    return path


# ─────────────────────────────────────────────────────────────────────────────
# 収束チェック
# ─────────────────────────────────────────────────────────────────────────────
_history: list[dict] = []


def check_convergence() -> bool:
    """
    連続2イテレーションで CV AUC 改善 < 0.001 → True（収束）
    直前比で CV AUC が 0.005 以上低下 → True（悪化）
    """
    if len(_history) < 2:
        return False

    prev = _history[-2].get("cv_auc_mean")
    curr = _history[-1].get("cv_auc_mean")
    if prev is None or curr is None:
        return False

    try:
        prev_f, curr_f = float(prev), float(curr)
    except (TypeError, ValueError):
        return False

    if curr_f < prev_f - 0.005:
        _log(f"  ⚠ CV AUC が低下: {prev_f:.4f} → {curr_f:.4f} (差={curr_f-prev_f:.4f}). 収束と判断します。")
        return True

    if len(_history) >= 2:
        prev2 = _history[-2].get("cv_auc_mean")
        prev1 = _history[-1].get("cv_auc_mean")
        if prev2 is not None and prev1 is not None:
            try:
                if abs(float(prev1) - float(prev2)) < 0.001:
                    _log(f"  ✅ CV AUC 変化 < 0.001: {float(prev2):.4f} → {float(prev1):.4f}. 収束しました。")
                    return True
            except (TypeError, ValueError):
                pass

    return False


# ─────────────────────────────────────────────────────────────────────────────
# メインループ
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="特徴量反復最適化パイプライン")
    parser.add_argument("--iterations",  type=int,  default=10,                      help="繰り返し回数")
    parser.add_argument("--start-iter",  type=int,  default=1,                       help="開始イテレーション番号")
    parser.add_argument("--skip-scrape", action="store_true",                        help="スクレイピングをスキップ")
    parser.add_argument("--skip-profile",action="store_true",                        help="プロファイリング生成をスキップ")
    parser.add_argument("--scrape-full", action="store_true",                        help="全期間を強制再取得")
    parser.add_argument("--optuna-trials", type=int, default=30,                     help="Optuna トライアル数")
    parser.add_argument("--base-url",    default="http://localhost:8000",            help="FastAPI ベース URL")
    args = parser.parse_args()

    base      = args.base_url
    max_itr   = args.start_iter + args.iterations - 1
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")

    _log("=" * 60)
    _log(f"特徴量反復最適化パイプライン 開始")
    _log(f"  イテレーション: ITR-{args.start_iter:02d} 〜 ITR-{max_itr:02d}")
    _log(f"  開始日時:       {timestamp}")
    _log(f"  FastAPI URL:    {base}")
    _log("=" * 60)

    # API 疎通確認
    health = api_get(base, "/health", timeout=5)
    if "__error__" in health:
        _log(f"  ⚠ FastAPI ({base}) に接続できません: {health}")
        _log("  サーバーを起動してから再実行してください。")
        sys.exit(1)
    _log(f"  ✓ API 疎通確認 OK")

    for itr in range(args.start_iter, max_itr + 1):
        _log("")
        _log("█" * 60)
        _log(f"  ITR-{itr:02d} / {max_itr:02d}  開始: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        _log("█" * 60)

        # Step A: スクレイピング（初回 or --scrape-full 指定時のみ、または初回のみ）
        if not args.skip_scrape and (itr == args.start_iter or args.scrape_full):
            step_scrape(base, force_full=args.scrape_full)
        else:
            _log("  [STEP A] スクレイピングをスキップ")

        # Step B: モデル学習
        train_metrics = step_train(base, optuna_trials=args.optuna_trials)
        if not train_metrics:
            _log(f"  ✗ ITR-{itr:02d}: 学習失敗。このイテレーションをスキップします。")
            continue
        _history.append(train_metrics)

        # Step C: 特徴量重要度レポート
        step_importance_report(itr)

        # Step D: プロファイリングレポート生成
        profile_html = None
        if not args.skip_profile:
            profile_html = step_profiling(itr)
        else:
            _log("  [STEP D] プロファイリング生成をスキップ")
            # 最新の profiling HTML を探す
            existing = sorted(REPORTS_DIR.glob("profiling_ITR-*.html"))
            if existing:
                profile_html = existing[-1]
                _log(f"  [STEP D] 既存レポートを使用: {profile_html.name}")

        # Step E: プロファイリング解析
        profile_analysis: dict = {}
        if profile_html:
            profile_analysis = step_analyze_profiling(profile_html)

        # Step F: 結果保存
        metrics_path = step_save_metrics(itr, train_metrics, profile_analysis)

        # ログ保存
        log_path = _save_log(itr)
        _log(f"  ✓ ログ保存: {log_path.name}")

        # 収束チェック（2イテレーション目以降）
        if itr >= args.start_iter + 1 and check_convergence():
            _log("")
            _log(f"★ ITR-{itr:02d} で収束判定 → ループ終了")
            break

        # 次イテレーションへの指示（AI による特徴量変更のトリガー）
        _log("")
        _log(f"  ──────────────────────────────────────────────────────")
        _log(f"  ITR-{itr:02d} 完了。")
        _log(f"  次のステップ:")
        _log(f"    1. {metrics_path.name} の recommendations を確認")
        if profile_html:
            _log(f"    2. {profile_html.name} をブラウザで開いて相関を確認")
        _log(f"    3. keiba/keiba_ai/constants.py の UNNECESSARY_COLUMNS を更新")
        _log(f"       (feature-profiling-analysis スキルの手順を参照)")
        _log(f"    4. feature_engineering.py / lightgbm_feature_optimizer.py を更新")
        _log(f"    5. 更新後、このスクリプトを --start-iter {itr+1} --skip-scrape で再実行")
        _log(f"  ──────────────────────────────────────────────────────")

        # 最初の1サイクルは特徴量変更が必要なため自動ループしない
        # --skip-profile --skip-scrape で連続実行する場合はここをコメントアウト
        if not args.skip_profile:
            _log("")
            _log(f"  ⏸ スクリプトを停止します（特徴量変更後に --start-iter {itr+1} で再開してください）")
            break

    # 全体サマリー
    _log("")
    _log("=" * 60)
    _log("全イテレーション サマリー")
    _log("=" * 60)
    if _history:
        _log(f"  {'ITR':<6} {'CV AUC':<10} {'AUC':<8} {'LogLoss':<10} 特徴量数")
        for i, h in enumerate(_history, start=args.start_iter):
            cv  = h.get("cv_auc_mean",   "?")
            auc = h.get("auc",           "?")
            ll  = h.get("logloss",       "?")
            fc  = h.get("feature_count", "?")
            _log(f"  {i:<6} {str(cv):<10} {str(auc):<8} {str(ll):<10} {fc}")

    # 最終ログ保存
    final_log = REPORTS_DIR / f"iterative_optimize_{timestamp}.log"
    final_log.write_text("\n".join(_LOG_LINES), encoding="utf-8")
    _log(f"\n✅ 完了。全ログ: {final_log}")


if __name__ == "__main__":
    main()
