"""Replay observed residual sources and verify every changed research JSON row."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba"), str(ROOT / "scripts")]

import pandas as pd

from audit_residual_acquisition import race_evidence, readonly
from experiments.common import file_sha256, read_json
from experiments.dataset import _race_metadata, _runner
from experiments.search import freeze_json
from scraping.acquisition_horse_history import prepare_history
from scraping.fetch_pipeline import decode_html_body
from scraping.missingness_goal import _protected
from training.eligibility import select_training_eligible_rows


def verify(root):
    task = read_json(root / "contract.json")
    source, evidence = Path(task["source"]), Path(task["evidence"])
    source_task = read_json(evidence / "contract.json")
    for path, digest in source_task["code_hashes"].items():
        if file_sha256(Path(path)) != digest:
            raise ValueError("residual-replay-code-changed")
    for name, digest in read_json(evidence / "artifacts.json").items():
        if file_sha256(evidence / name) != digest:
            raise ValueError("residual-replay-source-archive-changed")
    if (file_sha256(source) != task["source_sha256"] or _protected() != task["protected"]
            or file_sha256(evidence / "verification.json") != task["evidence_sha256"]):
        raise ValueError("residual-replay-input-changed")
    proofs, classes, nonfinishes, selected = read_json(evidence / "race-proofs.json"), {}, {}, 0
    with closing(readonly(source)) as before, closing(readonly(root / "research.db")) as after, closing(
        readonly(evidence / "sources.db")
    ) as archive:
        for proof in proofs:
            race, url = proof["race_id"], proof["source"]["source_url"]
            final, status, body, stamp = archive.execute(
                "SELECT final_url,status,body,fetched_at FROM sources WHERE url=?", (url,)).fetchone()
            if status != 200 or final.rstrip("/") != url.rstrip("/"):
                raise ValueError("residual-replay-transport-changed")
            metadata = json.loads(before.execute("SELECT data FROM races_ultimate WHERE race_id=?", (race,)).fetchone()[0])
            runners = [json.loads(raw) for raw, in before.execute(
                "SELECT data FROM race_results_ultimate WHERE race_id=?", (race,))]
            digest = hashlib.sha256(body).hexdigest()
            replay = race_evidence(
                decode_html_body(body), race_id=race, source_url=url, expected_date=metadata["date"],
                expected_runners=runners, observed_at_utc=datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
                raw_body_sha256=digest, raw_body=body, allow_cached_transport_identity=True,
                cache_receipt={"normalized_url": url, "status": status, "body_sha256": digest, "fetched_at": stamp})
            for key in ("requested_class_repair", "requested_clock_horses", "requested_prior_history_confirmation"):
                replay[key] = proof[key]
            if replay != proof:
                raise ValueError("residual-replay-source-proof-disagrees")
            if proof["requested_class_repair"]:
                classes[race] = proof
            if proof["requested_prior_history_confirmation"]:
                nonfinishes[race] = proof
                state = after.execute("SELECT lifecycle,quality_status,excluded_from_standard "
                                      "FROM scrape_race_acquisition_state WHERE race_id=?", (race,)).fetchone()
                if state != ("settled", "complete", 0):
                    raise ValueError("residual-replay-prior-quality-not-confirmed")
                meta, reason = _race_metadata(race, metadata)
                if reason:
                    raise ValueError("residual-replay-prior-metadata-conflict")
                rows = []
                for raw, in after.execute("SELECT data FROM race_results_ultimate WHERE race_id=?", (race,)):
                    row, reason = _runner(race, json.loads(raw), meta)
                    if reason:
                        raise ValueError("residual-replay-prior-runner-conflict")
                    rows.append(row)
                selection = select_training_eligible_rows(
                    pd.DataFrame(rows), target="speed_deviation", recorded_quality_states={race: state})
                selected += len(selection.frame)
        count = 0
        for table, keys in (("races_ultimate", "race_id"), ("race_results_ultimate", "id,race_id")):
            old_rows = before.execute(f"SELECT {keys},data FROM {table} ORDER BY {keys}")
            new_rows = after.execute(f"SELECT {keys},data FROM {table} ORDER BY {keys}")
            from itertools import zip_longest
            for old, new in zip_longest(old_rows, new_rows):
                if old is None or new is None or old[:-1] != new[:-1]:
                    raise ValueError("residual-replay-row-identity-changed")
                previous, actual = json.loads(old[-1]), json.loads(new[-1])
                if previous == actual:
                    continue
                expected = previous.copy()
                race = old[-2]
                if table == "races_ultimate" and race in classes:
                    proof = classes[race]
                    expected.update(race_class=proof["explicit_race_class"], _race_class_source=proof["source"])
                elif table == "race_results_ultimate" and race in nonfinishes:
                    proof = nonfinishes[race]
                    observed = next(row for row in proof["rows"] if row["horse_id"] == previous["horse_id"])
                    expected.update(finish_position=observed["finish_source_raw"], _finish_source=proof["source"])
                if actual != expected:
                    raise ValueError("residual-replay-unrelated-observation-changed")
                count += 1
        journal_count = after.execute("SELECT COUNT(*) FROM data_quality_repair_journal_v1 WHERE batch_id=?",
                                     (root.name,)).fetchone()[0]
        if journal_count != count:
            raise ValueError("residual-replay-journal-disagrees")
        careers = read_json(evidence / "career-audit.json")
        for career in careers:
            profile = archive.execute("SELECT body FROM sources WHERE url=?", (career["profile_source"],)).fetchone()[0]
            response = archive.execute("SELECT body FROM sources WHERE url=?", (career["ajax_source"],)).fetchone()[0]
            if (hashlib.sha256(profile).hexdigest() != career["profile_sha256"]
                    or hashlib.sha256(response).hexdigest() != career["ajax_sha256"]):
                raise ValueError("residual-replay-career-bytes-changed")
            _, audit = prepare_history(decode_html_body(profile), decode_html_body(response),
                                       career["horse_id"], career["target_date"])
            if any(career[key] != value for key, value in audit.items()):
                raise ValueError("residual-replay-career-inventory-disagrees")
    result = {
        "verified": True, "public_race_sources_replayed": len(proofs), "career_sources_replayed": len(careers),
        "raw_rows_reverified": count, "class_races_reverified": len(classes),
        "source_confirmed_prior_rows_eligible": selected, "source_and_protected_state_unchanged": True,
        "repaired_db_sha256": file_sha256(root / "research.db"), "verifier_sha256": file_sha256(Path(__file__)),
        "research_only": True, "global_missingness_zero_claimed": False,
    }
    freeze_json(root / "replay-verification.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    print(json.dumps(verify(parser.parse_args().root.resolve(strict=True)), ensure_ascii=False))
