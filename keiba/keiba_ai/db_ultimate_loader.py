"""
Ultimate版データ用のDB読み込み関数
race_results_ultimateテーブルからJSON形式でデータを読み込む
"""
import sqlite3
import pandas as pd
import json
from pathlib import Path

def load_ultimate_training_frame(db_path: Path) -> pd.DataFrame:
    """
    race_results_ultimateテーブルからUltimate版データを読み込む
    
    Args:
        db_path: keiba_ultimate.dbのパス
        
    Returns:
        DataFrame with Ultimate features
    """
    # Pathオブジェクトに変換（文字列の場合も対応）
    if not isinstance(db_path, Path):
        db_path = Path(db_path)
    
    print(f"→ Ultimate DBからデータ読み込み: {db_path}")
    print(f"  絶対パス: {db_path.absolute()}")
    print(f"  存在チェック: {db_path.exists()}")
    
    if not db_path.exists():
        print(f"  ✗ DBファイルが見つかりません: {db_path}")
        return pd.DataFrame()
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # テーブル存在確認
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='race_results_ultimate'")
    if not cursor.fetchone():
        print("  ✗ race_results_ultimateテーブルが存在しません")
        conn.close()
        return pd.DataFrame()
    
    # 全データ取得
    cursor.execute("SELECT race_id, data FROM race_results_ultimate")
    rows = cursor.fetchall()
    conn.close()
    
    if len(rows) == 0:
        print("  ✗ データが0件です")
        return pd.DataFrame()
    
    print(f"  ✓ {len(rows)}行取得")
    
    # JSON → DataFrame
    records = []
    for race_id, data_json in rows:
        try:
            data = json.loads(data_json)
            # race_idがデータに含まれていなければ追加
            if 'race_id' not in data:
                data['race_id'] = race_id
            records.append(data)
        except json.JSONDecodeError:
            print(f"  ⚠ JSON解析エラー: race_id={race_id}")
            continue
    
    df = pd.DataFrame(records)
    print(f"  ✓ DataFrame変換: {len(df)}行 × {len(df.columns)}列")
    
    return df
