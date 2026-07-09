#!/usr/bin/env python3
"""Read-only diagnosis for source-empty result cells in live validation samples.

This script reads an existing live validation report and cached HTML only.
It performs no HTTP access and no DB writes.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT_DIR / "reports" / "p0_targeted_refetch_live_validation.json"
DEFAULT_CACHE_DB = ROOT_DIR / "keiba" / "data" / "fetch_cache.db"
DEFAULT_OUTPUT = ROOT_DIR / "reports" / "source_empty_result_cells_diagnosis.json"

DOMAIN_CANCELED_TOKENS = ("取消",)
DOMAIN_EXCLUDED_TOKENS = ("除外",)
DOMAIN_DNF_TOKENS = ("競走中止", "中止", "失格", "降着", "取")
DOMAIN_NON_STARTER_TOKENS = ("未出走", "不出走")


@dataclass
class Target:
    url: str
    race_id: str | None
    horse_id: str | None
    horse_number: str | None
    horse_name: str | None


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"error: {label} not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"error: failed to parse {label}: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"error: invalid {label} JSON object: {path}")
    return payload


def _open_ro_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"error: cache-db not found: {path}")
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path or "/"
    query = parts.query or ""
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}{('?' + query) if query else ''}"


def _decode_body(raw: Any) -> str:
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


def _fetch_cached_html(conn: sqlite3.Connection, url: str, race_id: str | None) -> tuple[str | None, str | None, str | None]:
    normalized = _normalize_url(url)
    row = conn.execute(
        "SELECT normalized_url, final_url, body FROM http_cache WHERE normalized_url = ?",
        (normalized,),
    ).fetchone()
    if row:
        return str(row[0] or normalized), str(row[1] or ""), _decode_body(row[2])

    if race_id:
        like = f"%/race/{race_id}/%"
        row = conn.execute(
            "SELECT normalized_url, final_url, body FROM http_cache WHERE normalized_url LIKE ? OR final_url LIKE ? ORDER BY fetched_at DESC LIMIT 1",
            (like, like),
        ).fetchone()
        if row:
            return str(row[0] or ""), str(row[1] or ""), _decode_body(row[2])

    return None, None, None


def _extract_horse_id_from_cell(cell: Any) -> str:
    a = cell.find("a") if cell else None
    href = str(a.get("href") or "") if a else ""
    m = re.search(r"/horse/(?:result/)?([A-Za-z0-9]+)(?:/|$)", href)
    return m.group(1) if m else ""


def _norm_header(v: str) -> str:
    return re.sub(r"\s+", "", v or "")


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(tok in text for tok in tokens)


def _target_key(race_id: str | None, horse_id: str | None) -> str:
    return f"{race_id or ''}:{horse_id or ''}"


def _collect_targets(report: dict[str, Any], max_samples: int) -> list[Target]:
    rows = report.get("sample_results") if isinstance(report.get("sample_results"), list) else []
    out: list[Target] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("action") or "") != "source-empty-result-cells":
            continue
        out.append(
            Target(
                url=str(row.get("url") or "").strip(),
                race_id=str(row.get("race_id") or "").strip() or None,
                horse_id=str(row.get("horse_id") or "").strip() or None,
                horse_number=str(row.get("horse_number") or "").strip() or None,
                horse_name=str(row.get("horse_name") or "").strip() or None,
            )
        )
        if len(out) >= max_samples:
            break
    return [x for x in out if x.url]


def _classify_target(target: Target, html: str) -> tuple[str, dict[str, Any], str]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="race_table_01")
    if table is None:
        return "alternate-page-required", {
            "html_has_result_table": False,
            "target_row_found": False,
            "target_row_all_td_text": [],
            "other_horses_have_results": False,
            "result_finalized_signal": False,
            "finish_cell": "",
            "time_cell": "",
            "margin_cell": "",
            "remarks_cell": "",
            "matched_horse_id": "",
            "matched_horse_name": "",
            "matched_horse_number": "",
        }, "race_table_01 not found in cached HTML"

    trs = table.find_all("tr")
    if not trs:
        return "alternate-page-required", {
            "html_has_result_table": True,
            "target_row_found": False,
            "target_row_all_td_text": [],
            "other_horses_have_results": False,
            "result_finalized_signal": False,
            "finish_cell": "",
            "time_cell": "",
            "margin_cell": "",
            "remarks_cell": "",
            "matched_horse_id": "",
            "matched_horse_name": "",
            "matched_horse_number": "",
        }, "result table rows are empty"

    headers = [c.get_text(strip=True) for c in trs[0].find_all(["th", "td"])]
    nheaders = [_norm_header(h) for h in headers]

    def hidx(*keys: str) -> int:
        for key in keys:
            for i, h in enumerate(nheaders):
                if key in h:
                    return i
        return -1

    idx_finish = hidx("着順")
    idx_time = hidx("タイム")
    idx_margin = hidx("着差")
    idx_horse_num = hidx("馬番")
    idx_horse = hidx("馬名")
    idx_remarks = hidx("備考", "状態")

    target_row = None
    wrong_target_row = False
    other_horses_have_results = False

    parsed_rows: list[dict[str, Any]] = []
    for tr in trs[1:]:
        cols = tr.find_all("td")
        if len(cols) < 4:
            continue

        def cell(i: int) -> str:
            return cols[i].get_text(strip=True) if i >= 0 and i < len(cols) else ""

        horse_id = _extract_horse_id_from_cell(cols[idx_horse]) if idx_horse >= 0 and idx_horse < len(cols) else ""
        horse_name = cell(idx_horse)
        horse_number = cell(idx_horse_num)
        finish = cell(idx_finish)
        time_txt = cell(idx_time)
        margin = cell(idx_margin)
        remarks = cell(idx_remarks)

        parsed_rows.append(
            {
                "horse_id": horse_id,
                "horse_name": horse_name,
                "horse_number": horse_number,
                "finish": finish,
                "time": time_txt,
                "margin": margin,
                "remarks": remarks,
                "all_td_text": [c.get_text(strip=True) for c in cols],
            }
        )

    for row in parsed_rows:
        if target.horse_id and row["horse_id"] == target.horse_id:
            target_row = row
            break

    if target_row is None and target.horse_number:
        for row in parsed_rows:
            if row["horse_number"] == target.horse_number:
                target_row = row
                if target.horse_id and row["horse_id"] and row["horse_id"] != target.horse_id:
                    wrong_target_row = True
                break

    if target_row is None and target.horse_name:
        for row in parsed_rows:
            if row["horse_name"] == target.horse_name:
                target_row = row
                wrong_target_row = True
                break

    if parsed_rows:
        for row in parsed_rows:
            if target_row is not None and row is target_row:
                continue
            if row["finish"] or row["time"] or row["margin"]:
                other_horses_have_results = True
                break

    page_text = soup.get_text(" ", strip=True)
    result_finalized_signal = bool(
        ("払戻" in page_text)
        or ("レース結果" in page_text)
        or other_horses_have_results
    )

    if target_row is None:
        if wrong_target_row:
            cls = "wrong-target-row"
            reason = "target row not found by horse_id; nearest row matched by horse_number/horse_name"
        else:
            cls = "alternate-page-required"
            reason = "target row not found in result table"
        return cls, {
            "html_has_result_table": True,
            "target_row_found": False,
            "target_row_all_td_text": [],
            "other_horses_have_results": other_horses_have_results,
            "result_finalized_signal": result_finalized_signal,
            "finish_cell": "",
            "time_cell": "",
            "margin_cell": "",
            "remarks_cell": "",
            "matched_horse_id": "",
            "matched_horse_name": "",
            "matched_horse_number": "",
        }, reason

    finish_cell = str(target_row.get("finish") or "")
    time_cell = str(target_row.get("time") or "")
    margin_cell = str(target_row.get("margin") or "")
    remarks_cell = str(target_row.get("remarks") or "")
    row_text = " ".join([finish_cell, time_cell, margin_cell, remarks_cell, str(target_row.get("horse_name") or "")]).strip()

    if _contains_any(row_text, DOMAIN_CANCELED_TOKENS):
        cls = "domain-allowed-canceled"
        reason = "target row indicates cancellation token"
    elif _contains_any(row_text, DOMAIN_EXCLUDED_TOKENS):
        cls = "domain-allowed-excluded"
        reason = "target row indicates excluded token"
    elif _contains_any(row_text, DOMAIN_DNF_TOKENS):
        cls = "domain-allowed-did-not-finish"
        reason = "target row indicates did-not-finish token"
    elif _contains_any(row_text, DOMAIN_NON_STARTER_TOKENS):
        cls = "domain-allowed-non-starter"
        reason = "target row indicates non-starter token"
    elif wrong_target_row:
        cls = "wrong-target-row"
        reason = "target row matched only by fallback key and conflicts with target horse_id"
    elif other_horses_have_results and not (finish_cell or time_cell or margin_cell):
        cls = "manual-review-required"
        reason = "other horses have results but target row cells are empty without status token"
    elif result_finalized_signal and not (finish_cell or time_cell or margin_cell):
        cls = "source-result-missing"
        reason = "result page appears finalized but target row has no result values"
    elif not result_finalized_signal:
        cls = "alternate-page-required"
        reason = "result finalization signal missing; alternate page/source may be required"
    else:
        cls = "manual-review-required"
        reason = "unclassified empty result row"

    return cls, {
        "html_has_result_table": True,
        "target_row_found": True,
        "target_row_all_td_text": list(target_row.get("all_td_text") or []),
        "other_horses_have_results": other_horses_have_results,
        "result_finalized_signal": result_finalized_signal,
        "finish_cell": finish_cell,
        "time_cell": time_cell,
        "margin_cell": margin_cell,
        "remarks_cell": remarks_cell,
        "matched_horse_id": str(target_row.get("horse_id") or ""),
        "matched_horse_name": str(target_row.get("horse_name") or ""),
        "matched_horse_number": str(target_row.get("horse_number") or ""),
    }, reason


def _recommended_next_actions(counts: Counter[str]) -> list[str]:
    out: list[str] = []
    domain_allowed_count = sum(
        counts.get(k, 0)
        for k in (
            "domain-allowed-non-starter",
            "domain-allowed-canceled",
            "domain-allowed-excluded",
            "domain-allowed-did-not-finish",
        )
    )
    if domain_allowed_count > 0:
        out.append("domain-allowed 系は targeted refetch execution 候補から除外")
    if counts.get("source-result-missing", 0) > 0:
        out.append("source-result-missing は manual review または alternate source 候補として扱う")
    if counts.get("alternate-page-required", 0) > 0:
        out.append("alternate-page-required は URL生成/ページ種別を見直す")
    if counts.get("wrong-target-row", 0) > 0:
        out.append("wrong-target-row は horse_id / horse_number matching 修正候補")
    if counts.get("manual-review-required", 0) > 0:
        out.append("manual-review-required は実ページとドメインルール確認を優先")
    if not out:
        out.append("source-empty-result-cells は検出されたが追加分類対象はありません")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose source-empty result cells from live-validation report and cache")
    parser.add_argument("--input-live-validation", default=str(DEFAULT_INPUT), help="Path to p0_targeted_refetch_live_validation.json")
    parser.add_argument("--cache-db", default=str(DEFAULT_CACHE_DB), help="Path to fetch_cache.db (read-only)")
    parser.add_argument("--max-samples", type=int, default=20, help="Maximum source-empty samples to diagnose")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON path")
    args = parser.parse_args()

    live_report = _load_json(Path(args.input_live_validation), "input-live-validation")
    targets = _collect_targets(live_report, max(1, int(args.max_samples)))

    cache_conn = _open_ro_db(Path(args.cache_db))
    try:
        samples: list[dict[str, Any]] = []
        counts: Counter[str] = Counter()
        checked_count = 0

        for target in targets:
            checked_count += 1
            cache_key, final_url, html = _fetch_cached_html(cache_conn, target.url, target.race_id)
            if html is None:
                classification = "alternate-page-required"
                reason = "cached HTML not found for target URL/race"
                detail = {
                    "html_has_result_table": False,
                    "target_row_found": False,
                    "target_row_all_td_text": [],
                    "other_horses_have_results": False,
                    "result_finalized_signal": False,
                    "finish_cell": "",
                    "time_cell": "",
                    "margin_cell": "",
                    "remarks_cell": "",
                    "matched_horse_id": "",
                    "matched_horse_name": "",
                    "matched_horse_number": "",
                }
            else:
                classification, detail, reason = _classify_target(target, html)

            counts[classification] += 1
            samples.append(
                {
                    "key": _target_key(target.race_id, target.horse_id),
                    "race_id": target.race_id,
                    "horse_id": target.horse_id,
                    "horse_number": target.horse_number,
                    "horse_name": target.horse_name,
                    "url": target.url,
                    "cache_key": cache_key,
                    "cache_final_url": final_url,
                    "classification": classification,
                    "classification_reason": reason,
                    **detail,
                }
            )

        domain_allowed_count = sum(
            counts.get(k, 0)
            for k in (
                "domain-allowed-non-starter",
                "domain-allowed-canceled",
                "domain-allowed-excluded",
                "domain-allowed-did-not-finish",
            )
        )

        out = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "input_live_validation": str(args.input_live_validation),
            "input_cache_db": str(args.cache_db),
            "max_samples": int(args.max_samples),
            "checked_count": checked_count,
            "domain_allowed_count": domain_allowed_count,
            "source_result_missing_count": int(counts.get("source-result-missing", 0)),
            "wrong_target_row_count": int(counts.get("wrong-target-row", 0)),
            "alternate_page_required_count": int(counts.get("alternate-page-required", 0)),
            "manual_review_count": int(counts.get("manual-review-required", 0)),
            "classification_breakdown": [
                {"classification": k, "count": int(v)}
                for k, v in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
            ],
            "sample_diagnostics": samples,
            "recommended_next_actions": _recommended_next_actions(counts),
            "safety_flags": {
                "read_only": True,
                "no_http_access": True,
                "no_db_write": True,
                "no_upsert": True,
                "no_repair_execute": True,
                "no_bulk_refetch": True,
            },
        }

        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

        print(
            json.dumps(
                {
                    "output": str(output),
                    "checked_count": out["checked_count"],
                    "domain_allowed_count": out["domain_allowed_count"],
                    "source_result_missing_count": out["source_result_missing_count"],
                    "wrong_target_row_count": out["wrong_target_row_count"],
                    "alternate_page_required_count": out["alternate_page_required_count"],
                    "manual_review_count": out["manual_review_count"],
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        cache_conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
