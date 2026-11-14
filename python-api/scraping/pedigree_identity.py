"""Source-linked static parent identities; never infer aliases from a name."""
from __future__ import annotations

from copy import deepcopy
import re
from urllib.parse import urljoin, urlsplit

from lxml import html as lxml_html

from scraping.pedigree_gap_repair import ALIASES, FIELDS
from scraping.saved_horse_reuse import _present


HOSTS = {"db.netkeiba.com", "db.sp.netkeiba.com"}
ID = re.compile(r"[A-Za-z0-9]{1,32}")


def _profile_id(url: str) -> str | None:
    value = urlsplit(url)
    match = re.fullmatch(r"/horse/([A-Za-z0-9]{1,32})/?", "/" + value.path.lstrip("/"))
    if (value.scheme == "https" and value.netloc in HOSTS and not value.query
            and not value.fragment and match):
        return match[1]
    return None


def parse_parent_identities(html: str, horse_id: str, source_url: str) -> dict:
    if not ID.fullmatch(horse_id):
        raise ValueError("pedigree-child-id-invalid")
    requested = urlsplit(source_url)
    if (requested.scheme != "https" or requested.netloc not in HOSTS or requested.query
            or requested.fragment or requested.path.rstrip("/") != f"/horse/ped/{horse_id}"):
        raise ValueError("pedigree-source-url-invalid")
    if not html.strip():
        raise ValueError("pedigree-source-body-empty")
    tree = lxml_html.fromstring(html)
    canonicals = tree.xpath(
        "//link[contains(concat(' ', normalize-space(@rel), ' '), ' canonical ')]/@href"
        "| //meta[@property='og:url']/@content"
    )
    if not canonicals or any(_profile_id(value) != horse_id for value in canonicals):
        raise ValueError("pedigree-source-child-identity-conflict")
    tables = tree.xpath("//table[contains(concat(' ',normalize-space(@class),' '),' blood_table ')]")
    if not tables:
        tables = [table for table in tree.xpath("//table")
                  if len(table.xpath("./tr|./tbody/tr")) == 32
                  and len(table.xpath("./tr|./tbody/tr")[0].xpath("./td")) >= 5]
    if len(tables) != 1:
        raise ValueError("pedigree-source-table-not-unique")
    rows = tables[0].xpath("./tr|./tbody/tr")
    if len(rows) != 32:
        raise ValueError("pedigree-source-generation-layout-unconfirmed")
    first, middle = rows[0].xpath("./td"), rows[16].xpath("./td")
    if len(first) < 1 or len(middle) < 2:
        raise ValueError("pedigree-source-parent-cells-missing")
    cells = {"sire": first[0], "dam": middle[0], "damsire": middle[1]}
    result = {}
    for field, cell in cells.items():
        if cell.get("rowspan") != ("8" if field == "damsire" else "16"):
            raise ValueError("pedigree-source-parent-position-unconfirmed")
        anchors = [(node, _profile_id(urljoin(source_url, node.get("href", ""))))
                   for node in cell.xpath(".//a[@href]")]
        anchors = [(node, identity) for node, identity in anchors if identity is not None]
        if len(anchors) != 1:
            raise ValueError("pedigree-source-parent-link-not-unique")
        anchor, identity = anchors[0]
        name = "".join(part.strip() for part in anchor.itertext())
        if (identity == horse_id or not _present(name) or len(name) > 160
                or name in {"血統", "産駒"} or any(ord(char) < 32 for char in name)):
            raise ValueError("pedigree-source-parent-label-invalid")
        result[field] = {"horse_id": identity, "name": name}
    return result


def fill_missing_parents(item: dict, proof: dict) -> tuple[dict, list[str]]:
    """Fill a row, not an entire horse's legacy spelling consensus.

    A provider-confirmed child and parent triple is stronger evidence than
    unidentified name variants in OTHER rows. Those rows stay untouched. Within
    this row, every existing name/alias or ID must agree; no partial families
    are mixed. Country suffixes are preserved verbatim, not stripped.
    """
    if item.get("horse_id") != proof["horse_id"]:
        raise ValueError("pedigree-repair-child-identity-conflict")
    parents = proof["parents"]
    for field in FIELDS:
        existing_id = item.get(field + "_id")
        if _present(existing_id) and existing_id != parents[field]["horse_id"]:
            raise ValueError("pedigree-repair-known-parent-id-conflict")
        for alias in ALIASES[field]:
            existing = item.get(alias)
            if (_present(existing) and (not isinstance(existing, str)
                                      or existing.strip() != parents[field]["name"])):
                raise ValueError("pedigree-repair-known-name-or-alias-conflict")
    missing = [field for field in FIELDS if not _present(item.get(field))]
    if not missing:
        return deepcopy(item), []
    if "_pedigree_identity" in item:
        raise ValueError("pedigree-repair-existing-provenance")
    updated = deepcopy(item)
    for field in FIELDS:
        if field in missing:
            updated[field] = parents[field]["name"]
        if not _present(updated.get(field + "_id")):
            updated[field + "_id"] = parents[field]["horse_id"]
    updated["_pedigree_identity"] = {
        "schema": "provider-parent-identity-v1", "source_url": proof["source_url"],
        "raw_body_sha256": proof["raw_body_sha256"], "proof_sha256": proof["proof_sha256"],
        "source_observed_at_utc": proof["source_observed_at_utc"],
        "reconstructed_at_utc": proof["reconstructed_at_utc"],
        "historical_capture_time_verified": False,
        "legacy_unidentified_spellings_in_other_rows_not_equated": True,
    }
    return updated, missing
