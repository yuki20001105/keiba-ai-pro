from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib

import pytest

from scraping.last3f_evidence import classify_last3f, clock_column, parse_last3f_evidence

RID = "202101010101"
URL = f"https://race.netkeiba.com/race/result.html?race_id={RID}"
RUNNERS = [{"horse_number": 1, "horse_id": "2018100001", "horse_name": "テストワン"},
           {"horse_number": 2, "horse_id": "2018100002", "horse_name": "テストツー"}]


def document(*, header="後3F", values=("34.8", "**"), date="2021年6月12日", count="2頭", canonical=URL):
    return f'''<html><head><title>結果 | {date} 札幌1R</title>
<link rel="canonical" href="{canonical}"><meta property="og:url" content="{canonical}"></head>
<body><div class="RaceData02">1回 札幌 1日目 {count}</div><table class="RaceTable01">
<tr><th>着順</th><th>馬番</th><th>馬名</th><th>{header}</th></tr>
<tr><td>1</td><td>1</td><td><a href="https://db.netkeiba.com/horse/2018100001/">テストワン</a></td><td>{values[0]}</td></tr>
<tr><td>2</td><td>2</td><td><a href="/horse/result/recent/2018100002/">テストツー</a></td><td>{values[1]}</td></tr>
</table></body></html>'''


def parse(html=None, **changes):
    html = document() if html is None else html
    args = {"race_id": RID, "source_url": URL, "observed_at_utc": "2026-10-17T07:05:01Z",
            "raw_body_sha256": hashlib.sha256(html.encode()).hexdigest(), "expected_date": "20210612",
            "expected_runners": deepcopy(RUNNERS), "raw_body": html.encode()}
    args.update(changes)
    return parse_last3f_evidence(html, **args)


@pytest.mark.parametrize("label", ["上り", "上がり", "上り3F", "上がり3F", "後3F", " 上 が り ３Ｆ "])
def test_exact_clock_headers_are_supported(label):
    assert clock_column(["上がり指数", label, "単勝"]) == 1


@pytest.mark.parametrize("labels", [["上がり指数"], ["上り指数"], ["上り", "後3F"], ["タイム"], [], ["推定上がり3F"]])
def test_unknown_or_ambiguous_headers_fail_closed(labels):
    assert clock_column(labels) == -1


@pytest.mark.parametrize("raw,state,value", [("34.8", "valid", 34.8), ("３４．８", "valid", 34.8),
    ("**", "masked", None), ("***", "masked", None), (None, "missing", None), ("-", "missing", None),
    ("", "missing", None), ("0", "invalid", None), ("-1", "invalid", None), ("nan", "invalid", None),
    ("inf", "invalid", None), ("1e2", "invalid", None), ("9.9", "invalid", None), ("100", "invalid", None),
    ("**34", "invalid", None), ("取消", "invalid", None)])
def test_missing_masked_and_invalid_are_not_zero_filled(raw, state, value):
    assert classify_last3f(raw) == (state, value)


def test_complete_identity_and_source_hashes_are_bound_without_historical_claim():
    html = document()
    got = parse(html)
    assert got["race_date"] == "20210612" and got["runner_count"] == 2
    assert got["rows"][0]["last3f_seconds"] == 34.8
    assert got["rows"][1]["last3f_state"] == "masked"
    assert got["rows"][1]["last3f_seconds"] is None
    assert got["source"]["raw_body_sha256"] == hashlib.sha256(html.encode()).hexdigest()
    assert got["source"]["decoded_html_sha256"] == hashlib.sha256(html.encode()).hexdigest()
    assert got["source"]["raw_digest_verification"] == "verified_against_bytes"
    assert got["source"]["received_at_utc"].startswith("2026")
    assert got["source"]["historical_capture_time_proven"] is False


def test_no_original_bytes_does_not_claim_local_raw_digest_verification():
    assert parse(raw_body=None)["source"]["raw_digest_verification"] == "caller_digest_supplied"


@pytest.mark.parametrize("changes", [
    {"source_url": URL.replace("https:", "http:")}, {"source_url": URL.replace("race.netkeiba.com", "evil.example")},
    {"source_url": URL + "&cookie=secret"}, {"source_url": URL + "&race_id=" + RID},
    {"source_url": URL.replace("result.html", "shutuba.html")},
    {"source_url": URL.replace(RID, "202101010102")},
    {"observed_at_utc": "2026-10-17T16:05:00"}, {"observed_at_utc": "not-time"},
    {"raw_body_sha256": "0" * 64}, {"raw_body_sha256": "abc"},
    {"expected_date": "20210613"}, {"expected_date": "20210631"},
    {"expected_date": "20200612"}, {"http_status": 403}, {"http_status": 429},
    {"expected_runners": RUNNERS[:1]}, {"expected_runners": [RUNNERS[0], RUNNERS[0]]},
])
def test_metadata_mismatches_reject_the_entire_page(changes):
    with pytest.raises(ValueError):
        parse(**changes)


