"""Preserve the unreleased overstrict audit and initialize its corrected revision.

No outcome metrics have been used to change eligibility. Original policy permits
settled nonwinner ties; duplicate horse identity and duplicate rank are different.
"""
from pathlib import Path
import json
import hashlib
import shutil

ROOT = Path(__file__).resolve().parents[1]


def main():
    base = ROOT / "reports/generated/data-quality"
    original = base / "market-h04-clean-v1"
    revised = base / "market-h04-clean-v2"
    revised.mkdir(exist_ok=False)
    files = ["audit_plan.json", "lineage.json", "lineage_review_addendum.json", "history_source_audit.json"]
    for name in files:
        shutil.copyfile(original / name, revised / name)
    for name in ["acquisition", "missingness"]:
        shutil.copytree(original / name, revised / name)
    note = {"schema": "market-quality-audit-correction-v1", "original_unreleased_root": str(original),
        "corrected_root": str(revised), "reason": "Auditor mistakenly treated legitimate nonwinner dead heats and tied popularity as duplicate horse records",
        "original_policy_changed": False, "model_metrics_used_for_change": False,
        "source_data_modified": False, "all_original_races_obey_competition_min_rank_pattern": True,
        "original_false_exclusions": {"finish_rank_duplicate": 139, "popularity_rank_duplicate": 17, "union": 155},
        "verification_required": "Reaudit exact runner identity, competition rank sequence and equal odds for tied popularity; frozen single-winner payout requirement unchanged",
        "old_summary_not_authorized_for_phase2": True,
        "old_quality_summary_sha256": hashlib.sha256((original / "quality_summary.json").read_bytes()).hexdigest()}
    with (revised / "audit_correction.json").open("x", encoding="utf-8") as stream:
        json.dump(note, stream, ensure_ascii=False, indent=2)
    with (ROOT / "reports/generated/model-experiments/market-quality-audit-v1/unreleased-v1-correction.json").open("x", encoding="utf-8") as stream:
        json.dump(note, stream, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
