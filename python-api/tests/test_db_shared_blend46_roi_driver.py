"""Synthetic driver integration: no live DB, real fitting or production writes.

The load-data fixture compiles the actual function with only its two immutable
full-cohort cardinality literals changed to the tiny fixture sizes. A separate
test verifies those production literals remain 453857 and 32519. All join and
exclusion logic is otherwise the unchanged driver implementation.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import importlib.util
import inspect
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("isolated_roi_driver_tests", ROOT / "scripts/evaluate_db_shared_blend46_roi.py")
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)
import db_shared_blend46_betting as BETTING


def frame(dates=("20250105", "20250106")):
    rows = []
    for race, date in enumerate(dates):
        for horse in (1, 2, 3):
            rows.append({"race_id": f"{date}-r{race}", "race_date": date, "horse_number": horse,
                         "horse_id": f"h{race}-{horse}", "finish": horse, "venue": "05", "surface": "turf",
                         "odds": (2.5, 4., 6.)[horse - 1], "popularity": horse,
                         "probability": (.5, .3, .2)[horse - 1], "raw_score": (1.1, .7, .3)[horse - 1],
                         "expected_runner_count": 3, "tansho_payout": 260. if horse == 1 else np.nan})
    return pd.DataFrame(rows)


@pytest.fixture
def preflight_tree(tmp_path, monkeypatch):
    study = tmp_path / "reports/generated/frozen-study"
    study.mkdir(parents=True)
    protected = {"active_model_id": "unchanged", "forward_pins": {"p": "frozen"}}
    result = {"status": "completed", "roi_evaluated": False, "models_research_only": True,
              "fold_results": [{} for _ in range(11)], "preservation_after": protected}
    DRIVER.save(study / "results.json", result)
    raw = study / "runners.parquet"
    raw.write_bytes(b"tiny-receipt")
    DRIVER.save(study / "artifact-manifest.json", {"files": {
        "runners.parquet": {"bytes": raw.stat().st_size, "sha256": DRIVER.sha(raw)}}})
    old = tmp_path / "scripts/frozen-old.py"
    old.parent.mkdir(parents=True)
    old.write_text("frozen", encoding="utf-8")
    DRIVER.save(study / "protocol.json", {"source_manifest": {"scripts/frozen-old.py": DRIVER.sha(old)}})
    snapshot = tmp_path / "keiba/data/model_retrain_snapshots"
    snapshot.mkdir(parents=True)
    temporary_db = snapshot / "empty.db"
    temporary_db.write_bytes(b"")
    snapshot_sha = DRIVER.sha(temporary_db)
    actual_db = snapshot / (snapshot_sha + ".db")
    temporary_db.rename(actual_db)
    protocol = {"prediction_run": "reports/generated/frozen-study",
                "prediction_results_sha256": DRIVER.sha(study / "results.json"), "snapshot_sha256": snapshot_sha}
    config = tmp_path / "protocol.json"
    DRIVER.save(config, protocol)
    monkeypatch.setattr(DRIVER, "ROOT", tmp_path)
    monkeypatch.setattr(DRIVER, "CONFIG", config)
    monkeypatch.setitem(sys.modules, "evaluate_db_shared_venues", SimpleNamespace(preservation_receipt=lambda: deepcopy(protected)))
    return SimpleNamespace(study=study, result=result, old=old, snapshot=actual_db, config=config, protocol=protocol, protected=protected)


def overwrite_json(path, value):
    path.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")


def test_preflight_immutable_prior_snapshot_and_protected_state(preflight_tree):
    protocol, source, result, snapshot, before = DRIVER.preflight()
    assert source == preflight_tree.study
    assert snapshot == preflight_tree.snapshot
    assert result["roi_evaluated"] is False
    assert before == preflight_tree.protected


@pytest.mark.parametrize("mutation,error", [
    ("result_sha", "source-result-hash"), ("artifact", "manifest-mismatch"),
    ("old_source", "old-study-source-changed"), ("snapshot", "snapshot-hash-or-journal"),
    ("wal", "snapshot-hash-or-journal"), ("shm", "snapshot-hash-or-journal"),
    ("journal", "snapshot-hash-or-journal"), ("protected", "protected-state-not-original"),
    ("outside", "source-boundary"), ("manifest_outside", "manifest-mismatch"),
])
def test_preflight_fail_closed_on_source_or_protected_changes(preflight_tree, monkeypatch, mutation, error):
    tree = preflight_tree
    if mutation == "result_sha":
        (tree.study / "results.json").write_text("changed", encoding="utf-8")
    elif mutation == "artifact":
        (tree.study / "runners.parquet").write_bytes(b"changed")
    elif mutation == "old_source":
        tree.old.write_text("changed", encoding="utf-8")
    elif mutation == "snapshot":
        tree.snapshot.write_bytes(b"changed")
    elif mutation in ("wal", "shm", "journal"):
        Path(str(tree.snapshot) + "-" + mutation).write_bytes(b"journal")
    elif mutation == "protected":
        monkeypatch.setitem(sys.modules, "evaluate_db_shared_venues", SimpleNamespace(preservation_receipt=lambda: {"different": True}))
    elif mutation == "outside":
        protocol = dict(tree.protocol, prediction_run="outside-study")
        overwrite_json(tree.config, protocol)
    else:
        overwrite_json(tree.study / "artifact-manifest.json", {"files": {
            "../../escaped": {"bytes": 0, "sha256": "0" * 64}}})
    with pytest.raises(ValueError, match=error):
        DRIVER.preflight()


@pytest.mark.parametrize("mutation", ["roi", "research", "fold_count", "status"])
def test_preflight_requires_prior_research_completed_eleven_folds(preflight_tree, mutation):
    result = deepcopy(preflight_tree.result)
    if mutation == "roi":
        result["roi_evaluated"] = True
    elif mutation == "research":
        result["models_research_only"] = False
    elif mutation == "fold_count":
        result["fold_results"].pop()
    else:
        result["status"] = "running"
    path = preflight_tree.study / "results.json"
    overwrite_json(path, result)
    protocol = dict(preflight_tree.protocol, prediction_results_sha256=DRIVER.sha(path))
    overwrite_json(preflight_tree.config, protocol)
    with pytest.raises(ValueError, match="frozen-prediction-contract"):
        DRIVER.preflight()


def tiny_load_function(runners, races):
    source = inspect.getsource(DRIVER.load_data)
    node = ast.parse(source)

    class TinyCardinality(ast.NodeTransformer):
        def visit_Constant(self, value):
            if value.value == 453857:
                value.value = runners
            elif value.value == 32519:
                value.value = races
            return value

    node = TinyCardinality().visit(node)
    ast.fix_missing_locations(node)
    namespace = dict(DRIVER.__dict__)
    exec(compile(node, "tiny-cardinality-only-driver-fixture", "exec"), namespace)
    return namespace["load_data"]


def test_production_source_retains_full_cohort_counts_and_read_only_open():
    tree = ast.parse(inspect.getsource(DRIVER.load_data))
    literals = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)]
    assert 453857 in literals and 32519 in literals
    assert "?mode=ro" in literals
    assert "PRAGMA query_only=ON" in literals
    assert "roi-result-source-join-conflict" in literals
    assert "roi-common-method-cohort" in literals


@pytest.fixture
def load_fixture(tmp_path, monkeypatch):
    source = tmp_path / "frozen-study"
    (source / "primary").mkdir(parents=True)
    base = frame()
    raw_predictions = base.drop(columns=["odds", "popularity", "tansho_payout", "expected_runner_count"])
    predictions = pd.concat([raw_predictions.assign(strategy=kind) for kind in ("global", "venue_only", "blend50")], ignore_index=True)
    predictions = predictions.rename(columns={"probability": "win_probability"}).assign(split="TEST")
    path = source / "primary/Y2025-runners.parquet"
    path.write_bytes(b"fixture")
    state = SimpleNamespace(base=base, predictions=predictions,
                            returns=pd.DataFrame([{"race_id": race, "bet_type": "単勝", "combinations": "1", "payout": 260.}
                                                  for race in base.race_id.unique()]),
                            raw=[], close_count=0, query_only=False, connection_uri=None)
    for row in base.to_dict("records"):
        state.raw.append((row["race_id"], json.dumps({"horse_number": row["horse_number"], "horse_id": row["horse_id"],
            "finish_position": row["finish"], "odds": row["odds"], "popularity": row["popularity"]})))

    class Connection:
        def execute(self, query, params=None):
            if query == "PRAGMA query_only=ON":
                state.query_only = True
                return []
            assert query.startswith("SELECT race_id,data FROM race_results_ultimate")
            return state.raw

        def close(self):
            state.close_count += 1

    def connect(database_uri, **kwargs):
        state.connection_uri = database_uri
        assert kwargs == {"uri": True}
        return Connection()

    monkeypatch.setattr(DRIVER.sqlite3, "connect", connect)
    monkeypatch.setattr(pd, "read_parquet", lambda path: state.predictions.copy())
    monkeypatch.setattr(pd, "read_sql_query", lambda *args, **kwargs: state.returns.copy())
    worker = {"device": "cpu", "feature_count": 46,
              "artifacts": {"runners": {"path": str(path), "sha256": DRIVER.sha(path), "rows": len(predictions)}},
              "fold": {"test_year": 2025}, "partitions": {"TRAIN": {"date_to": "20231231"}, "CAL": {"date_to": "20241231"}}}
    state.source, state.worker, state.snapshot = source, worker, tmp_path / "read-only.db"
    state.load = tiny_load_function(len(base), base.race_id.nunique())
    return state


def load(state):
    state.worker["artifacts"]["runners"]["rows"] = len(state.predictions)
    return state.load(state.source, {"fold_results": [state.worker]}, state.snapshot)


def test_common_base_three_methods_exact_keys_and_read_only_database(load_fixture):
    methods, raw, payouts, audit = load(load_fixture)
    assert set(methods) == {"global", "venue_only", "blend50"}
    keys = [method[["race_id", "horse_number"]].to_dict("records") for method in methods.values()]
    assert keys[0] == keys[1] == keys[2]
    assert audit["input_races"] == 2 and audit["common_cohort"]["race_count"] == 2
    assert load_fixture.query_only is True and load_fixture.close_count == 1
    assert load_fixture.connection_uri.endswith("?mode=ro")
    assert audit["unknown_payout_replaced_with_zero"] is False
    assert audit["odds_settlement_substitution"] is False


def test_pending_race_excluded_not_zeroed_and_retained_in_audit(load_fixture):
    pending = load_fixture.base.race_id.unique()[1]
    load_fixture.returns = load_fixture.returns.loc[load_fixture.returns.race_id.ne(pending)]
    methods, raw, payouts, audit = load(load_fixture)
    assert audit["pending_payout_race_ids"] == [pending]
    assert audit["input_races"] == 2 and audit["common_cohort"]["race_count"] == 1
    assert pending in set(raw.race_id)
    assert not raw.loc[raw.race_id.eq(pending), "race_payout_complete"].any()
    assert raw.loc[raw.race_id.eq(pending), "tansho_payout"].isna().all()
    assert all(pending not in set(value.race_id) for value in methods.values())


@pytest.mark.parametrize("mutation", ["finish", "horse_id", "shutuba", "missing", "duplicate"])
def test_raw_result_source_join_conflicts_fail_closed(load_fixture, mutation):
    state = load_fixture
    race, raw = state.raw[0]
    payload = json.loads(raw)
    if mutation == "finish":
        payload["finish_position"] = 2
    elif mutation == "horse_id":
        payload["horse_id"] = "another-horse"
    elif mutation == "shutuba":
        payload["_shutuba"] = True
    if mutation in ("finish", "horse_id", "shutuba"):
        state.raw[0] = (race, json.dumps(payload))
    elif mutation == "missing":
        state.raw.pop(0)
    else:
        state.raw.append(state.raw[0])
    with pytest.raises(ValueError, match="source-join-conflict|duplicate-source-runner"):
        load(state)
    assert state.close_count == 1


@pytest.mark.parametrize("mutation", ["missing_strategy_runner", "duplicate_prediction", "gpu", "feature_count", "cal_boundary", "payout_winner", "bad_payout", "duplicate_payout"])
def test_prediction_and_payout_join_guards(load_fixture, mutation):
    state = load_fixture
    if mutation == "missing_strategy_runner":
        state.predictions = state.predictions.drop(index=0)
    elif mutation == "duplicate_prediction":
        state.predictions = pd.concat([state.predictions, state.predictions.iloc[[0]]])
    elif mutation == "gpu":
        state.worker["device"] = "gpu"
    elif mutation == "feature_count":
        state.worker["feature_count"] = 47
    elif mutation == "cal_boundary":
        state.worker["partitions"]["CAL"]["date_to"] = "20250105"
    elif mutation == "payout_winner":
        state.returns.loc[0, "combinations"] = "2"
    elif mutation == "bad_payout":
        state.returns.loc[0, "payout"] = 105
    else:
        state.returns = pd.concat([state.returns, state.returns.iloc[[0]]])
    with pytest.raises(ValueError):
        load(state)


def test_whole_bad_market_race_excluded_never_runner_only(load_fixture):
    race, raw = load_fixture.raw[0]
    payload = json.loads(raw)
    payload["odds"] = None
    load_fixture.raw[0] = race, json.dumps(payload)
    methods, raw, payouts, audit = load(load_fixture)
    assert audit["whole_missing_market_race_ids"] == [race]
    assert all(value.race_id.nunique() == 1 and len(value) == 3 for value in methods.values())


def test_whole_deadheat_race_excluded_all_three_probability_methods(load_fixture):
    state = load_fixture
    dead = state.base.race_id.unique()[0]
    mask = state.predictions.race_id.eq(dead)
    state.predictions.loc[mask & state.predictions.horse_number.eq(2), "finish"] = 1
    state.predictions.loc[mask, "win_probability"] = np.nan
    for index, (race, raw) in enumerate(state.raw):
        payload = json.loads(raw)
        if race == dead and payload["horse_number"] == 2:
            payload["finish_position"] = 1
            state.raw[index] = race, json.dumps(payload)
    state.returns = pd.concat([state.returns, pd.DataFrame([{"race_id": dead, "bet_type": "単勝", "combinations": "2", "payout": 130.}])], ignore_index=True)
    methods, raw, payouts, audit = load(state)
    assert audit["whole_dead_heat_race_ids"] == [dead]
    assert dead not in audit["pending_payout_race_ids"]
    assert all(dead not in set(value.race_id) for value in methods.values())


def test_phase_boundaries_preserve_rows_and_input_without_overlapping():
    original = frame(("20160105", "20200105", "20210105", "20220105", "20230105", "20240105", "20250105", "20260913"))
    split = DRIVER.phases(original, {"phases": BETTING.PERIODS})
    assert set(split) == set(BETTING.PERIODS)
    assert all(len(value) == 6 for value in split.values())
    assert sum(len(value) for value in split.values()) == len(original)
    assert max(split["TRAIN"].race_date) < min(split["CAL"].race_date)
    assert max(split["CAL"].race_date) < min(split["SELECT"].race_date)
    assert max(split["SELECT"].race_date) < min(split["TEST"].race_date)


@pytest.mark.parametrize("diagnostic_highest_available", [True, False])
def test_execute_predictions_are_price_inputs_only_and_selection_freezes_before_test(tmp_path, monkeypatch, diagnostic_highest_available):
    protocol = json.loads((ROOT / "config/research/db-shared-blend46-roi-20261012.json").read_text(encoding="utf-8"))
    protocol["bootstrap_repetitions"] = 30
    protocol["snapshot_sha256"] = "s" * 64
    original = frame(("20160105", "20200105", "20210105", "20220105", "20230105", "20240105", "20250105", "20260913"))
    methods = {name: original.copy() for name in ("global", "venue_only", "blend50")}
    all_ranks = original.assign(race_payout_complete=True)
    output = tmp_path / "research-output"
    source = tmp_path / "source"
    source.mkdir()
    DRIVER.save(source / "results.json", {"frozen": True})
    DRIVER.save(source / "artifact-manifest.json", {"files": {}})
    protocol["prediction_results_sha256"] = DRIVER.sha(source / "results.json")
    before = {"active_model_id": "unchanged", "forward": "unchanged"}
    snapshot = tmp_path / "snapshot.db"
    snapshot.write_bytes(b"fixture")
    monkeypatch.setattr(DRIVER, "OUTPUT", output)
    monkeypatch.setattr(DRIVER, "preflight", lambda: (protocol, source, {}, snapshot, deepcopy(before)))
    monkeypatch.setattr(DRIVER, "load_data", lambda *args: (methods, all_ranks, pd.DataFrame({"race_id": []}), {"pending_payout_race_ids": ["pending-retained"]}))
    original_sha = DRIVER.sha
    monkeypatch.setattr(DRIVER, "sha", lambda path: protocol["snapshot_sha256"] if Path(path) == snapshot else original_sha(path))
    monkeypatch.setitem(sys.modules, "evaluate_db_shared_venues", SimpleNamespace(preservation_receipt=lambda: deepcopy(before)))
    import db_shared_blend46_ticket_diagnostics as TICKETS
    monkeypatch.setitem(sys.modules, "db_shared_blend46_ticket_diagnostics", SimpleNamespace(
        __file__=TICKETS.__file__, diagnose_rank_tickets=lambda *args: {"retrospective": True}))
    events = []

    def frozen_path():
        candidates = list(output.glob("*/frozen-selection.json"))
        assert len(candidates) == 1
        return candidates[0]

    def fake_fit(train, cal, *, kind, market_only):
        assert train.race_date.between("20160101", "20201231").all()
        assert cal.race_date.between("20210101", "20221231").all()
        assert not set(train.race_id) & set(cal.race_id)
        events.append(("fit", kind, market_only))
        return {"kind": kind, "market_only": market_only, "research_only": True}

    def fake_predict(artifact, data):
        assert "finish" not in data and "tansho_payout" not in data
        assert data.race_date.between("20230101", "20260913").all()
        events.append(("predict", data.race_date.min()))
        return data.probability.to_numpy(float)

    def bootstrap(cohort, policies, reference_id, **kwargs):
        phase = "TEST" if cohort.race_date.min() >= "20250101" else "SELECT"
        if phase == "TEST":
            assert frozen_path().exists()
        events.append(("bootstrap", phase))
        return BETTING.bootstrap_policy_comparisons(cohort, policies, reference_id, repetitions=30, family_size=kwargs["family_size"])

    def choose(cohort, policies, bootstrap):
        assert cohort.race_date.between("20230101", "20241231").all()
        assert not any(event[:2] == ("bootstrap", "TEST") for event in events)
        selected = BETTING.choose_policy_on_select(cohort, policies, bootstrap)
        # Tiny fixture does not meet the 200-bet selection gate. Preserve no-bet
        # decision but expose a pre-fixed top1 diagnostic just as a normal
        # eligible-but-unprofitable real-data candidate would be exposed.
        if diagnostic_highest_available:
            selected["highest_candidate"] = {"policy_id": next(iter(policies)), "lower_roi_bound": 0., "roi_percent": 0., "bets": 0}
        return selected

    def probability(cohort):
        if cohort.race_date.min() >= "20250101":
            assert frozen_path().exists()
        events.append(("probability", cohort.race_date.min()))
        return BETTING.probability_metrics(cohort)

    fake = SimpleNamespace(**{name: getattr(BETTING, name) for name in dir(BETTING) if not name.startswith("__")})
    fake.__file__ = BETTING.__file__
    fake.fit_offset_model = fake_fit
    fake.predict_offset_model = fake_predict
    fake.bootstrap_policy_comparisons = bootstrap
    fake.choose_policy_on_select = choose
    fake.probability_metrics = probability
    monkeypatch.setitem(sys.modules, "db_shared_blend46_betting", fake)
    result = DRIVER.execute()
    assert result["status"] == "completed"
    assert len([event for event in events if event[0] == "fit"]) == 4
    assert len([event for event in events if event[0] == "predict"]) == 16  # replay for SELECT and TEST
    assert [event for event in events if event[0] == "bootstrap"] == [("bootstrap", "SELECT"), ("bootstrap", "TEST"), ("bootstrap", "TEST")]
    assert result["models_published"] is False and result["models_activated"] is False
    assert result["forward_changed"] is False and result["actual_bets"] == 0
    assert result["data_audit"]["pending_payout_race_ids"] == ["pending-retained"]
    run = frozen_path().parent
    saved = DRIVER.read(run / "frozen-selection.json")
    assert DRIVER.read(run / "frozen-selection-hash.json")["sha256"] == DRIVER.object_hash(saved)
    assert saved == result["frozen_selections"]
    assert (run / "artifact-manifest.json").exists()
    if not diagnostic_highest_available:
        assert all(saved[name]["selected_policy_id"] is None for name in ("overall", "global", "venue_only", "blend50", "logistic_blend", "lightgbm_blend"))
        assert saved["blend50"]["highest_candidate"] is None
        assert result["control_diagnostics"] == []
