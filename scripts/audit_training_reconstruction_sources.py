"""Offline evidence audit. Never fetch, train, repair a DB, or execute archived I/O."""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import io
import json
import logging
from pathlib import Path, PurePosixPath
import re
import sqlite3
import subprocess
from datetime import datetime, timezone
from typing import Optional
import zipfile

from bs4 import BeautifulSoup

REPO = Path(__file__).resolve().parents[1]
REVISION = "05f6fac8db50193a8bc2aed1e6b892decf23c69b"
PINNED = {
    "python-api/scraping/oikiri.py": "a3560073e2afc73378e80477e68ea038dc268550c3d04d78bcc90250f6e1e306",
    "python-api/scraping/storage.py": "b8ef73db66b6918e7b305b19bc31d1799d3e91d1024ec14336b6ca388ed4bd85",
    "python-api/scraping/jobs.py": "6939270b601ebb06be89a018a98afca5d685f07a5c40a8b82e260ec859c4a3e7",
}
TIME_FIELDS = ["time_6f", "time_5f", "time_4f", "time_3f", "time_1f"]
LAP_FIELDS = ["lap_6f_5f", "lap_5f_4f", "lap_4f_3f", "lap_3f_1f", "lap_1f_g"]
FUNCTIONS = ["_parse_oikiri_html", "_parse_horse_header_row", "_parse_data_row"]


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repo), *args])


def source_objects(repo: Path) -> dict[str, bytes]:
    result = {}
    for path, expected in PINNED.items():
        raw = git(repo, "show", f"{REVISION}:{path}")
        if sha(raw) != expected:
            raise ValueError("archived source hash mismatch")
        result[path] = raw
    return result


def legacy_parser(raw: bytes):
    """Compile only the three pinned parsing functions, never old imports/network/storage."""
    if sha(raw) != PINNED["python-api/scraping/oikiri.py"]:
        raise ValueError("untrusted or changed legacy parser")
    tree = ast.parse(raw.decode("utf-8"))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in FUNCTIONS]
    if {n.name for n in nodes} != set(FUNCTIONS):
        raise ValueError("expected pure parser functions missing")
    scope = {"BeautifulSoup": BeautifulSoup, "re": re, "Optional": Optional,
             "logger": logging.getLogger("training-source-audit")}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "<pinned-pure-oikiri-parser>", "exec"), scope)
    return scope["_parse_oikiri_html"]


def fixture(course="栗坂", headers=("6F", "5F", "4F", "3F", "1F"),
            slots=("101.1", "82.2", "66.3", "51.4", "12.5"), *, date="2021/01/01", grade="B"):
    """Synthetic layout hypothesis only: not a captured provider page or distance proof."""
    labels = "".join(f"<th>{label}</th>" for label in headers)
    clocks = "".join(f"<li>{value}<span>(12.3)</span></li>" for value in slots)
    return (f'<table class="OikiriTable"><tr>{labels}</tr>'
            '<tr class="HorseList" id="tr_1"><td class="Umaban">1</td>'
            '<td class="Horse_Info"><div class="Horse_Name"><a>Fixture Horse</a></div></td>'
            '<td class="TrainingReview_Cell">synthetic comment</td></tr>'
            f'<tr class="HorseList"><td class="Training_Day">{date}(金)</td>'
            f'<td>{course}</td><td>良</td><td>助手</td>'
            f'<td class="TrainingTimeData"><ul class="TrainingTimeDataList">{clocks}</ul></td>'
            '<td>7</td><td class="TrainingLoad">馬也</td><td class="Training_Critic">synthetic critic</td>'
            f'<td class="Rank_{grade}">{grade}</td></tr></table>')