@pytest.mark.parametrize("html", [
    document(canonical=URL.replace(RID, "202101010102")),
    document().replace('<link rel="canonical" href="' + URL + '">', '').replace('<meta property="og:url" content="' + URL + '">', ''),
    document(date="2021年6月13日"), document(date="日付不明"), document(count="3頭"),
    document(count="2頭 3頭"), document(header="上り指数"),
    document().replace("<th>後3F</th>", "<th>後3F</th><th>上り</th>"),
    document().replace("2018100002", "2018100099"),
    document().replace("テストツー", "別の馬"),
    document().replace("<td>2</td><td>2</td>", "<td>2</td><td>1</td>"),
    document().replace("/horse/result/recent/2018100002/", "/horse/unknown/2018100002/"),
])
def test_canonical_date_header_and_every_horse_must_match(html):
    with pytest.raises(ValueError):
        parse(html)


def test_known_db_source_variant_and_absent_declared_count_are_explicit():
    url = f"https://db.netkeiba.com/race/{RID}/"
    html = document(canonical=url, count="フルゲート18頭").replace("RaceTable01", "race_table_01")
    got = parse(html, source_url=url)
    assert got["declared_runner_count_available"] is False
    assert got["source"]["all_runner_identity_verified"] is True


def test_conflicting_visible_dates_reject_even_if_title_matches():
    html = document().replace("1回 札幌", "2021年6月13日 1回 札幌")
    with pytest.raises(ValueError, match="visible-source-date-not-unique"):
        parse(html)


def test_two_result_tables_are_not_arbitrarily_chosen():
    html = document()
    table = html[html.index('<table'):html.index('</table>') + len('</table>')]
    with pytest.raises(ValueError, match="result-table-not-unique"):
        parse(html.replace("</body>", table + "</body>"))


def test_unknown_clocks_are_retained_as_invalid_not_converted():
    got = parse(document(values=("bad", "0")))
    assert all(h["last3f_state"] == "invalid" and h["last3f_seconds"] is None for h in got["rows"])


def test_expected_runner_input_is_not_modified():
    before = deepcopy(RUNNERS)
    parse(expected_runners=RUNNERS)
    assert RUNNERS == before


def legacy_html():
    return document().replace('<link rel="canonical" href="' + URL + '">', '').replace('<meta property="og:url" content="' + URL + '">', '')


def transport_receipt(html):
    return {"normalized_url": URL, "status": 200, "body_sha256": hashlib.sha256(html.encode()).hexdigest(),
            "fetched_at": datetime.fromisoformat("2026-10-17T07:05:01+00:00").timestamp()}


def test_explicit_legacy_cache_grade_requires_transport_bytes_date_and_all_runners():
    html = legacy_html()
    got = parse(html, allow_cached_transport_identity=True, cache_receipt=transport_receipt(html))
    assert got["source"]["identity_grade"] == "cached_transport_url_plus_visible_date_and_all_runners"
    assert got["source"]["canonical_identity_verified"] is False
    assert got["source"]["cached_transport_receipt"]["raw_bytes_digest_verified"] is True
    assert got["source"]["all_runner_identity_verified"] is True
    assert got["source"]["historical_capture_time_proven"] is False


@pytest.mark.parametrize("patch", [
    {"normalized_url": URL.replace(RID, "202101010102")}, {"status": 403}, {"status": True},
    {"body_sha256": "0" * 64}, {"fetched_at": None}, {"fetched_at": True},
    {"fetched_at": float("nan")}, {"fetched_at": float("inf")}, {"fetched_at": 0},
    {"fetched_at": "2026-10-17T07:05:01Z"}, {"fetched_at": 1},
])
def test_legacy_cache_receipt_mismatches_do_not_gain_identity_permission(patch):
    html = legacy_html()
    receipt = transport_receipt(html)
    receipt.update(patch)
    with pytest.raises(ValueError):
        parse(html, allow_cached_transport_identity=True, cache_receipt=receipt)


@pytest.mark.parametrize("changes", [
    {"allow_cached_transport_identity": True},
    {"allow_cached_transport_identity": True, "cache_receipt": {}},
    {"allow_cached_transport_identity": True, "cache_receipt": None},
    {"allow_cached_transport_identity": "true"},
])
def test_no_implicit_or_unreceipted_legacy_fallback(changes):
    with pytest.raises(ValueError):
        parse(legacy_html(), **changes)


def test_legacy_cache_original_bytes_are_required_even_with_supplied_digest():
    html = legacy_html()
    with pytest.raises(ValueError, match="receipt-and-bytes-required"):
        parse(html, allow_cached_transport_identity=True, cache_receipt=transport_receipt(html), raw_body=None)


def test_legacy_option_never_ignores_a_present_contradictory_canonical():
    html = document(canonical=URL.replace(RID, "202101010102"))
    with pytest.raises(ValueError, match="canonical-race-id-mismatch"):
        parse(html, allow_cached_transport_identity=True, cache_receipt=transport_receipt(html))


@pytest.mark.parametrize("replace", [("2021年6月12日", "日付不明"), ("テストツー", "違う馬"), ("2頭", "3頭")])
def test_transport_identity_does_not_relax_visible_date_runner_or_count(replace):
    html = legacy_html().replace(*replace)
    with pytest.raises(ValueError):
        parse(html, allow_cached_transport_identity=True, cache_receipt=transport_receipt(html))
