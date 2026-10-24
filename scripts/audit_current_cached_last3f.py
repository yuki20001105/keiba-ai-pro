"""Read-only, bounded source evidence; never fetch or repair production data."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup


def last3f_column(headers: list[str]) -> int:
    # An "上がり指数" column is NOT the final 3F clock.
    accepted = {"上り", "上がり", "上り3F", "上がり3F"}
    hits = [i for i, h in enumerate(headers) if re.sub(r"\s+", "", h) in accepted]
    if len(hits) != 1:
        raise ValueError(f"Ambiguous/missing clock header: {hits}")
    return hits[0]


def positive_number(value: object) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-dir", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    snapshot = args.audit_dir / "snapshot.sqlite3"
    cache_path = root / "keiba/data/fetch_cache.db"
    assert last3f_column(["上 が り 指 数", "通過", "上り"]) == 2
    assert positive_number("38.9") and not positive_number("**")
    examples = []
    with sqlite3.connect(snapshot.resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        with sqlite3.connect(cache_path.as_uri() + "?mode=ro", uri=True) as cache:
            for race_id in [f"2020060101{i:02d}" for i in range(1, 6)]:
                url = f"https://db.netkeiba.com/race/{race_id}/"
                row = cache.execute(
                    "SELECT status, body, fetched_at FROM http_cache WHERE normalized_url=?", (url,)
                ).fetchone()
                if row is None:
                    examples.append({"race_id": race_id, "cached": False})
                    continue
                body = bytes(row[1])
                match = re.search(br'charset\s*=\s*["\x27]?([a-zA-Z0-9_-]+)', body[:8192], re.I)
                encoding = match.group(1).decode("ascii") if match else "utf-8"
                soup = BeautifulSoup(body.decode(encoding, errors="strict"), "lxml")
                table = soup.select_one("table.race_table_01")
                if table is None:
                    raise ValueError(f"Result table missing: {race_id}")
                rows = table.find_all("tr")
                headers = [x.get_text(" ", strip=True) for x in rows[0].find_all(["th", "td"])]
                column = last3f_column(headers)
                stored = {}
                for (raw,) in db.execute("SELECT data FROM race_results_ultimate WHERE race_id=?", (race_id,)):
                    item = json.loads(raw)
                    key = int(item["horse_number"])
                    if key in stored:
                        raise ValueError(f"Duplicate runner: {race_id}/{key}")
                    stored[key] = item
                counts = {
                    "html_rows": 0, "html_positive_clock": 0,
                    "html_masked_clock": 0, "matched_runners": 0,
                    "stored_positive_clock": 0, "stored_masked_clock": 0,
                    "numeric_html_but_missing_or_masked_in_db": 0,
                    "both_numeric": 0, "both_numeric_different": 0,
                }
                for tr in rows[1:]:
                    cells = tr.find_all("td")
                    if len(cells) <= column:
                        continue
                    horse_number = cells[2].get_text(strip=True)
                    if not horse_number.isdigit():
                        continue
                    counts["html_rows"] += 1
                    clock = cells[column].get_text(strip=True)
                    numeric = positive_number(clock)
                    counts["html_positive_clock"] += int(numeric)
                    counts["html_masked_clock"] += int("**" in clock)
                    item = stored.get(int(horse_number))
                    if item is None:
                        continue
                    counts["matched_runners"] += 1
                    saved = item.get("last_3f_time", item.get("last_3f"))
                    saved_numeric = positive_number(saved)
                    counts["stored_positive_clock"] += int(saved_numeric)
                    counts["stored_masked_clock"] += int("**" in str(saved))
                    counts["numeric_html_but_missing_or_masked_in_db"] += int(numeric and not saved_numeric)
                    if numeric and saved_numeric:
                        counts["both_numeric"] += 1
                        counts["both_numeric_different"] += int(abs(float(clock) - float(saved)) > 1e-8)
                examples.append({
                    "race_id": race_id, "source_url": url, "cached": True,
                    "status": row[0], "body_sha256": hashlib.sha256(body).hexdigest(),
                    "cache_fetched_at_epoch": row[2], "clock_column": column,
                    "counts": counts,
                })
    report = {
        "schema": "bounded-cached-last3f-evidence-v2",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Five selected already-cached 2020 JRA result pages, NOT a full-cache census",
        "supersedes_interpretation_of": "cache_examples.json (ambiguous 上がり指数 header matched; not the 3F clock)",
        "snapshot": str(snapshot.resolve()),
        "cache_is_separate_live_readonly_store": True,
        "historical_prestart_availability_proven": False,
        "network_requests": 0, "database_writes": 0, "repairs_started": False,
        "examples": examples,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    output = args.audit_dir / "cache_examples_v2.json"
    if output.exists():
        raise FileExistsError(output)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
