#!/usr/bin/env python3
"""Strict offline comparison of saved-horse context off/on on frozen HTML.

No production database is opened. No missing response is synthesized. The
horse-only experiment includes explicitly constructed saved-row fixtures;
the race pipeline replays a bounded real corpus, saves and audits each race.
It does NOT include live latency, list discovery or complete-day acquisition.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import nullcontext
import hashlib
import json
import logging
from pathlib import Path
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import types

ROOT = Path(__file__).resolve().parents[1]
AUDIT_KEY = "_acquisition_reuse"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def implementation_digest():
    # Include every scraper dependency (also lazily imported race helpers),
    # plus this harness. This intentionally covers some unused modules too.
    paths = [*sorted((ROOT / "python-api/scraping").glob("*.py")), Path(__file__).resolve()]
    return digest([(str(path.relative_to(ROOT)), hashlib.sha256(path.read_bytes()).hexdigest())
                   for path in paths])


def provider_fields(value):
    """Exclude only the reserved new audit key; retain every provider field."""
    if isinstance(value, dict):
        return {key: provider_fields(item) for key, item in value.items() if key != AUDIT_KEY}
    if isinstance(value, list):
        return [provider_fields(item) for item in value]
    return value


class MissingReplayInput(AssertionError):
    pass


class Response:
    def __init__(self, values):
        self.url, self.status, headers, self.body, *_ = values
        self.headers = json.loads(headers or "{}")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def read(self):
        return self.body


class StrictReplaySession:
    def __init__(self, pages):
        self.pages = pages
        self.requests = Counter()
        self.missing = set()

    def get(self, url, **kwargs):
        self.requests[url] += 1
        if url not in self.pages or self.pages[url] is None:
            self.missing.add(url)
            raise MissingReplayInput(f"No exact recorded response: {url}")
        return Response(self.pages[url])


class NoDelayPacer:
    async def acquire(self, *args):
        return "offline", 0.0

    async def heartbeat(self, *args):
        await asyncio.Event().wait()

    def observe(self, *args, **kwargs):
        pass

    def release(self, *args):
        pass

    def status(self, *args):
        return {}

    async def sleep(self, seconds):
        await asyncio.sleep(0)


def block_network(*args, **kwargs):
    raise AssertionError("External network forbidden in saved-horse benchmark")


def fixture_seed(payloads, scenario):
    """A declared fixture, not a claim these rows existed in the user's DB."""
    if scenario == "missing":
        return []
    horses = []
    for item in payloads:
        horse = {"horse_id": item["horse_id"], **provider_fields(item["detail"]),
                 "race_id": "202406010101"}
        if scenario == "ambiguous":
            horse["sire"] = str(horse.get("sire", "")) + " (unverified alias)"
        horses.append(horse)
    return [{"race_info": {"race_id": "202406010101", "date": "2024/12/01",
                            "distance": 1600, "venue": "中山", "track_type": "芝"},
             "horses": horses, "return_tables": []}]


def compare(runs):
    baseline = [run for run in runs if not run["enabled"]]
    optimized = [run for run in runs if run["enabled"]]
    keys = ("provider_sha256", "missing_counts", "quality_sha256", "saved_provider_sha256",
            "input_sha256", "seed_sha256", "implementation_sha256")
    matches = bool(baseline and optimized) and all(
        all(run[key] == baseline[0][key] for key in keys) for run in runs
    )
    before = statistics.median(run["local_replay_wall_sec"] for run in baseline)
    after = statistics.median(run["local_replay_wall_sec"] for run in optimized)
    return {"all_provider_fields_missingness_quality_equal": matches,
            "baseline_median_local_sec": before, "optimized_median_local_sec": after,
            "local_replay_speedup": before / after if matches and after else None,
            "baseline_replay_requests": [run["replay_requests"] for run in baseline],
            "optimized_replay_requests": [run["replay_requests"] for run in optimized]}


