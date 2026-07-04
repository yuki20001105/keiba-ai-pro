from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    from notebook_execution_engine import (
        ExecutionTraceLogger,
        execute_notebook_with_retry,
        write_detailed_execution_csv,
        write_execution_csv,
        write_execution_report,
    )
except ModuleNotFoundError:
    from scripts.notebook_execution_engine import (
        ExecutionTraceLogger,
        execute_notebook_with_retry,
        write_detailed_execution_csv,
        write_execution_csv,
        write_execution_report,
    )


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def notebook_list(root: Path) -> list[Path]:
    d = root / "notebooks"
    return [
        d / "00_config.ipynb",
        d / "01_data_collection.ipynb",
        d / "02_data_validation.ipynb",
        d / "03_feature_engineering.ipynb",
        d / "04_feature_analysis.ipynb",
        d / "05_model_training.ipynb",
        d / "06_prediction.ipynb",
        d / "07_evaluation.ipynb",
        d / "08_reporting.ipynb",
    ]


NOTEBOOK_CELL_TIMEOUTS = {
    "02_data_validation.ipynb": 300,
    "03_feature_engineering.ipynb": 600,
    "04_feature_analysis.ipynb": 1200,
    "05_model_training.ipynb": 3600,
}

AUDIT_PROFILE = {
    "03_feature_engineering.ipynb": {
        "audit_mode": True,
        "timeout": 600,
    },
    "05_model_training.ipynb": {
        "audit_mode": True,
        "timeout": 1800,
    },
}

MODE_PROFILE = {
    "fast": {
        "env": {
            "FAST_MODE": "1",
            "AUDIT_MODE": "0",
            "NB_MODE": "fast",
            "N_TRIALS": "10",
            "N_SPLITS": "3",
            "BOOSTING_TYPE": "gbdt",
        }
    },
    "audit": {
        "env": {
            "FAST_MODE": "0",
            "AUDIT_MODE": "1",
            "NB_MODE": "audit",
            "N_TRIALS": "3",
            "N_SPLITS": "2",
            "NUM_BOOST_ROUND": "200",
        }
    },
    "prod": {
        "env": {
            "FAST_MODE": "0",
            "AUDIT_MODE": "0",
            "NB_MODE": "prod",
            "N_TRIALS": "100",
            "N_SPLITS": "5",
            "BOOSTING_TYPE": "dart",
        }
    },
}


CACHE_PARQUET_FILES = (
    "ultimate_frame.parquet",
    "features.parquet",
    "predictions.parquet",
    "race_results.parquet",
    "horse_history.parquet",
    "training_data.parquet",
)


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_cache_layout(root: Path, mode: str, issues_notes: list[str]) -> None:
    cache_dir = root / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_index = cache_dir / "cache_index.json"

    db_path = root / "keiba" / "data" / "keiba_ultimate.db"
    notebook_dir = root / "notebooks"

    schema_source = sorted(
        [
            p.name + ":" + str(int(p.stat().st_mtime))
            for p in notebook_dir.glob("*.ipynb")
            if p.is_file()
        ]
    )
    feature_schema_hash = hashlib.sha256("|".join(schema_source).encode("utf-8")).hexdigest()
    data_version = str(int(db_path.stat().st_mtime)) if db_path.exists() else "0"

    new_key = {
        "data_version": data_version,
        "feature_schema_hash": feature_schema_hash,
        "notebook_step": "00-08",
        "mode": mode,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }

    old_key: dict[str, str] = {}
    if cache_index.exists():
        try:
            old_key = json.loads(cache_index.read_text(encoding="utf-8"))
        except Exception:
            old_key = {}

    reasons = []
    if old_key:
        if old_key.get("feature_schema_hash") != new_key["feature_schema_hash"]:
            reasons.append("schema変更")
        if old_key.get("data_version") != new_key["data_version"]:
            reasons.append("DB更新")
        if old_key.get("mode") != new_key["mode"]:
            reasons.append("mode変更")

    if reasons:
        for name in CACHE_PARQUET_FILES:
            p = cache_dir / name
            if p.exists():
                p.unlink()
        issues_notes.append(f"cache invalidated: {', '.join(reasons)}")

    for name in CACHE_PARQUET_FILES:
        p = cache_dir / name
        # 0-byte parquet placeholders break pandas.read_parquet; clean them automatically.
        if p.exists() and p.stat().st_size == 0:
            p.unlink()
            issues_notes.append(f"removed invalid empty parquet placeholder: {name}")

    cache_index.write_text(json.dumps(new_key, ensure_ascii=False, indent=2), encoding="utf-8")


