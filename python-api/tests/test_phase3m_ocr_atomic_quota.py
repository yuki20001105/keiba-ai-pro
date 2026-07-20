from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT
    / "supabase"
    / "bootstrap"
    / "v1"
    / "migrations"
    / "20260720143300_ocr_quota_reservation.sql"
)
MANIFEST = ROOT / "supabase" / "bootstrap" / "v1" / "manifest.json"
ROUTE = ROOT / "src" / "app" / "api" / "ocr" / "route.ts"
ROUTE_TEST = ROOT / "src" / "__tests__" / "ocr-atomic-quota.test.ts"


def _normalized(path: Path) -> str:
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8")).lower()


def test_manifest_appends_ocr_quota_after_global_function_hardening() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    migrations = manifest["migrations"]
    versions = [entry["version"] for entry in migrations]

    assert versions == sorted(versions)
    assert versions.index("20260720143200") < versions.index("20260720143300")
    ocr_entry = next(entry for entry in migrations if entry["version"] == "20260720143300")
    assert ocr_entry["path"].endswith("20260720143300_ocr_quota_reservation.sql")


def test_rpc_serializes_profile_quota_and_has_a_strict_one_row_contract() -> None:
    sql = _normalized(MIGRATION)

    assert "for update" in sql
    assert "returns table ( allowed boolean, used_count integer, monthly_limit integer, reset_at timestamptz )" in sql
    assert "if v_used >= v_limit then" in sql
    assert "v_used := v_used + 1" in sql
    assert sql.index("for update") < sql.index("v_used := v_used + 1")
    assert "raise exception using" in sql
    assert "ocr_quota_profile_not_found" in sql
    assert "ocr_quota_profile_invalid" in sql


def test_route_reserves_before_vision_and_maps_quota_failures_closed() -> None:
    route = ROUTE.read_text(encoding="utf-8")

    assert "supabase.rpc(" in route
    assert "'consume_ocr_quota'" in route
    assert route.index("await image.arrayBuffer()") < route.index("supabase.rpc(")
    assert route.index("supabase.rpc(") < route.index("visionClient.textDetection(buffer)")
    assert "OCR_MAX_IMAGE_BYTES" in route
    assert "OCR_ALLOWED_IMAGE_TYPES" in route
    assert "hasExpectedImageSignature" in route
    assert "OCR quota service is unavailable" in route
    assert "OCR quota service returned an invalid response" in route
    assert "Monthly OCR usage limit reached" in route
    assert "Only pre-Vision local failures avoid" in route
    assert "{ error: 'OCR processing failed' }" in route
    assert "error.message" not in route
    assert "{ status: 503 }" in route
    assert "{ status: 429 }" in route
    assert ".from('profiles')" not in route


def test_route_regression_exercises_concurrency_and_malformed_rpc_rows() -> None:
    test_source = ROUTE_TEST.read_text(encoding="utf-8")

    assert "Promise.all" in test_source
    assert "only the allowed request reaches Vision" in test_source
    assert "mockVisionTextDetection).toHaveBeenCalledTimes(1)" in test_source
    assert "fails closed with 503" in test_source
    assert "inconsistent denial" in test_source
    assert "without reserving quota" in test_source
    assert "local image-buffer preparation fails" in test_source
    assert "Vision attempt fails and returns no provider detail" in test_source
    assert "internal pre-reservation failure" in test_source
