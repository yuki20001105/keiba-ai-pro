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
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app_config import logger  # type: ignore

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


def _build_bet_rows(
    analyze_results: list[dict],
    bankroll: int,
    min_ev: float,
    min_prob: float,
    max_bets_per_race: int,
) -> list[dict]:
    """analyze_race レスポンスのリストから購入推奨行を生成する"""
    rows: list[dict] = []

    for result in analyze_results:
        if not result.get("success"):
            continue

        race_info = result.get("race_info") or {}
        race_id = race_info.get("race_id") or result.get("race_id", "")
        race_name = race_info.get("race_name", "")
        venue = race_info.get("venue", "")
        race_no = race_info.get("race_no", 0)
        post_time = race_info.get("post_time", "")

        predictions: list[dict] = result.get("predictions", [])
        bet_types: dict[str, Any] = result.get("bet_types") or {}
        best_bet_type: str = result.get("best_bet_type") or ""
        race_level: str = result.get("race_level") or "normal"
        best_bet_info: dict = result.get("best_bet_info") or {}

        # ---- bet_types から買い目を展開 ----
        _horse_map: dict[str, str] = {
            str(p.get("horse_number") or p.get("horse_no", "")): p.get("horse_name", "")
            for p in predictions
        }

        added = 0
        for bet_label, candidates_info in bet_types.items():
            if added >= max_bets_per_race:
                break

            candidates: list[dict] = []
            if isinstance(candidates_info, dict):
                candidates = candidates_info.get("combinations") or candidates_info.get("horses") or []
            elif isinstance(candidates_info, list):
                candidates = candidates_info

            for cand in candidates:
                if added >= max_bets_per_race:
                    break

                ev: float = cand.get("expected_value") or 0.0
                prob: float = cand.get("probability") or 0.0
                comb: str = str(cand.get("combination") or cand.get("combo") or "")
                odds: float | None = cand.get("odds")
                unit_price: int = int(cand.get("unit_price") or 100)
                units: int = int(cand.get("units") or cand.get("count") or 1)

                if ev < min_ev:
                    continue
                if prob < min_prob:
                    continue
                if not comb:
                    continue

                # 馬番リストから馬名を解決
                nums = re.split(r"[-→]", comb)
                horse_names = [_horse_map.get(n.strip(), f"#{n.strip()}") for n in nums]

                rows.append({
                    "race_id": race_id,
                    "race_name": race_name,
                    "venue": venue,
                    "race_no": race_no,
                    "post_time": post_time,
                    "bet_type": bet_label,
                    "bet_type_code": BET_TYPE_TO_CODE.get(bet_label, bet_label.lower()),
                    "combination": comb,
                    "horse_names": horse_names,
                    "unit_price": unit_price,
                    "units": units,
                    "total_cost": unit_price * units,
                    "expected_value": round(ev, 3),
                    "win_probability": round(prob, 4),
                    "odds": odds,
                    "race_level": race_level,
                    "is_best_bet": bet_label == best_bet_type,
                })
                added += 1

    return rows


import re  # noqa: E402 (上のimportブロック後に置く)


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
