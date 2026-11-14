"""Read explicit race conditions without guessing from a named stakes race."""
from __future__ import annotations

import re
import unicodedata


def explicit_race_class(*sources: object) -> str | None:
    """Return an unambiguous published label, including old JRA earnings bands."""
    labels = set()
    for source in sources:
        if not isinstance(source, str):
            continue
        text = unicodedata.normalize("NFKC", source)
        for match in re.finditer(r"(?<![A-Za-z])G(III|II|I|[123])(?![A-Za-z0-9])", text):
            labels.add("G" + {"I": "1", "II": "2", "III": "3"}.get(match[1], match[1]))
        for match in re.finditer(r"(?<!\d)([123])勝(?:クラス)?(?!\d)", text):
            labels.add(match[1] + "勝クラス")
        for match in re.finditer(r"(?<![\d,])(500|1000|1600)(?:万)?下", text):
            labels.add({"500": "1", "1000": "2", "1600": "3"}[match[1]] + "勝クラス")
        for pattern, label in (
            (r"(?<!\w)L(?!\w)|\bListed\b|リステッド", "Listed"),
            (r"\bOP\b|\bOpen\b|オープン", "オープン"),
            (r"未勝利", "未勝利"), (r"新馬", "新馬"),
            (r"重賞", "重賞"),
        ):
            if re.search(pattern, text, re.IGNORECASE):
                labels.add(label)
    # A grade/Listed label can be accompanied by the broader open condition.
    specific = labels - {"オープン", "重賞"}
    if len(specific) == 1:
        candidate = next(iter(specific))
        if candidate.startswith("G") or candidate == "Listed" or len(labels) == 1:
            return candidate
    return next(iter(labels)) if len(labels) == 1 else None
