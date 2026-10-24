"""Offline provider-contract tests; retained captures are optional, never fetched."""
import hashlib
import json
from pathlib import Path

import pytest

from forward_runtime import provider as p


RACE = "202606040601"
DAY = "20260920"
POST = "2026-09-20T10:00:00+09:00"


def html(content):
    return ('<html><head><meta charset="utf-8"></head><body>' + content + '</body></html>').encode()


def receipt(body, path="/race/shutuba.html", query=None, received="2026-09-20T10:10:00+09:00"):
    url = "https://race.netkeiba.com" + path + "?" + (query or f"race_id={RACE}")
    return {"request_url": url, "url": url, "status": 200, "source": "network",
            "received_at": received, "body_sha256": hashlib.sha256(body).hexdigest()}


def header(*, result=False, count=3):
    endpoint = "result" if result else "shutuba"
    return (f'<title>未勝利 2026年9月20日 中山1R</title>'
            f'<link rel="canonical" href="https://race.netkeiba.com/race/{endpoint}.html?race_id={RACE}">'
            '<div class="RaceName">未勝利</div>'
            '<div class="RaceData01">10:00 発走 / ダ1800m / 天候:曇 / 馬場:良</div>'
            f'<div class="RaceData02">中山 / 未勝利 / {count}頭</div>')


def runner(number, *, mass="--", extra=""):
    return (f'<tr class="HorseList"><td>{number}</td><td>{number}</td><td></td>'
            f'<td><a href="https://db.netkeiba.com/horse/202410000{number}/">馬{number}</a></td>'
            '<td>牡2</td><td>55.0</td>'
            '<td><a href="https://db.netkeiba.com/jockey/result/recent/01091/">騎手</a></td>'
            '<td><a href="https://db.netkeiba.com/trainer/result/recent/01089/">調教師</a></td>'
            f'<td>{mass}</td><td>単勝99.9 人気1 {extra}</td></tr>')


def card(*, count=3, rows=None, navigation=True):
    nav = '<div class="RaceNumWrap">' + ''.join(
        f'<a href="?race_id={RACE[:-2]}{n:02d}">{n}R</a>' for n in range(1, 13)) + '</div>'
    return html(header(count=count) + (nav if navigation else '')
                + '<table class="Shutuba_Table ShutubaTable">'
                + (rows if rows is not None else ''.join(runner(n) for n in range(1, 4))) + '</table>')


def odds():
    return {"status": "middle", "data": {"yy": "26", "jyo": "06", "kai": "04", "nichi": "06", "rno": "01",
            "official_datetime": "2026-09-20 09:54:59", "shusso": "3", "touroku": "3",
            "odds": {"1": {"01": ["2.1"], "02": ["3.2"], "03": ["8.7"]}}}}


def race_list(*, jump=True):
    rows = []
    for n in range(1, 4):
        description = "障害3000m" if jump and n == 3 else "芝1600m"
        rows.append(f'<div class="RaceList_DataItem"><a href="/race/shutuba.html?race_id={RACE[:-2]}{n:02d}">{n}R</a>'
                    f'<span class="RaceList_ItemLong">{description}</span><span class="RaceList_Itemtime">10:{n:02d}</span></div>')
    return html(''.join(rows))


def list_receipt(body):
    return receipt(body, "/top/race_list_sub.html", f"kaisai_date={DAY}")


def result(*, ranks=(1, 2, 3), count=3, payout=210):
    rows = ''.join(f'<tr class="HorseList"><td>{rank}</td><td>1</td><td>{n}</td>'
                   '<td>馬</td><td>牡2</td><td>55</td><td>騎手</td><td>1:50.0</td></tr>'
                   for n, rank in enumerate(ranks, 1))
    winners = [n for n, rank in enumerate(ranks, 1) if rank == 1]
    nums = ''.join(f'<span>{n}</span>' for n in winners)
    amounts = '<br>'.join(f'{payout:,}円' for _ in winners)
    return html(header(result=True, count=count) + f'<table id="All_Result_Table" summary="全着順">{rows}</table>'
                f'<table class="Payout_Detail_Table"><tr class="Tansho"><td class="Result">{nums}</td>'
                f'<td class="Payout">{amounts}</td></tr></table>')


