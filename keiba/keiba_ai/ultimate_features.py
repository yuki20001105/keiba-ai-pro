"""
Ultimate版特徴量計算モジュール
過去10走統計を自動計算
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional
import sqlite3
from pathlib import Path


class UltimateFeatureCalculator:
    """Ultimate版特徴量（過去10走統計）を計算"""
    
    def __init__(self, db_path: str):
        """
        Args:
            db_path: SQLiteデータベースのパス
        """
        self.db_path = Path(db_path)
        
    def calculate_horse_past_10_races(self, horse_id: str, current_race_id: str) -> Dict:
        """
        馬の過去10走統計を計算
        
        Args:
            horse_id: 馬ID
            current_race_id: 現在のレースID（これより前のレースを集計）
        
        Returns:
            過去10走統計の辞書
        """
        conn = sqlite3.connect(self.db_path)
        
        # 過去10走のデータを取得（現在のレースより前）
        query = """
        SELECT 
            r.finish,
            r.time,
            r.margin,
            r.last3f,
            e.odds,
            e.popularity,
            races.distance,
            races.surface
        FROM race_results r
        JOIN entries e ON r.race_id = e.race_id AND r.horse_number = e.horse_number
        JOIN races ON r.race_id = races.race_id
        WHERE e.horse_id = ?
          AND r.race_id < ?
          AND r.finish IS NOT NULL
        ORDER BY r.race_id DESC
        LIMIT 10
        """
        
        df = pd.read_sql_query(query, conn, params=(horse_id, current_race_id))
        conn.close()
        
        if len(df) == 0:
            return self._get_default_stats()
        
        # 統計計算
        stats = {
            # 基本統計
            'past_10_races_count': len(df),
            'past_10_avg_finish': df['finish'].mean(),
            'past_10_std_finish': df['finish'].std() if len(df) > 1 else 0.0,
            'past_10_best_finish': df['finish'].min(),
            'past_10_worst_finish': df['finish'].max(),
            
            # 勝率・複勝率
            'past_10_win_rate': (df['finish'] == 1).sum() / len(df),
            'past_10_place_rate': (df['finish'] <= 2).sum() / len(df),
            'past_10_show_rate': (df['finish'] <= 3).sum() / len(df),
            
            # 人気
            'past_10_avg_popularity': df['popularity'].mean() if 'popularity' in df.columns else 0.0,
            
            # 最近のトレンド（直近3走 vs それ以前）
            'recent_3_avg_finish': df.head(3)['finish'].mean() if len(df) >= 3 else df['finish'].mean(),
            'past_7_avg_finish': df.tail(7)['finish'].mean() if len(df) >= 10 else 0.0,
            
            # 一貫性（標準偏差が小さいほど安定）
            'finish_consistency': 1 / (1 + df['finish'].std()) if len(df) > 1 else 0.5,
            
            # 最近の調子（直近3走の平均着順が良いほど高スコア）
            'recent_form_score': max(0, 10 - df.head(3)['finish'].mean()) / 10 if len(df) >= 3 else 0.0,
        }
        
        return stats
    
    def calculate_jockey_stats(self, jockey_id: str, current_race_id: str, days: int = 180) -> Dict:
        """
        騎手の最近の統計を計算
        
        Args:
            jockey_id: 騎手ID
            current_race_id: 現在のレースID
            days: 集計期間（日数）
        
        Returns:
            騎手統計の辞書
        """
        conn = sqlite3.connect(self.db_path)
        
        current_date = current_race_id[:8]
        from datetime import datetime, timedelta
        cutoff_date = (datetime.strptime(current_date, '%Y%m%d') - timedelta(days=days)).strftime('%Y%m%d')
        
        query = """
        SELECT 
            r.finish,
            e.popularity
        FROM race_results r
        JOIN entries e ON r.race_id = e.race_id AND r.horse_number = e.horse_number
        WHERE e.jockey_id = ?
          AND r.race_id >= ?
          AND r.race_id < ?
          AND r.finish IS NOT NULL
        """
        
        df = pd.read_sql_query(query, conn, params=(jockey_id, cutoff_date, current_race_id))
        conn.close()
        
        if len(df) == 0:
            return {
                'jockey_recent_win_rate': 0.0,
                'jockey_recent_place_rate': 0.0,
                'jockey_recent_show_rate': 0.0,
                'jockey_recent_races': 0,
                'jockey_avg_finish': 0.0,
            }
        
        return {
            'jockey_recent_win_rate': (df['finish'] == 1).sum() / len(df),
            'jockey_recent_place_rate': (df['finish'] <= 2).sum() / len(df),
            'jockey_recent_show_rate': (df['finish'] <= 3).sum() / len(df),
            'jockey_recent_races': len(df),
            'jockey_avg_finish': df['finish'].mean(),
        }
    
    def calculate_trainer_stats(self, trainer_id: str, current_race_id: str, days: int = 180) -> Dict:
        """
        調教師の最近の統計を計算
        """
        conn = sqlite3.connect(self.db_path)
        
        current_date = current_race_id[:8]
        from datetime import datetime, timedelta
        cutoff_date = (datetime.strptime(current_date, '%Y%m%d') - timedelta(days=days)).strftime('%Y%m%d')
        
        query = """
        SELECT 
            r.finish
        FROM race_results r
        JOIN entries e ON r.race_id = e.race_id AND r.horse_number = e.horse_number
        WHERE e.trainer_id = ?
          AND r.race_id >= ?
          AND r.race_id < ?
          AND r.finish IS NOT NULL
        """
        
        df = pd.read_sql_query(query, conn, params=(trainer_id, cutoff_date, current_race_id))
        conn.close()
        
        if len(df) == 0:
            return {
                'trainer_recent_win_rate': 0.0,
                'trainer_recent_place_rate': 0.0,
                'trainer_recent_show_rate': 0.0,
                'trainer_recent_races': 0,
            }
        
        return {
            'trainer_recent_win_rate': (df['finish'] == 1).sum() / len(df),
            'trainer_recent_place_rate': (df['finish'] <= 2).sum() / len(df),
            'trainer_recent_show_rate': (df['finish'] <= 3).sum() / len(df),
            'trainer_recent_races': len(df),
        }
    
    def add_ultimate_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        データフレームにUltimate版特徴量を追加
        
        Args:
            df: 入力データフレーム（horse_id, jockey_id, trainer_id, race_id必須）
        
        Returns:
            Ultimate特徴量が追加されたデータフレーム
        """
        df = df.copy()
        
        print(f"\n🚀 Ultimate版特徴量計算開始: {len(df)}行")
        
        # 馬の過去10走統計
        if 'horse_id' in df.columns and 'race_id' in df.columns:
            print("  - 馬の過去10走統計を計算中...")
            horse_stats_list = []
            for idx, row in df.iterrows():
                stats = self.calculate_horse_past_10_races(row['horse_id'], row['race_id'])
                horse_stats_list.append(stats)
            
            horse_stats_df = pd.DataFrame(horse_stats_list)
            df = pd.concat([df, horse_stats_df], axis=1)
            print(f"    ✓ {len(horse_stats_df.columns)}個の特徴量を追加")
        
        # 騎手統計
        if 'jockey_id' in df.columns:
            print("  - 騎手統計を計算中...")
            jockey_stats_list = []
            for idx, row in df.iterrows():
                stats = self.calculate_jockey_stats(row['jockey_id'], row['race_id'])
                jockey_stats_list.append(stats)
            
            jockey_stats_df = pd.DataFrame(jockey_stats_list)
            df = pd.concat([df, jockey_stats_df], axis=1)
            print(f"    ✓ {len(jockey_stats_df.columns)}個の特徴量を追加")
        
        # 調教師統計
        if 'trainer_id' in df.columns:
            print("  - 調教師統計を計算中...")
            trainer_stats_list = []
            for idx, row in df.iterrows():
                stats = self.calculate_trainer_stats(row['trainer_id'], row['race_id'])
                trainer_stats_list.append(stats)
            
            trainer_stats_df = pd.DataFrame(trainer_stats_list)
            df = pd.concat([df, trainer_stats_df], axis=1)
            print(f"    ✓ {len(trainer_stats_df.columns)}個の特徴量を追加")
        
        print(f"✓ Ultimate版特徴量計算完了: 合計{len(df.columns)}列")
        
        return df
    
    def _get_default_stats(self) -> Dict:
        """デフォルトの統計値（データがない場合）"""
        return {
            'past_10_races_count': 0,
            'past_10_avg_finish': 0.0,
            'past_10_std_finish': 0.0,
            'past_10_best_finish': 0,
            'past_10_worst_finish': 0,
            'past_10_win_rate': 0.0,
            'past_10_place_rate': 0.0,
            'past_10_show_rate': 0.0,
            'past_10_avg_popularity': 0.0,
            'recent_3_avg_finish': 0.0,
            'past_7_avg_finish': 0.0,
            'finish_consistency': 0.0,
            'recent_form_score': 0.0,
        }


