from scraping.fetch_pipeline import decode_html_body


def test_decode_html_body_uses_utf8_meta_charset() -> None:
    html = '<html><head><meta charset="utf-8"></head><body>3歳未勝利 ダ1000m</body></html>'

    decoded = decode_html_body(html.encode("utf-8"))

    assert "3歳未勝利" in decoded
    assert "ダ1000m" in decoded
    assert "�" not in decoded


def test_decode_html_body_uses_euc_jp_meta_charset() -> None:
    html = '<html><head><meta charset="EUC-JP"></head><body>札幌 芝1200m</body></html>'

    decoded = decode_html_body(html.encode("euc-jp"))

    assert "札幌" in decoded
    assert "芝1200m" in decoded
    assert "�" not in decoded


def test_decode_html_body_falls_back_to_utf8_then_euc_jp() -> None:
    utf8_html = "<html><body>新潟 ダ1800m</body></html>".encode("utf-8")
    euc_html = "<html><body>中京 芝2000m</body></html>".encode("euc-jp")

    assert "新潟" in decode_html_body(utf8_html)
    assert "中京" in decode_html_body(euc_html)