def test_card_uses_current_canonical_fields_and_preserves_missing():
    rows = p.parse_card(card(), RACE, POST)
    assert len(rows) == 3 and rows[0]["horse_number"] == 1
    assert rows[0]["surface"] == "dirt" and rows[0]["race_class"] == "未勝利"
    assert rows[0]["sex"] == "牡" and rows[0]["weather"] == "曇"
    assert rows[0]["field_condition"] == "良" and rows[0]["venue"] == "中山"
    assert rows[0]["horse_weight"] is None and rows[0]["horse_weight_change"] is None
    assert rows[0]["sire"] is None and rows[0]["damsire"] is None
    assert not {"odds", "popularity", "finish", "time_seconds", "prev_finish"} & set(rows[0])


def test_current_weight_and_sex_normalized_but_zero_weight_change_not_missing():
    body = card(rows=runner(1, mass="４８０（０）") + runner(2) + runner(3)).replace("牡2".encode(), "牝３".encode())
    row = p.parse_card(body, RACE, POST)[0]
    assert (row["horse_weight"], row["horse_weight_change"], row["sex"], row["age"]) == (480, 0, "牝", 3)


def test_nested_history_and_memo_outside_card_cells_never_enter_inputs():
    extra = '<table><tr class="HorseList"><td>取消 中止 999 未来結果</td></tr></table>'
    rows = runner(1, extra=extra) + runner(2) + runner(3)
    assert p.parse_card(card(rows=rows), RACE, POST) == p.parse_card(card(), RACE, POST)


@pytest.mark.parametrize("old,new", [
    ("2026年9月20日", "2026年9月19日"), ("10:00 発走", "10:01 発走"),
    ("race/shutuba.html", "race/result.html"), ("race.netkeiba.com", "race.netkeiba.com.evil.test"),
    ("ダ1800m", "障害3000m"), ("3頭", "2頭"), ("3頭", "頭数未定"),
    ("<td>牡2</td>", "<td>取消</td>"), ("/horse/2024100002/", "/horse/2024100001/"),
    ("<td>3</td><td>3</td>", "<td>3</td><td>2</td>"),
    ("db.netkeiba.com/horse/", "evil.test/horse/"),
    ("<td>55.0</td>", "<td>--</td>"), ("<td>--</td>", "<td>0(0)</td>"),
])
def test_card_rejects_unproven_or_partial_inputs(old, new):
    with pytest.raises(ValueError):
        p.parse_card(card().replace(old.encode(), new.encode()), RACE, POST)


def test_grade_remains_explicit_not_arbitrarily_mapped_to_op():
    body = card().replace(b'<div class="RaceName">', b'<div class="RaceName"><span class="Icon_GradeType1"></span>')
    assert p.parse_card(body, RACE, POST)[0]["race_class"] == "G1"


def test_navigation_is_bound_to_card_day_and_contiguous_ids():
    assert p.parse_navigation(card(), RACE, POST) == [RACE[:-2] + f"{n:02d}" for n in range(1, 13)]
    with pytest.raises(ValueError):
        p.parse_navigation(card().replace(b'>2R<', b'>99R<'), RACE, POST)
    with pytest.raises(ValueError):
        p.parse_navigation(card().replace(b'race_id=202606040602', b'race_id=202606040702'), RACE, POST)


def test_odds_zero_padded_horse_keys_and_explicit_jst_tick():
    value = p.parse_odds(json.dumps(odds()).encode(), RACE, received_at="2026-09-20T00:55:00+00:00")
    assert value["odds"] == {1: 2.1, 2: 3.2, 3: 8.7}
    assert value["provider_time"] == "2026-09-20T09:54:59+09:00"


