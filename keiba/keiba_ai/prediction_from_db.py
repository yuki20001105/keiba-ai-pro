"""
データベースから特徴量を作成して予測を行う
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional
import pandas as pd

from .config import load_config, AppConfig
from .db import connect, init_db, load_training_frame
from .feature_engineering import add_derived_features


def load_features_from_db(race_id: str, cfg: AppConfig) -> pd.DataFrame:
    """
    データベースからレース特徴量を取得
    
    Parameters
    ----------
    race_id : str
        レースID
    cfg : AppConfig
        設定
    
    Returns
    -------
    pd.DataFrame
        特徴量データフレーム
    """
    con = connect(cfg.storage.sqlite_path)
    init_db(con)
    
    # entriesテーブルからデータを取得
    query = """
    SELECT 
        e.*,
        r.finish,
        r.odds as result_odds,
        r.popularity as result_popularity
    FROM entries e
    LEFT JOIN results r ON e.race_id = r.race_id AND e.horse_id = r.horse_id
    WHERE e.race_id = ?
    ORDER BY e.horse_no
    """
    
    df = pd.read_sql(query, con, params=(race_id,))
    
    # 派生特徴量計算用に全履歴データを取得
    full_history_df = load_training_frame(con)
    con.close()
    
    if df.empty:
        return pd.DataFrame()
    
    # 予測用の特徴量を準備
    df["race_id"] = race_id
    
    # horse_noをumabanとしても使えるようにする
    if "horse_no" in df.columns:
        df["umaban"] = df["horse_no"]
    
    # entry_oddsとentry_popularityをresultから取得（もし存在すれば）
    if "result_odds" in df.columns and df["result_odds"].notna().any():
        df["entry_odds"] = df["result_odds"]
    if "result_popularity" in df.columns and df["result_popularity"].notna().any():
        df["entry_popularity"] = df["result_popularity"]
    
    # 派生特徴量を追加
    try:
        df = add_derived_features(df, full_history_df=full_history_df)
    except Exception as e:
        print(f"派生特徴量計算エラー: {e}")
        df = add_derived_features(df, full_history_df=None)
    
    return df
