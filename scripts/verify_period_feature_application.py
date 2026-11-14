"""Independently replay the full research matrix without its feature generator."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]

import numpy as np
import pandas as pd

from experiments.common import file_sha256, read_json
from experiments.search import freeze_json
from scraping.missingness_goal import _protected

IDENTITY = ["race_id", "horse_id", "horse_number", "race_date"]


def compare(actual, expected, column):
    if not np.allclose(np.asarray(actual, dtype=float), np.asarray(expected, dtype=float),
                       rtol=1e-6, atol=1e-6, equal_nan=True):
        raise ValueError(f"period-replay-feature-disagrees: {column}")


def verify_histories(history, targets, rebuilt):
    expected = {}
    source = history.sort_values(["race_date", "race_id", "horse_number"], kind="mergesort").copy()
    dates = pd.to_datetime(source.race_date, format="%Y%m%d")
    source["_ordinal"] = dates.map(pd.Timestamp.toordinal)
    target_days = pd.to_datetime(targets.race_date, format="%Y%m%d").map(pd.Timestamp.toordinal)
    target_groups = targets.groupby("horse_id", sort=False).indices
    for horse, rows in source.groupby("horse_id", sort=False):
        positions = target_groups.get(horse)
        if positions is None:
            continue
        indices = np.searchsorted(rows.race_date.to_numpy(), targets.race_date.iloc[positions].to_numpy())
        data = {
            "finish": rows.finish.to_numpy(dtype=float), "time_seconds": rows.time_seconds.to_numpy(dtype=float),
            "distance": rows.distance.to_numpy(dtype=float),
            "speed_mps": rows.distance.to_numpy(dtype=float) / rows.time_seconds.to_numpy(dtype=float),
        }
        data["last3f_time"] = pd.to_numeric(rows.last_3f_time, errors="raise").to_numpy(dtype=float)

        def put(column, values):
            if column not in expected:
                expected[column] = np.full(len(targets), np.nan)
            expected[column][positions] = values

        put("safe_prior_starts", indices)
        for offset, prefix in ((1, "prev"), (2, "prev2")):
            valid = indices >= offset
            for name in ("finish", "time_seconds", "distance", "speed_mps"):
                values = np.full(len(indices), np.nan)
                values[valid] = data[name][indices[valid] - offset]
                put(f"safe_{prefix}_{name}", values)
        valid = indices > 0
        elapsed, change = np.full(len(indices), np.nan), np.full(len(indices), np.nan)
        elapsed[valid] = (target_days.iloc[positions].to_numpy()[valid]
                          - rows["_ordinal"].to_numpy()[indices[valid] - 1])
        change[valid] = (targets.distance.iloc[positions].to_numpy()[valid]
                         - data["distance"][indices[valid] - 1])
        put("safe_days_since_last", elapsed)
        put("safe_distance_change", change)
        for window in (3, 5, 10):
            count = np.minimum(indices, window)
            lower = indices - count
            put(f"safe_past{window}_count", count)
            put(f"safe_past{window}_observed_coverage", count / window)
            for name, array in (
                ("avg_finish", data["finish"]), ("avg_speed_mps", data["speed_mps"]),
                ("avg_last3f_time", data["last3f_time"]),
                ("win_rate", (data["finish"] == 1).astype(float)),
                ("top3_rate", (data["finish"] <= 3).astype(float)),
            ):
                observed = np.isfinite(array)
                sums = np.r_[0, np.cumsum(np.where(observed, array, 0))]
                counts = np.r_[0, np.cumsum(observed)]
                denominator = counts[indices] - counts[lower]
                values = np.full(len(indices), np.nan)
                np.divide(sums[indices] - sums[lower], denominator,
                          out=values, where=denominator > 0)
                put(f"safe_past{window}_{name}", values)
    for column, values in expected.items():
        compare(rebuilt[column], values, column)
    return list(expected)


def verify_aggregates(history, targets, rebuilt):
    source, target = history.copy(), targets.copy()
    for frame in (source, target):
        frame["_day"] = frame.race_date.astype(int)
        frame["_band"] = np.select(
            [frame.distance.lt(1400), frame.distance.lt(1900), frame.distance.lt(2300)],
            ["short", "mile", "middle"], default="long",
        )
    source["_n"] = 1
    source["_win"] = source.finish.eq(1).astype(int)
    source["_top3"] = source.finish.le(3).astype(int)
    source["_speed"] = source.distance / source.time_seconds
    target["_row"] = np.arange(len(target))
    verified = []
    for group, keys in (
        ("horse_surface", ["horse_id", "surface"]),
        ("horse_distance_band", ["horse_id", "_band"]),
        ("horse_course", ["horse_id", "venue", "surface"]),
        ("jockey", ["jockey_id"]), ("trainer", ["trainer_id"]),
        ("sire", ["sire"]), ("damsire", ["damsire"]),
    ):
        for frame in (source, target):
            for key in keys:
                values = frame[key].astype("string").str.strip()
                frame[key] = values.mask(values.str.lower().isin(
                    ["", "none", "nan", "<na>", "unknown", "unknown_local",
                     "null", "n/a", "-", "--", "\u4e0d\u660e"],
                ))
        known = target[keys].notna().all(axis=1)
        daily = source.groupby([*keys, "_day"], sort=True)[["_n", "_win", "_top3", "_speed"]].sum()
        cumulative = daily.groupby(level=keys).cumsum().reset_index()
        joined = pd.merge_asof(
            target.loc[known, [*keys, "_day", "_row"]].sort_values("_day"),
            cumulative.sort_values("_day"), on="_day", by=keys,
            direction="backward", allow_exact_matches=False,
        ).set_index("_row").reindex(range(len(target)))
        n = joined["_n"].fillna(0).where(known.to_numpy()).to_numpy()
        fields = {"starts": n, "win_rate": joined["_win"].to_numpy() / np.where(n > 0, n, np.nan),
                  "top3_rate": joined["_top3"].to_numpy() / np.where(n > 0, n, np.nan)}
        if group.startswith("horse_"):
            fields["avg_speed_mps"] = joined["_speed"].to_numpy() / np.where(n > 0, n, np.nan)
        for name, values in fields.items():
            column = f"safe_{group}_{name}"
            compare(rebuilt[column], values, column)
            verified.append(column)
    return verified


def remaining_reasons(frame):
    def missing_under(columns, mask):
        return int(frame.loc[mask, columns].isna().to_numpy().sum())

    fields = ("win_rate", "top3_rate", "avg_speed_mps")
    previous_fields = ("finish", "time_seconds", "distance", "speed_mps")
    no_history_columns = (
        ["safe_days_since_last", "safe_distance_change"]
        + [f"safe_prev_{field}" for field in previous_fields]
        + [f"safe_past{window}_{field}" for window in (3, 5, 10)
           for field in ("avg_finish", "avg_speed_mps", "avg_last3f_time", "win_rate", "top3_rate")]
    )
    counts = {
        "no_prior_stored_horse_history": missing_under(
            no_history_columns, frame.safe_prior_starts.eq(0)),
        "fewer_than_two_prior_stored_starts": missing_under(
            [f"safe_prev2_{field}" for field in previous_fields], frame.safe_prior_starts.lt(2)),
        "no_surface_history_for_surface_or_course": missing_under(
            [f"safe_horse_{group}_{field}" for group in ("surface", "course") for field in fields],
            frame.safe_horse_surface_starts.eq(0)),
        "no_distance_band_history_or_surface_fallback": missing_under(
            [f"safe_horse_distance_band_{field}" for field in fields],
            frame.safe_horse_distance_band_starts.eq(0) & frame.safe_horse_surface_starts.eq(0)),
        "identified_entity_without_prior_stored_results": sum(
            missing_under([f"safe_{entity}_win_rate", f"safe_{entity}_top3_rate"],
                          frame[f"safe_{entity}_starts"].eq(0))
            for entity in ("jockey", "trainer", "sire", "damsire")),
        "unidentified_entity": sum(
            missing_under([f"safe_{entity}_{field}" for field in ("starts", "win_rate", "top3_rate")],
                          frame[f"safe_{entity}_starts"].isna())
            for entity in ("jockey", "trainer", "sire", "damsire")),
        "unavailable_race_class": int(frame.safe_race_class.isna().sum()),
        "prior_starts_without_last3f_measurement": sum(
            missing_under([f"safe_past{window}_avg_last3f_time"], frame[f"safe_past{window}_count"].gt(0))
            for window in (3, 5, 10)),
    }
    return counts


def run(root):
    root = root.resolve(strict=True)
    contract, verification = read_json(root / "contract.json"), read_json(root / "verification.json")
    for name, digest in read_json(root / "artifacts.json").items():
        if file_sha256(root / name) != digest:
            raise ValueError(f"period-replay-artifact-changed: {name}")
    for name, digest in contract["code_hashes"].items():
        if file_sha256(ROOT / name) != digest:
            raise ValueError(f"period-replay-source-changed: {name}")
    baseline = Path(contract["baseline"])
    if (file_sha256(baseline / "research.db") != contract["source_sha256"]
            or file_sha256(baseline / "dataset" / "dataset.parquet") != contract["dataset_sha256"]
            or file_sha256(baseline / "dataset" / "dataset_manifest.json") != contract["manifest_sha256"]
            or _protected() != contract["protected"]):
        raise ValueError("period-replay-input-or-production-changed")
    before = pd.read_parquet(baseline / "dataset" / "dataset.parquet")
    rebuilt = pd.read_parquet(root / "history-rebuilt.parquet")
    after = pd.read_parquet(root / "applied-dataset.parquet")
    history = pd.read_parquet(root / "history-observations.parquet")
    journal = pd.read_parquet(root / "imputations.parquet")
    features, flags = contract["value_features"], contract["imputation_flags"]
    pd.testing.assert_frame_equal(before[IDENTITY], rebuilt[IDENTITY])
    pd.testing.assert_frame_equal(before.drop(columns=features), after[before.columns].drop(columns=features))
    target_source = history.set_index(IDENTITY).reindex(pd.MultiIndex.from_frame(before[IDENTITY])).reset_index()
    verified = verify_histories(history, target_source, rebuilt)
    verified += verify_aggregates(history, target_source, rebuilt)
    untouched = [column for column in features if column not in verified]
    pd.testing.assert_frame_equal(before[untouched], rebuilt[untouched], check_dtype=False)
    replay = rebuilt.copy()
    for flag in flags:
        column = flag.removesuffix("_was_imputed")
        group = "horse_course" if "_course_" in column else "horse_distance_band"
        suffix = column.removeprefix(f"safe_{group}_")
        source = f"safe_horse_surface_{suffix}"
        values, starts = rebuilt[source], rebuilt.safe_horse_surface_starts
        valid_value = values.between(0, 1) if column.endswith("_rate") else values.gt(0)
        mask = (rebuilt[column].isna() & rebuilt[f"safe_{group}_starts"].eq(0)
                & starts.gt(0) & starts.mod(1).eq(0) & np.isfinite(starts)
                & np.isfinite(values) & valid_value)
        replay[flag] = mask.astype("int8")
        replay.loc[mask, column] = rebuilt.loc[mask, source]
        entries = journal.loc[journal.column.eq(column)].set_index("row_index")
        if list(entries.index) != list(rebuilt.index[mask]):
            raise ValueError(f"period-replay-journal-identity: {column}")
        pd.testing.assert_frame_equal(entries[IDENTITY], rebuilt.loc[mask, IDENTITY], check_names=False)
        compare(entries.value, rebuilt.loc[mask, source], column + "-journal-value")
        compare(entries.source_starts, starts.loc[mask], column + "-journal-source-starts")
        if (not entries.source_column.eq(source).all()
                or not entries.reason.eq("no_prior_condition_in_frozen_eligible_store").all()
                or entries.public_career_completeness_verified.any()):
            raise ValueError("period-replay-journal-coverage-or-source-invalid")
    pd.testing.assert_frame_equal(replay, after)
    model = pd.read_parquet(root / "model-inputs.parquet")
    pd.testing.assert_frame_equal(model, after[contract["model_input_columns"]])
    yearly = read_json(root / "yearly-missingness.json")
    for stage, frame in (("before", before), ("history_rebuilt", rebuilt), ("after", after)):
        for year in range(2016, 2027):
            part = frame.loc[frame.race_date.str.startswith(str(year))]
            expected = yearly[stage][str(year)]
            if len(part) != expected["rows"] or int(part.race_id.nunique()) != expected["races"]:
                raise ValueError("period-replay-yearly-population-changed")
            for column in features:
                if int(part[column].isna().sum()) != expected["features"][column]:
                    raise ValueError("period-replay-yearly-missingness-disagrees")
            if int(part[features].isna().sum().sum()) != expected["missing_cells"]:
                raise ValueError("period-replay-yearly-total-disagrees")
    if int(after[flags].to_numpy().sum()) != len(journal):
        raise ValueError("period-replay-flags-disagree-with-journal")
    reasons = remaining_reasons(after)
    remaining = read_json(root / "remaining-missingness.json")
    missing_total = int(after[features].isna().sum().sum())
    if (sum(reasons.values()) != missing_total or remaining["missing_cells"] != missing_total
            or remaining["imputation_applied"] is not True
            or remaining["public_career_completeness_verified"] is not False):
        raise ValueError("period-replay-remaining-reasons-or-imputation-stage-disagree")
    result = {
        "verified": True, "independent_full_history_features_verified": len(verified),
        "unchanged_base_features_verified": len(untouched), "years_verified": 11,
        "rows_verified": len(after), "imputed_cells_verified": len(journal),
        "after_missing_cells": missing_total, "remaining_reason_cells": reasons,
        "source_and_protected_state_unchanged": True, "research_only": True,
        "public_career_completeness_verified": False,
        "generator_verification_sha256": file_sha256(root / "verification.json"),
        "verifier_sha256": file_sha256(Path(__file__)),
    }
    if result["after_missing_cells"] != verification["after_backoff_missing_cells"]:
        raise ValueError("period-replay-total-disagrees")
    freeze_json(root / "replay-verification.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    print(json.dumps(run(parser.parse_args().root), ensure_ascii=False))
