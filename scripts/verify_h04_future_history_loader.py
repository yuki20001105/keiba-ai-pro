"""Verify the FROZEN loader against a future daily with an isolated fake root.

No Runtime constructor, ledger, ownership, collection, inference or network is
called. --load opts into the JSON memory load; default prints a read-only plan.
Only a new verification directory receives the loader's fake daily binding.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gc
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_h04_history_revision as preparation

UTC = timezone.utc
SCHEMA = "h04-future-history-frozen-loader-verification-v1"


def verify_loader(*, target_date, output_dir, load=False, now=None,
                  runtime_root=preparation.RUNTIME_ROOT):
    import psutil
    current = now or datetime.now(UTC)
    preparation.validate_target(target_date, now=current)
    output = preparation.validate_output(output_dir)
    runtime_root = Path(runtime_root).resolve(strict=True)
    directory = runtime_root / "daily" / target_date
    manifest = preparation.read_json(directory / "preparation_manifest.json")
    if manifest.get("target_date") != target_date or not manifest.get("history_max_source_date", "99999999") < target_date:
        raise ValueError("prepared-future-history-boundary-required")
    before = preparation.protection_receipt(runtime_root=runtime_root)
    actual_binding = runtime_root / "daily-bindings" / (target_date + ".json")
    if actual_binding.exists():
        raise ValueError("real-future-binding-must-not-be-created-in-advance")
    hashes = {name: preparation.file_hash(directory / name) for name in
              ("preparation_manifest.json", "history_state.json", "prior_bloodlines.json")}
    if hashes["history_state.json"] != manifest["state_sha256"] or hashes["prior_bloodlines.json"] != manifest["bloodline_sha256"]:
        raise ValueError("future-daily-artifact-hash-mismatch")
    available = psutil.virtual_memory().available
    result = {"schema": SCHEMA, "operation": "dry_run", "target_date": target_date,
        "original_daily_directory": str(directory), "new_fake_runtime_root": str(output),
        "future_daily_sha256": hashes, "prepared_physical_snapshot_sha256": manifest["snapshot_sha256"],
        "history_max_source_date": manifest["history_max_source_date"],
        "available_memory_bytes": available, "required_memory_bytes": 4 * 1024**3,
        "original_protected_before": before, "real_future_binding_absent": True,
        "runtime_constructor_called": False, "owner_ledger_capture_infer_called": False,
        "network": False, "real_runtime_binding_written": False}
    if not load:
        return result
    if available < 4 * 1024**3:
        raise ValueError("four-gib-memory-reserve-required-for-frozen-loader-verification")
    preparation.capture_idle_receipt(runtime_root, now=current)
    output.mkdir(parents=True, exist_ok=False)
    preparation.write_new_json(output / "verification_request.json", result)
    instance = None
    try:
        from forward_runtime.service import Runtime
        instance = Runtime.__new__(Runtime)
        instance.root = output
        loaded = Runtime.load_daily(instance, directory, target_date)
        fake_binding = output / "daily-bindings" / (target_date + ".json")
        if loaded != manifest or preparation.read_json(fake_binding) != manifest:
            raise ValueError("frozen-loader-did-not-bind-exact-original-manifest")
        if (instance.state["state"]["target_date"] != target_date
                or instance.state["state"]["history_max_source_date"] != manifest["history_max_source_date"]
                or instance.blood["target_date"] != target_date):
            raise ValueError("frozen-loader-state-or-bloodline-boundary-mismatch")
        after = preparation.protection_receipt(runtime_root=runtime_root)
        if before != after or actual_binding.exists():
            raise ValueError("real-protected-runtime-or-binding-changed-during-fake-loader-check")
        if any(preparation.file_hash(directory / name) != digest for name, digest in hashes.items()):
            raise ValueError("original-future-daily-changed-during-loader-check")
        result.update(operation="frozen_loader_accepted_isolated_fake_binding", passed=True,
            original_protected_after=after, protected_state_unchanged=True,
            fake_binding_path=str(fake_binding), fake_binding_sha256=preparation.file_hash(fake_binding),
            loaded_history_rows=instance.state["state"]["history_row_count"],
            loaded_history_races=instance.state["state"]["history_race_count"],
            completed_at_utc=datetime.now(UTC).isoformat())
        preparation.write_new_json(output / "verification_receipt.json", result)
        return result
    finally:
        if instance is not None:
            instance.daily = instance.state = instance.blood = None
        gc.collect()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--load", action="store_true")
    args = parser.parse_args()
    result = verify_loader(target_date=args.target_date, output_dir=args.output_dir, load=args.load)
    print(json.dumps({key: result.get(key) for key in ("schema", "operation", "target_date", "passed",
        "available_memory_bytes", "required_memory_bytes", "loaded_history_rows", "loaded_history_races",
        "real_future_binding_absent", "real_runtime_binding_written")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
