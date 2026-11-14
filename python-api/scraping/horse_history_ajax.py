"""Validate the public career response loaded by current horse profile pages."""
from __future__ import annotations

import json
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from scraping.pedigree_identity import ID, _profile_id


def history_url(horse_id: str) -> str:
    if not ID.fullmatch(horse_id):
        raise ValueError("horse-history-id-invalid")
    return "https://db.netkeiba.com/horse/ajax_horse_results.html?" + urlencode({
        "input": "UTF-8", "output": "json", "id": horse_id,
    })


def combine_profile_history(profile: str, response: str, horse_id: str) -> str:
    soup = BeautifulSoup(profile, "lxml")
    urls = [tag.get("href") or tag.get("content") for tag in
            soup.select('link[rel="canonical"],meta[property="og:url"]')]
    if not urls or any(_profile_id(url) != horse_id for url in urls):
        raise ValueError("horse-history-profile-child-conflict")
    payload = json.loads(response)
    if (not isinstance(payload, dict) or payload.get("status") != "OK"
            or not isinstance(payload.get("data"), str) or not payload["data"].strip()):
        raise ValueError("horse-history-ajax-not-successful-html")
    containers = soup.select("#horse_results_box")
    if len(containers) != 1:
        raise ValueError("horse-history-container-not-unique")
    containers[0].clear()
    containers[0].append(BeautifulSoup(payload["data"], "html.parser"))
    return str(soup)
