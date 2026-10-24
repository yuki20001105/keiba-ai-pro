"""Predeclared whole-race segments from predictors only, behind Phase-1 gate.

No model training or metric computation. Unknown observations produce nullable
flags, never an assumed negative. Original fractional-rank model inputs survive.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd

F8 = ["safe_weight_change", "safe_prior_starts", "safe_days_since_last", "safe_jockey_win_rate",
      "safe_trainer_win_rate", "safe_horse_surface_win_rate", "safe_horse_distance_band_win_rate",
      "safe_horse_course_win_rate"]
SEGMENT_NAMES = ["age_2_only", "age_3_only", "newcomer", "maiden", "any_zero_observed_history",
                 "many_one_two_observed_history", "any_first_turf", "any_first_dirt",
                 "any_turf_to_dirt", "any_dirt_to_turf", "any_large_distance_change", "any_long_layoff",
                 "heavy_going", "bad_going", "small_field", "large_field", "any_longshot",
                 "any_large_market_H04_rank_divergence"]
KEYS = ["race_id", "horse_number"]
HEAVY_LABELS = ["重"]
BAD_LABELS = ["不", "不良"]
REPO = Path(__file__).resolve().parents[2]


def segment_definitions(class_labels):
    if set(class_labels) != {"newcomer", "maiden"}:
        raise ValueError("exact-newcomer-and-maiden-label-contract-required")
    for values in class_labels.values():
        if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() for v in values) or len(set(values)) != len(values):
            raise ValueError("nonempty-unique-exact-class-labels-required")
    if set(class_labels["newcomer"]) & set(class_labels["maiden"]):
        raise ValueError("overlapping-race-class-labels")
    rules = ["all runners have age 2", "all runners have age 3",
             "one consistent nonmissing race_class exactly in newcomer labels",
             "one consistent nonmissing race_class exactly in maiden labels",
             "at least one runner has observed prior_starts=0; not proof of complete career",
             "at least ceil(N/2) runners have observed prior_starts in {1,2}",
             "at least one current turf runner with prior_starts>0 and horse_surface_starts=0",
             "at least one current dirt runner with prior_starts>0 and horse_surface_starts=0",
             "at least one runner currently dirt with proven strictly-prior surface turf",
             "at least one runner currently turf with proven strictly-prior surface dirt",
             "at least one runner abs(distance_change)>=400 metres",
             "at least one runner days_since_last>=180 days",
             "one consistent nonmissing field_condition exactly 重",
             "one consistent nonmissing field_condition exactly 不 or 不良 (fixed aliases)",
             "N<=8", "N>=16",
             "at least one runner popularity>=10 and odds>=30; broad whole-race membership",
             "at least one abs(market ordinal rank-H04 ordinal rank)>=5; ties ascending horse number"]
    return {"schema": "market-segments-predictor-only-v1", "segment_count": 18,
            "segments": [{"segment": name, "definition": rule, "unit": "whole_race"} for name, rule in zip(SEGMENT_NAMES, rules)],
            "class_labels": class_labels, "going_labels": {"heavy": HEAVY_LABELS.copy(), "bad": BAD_LABELS.copy()}, "F8": F8.copy(),
            "unknown": "nullable Boolean; never converted to False; any TRUE is known TRUE even with other unknowns",
            "outcomes_used": False, "surface_change_proxy": "none; requires dated source mapping or unknown",
            "history_semantics": "observed eligible history only, not proven complete career",
            "class_labels_frozen_before_metrics": True}


def _scope(frame, *, complete=False):
    if frame.empty or not {"race_id", "race_date", "horse_number"}.issubset(frame) or frame.columns.duplicated().any():
        raise ValueError("nonempty-unique-column-input-required")
    if frame[KEYS].isna().any().any() or frame.duplicated(KEYS).any():
        raise ValueError("unique-present-runner-keys-required")
    dates = frame.race_date.astype(str)
    if not dates.str.fullmatch(r"202[12]\d{4}").all():
        raise ValueError("only-2021-2022-feature-rows-permitted")
    pd.to_datetime(dates, format="%Y%m%d", errors="raise")
    numbers = pd.to_numeric(frame.horse_number, errors="coerce")
    if not numbers.between(1, 18).all() or not numbers.eq(np.floor(numbers)).all():
        raise ValueError("invalid-runner-number")
    if frame.groupby("race_id").race_date.nunique().ne(1).any():
        raise ValueError("inconsistent-race-date")
    if complete:
        n = frame.groupby("race_id").race_id.transform("size")
        if "expected_runner_count" not in frame or not frame.expected_runner_count.eq(n).all() or not n.between(2, 18).all():
            raise ValueError("complete-fixed-race-cohort-required")


def _numeric(frame, column, *, minimum=None, integer=False):
    raw = frame[column] if column in frame else pd.Series(np.nan, index=frame.index)
    values = pd.to_numeric(raw, errors="coerce")
    valid = np.isfinite(values)
    if minimum is not None:
        valid &= values.ge(minimum)
    if integer:
        valid &= values.eq(np.floor(values))
    return values.where(valid)


def _test(values, predicate):
    return predicate(values).astype("boolean").where(values.notna(), pd.NA)


def _text(frame, column, allowed=None):
    values = frame[column].astype("string") if column in frame else pd.Series(pd.NA, index=frame.index, dtype="string")
    values = values.where(values.notna() & values.str.strip().ne(""))
    if allowed is not None:
        values = values.where(values.isin(allowed))
    return values


def _reduce(series, mode, count):
    known = series.dropna().astype(bool)
    unknown = len(series) - len(known)
    positives = int(known.sum())
    if mode == "any":
        return True if positives else pd.NA if unknown else False
    if mode == "all":
        return False if (len(known) - positives) else pd.NA if unknown else True
    threshold = int(np.ceil(count / 2))
    return True if positives >= threshold else False if positives + unknown < threshold else pd.NA


def _prior_surface(work, mapping, provenance, expected_snapshot):
    previous = pd.Series(pd.NA, index=work.index, dtype="string")
    if mapping is None:
        return previous, {"provided": False, "verified_rows": 0, "reason": "strict-prior-surface-mapping-unavailable"}
    required = {*KEYS, "source_race_id", "source_race_date", "previous_surface"}
    if not required.issubset(mapping) or mapping.duplicated(KEYS).any() or mapping[KEYS].isna().any().any():
        raise ValueError("invalid-prior-surface-mapping")
    if (not provenance or provenance.get("history_boundary") != "source_race_date < target_race_date"
            or not isinstance(expected_snapshot, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_snapshot)
            or provenance.get("snapshot_sha256") != expected_snapshot):
        raise ValueError("prior-surface-source-contract-unproven")
    joined = mapping[list(required)].merge(work[KEYS + ["race_date"]], on=KEYS, how="left", validate="one_to_one", indicator=True)
    if not joined._merge.eq("both").all():
        raise ValueError("prior-surface-keys-outside-cohort")
    source_dates = joined.source_race_date.astype(str)
    if (not source_dates.str.fullmatch(r"\d{8}").all() or not source_dates.between("20130101", "20221231").all()
            or not source_dates.lt(joined.race_date.astype(str)).all() or not joined.previous_surface.isin(["turf", "dirt"]).all()
            or not joined.source_race_id.astype(str).str.fullmatch(r"\d{12}").all()
            or not joined.source_race_id.astype(str).str[:4].eq(source_dates.str[:4]).all()):
        raise ValueError("invalid-same-day-or-future-prior-surface")
    pd.to_datetime(source_dates, format="%Y%m%d", errors="raise")
    proof = work[KEYS + ["safe_days_since_last", "safe_prior_starts"]].merge(joined[KEYS + ["race_date", "source_race_date"]], on=KEYS, how="inner", validate="one_to_one")
    days = (pd.to_datetime(proof.race_date, format="%Y%m%d") - pd.to_datetime(proof.source_race_date, format="%Y%m%d")).dt.days
    if not proof.safe_prior_starts.ge(1).all() or not pd.to_numeric(proof.safe_days_since_last, errors="coerce").eq(days).all():
        raise ValueError("prior-surface-date-not-bound-to-frozen-latest-observed-history")
    ordered = work[KEYS].merge(mapping[KEYS + ["previous_surface"]], how="left", on=KEYS, validate="one_to_one", sort=False)
    return ordered.previous_surface.astype("string"), {"provided": True, "verified_rows": len(mapping),
        "missing_rows": len(work)-len(mapping), "provenance": provenance,
        "limitation": "caller supplies proven latest observed eligible history; complete career coverage not inferred"}


def prepare_segment_frame(clean, source_features, *, class_labels, prior_surface=None,
                          prior_surface_provenance=None, source_snapshot_sha256=None):
    """Return (model_frame, race_flags, runner_flags, manifest), without fitting.

    Only predictor fields enter membership. Labels, if present, are copied for
    later separately authorized evaluation and never read by this function.
    """
    definition = segment_definitions(class_labels)
    _scope(clean, complete=True)
    _scope(source_features)
    safe_columns = [c for c in source_features if c.startswith("safe_")]
    if not set(F8).issubset(safe_columns):
        raise ValueError("all-fixed-F8-columns-required-even-if-missing")
    source = source_features[KEYS + ["race_date"] + safe_columns]
    work = clean.reset_index(drop=True).copy()
    overlap = sorted(set(safe_columns) & set(work))
    joined = work.merge(source, how="left", on=KEYS + ["race_date"], validate="one_to_one", indicator=True, suffixes=("", "__source"), sort=False)
    if len(joined) != len(work) or not joined._merge.eq("both").all():
        raise ValueError("clean-to-source-feature-key-mismatch")
    for name in overlap:
        left, right = joined[name], joined[name + "__source"]
        if not (left.eq(right).fillna(False) | (left.isna() & right.isna())).all():
            raise ValueError("existing-feature-conflicts-with-frozen-source:" + name)
    work = joined.drop(columns=["_merge"] + [c + "__source" for c in overlap])
    n = work.expected_runner_count
    age = _numeric(work, "safe_age", minimum=1, integer=True)
    starts = _numeric(work, "safe_prior_starts", minimum=0, integer=True)
    surface_starts = _numeric(work, "safe_horse_surface_starts", minimum=0, integer=True)
    days = _numeric(work, "safe_days_since_last", minimum=0)
    change = _numeric(work, "safe_distance_change")
    surface = _text(work, "safe_surface", ["turf", "dirt"])
    classes = _text(work, "safe_race_class")
    going = _text(work, "safe_field_condition")
    previous, prior_audit = _prior_surface(work, prior_surface, prior_surface_provenance, source_snapshot_sha256)
    flags = work[KEYS + ["race_date"]].copy()
    flags["age_2_only"] = _test(age, lambda x: x.eq(2))
    flags["age_3_only"] = _test(age, lambda x: x.eq(3))
    for label in ("newcomer", "maiden"):
        flags[label] = classes.isin(class_labels[label]).astype("boolean").where(classes.notna(), pd.NA)
    flags["any_zero_observed_history"] = _test(starts, lambda x: x.eq(0))
    flags["many_one_two_observed_history"] = _test(starts, lambda x: x.between(1, 2))
    for current in ("turf", "dirt"):
        flags["any_first_" + current] = surface.eq(current) & _test(starts, lambda x: x.gt(0)) & _test(surface_starts, lambda x: x.eq(0))
    flags["any_turf_to_dirt"] = surface.eq("dirt") & previous.eq("turf")
    flags["any_dirt_to_turf"] = surface.eq("turf") & previous.eq("dirt")
    flags["any_large_distance_change"] = _test(change, lambda x: x.abs().ge(400))
    flags["any_long_layoff"] = _test(days, lambda x: x.ge(180))
    flags["heavy_going"] = going.isin(HEAVY_LABELS).astype("boolean").where(going.notna(), pd.NA)
    flags["bad_going"] = going.isin(BAD_LABELS).astype("boolean").where(going.notna(), pd.NA)
    flags["small_field"] = n.le(8).astype("boolean")
    flags["large_field"] = n.ge(16).astype("boolean")
    popularity = _numeric(work, "popularity", minimum=1, integer=True)
    odds = _numeric(work, "odds", minimum=1)
    flags["any_longshot"] = _test(popularity, lambda x: x.ge(10)) & _test(odds, lambda x: x.ge(30))
    ranks = {}
    for field, name in [("normalized_market_probability", "market_rank_ordinal"), ("h04_score", "h04_rank_ordinal")]:
        values = _numeric(work, field)
        # Any missing ranking input invalidates that race's entire ordinal order.
        invalid = values.isna().groupby(work.race_id).transform("any")
        ordered = work.assign(_score=values).sort_values(["race_id", "_score", "horse_number"], ascending=[True, False, True], kind="stable")
        ranks[name] = (ordered.groupby("race_id", sort=False).cumcount() + 1).reindex(work.index).astype("Float64").where(~invalid)
        flags[name] = ranks[name]
    flags["rank_divergence_ordinal"] = ranks["market_rank_ordinal"] - ranks["h04_rank_ordinal"]
    flags["any_large_market_H04_rank_divergence"] = flags.rank_divergence_ordinal.abs().ge(5)
    records = []
    for race_id, part in flags.groupby("race_id", sort=False):
        record = {"race_id": race_id, "race_date": part.race_date.iloc[0], "runners": len(part)}
        for name in SEGMENT_NAMES:
            mode = "all" if name in {"age_2_only", "age_3_only", "newcomer", "maiden", "heavy_going", "bad_going"} else "half" if name == "many_one_two_observed_history" else "any"
            value = _reduce(part[name].astype("boolean"), mode, len(part))
            # Race-level class/going must be consistent, not just all predicate-false.
            original = classes if name in {"newcomer", "maiden"} else going if name in {"heavy_going", "bad_going"} else None
            if original is not None and original.loc[part.index].dropna().nunique() > 1:
                value = pd.NA
            record[name] = value
            record[name + "__evaluable"] = value is not pd.NA
        records.append(record)
    race_flags = pd.DataFrame(records)
    for name in SEGMENT_NAMES:
        race_flags[name] = race_flags[name].astype("boolean")
        flags[name] = flags[name].astype("boolean")
    coverage = {name: {"evaluable_races": int(race_flags[name].notna().sum()), "unknown_races": int(race_flags[name].isna().sum()),
                       "qualifying_races": int(race_flags[name].fillna(False).sum())} for name in SEGMENT_NAMES}
    manifest = {**definition, "races": len(race_flags), "runners": len(work), "safe_columns_joined": safe_columns,
                "source_snapshot_sha256": source_snapshot_sha256, "prior_surface": prior_audit,
                "segment_coverage": coverage, "cohort_rows_dropped": 0, "outcomes_or_metrics_used": False,
                "original_fractional_rank_features_preserved": True,
                "scope": "fixed Phase-1-clean historical research cohort; not all real-world races"}
    return work, race_flags, flags, manifest


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_segment_dataset(clean_path, source_dir, output_dir, *, quality_summary_path, expected_quality_sha256,
                          class_labels, phase1_gate=None, prior_surface=None, prior_surface_provenance=None):
    """Gated exclusive output; this function must not run until Phase-1 PASS."""
    clean_path, source_dir, output_dir = Path(clean_path).resolve(), Path(source_dir).resolve(), Path(output_dir).resolve()
    quality = _read(quality_summary_path)
    if _sha(quality_summary_path) != expected_quality_sha256 or quality.get("MODEL_RESEARCH_READY") is not True:
        raise ValueError("Phase-1-quality-gate-not-PASS")
    if (not phase1_gate or Path(phase1_gate.get("quality_summary_path", "")).resolve() != Path(quality_summary_path).resolve()
            or phase1_gate.get("quality_summary_sha256") != expected_quality_sha256):
        raise ValueError("independent-Phase-1-release-binding-required")
    from market_segments.modeling import _quality_gate
    _quality_gate(phase1_gate)
    clean_sha = _sha(clean_path)
    if clean_sha != quality.get("clean_runners_sha256"):
        raise ValueError("clean-cohort-not-bound-to-Phase-1-quality-gate")
    if not output_dir.is_relative_to(REPO / "reports/generated/model-experiments") or output_dir.exists():
        raise ValueError("new-isolated-experiment-output-required")
    # Read only the two known research years and only safe predictors/identities.
    manifest_path, dataset_path = source_dir / "dataset_manifest.json", source_dir / "dataset.parquet"
    source = _read(manifest_path)
    if _sha(dataset_path) != source["dataset_sha256"]:
        raise ValueError("source-feature-dataset-hash-mismatch")
    from market_edge.data import _source
    _, _, h04 = _source(source_dir.parent)
    features = h04["feature_columns"]
    if (len(features) != 99 or not set(features).issubset(source["feature_allowlist"])
            or h04["dataset_sha256"] != source["dataset_sha256"] or h04["manifest_sha256"] != _sha(manifest_path)):
        raise ValueError("verified-H04-safe99-source-feature-contract-required")
    source_frame = pd.read_parquet(dataset_path, columns=KEYS + ["race_date"] + features,
                                  filters=[("race_date", ">=", "20210101"), ("race_date", "<=", "20221231")], use_threads=False)
    clean = pd.read_parquet(clean_path, use_threads=False)
    outputs = prepare_segment_frame(clean, source_frame, class_labels=class_labels,
        prior_surface=prior_surface, prior_surface_provenance=prior_surface_provenance,
        source_snapshot_sha256=source["snapshot_sha256"])
    manifest = outputs[3]
    manifest["binding"] = {"quality_summary_path": str(Path(quality_summary_path).resolve()), "quality_summary_sha256": expected_quality_sha256,
        "clean_runners_path": str(clean_path), "clean_runners_sha256": _sha(clean_path),
        "source_dataset_sha256": source["dataset_sha256"], "source_manifest_sha256": _sha(manifest_path),
        "data_module_sha256": _sha(Path(__file__)), "phase1_gate": phase1_gate}
    if (_sha(quality_summary_path) != expected_quality_sha256 or _sha(clean_path) != clean_sha
            or _sha(dataset_path) != source["dataset_sha256"]):
        raise ValueError("quality-or-data-contract-changed-during-preparation")
    _quality_gate(phase1_gate)
    output_dir.mkdir(parents=True, exist_ok=False)
    for frame, name in zip(outputs[:3], ["model_input.parquet", "race_segments.parquet", "runner_segments.parquet"]):
        with (output_dir / name).open("xb") as stream:
            frame.to_parquet(stream, index=False)
    manifest["artifact_sha256"] = {name: _sha(output_dir / name) for name in ["model_input.parquet", "race_segments.parquet", "runner_segments.parquet"]}
    with (output_dir / "data_contract.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("clean-runners", "source-dir", "output-dir", "quality-summary", "class-labels", "phase1-gate"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--expected-quality-sha256", required=True)
    args = parser.parse_args()
    value = build_segment_dataset(args.clean_runners, args.source_dir, args.output_dir,
        quality_summary_path=args.quality_summary, expected_quality_sha256=args.expected_quality_sha256,
        class_labels=_read(args.class_labels), phase1_gate=_read(args.phase1_gate))
    print(json.dumps({"runners": value["runners"], "races": value["races"], "segment_count": value["segment_count"]}))


if __name__ == "__main__":
    main()
