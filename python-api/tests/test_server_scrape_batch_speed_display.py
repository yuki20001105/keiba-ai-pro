"""Whole-period UI telemetry must retain the child-attempt scope."""
from scraping.server_batch import batch_progress


def test_current_child_speed_is_not_a_whole_batch_rate():
    current = {"status": "running", "progress": {"done": 3, "total": 31,
        "race_task_concurrency": 3, "max_concurrent_http_requests": 1,
        "http_pacing_scope": "provider-family", "fetch_metrics_scope": "current_worker_attempt",
        "fetch_metrics": {"network_requests": 4}, "attempt_elapsed_sec": 8.0,
        "network_requests_per_min": 30., "current_date": "20260203",
        "current_date_total_races": 24, "current_date_processed_races": 4}}
    progress = batch_progress([("20260101", "20260131"), ("20260201", "20260228")],
        {0: {"races_collected": 99, "elapsed_time": 1000}}, current)
    assert progress["race_task_concurrency"] == 3
    assert progress["max_concurrent_http_requests"] == 1
    assert progress["fetch_metrics_scope"] == "current_worker_attempt"
    assert progress["network_requests_per_min"] == 30.
    assert progress["attempt_elapsed_sec"] == 8.
    assert progress["fetch_metrics"]["network_requests"] == 4
    assert progress["current_date_processed_races"] == 4


def test_unstarted_batch_does_not_invent_parallelism_or_speed():
    progress = batch_progress([("20260101", "20260131")], {})
    for key in ("race_task_concurrency", "max_concurrent_http_requests", "network_requests_per_min",
                "attempt_elapsed_sec", "fetch_metrics_scope"):
        assert progress[key] is None
