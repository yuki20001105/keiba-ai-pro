"""Local append-only forward evidence. No race/result DB write or live betting.

Only ``observe_http`` can create a direct-fetch receipt: it invokes a transport
and measures request/receipt times, never re-stamps an imported DB row. The
default transport is an explicit, paced netkeiba GET. Nothing runs on import.
Local hashes detect modification; they are not a third-party timestamp service.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import re
import uuid
from urllib.parse import parse_qs, urlsplit

UTC = timezone.utc
JST = timezone(timedelta(hours=9))
REPO = Path(__file__).resolve().parents[2]
DIGEST = re.compile(r"^[0-9a-f]{64}$")
POLICY = {"cutoff_seconds": 300, "capture_window_seconds": 30, "max_source_age_seconds": 120}
ARTIFACTS = {"model", "calibrator", "features", "rule"}
FORBIDDEN_FEATURES = {"safe_finish", "safe_time_seconds", "safe_odds", "safe_popularity", "safe_tansho_payout",
                      "safe_payout", "safe_speed_deviation", "safe_last3f_time", "safe_margin", "safe_prize"}


class CaptureError(ValueError):
    pass


def canonical_bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (ValueError, TypeError) as exc:
        raise CaptureError("noncanonical-json") from exc


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _time(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(UTC)
    except (ValueError, TypeError) as exc:
        raise CaptureError("timezone-aware-time-required") from exc


def _stamp(value):
    return _time(value).isoformat().replace("+00:00", "Z")


def _now(clock):
    return _time(clock() if clock else datetime.now(UTC))


def _root(path):
    path = Path(path).resolve()
    for protected in (REPO / "keiba/data", REPO / "python-api/models", REPO / "python-api/data", REPO / "python-api/experiments"):
        if path == protected or path.is_relative_to(protected):
            raise CaptureError("store-must-be-isolated")
    return path


def _exclusive(path, body):
    """Publish a complete file without overwrite; matching retries are allowed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".pending-" + uuid.uuid4().hex)
    try:
        with temporary.open("xb") as target:
            target.write(body)
            target.flush()
            os.fsync(target.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != body:
                raise CaptureError("immutable-record-conflict")
    finally:
        temporary.unlink(missing_ok=True)


def _object(root, body):
    sha = hashlib.sha256(body).hexdigest()
    _exclusive(root / "objects" / sha, body)
    return sha


def _read_object(root, sha):
    if not isinstance(sha, str) or not DIGEST.fullmatch(sha):
        raise CaptureError("invalid-object-hash")
    body = (root / "objects" / sha).read_bytes()
    if hashlib.sha256(body).hexdigest() != sha:
        raise CaptureError("object-hash-mismatch")
    return body


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _race(race_id, post_time):
    post = _time(post_time)
    if post.second or post.microsecond:
        raise CaptureError("minute-resolution-post-time-required")
    if not isinstance(race_id, str) or not re.fullmatch(r"\d{12}", race_id) or not 1 <= int(race_id[4:6]) <= 10:
        raise CaptureError("jra-race-id-required")
    if post.astimezone(JST).strftime("%Y") != race_id[:4]:
        raise CaptureError("race-year-mismatch")
    return post


def freeze_capture_protocol(store_dir, protocol, artifact_paths, *, _clock=None):
    """Freeze a new capture subprotocol and copy all bound artifacts locally.

    ``protocol`` requires protocol_id/start_at/end_at/feature_columns and may
    contain nullable_feature_columns and a parent_protocol_sha256. Period must
    be exactly 90 calendar days; all times are timezone aware. Extra descriptive
    protocol fields are retained and hashed. artifacts: model/calibrator/
    features/rule, optionally scorer and feature_builder source modules.
    """
    root = _root(store_dir)
    if (root / "protocol.json").exists():
        raise CaptureError("protocol-already-frozen")
    p = json.loads(canonical_bytes(protocol))
    if any(k in p for k in ("frozen_at", "artifact_hashes", "capture_module_sha256")):
        raise CaptureError("reserved-protocol-field")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", str(p.get("protocol_id", ""))):
        raise CaptureError("protocol-id-required")
    now, start, end = _now(_clock), _time(p.get("start_at")), _time(p.get("end_at"))
    if not now < start < end or end - start != timedelta(days=90):
        raise CaptureError("prospective-90-day-period-required")
    columns = p.get("feature_columns")
    if not isinstance(columns, list) or not columns or any(not isinstance(x, str) for x in columns) or len(set(columns)) != len(columns) or any(not re.fullmatch(r"safe_[a-z0-9_]+", x) or x in FORBIDDEN_FEATURES or x.startswith("safe_current_") for x in columns):
        raise CaptureError("explicit-safe-feature-allowlist-required")
    nullable = p.setdefault("nullable_feature_columns", [])
    if not isinstance(nullable, list) or len(set(nullable)) != len(nullable) or not set(nullable) <= set(columns):
        raise CaptureError("nullable-feature-contract-invalid")
    if not ARTIFACTS <= set(artifact_paths) or not set(artifact_paths) <= ARTIFACTS | {"scorer", "feature_builder"}:
        raise CaptureError("required-artifacts-missing")
    for key, value in POLICY.items():
        if key in p and p[key] != value:
            raise CaptureError("fixed-cutoff-policy-mismatch")
        p[key] = value
    p.update(schema="forward-capture-protocol-v1", frozen_at=_stamp(now), start_at=_stamp(start), end_at=_stamp(end),
             capture_module_sha256=file_digest(__file__), paper_only=True, automatic_purchase=False,
             minimum_complete_races=1000, minimum_race_days=30,
             source_time_policy="direct HTTP receipt; optional provider tick checked separately; never import-time")
    # Bytes are copied before the protocol commit marker; failures leave only
    # unreferenced content objects, never a partially committed protocol.
    p["artifact_hashes"] = {name: _object(root, Path(path).read_bytes()) for name, path in artifact_paths.items()}
    record = {"protocol": p, "protocol_sha256": digest(p)}
    _exclusive(root / "protocol.json", canonical_bytes(record))
    return record


def load_capture_protocol(store_dir):
    root = _root(store_dir)
    record = _read_json(root / "protocol.json")
    p = record["protocol"]
    if digest(p) != record["protocol_sha256"] or p["capture_module_sha256"] != file_digest(__file__):
        raise CaptureError("protocol-or-capture-source-changed")
    for key, value in POLICY.items():
        if p.get(key) != value:
            raise CaptureError("cutoff-contract-changed")
    for sha in p["artifact_hashes"].values():
        _read_object(root, sha)
    return record


def _within_period(p, moment, cutoff):
    if not _time(p["frozen_at"]) < moment or not _time(p["start_at"]) <= moment < _time(p["end_at"]):
        raise CaptureError("outside-frozen-prospective-period")
    if not cutoff - timedelta(seconds=p["max_source_age_seconds"]) <= moment <= cutoff:
        raise CaptureError("source-outside-cutoff-window")


def _post_in_period(p, post):
    if not _time(p["start_at"]) <= post < _time(p["end_at"]):
        raise CaptureError("race-outside-prospective-period")


def _url(url, race_id, kind):
    parsed = urlsplit(url)
    path = "/api/api_get_jra_odds.html" if kind == "odds" else "/race/shutuba.html"
    query = parse_qs(parsed.query)
    if parsed.scheme != "https" or parsed.hostname != "race.netkeiba.com" or parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.path != path or query.get("race_id") != [race_id]:
        raise CaptureError("source-url-identity-mismatch")
    if kind == "odds" and (query.get("type") != ["1"] or query.get("action") not in (["init"], ["update"])):
        raise CaptureError("win-odds-endpoint-required")


def _parse_odds(body):
    try:
        payload = json.loads(body)
        if payload.get("status") != "middle":
            raise CaptureError("pre-race-middle-odds-required")
        values = payload["data"]["odds"]["1"]
        if not isinstance(values, dict):
            raise ValueError()
        odds = {}
        for key, value in values.items():
            n = int(key)
            if str(n) != key or not 1 <= n <= 18 or n in odds or not isinstance(value, list) or not value:
                raise ValueError()
            price = float(value[0])
            if not math.isfinite(price) or price < 1.0:
                raise ValueError()
            odds[n] = price
        if not 2 <= len(odds) <= 18:
            raise ValueError()
        return odds, payload["data"].get("official_datetime")
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise CaptureError("invalid-complete-win-odds") from exc


def _card(body, race_id, post):
    """Conservative parser; fail closed on unknown markup or scratches.

    No guessed schedule or runner count is accepted. Real provider validation
    remains a rollout prerequisite; unit fixtures are synthetic.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(body, "html.parser")
    title = soup.title.get_text(" ") if soup.title else ""
    dates = {f"{int(y):04d}{int(m):02d}{int(d):02d}" for y, m, d in re.findall(r"(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})(?:日|\b)", title)}
    if dates != {post.astimezone(JST).strftime("%Y%m%d")}:
        raise CaptureError("card-date-not-proven")
    canonical = soup.select_one('link[rel="canonical"]')
    if canonical is None:
        raise CaptureError("card-race-identity-not-proven")
    _url(str(canonical.get("href", "")), race_id, "race_card")
    data = soup.select_one(".RaceData01")
    info = data.get_text(" ", strip=True) if data else ""
    time_match = re.search(r"(\d{1,2}:\d{2})\s*発走", info)
    if not time_match or time_match.group(1).zfill(5) != post.astimezone(JST).strftime("%H:%M"):
        raise CaptureError("card-post-time-not-proven")
    if any(marker in title + info for marker in ("障", "ジャンプ", "J・G", "JG")) or not re.search(r"(?:芝|ダ(?:ート)?)\s*\d{3,4}\s*m", info):
        raise CaptureError("jra-flat-card-required")
    table = soup.select_one("table.Shutuba_Table")
    if table is None or soup.select_one("table.RaceTable01, table.race_table_01"):
        raise CaptureError("pre-race-card-required")
    metadata = soup.select_one(".RaceData02")
    declared = re.findall(r"(?<!\d)(\d{1,2})\s*頭", metadata.get_text(" ") if metadata else "")
    if len(declared) != 1 or not 2 <= int(declared[0]) <= 18:
        raise CaptureError("declared-runner-count-required")
    runners = {}
    for row in table.select("tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        if len(cells) < 7 or any(word in row.get_text() for word in ("取消", "除外", "中止")):
            raise CaptureError("ambiguous-or-scratched-card")
        number = cells[1].get_text(strip=True)
        if not number.isdecimal() or not 1 <= int(number) <= 18 or int(number) in runners:
            raise CaptureError("card-runner-identity-invalid")
        anchor = cells[3].find("a", href=True)
        match = re.search(r"/horse/([A-Za-z0-9]+)(?:/|$)", urlsplit(str(anchor["href"])).path) if anchor else None
        if not match:
            raise CaptureError("card-horse-id-missing")
        runners[int(number)] = match.group(1)
    if len(runners) != int(declared[0]) or len(set(runners.values())) != len(runners):
        raise CaptureError("complete-card-required")
    return runners


async def _paced_fetch(url):
    import aiohttp
    from scraping.fetch_pipeline import fetch_text
    async with aiohttp.ClientSession() as session:
        result, _ = await fetch_text(session, url, use_cache=False, force_refresh=True,
                                    max_retries=1, max_body_bytes=2 * 1024 * 1024)
    return {"status": result.status, "body": result.body, "url": result.url, "source": result.source}


async def observe_http(store_dir, race_id, post_time, kind, *, fetcher=None, _clock=None):
    """Explicit one-shot GET, using the shared pacer by default; never scheduled.

    A custom transport is for trusted adapters/tests, not an old-data import.
    Returned receipt records transport source='network'; cached/final/late data
    cannot enter the prospective snapshot. No provider-time fallback to now.
    """
    root = _root(store_dir); record = load_capture_protocol(root); p = record["protocol"]
    if kind not in ("odds", "race_card"):
        raise CaptureError("source-kind-invalid")
    post = _race(race_id, post_time); cutoff = post - timedelta(seconds=p["cutoff_seconds"])
    _post_in_period(p, post)
    url = (f"https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={race_id}&type=1&action=update"
           if kind == "odds" else f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}")
    started = _now(_clock); _within_period(p, started, cutoff)
    result = await (fetcher or _paced_fetch)(url)
    received = _now(_clock); _within_period(p, received, cutoff)
    if received < started or result.get("source") != "network" or result.get("status") != 200:
        raise CaptureError("fresh-network-response-required")
    _url(result.get("url", ""), race_id, kind)
    body = result.get("body")
    if not isinstance(body, bytes) or not 0 < len(body) <= 2 * 1024 * 1024:
        raise CaptureError("raw-response-required")
    provider_time = None
    if kind == "odds":
        _, tick = _parse_odds(body)
        if tick not in (None, ""):
            provider_time = _time(tick)  # unknown formats/naive clocks are not invented
            if provider_time > received or cutoff - provider_time > timedelta(seconds=p["max_source_age_seconds"]):
                raise CaptureError("provider-tick-stale-or-future")
    else:
        _card(body, race_id, post)
    receipt = {"schema": "forward-http-receipt-v1", "protocol_sha256": record["protocol_sha256"],
               "race_id": race_id, "post_time": _stamp(post), "kind": kind,
               "capture_kind": "direct_fetch", "request_url": url, "final_url": result["url"], "status": 200,
               "request_started_at": _stamp(started), "received_at": _stamp(received), "source_at": _stamp(received),
               "provider_time": _stamp(provider_time) if provider_time else None,
               "body_sha256": _object(root, body), "body_size": len(body)}
    sha = digest(receipt)
    _exclusive(root / "receipts" / (sha + ".json"), canonical_bytes(receipt))
    return {"receipt_sha256": sha, **receipt}


def _receipt(root, sha, record, race_id, post, kind):
    if not isinstance(sha, str) or not DIGEST.fullmatch(sha):
        raise CaptureError("source-receipt-required")
    r = _read_json(root / "receipts" / (sha + ".json"))
    if digest(r) != sha or r["protocol_sha256"] != record["protocol_sha256"] or r["race_id"] != race_id or _time(r["post_time"]) != post or r["kind"] != kind or r["capture_kind"] != "direct_fetch":
        raise CaptureError("source-receipt-binding-mismatch")
    return r, _read_object(root, r["body_sha256"])


def _bound_callback(callback, name, p):
    source = inspect.getsourcefile(callback)
    if not source or name not in p["artifact_hashes"] or file_digest(source) != p["artifact_hashes"][name]:
        raise CaptureError(name + "-source-not-frozen")


def capture_snapshot(store_dir, payload, *, score_and_calibrate=None, feature_builder=None, _clock=None):
    """Commit one immutable snapshot per race; no paper purchase is made here.

    Payload keys: race_id/post_time/card_receipt_sha256/odds_receipt_sha256,
    features_generated_at/history_max_source_date/artifact_hashes/runners.
    Runners: horse_number/horse_id/features, optionally score/win_probability.
    Trusted scorer callback(runners, artifact_paths) returns predictions by horse
    number. Trusted builder callback(card_body, artifact_paths, payload) returns
    {runners, features_generated_at, history_max_source_date}. Its source must
    be frozen too. Without BOTH callbacks, data is marked NOT policy-ready.
    Hash lists alone never assert recomputation or inference verification.
    """
    root = _root(store_dir); record = load_capture_protocol(root); p = record["protocol"]
    data = json.loads(canonical_bytes(payload))
    keys = {"race_id", "post_time", "card_receipt_sha256", "odds_receipt_sha256", "features_generated_at", "history_max_source_date", "artifact_hashes", "runners"}
    if set(data) != keys:
        raise CaptureError("capture-payload-fields-invalid")
    race_id = data["race_id"]; post = _race(race_id, data["post_time"]); cutoff = post - timedelta(seconds=300)
    _post_in_period(p, post)
    now = _now(_clock); _within_period(p, now, cutoff)
    if now < cutoff - timedelta(seconds=30):
        raise CaptureError("capture-before-fixed-window")
    if data["artifact_hashes"] != p["artifact_hashes"]:
        raise CaptureError("artifact-binding-mismatch")
    card, card_body = _receipt(root, data["card_receipt_sha256"], record, race_id, post, "race_card")
    odds_receipt, odds_body = _receipt(root, data["odds_receipt_sha256"], record, race_id, post, "odds")
    members = _card(card_body, race_id, post); odds, _ = _parse_odds(odds_body)
    if set(members) != set(odds):
        raise CaptureError("incomplete-card-odds-field")
    for receipt in (card, odds_receipt):
        source, received, started = (_time(receipt[k]) for k in ("source_at", "received_at", "request_started_at"))
        _within_period(p, source, cutoff)
        if not started <= source == received <= now:
            raise CaptureError("source-time-order-invalid")
        if receipt["provider_time"] is not None and not cutoff - timedelta(seconds=120) <= _time(receipt["provider_time"]) <= received:
            raise CaptureError("provider-time-order-invalid")
    artifacts = {name: root / "objects" / sha for name, sha in p["artifact_hashes"].items()}
    if feature_builder is not None:
        _bound_callback(feature_builder, "feature_builder", p)
        built = feature_builder(card_body, artifacts, json.loads(canonical_bytes(data)))
        if set(built) != {"runners", "features_generated_at", "history_max_source_date"}:
            raise CaptureError("builder-output-contract-invalid")
        data.update(built)
    generated = _time(data["features_generated_at"])
    if not max(_time(card["received_at"]), _time(odds_receipt["received_at"])) <= generated <= _now(_clock):
        raise CaptureError("features-source-time-order-invalid")
    _within_period(p, generated, cutoff)
    history = data["history_max_source_date"]
    try:
        history_date = datetime.strptime(history, "%Y%m%d").date()
    except (ValueError, TypeError) as exc:
        raise CaptureError("history-source-date-required") from exc
    if history_date >= post.astimezone(JST).date():
        raise CaptureError("same-day-or-future-history")
    runners = data["runners"]
    if not isinstance(runners, list) or len(runners) != len(members):
        raise CaptureError("complete-feature-field-required")
    seen = set()
    for row in runners:
        if not isinstance(row, dict) or not {"horse_number", "horse_id", "features"} <= set(row) or not set(row) <= {"horse_number", "horse_id", "features", "score", "win_probability"}:
            raise CaptureError("runner-fields-invalid")
        n = row["horse_number"]
        if type(n) is not int or n not in members or n in seen or row["horse_id"] != members[n]:
            raise CaptureError("feature-runner-binding-mismatch")
        seen.add(n)
        features = row["features"]
        if not isinstance(features, dict) or set(features) != set(p["feature_columns"]):
            raise CaptureError("feature-allowlist-mismatch")
        for name, value in features.items():
            if value is None:
                if name not in p["nullable_feature_columns"]:
                    raise CaptureError("required-feature-missing")
            elif isinstance(value, bool) or not isinstance(value, (int, float, str)) or (isinstance(value, (int, float)) and not math.isfinite(value)):
                raise CaptureError("feature-value-invalid")
        row["odds"] = odds[n]
    if score_and_calibrate is not None:
        _bound_callback(score_and_calibrate, "scorer", p)
        # Market fields are physically removed before calling the scorer.
        inputs = [{k: r[k] for k in ("horse_number", "horse_id", "features")} for r in runners]
        predictions = score_and_calibrate(inputs, artifacts)
        if not isinstance(predictions, list) or len(predictions) != len(runners):
            raise CaptureError("inference-output-incomplete")
        by_number = {}
        for pred in predictions:
            if set(pred) != {"horse_number", "score", "win_probability"} or type(pred["horse_number"]) is not int or pred["horse_number"] in by_number:
                raise CaptureError("inference-output-invalid")
            by_number[pred["horse_number"]] = pred
        if set(by_number) != seen:
            raise CaptureError("inference-runner-mismatch")
        for row in runners:
            row.update(by_number[row["horse_number"]])
    predicted = _now(_clock)
    available = all("score" in row and "win_probability" in row for row in runners)
    if available:
        for row in runners:
            for key in ("score", "win_probability"):
                if type(row[key]) not in (int, float) or not math.isfinite(row[key]):
                    raise CaptureError("prediction-value-invalid")
            if not 0 <= row["win_probability"] <= 1:
                raise CaptureError("probability-range-invalid")
        if abs(sum(r["win_probability"] for r in runners) - 1) > 1e-8:
            raise CaptureError("race-probabilities-not-normalized")
    elif any("score" in row or "win_probability" in row for row in runners):
        raise CaptureError("partial-prediction-field")
    stored = _now(_clock); _within_period(p, stored, cutoff)
    if not generated <= predicted <= stored:
        raise CaptureError("prediction-time-order-invalid")
    # Verify again after callbacks so a callback cannot silently change artifacts.
    load_capture_protocol(root)
    verified = feature_builder is not None and score_and_calibrate is not None
    output = {"schema": "forward-race-snapshot-v1", "race_id": race_id, "post_time": _stamp(post),
              "cutoff_at": _stamp(cutoff), "features_generated_at": _stamp(generated),
              "prediction_at": _stamp(predicted) if available else None, "stored_at": _stamp(stored),
              "protocol_sha256": record["protocol_sha256"], "artifact_hashes": p["artifact_hashes"],
              "card_receipt_sha256": data["card_receipt_sha256"], "odds_receipt_sha256": data["odds_receipt_sha256"],
              "source_at": odds_receipt["source_at"], "received_at": odds_receipt["received_at"],
              "provider_time": odds_receipt["provider_time"], "capture_kind": "direct_fetch",
              "history_max_source_date": history, "feature_values_sha256": digest([{k: r[k] for k in ("horse_number", "horse_id", "features")} for r in runners]),
              "runners": runners, "inference_verified": score_and_calibrate is not None,
              "raw_features_recomputed": feature_builder is not None, "policy_ready": verified,
              "paper_only": True, "automatic_purchase": False,
              "limitations": ["Source receipts are local observation evidence, not signed provider timestamps.",
                              "Pari-mutuel indicative odds are not locked executable prices.",
                              "Callback verification attests execution of frozen code, not independent proof of its correctness."]}
    envelope = {"snapshot": output, "snapshot_sha256": digest(output)}
    _exclusive(root / "captures" / (race_id + ".json"), canonical_bytes(envelope))
    return envelope


def record_capture_failure(store_dir, race_id, reason, *, _clock=None):
    """Append a skip/failure event; never marks a race as successfully captured."""
    root = _root(store_dir); p = load_capture_protocol(root)
    if not re.fullmatch(r"\d{12}", race_id) or not isinstance(reason, str) or not reason.strip() or len(reason) > 300:
        raise CaptureError("failure-event-invalid")
    event = {"race_id": race_id, "reason": reason, "recorded_at": _stamp(_now(_clock)),
             "protocol_sha256": p["protocol_sha256"], "paper_action": "skip", "automatic_purchase": False}
    _exclusive(root / "failures" / (race_id + "-" + uuid.uuid4().hex + ".json"), canonical_bytes(event))
    return event
