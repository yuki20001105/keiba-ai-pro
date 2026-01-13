"""
データベース最適化ユーティリティ
バルクインサート機能による高速保存
"""
import sqlite3
import json
from pathlib import Path
from typing import List, Dict
import time


class UltimateDatabaseOptimizer:
    """Ultimate DB用の高速バルクインサート"""
    
    def __init__(self, db_path: str = "keiba_ultimate.db"):
        self.db_path = Path(db_path)
        self._ensure_tables()
    
    def _ensure_tables(self):
        """テーブルが存在しない場合は作成"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # race_results_ultimate テーブル
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS race_results_ultimate (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_id TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # races_ultimate テーブル
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS races_ultimate (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_id TEXT NOT NULL UNIQUE,
                data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # インデックス作成
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_race_results_race_id 
            ON race_results_ultimate(race_id)
        """)
        
        conn.commit()
        conn.close()
    
    def save_races_bulk(self, races_data: List[Dict]) -> Dict:
        """
        複数レースを一括保存（🚀 高速バルクインサート）
        
        Args:
            races_data: [
                {
                    'race_id': '202406010101',
                    'race_info': {...},
                    'results': [{...}, {...}, ...]
                },
                ...
            ]
        
        Returns:
            {
                'races_saved': 10,
                'results_saved': 160,
                'elapsed_seconds': 0.45
            }
        """
        start_time = time.time()
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # トランザクション開始
            cursor.execute("BEGIN TRANSACTION")
            
            # バルクインサート用データ準備
            race_values = []
            result_values = []
            
            for race_data in races_data:
                race_id = race_data['race_id']
                
                # レース情報
                if 'race_info' in race_data:
                    race_json = json.dumps(race_data['race_info'], ensure_ascii=False)
                    race_values.append((race_id, race_json))
                
                # 結果データ
                if 'results' in race_data:
                    for result in race_data['results']:
                        result_json = json.dumps(result, ensure_ascii=False)
                        result_values.append((race_id, result_json))
            
            # 一括実行（executemany）
            if race_values:
                cursor.executemany(
                    "INSERT OR REPLACE INTO races_ultimate (race_id, data) VALUES (?, ?)",
                    race_values
                )
            
            if result_values:
                cursor.executemany(
                    "INSERT INTO race_results_ultimate (race_id, data) VALUES (?, ?)",
                    result_values
                )
            
            conn.commit()
            
            elapsed = time.time() - start_time
            
            return {
                'races_saved': len(race_values),
                'results_saved': len(result_values),
                'elapsed_seconds': round(elapsed, 3),
                'avg_ms_per_race': round(elapsed / len(race_values) * 1000, 2) if race_values else 0
            }
            
        except Exception as e:
            conn.rollback()
            raise e
        
        finally:
            conn.close()
    
    def save_single_race(self, race_data: Dict) -> Dict:
        """1レース保存（従来互換用）"""
        return self.save_races_bulk([race_data])
    
    def get_stats(self) -> Dict:
        """データベース統計情報"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM race_results_ultimate")
        results_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(DISTINCT race_id) FROM race_results_ultimate")
        unique_races = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM races_ultimate")
        races_count = cursor.fetchone()[0]
        
        # ファイルサイズ
        file_size_mb = self.db_path.stat().st_size / (1024 * 1024) if self.db_path.exists() else 0
        
        conn.close()
        
        return {
            'total_results': results_count,
            'unique_races': unique_races,
            'races_table_count': races_count,
            'file_size_mb': round(file_size_mb, 2)
        }


# ============================================
# 使用例
# ============================================

def example_usage():
    """使用例"""
    
    # 初期化
    db = UltimateDatabaseOptimizer("keiba_ultimate.db")
    
    # サンプルデータ
    sample_races = [
        {
            'race_id': '202406010101',
            'race_info': {
                'race_name': '3歳未勝利',
                'distance': '1600m',
                'surface': '芝'
            },
            'results': [
                {
                    'horse_number': 1,
                    'horse_name': 'サンプル馬',
                    'finish_position': 1,
                    'horse_id': '2021012345'
                },
                {
                    'horse_number': 2,
                    'horse_name': 'テスト馬',
                    'finish_position': 2,
                    'horse_id': '2021012346'
                }
            ]
        },
        {
            'race_id': '202406010201',
            'race_info': {
                'race_name': '新馬',
                'distance': '1800m',
                'surface': 'ダート'
            },
            'results': [
                {
                    'horse_number': 1,
                    'horse_name': '新馬A',
                    'finish_position': 1,
                    'horse_id': '2024012347'
                }
            ]
        }
    ]
    
    # 一括保存
    print("一括保存テスト...")
    result = db.save_races_bulk(sample_races)
    print(f"保存完了: {result}")
    
    # 統計情報
    print("\nデータベース統計:")
    stats = db.get_stats()
    for key, value in stats.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    example_usage()
