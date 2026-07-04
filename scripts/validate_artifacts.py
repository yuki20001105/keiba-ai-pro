from __future__ import annotations

import argparse
import json
from pathlib import Path


REQUIRED_ARTIFACTS = (
    "feature_analysis.json",
    "prediction.csv",
    "roi_report.csv",
    "feature_llm_report.md",
    "calibration.png",
    "roi_cumulative.png",
)


def resolve_paths(root: Path) -> dict[str, Path]:
    reports = root / "notebooks" / "reports"
    feature_store = root / "notebooks" / "data" / "feature_store"
    out: dict[str, Path] = {}
    for name in REQUIRED_ARTIFACTS:
        p = reports / name
        if name == "feature_analysis.json" and not p.exists():
            p = feature_store / name
        out[name] = p
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    root = args.root.resolve()
    report_dir = root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    paths = resolve_paths(root)
    status = {name: p.exists() for name, p in paths.items()}

    lines = ["# artifact_validation", "", "| artifact | exists | path |", "|---|---|---|"]
    for name in REQUIRED_ARTIFACTS:
        p = paths[name]
        lines.append(f"| {name} | {'YES' if status[name] else 'NO'} | {p.as_posix()} |")

    md_path = report_dir / "artifact_validation_report.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    js_path = report_dir / "artifact_validation.json"
    js_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.out is not None:
        args.out.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")

    return 0 if all(status.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
