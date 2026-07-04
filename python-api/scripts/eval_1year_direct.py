"""
1年間（2025-01-01〜2026-03-31）評価パイプライン（直接呼び出し版）

API 認証を迂回し、_run_scrape_job / _do_train を asyncio.run() で直接実行。
train_direct.py と同じアプローチ。

使い方:
  # スクレイプあり（初回）
  python-api\.venv\Scripts\python.exe python-api/scripts/eval_1year_direct.py

  # スクレイプスキップ（データ取得済みの場合）
  python-api\.venv\Scripts\python.exe python-api/scripts/eval_1year_direct.py --skip-scrape

  # 強制再スクレイプ
  python-api\.venv\Scripts\python.exe python-api/scripts/eval_1year_direct.py --force-rescrape

  # win のみ学習
  python-api\.venv\Scripts\python.exe python-api/scripts/eval_1year_direct.py --skip-scrape --targets win
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

# ─── パス設定 ────────────────────────────────────────────────────
SCRIPT_DIR   = Path(__file__).resolve().parent          # python-api/scripts/
PYTHON_API   = SCRIPT_DIR.parent                        # python-api/
PROJECT_ROOT = PYTHON_API.parent                        # keiba-ai-pro/
REPORTS_DIR  = PROJECT_ROOT / "docs" / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(PYTHON_API))
sys.path.insert(0, str(PROJECT_ROOT / "keiba"))

# ─── 設定 ────────────────────────────────────────────────────────
DATE_FROM   = "20250101"
DATE_TO     = "20260430"
TRAIN_FROM  = "2025-01"
TRAIN_TO    = "2026-04"

# ─── ログ ────────────────────────────────────────────────────────
_LOG: list[str] = []


def _log(msg: str) -> None:
    ts   = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    _LOG.append(line)


def _save_log(suffix: str = "") -> Path:
    ts   = datetime.now().strftime("%Y%m%d_%H%M")
    path = REPORTS_DIR / f"eval_1year_direct{suffix}_{ts}.txt"
    path.write_text("\n".join(_LOG), encoding="utf-8")
    _log(f"ログ保存: {path}")
    return path


# ─── DB 確認 ─────────────────────────────────────────────────────

def check_db() -> dict:
    import sqlite3
    db = PROJECT_ROOT / "keiba" / "data" / "keiba_ultimate.db"
    try:
        conn = sqlite3.connect(str(db))
        total = conn.execute("SELECT COUNT(*) FROM race_results_ultimate").fetchone()[0]
        rows  = conn.execute(
            "SELECT date, race_count FROM scraped_dates "
            "WHERE date >= 20250101 AND date <= 20260331 ORDER BY date"
        ).fetchall()
        conn.close()
        _log(f"  DB 総行数: {total:,}")
        _log(f"  scraped_dates (2025-01〜2026-03): {len(rows)} 日")
        if rows:
            _log(f"  最古: {rows[0][0]}  最新: {rows[-1][0]}")
        return {"total": total, "dates": len(rows)}
    except Exception as e:
        _log(f"  DB確認エラー: {e}")
        return {}


# ─── STEP A: スクレイプ ───────────────────────────────────────────

async def step_scrape(force_rescrape: bool = False) -> bool:
    _log("=" * 60)
    _log(f"STEP A: スクレイプ {DATE_FROM} 〜 {DATE_TO}")
    _log("=" * 60)
    try:
        from scraping.jobs import _run_scrape_job as _rsj  # type: ignore
        import uuid
        job_id = str(uuid.uuid4())
        # _scrape_jobs に仮エントリを追加
        from scraping.jobs import _scrape_jobs  # type: ignore
        _scrape_jobs[job_id] = {"status": "pending", "progress": {}}
        _log(f"  直接呼び出し: job_id={job_id}")
        await _rsj(job_id, DATE_FROM, DATE_TO, force_rescrape=force_rescrape)
        final = _scrape_jobs.get(job_id, {})
        status = final.get("status", "?")
        res    = final.get("result", {})
        _log(f"  完了: status={status}")
        if isinstance(res, dict):
            _log(f"  レース: {res.get('races_collected','?')}  馬: {res.get('horses_collected','?')}")
        return status == "completed"
    except Exception as e:
        _log(f"  スクレイプエラー: {e}")
        import traceback; traceback.print_exc()
        return False


# ─── STEP B: 学習 ────────────────────────────────────────────────

async def step_train(target: str, optuna_trials: int = 30) -> dict:
    _log("=" * 60)
    _log(f"STEP B: 学習  target={target}  {TRAIN_FROM} 〜 {TRAIN_TO}")
    _log("=" * 60)
    try:
        from models import TrainRequest  # type: ignore
        from routers.train import _do_train  # type: ignore

        # speed_deviation は全期間データで学習（日付制限をかけると 4,577行/8日分しか
        # なく、prev_speed_index・騎手勝率等の rolling 特徴量が全て NaN になる）。
        # win は直近1年に絞ることで最新の馬場傾向・騎手配置を優先する。
        if target == "speed_deviation":
            _train_from = None   # 全期間: 約27,000行
            _train_to   = None
        else:
            _train_from = TRAIN_FROM   # 2025-01〜2026-03
            _train_to   = TRAIN_TO

        request = TrainRequest(
            target               = target,
            model_type           = "lightgbm",
            test_size            = 0.2,
            cv_folds             = 5,
            use_sqlite           = True,
            ultimate_mode        = True,
            use_optimizer        = True,
            use_optuna           = True,
            optuna_trials        = optuna_trials,
            optuna_timeout       = min(optuna_trials * 60, 2400),
            training_date_from   = _train_from,
            training_date_to     = _train_to,
            force_sync           = False,
        )

        dummy_user = {"sub": "eval-direct", "role": "admin", "subscription_tier": "premium"}

        def progress_cb(msg: str, pct=None):
            ts   = datetime.now().strftime("%H:%M:%S")
            line = f"[{ts}] ({pct or '?':>3}%) {msg}"
            print(line, flush=True)
            _LOG.append(line)

        result = await _do_train(request, current_user=dummy_user, progress_cb=progress_cb)

        mt = result.metrics if hasattr(result, "metrics") else {}
        _log(f"  学習完了!")
        _log(f"    model_id     : {getattr(result, 'model_id', '?')}")
        _log(f"    data_count   : {getattr(result, 'data_count', '?')}")
        _log(f"    race_count   : {getattr(result, 'race_count', '?')}")
        _log(f"    feature_count: {getattr(result, 'feature_count', '?')}")
        _log(f"    AUC          : {mt.get('auc', 'N/A') if isinstance(mt, dict) else getattr(mt, 'auc', 'N/A')}")
        _log(f"    CV AUC       : {mt.get('cv_auc_mean', 'N/A') if isinstance(mt, dict) else getattr(mt, 'cv_auc_mean', 'N/A')}")
        _log(f"    LogLoss      : {mt.get('logloss', 'N/A') if isinstance(mt, dict) else getattr(mt, 'logloss', 'N/A')}")

        def _mget(key):
            if isinstance(mt, dict):
                return mt.get(key)
            return getattr(mt, key, None)

        return {
            "target":        target,
            "model_id":      getattr(result, "model_id", None),
            "data_count":    getattr(result, "data_count", None),
            "race_count":    getattr(result, "race_count", None),
            "feature_count": getattr(result, "feature_count", None),
            "auc":           _mget("auc"),
            "cv_auc_mean":   _mget("cv_auc_mean"),
            "cv_auc_std":    _mget("cv_auc_std"),
            "logloss":       _mget("logloss"),
            "logloss_cal":   _mget("logloss_calibrated"),
            "top1_accuracy": _mget("top1_accuracy"),
        }
    except Exception as e:
        _log(f"  学習エラー ({target}): {e}")
        import traceback; traceback.print_exc()
        return {}


# ─── STEP C: 評価レポート ─────────────────────────────────────────

def step_report(results: list[dict]) -> None:
    _log("=" * 60)
    _log("STEP C: 評価レポート")
    _log("=" * 60)

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    report_path = REPORTS_DIR / f"eval_1year_report_{ts}.md"

    lines = [
        "# 1年間評価レポート (2025-01 〜 2026-03)",
        f"\n生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 1. 学習結果サマリー",
        "",
        "| target | データ数 | レース数 | 特徴量数 | AUC | CV AUC | CV std | LogLoss | LogLoss(cal) |",
        "|--------|---------|---------|---------|-----|--------|--------|---------|-------------|",
    ]

    def _fmt(v, fmt=".4f"):
        if v is None:
            return "N/A"
        try:
            return format(float(v), fmt)
        except Exception:
            return str(v)

    for r in results:
        lines.append(
            f"| {r.get('target','?')} "
            f"| {r.get('data_count','?')} "
            f"| {r.get('race_count','?')} "
            f"| {r.get('feature_count','?')} "
            f"| {_fmt(r.get('auc'))} "
            f"| {_fmt(r.get('cv_auc_mean'))} "
            f"| {_fmt(r.get('cv_auc_std'))} "
            f"| {_fmt(r.get('logloss'))} "
            f"| {_fmt(r.get('logloss_cal'))} |"
        )

    lines += [
        "",
        "## 2. AUC 解釈ガイド",
        "",
        "| AUC 範囲 | 解釈 |",
        "|---------|-----|",
        "| < 0.50  | ランダム以下（要調査） |",
        "| 0.50    | ランダム（予測力なし） |",
        "| 0.55–0.65 | 弱い（実用以下） |",
        "| 0.65–0.75 | 実用レベル（競馬では十分） |",
        "| 0.75–0.85 | 強い予測力 |",
        "| > 0.85  | **過学習またはデータリークを疑う** |",
        "",
        "## 3. 現状の課題",
        "",
        "### 3-1. データ不足（スクレイプ前）",
        "- 2025-01〜2026-03 の scraped_dates が 9 日のみ（うち 2025-01〜04 は 0 日）",
        "- 2025-05 の 4 日分と 2026-04〜05 の 5 日分のみ存在",
        "- このパイプラインで 2025-01-01〜2026-03-31 を取得",
        "",
        "### 3-2. 特徴量ドリフト（スクレイプ後の学習で解消）",
        "- win モデル: catalog 160 特徴量のうち 55 件がモデル未反映",
        "- speed_deviation: 53 件未反映",
        "- → このパイプラインの再学習で 160 特徴量を適用",
        "",
        "### 3-3. win モデルの過学習疑い",
        "- 旧モデル: テスト AUC 0.844 >> CV AUC 0.739（乖離 0.105）",
        "- データ 4752 行 / 408 レースは少ない",
        "- 時系列分割でテストセットが直近レースのみ → 季節性パターンを学習した可能性",
        "- → 1年間データでの再評価が必要",
        "",
        "### 3-4. speed_deviation の 2024-2026 小サンプル問題",
        "- 2024-01〜2026-02 で 2852 行 / 253 レース → CV AUC 0.282（ランダム以下）",
        "- 2016-01〜2026-03 で 34273 行 / 3011 レース → AUC 0.739",
        "- 1年間データ（約 2000 行想定）では過去と比較して不安定になる可能性",
        "",
        "### 3-5. place3 モデル未実装",
        "- 複勝予測モデルが存在しない",
        "",
        "## 4. 推奨アクション（優先順位順）",
        "",
        "| # | アクション | 根拠 |",
        "|---|----------|-----|",
        "| 1 | 2025-01〜2026-03 スクレイプ完了 ← **このパイプライン** | データ不足解消 |",
        "| 2 | 160 特徴量で win/speed_deviation 再学習 ← **このパイプライン** | drift 解消 |",
        "| 3 | win AUC ギャップ調査（FUTURE_FIELDS 再確認） | データリーク排除 |",
        "| 4 | speed_deviation 学習範囲 2016-2026 に固定 | AUC 0.74 安定化 |",
        "| 5 | place3 モデル追加 | 買い目の幅拡大 |",
        "",
        "## 5. ログ",
        "",
    ] + _LOG

    report_path.write_text("\n".join(lines), encoding="utf-8")
    _log(f"\n評価レポート保存: {report_path}")

    # JSON
    json_path = REPORTS_DIR / f"eval_1year_metrics_{ts}.json"
    json_path.write_text(
        json.dumps({"results": results, "date_range": {"from": DATE_FROM, "to": DATE_TO},
                    "generated_at": datetime.now().isoformat()},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _log(f"メトリクス JSON 保存: {json_path}")


# ─── メイン ──────────────────────────────────────────────────────

async def _main_async(args) -> None:
    _log("=" * 60)
    _log("1年間評価パイプライン（直接呼び出し版）開始")
    _log(f"  対象期間  : {DATE_FROM} 〜 {DATE_TO}")
    _log(f"  ターゲット: {args.targets}")
    _log(f"  Optuna 試行数: {args.optuna_trials}")
    _log("=" * 60)

    _log("\n--- DB 現状確認 ---")
    db_info = check_db()

    # STEP A: スクレイプ
    if not args.skip_scrape:
        scrape_ok = await step_scrape(force_rescrape=args.force_rescrape)
        _log(f"\nスクレイプ: {'OK' if scrape_ok else 'NG (続行)'}")
        _log("\n--- スクレイプ後 DB 確認 ---")
        check_db()
    else:
        _log("\n(スクレイプスキップ)")

    # STEP B: 学習
    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    results = []
    for target in targets:
        m = await step_train(target, optuna_trials=args.optuna_trials)
        if m:
            results.append(m)

    # STEP C: レポート
    step_report(results)

    _log("\n" + "=" * 60)
    _log("パイプライン完了")
    _log("=" * 60)
    _save_log()


def main() -> None:
    parser = argparse.ArgumentParser(description="1年間評価パイプライン（直接呼び出し版）")
    parser.add_argument("--skip-scrape",    action="store_true")
    parser.add_argument("--force-rescrape", action="store_true")
    parser.add_argument("--targets",        default="win,speed_deviation")
    parser.add_argument("--optuna-trials",  type=int, default=30)
    args = parser.parse_args()

    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