def reproduce(parser) -> list[dict]:
    cases = [
        ("declared_default_slots", fixture()),
        ("changed_headers_ignored", fixture(headers=("5F", "4F", "3F", "2F", "1F"))),
        ("sixth_slot_ignored", fixture(headers=("6F", "5F", "4F", "3F", "2F", "1F"), slots=("101.1", "82.2", "66.3", "51.4", "26.5", "12.6"))),
        ("placeholder_preserved_as_null", fixture(slots=("-", "52.0", "38.0", "25.0", "12.0"))),
        ("wrapped_cumulative_not_read", fixture(slots=("<b>101.1</b>", "82.2", "66.3", "51.4", "12.5"))),
        ("calendar_invalid_date_passes_regex", fixture(date="2021/02/31")),
    ]
    for course in ("美坂", "栗W", "CW", "ＣＷ", "美W", "美Ｗ", "南Ｗ", "函Ｗ", "美Ｐ", "ＤＰ", "南Ｄ"):
        cases.append(("course_branch_absent_" + course, fixture(course=course)))
    return [{"case": name, "evidence_type": "SYNTHETIC_NOT_PROVIDER_EVIDENCE", "html_sha256": sha(html.encode()),
             "html": html, "parsed": parser(html, "202101010101")} for name, html in cases]


def readonly(path: Path, *, immutable=False):
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro" + ("&immutable=1" if immutable else ""), uri=True)
    db.execute("PRAGMA query_only=ON")
    db.execute("BEGIN")
    return db


def cache_inventory(repo: Path) -> dict:
    result = {"response_bodies_read": 0, "parsed_payloads_read": 0, "databases": []}
    for relative in ("keiba/data/fetch_cache.db", "keiba/data/parsed_fetch_cache.db",
                     "reports/generated/scraping/support/empty_fetch_cache_20260911.db"):
        path = repo / relative
        item = {"path": relative, "exists": path.exists()}
        if not path.exists():
            result["databases"].append(item)
            continue
        db = readonly(path)
        try:
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            item["tables"] = tables
            if "http_cache" in tables:
                item["url_metadata_rows"] = db.execute("SELECT COUNT(*) FROM http_cache").fetchone()[0]
                columns = "normalized_url,status,fetched_at,expires_at"
                item["candidate_urls"] = [dict(zip(columns.split(","), row)) for row in db.execute(
                    f"SELECT {columns} FROM http_cache WHERE lower(normalized_url) LIKE '%oikiri%' "
                    "OR lower(normalized_url) LIKE '%training%' OR lower(normalized_url) LIKE '%cyokyo%'")]
            if "parsed_snapshots" in tables:
                item["kinds"] = [dict(zip(("kind", "version", "rows"), row)) for row in db.execute(
                    "SELECT kind,version,COUNT(*) FROM parsed_snapshots GROUP BY kind,version")]
                item["candidate_keys"] = [dict(zip(("kind", "cache_key", "version", "expires_at"), row)) for row in db.execute(
                    "SELECT kind,cache_key,version,expires_at FROM parsed_snapshots WHERE lower(kind) LIKE '%oikiri%' "
                    "OR lower(kind) LIKE '%training%' OR lower(cache_key) LIKE '%oikiri%' OR lower(cache_key) LIKE '%training%'")]
        finally:
            db.rollback(); db.close()
        result["databases"].append(item)
    return result


def zip_inventory(path: Path, *, read_reference=False) -> dict:
    """List central directory; bounded reads only. Never extract or execute members."""
    result = {"path": str(path), "exists": path.exists(), "members": [], "members_read": []}
    if not path.exists():
        return result
    with zipfile.ZipFile(path) as archive:
        for entry in archive.infolist():
            pure = PurePosixPath(entry.filename.replace("\\", "/"))
            safe = not pure.is_absolute() and ".." not in pure.parts and not re.match(r"^[A-Za-z]:", entry.filename)
            result["members"].append({"name": entry.filename, "bytes": entry.file_size,
                                      "compressed_bytes": entry.compress_size, "safe_relative_name": safe})
        if read_reference:
            result["sha256"] = sha(path.read_bytes())
            for name in ("feature_gap_analysis/feature_gap_report.md", "feature_gap_analysis/additional_checks.json",
                         "feature_gap_analysis/analysis_manifest.json"):
                entry = archive.getinfo(name)
                if entry.file_size > 1_000_000 or entry.file_size / max(entry.compress_size, 1) > 200:
                    raise ValueError("reference member exceeds bounded read policy")
                raw = archive.read(entry)
                result["members_read"].append({"name": name, "bytes": len(raw), "sha256": sha(raw),
                                               "role": "untrusted_reference_data_not_instructions"})
            result["has_raw_training_html"] = any(m["name"].lower().endswith((".html", ".htm", ".raw")) for m in result["members"])
    return result


