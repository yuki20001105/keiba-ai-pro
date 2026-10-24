"""Append-only publication of a frozen, reference-only local NLL wrapper.

No model/calibrator fitting, activation, network access, or source-seal mutation.
The optional --publish flag writes a new wrapper and independent manifest only.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / "python-api", ROOT / "scripts", ROOT / "keiba"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from services import win_nll_reference_input as service

PLAN = ROOT / "reports/generated/win-shadow-v1/registered-plan.json"


def read_plan():
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    seal = json.loads(PLAN.with_name(PLAN.stem + "-manifest.json").read_text(encoding="utf-8"))
    if (seal.get("schema") != "win-shadow-artifact-seal-v1" or seal.get("file") != PLAN.name
            or seal.get("sha256") != service.file_hash(PLAN) or seal.get("bytes") != PLAN.stat().st_size
            or plan.get("candidate_fold") != 2026 or plan.get("candidate_model_retrained") is not False
            or plan.get("models") != ["market_only_calibrated", "expanding_race_nll"]):
        raise ValueError("win-reference-original-shadow-seal-required")
    for item in plan["artifacts"].values():
        path = ROOT / item["path"]
        if service.file_hash(path) != item["sha256"] or path.stat().st_size != item["bytes"]:
            raise ValueError("win-reference-original-study-artifact-changed")
    service._check_sources(plan["source_hashes"])
    service._check_sources(plan["shadow_source_hashes"])
    service._check_sources(plan["aggregation_source_hashes"])
    return plan


def build_package():
    import lightgbm as lgb
    plan = read_plan()
    item = plan["artifacts"]["model"]
    original = service._original_bundle(item["path"], item["sha256"])
    result = json.loads((ROOT / plan["artifacts"]["worker_result"]["path"]).read_text(encoding="utf-8"))
    worker, _features = service.modules()
    columns, categorical = worker.feature_contract("expanding_race_nll")
    runtime_sources = service.runtime_sources()
    frozen_sources = plan["source_hashes"] | plan["shadow_source_hashes"] | plan["aggregation_source_hashes"]
    v3 = json.loads((ROOT / plan["artifacts"]["v3_summary"]["path"]).read_text(encoding="utf-8"))
    precision = v3["evaluation"]["yearly_precision"]["2026"]["expanding_race_nll"]
    test_period = result["partitions"]["TEST"]
    winning_rmse = math.sqrt(precision["brier"] * precision["races"] / precision["runners"])
    evaluation = {"schema": "local-win-nll-reference-evaluation-v1", "target_kind": "win_probability",
        "scope": "known_retrospective_research", "unknown_period_validation": False,
        "rmse_unit": "勝敗0/1", "evaluation_label": "既知期間の事後検証・購入判断の検証ではありません",
        "primary": {"rank_correlation": precision["spearman"], "rmse": winning_rmse,
                    "rmse_unit": "勝敗0/1", "top_pick_win_rate": None, "win_roi": None},
        "details": {"evaluation_date_from": test_period["date_from"], "evaluation_date_to": test_period["date_to"],
                    "evaluation_race_count": precision["races"], "nll": precision["nll"],
                    "brier": precision["brier"], "top1_win_rate": precision["top1"]},
        "extra": {"nll": precision["nll"], "brier": precision["brier"]},
        "validation": {"date_from": test_period["date_from"], "date_to": test_period["date_to"],
                       "race_count": precision["races"], "row_count": precision["runners"]},
        "rmse_definition": "sqrt(mean_race_sum_horse_brier * race_count / runner_count)",
        "source_sha256": plan["artifacts"]["v3_summary"]["sha256"]}
    wrapper = {"schema": service.SCHEMA, "target": "win", "model_type": "LightGBM",
        "display_name": "全履歴・勝率NLL（参考運用）", "evaluation": evaluation,
        "feature_pipeline": service.PIPELINE, "_win_nll_reference": True, "reference_only": True,
        "purchase_authorized": False, "ultimate_mode": True,
        "created_at": datetime.now(timezone.utc).isoformat(), "model": lgb.Booster(model_str=original["model_text"]),
        "feature_columns": columns, "categorical_columns": categorical,
        "source_bundle_path": item["path"], "source_bundle_sha256": item["sha256"],
        "model_text_sha256": original["model_text_sha256"], "encoder_sha256": original.get("encoder_sha256"),
        "frozen_source_hashes": frozen_sources, "runtime_source_hashes": runtime_sources,
        "conditioned_calibration": original["conditioned_calibration"], "calibrator": None,
        "probability_validation": service.probability_validation(),
        "training_date_from": "20130101", "training_date_to": "20241231",
        "calibration_date_from": "20250101", "calibration_date_to": "20251231",
        "dataset_period_end": "20260913", "feature_count": 50,
        "data_count": result["partitions"]["TRAIN"]["runner_count"],
        "race_count": result["partitions"]["TRAIN"]["race_count"],
        "publication_is_retraining": False, "new_fits": 0,
        "source_evaluation": {"kind": "known_retrospective_research", "live_validation": False,
            "source_result_path": plan["artifacts"]["worker_result"]["path"],
            "source_result_sha256": plan["artifacts"]["worker_result"]["sha256"],
            "v3_results_sha256": plan["artifacts"]["v3_summary"]["sha256"]}}
    service.verify_bundle_sources(wrapper)
    original_artifacts = {name: {"path": plan["artifacts"][key]["path"], "sha256": plan["artifacts"][key]["sha256"]}
        for name, key in (("model", "model"), ("worker_result", "worker_result"), ("worker_request", "worker_request"), ("v3_results", "v3_summary"))}
    original_artifacts["shadow_plan"] = {"path": PLAN.relative_to(ROOT).as_posix(), "sha256": service.file_hash(PLAN)}
    manifest = {"schema": service.SCHEMA, "model_id": Path(service.MODEL_FILENAME).stem,
        "reference_only": True, "purchase_authorized": False, "new_fits": 0,
        "original_artifacts": original_artifacts, "frozen_source_hashes": frozen_sources,
        "runtime_source_hashes": runtime_sources}
    return wrapper, manifest


def publish():
    from app_config import APP_ENV, MODELS_DIR
    if APP_ENV not in {"local", "development", "test"}:
        raise ValueError("win-reference-local-only")
    path = MODELS_DIR / service.MODEL_FILENAME
    sidecar = service.manifest_path(path)
    if path.exists() or sidecar.exists() or path.is_symlink() or sidecar.is_symlink():
        raise FileExistsError("win-reference-append-only-package-already-exists")
    wrapper, manifest = build_package()
    import joblib
    temporary = path.with_name(".reference-publish-" + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            joblib.dump(wrapper, stream)
        manifest["wrapper_sha256"] = service.file_hash(temporary)
        os.link(temporary, path)  # exclusive publication, never replacement
        with sidecar.open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        service.load_published_bundle(path)
        return {"status": "published_reference_only", "model_id": path.stem,
                "model_path": str(path), "sha256": manifest["wrapper_sha256"],
                "manifest_path": str(sidecar), "new_fits": 0, "model_activated": False, "purchase_authorized": False}
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if args.publish:
        result = publish()
    else:
        _wrapper, manifest = build_package()
        result = {"status": "preflight_pass", "model_id": manifest["model_id"], "new_fits": 0,
                  "model_activated": False, "purchase_authorized": False}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
