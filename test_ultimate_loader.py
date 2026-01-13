"""
db_ultimate_loader.pyを直接テスト
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent / "keiba"))

from keiba_ai.db_ultimate_loader import load_ultimate_training_frame

db_path = Path("keiba/data/keiba_ultimate.db")

print("【db_ultimate_loader.py テスト】\n")
print(f"DB: {db_path}")
print(f"存在: {db_path.exists()}\n")

df = load_ultimate_training_frame(db_path)

print(f"\n結果:")
print(f"  行数: {len(df)}")
print(f"  列数: {len(df.columns)}")

if len(df) > 0:
    print(f"\n列名（最初10個）:")
    for col in df.columns[:10]:
        print(f"  - {col}")
    
    print(f"\nサンプル（最初3行）:")
    print(df.head(3))
else:
    print("\n✗ DataFrameが空です")