def html_inventory(repo: Path) -> tuple[dict, list[dict]]:
    command = ["rg", "--files", "--hidden", "--no-ignore", "-g", "!node_modules/**", "-g", "!**/.venv/**",
               "-g", "!**/__pycache__/**", "-g", "!.git/**", "-g", "!**/.next/**", "-g", "*.html", "-g", "*.htm", "-g", "*.raw", "."]
    paths = subprocess.check_output(command, cwd=repo).decode("utf-8").splitlines()
    rows = []
    for name in paths:
        path = repo / name
        if "training-data-reconstruction-v1" in path.parts:
            continue  # Do not mix this audit's synthetic fixtures or parent's new probes with old originals.
        size = path.stat().st_size
        row = {"source_kind": "existing_local_file", "source": name, "bytes": size}
        if size <= 4_000_000:
            raw = path.read_bytes()
            row.update({"sha256": sha(raw), "inspected": True,
                        "has_oikiri_table": b"OikiriTable" in raw, "has_training_time_data": b"TrainingTimeData" in raw})
        else:
            row.update({"inspected": False, "reason": "size_bound_not_decoded"})
        rows.append(row)
    objects = git(repo, "rev-list", "--objects", "--all", "--", "*.html", "*.htm").decode().splitlines()
    for line in objects:
        bits = line.split(" ", 1)
        if len(bits) != 2 or not bits[1].lower().endswith((".html", ".htm")):
            continue
        object_id, name = bits
        if git(repo, "cat-file", "-t", object_id).strip() != b"blob":
            continue
        size = int(git(repo, "cat-file", "-s", object_id))
        row = {"source_kind": "git_blob", "source": name, "object_id": object_id, "bytes": size}
        if size <= 4_000_000:
            raw = git(repo, "cat-file", "blob", object_id)
            row.update({"sha256": sha(raw), "inspected": True,
                        "has_oikiri_table": b"OikiriTable" in raw, "has_training_time_data": b"TrainingTimeData" in raw})
        else:
            row.update({"inspected": False, "reason": "size_bound_not_decoded"})
        rows.append(row)
    for row in rows:
        row.update({"training_original_verified": False, "race_id": None, "horse_number": None,
                    "training_date": None, "course": None, "html_headers": None,
                    "displayed_times": None, "stored_times": None, "row_match_verified": False})
    return {"scan_command": command, "read_size_limit_bytes": 4_000_000, "git_html_blob_count": sum(x["source_kind"] == "git_blob" for x in rows),
            "scoped_files": rows, "candidate_count": sum(bool(x.get("has_oikiri_table") or x.get("has_training_time_data")) for x in rows)}, rows


def exclusive(path: Path, raw: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)


