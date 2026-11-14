"""Replay and monthly research with the published model's verified sources."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def parent(args):
    sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]
    from routers.predict import _load_served_model_bundle, _resolve_saved_model_path
    from services.trained_model_runtime import _source_path, recover_training_sources

    pointer = ROOT / "python-api" / "models" / ".active_model.json"
    model_id = json.loads(pointer.read_text(encoding="utf-8"))["model_id"]
    path = _resolve_saved_model_path(model_id)
    bundle = _load_served_model_bundle(path)
    sources = recover_training_sources(bundle)
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    request = {
        "root": str(root), "repository": str(ROOT), "phase": args.phase,
        "model_path": str(path), "model_sha256": bundle["_shared_artifact_sha256"],
        "source_hashes": bundle["source_hashes"], "pointer_sha256": sha(pointer),
        "owner_receipt": str(args.owner_receipt.resolve()) if args.owner_receipt else None,
    }
    archive = root / "runtime-sources"
    archive.mkdir(exist_ok=True)
    execution_sources = {}
    bodies = {**sources, **{name: (ROOT / relative).read_bytes() for name, relative in (
        ("scripts.run_reference_validation", Path("scripts") / "run_reference_validation.py"),
        ("training.reference_replay", Path("python-api") / "training" / "reference_replay.py"),
        ("training.monthly_validation", Path("python-api") / "training" / "monthly_validation.py"),
        ("training.monthly_validation_experiment", Path("python-api") / "training" / "monthly_validation_experiment.py"),
        ("training.db_shared_grouped", Path("python-api") / "training" / "db_shared_grouped.py"),
        ("training.target_comparison", Path("python-api") / "training" / "target_comparison.py"),
        ("betting.performance", Path("python-api") / "betting" / "performance.py"),
        ("betting.paper_simulation", Path("python-api") / "betting" / "paper_simulation.py"),
        ("betting.research_opponent_pairs", Path("python-api") / "betting" / "research_opponent_pairs.py"),
    )}}
    for module, body in bodies.items():
        digest = hashlib.sha256(body).hexdigest()
        archived = archive / (digest + ".py")
        if archived.exists() and sha(archived) != digest:
            raise ValueError("Archived execution source changed")
        archived.write_bytes(body)
        execution_sources[module] = {"sha256": digest, "file": str(archived.relative_to(root))}
    write(root / f"execution-sources.{args.phase}.json", execution_sources)
    with tempfile.TemporaryDirectory(prefix="keiba-validation-sources-") as directory:
        stage = Path(directory)
        packages = set()
        for module, body in sources.items():
            relative = _source_path(module)
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            packages.add(relative.parent)
        for package in packages:
            (stage / package / "__init__.py").write_text(
                f"__path__.append({str(ROOT / package)!r})\n", encoding="utf-8")
        request["stage"] = str(stage)
        request_path = stage / "request.json"
        write(request_path, request)
        subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), "--worker", str(request_path)],
                       check=True, timeout=3600)


def guard(request):
    from services.db_shared_input import validate_published_artifact
    if (validate_published_artifact(Path(request["model_path"])) != request["model_sha256"]
            or sha(ROOT / "python-api" / "models" / ".active_model.json") != request["pointer_sha256"]):
        raise ValueError("Selected model or active pointer changed during validation")


def prepare(request):
    from app_config import ULTIMATE_DB
    from training import db_shared_features as source
    from training.reference_replay import read_manifest

    root = Path(request["root"])
    snapshot, receipt = root / "source.sqlite", root / "inputs.json"
    if receipt.exists():
        existing = json.loads(receipt.read_text(encoding="utf-8"))
        for name in ("model_sha256", "source_hashes", "pointer_sha256"):
            if existing[name] != request[name]:
                raise ValueError("Existing validation inputs belong to another model")
        for name, digest in existing["files"].items():
            if sha(root / name) != digest:
                raise ValueError("Prepared validation inputs changed")
        return existing
    if snapshot.exists():
        raise ValueError("Unfinished source snapshot requires a new output directory")
    with sqlite3.connect(Path(ULTIMATE_DB).resolve().as_uri() + "?mode=ro", uri=True) as original:
        with sqlite3.connect(snapshot) as frozen:
            original.backup(frozen, pages=4096, sleep=.02)
    print("Source snapshot captured; preparing strict chronological features", flush=True)
    import joblib
    bundle = joblib.load(request["model_path"])
    manifest = read_manifest(snapshot, bundle["training_date_to"], datetime.now().strftime("%Y%m%d"))
    upper = max(row["date"] for row in manifest["races"]).replace("-", "")
    target, history, coverage = source.load_db_frames(snapshot, bundle["training_date_from"], upper)
    features = source.build_training_features(target, history)
    target.to_parquet(root / "target.parquet")
    history.to_parquet(root / "history.parquet")
    features.to_parquet(root / "features.parquet")
    write(root / "acquired-races.json", manifest)
    inputs = {
        **{name: request[name] for name in ("model_sha256", "source_hashes", "pointer_sha256")},
        "training_end_date": bundle["training_date_to"], "target_races": int(target.race_id.nunique()),
        "acquired_non_training_races": len(manifest["races"]), "coverage": coverage,
        "preparation_script_sha256": sha(__file__),
        "files": {name: sha(root / name) for name in
                  ("source.sqlite", "target.parquet", "history.parquet", "features.parquet", "acquired-races.json")},
    }
    guard(request)
    write(receipt, inputs)
    print(json.dumps({key: inputs[key] for key in ("target_races", "acquired_non_training_races", "training_end_date")}), flush=True)
    return inputs


def replay(request, inputs, bundle, target, features):
    import numpy as np
    import pandas as pd
    from betting.paper_simulation import list_paper_performance, record_paper_prediction
    from betting.performance import _race_date
    from services.db_shared_input import _finish_shared_input
    from training import db_normalized_registry, db_shared_features as source
    from training.reference_replay import score_batch

    if not request["owner_receipt"]:
        raise ValueError("A normal authenticated-login owner receipt is required")
    owner_receipt = json.loads(Path(request["owner_receipt"]).read_text(encoding="utf-8"))
    if owner_receipt.get("verified_status") != 200:
        raise ValueError("Owner receipt has no verified authenticated API request")
    owner = owner_receipt["owner"]
    root, snapshot = Path(request["root"]), Path(request["root"]) / "source.sqlite"
    ledger = ROOT / "keiba" / "data" / "betting_performance.db"
    manifest = json.loads((root / "acquired-races.json").read_text(encoding="utf-8"))
    heldout = target.race_date.map(_race_date).gt(_race_date(bundle["training_date_to"]))
    scores = score_batch(bundle, target.loc[heldout], features.loc[heldout])
    scores.to_parquet(root / "published-model-scores.parquet")
    positions = scores.groupby("race_id", sort=False).indices
    profile = db_normalized_registry.prediction_module(bundle)
    completed = {}
    log_path = root / "replay.jsonl"
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            if item["model_sha256"] != request["model_sha256"] or item["owner_sha256"] != hashlib.sha256(owner.encode()).hexdigest():
                raise ValueError("Replay progress belongs to a different owner or model")
            completed[item["race_id"]] = item
    probe_ids = set()
    for _, group in scores.groupby(scores.race_date.str[:4], sort=True):
        probe_ids.update((str(group.race_id.iloc[0]), str(group.race_id.iloc[-1])))
    parity = []
    with sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True) as db, log_path.open("a", encoding="utf-8") as log:
        for entry in manifest["races"]:
            rid, info = entry["race_id"], entry["race_info"]
            if rid in completed:
                continue
            if len(completed) % 100 == 0:
                guard(request)
            result = {"race_id": rid, "date": entry["date"], "model_sha256": request["model_sha256"],
                      "owner_sha256": hashlib.sha256(owner.encode()).hexdigest(), "prediction_success": False}
            try:
                _, reason = source.canonical._race_metadata(rid, info)
                if reason:
                    raise ValueError(reason)
                horses = pd.DataFrame([json.loads(row[0]) for row in db.execute(
                    "SELECT data FROM race_results_ultimate WHERE race_id=?", (rid,))])
                card = source._current_card(info, horses)
                info = {**info, "race_id": rid, "date": entry["date"].replace("-", ""),
                        "num_horses": int(card.num_horses.iloc[0])}
                if rid in positions:
                    part = scores.iloc[positions[rid]]
                    raw = features.loc[part.index].copy()
                    numbered = dict(zip(part.horse_number, range(len(part))))
                    order = [numbered[int(n)] for n in card.horse_number]
                    raw = raw.iloc[order].reset_index(drop=True)
                    part = part.iloc[order].reset_index(drop=True)
                    audit = {"issue_codes": [], "no_history_rows": int(raw.safe_prior_starts.eq(0).sum()),
                             "current_outcomes_consumed": False, "same_day_or_future_history_used": False}
                    result["method"] = "chronological_batch"
                    if rid in probe_ids:
                        canonical, _ = source.build_serving_features(snapshot, info, horses)
                        pd.testing.assert_frame_equal(raw, canonical, check_exact=True)
                        single_raw, single_probability = profile.predict_shared_scores(
                            bundle, profile.select_features(canonical), n_threads=1)
                        np.testing.assert_allclose(part.raw_score, single_raw, atol=1e-10, rtol=1e-10)
                        np.testing.assert_allclose(part.p_norm, single_probability, atol=1e-10, rtol=1e-10)
                        parity.append(rid)
                else:
                    raw, audit = source.build_serving_features(snapshot, info, horses)
                    raw_scores, probabilities = profile.predict_shared_scores(
                        bundle, profile.select_features(raw), n_threads=1)
                    if probabilities is None:
                        from training.db_shared_fit import race_softmax
                        probabilities = race_softmax(raw_scores, card.race_id, 1.0)
                    part = card[["horse_number", "horse_id"]].copy()
                    part["p_norm"] = probabilities
                    result["method"] = "canonical_single_race"
                selected = profile.select_features(raw)
                _, diagnostics = _finish_shared_input(
                    bundle, horses, selected, bundle["encoder"].transform(selected), audit,
                    race_info=info, model_path=request["model_path"], trusted_context=None)
                rows = []
                raw_rows = {int(row["horse_number"]): row for row in horses.to_dict("records")}
                for row in part.to_dict("records"):
                    raw_odds = raw_rows[int(row["horse_number"])].get("odds")
                    if raw_odds is None:
                        raw_odds = raw_rows[int(row["horse_number"])].get("win_odds")
                    try:
                        raw_odds = float(raw_odds) if not isinstance(raw_odds, bool) else None
                    except (TypeError, ValueError):
                        raw_odds = None
                    if raw_odds is not None and not np.isfinite(raw_odds):
                        raw_odds = None
                    rows.append({"horse_number": int(row["horse_number"]), "horse_id": row["horse_id"],
                                 "p_norm": float(row["p_norm"]), "odds": raw_odds})
                payload = {"success": True, "model_id": Path(request["model_path"]).stem,
                           "race_info": info, "predictions": rows, "input_diagnostics": diagnostics}
                result.update(prediction_success=True, simulation=record_paper_prediction(
                    ledger, owner, payload, bundle, model_sha256=request["model_sha256"], historical_validation=True))
            except ValueError as error:
                result["reason"] = str(error)
            completed[rid] = result
            log.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")
            log.flush()
            if len(completed) % 250 == 0:
                print(json.dumps({"processed": len(completed), "total": len(manifest["races"])}), flush=True)
    guard(request)
    if set(completed) != {entry["race_id"] for entry in manifest["races"]}:
        raise ValueError("Not every acquired non-training race was processed")
    cohort = {"model_id": Path(request["model_path"]).stem,
              "race_ids": {entry["race_id"] for entry in manifest["races"]}}
    data = list_paper_performance(ledger, snapshot, owner, origin="historical_validation", **cohort)
    strict = list_paper_performance(ledger, snapshot, owner, **cohort)
    with sqlite3.connect(ledger) as db:
        overlap = db.execute("SELECT COUNT(*) FROM paper_simulation_records WHERE owner=? AND model_id=? AND race_date<=?",
                             (owner, Path(request["model_path"]).stem, _race_date(bundle["training_date_to"]))).fetchone()[0]
        if overlap:
            raise ValueError("Training races appeared in the reference ledger")
    summary = {
        "processed": len(completed), "training_overlap": overlap,
        "predicted": sum(row["prediction_success"] for row in completed.values()),
        "eligible": sum(row.get("simulation", {}).get("eligible", False) for row in completed.values()),
        "existing_frozen_records_preserved": sum(
            row.get("simulation", {}).get("existing", False) for row in completed.values()),
        "existing_records_policy": "first-frozen predictions and tickets preserved, not replaced by replay scores",
        "reasons": dict(Counter(row.get("reason") or row.get("simulation", {}).get("reason") for row in completed.values()
                                if not row.get("simulation", {}).get("eligible"))),
        "summary": data["summary"], "models": data["model_performance"]["models"],
        "strict_summary": strict["summary"], "canonical_parity_races": parity,
        "model_sha256": request["model_sha256"], "snapshot_sha256": inputs["files"]["source.sqlite"],
    }
    write(root / "replay-summary.json", summary)
    print(json.dumps({key: summary[key] for key in ("processed", "predicted", "eligible", "training_overlap", "summary")}), flush=True)


def worker(request):
    stage = Path(request["stage"])
    sys.path[:0] = [str(stage / "python-api"), str(stage / "keiba"),
                    str(ROOT / "python-api"), str(ROOT / "keiba")]
    import joblib
    import pandas as pd
    from threadpoolctl import threadpool_limits
    from training import db_normalized_registry

    guard(request)
    bundle = joblib.load(request["model_path"])
    profile = db_normalized_registry.prediction_module(bundle)
    profile._validate_prediction_bundle(bundle)
    if profile.source_hashes() != request["source_hashes"]:
        raise ValueError("Recovered prediction sources do not match the saved artifact")
    bundle["_shared_artifact_sha256"] = request["model_sha256"]
    with threadpool_limits(limits=1):
        inputs = prepare(request)
        if request["phase"] == "prepare":
            return
        root = Path(request["root"])
        target, features = pd.read_parquet(root / "target.parquet"), pd.read_parquet(root / "features.parquet")
        if request["phase"] == "replay":
            replay(request, inputs, bundle, target, features)
        else:
            from training.monthly_validation_experiment import run_experiment
            history = pd.read_parquet(root / "history.parquet")
            run_experiment(root, target, features, history, bundle, inputs)
        guard(request)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        worker(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")))
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--root", type=Path, required=True)
        parser.add_argument("--phase", choices=("prepare", "replay", "study"), required=True)
        parser.add_argument("--owner-receipt", type=Path)
        parent(parser.parse_args())
