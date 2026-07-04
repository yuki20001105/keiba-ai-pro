from __future__ import annotations

from pathlib import Path

from scripts.validate_artifacts import REQUIRED_ARTIFACTS, resolve_paths


def test_resolve_paths_uses_feature_store_for_feature_analysis(tmp_path: Path) -> None:
    root = tmp_path
    (root / "notebooks" / "reports").mkdir(parents=True)
    (root / "notebooks" / "data" / "feature_store").mkdir(parents=True)

    p = root / "notebooks" / "data" / "feature_store" / "feature_analysis.json"
    p.write_text("{}", encoding="utf-8")

    paths = resolve_paths(root)
    assert paths["feature_analysis.json"] == p


def test_required_artifacts_constant() -> None:
    expected = {
        "feature_analysis.json",
        "prediction.csv",
        "roi_report.csv",
        "feature_llm_report.md",
        "calibration.png",
        "roi_cumulative.png",
    }
    assert set(REQUIRED_ARTIFACTS) == expected
