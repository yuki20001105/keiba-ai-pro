from urllib.parse import parse_qs, urlparse

from scraping.race_list import (
    build_mobile_month_url,
    extract_mobile_max_page,
    extract_mobile_race_entries,
    extract_race_ids,
)


def test_extract_race_ids_supports_historical_and_current_links() -> None:
    html = """
    <a href="/race/202601010101/">result</a>
    <a href="/race/shutuba.html?race_id=202601010102">card</a>
    <a href="/race/result.html?race_id=202601010103&rf=race_list">result</a>
    """

    assert extract_race_ids(html) == [
        "202601010101",
        "202601010102",
        "202601010103",
    ]


def test_extract_race_ids_deduplicates_in_document_order() -> None:
    html = """
    <a href="/race/202601010101/">one</a>
    <a href="/race/202601010101/">duplicate</a>
    <a href="/race/shutuba.html?race_id=202601010101">duplicate format</a>
    """

    assert extract_race_ids(html) == ["202601010101"]


def test_mobile_month_url_is_date_bounded_and_jra_only() -> None:
    query = parse_qs(urlparse(build_mobile_month_url("20250105", page=12)).query)

    assert query["start_year"] == ["2025"]
    assert query["start_mon"] == ["1"]
    assert query["end_year"] == ["2025"]
    assert query["end_mon"] == ["1"]
    assert query["track[]"] == ["1", "2", "3"]
    assert query["jyo[]"] == [f"{value:02d}" for value in range(1, 11)]
    assert query["page"] == ["12"]


def test_extract_mobile_race_entries_binds_id_to_displayed_date() -> None:
    html = """
    <a href="https://db.sp.netkeiba.com/race/202506010101/">
      <div class="DataBox_01"><h2>1R</h2><p>2025/1/5 中山 右1200m</p></div>
    </a>
    <a href="https://db.sp.netkeiba.com/race/202507010101/">
      <div class="DataBox_01"><h2>1R</h2><p>2025/01/06 京都 右1400m</p></div>
    </a>
    <a href="https://db.sp.netkeiba.com/race/202506010101/">
      <p>2025/1/5 duplicate</p>
    </a>
    """

    assert extract_mobile_race_entries(html) == [
        ("20250105", "202506010101"),
        ("20250106", "202507010101"),
    ]


def test_extract_mobile_max_page_uses_highest_advertised_page() -> None:
    html = """
    <a href="/?pid=race_list&amp;page=2">2</a>
    <a href="/?pid=race_list&amp;page=12">最後</a>
    """

    assert extract_mobile_max_page(html) == 12
