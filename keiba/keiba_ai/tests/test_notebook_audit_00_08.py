from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class AuditIssue:
    notebook: str
    cause: str
    fix_summary: str
    fix_code: str
    prevention: str


def _repo_root() -> Path:
    # .../keiba/keiba_ai/tests/test_notebook_audit_00_08.py -> repo root
    return Path(__file__).resolve().parents[3]


def _notebook_paths(root: Path) -> list[Path]:
    nb_dir = root / "notebooks"
    return [
        nb_dir / "00_config.ipynb",
        nb_dir / "01_data_collection.ipynb",
        nb_dir / "02_data_validation.ipynb",
        nb_dir / "03_feature_engineering.ipynb",
        nb_dir / "04_feature_analysis.ipynb",
        nb_dir / "05_model_training.ipynb",
        nb_dir / "06_prediction.ipynb",
        nb_dir / "07_evaluation.ipynb",
        nb_dir / "08_reporting.ipynb",
    ]


def _execute_notebook(path: Path) -> tuple[bool, str]:
    try:
        import nbformat
        from nbclient import NotebookClient
    except Exception as e:  # pragma: no cover
        return False, (
            "Notebook execution dependencies unavailable: "
            f"{e}. Install with: python -m pip install nbformat nbclient ipykernel"
        )

    try:
        nb = nbformat.read(path, as_version=4)
        client = NotebookClient(
            nb,
            timeout=1800,
            kernel_name="python3",
            allow_errors=False,
            resources={"metadata": {"path": str(path.parent)}},
        )
        client.execute()
        nbformat.write(nb, path)
        return True, "OK"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _apply_known_fix_for_reporting_notebook(root: Path) -> tuple[bool, str]:
    target = root / "notebooks" / "08_reporting.ipynb"
    if not target.exists():
        return False, "08_reporting.ipynb not found"

    src = target.read_text(encoding="utf-8")
    old = "clean = re.sub(r'\\*(.*?)\\*', clean, clean)"
    new = "clean = re.sub(r'\\*(.*?)\\*', r'\\1', clean)"
    if old not in src:
        return False, "no known pattern"

    target.write_text(src.replace(old, new), encoding="utf-8")
    return True, "fixed markdown cleanup regex replacement"


def _find_reports_dir(root: Path) -> Path:
    candidates = [root / "notebooks" / "reports", root / "reports"]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def _artifact_status(reports_dir: Path) -> dict[str, bool]:
    root = reports_dir.parent.parent if reports_dir.name == "reports" else reports_dir.parent
    feature_analysis_candidates = [
        reports_dir / "feature_analysis.json",
        root / "notebooks" / "data" / "feature_store" / "feature_analysis.json",
    ]
    return {
        "feature_analysis.json": any(p.exists() for p in feature_analysis_candidates),
        "prediction.csv": (reports_dir / "prediction.csv").exists(),
        "roi_report.csv": (reports_dir / "roi_report.csv").exists(),
        "feature_llm_report.md": (reports_dir / "feature_llm_report.md").exists(),
        "calibration.png": (reports_dir / "calibration.png").exists(),
        "roi_cumulative.png": (reports_dir / "roi_cumulative.png").exists(),
    }


def _write_audit_report_md(root: Path, summary: dict[str, Any], execution_results: dict[str, bool]) -> None:
    p = root / "audit_report.md"
    lines: list[str] = []
    lines.append("# Notebook Audit Report")
    lines.append("")
    lines.append("対象Notebook: 00_config.ipynb 〜 08_reporting.ipynb")
    lines.append("")
    lines.append("## Notebook 実行結果")
    lines.append("")
    for name in [
        "00_config.ipynb",
        "01_data_collection.ipynb",
        "02_data_validation.ipynb",
        "03_feature_engineering.ipynb",
        "04_feature_analysis.ipynb",
        "05_model_training.ipynb",
        "06_prediction.ipynb",
        "07_evaluation.ipynb",
        "08_reporting.ipynb",
    ]:
        lines.append(f"- {name}: {'OK' if execution_results.get(name, False) else 'FAILED'}")
    lines.append("")
    lines.append("## 最終判定")
    lines.append("")
    lines.append(f"- 学習成功: {'YES' if summary['train_ok'] else 'NO'}")
    lines.append(f"- 推論成功: {'YES' if summary['pred_ok'] else 'NO'}")
    lines.append(f"- 評価成功: {'YES' if summary['eval_ok'] else 'NO'}")
    lines.append(f"- ROI計算成功: {'YES' if summary['roi_ok'] else 'NO'}")
    lines.append(f"- レポート生成成功: {'YES' if summary['report_ok'] else 'NO'}")
    lines.append("")
    lines.append("## 成果物")
    lines.append("")
    for k, v in summary["artifacts"].items():
        lines.append(f"- {k}: {'OK' if v else 'MISSING'}")
    lines.append("")
    p.write_text("\n".join(lines), encoding="utf-8")


