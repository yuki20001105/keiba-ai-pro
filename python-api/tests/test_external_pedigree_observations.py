import importlib.util
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[2] / "scripts" / "verify_external_pedigree_observations.py"
spec = importlib.util.spec_from_file_location("external_pedigree_observations", path)
external = importlib.util.module_from_spec(spec)
spec.loader.exec_module(external)


HTML = """
<h1>マンタナ</h1>
<div class="data-3__inner">
  <div class="data-3__items">
    <div class="data-3__label">父</div>
    <div class="data-3__male"><div><a href="/horse/0001085636/">パイロ(USA)</a></div></div>
  </div>
  <div class="data-3__items">
    <div class="data-3__label">母</div>
    <div class="data-3__female"><div><a href="/horse/0001121989/">フウコウメイビ</a></div></div>
    <div>
      <div class="data-3__male"><div><a href="/horse/0000225628/">サクラバクシンオー</a></div></div>
      <div class="data-3__female">Maternal grandmother</div>
    </div>
  </div>
</div>
<dl><div><dt>生年月日</dt><dd>2018.02.26</dd></div>
<div><dt>生産牧場</dt><dd>中村 雅明</dd></div></dl>
"""
URL = "https://www.jbis.or.jp/horse/0001267860/"


def test_source_slots_preserve_country_and_registry_ids_without_normalizing_them_into_netkeiba():
    result = external.parse_jbis('<h1><a><img alt="Site logo"></a></h1>' + HTML, URL)
    assert result["parents"]["sire"] == {
        "registry": "jbis", "horse_id": "0001085636", "display_name": "パイロ(USA)",
    }
    assert result["parents"]["damsire"]["horse_id"] == "0000225628"
    assert result["birth_date"] == "20180226"
    assert result["name"] == "マンタナ"


def test_unconfirmed_maternal_grandsire_slot_is_rejected():
    changed = HTML.replace('<div class="data-3__male"><div><a href="/horse/0000225628/">',
                           '<div class="data-3__female"><div><a href="/horse/0000225628/">')
    with pytest.raises(ValueError, match="maternal-grandsire-position"):
        external.parse_jbis(changed, URL)


def test_parent_cannot_be_the_child_even_when_the_source_has_a_valid_looking_link():
    changed = HTML.replace("/horse/0001085636/", "/horse/0001267860/")
    with pytest.raises(ValueError, match="parent-identity-invalid"):
        external.parse_jbis(changed, URL)
