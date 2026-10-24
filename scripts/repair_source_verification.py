"""Independent source-body binding for the explicit repair CLI (no network)."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit,parse_qs

from bs4 import BeautifulSoup

from scraping.acquisition_scope import normalized_date
from scraping.safe_repair import RepairConflict, _time
from scraping.source_normalization import finish_column_evidence_from_html, normalize_finish_position


def text(value):
    return re.sub(r"\s+","",unicodedata.normalize("NFKC",str(value or "")))


def source_date(html):
    soup=BeautifulSoup(html,"lxml")
    nodes=list(soup.select("p.smalltxt, div.smalltxt, .RaceData02, .RaceHeader_Value"))
    if soup.title: nodes.append(soup.title)
    dates=set()
    for node in nodes:
        for y,m,d in re.findall(r"(\d{4})[年/-]\s*(\d{1,2})[月/-]\s*(\d{1,2})(?:日|\b)",node.get_text(" ",strip=True)):
            value=f"{int(y):04d}{int(m):02d}{int(d):02d}"
            if normalized_date(value): dates.add(value)
    return next(iter(dates)) if len(dates)==1 else None


def validate_result_source(candidate, receipts, race_id, race_date):
    evidence=candidate.get("_source_evidence",{})
    if (evidence.get("schema")!="result-acquisition-source-evidence-v1"
            or evidence.get("race_id")!=race_id or evidence.get("source_date")!=race_date
            or evidence.get("http_status")!=200):
        raise RepairConflict("repair-parser-source-evidence-missing-or-mismatched")
    matches=[r for r in receipts if r.get("body_sha256")==evidence.get("body_sha256")
             and r.get("url")==evidence.get("source_url")
             and r.get("decoded_html_sha256")==evidence.get("decoded_html_sha256")
             and r.get("parsed_source_date")==race_date]
    if len(matches)!=1:
        raise RepairConflict("repair-source-receipt-not-uniquely-bound")
    receipt=matches[0]
    raw=Path(receipt["html_path"]).read_bytes()
    decoded=Path(receipt["decoded_html_path"]).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=receipt["body_sha256"] or hashlib.sha256(decoded).hexdigest()!=evidence["decoded_html_sha256"]:
        raise RepairConflict("repair-source-artifact-hash-mismatch")
    html=decoded.decode("utf-8")
    if source_date(html)!=race_date:
        raise RepairConflict("repair-independent-source-date-not-verified")
    finish_evidence=finish_column_evidence_from_html(html,race_id=race_id,source_url=receipt["url"])
    if not finish_evidence:
        raise RepairConflict("repair-source-result-table-not-verified")
    soup=BeautifulSoup(html,"lxml")
    for tag in soup.select('link[rel="canonical"], meta[property="og:url"]'):
        value=str(tag.get("href") or tag.get("content") or "")
        ids=re.findall(r"/race/(\d{12})(?:/|$)",urlsplit(value).path)+parse_qs(urlsplit(value).query).get("race_id",[])
        if any(rid!=race_id for rid in ids):
            raise RepairConflict("repair-source-canonical-id-conflict")
    table=soup.select_one("table.race_table_01, table.RaceTable01, table.ResultsByRaceDetail")
    rows=table.find_all("tr")
    headers=[text(cell.get_text(" ",strip=True)) for cell in rows[0].find_all(["th","td"])]
    if headers.count("馬番")!=1 or headers.count("馬名")!=1:
        raise RepairConflict("repair-source-identity-columns-missing")
    number_col,name_col=headers.index("馬番"),headers.index("馬名")
    time_col=headers.index("タイム") if headers.count("タイム")==1 else None
    source=[]
    source_times={}
    for row in rows[1:]:
        cells=row.find_all("td")
        if len(cells)<=max(number_col,name_col): continue
        number=text(cells[number_col].get_text(strip=True))
        if not number.isdigit(): raise RepairConflict("repair-source-runner-number-invalid")
        links=[]
        for anchor in cells[name_col].find_all("a",href=True):
            match=re.search(r"/horse/(?:result/(?:recent/)?)?([A-Za-z0-9]+)(?:/|$)",urlsplit(anchor["href"]).path)
            if match: links.append((match.group(1),text(anchor.get_text(strip=True))))
        if len(links)!=1: raise RepairConflict("repair-source-horse-link-not-unique")
        source.append((int(number),*links[0]))
        if time_col is not None and len(cells)>time_col:
            source_times[int(number)]=cells[time_col].get_text(strip=True)
    parsed=[(int(h["horse_number"]),str(h["horse_id"]),text(h["horse_name"])) for h in candidate["horses"]]
    declared=[(int(h["horse_number"]),str(h["horse_id"]),text(h["horse_name"])) for h in evidence.get("runner_identity",[])]
    if not source or len(set(source))!=len(source) or sorted(source)!=sorted(parsed) or sorted(parsed)!=sorted(declared):
        raise RepairConflict("repair-independent-all-runner-identity-mismatch")
    for horse in candidate["horses"]:
        number=int(horse["horse_number"])
        raw_finish=finish_evidence["finish_labels_by_horse_number"][str(number)]
        expected=normalize_finish_position(raw_finish,column_evidence=finish_evidence,
            race_id=race_id,horse_number=number)
        if expected["status"] not in {"numeric","nonfinisher","source_verified_demotion"}:
            raise RepairConflict("repair-source-finish-unverified")
        if text(expected["value"])!=text(horse.get("finish_position",horse.get("finish"))):
            raise RepairConflict("repair-independent-finish-value-mismatch")
        if expected["status"]=="source_verified_demotion" and (text(horse.get("finish_position_raw"))!=text(raw_finish)
                or horse.get("finish_annotation")!="降"):
            raise RepairConflict("repair-independent-finish-annotation-mismatch")
        if number in source_times and expected["status"]!="nonfinisher":
            if _time(source_times[number]) is None or _time(source_times[number])!=_time(horse.get("finish_time",horse.get("time_seconds"))):
                raise RepairConflict("repair-independent-finish-time-mismatch")
    counts=set()
    for node in soup.select("p.smalltxt, div.smalltxt, .RaceData02, .RaceHeader_Value"):
        for match in re.finditer(r"(\d{1,2})頭",node.get_text(" ",strip=True)):
            if not node.get_text(" ",strip=True)[max(0,match.start()-6):match.start()].endswith("フルゲート"):
                counts.add(int(match.group(1)))
    if counts and counts!={len(source)}:
        raise RepairConflict("repair-source-declared-count-mismatch")
    return {**receipt,"all_runner_identity_verified":True,"source_runner_rows":len(source),
            "independent_declared_count":next(iter(counts)) if len(counts)==1 else None,
            "independent_declared_count_available":bool(counts)}
