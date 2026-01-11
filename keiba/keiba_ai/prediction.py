"""
機械学習モデルを使用して馬券の的中確率と期待値を計算するモジュール
"""
from __future__ import annotations
import joblib
from itertools import combinations
from pathlib import Path

import pandas as pd
import numpy as np
import yaml

from .extract_odds import RealtimeOdds


class Predictor:
    """予測モデルを利用して馬券候補を作成するクラス"""

    def __init__(
        self,
        race_id: str,
        realtime_odds: RealtimeOdds,
        model_dir: Path = Path("data/03_train"),
        model_filename: str = "model_latest.joblib",
        config_filepath: Path = Path("config.yaml"),
    ):
        """
        予測モデルを利用して馬券候補を作成するクラスの初期化

        Parameters
        ----------
        race_id : str
            対象レースID（例：202505020301）
        realtime_odds : RealtimeOdds
            直前オッズ取得済みのRealtimeOddsインスタンス
        model_dir : Path, optional
            モデルファイルが格納されているディレクトリ
        model_filename : str, optional
            モデルファイル名
        config_filepath : Path, optional
            学習時の設定ファイルのパス
        """
        self.race_id = race_id
        self.realtime_odds = realtime_odds
        self.prediction_df: pd.DataFrame | None = None

        # モデルの読み込み
        model_path = Path(model_dir) / model_filename
        bundle = joblib.load(model_path)
        self.model = bundle["model"]
        self.feature_cols_num = bundle.get("feature_cols_num", [])
        self.feature_cols_cat = bundle.get("feature_cols_cat", [])
        self.feature_cols = self.feature_cols_num + self.feature_cols_cat

    def create_prediction_df(self, features: pd.DataFrame) -> pd.DataFrame:
        """
        特徴量データを元に予測結果のデータフレームを作成する

        Parameters
        ----------
        features : pd.DataFrame
            特徴量データフレーム

        Returns
        -------
        pd.DataFrame
            予測結果のデータフレーム（race_id, umaban, predを含む）
        """
        features_copy = features.copy()

        # 単勝オッズを追加（umabanとオッズのキーの型を統一）
        if "umaban" in features_copy.columns:
            # umabanを整数型に変換
            features_copy["umaban"] = features_copy["umaban"].astype(int)
            # オッズ辞書のキーも整数に変換してマッピング
            tansho_odds_dict = {int(k): v for k, v in self.realtime_odds.tansho.items()}
            features_copy["tansho_odds"] = features_copy["umaban"].map(tansho_odds_dict)
        elif "horse_no" in features_copy.columns:
            # horse_noをumabanとして使用
            features_copy["umaban"] = features_copy["horse_no"].astype(int)
            tansho_odds_dict = {int(k): v for k, v in self.realtime_odds.tansho.items()}
            features_copy["tansho_odds"] = features_copy["umaban"].map(tansho_odds_dict)
        else:
            raise ValueError("特徴量に'umaban'または'horse_no'カラムが見つかりません")
        
        features_copy["popularity"] = features_copy["tansho_odds"].rank(ascending=True, method="min")

        # 必要な特徴量を保持
        prediction_df = features_copy[["race_id", "umaban"]].copy()

        # モデルが期待する全ての特徴量を用意（欠損カラムは0で補完）
        for col in self.feature_cols:
            if col not in features_copy.columns:
                features_copy[col] = 0  # 欠損カラムは0で補完
        
        # 特徴量を正しい順序で抽出
        X = features_copy[self.feature_cols].fillna(0)

        # 予測（確率）
        prediction_df["pred"] = self.model.predict_proba(X)[:, 1]
        self.prediction_df = prediction_df

        return prediction_df

    def create_candidates_tansho(
        self,
        pred_col: str = "pred",
        min_pred: float = 0.0,
        bet_amount: int = 100,
    ) -> pd.DataFrame:
        """
        単勝の馬券候補を作成する

        Parameters
        ----------
        pred_col : str, optional
            予測勝率のカラム名
        min_pred : float, optional
            予測勝率による足切り
        bet_amount : int, optional
            購入金額

        Returns
        -------
        pd.DataFrame
            単勝の馬券候補
        """
        if self.prediction_df is None:
            raise ValueError("create_prediction_df() を先に実行してください")

        df = self.prediction_df.copy()
        df = df[df[pred_col] >= min_pred].copy()

        candidates = []
        for _, row in df.iterrows():
            umaban = int(row["umaban"])
            pred = row[pred_col]
            odds = self.realtime_odds.tansho.get(umaban, np.nan)

            if pd.isna(odds) or odds == 0:
                continue

            expect_return = pred * odds

            candidates.append({
                "race_id": self.race_id,
                "bet_type": "単勝",
                "umaban": [umaban],
                "amount": bet_amount,
                "odds": odds,
                "prob": pred,
                "expect_return": expect_return,
            })

        return pd.DataFrame(candidates)

    def create_candidates_umatan(
        self,
        pred_col: str = "pred",
        min_pred_sum: float = 0.0,
        bet_amount: int = 100,
    ) -> pd.DataFrame:
        """
        馬単の馬券候補を作成する

        Parameters
        ----------
        pred_col : str, optional
            予測勝率のカラム名
        min_pred_sum : float, optional
            予測勝率の合計による足切り（デフォルト0：使用しない）
        bet_amount : int, optional
            購入金額

        Returns
        -------
        pd.DataFrame
            馬単の馬券候補
        """
        if self.prediction_df is None:
            raise ValueError("create_prediction_df() を先に実行してください")

        df = self.prediction_df.copy()
        candidates = []

        for (_, row1), (_, row2) in combinations(df.iterrows(), 2):
            uma1 = int(row1["umaban"])
            uma2 = int(row2["umaban"])
            pred1 = row1[pred_col]
            pred2 = row2[pred_col]

            # 1着→2着と2着→1着の両方を考える
            for first, second, p_first, p_second in [
                (uma1, uma2, pred1, pred2),
                (uma2, uma1, pred2, pred1),
            ]:
                key = f"{first:02d},{second:02d}"
                odds = self.realtime_odds.umatan.get(key, np.nan)

                prob = p_first * p_second
                if pd.isna(odds) or odds == 0 or (min_pred_sum > 0 and prob < min_pred_sum):
                    continue

                expect_return = prob * odds

                candidates.append({
                    "race_id": self.race_id,
                    "bet_type": "馬単",
                    "umaban": [first, second],
                    "amount": bet_amount,
                    "odds": odds,
                    "prob": prob,
                    "expect_return": expect_return,
                })

        return pd.DataFrame(candidates)

    def create_candidates_umaren(
        self,
        pred_col: str = "pred",
        min_pred_sum: float = 0.0,
        bet_amount: int = 100,
    ) -> pd.DataFrame:
        """
        馬連の馬券候補を作成する

        Parameters
        ----------
        pred_col : str, optional
            予測勝率のカラム名
        min_pred_sum : float, optional
            予測勝率の合計による足切り
        bet_amount : int, optional
            購入金額

        Returns
        -------
        pd.DataFrame
            馬連の馬券候補
        """
        if self.prediction_df is None:
            raise ValueError("create_prediction_df() を先に実行してください")

        df = self.prediction_df.copy()
        candidates = []

        for (_, row1), (_, row2) in combinations(df.iterrows(), 2):
            uma1 = int(row1["umaban"])
            uma2 = int(row2["umaban"])
            pred1 = row1[pred_col]
            pred2 = row2[pred_col]

            key = f"{min(uma1, uma2):02d},{max(uma1, uma2):02d}"
            odds = self.realtime_odds.umaren.get(key, np.nan)

            prob = pred1 * pred2 + pred2 * pred1  # 1着2着の順序を気にしない

            if pd.isna(odds) or odds == 0 or prob < min_pred_sum:
                continue

            expect_return = prob * odds

            candidates.append({
                "race_id": self.race_id,
                "bet_type": "馬連",
                "umaban": sorted([uma1, uma2]),
                "amount": bet_amount,
                "odds": odds,
                "prob": prob,
                "expect_return": expect_return,
            })

        return pd.DataFrame(candidates)

    def create_candidates_sanrentan(
        self,
        pred_col: str = "pred",
        min_pred_sum: float = 0.0,
        bet_amount: int = 100,
    ) -> pd.DataFrame:
        """
        三連単の馬券候補を作成する

        Parameters
        ----------
        pred_col : str, optional
            予測勝率のカラム名
        min_pred_sum : float, optional
            予測勝率の合計による足切り
        bet_amount : int, optional
            購入金額

        Returns
        -------
        pd.DataFrame
            三連単の馬券候補
        """
        if self.prediction_df is None:
            raise ValueError("create_prediction_df() を先に実行してください")

        df = self.prediction_df.copy()
        candidates = []

        for uma_list in combinations(df.iterrows(), 3):
            umas = [int(row["umaban"]) for idx, row in uma_list]
            preds = [row[pred_col] for idx, row in uma_list]

            # 3つの馬の順列を全て考える
            from itertools import permutations

            for perm in permutations(range(3)):
                first_idx, second_idx, third_idx = perm
                first = umas[first_idx]
                second = umas[second_idx]
                third = umas[third_idx]
                p_first = preds[first_idx]
                p_second = preds[second_idx]
                p_third = preds[third_idx]

                key = f"{first:02d},{second:02d},{third:02d}"
                odds = self.realtime_odds.sanrentan.get(key, np.nan)

                prob = p_first * p_second * p_third

                if pd.isna(odds) or odds == 0 or prob < min_pred_sum:
                    continue

                expect_return = prob * odds

                candidates.append({
                    "race_id": self.race_id,
                    "bet_type": "三連単",
                    "umaban": [first, second, third],
                    "amount": bet_amount,
                    "odds": odds,
                    "prob": prob,
                    "expect_return": expect_return,
                })

        return pd.DataFrame(candidates)

    def create_candidates_sanrenpuku(
        self,
        pred_col: str = "pred",
        min_pred_sum: float = 0.0,
        bet_amount: int = 100,
    ) -> pd.DataFrame:
        """
        三連複の馬券候補を作成する

        Parameters
        ----------
        pred_col : str, optional
            予測勝率のカラム名
        min_pred_sum : float, optional
            予測勝率の合計による足切り
        bet_amount : int, optional
            購入金額

        Returns
        -------
        pd.DataFrame
            三連複の馬券候補
        """
        if self.prediction_df is None:
            raise ValueError("create_prediction_df() を先に実行してください")

        df = self.prediction_df.copy()
        candidates = []

        for uma_comb in combinations(df.iterrows(), 3):
            umas = [int(row["umaban"]) for idx, row in uma_comb]
            preds = [row[pred_col] for idx, row in uma_comb]

            key = f"{umas[0]:02d},{umas[1]:02d},{umas[2]:02d}"
            odds = self.realtime_odds.sanrenpuku.get(key, np.nan)

            # 三連複は順序を気にしない - 全順列の確率を足す
            from itertools import permutations

            prob = 0.0
            for perm in permutations(range(3)):
                prob += preds[perm[0]] * preds[perm[1]] * preds[perm[2]]

            if pd.isna(odds) or odds == 0 or prob < min_pred_sum:
                continue

            expect_return = prob * odds

            candidates.append({
                "race_id": self.race_id,
                "bet_type": "三連複",
                "umaban": sorted(umas),
                "amount": bet_amount,
                "odds": odds,
                "prob": prob,
                "expect_return": expect_return,
            })

        return pd.DataFrame(candidates)