def _write_issues_md(root: Path, issues: list[AuditIssue], summary: dict[str, Any]) -> None:
    p = root / "issues.md"
    lines: list[str] = []
    lines.append("# Notebook Audit Issues")
    lines.append("")
    lines.append("対象: 00_config.ipynb 〜 08_reporting.ipynb")
    lines.append("")
    lines.append("## 最終ステータス")
    lines.append("")
    lines.append(f"- 学習成功: {'YES' if summary['train_ok'] else 'NO'}")
    lines.append(f"- 推論成功: {'YES' if summary['pred_ok'] else 'NO'}")
    lines.append(f"- 評価成功: {'YES' if summary['eval_ok'] else 'NO'}")
    lines.append(f"- ROI計算成功: {'YES' if summary['roi_ok'] else 'NO'}")
    lines.append(f"- レポート生成成功: {'YES' if summary['report_ok'] else 'NO'}")
    lines.append("")
    lines.append("## 生成物確認")
    lines.append("")
    for k, v in summary["artifacts"].items():
        lines.append(f"- {k}: {'OK' if v else 'MISSING'}")
    lines.append("")

    if not issues:
        lines.append("## Issue 一覧")
        lines.append("")
        lines.append("Issue は検出されませんでした。")
    else:
        lines.append("## Issue 一覧")
        lines.append("")
        for i, issue in enumerate(issues, start=1):
            lines.append(f"### Issue {i}")
            lines.append("")
            lines.append(f"- 発生Notebook: {issue.notebook}")
            lines.append(f"- 原因: {issue.cause}")
            lines.append(f"- 修正内容: {issue.fix_summary}")
            lines.append("- 修正コード:")
            lines.append("")
            lines.append("```text")
            lines.append(issue.fix_code)
            lines.append("```")
            lines.append(f"- 再発防止策: {issue.prevention}")
            lines.append("")

    p.write_text("\n".join(lines), encoding="utf-8")


def test_notebook_audit_00_to_08() -> None:
    root = _repo_root()
    notebooks = _notebook_paths(root)
    issues: list[AuditIssue] = []
    execution_results: dict[str, bool] = {}

    # Pass 1: execute all notebooks in order and continue on errors.
    failed: list[tuple[Path, str]] = []
    for nb in notebooks:
        if not nb.exists():
            msg = "Notebook file not found"
            failed.append((nb, msg))
            issues.append(
                AuditIssue(
                    notebook=nb.name,
                    cause=msg,
                    fix_summary="監査対象に存在しないため実行不可",
                    fix_code=f"missing file: {nb}",
                    prevention="Notebook 命名規則と配置の事前チェックを CI に追加する",
                )
            )
            execution_results[nb.name] = False
            continue

        ok, detail = _execute_notebook(nb)
        execution_results[nb.name] = ok
        if not ok:
            failed.append((nb, detail))
            issues.append(
                AuditIssue(
                    notebook=nb.name,
                    cause=detail,
                    fix_summary="初回実行で失敗。既知パターン修正を試行",
                    fix_code=detail,
                    prevention="Notebook を変更した際は 00→08 の通し実行を必須化する",
                )
            )

    # Apply known fix and rerun only failed notebooks.
    changed, changed_msg = _apply_known_fix_for_reporting_notebook(root)
    if changed:
        issues.append(
            AuditIssue(
                notebook="08_reporting.ipynb",
                cause="Markdown 正規化処理で re.sub の置換引数が誤っていた",
                fix_summary="置換式を backreference 形式に修正",
                fix_code="clean = re.sub(r'\\*(.*?)\\*', r'\\1', clean)",
                prevention="文字列置換ヘルパーのユニットテストを追加する",
            )
        )

    rerun_failed: list[tuple[Path, str]] = []
    for nb, _ in failed:
        if not nb.exists():
            continue
        ok, detail = _execute_notebook(nb)
        execution_results[nb.name] = ok
        if not ok:
            rerun_failed.append((nb, detail))
            issues.append(
                AuditIssue(
                    notebook=nb.name,
                    cause=detail,
                    fix_summary="再実行でも失敗。追加調査が必要",
                    fix_code=detail,
                    prevention="失敗ログを notebook 別に保存し、失敗時に即座に再現できるようにする",
                )
            )

    reports_dir = _find_reports_dir(root)
    artifacts = _artifact_status(reports_dir)

    train_ok = execution_results.get("05_model_training.ipynb", False)
    pred_ok = execution_results.get("06_prediction.ipynb", False)
    eval_ok = execution_results.get("07_evaluation.ipynb", False)
    roi_ok = artifacts.get("roi_report.csv", False)
    report_ok = execution_results.get("08_reporting.ipynb", False) and artifacts.get(
        "feature_llm_report.md", False
    )

    summary = {
        "train_ok": train_ok,
        "pred_ok": pred_ok,
        "eval_ok": eval_ok,
        "roi_ok": roi_ok,
        "report_ok": report_ok,
        "artifacts": artifacts,
        "report_dir": str(reports_dir),
        "changed_fix": changed_msg,
    }
    _write_issues_md(root, issues, summary)
    _write_audit_report_md(root, summary, execution_results)

    # Success criteria requested by user.
    assert train_ok, "学習成功条件を満たしていません。issues.md を確認してください。"
    assert pred_ok, "推論成功条件を満たしていません。issues.md を確認してください。"
    assert eval_ok, "評価成功条件を満たしていません。issues.md を確認してください。"
    assert roi_ok, "ROI計算成功条件を満たしていません。issues.md を確認してください。"
    assert report_ok, "レポート生成成功条件を満たしていません。issues.md を確認してください。"
    for name, ok in artifacts.items():
        assert ok, f"成果物 {name} が未生成です。issues.md を確認してください。"
