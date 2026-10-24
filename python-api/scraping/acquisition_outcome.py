"""Small, non-sensitive acquisition diagnostics for the prediction screen."""
from __future__ import annotations


def list_failure(date: str, code: str, attempts: list[dict] | None = None) -> dict:
    # Never forward response bodies, URLs, arbitrary exception text or headers.
    statuses = sorted({
        item["status"] for item in (attempts or [])
        if isinstance(item, dict) and isinstance(item.get("status"), int)
        and 400 <= item["status"] <= 599
    })
    return {"date": date, "code": code, "http_statuses": statuses}


def acquisition_failure(quality_audit: dict, list_failures: list[dict]) -> dict:
    if quality_audit.get("quality_complete") is True:
        return {}
    if list_failures:
        statuses = sorted({status for item in list_failures for status in item.get("http_statuses", [])})
        http = f"（HTTP {', '.join(str(status) for status in statuses)}）" if statuses else ""
        return {
            "failure_code": "race_list_unavailable",
            "failure_reason": f"指定日のレース一覧を取得できませんでした{http}。時間をおいて再試行してください。",
        }
    return {
        "failure_code": "acquisition_incomplete",
        "failure_reason": "データの一部が未取得・未確定です。取得済みのレースは確認できます。",
    }
