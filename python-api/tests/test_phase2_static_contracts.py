from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_quota_function_grants_are_service_role_only() -> None:
    single = _read("supabase/migrations/20260228_pred_count.sql")
    batch = _read("supabase/migrations/20260711_pred_count_batch.sql")

    # Protected historical migration must remain unchanged; grant hardening is done in new migration.
    assert "REVOKE ALL ON FUNCTION public.consume_pred_count(UUID) FROM PUBLIC;" not in single
    assert "REVOKE ALL ON FUNCTION public.consume_pred_count(UUID) FROM anon;" not in single
    assert "REVOKE ALL ON FUNCTION public.consume_pred_count(UUID) FROM authenticated;" not in single
    assert "GRANT EXECUTE ON FUNCTION public.consume_pred_count(UUID) TO service_role;" not in single

    assert "REVOKE ALL ON FUNCTION public.consume_pred_count(UUID) FROM PUBLIC;" in batch
    assert "REVOKE ALL ON FUNCTION public.consume_pred_count(UUID) FROM anon;" in batch
    assert "REVOKE ALL ON FUNCTION public.consume_pred_count(UUID) FROM authenticated;" in batch
    assert "GRANT EXECUTE ON FUNCTION public.consume_pred_count(UUID) TO service_role;" in batch

    assert "REVOKE ALL ON FUNCTION public.consume_pred_count_batch(UUID, INT) FROM PUBLIC;" in batch
    assert "REVOKE ALL ON FUNCTION public.consume_pred_count_batch(UUID, INT) FROM anon;" in batch
    assert "REVOKE ALL ON FUNCTION public.consume_pred_count_batch(UUID, INT) FROM authenticated;" in batch
    assert "GRANT EXECUTE ON FUNCTION public.consume_pred_count_batch(UUID, INT) TO service_role;" in batch


# Proxy contract checks are static and fast: required routes must forward Authorization
# and keep backend status propagation semantics.
def test_next_proxies_forward_authorization_and_preserve_status() -> None:
    targets = [
        "src/app/api/debug/race-ids/route.ts",
        "src/app/api/export/bet-list/route.ts",
        "src/app/api/export/data/route.ts",
        "src/app/api/export/db/route.ts",
        "src/app/api/features/catalog/route.ts",
    ]

    for rel in targets:
        body = _read(rel)
        assert "request.headers.get('Authorization')" in body
        assert "headers.Authorization = authorization" in body

    # status passthrough
    debug_route = _read("src/app/api/debug/race-ids/route.ts")
    export_data_route = _read("src/app/api/export/data/route.ts")
    feature_catalog_route = _read("src/app/api/features/catalog/route.ts")
    export_bet_list_route = _read("src/app/api/export/bet-list/route.ts")
    export_db_route = _read("src/app/api/export/db/route.ts")

    assert "status: response.status" in debug_route
    assert "status: response.status" in export_data_route
    assert "status: response.status" in feature_catalog_route
    assert "status: res.status" in export_bet_list_route
    assert "status: response.status" in export_db_route



def test_next_proxy_error_shape_uses_detail() -> None:
    targets = [
        "src/app/api/debug/race-ids/route.ts",
        "src/app/api/export/bet-list/route.ts",
        "src/app/api/export/data/route.ts",
        "src/app/api/export/db/route.ts",
        "src/app/api/features/catalog/route.ts",
    ]

    for rel in targets:
        body = _read(rel)
        assert "NextResponse.json({ detail: message }, { status: 500 })" in body
