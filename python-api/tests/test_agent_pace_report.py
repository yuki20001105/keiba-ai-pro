from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


def report_api():
    path = Path(__file__).resolve().parents[2] / "scripts" / "analyze_agent_pace_research.py"
    spec = spec_from_file_location("agent_pace_report", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fitted(identifier, nll, features, top1):
    return {"id": identifier, "specification": {"params": {}}, "folds": [
        {"year": year, "features": features, "metrics": {
            "nll": nll, "brier": .8, "top1": top1, "ece": .01}}
        for year in (2022, 2023, 2024)]}


def document():
    return {
        "root": "research",
        "selection": {"winner": "simple", "numerical_minimum": "complex"},
        "development": [fitted("win80", 2.2, 80, .2),
                        fitted("complex", 2.1, 97, .3),
                        fitted("simple", 2.1005, 46, .29)],
        "confirmation": [fitted("win80", 2.3, 80, .2), fitted("simple", 2.4, 46, .19)],
        "verification": {"verified": True, "model_improved": False,
                         "production_eligible": False, "production_unchanged": True,
                         "comparisons_vs_win80": {"2026": {
                             "nll_improvement": -.1, "nll_improvement_ci95": [-.2, .01],
                             "paired_dates": 88, "races": 2444}}},
        "summary": {"cycles": 10, "new_specifications": 30, "new_development_folds": 90,
                    "fit_reservations": 184, "started": 100., "finished": 400.,
                    "selected_research_bundle": "research\\simple.joblib",
                    "selected_research_bundle_sha256": "abc"},
        "cycles": [{"cycle": 1, "hypothesis": "test | controlled\ncomparison",
                    "reasoning": "evidence", "expected_outcome": "lower NLL",
                    "results": [fitted("simple", 2.1005, 46, .29)],
                    "interim_selection": {"winner": "simple"}}],
        "model_card": {"feature_columns": ["one", "two"], "parameters": {}},
    }


def test_report_keeps_parsimonious_choice_separate_and_does_not_claim_confirmation_gain():
    text = report_api().render(document())
    assert "**simple**" in text
    assert "complex" in text
    assert "2.100500" in text
    assert "2.400000" in text
    assert "-0.200000" in text
    assert "確認期間での改善判定: **未達**" in text
    assert "確認期間での改善判定: **達成**" not in text
    assert "test \\| controlled comparison" in text


def test_report_refuses_unverified_or_production_changed_evidence():
    module = report_api()
    for key, value in (("verified", False), ("production_unchanged", False),
                       ("production_eligible", True)):
        data = document()
        data["verification"][key] = value
        with pytest.raises(ValueError, match="verified-research"):
            module.render(data)


def test_report_refuses_missing_selected_confirmation_instead_of_using_numeric_winner():
    data = document()
    data["confirmation"] = [fitted("win80", 2.3, 80, .2), fitted("complex", 2.0, 97, .3)]
    with pytest.raises(ValueError, match="selected-confirmation"):
        report_api().render(data)


def test_report_discloses_confirmation_accuracy_and_calibration_tradeoffs():
    data = document()
    for fold in data["confirmation"][1]["folds"]:
        fold["metrics"]["ece"] = .04
    text = report_api().render(data)
    assert "-1.000ポイント" in text
    assert "ECEは0.010000→0.040000" in text
    assert "ECEの改善は確認できていない" in text
