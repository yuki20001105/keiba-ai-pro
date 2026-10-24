"""Isolated reconstruction study: exclusive outputs and frozen-input checks."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "python-api"), str(ROOT / "keiba")]
for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "2"
OUT = ROOT / "reports/generated/model-experiments/training-data-reconstruction-v1"
SNAPSHOT = ROOT / "reports/generated/data-quality/feature-pit-source-v1/audit-source.sqlite"
TRACK = ROOT / "reports/generated/forward-runtime/h04-live-20260920-v2"


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def now():
    return datetime.now(timezone.utc).isoformat()


def protection():
    from snapshot_feature_pit_audit import protected
    from verify_market_segments_outputs import verify_files
    value = protected()
    prior = {}
    for relative in ("data-quality/feature-pit-source-v1", "model-experiments/speed-figure-pit-v1", "model-experiments/training-data-pit-audit-v1"):
        directory = ROOT / "reports/generated" / relative
        manifest = read(directory / "artifact_manifest.json")
        checked = verify_files(directory, manifest["files"])
        verify_files(ROOT, manifest.get("code_and_tests_sha256", {}))
        prior[relative] = {"manifest_sha256": sha(directory / "artifact_manifest.json"), "verified_files": checked}
    paths = subprocess.check_output(["git", "diff", "--name-only", "HEAD"], cwd=ROOT, text=True).splitlines()
    value["prior_audits"] = prior
    value["preexisting_tracked_edits"] = {p: sha(ROOT / p) for p in paths if (ROOT / p).is_file()}
    value["snapshot_sha256"] = sha(SNAPSHOT)
    return value


def invariant_projection(state):
    # Collector heartbeat/expiry changes normally. Never freeze its live ledger.
    return {k: v for k, v in state.items() if k != "collector"}
