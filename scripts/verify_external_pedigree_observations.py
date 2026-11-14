"""Verify alternate-registry parent facts; never equate registry IDs or strip country labels."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python-api"))
sys.path.insert(0, str(ROOT / "keiba"))

from bs4 import BeautifulSoup

from experiments.common import file_sha256, read_json
from experiments.search import freeze_json
from scraping.acquisition_horse_history import validate_profile_child
from scraping.fetch_pipeline import decode_html_body
from scraping.missingness_goal import _protected


def profile_fact(soup, label, heading):
    matches = [node for node in soup.find_all(heading) if node.get_text(strip=True) == label]
    if len(matches) != 1:
        raise ValueError(f"external-pedigree-profile-fact-not-unique: {label}")
    value = matches[0].find_next_sibling("dd" if heading == "dt" else "td")
    if value is None:
        raise ValueError(f"external-pedigree-profile-fact-missing: {label}")
    return value.get_text(" ", strip=True)


def birth_date(value):
    parts = re.findall(r"\d+", value)
    if len(parts) != 3:
        raise ValueError("external-pedigree-birth-date-invalid")
    year, month, day = map(int, parts)
    return datetime(year, month, day).strftime("%Y%m%d")


def profile_heading(soup):
    headings = [node.get_text(" ", strip=True) for node in soup.find_all("h1")
                if node.get_text(strip=True)]
    if len(headings) != 1:
        raise ValueError("external-pedigree-child-heading-not-unique")
    return headings[0]


def parse_jbis(html, source_url):
    parsed = urlsplit(source_url)
    if (parsed.scheme != "https" or parsed.hostname != "www.jbis.or.jp"
            or not re.fullmatch(r"/horse/\d{10}/", parsed.path) or parsed.query or parsed.fragment):
        raise ValueError("external-pedigree-jbis-source-invalid")
    soup = BeautifulSoup(html, "lxml")
    groups = soup.select(".data-3__inner .data-3__items")
    if len(groups) != 2:
        raise ValueError("external-pedigree-parent-layout-unconfirmed")
    sire = groups[0].find("div", class_="data-3__male", recursive=False)
    dam = groups[1].find("div", class_="data-3__female", recursive=False)
    if sire is None or dam is None:
        raise ValueError("external-pedigree-parent-position-unconfirmed")
    grandparents = dam.find_next_sibling("div")
    damsire = grandparents.find("div", class_="data-3__male", recursive=False) if grandparents else None
    if damsire is None:
        raise ValueError("external-pedigree-maternal-grandsire-position-unconfirmed")
    parents = {}
    for field, node in (("sire", sire), ("dam", dam), ("damsire", damsire)):
        anchors = node.find_all("a", href=True)
        if len(anchors) != 1:
            raise ValueError("external-pedigree-parent-link-not-unique")
        url = urlsplit(urljoin(source_url, anchors[0]["href"]))
        match = re.fullmatch(r"/horse/(\d{10})/", url.path)
        name = anchors[0].get_text(" ", strip=True)
        if (url.scheme != "https" or url.hostname != "www.jbis.or.jp" or not match or not name
                or url.query or url.fragment or match[1] == parsed.path.split("/")[2]):
            raise ValueError("external-pedigree-parent-identity-invalid")
        parents[field] = {"registry": "jbis", "horse_id": match[1], "display_name": name}
    return {
        "name": profile_heading(soup),
        "birth_date": birth_date(profile_fact(soup, "生年月日", "dt")),
        "breeder": re.sub(r"\s+", "", profile_fact(soup, "生産牧場", "dt")),
        "parents": parents,
    }


def verify(root, primary):
    before = _protected()
    for name, digest in read_json(root / "artifacts.json").items():
        if file_sha256(root / name) != digest:
            raise ValueError("external-pedigree-source-artifact-changed")
    observed = read_json(root / "source-observations.json")
    with closing(sqlite3.connect((root / "sources.db").resolve().as_uri() + "?mode=ro", uri=True)) as db:
        url, final, status, body, _ = db.execute("SELECT * FROM sources").fetchone()
    if (status != 200 or url != final or url != observed["source_url"]
            or urlsplit(url).path != f"/horse/{observed['jbis_child_id']}/"):
        raise ValueError("external-pedigree-transport-unverified")
    if hashlib.sha256(body).hexdigest() != observed["source_body_sha256"]:
        raise ValueError("external-pedigree-source-bytes-changed")
    secondary = parse_jbis(decode_html_body(body), url)
    if secondary["parents"] != observed["parents"] or secondary["name"] != observed["child_heading"]:
        raise ValueError("external-pedigree-parent-facts-disagree")
    with closing(sqlite3.connect((primary / "sources.db").resolve().as_uri() + "?mode=ro", uri=True)) as db:
        p_url, p_final, p_status, p_body, _ = db.execute("SELECT * FROM sources").fetchone()
    p_receipt = read_json(primary / "verification.json")
    if (p_status != 200 or p_url != p_final or p_url != p_receipt["url"]
            or hashlib.sha256(p_body).hexdigest() != p_receipt["body_sha256"]):
        raise ValueError("external-pedigree-primary-source-changed")
    html = decode_html_body(p_body)
    validate_profile_child(html, observed["netkeiba_child_id"])
    soup = BeautifulSoup(html, "lxml")
    identity = {
        "name": profile_heading(soup),
        "birth_date": birth_date(profile_fact(soup, "生年月日", "th")),
        "breeder": re.sub(r"\s+", "", profile_fact(soup, "生産者", "th")),
    }
    if identity != {key: secondary[key] for key in identity}:
        raise ValueError("external-pedigree-child-cross-registry-match-failed")
    if (observed["netkeiba_parent_ids_not_inferred"] is not True
            or observed["country_suffix_preserved"] is not True or _protected() != before):
        raise ValueError("external-pedigree-registry-or-production-guard-failed")
    result = {
        "verified": True, "child_identity_match": identity, "parents": secondary["parents"],
        "source_url": url, "source_body_sha256": observed["source_body_sha256"],
        "primary_profile_body_sha256": p_receipt["body_sha256"],
        "parent_observation_fields_missing": 0, "netkeiba_parent_ids_not_inferred": True,
        "canonical_parent_name_crosswalk_verified": False,
        "model_input_or_raw_db_changed": False, "historical_prestart_capture_proven": False,
        "research_only": True, "verifier_sha256": file_sha256(Path(__file__)),
    }
    freeze_json(root / "replay-verification.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--primary-profile", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.root.resolve(strict=True), args.primary_profile.resolve(strict=True)),
                     ensure_ascii=False), flush=True)