async def worker(args):
    sandbox, corpus = Path(args.sandbox), Path(args.corpus)
    sandbox.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT / "python-api"))
    # Avoid importing live application configuration or constructing clients.
    config = types.ModuleType("app_config")
    config.logger = logging.getLogger("saved-horse-offline")
    config.logger.addHandler(logging.NullHandler())
    config.logger.setLevel(logging.CRITICAL)
    sys.modules["app_config"] = config
    source_hash = implementation_digest()
    from scraping import fetch_pipeline as pipeline, horse, mobile_race, parsed_cache, quality, storage
    from scraping.saved_horse_reuse import acquisition_horse_reuse

    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    key = "profile_inputs" if args.workload == "horses" else "race_inputs"
    selected = manifest[key][:args.limit]
    horse_ids = {item["horse_id"] for item in selected} if args.workload == "horses" else {
        horse_id for item in selected for horse_id in item["horse_ids"]
    }
    with sqlite3.connect((corpus / "corpus.db").resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        # Frozen corpus only. Read its URL index first, then selected bodies.
        urls = [row[0] for row in conn.execute("SELECT url FROM pages")]
        needed = {item["url"] for item in selected}
        needed.update(url for url in urls if "/horse/" in url and any(f"/{hid}/" in url for hid in horse_ids))
        pages = {url: conn.execute("SELECT final_url,status,headers,body,fetched_at,expires_at FROM pages WHERE url=?",
                                  (url,)).fetchone() for url in sorted(needed)}
        if any(row is None for row in pages.values()):
            raise MissingReplayInput("Frozen manifest points to a missing source page")
        pedigree = [row for row in conn.execute("SELECT * FROM pedigree") if row[0] in horse_ids]
    pipeline._CACHE_DB_PATH = sandbox / "http.db"
    pipeline._REQUEST_PACER = NoDelayPacer()
    parsed_cache._CACHE_PATH = sandbox / "parsed.db"
    horse._PEDIGREE_DB_PATH = sandbox / "pedigree.db"
    horse._init_pedigree_table()
    with sqlite3.connect(horse._PEDIGREE_DB_PATH) as conn:
        conn.executemany("INSERT INTO pedigree_cache(horse_id,sire,dam,damsire) VALUES(?,?,?,?)", pedigree)
    # Race corpus has verified mobile profiles. Both arms start with exactly
    # the same mobile preference, avoiding synthetic desktop failure inputs.
    if args.workload == "race_pipeline":
        parsed_cache.remember_mobile_endpoint("horse-result", valid=True)
    db_path = sandbox / "saved.db"
    storage._init_sqlite_db(db_path)
    quality.init_acquisition_quality_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE INDEX horse_identity ON race_results_ultimate(json_extract(data,'$.horse_id'))")
        conn.execute("CREATE INDEX race_identity ON race_results_ultimate(race_id)")
    seed = json.loads(Path(args.seed).read_text(encoding="utf-8")) if args.seed else []
    for payload in seed:
        if not storage._save_race_sqlite_only(payload, db_path):
            raise AssertionError("Seed persistence failed")
    socket.socket.connect = block_network
    socket.socket.connect_ex = block_network
    socket.create_connection = block_network
    session = StrictReplaySession(pages)
    payloads, audits = [], []
    started = time.perf_counter()
    with pipeline.fetch_context(job_id="offline-benchmark"):
        for item in selected:
            target_date = "20250101" if args.workload == "horses" else item["date"]
            context = acquisition_horse_reuse(db_path, target_date) if args.enabled else nullcontext()
            with context:
                if args.workload == "horses":
                    detail = await horse.scrape_horse_detail(session, item["horse_id"], quick_mode=False)
                    payloads.append({"horse_id": item["horse_id"], "detail": detail})
                else:
                    fetched, html = await pipeline.fetch_text(session, item["url"])
                    if fetched.status != 200:
                        raise AssertionError("Recorded race is unavailable")
                    payload = await mobile_race.parse_mobile_race(
                        session, item["race_id"], html, date_hint=item["date"], quick_mode=True,
                        horse_detail_fetcher=horse.scrape_horse_detail,
                    )
                    if not payload or not storage._save_race_sqlite_only(payload, db_path):
                        raise AssertionError("Race parse/persistence failed")
                    report = quality.classify_race_quality(payload)
                    quality.record_race_quality(db_path, race_date=item["date"], race_id=item["race_id"],
                                                report=report, race_data=payload)
                    audits.append(report.as_dict())
                    payloads.append(payload)
        elapsed = time.perf_counter() - started
        metrics = pipeline.get_fetch_metrics()
    if session.missing:
        raise MissingReplayInput("Unresolved recorded URLs: " + repr(sorted(session.missing)))
    provider = provider_fields(payloads)
    details = [item["detail"] for item in provider] if args.workload == "horses" else [
        detail for payload in provider for detail in payload["horses"]
    ]
    fields = sorted({key for item in details for key in item})
    missing = {key: sum(item.get(key) is None or item.get(key) == "" for item in details) for key in fields}
    with sqlite3.connect(db_path) as conn:
        saved = [provider_fields(json.loads(row[0])) for row in conn.execute(
            "SELECT data FROM race_results_ultimate ORDER BY race_id,id")]
    result = {
        "enabled": args.enabled, "workload": args.workload,
        "local_replay_wall_sec": elapsed, "replay_requests": sum(session.requests.values()),
        "external_http_requests": 0, "synthetic_responses": 0,
        "recorded_error_status_requests_with_unavailable_empty_body": sum(
            session.requests[url] for url, row in pages.items() if row[1] >= 400 and not row[3]
        ),
        "input_sha256": digest([(url, row[1], hashlib.sha256(row[3]).hexdigest()) for url, row in pages.items()]),
        "seed_sha256": digest(seed), "provider_sha256": digest(provider),
        "saved_provider_sha256": digest(saved), "quality_sha256": digest(audits),
        "quality": audits, "missing_counts": missing, "fetch_metrics": metrics,
        "payloads": payloads,
        "implementation_sha256": source_hash,
    }
    if implementation_digest() != source_hash:
        raise AssertionError("Measured implementation changed during replay; rerun after edits finish")
    pipeline.close_fetch_cache_connections()
    parsed_cache.close_cache_connections()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-corpus", type=Path)
    parser.add_argument("--race-corpus", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--sandbox", type=Path)
    parser.add_argument("--seed", type=Path)
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--workload", choices=("horses", "race_pipeline"), default="horses")
    parser.add_argument("--enabled", action="store_true")
    args = parser.parse_args()
    if args.worker:
        args.output.write_text(json.dumps(asyncio.run(worker(args)), ensure_ascii=False, indent=2), encoding="utf-8")
        return
    if not args.profile_corpus or not args.race_corpus:
        parser.error("Both existing frozen corpus directories are required")
    work = Path(tempfile.mkdtemp(prefix="keiba-saved-horse-replay-"))
    serial = 0

    def run(workload, corpus, enabled, seed=None):
        nonlocal serial
        serial += 1
        directory = work / str(serial)
        directory.mkdir()
        output = directory / "result.json"
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--workload", workload,
                   "--corpus", str(corpus), "--sandbox", str(directory), "--output", str(output),
                   "--limit", "6" if workload == "horses" else "4"]
        if enabled:
            command.append("--enabled")
        if seed:
            command.extend(["--seed", str(seed)])
        subprocess.run(command, check=True, timeout=90)
        return json.loads(output.read_text(encoding="utf-8"))

    preparation = {}
    for workload, corpus in (("horses", args.profile_corpus), ("race_pipeline", args.race_corpus)):
        preparation[workload] = run(workload, corpus, False)
    groups = []
    for workload, scenario, corpus in (
        ("horses", "verified", args.profile_corpus),
        ("horses", "ambiguous", args.profile_corpus),
        ("horses", "missing", args.profile_corpus),
        ("race_pipeline", "existing_saved_races", args.race_corpus),
    ):
        seed = (fixture_seed(preparation[workload]["payloads"], scenario) if workload == "horses"
                else provider_fields(preparation[workload]["payloads"]))
        seed_path = work / f"seed-{workload}-{scenario}.json"
        seed_path.write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")
        runs = []
        for repetition in range(args.repeats):
            for enabled in ((False, True) if repetition % 2 == 0 else (True, False)):
                result = run(workload, corpus, enabled, seed_path)
                result.pop("payloads")
                runs.append(result)
        groups.append({"workload": workload, "scenario": scenario,
                       "comparison": compare(runs), "runs": runs})
        print(json.dumps({"workload": workload, "scenario": scenario,
                          "comparison": groups[-1]["comparison"]}, ensure_ascii=False), flush=True)
    race_run = next(group for group in groups if group["workload"] == "race_pipeline")["runs"][0]
    repair_required = sum(item.get("field_states", {}).get("pedigree") == "repair_required"
                          for item in race_run["quality"])
    empty_errors = race_run["recorded_error_status_requests_with_unavailable_empty_body"]
    report = {
        "schema_version": 1, "mode": "strict-offline-frozen-html-replay",
        "external_http_requests": 0, "synthetic_http_responses": 0,
        "scratch": str(work), "profile_corpus": str(args.profile_corpus), "race_corpus": str(args.race_corpus),
        "baseline": "Current code with acquisition_horse_reuse context disabled",
        "optimized": "Same code and identical initial HTTP/pedigree/saved-row seed with context enabled",
        "audit_excluded_from_provider_comparison": [AUDIT_KEY],
        "source_hash_scope": "All python-api/scraping/*.py files and this benchmark script",
        "limitations": [
            "Small intentionally selected corpus: six horse profiles and four race pages; not representative of 2016-2026.",
            "Horse saved-row fixtures are constructed from recorded provider outputs; ambiguous/missing cases are deliberate fixtures.",
            "Race stage includes race HTML, horse enrichment, SQLite save and per-race quality audit; excludes list/calendar discovery, full-day audit, actual network latency and pacing delays.",
            "Race corpus starts with its original dedicated pedigree cache and equal verified mobile endpoint preference in both arms.",
            "Recorded failure statuses are replayed exactly; where the original corpus has no error body it retains the empty body. Missing successes or failures are never invented.",
            f"The selected {len(race_run['quality'])}-race corpus retains pedigree repair_required on {repair_required} races. Its {empty_errors} error-status requests replay recorded historical statuses with unavailable bodies represented as empty bytes, not exact saved error HTML; inspect status counters for the HTTP codes.",
            "Equal quality does not mean complete quality: pre-existing repair_required fields remain visible. Training eligibility is not independently recomputed in this bounded harness.",
            "Only new reserved audit metadata is excluded. No provider fields, missingness or quality differences are ignored.",
            "Local replay speed ratios are not live full-acquisition speedup estimates.",
        ], "groups": groups,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not all(group["comparison"]["all_provider_fields_missingness_quality_equal"] for group in groups):
        raise SystemExit("Provider/quality mismatch: no speedup claim is valid")


if __name__ == "__main__":
    main()
