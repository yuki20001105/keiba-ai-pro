"""Owner-scoped, frozen live recommendations and paper/actual performance.

This is an application ledger, not a research ledger or a betting executor.
Only a server-generated snapshot can supply probability, odds, or model data.
Official dividends per 100 yen settle tickets; predicted odds never do.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from itertools import combinations
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any
import uuid

from .value_policy import BET_TYPES, ORDERED_TYPES, POLICY_VERSION, _instant, _post_time, verified_prediction_input
from .selection_diagnostics import summarize_selection_diagnostics

_DDL = """
CREATE TABLE IF NOT EXISTS betting_performance_snapshots (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(owner, payload_sha256)
);
CREATE TABLE IF NOT EXISTS betting_performance_records (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, snapshot_id TEXT NOT NULL,
    race_id TEXT NOT NULL, race_date TEXT NOT NULL, mode TEXT NOT NULL,
    payload_json TEXT NOT NULL, tickets_json TEXT NOT NULL,
    status TEXT NOT NULL, result_json TEXT, created_at TEXT NOT NULL,
    settled_at TEXT, UNIQUE(owner, race_id, mode)
);
CREATE INDEX IF NOT EXISTS betting_performance_owner_date
ON betting_performance_records(owner, mode, race_date DESC);
"""
_ALIASES = {
    "単勝": "単勝", "win": "単勝", "tan": "単勝", "tansho": "単勝",
    "馬連": "馬連", "umaren": "馬連", "quinella": "馬連",
    "ワイド": "ワイド", "wide": "ワイド",
    "馬単": "馬単", "umatan": "馬単", "exacta": "馬単",
    "三連複": "三連複", "3連複": "三連複", "sanrenpuku": "三連複", "trio": "三連複",
    "三連単": "三連単", "3連単": "三連単", "sanrentan": "三連単", "trifecta": "三連単",
}
_SELECTION_FIELDS = frozenset({"bet_type", "combination", "horse_numbers", "stake"})
_TERMINAL = frozenset({"settled", "skipped"})
_CANCEL_WORDS = ("取消", "除外", "中止", "失格", "返還", "同着", "cancel", "refund", "dead_heat", "dead-heat")


def _encoded(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("保存データが不正です") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_encoded(value).encode("utf-8")).hexdigest()


def _owner(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PermissionError("記録が見つかりません")
    return value


def _now(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("時刻にはタイムゾーンが必要です")
    return value.astimezone(timezone.utc)


def _race_date(value: Any) -> str:
    text = str(value or "").replace("/", "-")
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError("レース開催日が不正です") from exc


@contextmanager
def _database(path: str | Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(_DDL)
        # Race-first freeze and duplicate submissions are atomic across threads.
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def save_snapshot(db_path: str | Path, owner: str, response_dict: dict) -> str:
    """Freeze a server response. Same owner and content return the same ID."""
    owner = _owner(owner)
    if not isinstance(response_dict, dict):
        raise ValueError("予測結果が不正です")
    info, rec = response_dict.get("race_info"), response_dict.get("recommendation")
    if not isinstance(info, dict) or not str(info.get("race_id") or "").strip():
        raise ValueError("レース情報がありません")
    _race_date(info.get("date") or info.get("race_date"))
    if not isinstance(rec, dict) or rec.get("policy_version") != POLICY_VERSION:
        raise ValueError("再判定が必要です")
    if rec.get("decision") in {"buy", "priority"} and (
        rec.get("input_verified") is not True
        or not verified_prediction_input(response_dict.get("input_diagnostics"))
    ):
        raise ValueError("モデルの入力互換性が未確認です")
    encoded, digest = _encoded(response_dict), _digest(response_dict)
    with _database(db_path) as db:
        previous = db.execute(
            "SELECT id FROM betting_performance_snapshots WHERE owner=? AND payload_sha256=?", (owner, digest)
        ).fetchone()
        if previous:
            return str(previous["id"])
        snapshot_id = str(uuid.uuid4())
        db.execute("INSERT INTO betting_performance_snapshots VALUES (?,?,?,?,?)",
                   (snapshot_id, owner, encoded, digest, _now(None).isoformat()))
        return snapshot_id


def _ticket_key(ticket: dict, *, selected: bool = False) -> tuple[str, tuple[int, ...]]:
    if not isinstance(ticket, dict) or (selected and set(ticket) - _SELECTION_FIELDS):
        raise ValueError("買い目には券種・馬番・金額だけ指定してください")
    kind = ticket.get("bet_type")
    members = ticket.get("horse_numbers")
    if kind not in BET_TYPES or not isinstance(members, list) or len(members) != BET_TYPES[kind]:
        raise ValueError("買い目の券種または馬番が不正です")
    if any(type(n) is not int or not 1 <= n <= 99 for n in members) or len(set(members)) != len(members):
        raise ValueError("買い目の馬番が不正です")
    canonical = members if kind in ORDERED_TYPES else sorted(members)
    if members != canonical or ticket.get("combination") != "-".join(map(str, canonical)):
        raise ValueError("買い目の組み合わせが一致しません")
    return kind, tuple(canonical)


def _offer_is_live(rec: dict, info: dict, now: datetime) -> bool:
    evaluated, valid_until = _instant(rec.get("evaluated_at")), _instant(rec.get("valid_until"))
    post = _post_time({**info, "date": info.get("date") or info.get("race_date")})
    return bool(
        rec.get("policy_version") == POLICY_VERSION
        and rec.get("input_verified") is True
        and rec.get("probability_basis") == "calibrated"
        and rec.get("decision") in {"buy", "priority"}
        and evaluated is not None and valid_until is not None and post is not None
        and evaluated <= now < min(valid_until, evaluated + timedelta(minutes=5), post - timedelta(minutes=1))
    )


def _selected_tickets(payload: dict, selected: list[dict], now: datetime) -> list[dict]:
    info, rec = payload["race_info"], payload["recommendation"]
    if not isinstance(selected, list) or len(selected) > 20:
        raise ValueError("買い目が不正です")
    if not selected:
        # Persisting a pass cannot create a purchase, even after offer expiry.
        return []
    if not verified_prediction_input(payload.get("input_diagnostics")):
        raise ValueError("モデルの入力互換性が未確認です")
    if not _offer_is_live(rec, info, now):
        raise ValueError("買い目の期限切れです。予測を再実行してください")
    offers = rec.get("tickets")
    if not isinstance(offers, list) or not offers:
        raise ValueError("購入候補がありません")
    by_key: dict[tuple, dict] = {}
    for offer in offers:
        key = _ticket_key(offer)
        if key in by_key:
            raise ValueError("保存済み買い目が重複しています")
        stake = offer.get("stake")
        if type(stake) is not int or stake < 100 or stake % 100:
            raise ValueError("保存済み買い目の金額が不正です")
        for field in ("probability", "odds", "expected_value", "conservative_expected_value"):
            value = offer.get(field)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("保存済み買い目の根拠が不正です")
        if not 0 < offer["probability"] <= 1 or offer["odds"] <= 1 or min(offer["expected_value"], offer["conservative_expected_value"]) <= 1:
            raise ValueError("保存済み買い目の根拠が不正です")
        by_key[key] = offer
    frozen, seen = [], set()
    for item in selected:
        key = _ticket_key(item, selected=True)
        stake = item.get("stake")
        if key in seen or key not in by_key:
            raise ValueError("予測で選定されていない買い目です")
        seen.add(key)
        if type(stake) is not int or stake < 100 or stake % 100 or stake > by_key[key]["stake"]:
            raise ValueError("金額は候補の範囲内で100円単位にしてください")
        frozen.append({**by_key[key], "stake": stake})
    ceiling = rec.get("budget")
    per_race = rec.get("per_race_limit", ceiling)
    if (type(ceiling) not in (int, float) or type(per_race) not in (int, float)
        or not math.isfinite(ceiling) or not math.isfinite(per_race)
        or sum(t["stake"] for t in frozen) > min(ceiling, per_race)):
        raise ValueError("レース予算を超えています")
    return frozen


def _record(row: sqlite3.Row | dict, *, existing: bool = False) -> dict:
    payload, tickets = json.loads(row["payload_json"]), json.loads(row["tickets_json"])
    rec, info = payload["recommendation"], payload["race_info"]
    selection = payload.get("_performance_selection") or {}
    if not tickets and not selection:
        # Compatibility for records saved before the reason was frozen. Use
        # their creation time, never the time this screen is being refreshed.
        selected_at = _instant(row["created_at"])
        expired = rec.get("decision") in {"buy", "priority"} and (
            selected_at is None or not _offer_is_live(rec, info, selected_at)
        )
        selection = {"reason": "買い目の期限切れ" if expired else "この日の予算上限",
                     "reason_codes": ["offer_expired" if expired else "day_budget_limit"]}
    result = json.loads(row["result_json"]) if row["result_json"] else {}
    total = sum(t["stake"] for t in tickets)
    actual_return = result.get("actual_return") if row["status"] == "settled" else None
    return {
        "id": row["id"], "snapshot_id": row["snapshot_id"], "race_id": row["race_id"], "race_date": row["race_date"],
        "race_info": info, "model_id": payload.get("model_id") or rec.get("model_id") or "",
        "policy_version": rec["policy_version"], "decision": rec.get("decision") if tickets else "skip",
        "reason": selection.get("reason", rec.get("reason")) if not tickets and rec.get("decision") in {"buy", "priority"} else rec.get("reason"),
        "reason_codes": selection.get("reason_codes", rec.get("reason_codes", [])) if not tickets and rec.get("decision") in {"buy", "priority"} else rec.get("reason_codes", []),
        "tickets": result.get("tickets", tickets), "mode": row["mode"], "status": row["status"],
        "total_cost": total, "actual_return": actual_return,
        "is_hit": result.get("is_hit") if row["status"] == "settled" else None,
        "profit": actual_return - total if actual_return is not None else None,
        "created_at": row["created_at"], "settled_at": row["settled_at"],
        "settlement_reason": result.get("reason"), "existing": existing,
    }


def record_selection(
    db_path: str | Path, owner: str, snapshot_id: str, tickets: list[dict], mode: str = "paper", *, now: datetime | None = None,
) -> dict:
    """Freeze one choice per owner/race/mode. No purchase is transmitted."""
    owner, now = _owner(owner), _now(now)
    if mode not in {"paper", "actual"}:
        raise ValueError("記録区分が不正です")
    with _database(db_path) as db:
        snapshot = db.execute("SELECT * FROM betting_performance_snapshots WHERE id=? AND owner=?", (snapshot_id, owner)).fetchone()
        if snapshot is None:
            raise PermissionError("記録が見つかりません")
        payload = json.loads(snapshot["payload_json"])
        if _digest(payload) != snapshot["payload_sha256"]:
            raise ValueError("保存済み予測の整合性を確認してください")
        info, rec = payload["race_info"], payload["recommendation"]
        if rec.get("policy_version") != POLICY_VERSION:
            raise ValueError("再判定が必要です")
        race_id = str(info["race_id"])
        previous = db.execute("SELECT * FROM betting_performance_records WHERE owner=? AND race_id=? AND mode=?", (owner, race_id, mode)).fetchone()
        if previous:
            return _record(previous, existing=True)
        frozen = _selected_tickets(payload, tickets, now)
        if not frozen:
            expired = rec.get("decision") in {"buy", "priority"} and not _offer_is_live(rec, info, now)
            if rec.get("decision") in {"buy", "priority"}:
                # Derived only from the stored server offer and server time.
                # The client cannot label an expired offer as a budget pass.
                payload = {**payload, "_performance_selection": {
                    "reason": "買い目の期限切れ" if expired else "この日の予算上限",
                    "reason_codes": ["offer_expired" if expired else "day_budget_limit"],
                }}
        if mode == "actual" and not frozen:
            raise ValueError("購入済み記録には買い目が必要です")
        if mode == "actual":
            paper = db.execute("SELECT * FROM betting_performance_records WHERE owner=? AND race_id=? AND mode='paper'", (owner, race_id)).fetchone()
            if paper:
                paper_payload, paper_tickets = json.loads(paper["payload_json"]), json.loads(paper["tickets_json"])
                identity = lambda ts: sorted((_ticket_key(t), t["stake"]) for t in ts)
                if (identity(frozen) != identity(paper_tickets)
                    or (paper_payload.get("model_id") or "") != (payload.get("model_id") or "")):
                    raise ValueError("AI参考に保存した買い目と一致しません")
                # Actual confirmation copies the already frozen decision, not a
                # recalculated probability or a later outcome-aware prediction.
                payload, frozen, snapshot_id = paper_payload, paper_tickets, paper["snapshot_id"]
        record_id = str(uuid.uuid4())
        db.execute("INSERT INTO betting_performance_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (record_id, owner, snapshot_id, race_id, _race_date(info.get("date") or info.get("race_date")), mode,
                    _encoded(payload), _encoded(frozen), "pending" if frozen else "skipped", None, now.isoformat(), None))
        row = db.execute("SELECT * FROM betting_performance_records WHERE id=? AND owner=?", (record_id, owner)).fetchone()
        return _record(row)


def _parse_finish(value: Any) -> int | None:
    if type(value) is int:
        return value if value > 0 else None
    if isinstance(value, str) and re.fullmatch(r"[1-9]\d*", value.strip()):
        return int(value.strip())
    return None


def _exceptional(row: dict) -> bool:
    if any(row.get(key) is True for key in ("cancelled", "is_cancelled", "excluded", "is_excluded", "refunded", "refund", "dead_heat", "is_dead_heat")):
        return True
    text = " ".join(str(row.get(key) or "").lower() for key in (
        "status", "race_status", "result_status", "finish_position", "finish", "finish_order", "actual_finish", "remarks", "note",
    ))
    return any(word in text for word in _CANCEL_WORDS)


def _result_source(path: str | Path, race_id: str) -> tuple[dict | None, list[dict], list[dict]]:
    path = Path(path)
    if not path.is_file():
        return None, [], []
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        # All provider tables must come from one coherent read snapshot while
        # an acquisition worker may be replacing results in a transaction.
        db.execute("BEGIN")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        info, runners, dividends = None, [], []
        if "races_ultimate" in tables:
            source = db.execute("SELECT data FROM races_ultimate WHERE race_id=?", (race_id,)).fetchone()
            if source:
                info = json.loads(source["data"])
        if "race_results_ultimate" in tables:
            for source in db.execute("SELECT data FROM race_results_ultimate WHERE race_id=?", (race_id,)):
                data = json.loads(source["data"])
                if isinstance(data, dict):
                    runners.append(data)
                elif isinstance(data, list) and all(isinstance(item, dict) for item in data):
                    runners.extend(data)
        if "return_tables_ultimate" in tables:
            dividends = [dict(row) for row in db.execute("SELECT bet_type,combinations,payout FROM return_tables_ultimate WHERE race_id=?", (race_id,))]
        return info if isinstance(info, dict) else None, runners, dividends
    finally:
        db.close()


def _official_key(kind: str, value: Any) -> tuple[int, ...] | None:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    text = str(value).strip()
    # Do not accept numeric fragments hidden inside arbitrary source labels.
    if not re.fullmatch(r"\d+(?:\s*[-−－—→>,/\s]\s*\d+)*", text):
        return None
    numbers = tuple(int(n) for n in re.findall(r"\d+", text))
    if len(numbers) != BET_TYPES[kind] or len(set(numbers)) != len(numbers) or any(n < 1 or n > 99 for n in numbers):
        return None
    return numbers if kind in ORDERED_TYPES else tuple(sorted(numbers))


def _settlement(row: sqlite3.Row, result_path: str | Path) -> tuple[str, dict]:
    payload, tickets = json.loads(row["payload_json"]), json.loads(row["tickets_json"])
    if not tickets:
        return "skipped", {"reason": "買い目なし"}
    try:
        info, runners, dividends = _result_source(result_path, row["race_id"])
    except (sqlite3.Error, ValueError, TypeError):
        return "review", {"reason": "結果データを確認してください"}
    if info is None or not runners:
        return "pending", {"reason": "結果待ち"}
    source_date = info.get("date") or info.get("race_date")
    if source_date:
        try:
            if _race_date(source_date) != row["race_date"]:
                return "review", {"reason": "結果の開催日が一致しません"}
        except ValueError:
            return "review", {"reason": "結果の開催日を確認してください"}
    if _exceptional(info) or any(_exceptional(runner) for runner in runners):
        return "review", {"reason": "取消・返還・同着などの確認が必要です"}
    expected = payload["race_info"].get("num_horses")
    if type(expected) is not int or not 2 <= expected <= 18:
        return "review", {"reason": "出走頭数を確認してください"}
    numbers = [runner.get("horse_number", runner.get("horse_no")) for runner in runners]
    numbers = [_parse_finish(number) or 0 for number in numbers]
    if len(set(numbers)) != len(numbers) or any(n < 1 or n > 99 for n in numbers):
        return "review", {"reason": "出走馬の重複・不整合を確認してください"}
    if len(runners) < expected:
        return "pending", {"reason": "結果の取得が未完了です"}
    if len(runners) != expected:
        return "review", {"reason": "出走頭数が一致しません"}
    finishes = [_parse_finish(next((runner[key] for key in ("finish_position", "finish", "finish_order", "actual_finish") if runner.get(key) is not None), None)) for runner in runners]
    if any(finish is None for finish in finishes):
        return "pending", {"reason": "確定着順待ち"}
    if len(set(finishes)) != len(finishes):
        return "review", {"reason": "同着の払戻確認が必要です"}
    if set(finishes) != set(range(1, expected + 1)):
        return "review", {"reason": "確定着順が不整合です"}
    # Compare the saved identity set as well as count. A changed entry cannot
    # settle a historical selection just because it kept the same horse number.
    predicted = payload.get("predictions")
    if isinstance(predicted, list) and predicted:
        source_numbers = {p.get("horse_number", p.get("horse_no")) for p in predicted if isinstance(p, dict)}
        if source_numbers != set(numbers):
            return "review", {"reason": "予測時と出走馬が一致しません"}
        source_horses = dict(zip(numbers, runners))
        for prediction in predicted:
            if not isinstance(prediction, dict) or not prediction.get("horse_id"):
                continue
            number = prediction.get("horse_number", prediction.get("horse_no"))
            if str(source_horses[number].get("horse_id") or "") != str(prediction["horse_id"]):
                return "review", {"reason": "予測時と馬の識別情報が一致しません"}
    ordered = [number for _, number in sorted(zip(finishes, numbers))]
    top3 = ordered[:3]
    winners = {
        "単勝": {tuple(ordered[:1])}, "馬連": {tuple(sorted(ordered[:2]))},
        "馬単": {tuple(ordered[:2])}, "ワイド": {tuple(sorted(pair)) for pair in combinations(top3, 2)},
        "三連複": {tuple(sorted(top3))}, "三連単": {tuple(top3)},
    }
    needed = {t["bet_type"] for t in tickets}
    official: dict[str, dict[tuple, int]] = {}
    for kind in needed:
        minimum_runners = 2 if kind == "単勝" else 3 if kind in {"馬連", "馬単"} else 4
        if expected < minimum_runners:
            return "review", {"reason": "券種の成立条件を確認してください"}
        rows = [d for d in dividends if _ALIASES.get(str(d.get("bet_type") or "").strip().lower()) == kind]
        if not rows:
            return "payout_pending", {"reason": "公式払戻待ち"}
        known: dict[tuple, int] = {}
        for dividend in rows:
            key, amount = _official_key(kind, dividend.get("combinations")), dividend.get("payout")
            if key is None or type(amount) is not int or amount <= 0:
                return "review", {"reason": "公式払戻データが不正です"}
            if key in known and known[key] != amount:
                return "review", {"reason": "公式払戻に不一致があります"}
            known[key] = amount
        if set(known) - winners[kind]:
            return "review", {"reason": "着順と公式払戻が一致しません"}
        if set(known) != winners[kind]:
            return "payout_pending", {"reason": "公式払戻表の取得が未完了です"}
        official[kind] = known
    settled_tickets, total_return = [], 0
    for ticket in tickets:
        key = _ticket_key(ticket)[1]
        payout = official[ticket["bet_type"]].get(key, 0)
        amount = payout * (ticket["stake"] // 100)
        total_return += amount
        settled_tickets.append({**ticket, "actual_return": amount, "is_hit": bool(payout), "status": "settled"})
    return "settled", {
        "actual_return": total_return, "is_hit": any(t["is_hit"] for t in settled_tickets),
        "tickets": settled_tickets, "reason": "公式払戻で確定", "source_sha256": _digest({"race": info, "runners": runners, "dividends": dividends}),
    }


def _summary(records: list[dict]) -> dict:
    settled = [record for record in records if record["status"] == "settled"]
    pending = [record for record in records if record["status"] not in _TERMINAL]
    cost = sum(record["total_cost"] for record in settled)
    returned = sum(record["actual_return"] for record in settled)
    return {
        "settled_cost": cost, "settled_return": returned, "profit": returned - cost,
        "roi": round(returned / cost * 100, 1) if cost else None,
        "settled_races": len(settled), "hit_races": sum(bool(record["is_hit"]) for record in settled),
        "pending_races": len(pending), "pending_cost": sum(record["total_cost"] for record in pending),
        "skipped_races": sum(record["status"] == "skipped" for record in records),
    }


def list_performance(
    db_path: str | Path, result_db_path: str | Path, owner: str, mode: str, limit: int = 100,
    date_from: str | None = None, date_to: str | None = None,
) -> dict:
    """Reconcile local official results; summary covers the entire filtered set."""
    owner = _owner(owner)
    if mode not in {"paper", "actual"} or type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("表示条件が不正です")
    start = _race_date(date_from) if date_from else None
    end = _race_date(date_to) if date_to else None
    if start and end and start > end:
        raise ValueError("期間が不正です")
    with _database(db_path) as db:
        where, values = "r.owner=? AND r.mode=?", [owner, mode]
        if start:
            where += " AND r.race_date>=?"
            values.append(start)
        if end:
            where += " AND r.race_date<=?"
            values.append(end)
        rows = db.execute("SELECT r.* FROM betting_performance_records r WHERE " + where + " ORDER BY r.race_date DESC,r.created_at DESC,r.id DESC", values).fetchall()
        snapshots = {row["id"]: dict(row) for row in db.execute(
            "SELECT DISTINCT s.* FROM betting_performance_snapshots s JOIN betting_performance_records r "
            "ON s.id=r.snapshot_id AND s.owner=r.owner WHERE " + where, values,
        )}
        records = []
        diagnostic_rows = []
        for row in rows:
            if row["status"] not in _TERMINAL:
                status, result = _settlement(row, result_db_path)
                settled_at = _now(None).isoformat() if status == "settled" else None
                db.execute("UPDATE betting_performance_records SET status=?,result_json=?,settled_at=? WHERE id=? AND owner=?",
                           (status, _encoded(result), settled_at, row["id"], owner))
                row = dict(row)
                row.update(status=status, result_json=_encoded(result), settled_at=settled_at)
            records.append(_record(row))
            diagnostic_rows.append(dict(row))
        summary = _summary(records)
    # Bootstrap is read-only CPU work outside the ledger's write transaction.
    diagnostics = summarize_selection_diagnostics(
        diagnostic_rows, snapshots, mode=mode, date_from=start, date_to=end,
    )
    return {"records": records[:limit], "summary": summary, "total_count": len(records),
            "selection_diagnostics": diagnostics}
