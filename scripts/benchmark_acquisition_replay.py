#!/usr/bin/env python3
"""Compare acquisition implementations using identical, frozen saved HTML.

No live request is possible: the workers use a replay session and block socket
connections. Source databases are opened read-only; all caches belong to a
temporary sandbox. The measured seconds are LOCAL REPLAY wall time, excluding
network latency and rate sleeps. Request counts are replay work, not live HTTP.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import cProfile
import faulthandler
import hashlib
import importlib
import json
import logging
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import types
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def compare_stages(runs: list[dict[str, Any]], stages: list[str]) -> list[dict[str, Any]]:
    comparisons = []
    for stage in stages:
        before = [run for run in runs if run["stage"] == stage and run["implementation"] == "baseline"]
        after = [run for run in runs if run["stage"] == stage and run["implementation"] == "optimized"]
        if not before or not after:
            continue
        matching = all(run["payload_sha256"] == before[0]["payload_sha256"] and
                       run["quality_sha256"] == before[0]["quality_sha256"] and
                       run["training_eligibility"] == before[0]["training_eligibility"] for run in before + after)
        b = statistics.median(run["local_replay_wall_sec"] for run in before)
        a = statistics.median(run["local_replay_wall_sec"] for run in after)
        comparisons.append({"stage": stage, "identical_all_fields_quality_and_training": matching,
                            "baseline_median_local_sec": b, "optimized_median_local_sec": a,
                            "local_replay_speedup": b / a if matching and a else None,
                            "local_replay_time_reduction_pct": 100 * (1 - a / b) if matching and b else None})
    return comparisons


def combine_reference_reports(current: dict[str, Any], reference: dict[str, Any],
                              current_path: str, reference_path: str) -> dict[str, Any]:
    """Compare a fresh optimized-only replay with explicitly separate baseline runs."""
    if current["corpus"] != reference["corpus"] or current["workload"] != reference["workload"]:
        raise ValueError("Reference comparison requires the exact frozen corpus and workload")
    if any(report.get("profiling_enabled") or report.get("external_http_requests") != 0
           for report in (current, reference)):
        raise ValueError("Reference comparison requires unprofiled offline replay")
    if not current["runs"] or any(run["implementation"] != "optimized" for run in current["runs"]):
        raise ValueError("Current report must contain optimized-only runs")
    baseline = [{**run, "raw_report": reference_path} for run in reference["runs"] if run["implementation"] == "baseline"]
    optimized = [{**run, "raw_report": current_path} for run in current["runs"]]
    if Counter(run["stage"] for run in baseline) != Counter(run["stage"] for run in optimized):
        raise ValueError("Reference and current stages/repetition counts differ")
    runs = baseline + optimized
    hashes = {name: {run["package_sha256"] for run in runs if run["implementation"] == name}
              for name in ("baseline", "optimized")}
    if any(len(versions) != 1 for versions in hashes.values()):
        raise ValueError("Implementation source changed within reference/current runs")
    for run in runs:
        if run.get("external_http_requests") != 0:
            raise ValueError("Live traffic in a replay run")
    stages = list(dict.fromkeys(run["stage"] for run in optimized))
    return {**current,
            "comparison_sampling": "Non-interleaved: baseline from the earlier reference run; optimized remeasured after a safety fix. Machine load/timing can differ between runs.",
            "raw_reports": {"baseline": reference_path, "optimized": current_path},
            "reference_baseline_scratch": reference.get("scratch"),
            "implementation_sha256": {name: next(iter(versions)) for name, versions in hashes.items()},
            "limitations": [*current.get("limitations", []), "Baseline and optimized were measured in separate runs, not interleaved; no claim of identical machine load."],
            "comparisons": compare_stages(runs, stages), "runs": runs}


def readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def freeze_corpus(cache_path: Path, pedigree_path: Path, result_path: Path,
                  destination: Path, limit: int) -> dict[str, Any]:
    """Select real historical JRA pages, preferring repeated horses across races."""
    with readonly(cache_path) as source, readonly(pedigree_path) as ped, readonly(result_path) as results:
        urls = {row[0] for row in source.execute("SELECT normalized_url FROM http_cache")}
        failures = {row[0]: (int(row[1]), float(row[2])) for row in source.execute(
            "SELECT normalized_url,http_status,updated_at FROM fetch_resume WHERE status='error' AND http_status IN (400,404,410) ORDER BY updated_at")}
        pedigrees = {str(row[0]): row for row in ped.execute(
            "SELECT horse_id,sire,dam,damsire FROM pedigree_cache WHERE sire IS NOT NULL AND sire != ''")}
        candidates = []
        for url, body in source.execute(
                "SELECT normalized_url,body FROM http_cache WHERE normalized_url LIKE ? ORDER BY normalized_url",
                ("https://db.sp.netkeiba.com/race/%",)):
            match = re.fullmatch(r"https://db\.sp\.netkeiba\.com/race/(\d{12})/", url)
            if not match:
                continue
            rid = match[1]
            if not ("2016" <= rid[:4] <= "2025") or not 1 <= int(rid[4:6]) <= 10:
                continue
            horses = set(x.decode() for x in re.findall(rb"/horse/(?:result/)?(\d{10})", body))
            if not horses or b"ResultsByRaceDetail" not in body:
                continue
            # The existing mobile parser does not yet recognize the source's
            # abbreviated obstacle label. Keep this performance test scoped to
            # supported flat races instead of changing parser semantics.
            if re.search(rb"\xe9\x9a\x9c[0-9]{3,4}m", body):
                continue
            available = all(
                any(f"https://{host}/horse/result/{hid}/" in urls
                    for host in ("db.netkeiba.com", "db.sp.netkeiba.com"))
                and (hid in pedigrees or f"https://db.netkeiba.com/horse/ped/{hid}/" in urls
                     or f"https://db.netkeiba.com/horse/ped/{hid}/" in failures)
                for hid in horses)
            if not available:
                continue
            date_row = results.execute("SELECT json_extract(data,'$.date') FROM races_ultimate WHERE race_id=?", (rid,)).fetchone()
            if not date_row:
                continue
            candidates.append({"race_id": rid, "url": url, "horse_ids": sorted(horses),
                               "date": re.sub(r"\D", "", str(date_row[0]))[:8]})
        selected = []
        seen: set[str] = set()
        # Represent both available historical years before favouring reuse.
        for year in sorted({item["race_id"][:4] for item in candidates}):
            first = next(item for item in candidates if item["race_id"].startswith(year))
            selected.append(first)
            seen.update(first["horse_ids"])
            candidates.remove(first)
        while candidates and len(selected) < limit:
            best = max(candidates, key=lambda item: (len(seen.intersection(item["horse_ids"])), item["race_id"]))
            selected.append(best)
            seen.update(best["horse_ids"])
            candidates.remove(best)
        selected = selected[:limit]
        if len(selected) < limit:
            raise RuntimeError(f"Only {len(selected)} complete saved race inputs; requested {limit}")
        selected.sort(key=lambda item: (item["date"], item["race_id"]))
        seen = {hid for item in selected for hid in item["horse_ids"]}
        needed = {item["url"] for item in selected}
        for url in urls | set(failures):
            if any(f"/{hid}/" in url for hid in seen) and "/horse/" in url:
                needed.add(url)
        destination.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(destination / "corpus.db") as frozen:
            frozen.execute("CREATE TABLE pages(url TEXT PRIMARY KEY, final_url TEXT, status INTEGER, headers TEXT, body BLOB, fetched_at REAL, expires_at REAL)")
            for url in sorted(needed):
                row = source.execute("SELECT normalized_url,final_url,status,headers_json,body,fetched_at,expires_at FROM http_cache WHERE normalized_url=?", (url,)).fetchone()
                if row is None:
                    status, observed_at = failures[url]
                    # Error bodies were not persisted and are not consumed by
                    # the parser for these HTTP statuses. Preserve the observed
                    # status, never invent a successful data-bearing response.
                    row = (url, url, status, "{}", b"", observed_at, observed_at)
                frozen.execute("INSERT INTO pages VALUES(?,?,?,?,?,?,?)", row)
            frozen.execute("CREATE TABLE pedigree(horse_id TEXT PRIMARY KEY,sire TEXT,dam TEXT,damsire TEXT)")
            frozen.executemany("INSERT INTO pedigree VALUES(?,?,?,?)", [pedigrees[hid] for hid in sorted(seen) if hid in pedigrees])
            hashes = [(row[0], row[1], hashlib.sha256(row[2]).hexdigest()) for row in frozen.execute("SELECT url,status,body FROM pages ORDER BY url")]
        manifest = {
            "race_inputs": selected, "pages": len(needed), "unique_horses": len(seen),
            "horse_appearances": sum(len(item["horse_ids"]) for item in selected),
            "repeated_appearances": sum(len(item["horse_ids"]) for item in selected) - len(seen),
            "input_sha256": digest(hashes), "seeded_pedigree_records": sum(hid in pedigrees for hid in seen),
            "recorded_error_responses_without_bodies": len(needed - urls),
            "corpus_selection": "Historical JRA saved mobile pages; complete replay dependencies; intentionally includes repeated horses",
        }
        (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest


def _block_network(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("Live network is forbidden in acquisition replay")


def freeze_profiles(cache_path: Path, destination: Path, limit: int) -> dict[str, Any]:
    """A separate component workload: complete authentic horse result pages."""
    with readonly(cache_path) as source:
        urls = {row[0] for row in source.execute("SELECT normalized_url FROM http_cache")}
        selected = []
        for url, body in source.execute("SELECT normalized_url,body FROM http_cache WHERE normalized_url LIKE ? ORDER BY normalized_url",
                                        ("https://db.netkeiba.com/horse/result/%",)):
            match = re.fullmatch(r"https://db\.netkeiba\.com/horse/result/(\d{10})/", url)
            if not match or b"db_h_race_results" not in body:
                continue
            hid = match[1]
            pedigree_url = f"https://db.netkeiba.com/horse/ped/{hid}/"
            if pedigree_url not in urls:
                continue
            selected.append({"horse_id": hid, "url": url, "pedigree_url": pedigree_url})
            if len(selected) == limit:
                break
        if len(selected) != limit:
            raise RuntimeError(f"Only {len(selected)} saved complete-table horse profiles available")
        destination.mkdir(parents=True, exist_ok=True)
        needed = {item[key] for item in selected for key in ("url", "pedigree_url")}
        with sqlite3.connect(destination / "corpus.db") as frozen:
            frozen.execute("CREATE TABLE pages(url TEXT PRIMARY KEY, final_url TEXT, status INTEGER, headers TEXT, body BLOB, fetched_at REAL, expires_at REAL)")
            for url in sorted(needed):
                row = source.execute("SELECT normalized_url,final_url,status,headers_json,body,fetched_at,expires_at FROM http_cache WHERE normalized_url=?", (url,)).fetchone()
                frozen.execute("INSERT INTO pages VALUES(?,?,?,?,?,?,?)", row)
            frozen.execute("CREATE TABLE pedigree(horse_id TEXT PRIMARY KEY,sire TEXT,dam TEXT,damsire TEXT)")
            hashes = [(row[0], row[1], hashlib.sha256(row[2]).hexdigest()) for row in frozen.execute("SELECT url,status,body FROM pages ORDER BY url")]
        manifest = {"profile_inputs": selected, "pages": len(needed), "unique_horses": len(selected),
                    "horse_appearances": 3 * len(selected), "repeated_appearances": 2 * len(selected),
                    "input_sha256": digest(hashes), "seeded_pedigree_records": 0,
                    "corpus_selection": "Saved complete-table horse profiles with saved pedigree HTML; three identical passes to represent reuse across races"}
        (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest


class Response:
    def __init__(self, url: str, status: int, headers: dict[str, str], body: bytes):
        self.url, self.status, self.headers, self.body = url, status, headers, body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def read(self):
        return self.body


class ReplaySession:
    def __init__(self, pages: dict[str, tuple]):
        self.pages = pages
        self.requests: Counter[str] = Counter()
        self.synthetic_400 = 0
        self.unresolved: set[str] = set()

    def get(self, url: str, **kwargs):
        self.requests[url] += 1
        if url in self.pages:
            final, status, headers, body, *_ = self.pages[url]
            return Response(final, status, json.loads(headers or "{}"), body)
        mobile = url.replace("db.netkeiba.com", "db.sp.netkeiba.com", 1)
        if mobile != url and mobile in self.pages:
            # The production cache persists successes only. The known desktop
            # compatibility failure is therefore a declared synthetic input.
            self.synthetic_400 += 1
            return Response(url, 400, {}, b"Replay desktop compatibility 400")
        self.unresolved.add(url)
        raise AssertionError(f"Missing frozen response: {url}")


def summarize_payloads(payloads: list[dict], quality_module) -> dict[str, Any]:
    horses = [horse for payload in payloads for horse in payload.get("horses", [])]
    fields = sorted({field for horse in horses for field in horse})
    missing = {field: sum(horse.get(field) is None or horse.get(field) == "" for horse in horses) for field in fields}
    quality = [quality_module.classify_race_quality(payload).as_dict() for payload in payloads]
    from training.eligibility import select_training_eligible_rows
    sys.path.insert(0, str(ROOT / "keiba"))
    from keiba_ai.feature_engineering import parse_race_time_to_seconds
    import pandas as pd
    # Match the canonical DB loader's authoritative date and parsed-time
    # aliases before applying the unchanged eligibility policy.
    rows = [{**payload["race_info"], **horse, "race_date": payload["race_info"].get("date"),
             "time_seconds": parse_race_time_to_seconds(horse.get("finish_time"))}
            for payload in payloads for horse in payload["horses"]]
    quality_states = {item["race_id"]: (item["lifecycle"], "complete" if item["valid_for_date_completion"] else "incomplete", False) for item in quality}
    selection = select_training_eligible_rows(pd.DataFrame(rows), target="speed_deviation", recorded_quality_states=quality_states)
    return {"payload_sha256": digest(payloads), "horse_count": len(horses), "field_count": len(fields),
            "missing_counts": missing, "quality": quality, "quality_sha256": digest(quality),
            "training_eligibility": selection.manifest.as_dict()}


async def worker_async(args) -> dict[str, Any]:
    sandbox = Path(args.sandbox)
    corpus = Path(args.corpus)
    source = Path(args.package)
    package_root = sandbox / "python-api"
    shutil.copytree(source, package_root / "scraping")
    sys.path.insert(0, str(ROOT / "python-api"))
    sys.path.insert(0, str(package_root))
    # Imported scraper modules need only a logger. Loading the live app config
    # would initialize logs, model paths and optional external clients.
    app_config = types.ModuleType("app_config")
    app_config.logger = logging.getLogger("replay")
    app_config.logger.addHandler(logging.NullHandler())
    app_config.logger.setLevel(logging.CRITICAL)
    sys.modules["app_config"] = app_config
    with readonly(corpus / "corpus.db") as conn:
        pages = {row[0]: row[1:] for row in conn.execute("SELECT * FROM pages")}
        pedigree = list(conn.execute("SELECT * FROM pedigree"))
    conn.close()
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    import scraping.fetch_pipeline as pipeline
    import scraping.horse as horse
    import scraping.mobile_race as mobile_race
    import scraping.quality as quality
    pipeline._CACHE_DB_PATH = sandbox / "http.db"
    horse._PEDIGREE_DB_PATH = sandbox / "pedigree.db"
    horse._init_pedigree_table()
    with sqlite3.connect(horse._PEDIGREE_DB_PATH) as conn:
        conn.executemany("INSERT OR REPLACE INTO pedigree_cache(horse_id,sire,dam,damsire) VALUES(?,?,?,?)", pedigree)
    conn.close()
    pipeline._init_cache_db()
    if args.stage == "warm_http":
        now = time.time()
        with sqlite3.connect(pipeline._CACHE_DB_PATH) as conn:
            conn.executemany("INSERT INTO http_cache VALUES(?,?,?,?,?,?,?)",
                             [(url, data[0], data[1], data[2], data[3], now, now + 86400 * 30) for url, data in pages.items() if data[1] == 200])
        conn.close()
    virtual = {"now": time.time(), "sleep_requested_sec": 0.0}
    actual_sleep = asyncio.sleep

    async def fake_sleep(seconds, result=None):
        virtual["now"] += max(0.0, float(seconds))
        virtual["sleep_requested_sec"] += max(0.0, float(seconds))
        return await actual_sleep(0, result)

    async def no_legacy_rate(*args, **kwargs):
        return None

    asyncio.sleep = fake_sleep
    pipeline._respect_rate_limit = no_legacy_rate
    # The new controller's interval waits are simulated. Legacy waits are
    # removed too: wall-time comparisons represent parsing/cache work only.
    pacer = getattr(pipeline, "_REQUEST_PACER", None)
    if pacer is not None:
        pipeline._REQUEST_PACER = type(pacer)(sandbox / "pacer.db", clock=lambda: virtual["now"], sleep=fake_sleep)
    try:
        parsed_cache = importlib.import_module("scraping.parsed_cache")
    except ModuleNotFoundError:
        parsed_cache = None
    if parsed_cache is not None:
        parsed_cache._CACHE_PATH = sandbox / "parsed.db"
    socket.socket.connect = _block_network
    socket.socket.connect_ex = _block_network
    socket.create_connection = _block_network
    session = ReplaySession(pages)
    parse_counts: Counter[str] = Counter()
    parse_seconds: Counter[str] = Counter()
    parse_metrics_lock = threading.Lock()
    for module in (horse, mobile_race):
        original = module.BeautifulSoup

        def traced_soup(*pos, _original=original, _name=module.__name__, **kw):
            start = time.perf_counter()
            try:
                return _original(*pos, **kw)
            finally:
                elapsed_parse = time.perf_counter() - start
                with parse_metrics_lock:
                    parse_counts[_name] += 1
                    parse_seconds[_name] += elapsed_parse
        module.BeautifulSoup = traced_soup

    async def one_pass():
        output = []
        if args.workload == "profiles":
            for _ in range(3):
                for item in manifest["profile_inputs"]:
                    detail = await horse.scrape_horse_detail(session, item["horse_id"], quick_mode=False)
                    output.append({"horse_id": item["horse_id"], "detail": detail})
            return output
        for item in manifest["race_inputs"]:
            fetched, html = await pipeline.fetch_text(session, item["url"], cache_ttl_sec=43200, min_interval_sec=1)
            assert fetched.status == 200
            payload = await mobile_race.parse_mobile_race(session, item["race_id"], html,
                date_hint=item["date"], quick_mode=True, horse_detail_fetcher=horse.scrape_horse_detail)
            if not payload:
                raise AssertionError(f"Race parser rejected {item['race_id']}")
            output.append(payload)
        return output

    if args.stage == "warm_repeat":
        await one_pass()
        session.requests.clear()
        session.synthetic_400 = 0
        parse_counts.clear()
        parse_seconds.clear()
        pipeline.get_fetch_metrics(reset=True)
        virtual["sleep_requested_sec"] = 0
    profiler = cProfile.Profile() if args.profile else None
    started = time.perf_counter()
    if profiler:
        profiler.enable()
    payloads = await one_pass()
    if profiler:
        profiler.disable()
        profiler.dump_stats(str(sandbox / "replay.prof"))
    elapsed = time.perf_counter() - started
    if session.unresolved:
        raise AssertionError("Unresolved corpus requests: " + repr(sorted(session.unresolved)))
    if args.workload == "profiles":
        fields = sorted({field for item in payloads for field in item["detail"]})
        missing = {field: sum(item["detail"].get(field) is None or item["detail"].get(field) == "" for item in payloads) for field in fields}
        summary = {"payload_sha256": digest(payloads), "horse_count": len(payloads), "field_count": len(fields),
                   "missing_counts": missing, "quality_sha256": digest(missing),
                   "training_eligibility": {"not_applicable": "Horse profile component, no race result selection"}}
    else:
        summary = summarize_payloads(payloads, quality)
    summary.update({"stage": args.stage, "local_replay_wall_sec": elapsed,
                    "replay_requests": sum(session.requests.values()), "synthetic_compatibility_400": session.synthetic_400,
                    "request_unique_urls": len(session.requests), "html_parse_count": dict(parse_counts),
                    "html_parse_wall_sec": dict(parse_seconds), "fetch_metrics": pipeline.get_fetch_metrics(),
                    "html_parse_instrumentation": "BeautifulSoup constructor calls only; excludes preliminary lxml filtering in the optimized parser. Full pipeline time is included in local_replay_wall_sec.",
                    "simulated_sleep_requested_sec_not_elapsed": virtual["sleep_requested_sec"],
                    "external_http_requests": 0,
                    "package_sha256": digest([(path.name, hashlib.sha256(path.read_bytes()).hexdigest()) for path in sorted((package_root / "scraping").glob("*.py"))])})
    if getattr(pipeline, "_REQUEST_PACER", None) is not None and hasattr(pipeline._REQUEST_PACER, "close"):
        pipeline._REQUEST_PACER.close()
    for module, name in ((parsed_cache, "close_cache_connections"),
                         (pipeline, "close_fetch_cache_connections")):
        close = getattr(module, name, None)
        if callable(close):
            close()
    (sandbox / "payloads.json").write_text(json.dumps(payloads, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-package", type=Path)
    parser.add_argument("--race-count", type=int, default=24)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/generated/acquisition-replay.json")
    parser.add_argument("--current-report", type=Path, help="Combine an optimized-only raw report without rerunning it")
    parser.add_argument("--reference-baseline-report", type=Path, help="Explicit separately measured baseline report")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--package")
    parser.add_argument("--corpus")
    parser.add_argument("--sandbox")
    parser.add_argument("--stage", choices=("cold", "warm_http", "warm_repeat"))
    parser.add_argument("--workload", choices=("races", "profiles"), default="races")
    parser.add_argument("--frozen-corpus", type=Path, help="Reuse an existing read-only corpus directory without consulting app DBs")
    parser.add_argument("--stages", nargs="+", choices=("cold", "warm_http", "warm_repeat"), default=("cold", "warm_http", "warm_repeat"))
    parser.add_argument("--implementation", choices=("baseline", "optimized", "both"), default="both")
    parser.add_argument("--profile", action="store_true", help="Save per-worker cProfile data (diagnostic timings are not benchmark medians)")
    args = parser.parse_args()
    if args.current_report or args.reference_baseline_report:
        if not args.current_report or not args.reference_baseline_report:
            parser.error("Both --current-report and --reference-baseline-report are required")
        report = combine_reference_reports(
            json.loads(args.current_report.read_text(encoding="utf-8")),
            json.loads(args.reference_baseline_report.read_text(encoding="utf-8")),
            str(args.current_report.resolve()), str(args.reference_baseline_report.resolve()))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"output": str(args.output), "comparisons": report["comparisons"]}, ensure_ascii=False, indent=2))
        if not all(item["identical_all_fields_quality_and_training"] for item in report["comparisons"]):
            raise SystemExit("Output mismatch: no speedup claim is valid")
        return
    if args.worker:
        faulthandler.dump_traceback_later(90, repeat=True)
        result = asyncio.run(worker_async(args))
        faulthandler.cancel_dump_traceback_later()
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    if not args.baseline_package or not args.baseline_package.is_dir():
        parser.error("--baseline-package must point to the preserved pre-change scraping package")
    work = Path(tempfile.mkdtemp(prefix="keiba-acquisition-replay-"))
    if args.frozen_corpus:
        corpus_dir = args.frozen_corpus.resolve()
        manifest = json.loads((corpus_dir / "manifest.json").read_text(encoding="utf-8"))
        if not (corpus_dir / "corpus.db").is_file():
            parser.error("Frozen corpus database is missing")
        if (args.workload == "profiles") != ("profile_inputs" in manifest):
            parser.error("Frozen corpus does not match requested workload")
    elif args.workload == "profiles":
        manifest = freeze_profiles(ROOT / "keiba/data/fetch_cache.db", work / "corpus", args.race_count)
        corpus_dir = work / "corpus"
    else:
        manifest = freeze_corpus(ROOT / "keiba/data/fetch_cache.db", ROOT / "keiba/data/pedigree_cache.db",
                                 ROOT / "keiba/data/keiba_ultimate.db", work / "corpus", args.race_count)
        corpus_dir = work / "corpus"
    runs = []
    # Alternate implementations to reduce order-dependent machine warmup bias.
    for repeat in range(args.repeats):
        for stage in args.stages:
            pairs = [("baseline", args.baseline_package), ("optimized", ROOT / "python-api/scraping")]
            if args.implementation != "both":
                pairs = [pair for pair in pairs if pair[0] == args.implementation]
            if repeat % 2:
                pairs.reverse()
            for name, package in pairs:
                sandbox = work / f"{name}-{stage}-{repeat}"
                sandbox.mkdir()
                output = sandbox / "result.json"
                command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--package", str(package),
                           "--corpus", str(corpus_dir), "--sandbox", str(sandbox), "--stage", stage,
                           "--output", str(output), "--workload", args.workload]
                if args.profile:
                    command.append("--profile")
                subprocess.run(command, check=True, cwd=ROOT)
                result = json.loads(output.read_text(encoding="utf-8"))
                result.update({"implementation": name, "repeat": repeat})
                runs.append(result)
                print(f"{name} {stage} {repeat}: local={result['local_replay_wall_sec']:.3f}s replay_requests={result['replay_requests']}", flush=True)
    # A median of different implementations is not a valid before/after test.
    package_versions = {name: sorted({run["package_sha256"] for run in runs if run["implementation"] == name})
                        for name in {run["implementation"] for run in runs}}
    if any(len(hashes) != 1 for hashes in package_versions.values()):
        raise SystemExit("Scraping source changed during benchmark; repeat with frozen implementations")
    comparisons = compare_stages(runs, args.stages)
    report = {"schema_version": 1, "mode": "offline-saved-html-replay", "workload": args.workload, "corpus": manifest,
              "profiling_enabled": args.profile, "frozen_corpus_path": str(corpus_dir),
              "external_http_requests": 0, "baseline_package": str(args.baseline_package), "scratch": str(work),
              "limitations": ["Local wall time excludes network latency and all rate/wait delays; not a live acquisition speedup.",
                  "html_parse_count/html_parse_wall_sec instrument BeautifulSoup constructors only, not preliminary lxml filtering; local_replay_wall_sec includes the full pipeline.",
                  "Observed 400/404/410 statuses are frozen from fetch_resume when success HTML is absent; unavailable error bodies are empty, as parsers do not consume them.",
                  "Desktop 400 compatibility replies are synthetic only where an exact saved mobile response exists.",
                  "Existing pedigree records seeded identically; historical JRA corpus selected for repeated horses, not representative of all 2016-2026 races.",
                  "Same parsed fields and training eligibility do not establish model accuracy or historical feature leakage correctness.",
                  "Race workload starts at the saved canonical mobile race URL and excludes race discovery and desktop race fallback.",
                  "No durable production save, server-side monthly orchestration, or end-to-end browser timing in this benchmark."],
              "implementation_sha256": {name: hashes[0] for name, hashes in package_versions.items()},
              "comparisons": comparisons, "runs": runs}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "scratch": str(work), "comparisons": comparisons}, ensure_ascii=False, indent=2))
    if not all(item["identical_all_fields_quality_and_training"] for item in comparisons):
        raise SystemExit("Output mismatch: no speedup claim is valid")


if __name__ == "__main__":
    main()