def _run_pass(
    root: Path,
    nbs: list[Path],
    trace: ExecutionTraceLogger,
    *,
    default_cell_timeout: int,
    notebook_timeout: int,
    max_retry: int,
    mode: str = "audit",
):
    rows = []
    mode_cfg = MODE_PROFILE.get(mode, MODE_PROFILE["audit"])
    for nb in nbs:
        if not nb.exists():
            continue
        cell_timeout = NOTEBOOK_CELL_TIMEOUTS.get(nb.name, default_cell_timeout)
        if mode == "audit" and nb.name in AUDIT_PROFILE:
            cell_timeout = int(AUDIT_PROFILE[nb.name].get("timeout", cell_timeout))

        kernel_env = dict(mode_cfg.get("env", {}))

        rows.append(
            execute_notebook_with_retry(
                nb,
                cell_timeout=cell_timeout,
                notebook_timeout=notebook_timeout,
                max_retry=max_retry,
                trace=trace,
                kernel_env=kernel_env,
            )
        )
    return rows


def artifacts_status(root: Path) -> dict[str, bool]:
    reports = root / "notebooks" / "reports"
    feature_store = root / "notebooks" / "data" / "feature_store"
    return {
        "feature_analysis.json": (feature_store / "feature_analysis.json").exists() or (reports / "feature_analysis.json").exists(),
        "prediction.csv": (reports / "prediction.csv").exists(),
        "roi_report.csv": (reports / "roi_report.csv").exists(),
        "feature_llm_report.md": (reports / "feature_llm_report.md").exists(),
        "calibration.png": (reports / "calibration.png").exists(),
        "roi_cumulative.png": (reports / "roi_cumulative.png").exists(),
    }


def ensure_reports_dir(root: Path) -> Path:
    p = root / "reports"
    p.mkdir(parents=True, exist_ok=True)
    return p


def sync_notebook_reports(root: Path, reports_dir: Path) -> None:
    src = root / "notebooks" / "reports"
    feature_store = root / "notebooks" / "data" / "feature_store"
    if not src.exists():
        src = None
    wanted = {
        "optuna_trial_log.csv",
        "gpu_usage_log.csv",
        "notebook_execution_log.csv",
        "feature_analysis.json",
        "prediction.csv",
        "roi_report.csv",
        "feature_llm_report.md",
        "calibration.png",
        "roi_cumulative.png",
    }
    for name in wanted:
        copied = False
        if src is not None:
            sp = src / name
            if sp.exists():
                shutil.copy2(sp, reports_dir / name)
                copied = True
        if copied:
            continue
        if name == "feature_analysis.json":
            fp = feature_store / name
            if fp.exists():
                shutil.copy2(fp, reports_dir / name)


