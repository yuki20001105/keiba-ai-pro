"""
学習に使用されるデータの詳細を確認するスクリプト
"""
from pathlib import Path
import pandas as pd
from keiba_ai.config import load_config
from keiba_ai.db import connect, init_db, load_training_frame

def main():
    cfg = load_config("config.yaml")
    con = connect(cfg.storage.sqlite_path)
    init_db(con)
    
    # 学習用データフレームを取得
    df = load_training_frame(con)
    
    print("=" * 60)
    print("学習データ統計")
    print("=" * 60)
    
    if df.empty:
        print("❌ 学習データが見つかりません")
        con.close()
        return
    
    print(f"\n総データ数: {len(df)} エントリー")
    
    # レース数
    race_count = df['race_id'].nunique()
    print(f"レース数: {race_count}")
    
    # 日付範囲
    if 'race_id' in df.columns:
        dates = pd.to_datetime(df['race_id'].str[:8], format='%Y%m%d', errors='coerce')
        dates = dates.dropna()
        if not dates.empty:
            print(f"期間: {dates.min().date()} ～ {dates.max().date()}")
            print(f"日数: {(dates.max() - dates.min()).days} 日")
    
    # ターゲット変数の分布
    if 'finish' in df.columns:
        print(f"\n【着順分布】")
        # sort_indexではなくvalue_counts()のみ（混在型対応）
        finish_counts = df['finish'].value_counts()
        for finish, count in finish_counts.items():
            print(f"  {finish}: {count}頭")
        
        # 勝率予測用のクラス分布
        win_count = (df['finish'] == 1).sum()
        lose_count = (df['finish'] != 1).sum()
        print(f"\n【勝率予測のクラス分布】")
        print(f"  勝ち（1着）: {win_count}頭 ({win_count/len(df)*100:.1f}%)")
        print(f"  負け（2着以下）: {lose_count}頭 ({lose_count/len(df)*100:.1f}%)")
        
        # 3着内予測用のクラス分布（数値のみでフィルタ）
        numeric_finish = pd.to_numeric(df['finish'], errors='coerce')
        place3_count = (numeric_finish <= 3).sum()
        out_of_place3 = (numeric_finish > 3).sum()
        non_numeric_count = numeric_finish.isna().sum()
        print(f"\n【3着内予測のクラス分布】")
        print(f"  3着以内: {place3_count}頭 ({place3_count/len(df)*100:.1f}%)")
        print(f"  4着以下: {out_of_place3}頭 ({out_of_place3/len(df)*100:.1f}%)")
        if non_numeric_count > 0:
            print(f"  非数値（取消等）: {non_numeric_count}頭 ({non_numeric_count/len(df)*100:.1f}%)")
    
    # 特徴量のカラム
    feature_cols = ["horse_no", "bracket", "age", "handicap", "weight", "weight_diff", 
                    "entry_odds", "entry_popularity", "sex", "jockey_id", "trainer_id"]
    available_features = [c for c in feature_cols if c in df.columns]
    
    print(f"\n【特徴量カラム】")
    print(f"利用可能: {len(available_features)}/{len(feature_cols)} カラム")
    for col in available_features:
        non_null = df[col].notna().sum()
        null_count = df[col].isna().sum()
        print(f"  {col}: {non_null}/{len(df)} 有効 ({null_count} 欠損)")
    
    missing_features = [c for c in feature_cols if c not in df.columns]
    if missing_features:
        print(f"\n欠損カラム: {', '.join(missing_features)}")
    
    # レース一覧（最初の10件と最後の10件）
    print(f"\n【レース一覧（最初の10件）】")
    race_ids = df['race_id'].unique()
    for rid in race_ids[:10]:
        race_entries = len(df[df['race_id'] == rid])
        wins = (df[df['race_id'] == rid]['finish'] == 1).sum()
        print(f"  {rid}: {race_entries}頭（1着: {wins}頭）")
    
    if len(race_ids) > 20:
        print(f"\n【レース一覧（最後の10件）】")
        for rid in race_ids[-10:]:
            race_entries = len(df[df['race_id'] == rid])
            wins = (df[df['race_id'] == rid]['finish'] == 1).sum()
            print(f"  {rid}: {race_entries}頭（1着: {wins}頭）")
    
    con.close()
    print("\n" + "=" * 60)

if __name__ == "__main__":
    main()
