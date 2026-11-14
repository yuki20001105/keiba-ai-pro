import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from experiments.features import engineer_features

path = Path(__file__).resolve().parents[2] / "scripts" / "audit_acquisition_adequacy.py"
spec = importlib.util.spec_from_file_location("acquisition_adequacy", path)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def frame():
    data = pd.DataFrame([
        {"race_id": "201506010101", "race_date": "20150101", "horse_id": "A",
         "horse_number": 1, "distance": 1200, "time_seconds": 80, "finish": 2,
         "surface": "dirt", "venue": "Nakayama", "num_horses": 1,
         "jockey_id": "J", "trainer_id": "T", "sire": None, "damsire": None,
         "last_3f_time": None},
    ])
    return engineer_features(data)


def career(**changes):
    return {
        "horse_id": "A", "target_date": "20150101", "profile_sha256": "p", "ajax_sha256": "a",
        "coverage_matches_published_career_count": True, "strictly_prior_started_rows": 0,
        "unknown_prior_start_status_rows": 0, "strictly_prior_race_inventory": [], **changes,
    }


def test_all_features_are_classified_without_calling_empty_store_a_true_debut():
    data, contract = frame()
    for column in contract["feature_sets"]["M03"]:
        reasons = audit.missing_reasons(data, column)
        assert not reasons.eq("unclassified").any()
        resolutions = audit.source_resolution(data, column, reasons, [], {}, {}, data)
        assert not resolutions.eq("verified_no_public_prior_start").any()
    assert audit.missing_reasons(data, "safe_prev2_finish").iloc[0] == "fewer_than_two_prior_stored_starts"
    assert audit.missing_reasons(data, "safe_sire_starts").iloc[0] == "unidentified_entity"


def test_public_no_prior_requires_complete_count_and_known_start_status():
    assert audit.public_no_prior_confirmed(career())
    assert not audit.public_no_prior_confirmed(career(coverage_matches_published_career_count=False))
    assert not audit.public_no_prior_confirmed(career(unknown_prior_start_status_rows=1))
    data, _ = frame()
    reasons = audit.missing_reasons(data, "safe_prev_finish")
    unrelated = career(target_date="20150102")
    resolution = audit.source_resolution(data, "safe_prev_finish", reasons, [unrelated], {}, {}, data)
    assert resolution.iloc[0] == "public_source_not_audited"


def test_inventory_routes_missing_results_not_all_known_starts_and_excludes_nonstarts():
    history = pd.DataFrame([{"race_id": "201406010101", "race_date": "20140101", "horse_id": "A"}])
    rows = [
        {"race_id": "201406010101", "race_date": "20140101", "scope": "jra", "start_status": "started"},
        {"race_id": "201405010101", "race_date": "20140201", "scope": "jra", "start_status": "started"},
        {"race_id": "201444010101", "race_date": "20140301", "scope": "outside_jra", "start_status": "started"},
        {"race_id": "201405010102", "race_date": "20140401", "scope": "jra", "start_status": "not_started"},
        {"race_id": "201405010103", "race_date": "20140501", "scope": "jra", "start_status": "unknown"},
    ]
    plan = audit.compare_inventory(career(strictly_prior_race_inventory=rows), history)
    assert [item["action"] for item in plan] == [
        "eligible_history_already_present", "audit_full_result_before_history_import",
        "outside_jra_contract_separate_scope_review", "confirmed_nonstart_do_not_import_as_a_start",
        "audit_unknown_finish_do_not_assume_nonstart",
    ]
    rows[0]["race_date"] = "20150101"
    with pytest.raises(ValueError, match="current-or-future"):
        audit.compare_inventory(career(strictly_prior_race_inventory=rows), history)


def test_public_old_history_requires_a_new_history_scope_not_a_silent_import():
    history = pd.DataFrame(columns=["race_id", "race_date", "horse_id"])
    rows = [{"race_id": "201206010101", "race_date": "20120101", "scope": "jra", "start_status": "started"}]
    plan = audit.compare_inventory(career(strictly_prior_race_inventory=rows), history)
    assert plan[0]["action"] == "before_history_start_contract_separate_scope_review"


def test_acquired_pedigree_does_not_change_frozen_missing_values_or_claim_completion():
    data, _ = frame()
    original = data.copy(deep=True)
    reasons = audit.missing_reasons(data, "safe_sire_win_rate")
    result = audit.source_resolution(data, "safe_sire_win_rate", reasons, [], {"A": "accepted"}, {}, data)
    assert result.iloc[0] == "pedigree_acquired_pending_materialization"
    pd.testing.assert_frame_equal(data, original)


def test_missing_clock_requires_proofs_for_every_strictly_prior_window_observation():
    history = pd.DataFrame([
        {"race_id": "201406010101", "race_date": "20140101", "horse_id": "A", "last_3f_time": None},
        {"race_id": "201406010102", "race_date": "20150101", "horse_id": "A", "last_3f_time": 35},
    ])
    data, _ = frame()
    reasons = pd.Series(["prior_starts_without_last3f_measurement"], dtype="string")
    assert audit.source_resolution(data, "safe_past3_avg_last3f_time", reasons, [], {}, {}, history).iloc[0] == (
        "public_source_not_audited"
    )
    proof = {("201406010101", "A"): "missing"}
    result = audit.source_resolution(data, "safe_past3_avg_last3f_time", reasons, [], {}, proof, history)
    assert result.iloc[0] == "verified_public_prior_clock_empty"


def test_unknown_missing_counts_fail_instead_of_silently_assigning_an_acquisition_reason():
    data, _ = frame()
    data["safe_prior_starts"] = float("nan")
    with pytest.raises(ValueError, match="unclassified-missing"):
        audit.missing_reasons(data, "safe_prev_finish")


def test_persisted_ledger_covers_every_and_only_missing_cell(tmp_path):
    data, contract = frame()
    features = contract["feature_sets"]["M03"]
    reasons, resolutions = audit.write_cell_audit(tmp_path, data, features, [], {}, {}, data)
    ledger = pd.read_parquet(tmp_path / "missing-cell-ledger.parquet")
    assert len(ledger) == sum(reasons.values()) == int(data[features].isna().sum().sum())
    assert sum(resolutions.values()) == len(ledger)
    assert not ledger.duplicated(["row_index", "feature"]).any()
    for row in ledger.itertuples():
        assert pd.isna(data.at[row.row_index, row.feature])


def test_independent_verifier_rejects_an_entity_reason_on_a_previous_race_feature():
    path = Path(__file__).resolve().parents[2] / "scripts" / "verify_acquisition_adequacy.py"
    spec = importlib.util.spec_from_file_location("acquisition_adequacy_replay", path)
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    data, _ = frame()
    part = pd.DataFrame([{
        "row_index": 0, "feature": "safe_prev_finish",
        "reason": "identified_entity_without_prior_stored_results",
    }])
    with pytest.raises(ValueError, match="invalid-cell-reason"):
        replay.check_reasons(part, data)