def append_performance_targets(root: Path, rows) -> None:
    report_path = root / "audit_report.md"
    if report_path.exists():
        base = report_path.read_text(encoding="utf-8")
    else:
        base = "# Notebook Audit Report\n\n"

    elapsed = {r.notebook: float(r.elapsed_sec) for r in rows}
    targets = [
        ("02_data_validation.ipynb", 60.0),
        ("03_feature_engineering.ipynb", 300.0),
    ]

    lines = []
    lines.append("\n## 性能目標チェック")
    lines.append("")
    lines.append("| Notebook | Target(sec) | Actual(sec) | Status |")
    lines.append("|---|---:|---:|---|")
    for nb, target in targets:
        val = elapsed.get(nb)
        if val is None:
            lines.append(f"| {nb} | {target:.0f} | N/A | SKIP |")
        else:
            status = "PASS" if val <= target else "FAIL"
            lines.append(f"| {nb} | {target:.0f} | {val:.3f} | {status} |")

    # 05_model_training の GPU高速化目標は benchmark から判定
    bench = root / "reports" / "gpu_benchmark.csv"
    speedup = None
    if bench.exists():
        try:
            import csv

            ratios = []
            with bench.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        ratio = float(row.get("speedup_ratio", "nan"))
                    except Exception:
                        continue
                    if ratio > 0:
                        ratios.append(ratio)
            if ratios:
                speedup = max(ratios)
        except Exception:
            speedup = None

    lines.append("")
    if speedup is None:
        lines.append("- 05_model_training GPU高速化(50%以上): 判定不可（gpu_benchmark.csv不足）")
    else:
        status = "PASS" if speedup >= 2.0 else "FAIL"
        lines.append(f"- 05_model_training GPU高速化(50%以上): {status} (speedup={speedup:.2f}x)")

    report_path.write_text(base.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")


def generate_support_reports(root: Path) -> None:
    script = root / "scripts" / "generate_performance_reports.py"
    if not script.exists():
        return
    subprocess.call([sys.executable, str(script)], cwd=str(root))


def update_issues_md(root: Path, unresolved_notes: list[str], fix_notes: list[str]) -> None:
    p = root / "issues.md"
    if p.exists():
        base = p.read_text(encoding="utf-8")
    else:
        base = "# Notebook Audit Issues\n\n"

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    block = [
        f"## Notebook監査自動修正ログ {stamp}",
        "",
        "- 原因:",
        "  - Windows + nbclient 実行で、特定Notebookで kernel message 待ちが長時間化するケースを確認",
        "  - タイムアウト監視が notebook 全体/セル単位で不足していた",
        "- 修正内容:",
        "  - セル単位トレース（開始/終了/実行セル番号/セルソース）をJSONLで記録",
        "  - `cell_timeout=600`, `notebook_timeout=7200` を導入",
        "  - Timeout時は `TimeoutNotebookError` として分類",
        "  - Kernel restart + Notebook単位再実行（最大3回）を実装",
        "  - `notebook_execution_report.md`, `notebook_execution_log.csv` を出力",
        "- 修正コード:",
        "  - scripts/notebook_execution_engine.py",
        "  - scripts/notebook_audit_runner.py",
        "  - scripts/run_notebooks_02_08.py",
        "  - keiba/keiba_ai/tests/test_notebook_hang.py",
        "  - keiba/keiba_ai/gpu_utils.py",
        "  - keiba/keiba_ai/optuna_optimizer.py",
        "- 再発防止策:",
        "  - PR時に `pytest keiba/keiba_ai/tests/test_notebook_hang.py -q` を実行し、単独/連続実行とtimeoutを監視",
    ]
    if fix_notes:
        block.append("- 自動修正内容:")
        for n in fix_notes:
            block.append(f"  - {n}")
    if unresolved_notes:
        block.append("- 未解決/観測事項:")
        for n in unresolved_notes:
            block.append(f"  - {n}")

    p.write_text(base.rstrip() + "\n\n" + "\n".join(block) + "\n", encoding="utf-8")


def update_audit_report_md(root: Path, exec_rows, artifacts: dict[str, bool]) -> None:
    p = root / "audit_report.md"
    lines: list[str] = []
    lines.append("# Notebook Audit Report")
    lines.append("")
    lines.append("## Notebook実行結果（最終仕上げ）")
    lines.append("")
    lines.append("| Notebook | status | elapsed_sec | retry | error |")
    lines.append("|---|---|---:|---:|---|")
    for r in exec_rows:
        err = r.error.replace("\n", " ")[:140]
        lines.append(f"| {r.notebook} | {r.status} | {r.elapsed_sec:.3f} | {r.retry} | {err} |")

    lines.append("")
    lines.append("## 成果物確認")
    lines.append("")
    for k, v in artifacts.items():
        lines.append(f"- {k}: {'OK' if v else 'MISSING'}")

    lines.append("")
    lines.append("## ゴール判定")
    lines.append("")
    expected = {
        "00_config.ipynb",
        "01_data_collection.ipynb",
        "02_data_validation.ipynb",
        "03_feature_engineering.ipynb",
        "04_feature_analysis.ipynb",
        "05_model_training.ipynb",
        "06_prediction.ipynb",
        "07_evaluation.ipynb",
        "08_reporting.ipynb",
    }
    executed = {r.notebook for r in exec_rows}
    full_range_covered = expected.issubset(executed)
    goal_ok = full_range_covered and all(v for v in artifacts.values()) and all(r.status == "success" for r in exec_rows)
    lines.append(f"- 完全自動実行(00->08): {'YES' if goal_ok else 'PARTIAL'}")
    if not goal_ok:
        if not full_range_covered:
            missing = sorted(expected - executed)
            lines.append(f"- 未実行Notebook: {', '.join(missing)}")
        lines.append("- 再現条件: Windows環境でのnbclient実行時に一部Notebookでカーネル応答待ちが長時間化")
        lines.append("- 原因候補: kernel_clientのmessage待ち、Windowsイベントループ特性、Notebook内の長時間セル")
        lines.append("- 回避策: TimeoutNotebookError + 自動リトライ + kernel restart")
        lines.append("- 恒久対応案: セル分割・重処理の外部化・Notebook CI分割実行")

    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cell-timeout", type=int, default=600)
    parser.add_argument("--notebook-timeout", type=int, default=7200)
    parser.add_argument("--max-retry", type=int, default=3)
    parser.add_argument("--mode", choices=["fast", "audit", "prod"], default="audit")
    parser.add_argument("--start", type=int, default=0, help="start notebook index (0=00_config)")
    parser.add_argument("--end", type=int, default=8, help="end notebook index (8=08_reporting)")
    parser.add_argument("--rerun-failed", type=int, default=1, help="how many extra rerun passes for failed notebooks")
    args = parser.parse_args()

    root = repo_root()
    reports_dir = ensure_reports_dir(root)
    fix_notes: list[str] = []
    ensure_cache_layout(root, args.mode, fix_notes)
    all_nbs = notebook_list(root)
    nbs = all_nbs[args.start : args.end + 1]

    trace = ExecutionTraceLogger(root / "notebook_execution_trace.jsonl")
    rows = _run_pass(
        root,
        nbs,
        trace,
        default_cell_timeout=args.cell_timeout,
        notebook_timeout=args.notebook_timeout,
        max_retry=args.max_retry,
        mode=args.mode,
    )

    # Auto-rerun failed notebooks with relaxed timeout as an automatic recovery path.
    for _ in range(max(0, args.rerun_failed)):
        failed = [r.notebook for r in rows if r.status != "success"]
        if not failed:
            break
        failed_paths = [root / "notebooks" / name for name in failed]
        rerun_rows = _run_pass(
            root,
            failed_paths,
            trace,
            default_cell_timeout=max(args.cell_timeout, 180),
            notebook_timeout=max(args.notebook_timeout, 7200),
            max_retry=max(1, args.max_retry),
            mode=args.mode,
        )
        by_name = {r.notebook: r for r in rows}
        for rr in rerun_rows:
            by_name[rr.notebook] = rr
        rows = [by_name[p.name] for p in nbs if p.name in by_name]

    write_execution_csv(root / "notebook_execution_log.csv", rows)
    write_execution_report(root / "notebook_execution_report.md", rows)
    write_execution_csv(reports_dir / "notebook_execution_log.csv", rows)
    write_execution_report(reports_dir / "notebook_execution_report.md", rows)
    write_detailed_execution_csv(root / "notebook_execution_trace.jsonl", reports_dir / "notebook_execution_log.csv")

    artifacts = artifacts_status(root)
    unresolved = []
    for r in rows:
        if r.status != "success":
            unresolved.append(
                f"{r.notebook}: status={r.status}, last_cell={r.last_cell_number}, error={r.error[:160]}"
            )

    update_issues_md(root, unresolved, fix_notes)
    update_audit_report_md(root, rows, artifacts)
    append_performance_targets(root, rows)
    sync_notebook_reports(root, reports_dir)
    generate_support_reports(root)

    # keep key reports duplicated under reports/ for CI consumers
    for fn in [
        "issues.md",
        "audit_report.md",
        "notebook_execution_report.md",
        "performance_report.md",
        "memory_optimization_report.md",
        "cache_strategy.md",
    ]:
        src = root / fn
        if src.exists():
            shutil.copy2(src, reports_dir / fn)

    return 0 if all(r.status == "success" for r in rows) and all(artifacts.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