@pytest.mark.parametrize("change", ["future", "missing_tick", "duplicate_key", "partial", "wrong_race", "final", "nan"])
def test_odds_cannot_replace_missing_clock_or_incomplete_field(change):
    body = odds()
    if change == "future": body["data"]["official_datetime"] = "2026-09-20 09:55:01"
    if change == "missing_tick": body["data"].pop("official_datetime")
    if change == "duplicate_key": body["data"]["odds"]["1"]["1"] = ["2.1"]
    if change == "partial": body["data"]["odds"]["1"].pop("03")
    if change == "wrong_race": body["data"]["nichi"] = "05"
    if change == "final": body["status"] = "result"
    if change == "nan": body["data"]["odds"]["1"]["03"] = ["NaN"]
    with pytest.raises((ValueError, TypeError)):
        p.parse_odds(json.dumps(body).encode(), RACE, received_at="2026-09-20T09:55:00+09:00")


def test_list_has_date_bound_receipt_but_does_not_claim_completeness():
    body = race_list()
    value = p.parse_race_list(body, DAY, receipt=list_receipt(body))
    assert len(value["races"]) == 2 and len(value["expected_race_ids"]) == 3
    assert value["excluded_races"] == [{"race_id": RACE[:-2] + "03", "reason": "jump"}]
    assert value.get("complete") is not True
    assert value["races"][0]["post_time"] == "2026-09-20T10:01:00+09:00"


@pytest.mark.parametrize("key,value", [
    ("request_url", "https://race.netkeiba.com/top/race_list_sub.html?kaisai_date=20260919"),
    ("url", "https://race.netkeiba.com/top/race_list_sub.html?kaisai_date=20260919"),
    ("url", "https://race.netkeiba.com/top/race_list.html?kaisai_date=20260920"),
    ("url", "https://evil.test/top/race_list_sub.html?kaisai_date=20260920"),
    ("body_sha256", "0" * 64),
])
def test_list_rejects_wrong_receipt(key, value):
    body = race_list(); evidence = list_receipt(body); evidence[key] = value
    with pytest.raises(ValueError):
        p.parse_race_list(body, DAY, receipt=evidence)


def test_list_empty_is_not_proof_of_no_racing():
    body = html("no published races")
    with pytest.raises(ValueError):
        p.parse_race_list(body, DAY, receipt=list_receipt(body))


def test_calendar_requires_every_day_in_selected_month():
    body = html('<select id="cal_select_year"><option value="2026" selected>2026</option></select>'
                '<div class="CalendarMonth"><span class="Active"><a href="?year=2026&month=9">9</a></span></div>'
                '<table class="Calendar_Table">' + ''.join(
                    f'<tr><td class="RaceCellBox"><span class="Day">{d}</span>'
                    + ('<span class="JyoName">中山</span>' if d == 20 else '') + '</td></tr>'
                    for d in range(1, 31)) + '</table>')
    value = p.parse_calendar(body, 2026, 9)
    assert len(value) == 30 and value[DAY] == ["06"] and value["20260921"] == []
    with pytest.raises(ValueError):
        p.parse_calendar(body.replace(b'<span class="Day">30</span>', b''), 2026, 9)
    with pytest.raises(ValueError):
        p.parse_calendar(body, 2026, 8)


@pytest.mark.parametrize("ranks", [(1, 2, 3), (1, 1, 3), (1, 2, "中止"), (1, "失格", 2)])
def test_complete_confirmed_result_includes_every_winner_and_preserves_dnf(ranks):
    body = result(ranks=ranks); evidence = receipt(body, "/race/result.html")
    value = p.parse_result(body, RACE, POST, receipt=evidence)
    winners = [n for n, rank in enumerate(ranks, 1) if rank == 1]
    assert value["complete"] and value["runner_numbers"] == [1, 2, 3]
    assert value["winners"] == winners and value["win_payouts"] == {str(n): 210 for n in winners}
    assert value["source_sha256"] == hashlib.sha256(body).hexdigest()


