#!/usr/bin/env python3
"""Build a read-only dry-run plan for P0 reparse cache viability.

The script only reads cached HTML / cached pedigree data and compares reparsed
values with the current P0 repair plan targets. It never performs HTTP access,
DB writes, or upserts.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

ROOT_DIR = Path(__file__).resolve().parent.parent
PYTHON_API_DIR = ROOT_DIR / "python-api"
if str(PYTHON_API_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_API_DIR))

from scraping.constants import HTML_STRAINER, VENUE_MAP, is_cloudflare_block  # type: ignore

DEFAULT_P0_PLAN_INPUT = ROOT_DIR / "reports" / "p0_scrape_repair_plan.json"
DEFAULT_OUTPUT = ROOT_DIR / "reports" / "p0_reparse_cache_plan.json"
DEFAULT_CACHE_DB = ROOT_DIR / "keiba" / "data" / "fetch_cache.db"
DEFAULT_PEDIGREE_CACHE_DB = ROOT_DIR / "keiba" / "data" / "pedigree_cache.db"

SUPPORTED_COLUMNS = {
    "finish_position",
    "result_time",
    "margin",
    "horse_id",
    "horse_name",
    "frame_number",
    "horse_number",
    "race_date",
    "venue",
    "sire",
    "dam",
    "broodmare_sire",
}


@dataclass
class Candidate:
    race_id: str | None
    horse_id: str | None
    column: str
    reason: str
    action: str
    priority: str
    source_hint: str
    recommended_next_action: str


@dataclass
class ParseResult:
    page_kind: str
    cache_key: str
    page_ok: bool
    fields: dict[str, Any]
    quality_score: float
    error: str | None = None


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"error: {label} not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"error: failed to parse {label}: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"error: invalid {label} JSON object: {path}")
    return data


def _normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path or "/"
    query = parts.query or ""
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}{('?' + query) if query else ''}"


def _read_text_body(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        for enc in ("utf-8", "euc-jp", "cp932", "shift_jis"):
            try:
                return raw.decode(enc)
            except Exception:
                continue
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def _is_valid_html(html: str) -> bool:
    if not html or not html.strip():
        return False
    if len(html.strip()) < 80:
        return False
    if is_cloudflare_block(html.encode("utf-8", errors="ignore")):
        return False
    lowered = html.lower()
    if "forbidden" in lowered or "access denied" in lowered or "cloudflare" in lowered:
        return False
    return True


def _open_cache_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"error: cache DB not found: {path}")
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _fetch_http_cache_html(conn: sqlite3.Connection, candidates: list[str]) -> tuple[str | None, str | None, str | None]:
    for candidate in candidates:
        normalized = _normalize_url(candidate)
        row = conn.execute(
            "SELECT final_url, status, headers_json, body FROM http_cache WHERE normalized_url = ?",
            (normalized,),
        ).fetchone()
        if not row:
            continue
        html = _read_text_body(row[3])
        return normalized, _read_text_body(row[0]), html
    return None, None, None


def _fetch_pedigree_cache(conn: sqlite3.Connection, horse_id: str | None) -> dict[str, Any] | None:
    if not horse_id:
        return None
    try:
        row = conn.execute(
            "SELECT sire, dam, damsire FROM pedigree_cache WHERE horse_id = ?",
            (horse_id,),
        ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return {"sire": row[0] or "", "dam": row[1] or "", "broodmare_sire": row[2] or ""}


def _current_quality(candidate: Candidate) -> float:
    if candidate.reason == "domain-allowed-missing":
        return 100.0
    if candidate.reason == "derived-field-candidate":
        return 75.0
    if candidate.reason == "consistency:race_without_horse_data":
        return 35.0
    if candidate.action == "repair-from-existing-metadata":
        return 65.0
    if candidate.action == "reparse-cache":
        return 55.0
    if candidate.action == "refetch-required":
        return 45.0
    if candidate.action == "schema-review":
        return 85.0
    return 60.0


def _score_from_fields(fields: dict[str, Any], page_ok: bool, cache_valid: bool) -> float:
    if not page_ok or not cache_valid:
        return 0.0
    score = 100.0
    for key in ("finish_position", "result_time", "margin", "horse_id", "horse_name", "frame_number", "horse_number", "race_date", "venue", "sire", "dam", "broodmare_sire"):
        if fields.get(key) in (None, "", []):
            score -= 6.0
    return max(0.0, score)


def _extract_race_text_fields(soup: BeautifulSoup, html: str, race_id: str | None, horse_id: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {"page_kind": "race"}
    race_table = soup.find("table", class_="race_table_01")
    if not race_table:
        return out

    header_rows = race_table.find_all("tr")
    if not header_rows:
        return out

    headers = [c.get_text(strip=True) for c in header_rows[0].find_all(["th", "td"])]

    def idx(names: list[str], default: int = -1) -> int:
        for name in names:
            for i, h in enumerate(headers):
                if name in h:
                    return i
        return default

    idx_finish = idx(["着順"], 0)
    idx_bracket = idx(["枠番"], 1)
    idx_horse_num = idx(["馬番"], 2)
    idx_horse = idx(["馬名"], 3)
    idx_time = idx(["タイム"], 7)
    idx_margin = idx(["着差"], 8)

    entries: list[dict[str, Any]] = []
    for row in race_table.find_all("tr")[1:]:
        cols = row.find_all("td")
        if len(cols) < 4:
            continue

        def text(i: int) -> str:
            return cols[i].get_text(strip=True) if i < len(cols) else ""

        def href(i: int) -> str:
            a = cols[i].find("a") if i < len(cols) else None
            raw = a.get("href", "") if a else ""
            return f"https://db.netkeiba.com{raw}" if raw and raw.startswith("/") else raw

        horse_url = href(idx_horse)
        horse_id = ""
        if horse_url:
            import re

            m = re.search(r"/horse/(?:result/)?([A-Za-z0-9]+)(?:/|$)", horse_url)
            horse_id = m.group(1) if m else ""

        finish = text(idx_finish)
        entries.append(
            {
                "race_id": race_id,
                "horse_id": horse_id,
                "horse_name": text(idx_horse),
                "frame_number": text(idx_bracket),
                "horse_number": text(idx_horse_num),
                "finish_position": finish if finish else None,
                "result_time": text(idx_time) or None,
                "margin": text(idx_margin) or None,
            }
        )

    smalltxt = soup.find("p", class_="smalltxt")
    info_text = smalltxt.get_text(" ") if smalltxt else html[:2000]
    race_date = None
    if smalltxt:
        import re

        m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", smalltxt.get_text())
        if m:
            race_date = f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}"
    if not race_date:
        import re

        m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", html)
        if m:
            race_date = f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}"

    venue = ""
    if race_id and len(race_id) >= 6:
        venue = VENUE_MAP.get(race_id[4:6], race_id[4:6])
    if not venue:
        import re

        m = re.search(r"([\u4e00-\u9fff]{2,3})\s*\d{4}年", info_text)
        if m:
            venue = m.group(1)

    selected = None
    if horse_id:
        for entry in entries:
            if str(entry.get("horse_id") or "") == horse_id:
                selected = entry
                break
    if selected is None and entries:
        selected = entries[0]

    if selected:
        for key in ("horse_id", "horse_name", "frame_number", "horse_number", "finish_position", "result_time", "margin"):
            out[key] = selected.get(key)

    out.update(
        {
            "page_kind": "race",
            "page_ok": bool(entries),
            "race_date": race_date,
            "venue": venue or None,
            "entries": entries,
        }
    )
    return out


def _extract_horse_fields(soup: BeautifulSoup, html: str) -> dict[str, Any]:
    out: dict[str, Any] = {"page_kind": "horse"}
    prof_table = soup.find("table", attrs={"class": lambda c: c and "db_prof_table" in c})
    horse_name = ""
    title = soup.find("h1")
    if title:
        horse_name = title.get_text(strip=True)
    if prof_table:
        for row in prof_table.find_all("tr"):
            th = row.find("th")
            td = row.find("td")
            if not th or not td:
                continue
            key = th.get_text(strip=True)
            val = td.get_text(strip=True)
            if "生年月日" in key:
                out["horse_birth_date"] = val
            elif "通算成績" in key:
                out["horse_total_runs"] = val
            elif "馬主" in key:
                out["horse_owner"] = val
            elif "生産者" in key:
                out["horse_breeder"] = val
            elif "産地" in key:
                out["horse_breeding_farm"] = val
    blood_table = soup.find("table", class_="blood_table")
    if blood_table:
        rows = blood_table.find_all("tr")
        if rows:
            first = rows[0].find_all("td")
            if first and first[0].find("a"):
                out["sire"] = first[0].find("a").get_text(strip=True)
        if rows:
            half = len(rows) // 2
            if half and len(rows) > half:
                mid = rows[half].find_all("td")
                if mid and mid[0].find("a"):
                    out["dam"] = mid[0].find("a").get_text(strip=True)
                if len(mid) >= 2 and mid[1].find("a"):
                    out["broodmare_sire"] = mid[1].find("a").get_text(strip=True)
    out.update({"horse_name": horse_name or None, "page_ok": bool(horse_name or prof_table or blood_table)})
    return out


def _extract_ped_fields(soup: BeautifulSoup) -> dict[str, Any]:
    out: dict[str, Any] = {"page_kind": "ped"}
    blood_table = soup.find("table", class_="blood_table")
    if not blood_table:
        return {**out, "page_ok": False}
    rows = blood_table.find_all("tr")
    if rows:
        first = rows[0].find_all("td")
        if first and first[0].find("a"):
            out["sire"] = first[0].find("a").get_text(strip=True)
    if rows:
        half = len(rows) // 2
        if half and len(rows) > half:
            mid = rows[half].find_all("td")
            if mid and mid[0].find("a"):
                out["dam"] = mid[0].find("a").get_text(strip=True)
            if len(mid) >= 2 and mid[1].find("a"):
                out["broodmare_sire"] = mid[1].find("a").get_text(strip=True)
    out["page_ok"] = True
    return out


def _parse_cache_html(page_kind: str, cache_key: str, html: str, race_id: str | None, horse_id: str | None) -> ParseResult:
    if not _is_valid_html(html):
        return ParseResult(page_kind=page_kind, cache_key=cache_key, page_ok=False, fields={}, quality_score=0.0, error="invalid-html")

    soup = BeautifulSoup(html, "lxml", parse_only=HTML_STRAINER)
    if page_kind == "race":
        fields = _extract_race_text_fields(soup, html, race_id, horse_id)
    elif page_kind == "ped":
        fields = _extract_ped_fields(soup)
    else:
        fields = _extract_horse_fields(soup, html)

    page_ok = bool(fields.pop("page_ok", False))
    quality = _score_from_fields(fields, page_ok=page_ok, cache_valid=True)
    return ParseResult(page_kind=page_kind, cache_key=cache_key, page_ok=page_ok, fields=fields, quality_score=quality)


def _resolve_candidates(candidate: Candidate) -> list[tuple[str, str, str]]:
    race_id = candidate.race_id or ""
    horse_id = candidate.horse_id or ""
    urls: list[tuple[str, str, str]] = []

    def add(page_kind: str, url: str, source_hint: str) -> None:
        if url:
            urls.append((page_kind, url, source_hint))

    race_url = f"https://db.netkeiba.com/race/{race_id}/" if race_id else ""
    horse_result_url = f"https://db.netkeiba.com/horse/result/{horse_id}/" if horse_id else ""
    horse_old_url = f"https://db.netkeiba.com/horse/{horse_id}/" if horse_id else ""
    horse_ped_url = f"https://db.netkeiba.com/horse/ped/{horse_id}/" if horse_id else ""

    if candidate.action == "repair-from-existing-metadata":
        add("race", race_url, "race-result-cache")
    elif candidate.action == "reparse-cache":
        if candidate.column in {"sire", "dam", "broodmare_sire"}:
            add("ped", horse_ped_url, "pedigree-cache")
            add("horse", horse_result_url, "horse-detail-cache")
            add("horse", horse_old_url, "horse-detail-cache-legacy")
        elif candidate.column in {"horse_name", "frame_number", "horse_number"}:
            add("race", race_url, "race-result-cache")
            add("horse", horse_result_url, "horse-detail-cache")
        else:
            add("race", race_url, "race-result-cache")
            add("horse", horse_result_url, "horse-detail-cache")
    elif candidate.action == "refetch-required":
        add("race", race_url, "race-result-cache")
    elif candidate.action == "schema-review":
        add("race", race_url, "race-result-cache")
    else:
        add("race", race_url, "race-result-cache")
        add("horse", horse_result_url, "horse-detail-cache")

    return urls


def _current_before_value(candidate: Candidate) -> Any:
    if candidate.reason == "domain-allowed-missing":
        return ""
    if candidate.reason == "consistency:race_without_horse_data":
        return "0 horses"
    if candidate.column in {"sire", "dam", "broodmare_sire"}:
        return ""
    return None


def _evaluate_candidate(candidate: Candidate, cache_conn: sqlite3.Connection, ped_conn: sqlite3.Connection | None) -> tuple[str, ParseResult | None, dict[str, Any]]:
    before = _current_before_value(candidate)
    current_quality = _current_quality(candidate)

    for page_kind, url, source_hint in _resolve_candidates(candidate):
        if page_kind == "ped":
            if not candidate.horse_id or ped_conn is None:
                continue
            ped = _fetch_pedigree_cache(ped_conn, candidate.horse_id)
            if ped:
                parsed = ParseResult(page_kind="ped", cache_key=f"pedigree_cache:{candidate.horse_id}", page_ok=True, fields=ped, quality_score=_score_from_fields(ped, True, True))
                after = parsed.fields.get(candidate.column)
                if candidate.column == "broodmare_sire" and not after:
                    after = parsed.fields.get("broodmare_sire") or parsed.fields.get("dam")
                return _classify(candidate, before, after, current_quality, parsed, source_hint)
            continue

        normalized, final_url, html = _fetch_http_cache_html(cache_conn, [url])
        if not normalized or html is None:
            continue

        parsed = _parse_cache_html(page_kind, normalized, html, candidate.race_id, candidate.horse_id)
        if not parsed.page_ok:
            return "reparse-failed", parsed, {"before": before, "after": None, "current_quality_score": current_quality, "reparsed_quality_score": parsed.quality_score, "source_cache_key": normalized, "source_hint": source_hint}

        after = parsed.fields.get(candidate.column)
        if candidate.reason == "consistency:race_without_horse_data":
            entries = parsed.fields.get("entries") if isinstance(parsed.fields.get("entries"), list) else []
            after = f"{len(entries)} horses parsed"
            before = "0 horses"
        return _classify(candidate, before, after, current_quality, parsed, source_hint)

    return "cache-missing", None, {
        "before": before,
        "after": None,
        "current_quality_score": current_quality,
        "reparsed_quality_score": 0.0,
        "source_cache_key": None,
        "source_hint": "cache-missing",
    }


def _classify(
    candidate: Candidate,
    before: Any,
    after: Any,
    current_quality: float,
    parsed: ParseResult,
    source_hint: str,
) -> tuple[str, ParseResult, dict[str, Any]]:
    repaired = False
    if candidate.reason == "consistency:race_without_horse_data":
        repaired = isinstance(after, str) and after != "0 horses"
    else:
        repaired = after not in (None, "", [], "unknown_local")

    if parsed.cache_key and parsed.quality_score <= 0:
        return "reparse-failed", parsed, {
            "before": before,
            "after": after,
            "current_quality_score": current_quality,
            "reparsed_quality_score": parsed.quality_score,
            "source_cache_key": parsed.cache_key,
            "source_hint": source_hint,
        }

    if parsed.quality_score < current_quality:
        return "no-downgrade-skip", parsed, {
            "before": before,
            "after": after,
            "current_quality_score": current_quality,
            "reparsed_quality_score": parsed.quality_score,
            "source_cache_key": parsed.cache_key,
            "source_hint": source_hint,
        }

    if before == after and candidate.reason != "consistency:race_without_horse_data":
        return "no-change", parsed, {
            "before": before,
            "after": after,
            "current_quality_score": current_quality,
            "reparsed_quality_score": parsed.quality_score,
            "source_cache_key": parsed.cache_key,
            "source_hint": source_hint,
        }

    if repaired:
        return "would-fix-from-cache", parsed, {
            "before": before,
            "after": after,
            "current_quality_score": current_quality,
            "reparsed_quality_score": parsed.quality_score,
            "source_cache_key": parsed.cache_key,
            "source_hint": source_hint,
        }

    return "manual-review", parsed, {
        "before": before,
        "after": after,
        "current_quality_score": current_quality,
        "reparsed_quality_score": parsed.quality_score,
        "source_cache_key": parsed.cache_key,
        "source_hint": source_hint,
    }


def _recommended_next_action(action: str, candidate: Candidate) -> str:
    if action == "would-fix-from-cache":
        if candidate.column == "finish_position":
            return "cache-backed reparse can repair finish_position"
        if candidate.reason == "consistency:race_without_horse_data":
            return "apply race cache parse result"
        if candidate.column in {"sire", "dam", "broodmare_sire"}:
            return "apply pedigree cache result"
        return "apply cache-backed reparse"
    if action == "cache-missing":
        return "keep as refetch candidate"
    if action == "reparse-failed":
        return "inspect broken cache or page mismatch"
    if action == "no-downgrade-skip":
        return "keep existing value and skip downgrade"
    if action == "no-change":
        return "no-op"
    return "manual review"


def _build_candidate_list(plan: dict[str, Any], target: str, max_targets: int) -> list[Candidate]:
    raw = plan.get("sample_targets", []) if isinstance(plan.get("sample_targets"), list) else []
    candidates: list[Candidate] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "")
        reason = str(item.get("reason") or "")
        column = str(item.get("column") or "")
        if action not in {"reparse-cache", "refetch-required", "repair-from-existing-metadata"}:
            continue
        if target != "all" and column not in {"(check)", target, "finish_position", "result_time", "margin", "horse_id", "horse_name", "frame_number", "horse_number", "race_date", "venue", "sire", "dam", "broodmare_sire"}:
            continue
        candidates.append(
            Candidate(
                race_id=str(item.get("race_id") or "").strip() or None,
                horse_id=str(item.get("horse_id") or "").strip() or None,
                column=column,
                reason=reason,
                action=action,
                priority=str(item.get("priority") or ""),
                source_hint=str(item.get("source_hint") or ""),
                recommended_next_action=str(item.get("recommended_next_action") or ""),
            )
        )
        if len(candidates) >= max_targets:
            break
    return candidates


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build a read-only dry-run plan for P0 reparse cache viability")
    p.add_argument("--input-p0-plan", default=str(DEFAULT_P0_PLAN_INPUT), help="Path to p0_scrape_repair_plan.json")
    p.add_argument("--target", choices=["all", "race", "horse", "result", "pedigree", "odds"], default="all")
    p.add_argument("--max-targets", type=int, default=120, help="Maximum sample targets to inspect")
    p.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON path")
    p.add_argument("--cache-db", default=str(DEFAULT_CACHE_DB), help="Read-only fetch cache DB path")
    p.add_argument("--pedigree-cache-db", default=str(DEFAULT_PEDIGREE_CACHE_DB), help="Read-only pedigree cache DB path")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    plan = _load_json(Path(args.input_p0_plan), label="input-p0-plan")

    cache_path = Path(args.cache_db)
    ped_path = Path(args.pedigree_cache_db)
    candidates = _build_candidate_list(plan, str(args.target), int(args.max_targets))

    cache_conn = _open_cache_db(cache_path)
    ped_conn = _open_cache_db(ped_path) if ped_path.exists() else None
    try:
        records: list[dict[str, Any]] = []
        action_counts: Counter[str] = Counter()
        cache_available_count = 0
        cache_missing_count = 0
        reparse_attempt_count = 0
        reparse_success_count = 0
        reparse_failed_count = 0
        repairable_count = 0
        no_downgrade_skip_count = 0
        estimated_db_update_count = 0

        grouped_samples: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for cand in candidates:
            action, parsed, detail = _evaluate_candidate(cand, cache_conn, ped_conn)
            detail = dict(detail)
            detail.update(
                {
                    "race_id": cand.race_id,
                    "horse_id": cand.horse_id,
                    "column": cand.column,
                    "reason": cand.reason,
                    "action": action,
                    "priority": cand.priority,
                    "recommended_next_action": _recommended_next_action(action, cand),
                }
            )
            records.append(detail)
            action_counts[action] += 1

            if action != "cache-missing":
                cache_available_count += 1
                reparse_attempt_count += 1
            else:
                cache_missing_count += 1

            if action in {"would-fix-from-cache", "no-downgrade-skip", "no-change", "manual-review"}:
                reparse_success_count += 1
            elif action in {"reparse-failed"}:
                reparse_failed_count += 1
            elif action == "cache-missing":
                pass

            if action == "would-fix-from-cache":
                repairable_count += 1
                estimated_db_update_count += 1
            if action == "no-downgrade-skip":
                no_downgrade_skip_count += 1

            grouped_samples[action].append(detail)

        would_fix_count = int(action_counts.get("would-fix-from-cache", 0))
        would_not_fix_count = len(records) - would_fix_count
        not_repairable_count = len(records) - repairable_count

        sample_diffs: list[dict[str, Any]] = []
        for action_name in [
            "would-fix-from-cache",
            "cache-missing",
            "reparse-failed",
            "no-downgrade-skip",
            "no-change",
            "manual-review",
        ]:
            for item in grouped_samples.get(action_name, [])[:10]:
                sample_diffs.append(
                    {
                        "race_id": item.get("race_id"),
                        "horse_id": item.get("horse_id"),
                        "column": item.get("column"),
                        "before": item.get("before"),
                        "after": item.get("after"),
                        "current_quality_score": item.get("current_quality_score"),
                        "reparsed_quality_score": item.get("reparsed_quality_score"),
                        "action": item.get("action"),
                        "reason": item.get("reason"),
                        "source_cache_key": item.get("source_cache_key"),
                        "recommended_next_action": item.get("recommended_next_action"),
                    }
                )

        payload = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "input_p0_plan": str(args.input_p0_plan),
            "target": args.target,
            "max_targets": int(args.max_targets),
            "verdict": "pass" if would_fix_count == 0 else "warn",
            "p0_total_count": len(records),
            "cache_available_count": cache_available_count,
            "cache_missing_count": cache_missing_count,
            "reparse_attempt_count": reparse_attempt_count,
            "reparse_success_count": reparse_success_count,
            "reparse_failed_count": reparse_failed_count,
            "repairable_count": repairable_count,
            "not_repairable_count": not_repairable_count,
            "would_fix_count": would_fix_count,
            "would_not_fix_count": would_not_fix_count,
            "no_downgrade_skip_count": no_downgrade_skip_count,
            "estimated_db_update_count": estimated_db_update_count,
            "estimated_http_request_count": 0,
            "sample_diffs": sample_diffs,
            "recommended_next_actions": [
                "finish_position / result_time / margin は cache があれば reparse-cache を優先",
                "cache がないものは cache-missing として保持し、HTTP 再取得しない dry-run に留める",
                "sire / dam / broodmare_sire は horse/ped cache を優先して比較する",
                "no-downgrade-skip は既存品質を維持し、採用しない",
            ],
            "safeguards": {
                "read_only": True,
                "no_db_write": True,
                "no_http_access": True,
                "no_scrape_execute": True,
                "no_upsert": True,
                "no_force_refresh_execute": True,
            },
        }

        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        print(
            json.dumps(
                {
                    "output": str(out),
                    "verdict": payload["verdict"],
                    "target": payload["target"],
                    "p0_total_count": payload["p0_total_count"],
                    "cache_available_count": payload["cache_available_count"],
                    "cache_missing_count": payload["cache_missing_count"],
                    "reparse_attempt_count": payload["reparse_attempt_count"],
                    "reparse_success_count": payload["reparse_success_count"],
                    "reparse_failed_count": payload["reparse_failed_count"],
                    "repairable_count": payload["repairable_count"],
                    "not_repairable_count": payload["not_repairable_count"],
                    "would_fix_count": payload["would_fix_count"],
                    "would_not_fix_count": payload["would_not_fix_count"],
                    "no_downgrade_skip_count": payload["no_downgrade_skip_count"],
                    "estimated_db_update_count": payload["estimated_db_update_count"],
                    "estimated_http_request_count": payload["estimated_http_request_count"],
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        cache_conn.close()
        if ped_conn is not None:
            ped_conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
