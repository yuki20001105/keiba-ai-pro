"""
データ確認用デバッグエンドポイント
GET /api/debug/race/{race_id}          - スクレイプ生データ（race_info + 馬エントリー全カラム）
GET /api/debug/race/{race_id}/features - 特徴量エンジニアリング後の全カラム・値
"""
from __future__ import annotations

import json
import sqlite3
import traceback
from typing import Any, Dict, List

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException

from app_config import ULTIMATE_DB, logger  # type: ignore
from deps.auth import require_premium  # type: ignore

router = APIRouter()


def _load_raw_race(race_id: str) -> Dict[str, Any]:
    """races_ultimate + race_results_ultimate から生データを読み込む"""
    db_path = ULTIMATE_DB
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # race_info
    cur.execute("SELECT data FROM races_ultimate WHERE race_id = ?", (race_id,))
    rrow = cur.fetchone()
    if not rrow:
        conn.close()
        raise HTTPException(status_code=404, detail=f"レース {race_id} が races_ultimate に見つかりません")
    race_info = json.loads(rrow[0])

    # horse entries
    cur.execute(
        "SELECT data FROM race_results_ultimate WHERE race_id = ? ORDER BY json_extract(data, '$.horse_number')",
        (race_id,),
    )
    hrows = cur.fetchall()
    conn.close()

    horses = []
    for hr in hrows:
        hd = json.loads(hr[0])
        hd["race_id"] = race_id
        horses.append(hd)

    return {"race_info": race_info, "horses": horses}


def _build_feature_df(race_id: str, race_info: Dict, horses: List[Dict]) -> pd.DataFrame:
    """predict.pyと同じ手順で特徴量エンジニアリングを適用する"""
    from keiba_ai.feature_engineering import add_derived_features  # type: ignore
    from keiba_ai.ultimate_features import UltimateFeatureCalculator  # type: ignore

    # DataFrame組み立て
    records = []
    for hd in horses:
        row = dict(hd)
        for k, v in race_info.items():
            if k not in row or row[k] is None:
                row[k] = v
        records.append(row)

    df = pd.DataFrame(records)

    # カラム名マッピング
    col_map = {
        "finish_position": "finish",
        "finish_time": "time",
        "track_type": "surface",
        "last_3f": "last_3f_time",
        "weight_kg": "horse_weight",
        "weight_change": "horse_weight_change",
        "jockey_weight": "burden_weight",
    }
    for old, new in col_map.items():
        if old in df.columns and new not in df.columns:
            df[new] = df[old]

    # ID補完
    for url_col, id_col, name_col in [
        ("jockey_url", "jockey_id", "jockey_name"),
        ("trainer_url", "trainer_id", "trainer_name"),
        ("horse_url", "horse_id", "horse_name"),
    ]:
        if id_col not in df.columns:
            if url_col in df.columns:
                df[id_col] = df[url_col].str.extract(r"/([^/]+)/?$")[0]
            elif name_col in df.columns:
                df[id_col] = df[name_col]

    # 数値変換
    numeric_cols = [
        "bracket_number", "horse_number", "burden_weight", "odds", "popularity",
        "horse_weight", "age", "distance", "num_horses", "kai", "day",
        "horse_total_runs", "horse_total_wins", "horse_total_prize_money",
        "prev_race_distance", "prev_race_finish", "prev_race_weight",
        "prev2_race_distance", "prev2_race_finish", "prev2_race_weight",
    ]
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # sex/age 抽出
    if "sex_age" in df.columns:
        if "sex" not in df.columns or df["sex"].isna().all():
            df["sex"] = df["sex_age"].str.extract(r"^([牡牝セ])")[0]
        if "age" not in df.columns or df["age"].isna().all():
            df["age"] = pd.to_numeric(df["sex_age"].str.extract(r"(\d+)$")[0], errors="coerce")

    # corner_positions_list
    if "corner_positions" in df.columns and "corner_positions_list" not in df.columns:
        def _parse_cp(s):
            try:
                if pd.isna(s) or s == "":
                    return []
                return [int(x) for x in str(s).split("-") if x.strip().isdigit()]
            except Exception:
                return []
        df["corner_positions_list"] = df["corner_positions"].apply(_parse_cp)

    # 特徴量エンジニアリング
    df = add_derived_features(df, full_history_df=df)
    calculator = UltimateFeatureCalculator(str(ULTIMATE_DB))
    df = calculator.add_ultimate_features(df)
    df = df.loc[:, ~df.columns.duplicated()]

    return df


def _df_to_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """DataFrame を JSON化可能な形式に変換"""
    import math

    def _clean(v: Any) -> Any:
        if isinstance(v, float):
            if math.isnan(v) or math.isinf(v):
                return None
            return round(v, 6)
        if hasattr(v, "item"):  # numpy scalar
            return v.item()
        if isinstance(v, list):
            return [_clean(x) for x in v]
        return v

    records = []
    for _, row in df.iterrows():
        records.append({col: _clean(row[col]) for col in df.columns})
    return records


@router.get("/api/debug/race/{race_id}")
async def debug_raw_race(
    race_id: str,
    current_user: dict = Depends(require_premium),
):
    """スクレイプ生データを返す（race_info + 馬エントリー全カラム）"""
    try:
        raw = _load_raw_race(race_id)
        # race_info のカラム一覧
        race_info_columns = list(raw["race_info"].keys())
        # horse エントリーのカラム一覧（全馬のunion）
        horse_cols_set: set = set()
        for h in raw["horses"]:
            horse_cols_set.update(h.keys())
        horse_columns = sorted(horse_cols_set)

        return {
            "race_id": race_id,
            "race_info_columns": race_info_columns,
            "horse_columns": horse_columns,
            "race_info": raw["race_info"],
            "horses": raw["horses"],
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/debug/race/{race_id}/features")
async def debug_features(
    race_id: str,
    current_user: dict = Depends(require_premium),
):
    """特徴量エンジニアリング後の全カラム・値を返す"""
    try:
        raw = _load_raw_race(race_id)
        df = _build_feature_df(race_id, raw["race_info"], raw["horses"])

        feature_columns = list(df.columns)
        records = _df_to_records(df)

        return {
            "race_id": race_id,
            "feature_count": len(feature_columns),
            "horse_count": len(records),
            "feature_columns": feature_columns,
            "records": records,
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
