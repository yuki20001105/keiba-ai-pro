from scraping.race_list import extract_race_ids


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
