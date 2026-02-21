import sys
from pathlib import Path

# validation/ から実行しても root から実行しても動作するよう __file__ ベースで解決
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / 'keiba'))
from keiba_ai.db_ultimate_loader import load_ultimate_training_frame
from keiba_ai.feature_engineering import add_derived_features

db = _ROOT / 'keiba' / 'data' / 'keiba_ultimate.db'
df = load_ultimate_training_frame(db)
df2 = add_derived_features(df, full_history_df=df)

obj_cols = df2.select_dtypes(include='object').columns.tolist()
num_cols = df2.select_dtypes(include='number').columns.tolist()
print(f'\n特徴量合計: {len(df2.columns)}列')
print(f'  数値型: {len(num_cols)}列')
print(f'  文字列型(除外対象): {len(obj_cols)}列')
print(f'  除外対象カラム: {obj_cols}')
print(f'\n有効特徴量(モデルに使える): {len(num_cols)}列')

key_cols = ['distance', 'surface', 'horse_id', 'jockey_id', 'trainer_id',
            'horse_distance_win_rate', 'jockey_course_win_rate', 'corner_position_avg',
            'straight_length', 'inner_advantage', 'weight', 'horse_weight']
print('\n重要カラムの確認:')
for c in key_cols:
    status = 'OK' if c in df2.columns else 'MISSING'
    print(f'  {c}: {status}')
