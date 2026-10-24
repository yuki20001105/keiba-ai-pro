"""
購入推奨エクスポートエンドポイント
POST /api/export/bet-list       - 購入推奨リストをJSON/CSV形式で返す
GET  /api/export/bet-list/csv   - CSVファイルダウンロード（クエリパラメータで race_ids 指定）

出力形式:
  {
    "generated_at": "...",
    "bankroll": 100000,
    "summary": { "total_cost": 3800, "races": 3, "bets": 9 },
    "bets": [
      {
        "race_id": "202505051211",
        "race_name": "...",
        "venue": "東京",
        "race_no": 11,
        "post_time": "15:40",
        "bet_type": "単勝",        # 馬券種
        "bet_type_code": "tansho", # IPATコード対応
        "combination": "5",        # 馬番（馬連は "3-5"、三連複は "2-5-8"）
        "horse_names": ["ディープインパクト"],
        "unit_price": 100,
        "units": 3,
        "total_cost": 300,
        "expected_value": 2.34,
        "win_probability": 0.28,
        "odds": 8.3,
        "race_level": "decisive"
      },
      ...
    ]
  }

IPAT馬券種コード対応:
  単勝=tan / 複勝=fuku / 枠連=wakuren / 馬連=umaren
  ワイド=wide / 馬単=umatan / 三連複=sanrenpuku / 三連単=sanrentan
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app_config import logger  # type: ignore
from betting.value_policy import POLICY_VERSION, verified_prediction_input

router = APIRouter()

# 馬券種 → IPATコード対応表
BET_TYPE_TO_CODE: dict[str, str] = {
    "単勝": "tan",
    "複勝": "fuku",
    "枠連": "wakuren",
    "馬連": "umaren",
    "ワイド": "wide",
    "馬単": "umatan",
    "三連複": "sanrenpuku",
    "三連単": "sanrentan",
}

# IPATコード → 日本語
CODE_TO_BET_TYPE: dict[str, str] = {v: k for k, v in BET_TYPE_TO_CODE.items()}


def _aware_instant(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _build_bet_rows(
    analyze_results: list[dict],
    bankroll: int,
    min_ev: float,
    min_prob: float,
    max_bets_per_race: int,
    *,
    now: datetime | None = None,
) -> list[dict]:
    """Export only the decision policy's selected tickets and assigned stakes.

    Legacy candidate lists are not purchase decisions. In particular, a skip
    must never be turned back into an order by an export-time EV filter.
    """
    rows: list[dict] = []
    current = now if now is not None else datetime.now(timezone.utc)
    if current.tzinfo is None:
        return rows
    current = current.astimezone(timezone.utc)

    for result in analyze_results:
        if not isinstance(result, dict) or result.get("success") is not True:
            continue

        recommendation = result.get("recommendation")
        if not isinstance(recommendation, dict):
            continue
        if (
            recommendation.get("policy_version") != POLICY_VERSION
            or recommendation.get("input_verified") is not True
            or not verified_prediction_input(result.get("input_diagnostics"))
            or recommendation.get("probability_basis") != "calibrated"
            or recommendation.get("decision") not in ("buy", "priority")
        ):
            continue
        if (
            recommendation.get("decision") == "skip"
            or recommendation.get("action") == "見送り"
            or result.get("race_level") == "skip"
        ):
            continue
        evaluated = _aware_instant(recommendation.get("evaluated_at"))
        valid_until = _aware_instant(recommendation.get("valid_until"))
        if (
            evaluated is None or valid_until is None
            or valid_until <= evaluated or current < evaluated
            or current >= min(valid_until, evaluated + timedelta(minutes=5))
        ):
            continue
        tickets = recommendation.get("tickets")
        if not isinstance(tickets, list) or not tickets:
            continue

        race_info = result.get("race_info") or {}
        if not isinstance(race_info, dict):
            continue
        race_id = race_info.get("race_id") or result.get("race_id", "")
        race_name = race_info.get("race_name", "")
        venue = race_info.get("venue", "")
        race_no = race_info.get("race_no", 0)
        post_time = race_info.get("post_time", "")

        predictions: list[dict] = result.get("predictions", [])
        best_bet_type: str = result.get("best_bet_type") or ""
        race_level: str = result.get("race_level") or "normal"

        _horse_map: dict[str, str] = {
            str(p.get("horse_number") or p.get("horse_no", "")): p.get("horse_name", "")
            for p in predictions
            if isinstance(p, dict)
        }

        added = 0
        for ticket in tickets:
            if added >= max_bets_per_race:
                break
            if not isinstance(ticket, dict):
                continue
            bet_label = ticket.get("bet_type")
            comb = str(ticket.get("combination") or "")
            if not isinstance(bet_label, str) or bet_label not in BET_TYPE_TO_CODE or not comb:
                continue
            raw_stake = ticket.get("stake")
            if any(isinstance(ticket.get(key), bool) for key in
                   ("stake", "expected_value", "conservative_expected_value", "probability", "odds")):
                continue
            try:
                ev = float(ticket["expected_value"])
                conservative_ev = float(ticket["conservative_expected_value"])
                prob = float(ticket["probability"])
                odds = float(ticket["odds"])
                stake = float(raw_stake)
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if (
                not all(math.isfinite(value) for value in (ev, conservative_ev, prob, odds, stake))
                or not 0 < conservative_ev <= ev
                or not 0 < prob <= 1
                or odds <= 1
                or stake < 100
                or not stake.is_integer()
                or stake % 100 != 0
            ):
                continue
            # Filter before display rounding; never alter the assigned stake.
            if conservative_ev < max(1.0, min_ev) or prob < min_prob:
                continue
            nums = re.split(r"[-→]", comb)
            horse_names = [_horse_map.get(n.strip(), f"#{n.strip()}") for n in nums]
            rows.append({
                "race_id": race_id,
                "race_name": race_name,
                "venue": venue,
                "race_no": race_no,
                "post_time": post_time,
                "bet_type": bet_label,
                "bet_type_code": BET_TYPE_TO_CODE[bet_label],
                "combination": comb,
                "horse_names": horse_names,
                "unit_price": 100,
                "units": int(stake) // 100,
                "total_cost": int(stake),
                "expected_value": round(ev, 3),
                "win_probability": round(prob, 4),
                "odds": odds,
                "race_level": race_level,
                "is_best_bet": bet_label == best_bet_type,
            })
            added += 1

    return rows


@router.post("/api/export/bet-list")
async def export_bet_list(body: dict):
    """
    /api/analyze-race の結果リストを受け取り購入推奨リストを構造化して返す。

    Request body:
      {
        "results": [ ... ],   # analyze_race レスポンスの配列
        "bankroll": 100000,   # 総資金（任意、デフォルト100000）
        "min_ev": 1.0,        # 最低期待値フィルタ（任意）
        "min_prob": 0.0,      # 最低確率フィルタ（任意）
        "max_bets_per_race": 5  # レースあたり最大買い目数（任意）
      }
    """
    analyze_results: list[dict] = body.get("results", [])
    bankroll: int = int(body.get("bankroll") or 100000)
    min_ev: float = float(body.get("min_ev") or 1.0)
    min_prob: float = float(body.get("min_prob") or 0.0)
    max_bets_per_race: int = int(body.get("max_bets_per_race") or 5)

    if not analyze_results:
        raise HTTPException(status_code=400, detail="results が空です")

    rows = _build_bet_rows(analyze_results, bankroll, min_ev, min_prob, max_bets_per_race)

    total_cost = sum(r["total_cost"] for r in rows)
    race_ids = list(dict.fromkeys(r["race_id"] for r in rows))

    return {
        "success": True,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "bankroll": bankroll,
        "summary": {
            "total_cost": total_cost,
            "races": len(race_ids),
            "bets": len(rows),
            "expected_return": round(
                sum(r["total_cost"] * r["expected_value"] for r in rows), 0
            ),
        },
        "bets": rows,
    }


@router.post("/api/export/bet-list/csv")
async def export_bet_list_csv(body: dict):
    """購入推奨リストをCSVファイルとして返す（ダウンロード用）"""
    analyze_results: list[dict] = body.get("results", [])
    bankroll: int = int(body.get("bankroll") or 100000)
    min_ev: float = float(body.get("min_ev") or 1.0)
    min_prob: float = float(body.get("min_prob") or 0.0)
    max_bets_per_race: int = int(body.get("max_bets_per_race") or 5)

    if not analyze_results:
        raise HTTPException(status_code=400, detail="results が空です")

    rows = _build_bet_rows(analyze_results, bankroll, min_ev, min_prob, max_bets_per_race)

    output = io.StringIO()
    fieldnames = [
        "race_id", "race_name", "venue", "race_no", "post_time",
        "bet_type", "bet_type_code", "combination", "horse_names",
        "unit_price", "units", "total_cost",
        "expected_value", "win_probability", "odds", "race_level", "is_best_bet",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        row = {**row, "horse_names": " / ".join(row.get("horse_names") or [])}
        writer.writerow(row)

    filename = f"bet_list_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
