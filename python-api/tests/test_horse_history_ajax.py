import json

import pytest

from scraping.horse_history_ajax import combine_profile_history, history_url


PROFILE = '<html><link rel="canonical" href="https://db.netkeiba.com/horse/2022104614/"><div id="horse_results_box"></div></html>'


def test_validated_child_and_public_response_are_combined_without_inventing_rows():
    output = combine_profile_history(
        PROFILE, json.dumps({"status": "OK", "data": "<table><tr><td>2025/12/28</td></tr></table>"}),
        "2022104614")
    assert "2025/12/28" in output
    assert history_url("2022104614").endswith("input=UTF-8&output=json&id=2022104614")


@pytest.mark.parametrize("payload", [{"status": "NG", "data": "blocked"}, {"status": "OK", "data": ""},
                                     {"status": "OK", "data": []}])
def test_unsuccessful_or_nonhtml_payloads_are_not_accepted(payload):
    with pytest.raises(ValueError, match="not-successful-html"):
        combine_profile_history(PROFILE, json.dumps(payload), "2022104614")


def test_wrong_profile_identity_is_rejected_before_combining_response():
    with pytest.raises(ValueError, match="child-conflict"):
        combine_profile_history(PROFILE, '{"status":"OK","data":"<table/>"}', "2022104615")
