from pathlib import Path

import pytest

from feature_provenance.speed import FIELDS, SOURCE_PATHS, formula_audit, replay_display_parser, source_objects


@pytest.fixture(scope="module")
def sources():
    return source_objects(Path(__file__).resolve().parents[2])


@pytest.mark.parametrize("text,expected", [("108", 108), ("-5", -5), ("0", 0), ("88*", 88), ("", None),
                                           ("-", None), ("未", None), ("**", None), ("***", None), ("88.5", None), ("unknown", None)])
def test_exact_archived_display_parser(sources, text, expected):
    assert replay_display_parser(sources[SOURCE_PATHS[0]]["text"], "<table><tr><td>" + text + "</td></tr></table>") == expected


def test_sort_key_not_used_as_actual_index(sources):
    html = '<table><tr><td><span class="Sort_Function_Data_Hidden">1108</span>108</td></tr></table>'
    assert replay_display_parser(sources[SOURCE_PATHS[0]]["text"], html) == 108


def test_unknown_formula_stops_all_reconstruction(sources):
    report = formula_audit(sources, current_feature_source="_ = speed_figures_df", current_producer_exists=False)
    assert report["classification"] == "B"
    assert report["RECONSTRUCTION_VALIDATED"] is False
    assert report["single_start_formula"]["raw_required_fields"] is None
    assert report["single_start_formula"]["expression"] is None
    assert set(FIELDS) == {item["column"] for item in report["aggregates"]}
    assert all(not item["formal_aggregation_verified"] for item in report["aggregates"])
    assert report["legacy_feature_usage"]["avg_5_index_used"] is False
    assert report["reconstruction"]["RECONSTRUCTION_VALIDATED"] is False
    assert report["reconstruction"]["comparison_pairs"] == 0
    assert report["reconstruction"]["MAE"] is None
    assert report["reconstruction"]["evaluation_allowed"] is False
    assert report["required_point_in_time_contract"]["infer_no_history_from_missing_provider_index_permitted"] is False
    assert report["official_public_sources"]
    average = next(x for x in report["aggregates"] if x["column"] == "avg_5_index")
    assert "not five most recent same-surface" in average["public_same_surface_rule"]
    assert not average["formal_aggregation_verified"]


def test_source_drift_requires_new_audit(sources):
    changed = {key: dict(value) for key, value in sources.items()}
    changed[SOURCE_PATHS[0]]["text"] = changed[SOURCE_PATHS[0]]["text"].replace("sk__max_index", "unrecognized_index")
    with pytest.raises(ValueError, match="contract"):
        formula_audit(changed, current_feature_source="_ = speed_figures_df", current_producer_exists=False)
