from __future__ import annotations
import argparse
from pathlib import Path
from typing import Optional, Sequence
from dataclasses import dataclass

import pandas as pd

from .config import load_config, AppConfig
from .utils import now_jst, yyyymmdd
from .ingest import ingest_by_date, ingest_one_race
from .train import train
from .netkeiba.client import NetkeibaClient
from .netkeiba.parsers import parse_shutuba_table
from .feature_engineering import add_derived_features
from .db import connect, init_db, load_training_frame


@dataclass
class PredictionFeatureCreator:
    """予測対象レースの特徴量を作成するクラス"""
    race_id: str
    cfg: AppConfig
    features: Optional[pd.DataFrame] = None

    def create_features(self) -> pd.DataFrame:
        """特徴量を作成"""
        try:
            client = NetkeibaClient(self.cfg.netkeiba, self.cfg.storage)
            url = client.build_url(self.cfg.netkeiba.shutuba_url.format(race_id=self.race_id))
            fr = client.fetch_html(url, cache_kind="shutuba", cache_key=self.race_id, use_cache=False)
            df = parse_shutuba_table(fr.text)
            
            # 基本的な前処理
            if df is not None and not df.empty:
                df["race_id"] = self.race_id
                # horse_noをumabanとしても使えるようにする
                if "horse_no" in df.columns:
                    df["umaban"] = df["horse_no"]
                
                # モデルが期待する列名に変更
                rename_map = {}
                if "odds" in df.columns:
                    rename_map["odds"] = "entry_odds"
                if "popularity" in df.columns:
                    rename_map["popularity"] = "entry_popularity"
                
                if rename_map:
                    df = df.rename(columns=rename_map)
                
                # entry_oddsとentry_popularityがない場合はダミー値を設定
                if "entry_odds" not in df.columns:
                    df["entry_odds"] = 1.0  # デフォルト値
                if "entry_popularity" not in df.columns:
                    df["entry_popularity"] = df.index + 1  # 1から順番に
                
                # 派生特徴量を追加（過去データを使用して統計計算）
                try:
                    con = connect(self.cfg.storage.sqlite_path)
                    init_db(con)
                    full_history_df = load_training_frame(con)
                    con.close()
                    
                    if not full_history_df.empty:
                        df = add_derived_features(df, full_history_df=full_history_df)
                    else:
                        print("警告: 過去データがないため、統計特徴量は計算されません")
                        df = add_derived_features(df, full_history_df=None)
                except Exception as e:
                    print(f"派生特徴量計算エラー: {e}")
                    df = add_derived_features(df, full_history_df=None)
                
                self.features = df
            return self.features
        except Exception as e:
            print(f"Failed to create features for {self.race_id}: {e}")
            self.features = pd.DataFrame()
            return self.features


def create_prediction_features(race_id: str, cfg: AppConfig) -> PredictionFeatureCreator:
    """予測用の特徴量を作成する関数"""
    pfc = PredictionFeatureCreator(race_id=race_id, cfg=cfg)
    pfc.create_features()
    return pfc

def run_daily(cfg_path: Path, target_date: str | None = None, train_after: bool = True) -> None:
    cfg = load_config(cfg_path)

    if target_date is None:
        # default: ingest yesterday's results (more likely settled)
        d = now_jst().replace(hour=0, minute=0, second=0, microsecond=0)
        target_date = yyyymmdd(d)

    race_ids = ingest_by_date(cfg_path, target_date)
    print(f"[daily] date={target_date} race_ids={len(race_ids)}")

    # Ingest each race (shutuba+result). Result might not exist yet; that's OK (will just fail).
    ok = 0
    for rid in race_ids:
        try:
            ingest_one_race(cfg_path, rid, fetch_shutuba=True, fetch_result=True)
            ok += 1
        except Exception as e:
            # keep going, but don't hammer; caching + jitter already applied in client
            print(f"[skip] race_id={rid} err={e}")

    print(f"[daily] ingested_ok={ok}/{len(race_ids)}")

    if train_after:
        try:
            model_path = train(cfg_path)
            print(f"[daily] trained model: {model_path}")
        except Exception as e:
            print(f"[daily] train skipped: {e}")

def main(argv: Optional[Sequence[str]] = None) -> None:
    p = argparse.ArgumentParser(description="Daily pipeline: fetch race_ids -> ingest -> (optional) train.")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--date", default=None, help="YYYYMMDD (default: today JST)")
    p.add_argument("--no_train", action="store_true")
    args = p.parse_args(argv)

    run_daily(Path(args.config), target_date=args.date, train_after=not args.no_train)

if __name__ == "__main__":
    main()