@pytest.mark.parametrize("ranks,count,payout", [((1, 2, 3), 4, 210), ((1, 2, "取消"), 3, 210),
                                              ((1, 2, 3), 3, 0), ((1, 2, 3), 3, 211),
                                              ((1, 0, 3), 3, 210), ((1, 2, 99), 3, 210)])
def test_result_rejects_partial_or_invalid_rows(ranks, count, payout):
    body = result(ranks=ranks, count=count, payout=payout)
    with pytest.raises(ValueError):
        p.parse_result(body, RACE, POST, receipt=receipt(body, "/race/result.html"))


@pytest.mark.parametrize("key,value", [("body_sha256", "0" * 64),
    ("url", "https://race.netkeiba.com/race/result.html?race_id=202606040501"),
    ("request_url", "https://evil.test/race/result.html?race_id=202606040601"),
    ("received_at", POST)])
def test_result_receipt_is_bound_to_body_race_and_post(key, value):
    body = result(); evidence = receipt(body, "/race/result.html"); evidence[key] = value
    with pytest.raises(ValueError):
        p.parse_result(body, RACE, POST, receipt=evidence)


FIXTURES = Path(__file__).resolve().parents[2] / "reports/generated/forward-runtime/provider-validation"


def captured(digest):
    raw, meta = FIXTURES / (digest + ".raw"), FIXTURES / (digest + ".receipt.json")
    if not raw.exists() or not meta.exists():
        pytest.skip("optional retained local technical capture is unavailable")
    body = raw.read_bytes(); evidence = json.loads(meta.read_text(encoding="utf-8"))
    assert hashlib.sha256(body).hexdigest() == digest == evidence["body_sha256"]
    return body, evidence


def test_retained_card_capture_is_15_current_runners():
    body, _ = captured("dee8853f458aa99a6bc2014f98bf9aee9969fa50ee265b80140380b5e959be1e")
    rows = p.parse_card(body, RACE, POST)
    assert len(rows) == 15 and {r["horse_number"] for r in rows} == set(range(1, 16))
    assert rows[0]["horse_id"] == "2024101291" and rows[0]["race_class"] == "未勝利"
    assert rows[0]["horse_weight"] is None
    assert len(p.parse_navigation(body, RACE, POST)) == 12


def test_retained_list_capture_has_24_ids_but_only_23_flat():
    body, evidence = captured("20f871e3fd02c7a43f799936f43d06c6ed38c8cfc71f13537067fdecf79a9084")
    value = p.parse_race_list(body, DAY, receipt=evidence)
    assert len(value["expected_race_ids"]) == 24 and len(value["races"]) == 23
    assert len(value["excluded_races"]) == 1 and value.get("complete") is not True


def test_retained_odds_capture_parses_jst_and_zero_padded_keys():
    body, evidence = captured("b5c7baca3f66a1672fa9726e501c3c11638a22fe503f81feea48b4f75c4e5681")
    value = p.parse_odds(body, RACE, received_at=evidence["received_at"])
    assert len(value["odds"]) == 15 and value["odds"][8] == 1.6
    assert value["provider_time"] == "2026-09-19T23:24:41+09:00"


def test_retained_calendar_capture_is_complete_month():
    body, _ = captured("187478dfc587000aa9f6365807ed70da1ec354492350f4c0c76238bdb642ffc5")
    value = p.parse_calendar(body, 2026, 9)
    assert len(value) == 30 and value[DAY] == ["06", "09"]


def test_retained_result_capture_has_9_runners_and_confirmed_payout():
    body, evidence = captured("0e17d0fcc286d62d480fc6032ae15b143b78edce6c26cdd49678140eb8adf952")
    value = p.parse_result(body, "202606040501", "2026-09-19T10:00:00+09:00", receipt=evidence)
    assert value["complete"] and len(value["runner_numbers"]) == 9
    assert value["winners"] and all(x >= 100 for x in value["win_payouts"].values())
