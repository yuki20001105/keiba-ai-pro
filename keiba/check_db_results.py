"""
DBに結果データ（finish列）があるレースを確認
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from keiba_ai.config import load_config
from keiba_ai.db import connect, init_db
import pandas as pd

def check_results_in_db():
    """DBに結果データがあるレースを確認"""
    cfg = load_config("config.yaml")
    con = connect(cfg.storage.sqlite_path)
    init_db(con)
    
    print("=" * 80)
    print("DB内の結果データ（finish列）を確認")
    print("=" * 80)
    print()
    
    # 結果データがあるレースを取得（results テーブルから）
    query = """
    SELECT DISTINCT r.race_id, COUNT(*) as horse_count
    FROM results r
    WHERE r.finish IS NOT NULL
    GROUP BY r.race_id
    ORDER BY r.race_id DESC
    LIMIT 20
    """
    
    results_df = pd.read_sql(query, con)
    
    if results_df.empty:
        print("❌ DBに結果データ（finish列）があるレースが見つかりません")
        print()
        print("対処方法:")
        print("1. 「1_データ取得」ページでレース結果を取得してください")
        print("2. または、register_to_db.py を実行してデータを登録してください")
    else:
        print(f"✅ 結果データがあるレース: {len(results_df)} 件")
        print()
        print("レースID一覧:")
        print("-" * 80)
        for _, row in results_df.iterrows():
            race_id = row['race_id']
            horse_count = row['horse_count']
            
            # 1着、2着、3着の馬番を取得
            detail_query = f"""
            SELECT e.horse_no as umaban, r.finish, r.odds
            FROM results r
            LEFT JOIN entries e ON r.race_id = e.race_id AND r.horse_id = e.horse_id
            WHERE r.race_id = '{race_id}' AND r.finish IN (1, 2, 3)
            ORDER BY r.finish
            """
            detail_df = pd.read_sql(detail_query, con)
            
            if not detail_df.empty:
                winner = detail_df[detail_df['finish'] == 1]['umaban'].iloc[0] if 1 in detail_df['finish'].values else "?"
                second = detail_df[detail_df['finish'] == 2]['umaban'].iloc[0] if 2 in detail_df['finish'].values else "?"
                third = detail_df[detail_df['finish'] == 3]['umaban'].iloc[0] if 3 in detail_df['finish'].values else "?"
                
                print(f"{race_id} - {horse_count}頭 | 結果: 1着={winner}番, 2着={second}番, 3着={third}番")
            else:
                print(f"{race_id} - {horse_count}頭 | 結果: データなし")
        
        print("-" * 80)
        print()
        print("✅ これらのレースで予測の可視化機能が使えます")
        print("   「3_予測」ページ → 「📋 DB登録済みレースから選択」→ 上記のレースIDを選択")
    
    con.close()
    
    print()
    print("=" * 80)

if __name__ == "__main__":
    check_results_in_db()
