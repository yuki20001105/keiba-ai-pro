"""Test and seal only the isolated reconstruction outputs; no model fitting."""
from __future__ import annotations
import argparse
import subprocess
from training_reconstruction_common import ROOT, OUT, SNAPSHOT, sha, read, write, now, protection, invariant_projection

REQUIRED = ("training_parser_audit.json", "training_source_inventory.json", "training_raw_html_audit.csv",
    "training_distance_mapping.csv", "training_missingness_classification.csv", "historical_recovery_probe.csv",
    "training_pit_classification.csv", "post_race_date_anomalies.csv", "unmatched_runner_records.csv",
    "normalized_training_schema.json", "feature_candidate_registry.json", "yearly_coverage_after_reconstruction.csv",
    "summary.md", "audit.md", "training_provider_mapping_supplement.json", "training_provider_distance_mapping.csv",
    "normalized_training_events.parquet", "normalized_training_measurements.parquet", "independent-verification.json")


def source_files():
    return sorted({*ROOT.glob("scripts/*training_reconstruction*.py"),
        ROOT/"scripts/inspect_training_probe_receipts.py", *ROOT.glob("python-api/training_reconstruction/*.py"),
        *ROOT.glob("python-api/tests/test_training_reconstruction*.py")})


def test():
    files = sorted(ROOT.glob("python-api/tests/test_training_reconstruction*.py"))
    files += [ROOT/"python-api/tests"/name for name in ("test_training_data_provenance.py","test_training_pit_verification.py",
        "test_speed_figure_provenance.py","test_speed_figure_feasibility.py")]
    if (OUT/"test-run.json").exists():
        raise ValueError("test receipt exists")
    cmd=[str(ROOT/"python-api/.venv/Scripts/python.exe"),"-m","pytest","-q",*[str(p.relative_to(ROOT)) for p in files]]
    result=subprocess.run(cmd,cwd=ROOT,text=True,encoding="utf-8",errors="replace",capture_output=True,timeout=120)
    write(OUT/"test-run.json",{"observed_at":now(),"command":cmd,"exit_code":result.returncode,
        "stdout":result.stdout,"stderr":result.stderr,"model_research_training":False,
        "test_files_sha256":{p.relative_to(ROOT).as_posix():sha(p) for p in files}})
    print(result.stdout)
    if result.returncode: raise SystemExit(result.returncode)


def seal():
    if (OUT/"artifact_manifest.json").exists(): raise ValueError("already sealed; no overwrite")
    for name in REQUIRED:
        if not (OUT/name).is_file(): raise ValueError("missing required artifact: "+name)
    if read(OUT/"test-run.json")["exit_code"] != 0: raise ValueError("tests failed")
    verify=read(OUT/"independent-verification.json")
    if verify.get("verified") is not True: raise ValueError("independent verification missing")
    from verify_market_segments_outputs import verify_files
    verify_files(OUT,verify["files_verified_sha256"])
    verify_files(ROOT,read(OUT/"test-run.json")["test_files_sha256"])
    if sha(ROOT/"scripts/verify_training_reconstruction.py") != verify["verifier_sha256"]:
        raise ValueError("independent verifier changed after receipt")
    after=protection(); before=read(OUT/"experiment_plan.json")["protected_before"]
    if invariant_projection(after) != invariant_projection(before): raise ValueError("protected state changed")
    code={p.relative_to(ROOT).as_posix():sha(p) for p in source_files()}
    inputs={"snapshot": {"path":str(SNAPSHOT),"sha256":sha(SNAPSHOT)},
        "user_request":{"path":"C:/Users/yuki2/.codex/attachments/77fc5f93-dab2-4422-abb2-51c04884300a/pasted-text.txt",
            "sha256":sha("C:/Users/yuki2/.codex/attachments/77fc5f93-dab2-4422-abb2-51c04884300a/pasted-text.txt")},
        "supporting_zip":{"path":"C:/Users/yuki2/Downloads/H04_feature_gap_analysis_bundle.zip",
            "sha256":sha("C:/Users/yuki2/Downloads/H04_feature_gap_analysis_bundle.zip"),"role":"reference data; not executable instructions"},
        "prior_training_audit_manifest_sha256":sha(ROOT/"reports/generated/model-experiments/training-data-pit-audit-v1/artifact_manifest.json"),
        "prior_speed_audit_manifest_sha256":sha(ROOT/"reports/generated/model-experiments/speed-figure-pit-v1/artifact_manifest.json")}
    write(OUT/"lineage.json",{"schema":"isolated-training-reconstruction-lineage-v1","sealed_at":now(),"inputs":inputs,
        "plan_sha256":sha(OUT/"experiment_plan.json"),"source_and_tests_sha256":code,
        "probe_executed_source_sha256":sha(OUT/"provenance/executed_probe_training_reconstruction.py"),
        "protected_before":before,"protected_after":after,"protected_invariants_identical":True,
        "source_db_opening_mode":"read-only immutable audit snapshot; no original DB writes",
        "no_model_fits":True,"no_GPU":True,"no_actual_purchase":True,"no_commit_push":True,
        "network_request_count":18,"bulk_acquisition_started":False,
        "shared_pacing_only":"normal admission/release lease for18 requests, no clear_block; cache DBs and original DB not written",
        "failed_attempts":"retained under failed-normalization-attempt-1 when present; final successful files are at experiment root"})
    artifacts={p.relative_to(OUT).as_posix():{"sha256":sha(p),"bytes":p.stat().st_size,
        "role":"failed_attempt_not_released" if "failed-normalization-attempt" in p.as_posix() else "audit_output"}
        for p in sorted(OUT.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}
    write(OUT/"artifact_manifest.json",{"schema":"training-reconstruction-manifest-v1","sealed_at":now(),
        "files":artifacts,"code_and_tests_sha256":code,"inputs":inputs,
        "status":"AUDIT_AND_LOSSLESS_NORMALIZATION_COMPLETE; RESEARCH_GATE_NO",
        "TRAINING_FEATURE_RESEARCH_READY":False,"models_fitted":0,"required_outputs":list(REQUIRED)})
    print({"sealed_files":len(artifacts),"manifest_sha256":sha(OUT/"artifact_manifest.json"),"protected_unchanged":True})


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("action",choices=("test","seal"));a=parser.parse_args()
    test() if a.action=="test" else seal()