def test_ultimate_features():
    """テスト実行"""
    import sys
    from pathlib import Path
    
    # データベースパス
    db_path = Path(__file__).parent / "keiba" / "data" / "keiba.db"
    
    if not db_path.exists():
        print(f"❌ データベースが見つかりません: {db_path}")
        return
    
    calculator = UltimateFeatureCalculator(str(db_path))
    
    # テストデータ作成
    test_df = pd.DataFrame({
        'race_id': ['202401010101', '202401010102'],
        'horse_id': ['2020100001', '2020100002'],
        'jockey_id': ['00001', '00002'],
        'trainer_id': ['00001', '00002'],
    })
    
    print("\n" + "="*80)
    print("Ultimate版特徴量テスト")
    print("="*80)
    
    result_df = calculator.add_ultimate_features(test_df)
    
    print(f"\n結果:")
    print(f"  入力: {len(test_df.columns)}列")
    print(f"  出力: {len(result_df.columns)}列")
    print(f"  追加: {len(result_df.columns) - len(test_df.columns)}列")
    
    print(f"\n追加された列:")
    new_cols = [col for col in result_df.columns if col not in test_df.columns]
    for i, col in enumerate(new_cols, 1):
        print(f"  {i}. {col}")


if __name__ == "__main__":
    test_ultimate_features()
