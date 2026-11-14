"""Scope-separated reference accounting, isolated from purchase authorization."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import timedelta, timezone
import hashlib
import math
from pathlib import Path
import sqlite3
import uuid

from betting.performance import _database, _digest, _encoded, _now, _owner, _race_date, _settlement
from betting.research_opponent_pairs import opponent_orders
from betting.value_policy import _post_time

POLICY = "fixed-two-reference-v1"
COLORS = ("#60a5fa", "#4ade80", "#a78bfa", "#fb923c", "#f87171", "#22d3ee", "#f472b6")
_DDL = """
CREATE TABLE IF NOT EXISTS paper_simulation_models (
    owner TEXT NOT NULL, model_id TEXT NOT NULL, model_sha256 TEXT NOT NULL,
    cutoff_date TEXT NOT NULL, display_name TEXT NOT NULL, color TEXT NOT NULL,
    training_end_date TEXT, membership_sha256 TEXT,
    PRIMARY KEY(owner, model_id)
);
CREATE TABLE IF NOT EXISTS paper_simulation_records (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, model_id TEXT NOT NULL, race_id TEXT NOT NULL,
    race_date TEXT NOT NULL, origin TEXT NOT NULL, payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL, tickets_json TEXT NOT NULL, total_cost INTEGER NOT NULL,
    status TEXT NOT NULL, actual_return INTEGER, result_json TEXT, created_at TEXT NOT NULL,
    settled_at TEXT, UNIQUE(owner,model_id,race_id)
);
CREATE INDEX IF NOT EXISTS paper_simulation_owner_date
ON paper_simulation_records(owner,race_date,model_id);
"""


@contextmanager
def _ledger(path):
    with _database(path) as db:
        # Individual statements retain the parent's BEGIN IMMEDIATE transaction.
        for statement in _DDL.split(";"):
            if statement.strip():
                db.execute(statement)
        if "training_end_date" not in {row["name"] for row in db.execute("PRAGMA table_info(paper_simulation_models)")}:
            db.execute("ALTER TABLE paper_simulation_models ADD COLUMN training_end_date TEXT")
        if "membership_sha256" not in {row["name"] for row in db.execute("PRAGMA table_info(paper_simulation_models)")}:
            db.execute("ALTER TABLE paper_simulation_models ADD COLUMN membership_sha256 TEXT")
        yield db


def _cutoff(bundle):
    values = [bundle["training_date_to"], bundle["dataset_period_end"]]
    validation = bundle.get("probability_validation") or {}
    values.extend(validation[key] for key in ("calibration_period_end", "validation_period_end") if key in validation)
    details = (bundle.get("evaluation") or {}).get("details") or {}
    if "evaluation_date_to" in details:
        values.append(details["evaluation_date_to"])
    return max(_race_date(value) for value in values)


def record_paper_prediction(db_path, owner, payload, bundle, *, model_sha256, now=None,
                            historical_validation=False, training_evaluation=False, _connection=None):
    owner, now = _owner(owner), _now(now)
    refused = {"eligible": False, "purchase_eligible": False}
    try:
        cutoff = _cutoff(bundle)
        training_end = _race_date(bundle["training_date_to"])
        info, diagnostics = payload["race_info"], payload["input_diagnostics"]
        day = _race_date(info.get("date") or info.get("race_date"))
        model_id = payload["model_id"]
        if training_evaluation:
            report = bundle.get("training_reference_evaluation") or {}
            expected = (report.get("records") or {}).get(str(info["race_id"]))
            if (report.get("schema") != "training-reference-v1"
                    or expected is None or _digest(expected) != _digest(payload)):
                return {**refused, "reason": "training_test_report_unverified"}
        membership = bundle.get("_monthly_reference")
        membership_sha = membership["partition_sha256"] if membership else None
        heldout_test = bool(membership and membership["test_dates"].get(str(info["race_id"])) == day)
        if membership:
            if str(info["race_id"]) in membership["development_races"]:
                return {**refused, "reason": "training_or_calibration_race"}
            if (str(info["race_id"]) in membership["test_dates"] and not heldout_test) or (day <= cutoff and not heldout_test):
                return {**refused, "reason": "test_membership_unverified"}
        hashes = bundle["feature_source_hashes"]
        issues = diagnostics.get("issue_codes")
        count = len(bundle["feature_columns"])
        missing = diagnostics.get("missing_value_rate")
        if day <= training_end and not heldout_test:
            return {**refused, "reason": "training_period", "training_end_date": training_end}
        if day <= cutoff and historical_validation is not True:
            return {**refused, "reason": "training_or_evaluation_period", "cutoff_date": cutoff}
        allowed_issues = {"pre_race_db_capture_unverified", "pre_race_db_capture_failed"}
        if historical_validation is True and day <= cutoff:
            allowed_issues.add("target_not_after_training_evaluation_period")
        if (payload.get("success") is not True or not isinstance(model_id, str) or not model_id
                or not isinstance(model_sha256, str) or len(model_sha256) != 64
                or bundle.get("_shared_artifact_sha256") != model_sha256
                or diagnostics.get("schema") != "prediction-input-contract-v1"
                or diagnostics.get("status") not in {"aligned", "unverified"}
                or diagnostics.get("feature_pipeline") != bundle.get("feature_pipeline")
                or not hashes or diagnostics.get("feature_source_hashes") != hashes
                or count == 0 or diagnostics.get("expected_count") != count
                or diagnostics.get("actual_count") != count
                or diagnostics.get("source_hash_matches") is not True
                or diagnostics.get("semantic_parity_verified") is not True
                or diagnostics.get("current_outcomes_consumed") is not False
                or diagnostics.get("history_available") is not True
                or diagnostics.get("missing_columns") != [] or diagnostics.get("blocked_columns") != []
                or type(missing) not in (int, float) or not math.isfinite(missing) or not 0 <= missing <= 1
                or not isinstance(issues, list)
                or any(code not in allowed_issues for code in issues)):
            return {**refused, "reason": "input_or_model_provenance_unverified"}
        predictions = payload["predictions"]
        if info.get("num_horses") != len(predictions):
            return {**refused, "reason": "incomplete_field"}
        axis, _, hole = opponent_orders([
            {"horse_number": row["horse_number"], "probability": row["p_norm"], "odds": row["odds"]}
            for row in predictions
        ])
        h2, h3 = hole[1:3]
        book = [("ワイド", sorted([axis, h3])),
                ("三連単", [h2, axis, h3]), ("三連単", [h3, axis, h2])]
        race_id = str(info["race_id"])
        if not race_id:
            return {**refused, "reason": "race_identity_missing"}
    except (KeyError, TypeError, ValueError, IndexError):
        return {**refused, "reason": "model_period_or_reference_field_unverified"}
    post = _post_time({**info, "date": day})
    today = now.astimezone(timezone(timedelta(hours=9))).date().isoformat()
    past_date_proven = historical_validation is True and (day < today or training_evaluation is True and day == today)
    if post is None and not past_date_proven:
        return {**refused, "reason": "post_time_unverified"}
    origin = "pre_race" if post is not None and now < post else "historical"
    if day <= cutoff and origin != "historical":
        return {**refused, "reason": "historical_validation_not_historical"}
    tickets = [{"bet_type": kind, "horse_numbers": members, "combination": "-".join(map(str, members)),
                "stake": 500} for kind, members in book]
    frozen = {
        "model_id": model_id, "model_sha256": model_sha256, "cutoff_date": cutoff,
        "training_end_date": training_end,
        "evaluation_scope": "training-test" if training_evaluation else "heldout-test" if heldout_test else "post-validation" if day > cutoff else "non-training-retrospective",
        "membership_sha256": membership_sha,
        "rule_version": POLICY, "rule_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "input_provenance": {key: diagnostics[key] for key in
                             ("feature_pipeline", "feature_source_hashes", "expected_count", "actual_count",
                              "missing_value_rate", "no_history_rows", "current_outcomes_consumed") if key in diagnostics},
        "race_info": {key: info[key] for key in ("race_id", "date", "race_date", "num_horses", "post_time", "race_name", "venue", "race_no") if key in info},
        "predictions": [{key: row[key] for key in ("horse_number", "horse_id", "p_norm", "odds") if key in row} for row in predictions],
    }
    with nullcontext(_connection) if _connection is not None else _ledger(db_path) as db:
        previous = db.execute("SELECT * FROM paper_simulation_records WHERE owner=? AND model_id=? AND race_id=?",
                              (owner, model_id, race_id)).fetchone()
        registered = db.execute("SELECT * FROM paper_simulation_models WHERE owner=? AND model_id=?", (owner, model_id)).fetchone()
        if registered and (registered["model_sha256"] != model_sha256 or registered["cutoff_date"] != cutoff
                           or registered["training_end_date"] not in (None, training_end)
                           or registered["membership_sha256"] != membership_sha):
            return {**refused, "reason": "registered_model_identity_changed"}
        if not registered:
            index = db.execute("SELECT COUNT(*) FROM paper_simulation_models WHERE owner=?", (owner,)).fetchone()[0]
            name = bundle.get("display_name")
            db.execute("INSERT INTO paper_simulation_models "
                       "(owner,model_id,model_sha256,cutoff_date,display_name,color,training_end_date,membership_sha256) VALUES (?,?,?,?,?,?,?,?)",
                       (owner, model_id, model_sha256, cutoff, name if isinstance(name, str) and name else model_id,
                        COLORS[index % len(COLORS)], training_end, membership_sha))
        elif registered["training_end_date"] is None:
            db.execute("UPDATE paper_simulation_models SET training_end_date=? WHERE owner=? AND model_id=?",
                       (training_end, owner, model_id))
        if not previous:
            db.execute(
                "INSERT INTO paper_simulation_records "
                "(id,owner,model_id,race_id,race_date,origin,payload_json,payload_sha256,tickets_json,total_cost,status,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), owner, model_id, race_id, day, origin, _encoded(frozen), _digest(frozen),
                 _encoded(tickets), 1500, "pending", now.isoformat()),
            )
        row = previous or db.execute("SELECT * FROM paper_simulation_records WHERE owner=? AND model_id=? AND race_id=?",
                                     (owner, model_id, race_id)).fetchone()
        return {"eligible": True, "purchase_eligible": False, "record_id": row["id"], "existing": previous is not None,
                "origin": row["origin"], "cutoff_date": cutoff, "total_cost": row["total_cost"], "status": row["status"],
                "rule_version": POLICY}


def _scope(owner, date_from, date_to, origin, year=None):
    owner = _owner(owner)
    if origin not in {"all", "historical", "pre_race", "historical_validation", "combined"}:
        raise ValueError("記録区分が不正です")
    start = _race_date(date_from) if date_from else None
    end = _race_date(date_to) if date_to else None
    if start and end and start > end:
        raise ValueError("期間が不正です")
    boundary = "training_end_date" if origin in {"historical_validation", "combined"} else "cutoff_date"
    clauses, values = [
        "r.owner=?",
        "EXISTS (SELECT 1 FROM paper_simulation_models m WHERE m.owner=r.owner AND m.model_id=r.model_id "
        f"AND ((m.membership_sha256 IS NULL AND r.race_date>m.{boundary}) "
        "OR (m.membership_sha256 IS NOT NULL AND (r.race_date>m.cutoff_date"
        + (" OR (json_extract(r.payload_json,'$.evaluation_scope')='heldout-test' "
           "AND json_extract(r.payload_json,'$.membership_sha256')=m.membership_sha256)"
           if origin in {"historical_validation", "combined"} else "")
        + "))))",
    ], [owner]
    if year is not None:
        if type(year) is not int or not 1900 <= year <= 2200:
            raise ValueError("年が不正です")
        clauses += ["r.race_date>=?", "r.race_date<=?"]
        values += [f"{year}-01-01", f"{year}-12-31"]
    if start:
        clauses.append("r.race_date>=?")
        values.append(start)
    if end:
        clauses.append("r.race_date<=?")
        values.append(end)
    if origin not in {"all", "combined"}:
        clauses.append("r.origin=?")
        values.append("historical" if origin == "historical_validation" else origin)
    return " AND ".join(clauses), values, start, end


def _reconcile(db, results, where, values):
    import json

    for row in db.execute("SELECT r.* FROM paper_simulation_records r WHERE " + where
                          + " AND r.status NOT IN ('settled','skipped')", values).fetchall():
        if _digest(json.loads(row["payload_json"])) != row["payload_sha256"]:
            status, result = "review", {"reason": "保存済み予測の整合性が一致しません"}
        else:
            status, result = _settlement(row, results)
        db.execute("UPDATE paper_simulation_records SET status=?,actual_return=?,result_json=?,settled_at=? WHERE id=? AND owner=?",
                   (status, result.get("actual_return") if status == "settled" else None, _encoded(result),
                    _now(None).isoformat() if status == "settled" else None, row["id"], row["owner"]))


def _models(db, owner, where, values):
    rows = db.execute(
        "SELECT m.* FROM paper_simulation_models m WHERE m.owner=? AND EXISTS "
        "(SELECT 1 FROM paper_simulation_records r WHERE r.model_id=m.model_id AND r.owner=m.owner AND " + where + ") ORDER BY m.model_id",
        [owner, *values],
    )
    return [{key: row[key] for key in ("model_id", "model_sha256", "cutoff_date", "training_end_date", "display_name", "color")} for row in rows]


def _totals(cost=0, returned=0, count=0):
    return {"total_cost": cost, "total_return": returned, "profit": returned - cost,
            "roi": round(returned / cost * 100, 1) if cost else None, "race_count": count}


def _metadata(origin, start, end):
    retrospective = origin == "historical_validation"
    combined = origin == "combined"
    return {"mode": "paper", "source": "combined-reference-simulation" if combined else "historical-validation-simulation" if retrospective else "unseen-simulation",
            "evaluation_scope": "non-training-combined" if combined else "non-training-retrospective" if retrospective else "post-validation",
            "origin": origin, "date_from": start, "date_to": end}


def list_paper_performance(db_path, result_db_path, owner, *, date_from=None, date_to=None, origin="all",
                           model_id=None, race_ids=None):
    where, values, start, end = _scope(owner, date_from, date_to, origin)
    if model_id is not None:
        where += " AND r.model_id=?"
        values.append(model_id)
    if race_ids is not None:
        where += " AND r.race_id IN (SELECT value FROM json_each(?))"
        values.append(_encoded(sorted(race_ids)))
    with _ledger(db_path) as db:
        _reconcile(db, result_db_path, where, values)
        models = {m["model_id"]: {
            **m, **_totals(), "points": [],
            "status_counts": dict.fromkeys(("settled", "pending", "payout_pending", "review", "skipped", "other"), 0),
            "cohorts": {key: {**_totals(), "record_count": 0, "pending_races": 0}
                        for key in ("evaluation", "historical", "pre_race")},
        } for m in _models(db, owner, where, values)}
        daily = db.execute(
            "SELECT r.model_id,r.race_date,SUM(r.total_cost) cost,SUM(r.actual_return) returned,COUNT(*) n "
            "FROM paper_simulation_records r WHERE " + where + " AND r.status='settled' AND r.total_cost>0 "
            "GROUP BY r.model_id,r.race_date ORDER BY r.race_date,r.model_id", values,
        )
        for row in daily:
            model = models[row["model_id"]]
            totals = _totals(model["total_cost"] + row["cost"], model["total_return"] + row["returned"], model["race_count"] + row["n"])
            model.update(totals)
            model["points"].append({"date": row["race_date"], "race_id": row["race_date"], "post_time": None, **totals})
        breakdown = db.execute(
            "SELECT r.model_id,r.status,CASE "
            "WHEN json_extract(r.payload_json,'$.evaluation_scope') IN ('heldout-test','training-test') THEN 'evaluation' "
            "WHEN r.origin='pre_race' THEN 'pre_race' ELSE 'historical' END cohort,"
            "COUNT(*) n,SUM(r.total_cost) cost,SUM(r.actual_return) returned "
            "FROM paper_simulation_records r WHERE " + where
            + " GROUP BY r.model_id,r.status,cohort", values,
        )
        for row in breakdown:
            model = models[row["model_id"]]
            status = row["status"] if row["status"] in model["status_counts"] else "other"
            model["status_counts"][status] += row["n"]
            cohort = model["cohorts"][row["cohort"]]
            cohort["record_count"] += row["n"]
            if status == "settled" and row["cost"] > 0:
                cohort.update(_totals(row["cost"], row["returned"], row["n"]))
            elif status not in {"settled", "skipped"}:
                cohort["pending_races"] += row["n"]
        counts = db.execute(
            "SELECT r.status,COUNT(*) n,SUM(r.total_cost) cost FROM paper_simulation_records r WHERE "
            + where + " GROUP BY r.status", values,
        ).fetchall()
        origins = dict(db.execute("SELECT r.origin,COUNT(*) FROM paper_simulation_records r WHERE " + where + " GROUP BY r.origin", values))
        years = [row[0] for row in db.execute("SELECT DISTINCT substr(r.race_date,1,4) FROM paper_simulation_records r WHERE "
                                             + where + " ORDER BY 1", values)]
        summary = {
            "settled_cost": sum(m["total_cost"] for m in models.values()),
            "settled_return": sum(m["total_return"] for m in models.values()),
            "pending_races": sum(row["n"] for row in counts if row["status"] not in {"settled", "skipped"}),
            "pending_cost": sum(row["cost"] for row in counts if row["status"] not in {"settled", "skipped"}),
            "settled_races": sum(m["race_count"] for m in models.values()), "skipped_races": 0,
        }
    return {"records": [], "total_count": sum(row["n"] for row in counts), "summary": summary,
            "model_performance": {"schema": "model-cumulative-roi-v2", **_metadata(origin, start, end),
                                  "point_granularity": "day", "includes_full_filtered_set": True,
                                  "years": years, "origin_counts": origins, "models": list(models.values())}}


def monthly_paper_performance(db_path, result_db_path, owner, year, *, date_from=None, date_to=None, origin="all"):
    where, values, start, end = _scope(owner, date_from, date_to, origin, year)
    with _ledger(db_path) as db:
        _reconcile(db, result_db_path, where, values)
        # Only the selected year's records are read, never historical curve pages.
        models = {row["model_id"]: {**{key: row[key] for key in ("model_id", "display_name", "color")},
                                   "months": [{"month": month, **_totals()} for month in range(1, 13)]}
                  for row in db.execute("SELECT * FROM paper_simulation_models WHERE owner=? ORDER BY model_id", (owner,))}
        rows = db.execute(
            "SELECT r.model_id,CAST(substr(r.race_date,6,2) AS INTEGER) month,"
            "SUM(r.total_cost) cost,SUM(r.actual_return) returned,COUNT(*) n "
            "FROM paper_simulation_records r WHERE " + where + " AND r.status='settled' AND r.total_cost>0 "
            "GROUP BY r.model_id,month ORDER BY month,r.model_id", values,
        )
        for row in rows:
            models[row["model_id"]]["months"][row["month"] - 1].update(_totals(row["cost"], row["returned"], row["n"]))
    return {"schema": "model-monthly-roi-v1", **_metadata(origin, start, end), "year": year, "models": list(models.values())}
