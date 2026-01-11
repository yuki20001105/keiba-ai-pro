"""
LightGBM最適化: カテゴリカル変数の前処理
ワンホットエンコーディングではなく、LightGBMのネイティブサポートを活用
"""
import pandas as pd
import numpy as np
from typing import List, Tuple, Dict
from sklearn.preprocessing import LabelEncoder


class LightGBMCategoricalPreprocessor:
    """LightGBM向けのカテゴリカル変数前処理クラス"""
    
    def __init__(self):
        self.label_encoders = {}
        self.categorical_features = []
        self.high_cardinality_stats = {}
    
    def fit_transform(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
        """学習データで前処理を実行
        
        Returns:
            処理済みデータフレームとカテゴリカル特徴量のリスト
        """
        df = df.copy()
        
        # ===== 1. 低カーディナリティ変数: Label Encoding =====
        low_cardinality_features = [
            'venue',          # 競馬場（10箇所程度）
            'track_type',     # コース種別（芝/ダート/障害）
            'surface',        # 同上
            'weather',        # 天候（晴/曇/雨/雪など）
            'track_condition',# 馬場状態（良/稍重/重/不良）
            'field_condition',# 同上
            'race_class',     # クラス（新馬/未勝利/1勝など）
            'course_direction',# 回り（右/左/直線）
            'sex',            # 性別（牡/牝/セ）
        ]
        
        for feature in low_cardinality_features:
            if feature in df.columns:
                df[feature] = self._encode_categorical(df, feature)
                self.categorical_features.append(feature)
        
        # ===== 2. 高カーディナリティ変数: 統計特徴量に変換 =====
        # 騎手名 → 騎手の統計情報
        if 'jockey_name' in df.columns or 'jockey_id' in df.columns:
            df = self._add_jockey_statistics(df)
        
        # 調教師名 → 調教師の統計情報
        if 'trainer_name' in df.columns or 'trainer_id' in df.columns:
            df = self._add_trainer_statistics(df)
        
        # 馬名は使用しない（馬IDの統計は既にfeature_engineeringで処理済み）
        
        # ===== 3. 年齢カテゴリなどの派生カテゴリ変数 =====
        # すでにadd_derived_features()で作成されたダミー変数がある場合
        # それらは数値として扱う（LightGBMはバイナリ特徴として最適化）
        
        return df, self.categorical_features
    
    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """テストデータ/予測データで前処理を実行（fitで学習した変換を適用）"""
        df = df.copy()
        
        # 低カーディナリティ変数の変換
        for feature in self.label_encoders.keys():
            if feature in df.columns:
                df[feature] = self._transform_categorical(df, feature)
        
        # 高カーディナリティ変数の統計特徴
        if 'jockey_name' in df.columns or 'jockey_id' in df.columns:
            df = self._add_jockey_statistics(df)
        
        if 'trainer_name' in df.columns or 'trainer_id' in df.columns:
            df = self._add_trainer_statistics(df)
        
        return df
    
    def _encode_categorical(self, df: pd.DataFrame, feature: str) -> pd.Series:
        """カテゴリカル変数をLabel Encoding"""
        if feature not in self.label_encoders:
            self.label_encoders[feature] = LabelEncoder()
            # 欠損値を'unknown'で埋める
            values = df[feature].fillna('unknown').astype(str)
            return pd.Series(
                self.label_encoders[feature].fit_transform(values),
                index=df.index
            )
        else:
            return self._transform_categorical(df, feature)
    
    def _transform_categorical(self, df: pd.DataFrame, feature: str) -> pd.Series:
        """学習済みエンコーダーで変換"""
        values = df[feature].fillna('unknown').astype(str)
        
        # 未知のカテゴリは'unknown'として扱う
        le = self.label_encoders[feature]
        known_classes = set(le.classes_)
        
        # 未知のカテゴリを'unknown'に置き換え
        values = values.apply(lambda x: x if x in known_classes else 'unknown')
        
        return pd.Series(
            le.transform(values),
            index=df.index
        )
    
    def _add_jockey_statistics(self, df: pd.DataFrame) -> pd.DataFrame:
        """騎手の統計特徴量を追加（すでにfeature_engineeringで計算済みの場合はスキップ）"""
        
        # jockey_course_win_rateなどがすでにあればスキップ
        if 'jockey_course_win_rate' in df.columns:
            print("  ℹ️ 騎手統計特徴量は既に存在します")
            return df
        
        # IDベースの処理（名前より安定）
        jockey_col = 'jockey_id' if 'jockey_id' in df.columns else 'jockey_name'
        
        if jockey_col not in df.columns:
            return df
        
        # 簡易的な統計（実際はfeature_engineering.pyで詳細に計算済み）
        print(f"  ℹ️ 騎手統計特徴量を追加: {jockey_col}ベース")
        
        # グループ統計
        jockey_stats = df.groupby(jockey_col).agg({
            'finish': ['mean', 'count'] if 'finish' in df.columns else []
        }).reset_index()
        
        if not jockey_stats.empty:
            jockey_stats.columns = [jockey_col, 'jockey_avg_finish', 'jockey_race_count']
            df = df.merge(jockey_stats, on=jockey_col, how='left')
        
        return df
    
    def _add_trainer_statistics(self, df: pd.DataFrame) -> pd.DataFrame:
        """調教師の統計特徴量を追加（すでにfeature_engineeringで計算済みの場合はスキップ）"""
        
        # trainer_recent_win_rateなどがすでにあればスキップ
        if 'trainer_recent_win_rate' in df.columns:
            print("  ℹ️ 調教師統計特徴量は既に存在します")
            return df
        
        trainer_col = 'trainer_id' if 'trainer_id' in df.columns else 'trainer_name'
        
        if trainer_col not in df.columns:
            return df
        
        print(f"  ℹ️ 調教師統計特徴量を追加: {trainer_col}ベース")
        
        # グループ統計
        trainer_stats = df.groupby(trainer_col).agg({
            'finish': ['mean', 'count'] if 'finish' in df.columns else []
        }).reset_index()
        
        if not trainer_stats.empty:
            trainer_stats.columns = [trainer_col, 'trainer_avg_finish', 'trainer_race_count']
            df = df.merge(trainer_stats, on=trainer_col, how='left')
        
        return df
    
    def get_lgb_params(self) -> Dict:
        """LightGBM用のカテゴリカル特徴量パラメータを返す"""
        if len(self.categorical_features) == 0:
            return {}
        
        # LightGBMにカテゴリカル特徴を指定
        return {
            'categorical_feature': self.categorical_features
        }


def prepare_for_lightgbm(df: pd.DataFrame, 
                         is_training: bool = True,
                         preprocessor: LightGBMCategoricalPreprocessor = None) -> Tuple[pd.DataFrame, LightGBMCategoricalPreprocessor, List[str]]:
    """LightGBM用にデータを準備
    
    Args:
        df: 元のデータフレーム
        is_training: 学習データかどうか
        preprocessor: 既存の前処理器（予測時に使用）
    
    Returns:
        処理済みデータ、前処理器、カテゴリカル特徴量リスト
    """
    print("\n" + "="*80)
    print("  LightGBM用データ前処理")
    print("="*80)
    
    if preprocessor is None:
        preprocessor = LightGBMCategoricalPreprocessor()
    
    if is_training:
        print("\n【学習モード】カテゴリカル変数をLabel Encoding")
        df_processed, categorical_features = preprocessor.fit_transform(df)
        
        print(f"\n  ✓ カテゴリカル特徴量: {len(categorical_features)}個")
        for feat in categorical_features:
            if feat in df_processed.columns:
                n_unique = df_processed[feat].nunique()
                print(f"    - {feat:20s}: {n_unique:3d} unique values")
    else:
        print("\n【予測モード】学習済み変換を適用")
        df_processed = preprocessor.transform(df)
        categorical_features = preprocessor.categorical_features
    
    print("\n  【重要】LightGBMのパラメータに以下を追加してください:")
    print(f"    params['categorical_feature'] = {categorical_features}")
    print("\n  【注意】ワンホットエンコーディングは不要です！")
    print("    LightGBMがカテゴリを最適に分割します")
    
    print("\n" + "="*80)
    
    return df_processed, preprocessor, categorical_features


# ===== 使用例 =====
if __name__ == "__main__":
    print("""
    ■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■
      LightGBM最適化ガイド: カテゴリカル変数処理
    ■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■
    
    【❌ やってはいけないこと】
    ✗ 騎手名/調教師名/馬名をワンホットエンコーディング
      → 特徴量が爆発的に増える（数千〜数万列）
      → メモリ不足、過学習、学習時間激増
    
    【✅ 推奨される方法】
    
    1. 低カーディナリティ（10種類未満）
       → Label Encoding + LightGBMのcategorical_feature
       
       例: 競馬場、コース種別、天候、馬場状態、クラス
       
       venue:          [東京, 中山, 阪神...] → [0, 1, 2...]
       track_type:     [芝, ダート]         → [0, 1]
       weather:        [晴, 曇, 雨]         → [0, 1, 2]
       track_condition:[良, 稍重, 重, 不良] → [0, 1, 2, 3]
    
    2. 高カーディナリティ（100種類以上）
       → 統計特徴量に変換
       
       例: 騎手名、調教師名
       
       騎手名 → jockey_win_rate, jockey_course_win_rate
       調教師名 → trainer_recent_win_rate, trainer_avg_finish
    
    3. 超高カーディナリティ（数千〜数万種類）
       → 使用しない or ID統計のみ
       
       例: 馬名
       
       馬名 → 使用しない
       馬ID → horse_distance_win_rate（過去の統計）
    
    【LightGBMでの使い方】
    
    ```python
    # 1. データ準備
    df_processed, preprocessor, cat_features = prepare_for_lightgbm(
        df_train, 
        is_training=True
    )
    
    # 2. 特徴量とターゲットに分割
    X = df_processed.drop(['target', 'horse_name', ...], axis=1)
    y = df_processed['target']
    
    # 3. LightGBMのパラメータ設定
    params = {
        'objective': 'binary',
        'metric': 'auc',
        'categorical_feature': cat_features,  # ← これが重要！
        'max_cat_to_onehot': 4,  # 4種類以下は自動でワンホット化
        'verbosity': -1
    }
    
    # 4. 学習
    import lightgbm as lgb
    train_data = lgb.Dataset(X, y, categorical_feature=cat_features)
    model = lgb.train(params, train_data, num_boost_round=100)
    ```
    
    【効果】
    ✓ メモリ使用量: 10分の1以下
    ✓ 学習速度: 5〜10倍高速
    ✓ 予測精度: 同等 or 向上（過学習が減少）
    ✓ 解釈性: カテゴリがそのまま分岐に使われる
    
    ■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■
    """)
