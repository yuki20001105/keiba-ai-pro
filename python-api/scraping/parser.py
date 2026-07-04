from __future__ import annotations

import re

from bs4 import BeautifulSoup


def parse_db_list_race_ids(html: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"/race/(\d{12})/", html or "")))


def parse_race_list_sub_race_ids(html: str) -> list[str]:
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    found: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        m = re.search(r"race_id=(\d{12})", a["href"])
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            found.append(m.group(1))
    return found