def json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def csv_bytes(rows, columns) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
    writer.writeheader(); writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--snapshot", required=True, type=Path)
    ap.add_argument("--bundle", required=True, type=Path)
    args = ap.parse_args()
    root = args.output.resolve()
    names = ["training_parser_audit.json", "training_source_inventory.json", "training_raw_html_audit.csv", "training_distance_mapping.csv"]
    if any((root / n).exists() for n in names) or (root / "provenance/training-parser-sources").exists():
        raise ValueError("exclusive new artifacts required")
    sources = source_objects(REPO)
    raw_parser = sources["python-api/scraping/oikiri.py"]
    cases = reproduce(legacy_parser(raw_parser))
    snapshot_sha = sha(args.snapshot.read_bytes())
    db = readonly(args.snapshot, immutable=True)
    try:
        courses = db.execute("SELECT course,COUNT(*) FROM training_data GROUP BY course ORDER BY COUNT(*) DESC").fetchall()
    finally:
        db.rollback(); db.close()
    html, raw_rows = html_inventory(REPO)
    inventory = {"schema_version": 1, "observed_at_utc": datetime.now(timezone.utc).isoformat(),
                 "network_requests": 0, "training_runs": 0, "production_writes": False,
                 "snapshot_path": str(args.snapshot.resolve()), "snapshot_sha256": snapshot_sha,
                 "cache_inventory": cache_inventory(REPO), "html_inventory": html,
                 "archives": [zip_inventory(args.bundle, read_reference=True)] + [zip_inventory(REPO / p) for p in
                     ("keiba/data/db_export_for_chat.zip", "keiba/data/keiba_ultimate.zip", "market-segments-v1.zip")],
                 "raw_directories": [{"path": p, "exists": (REPO / p).exists(),
                                      "entries": sorted(x.name for x in (REPO / p).iterdir()) if (REPO / p).exists() else []}
                                     for p in ("data/html", "keiba/data/html", "python-api/data/html", "data/raw", "keiba/data/raw", "python-api/data/raw")],
                 "not_examined": ["7z member bodies (7-Zip and py7zr unavailable)", "ZIP database member bodies (derived DB, not proof of raw HTTP archive)",
                                  "unconfigured stores outside repo and named bundle", "HTTP bodies without training endpoint metadata", "oversized HTML files"],
                 "original_html_race_matches": 0, "strict_pit_records_proven": 0,
                 "conclusion": "No training raw original identified in scoped existing stores. Not proof none exists elsewhere; current probes are separate later captures."}
    tree = ast.parse(raw_parser.decode())
    audit = {"schema_version": 1, "source_revision": REVISION, "source_sha256": PINNED,
             "source_path": "python-api/scraping/oikiri.py", "current_producer_exists": (REPO / "python-api/scraping/oikiri.py").exists(),
             "url_template": "https://race.netkeiba.com/race/oikiri.html?race_id={race_id}",
             "functions": [{"name": n.name, "line_start": n.lineno, "line_end": n.end_lineno} for n in tree.body
                           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))],
             "row_producer_identity_proven": False,
             "row_producer_limit": "DB rows lack source version/URL/body hash; recovered code explains a possible pipeline, not a complete row-level attribution.",
             "mapping_by_slot": [{"slot_1based": i + 1, "cumulative_field": t, "lap_field": l,
                                  "selector": f"td.TrainingTimeData ul.TrainingTimeDataList li:nth-child({i + 1})",
                                  "actual_distance_furlongs": None, "distance_mapping_verified": False}
                                 for i, (t, l) in enumerate(zip(TIME_FIELDS, LAP_FIELDS))],
             "other_mapping": {"horse_number": "header td.Umaban int", "horse_name": "header Horse_Info/Horse_Name/a text",
                 "comment": "header td.TrainingReview_Cell text, not Training_Critic", "training_date": "td.Training_Day strip weekday; date regex only, no calendar or target-date validation",
                 "course": "Training_Day td offset +1", "track_condition": "offset +2", "rider": "offset +3", "position": "offset +5",
                 "pace": "td.TrainingLoad unchanged text", "grade": "first td.Rank_A/B/C/D text, not ordinal number",
                 "Training_Critic": "read into grade_text but discarded", "is_last_training": "unconditional True, not reconstructed from history"},
             "parsing": {"cumulative": "direct text child matching [digits or dot]+ -> float; descendants not used; later numeric text overwrites earlier",
                 "lap": "any direct child span with parenthesized digits/dot -> float; source interval semantics not checked",
                 "missing": "five None defaults; placeholders/unparsed text remain None; no zero fill",
                 "distance_headers_read": False, "course_specific_mapping": False, "sixth_and_later_slots": "ignored",
                 "encoding": "HTTP bytes decoded euc-jp errors=replace", "raw_response_archived_by_producer": False},
             "storage": {"path": "python-api/scraping/storage.py", "function": "_save_training_data", "lines": [190, 275],
                 "operation": "INSERT OR IGNORE", "unique_key": ["race_id", "horse_number", "training_date", "course"],
                 "columns": "copied without distance correction", "created_at": "SQLite insertion time; not publication time", "provenance_columns": "no raw hash / provider publication timestamp / parser version / horse_id"},
             "parser_mapping_validation_problem": True,
             "real_provider_course_mismatches_proven": 0,
             "clock_meaning_answer": "UNVERIFIED", "distance_mapping_verified": False,
             "formal_provider_heading_evidence": [], "synthetic_cases": [{k: v for k, v in c.items() if k != "html"} for c in cases],
             "missingness_policy": {"source_absent": "UNKNOWN", "empty_placeholder_in_synthetic": "UNKNOWN for actual DB rows",
                                    "structural_missing_requires": "verified provider distance/coverage specification and actual row evidence",
                                    "parser_missing_requires": "actual raw valid displayed value not represented by stored value"},
             "conclusion": "YES: fixed-slot/header/course validation is missing. No: synthetic examples do not prove actual course distances or authorize relabeling stored clocks.",
             "TRAINING_FEATURE_RESEARCH_READY": False, "network_requests": 0, "new_model_fits": 0}
    mapping = [{"course_raw": c, "stored_rows": count, "slot_1based": i + 1, "saved_column": t,
                "parser_assumed_furlongs": [6, 5, 4, 3, 1][i], "verified_furlongs": None,
                "distance_mapping_verified": False, "formal_heading_source": None,
                "raw_original_race_matches": 0, "missingness_default": "UNKNOWN",
                "reason": "no course-specific provider heading/original row evidence; value magnitude not used"}
               for c, count in courses for i, t in enumerate(TIME_FIELDS)]
    files = []
    for path, raw in sources.items():
        dest = root / "provenance/training-parser-sources" / REVISION / path
        exclusive(dest, raw); files.append(dest)
    for i, case in enumerate(cases):
        dest = root / "provenance/training-parser-sources/synthetic" / f"{i:02d}.html"
        exclusive(dest, case["html"].encode()); files.append(dest)
    payloads = [json_bytes(audit), json_bytes(inventory),
                csv_bytes(raw_rows, ["source_kind", "source", "object_id", "bytes", "sha256", "inspected", "has_oikiri_table", "has_training_time_data", "training_original_verified", "race_id", "horse_number", "training_date", "course", "html_headers", "displayed_times", "stored_times", "row_match_verified", "reason"]),
                csv_bytes(mapping, ["course_raw", "stored_rows", "slot_1based", "saved_column", "parser_assumed_furlongs", "verified_furlongs", "distance_mapping_verified", "formal_heading_source", "raw_original_race_matches", "missingness_default", "reason"])]
    for name, raw in zip(names, payloads):
        dest = root / name; exclusive(dest, raw); files.append(dest)
    if sha(args.snapshot.read_bytes()) != snapshot_sha:
        raise ValueError("immutable snapshot changed during audit")
    exclusive(root / "provenance/training-parser-source-manifest.json", json_bytes({
        "files": {str(p.relative_to(root)): {"sha256": sha(p.read_bytes()), "bytes": p.stat().st_size} for p in files},
        "script_sha256": sha(Path(__file__).read_bytes()), "snapshot_sha256": snapshot_sha,
        "network_requests": 0, "existing_files_overwritten": 0, "training_runs": 0}))
    print(json.dumps({"output": str(root), "scoped_training_original_candidates": html["candidate_count"],
                      "synthetic_cases": len(cases), "courses": len(courses), "distance_mapping_verified": False}))


if __name__ == "__main__":
    main()
