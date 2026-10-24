from scraping.acquisition_outcome import acquisition_failure, list_failure


def test_diagnostics_exclude_private_transport_details():
    detail = list_failure("20260919", "current_race_list_unconfirmed", [
        {"status": 400, "headers": {"Authorization": "secret"}},
        {"status": 400}, {"status": 200}, {"status": "bad"}, {"status": -1},
    ])
    assert detail == {"date": "20260919", "code": "current_race_list_unconfirmed", "http_statuses": [400]}
    failure = acquisition_failure({"quality_complete": False}, [detail])
    assert failure["failure_code"] == "race_list_unavailable"
    assert "HTTP 400" in failure["failure_reason"]


def test_success_ignores_old_diagnostic_and_unconfirmed_has_no_fabricated_http_status():
    assert acquisition_failure({"quality_complete": True}, [{"http_statuses": [400]}]) == {}
    failure = acquisition_failure({"quality_complete": False}, [list_failure("20260919", "empty")])
    assert "HTTP" not in failure["failure_reason"]
    assert "再試行" in failure["failure_reason"]
