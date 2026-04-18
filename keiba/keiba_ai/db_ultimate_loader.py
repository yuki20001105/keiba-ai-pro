"""
Ultimate版データ用のDB読み込み関数
race_results_ultimateテーブルからJSON形式でデータを読み込む
"""
import sqlite3
import pandas as pd
import numpy as np
import json
from pathlib import Path

# 会場コード → 会場名 マップ（旧データでコードが数字のまま保存されているケースを学習時に解決）
_VENUE_MAP = {
    '01': '札幌', '02': '函館', '03': '福島', '04': '新潟',
    '05': '東京', '06': '中山', '07': '中京', '08': '京都',
    '09': '阪神', '10': '小倉',
    # NAR 会場
    '30': '門別', '31': '帯広（ば）',
    '35': '盛岡', '36': '水沢',
    '42': '浦和', '43': '船橋', '44': '大井', '45': '川崎',
    '46': '金沢', '47': '笠松', '48': '名古屋',
    '50': '園田', '51': '姫路',
    '54': '福山',
    '55': '高知',
    '60': '佐賀',
    '65': '帯広(ばんえい)', '66': '中津',
}

def load_ultimate_training_frame(db_path: Path) -> pd.DataFrame:
    """
    race_results_ultimateテーブルからUltimate版データを読み込む
    races_ultimateテーブルのdistance/track_type等もJOINして取得する
    
    Args:
        db_path: keiba_ultimate.dbのパス
        
    Returns:
        DataFrame with Ultimate features
    """
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
    
    # races_ultimateテーブルの存在確認
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='races_ultimate'")
    has_races_ultimate = cursor.fetchone() is not None
    
    # race_results_ultimate から全データ取得（イテレータでメモリ消費を削減）
    cursor.execute("SELECT race_id, data FROM race_results_ultimate")
    rows = cursor.fetchall()  # NOTE: 10万行超の場合は cursor.fetchmany() に切り替えの余地あり
    
    # races_ultimate から distance/track_type/date/num_horses を取得（イテレータで处理）
    race_meta = {}
    _invalid_race_ids: set = set()  # _invalid_distance フラグが立っているレース
    if has_races_ultimate:
        cursor.execute("SELECT race_id, data FROM races_ultimate")
        for race_id, data_json in cursor:  # fetchall()よりメモリ効率的
            try:
                data = json.loads(data_json)
                # fix-distance-zero: スクレイピングで距離が取れなかったレースは除外
                if data.get('_invalid_distance'):
                    _invalid_race_ids.add(race_id)
                    continue
                race_meta[race_id] = {
                    'distance': data.get('distance'),
                    'track_type': data.get('track_type'),
                    'surface': data.get('track_type'),  # track_typeをsurfaceとして使用
                    'num_horses': data.get('num_horses'),
                    'race_date': data.get('date'),
                    'race_name': data.get('race_name'),
                    'venue': data.get('venue'),
                    'weather': data.get('weather'),
                    'field_condition': data.get('field_condition'),
                    'race_class': data.get('race_class'),
                    # 新スクレイパーで追加されたフィールド
                    'post_time': data.get('post_time'),
                    'kai': data.get('kai'),
                    'day': data.get('day'),
                    'course_direction': data.get('course_direction'),
                    'lap_cumulative': data.get('lap_cumulative'),   # dict: {200: 12.1, ...}
                    'lap_sectional': data.get('lap_sectional'),     # dict: {200: 12.1, ...}
                    # 配当情報（return_tables_ultimateから後で補完）
                    'tansho_payout': None,      # 単勝払い戻し (円)
                    'fukusho_min_payout': None, # 複勝最低払い戻し
                    'fukusho_max_payout': None, # 複勝最高払い戻し
                    'sanrentan_payout': None,   # 三連単払い戻し (荒れ度指標)
                }
            except:
                pass
    if _invalid_race_ids:
        print(f"  ⚠ _invalid_distance レースをスキップ: {len(_invalid_race_ids)} レース")
        print(f"    (tools/fix_distance_zero.py を実行すると修正・再登録できます)")

    # ===== return_tables_ultimate からレース別配当情報を取得 =====
    # 単勝/複勝/三連単 配当 → race_meta に追加して全馬のエントリに結合する
    # 目的: 過去レースの「荒れ度」「市場予測との乖離」特徴量の計算
    _rt_cursor = conn.cursor()
    _rt_cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='return_tables_ultimate'"
    )
    if _rt_cursor.fetchone():
        _rt_cursor.execute(
            "SELECT race_id, bet_type, payout FROM return_tables_ultimate"
        )
        for _race_id_rt, _bet_type, _payout in _rt_cursor.fetchall():
            if _race_id_rt not in race_meta:
                continue
            _meta = race_meta[_race_id_rt]
            _bt = (_bet_type or "").strip()
            _payout = int(_payout) if _payout else 0
            if _bt == "単勝":
                # 単勝は1行のみが基本（最低値を採用：最も妥当な1位への配当）
                _cur = _meta.get("tansho_payout") or 9999999
                _meta["tansho_payout"] = min(_cur, _payout) if _payout > 0 else _cur
            elif _bt == "複勝":
                # 複勝は3行まで存在 → 最低・最高を記録
                _cur_min = _meta.get("fukusho_min_payout") or 9999999
                _cur_max = _meta.get("fukusho_max_payout") or 0
                if _payout > 0:
                    _meta["fukusho_min_payout"] = min(_cur_min, _payout)
                    _meta["fukusho_max_payout"] = max(_cur_max, _payout)
            elif _bt == "三連単":
                _cur = _meta.get("sanrentan_payout") or 9999999
                _meta["sanrentan_payout"] = min(_cur, _payout) if _payout > 0 else _cur

        # 9999999 は未取得フラグなのでNoneに戻す
        for _m in race_meta.values():
            for _k in ("tansho_payout", "fukusho_min_payout", "fukusho_max_payout", "sanrentan_payout"):
                if _m.get(_k) == 9999999:
                    _m[_k] = None
        n_rt = sum(1 for m in race_meta.values() if m.get("tansho_payout") is not None)
        print(f"  ✓ return_tables_ultimate: {n_rt}レースの配当情報をロード")

    conn.close()
    
    if len(rows) == 0:
        print("  ✗ データが0件です")
        return pd.DataFrame()
    
    print(f"  ✓ {len(rows)}行取得")
    
    # JSON → DataFrame
    records = []
    skipped_invalid = 0
    for race_id, data_json in rows:
        # _invalid_distance フラグのあるレースは学習・推論ともにスキップ
        if race_id in _invalid_race_ids:
            skipped_invalid += 1
            continue
        try:
            data = json.loads(data_json)
            if 'race_id' not in data:
                data['race_id'] = race_id
            # races_ultimateのメタ情報をマージ
            if race_id in race_meta:
                for k, v in race_meta[race_id].items():
                    if k not in data or data[k] is None:
                        data[k] = v
            records.append(data)
        except json.JSONDecodeError:
            print(f"  ⚠ JSON解析エラー: race_id={race_id}")
            continue
    if skipped_invalid:
        print(f"  ⚠ _invalid_distance エントリをスキップ: {skipped_invalid} 件")
    
    df = pd.DataFrame(records)
    print(f"  ✓ DataFrame変換: {len(df)}行 × {len(df.columns)}列")

    # ===== horse_name 空欄チェック・仮補完 =====
    # horse_name 空 = <a>タグのテキスト取得失敗。horse_id (href から取得) は有効なので
    # 結合キーおよび統計計算には影響しないが、表示・名前検索に支障が出るため警告する。
    if 'horse_name' in df.columns:
        _empty_name_mask = (
            df['horse_name'].isna() |
            df['horse_name'].astype(str).str.strip().isin(['', 'None', 'nan'])
        )
        n_empty = int(_empty_name_mask.sum())
        if n_empty > 0:
            if 'horse_id' in df.columns:
                df.loc[_empty_name_mask, 'horse_name'] = (
                    df.loc[_empty_name_mask, 'horse_id']
                    .astype(str)
                    .apply(lambda x: f'[{x}]' if x not in ('', 'None', 'nan') else '[不明馬]')
                )
            print(
                f"  ⚠ horse_name が空のエントリ: {n_empty} 件"
                f" → horse_id で仮補完 [horse_id]"
                f" (tools/patch_horse_names.py で正式修正可能)"
            )

    # ===== P0-2: num_horses の補完 =====
    # races_ultimate.data.num_horses が None/未設定の場合、
    # 同一 race_id のエントリ数からレース頭数を計算して補完する
    race_entry_counts = df.groupby('race_id')['race_id'].transform('count')
    if 'num_horses' not in df.columns:
        df['num_horses'] = race_entry_counts
        print(f"  ✓ num_horses: 全{len(df)}件をエントリ数から計算")
    else:
        df['num_horses'] = pd.to_numeric(df['num_horses'], errors='coerce')
        na_count = df['num_horses'].isna().sum()
        if na_count > 0:
            df['num_horses'] = df['num_horses'].fillna(race_entry_counts)
            print(f"  ✓ num_horses: {na_count}件をエントリ数から補完 (合計{len(df)}件)")
        else:
            print(f"  ✓ num_horses: 欠損なし ({len(df)}件)")

    # ===== venue コード解決（旧データでコードが数字のまま保存されているケースを解決） =====
    # 例: "55" → "高知", "65" → "帯広(ばんえい)"
    if 'venue' in df.columns:
        df['venue'] = df['venue'].apply(
            lambda v: _VENUE_MAP.get(str(v).zfill(2), v) if v and str(v).isdigit() else v
        )
        n_resolved = (df['venue'].notna() & ~df['venue'].apply(lambda v: str(v).isdigit() if v else False)).sum()
        print(f"  ✓ venue コード解決: {n_resolved}件")

    # ===== カラム名マッピング（Ultimate版 → 標準版） =====
    column_mapping = {
        'finish_position': 'finish',
        'finish_time': 'time',
        'track_type': 'surface',   # track_typeをsurfaceとして使用
        # 新スクレイパー形式: last_3f → last_3f_time
        'last_3f': 'last_3f_time',
        # 新スクレイパー形式: weight_kg → horse_weight
        'weight_kg': 'horse_weight',
        # weight_changeの別名統一
        'weight_change': 'horse_weight_change',
        # 毛色: horse_coat_color → coat_color (LightGBMが期待する名称)
        'horse_coat_color': 'coat_color',
        # 斤量: jockey_weight → burden_weight (LightGBMが期待する名称)
        'jockey_weight': 'burden_weight',
    }
    for old_name, new_name in column_mapping.items():
        if old_name in df.columns and new_name not in df.columns:
            df[new_name] = df[old_name]
        elif old_name in df.columns and new_name in df.columns and df[new_name].isna().all():
            df[new_name] = df[old_name]
    
    # jockey_id / trainer_id / horse_id: URLからIDを抽出、なければ名前を使用
    # ※ 地方馬・騎手は B プレフィックス付きID（例: B0060, B201600118）のため
    #   [A-Za-z0-9]+ で抽出（\d+ では取れない）
    for url_col, id_col, name_col in [
        ('jockey_url', 'jockey_id', 'jockey_name'),
        ('trainer_url', 'trainer_id', 'trainer_name'),
        ('horse_url', 'horse_id', 'horse_name'),
    ]:
        if id_col not in df.columns:
            if url_col in df.columns:
                # URLの末尾からIDを抽出: .../B0060/ → B0060, .../01091/ → 01091
                df[id_col] = df[url_col].str.extract(r'/([A-Za-z0-9]+)/?$')[0]
            elif name_col in df.columns:
                df[id_col] = df[name_col]
        elif url_col in df.columns:
            # ID列が存在するが空・NaNの行はURLから補完する
            # （旧スクレイパーが \d+ で地方IDをスキップしたケースを救済）
            mask_empty = (
                df[id_col].isna() |
                df[id_col].astype(str).str.strip().isin(['', 'None', 'nan'])
            )
            if mask_empty.any():
                extracted = df.loc[mask_empty, url_col].str.extract(r'/([A-Za-z0-9]+)/?$')[0]
                df.loc[mask_empty, id_col] = extracted
                # それでも空ならname_colから補完
                mask_still = (
                    df[id_col].isna() |
                    df[id_col].astype(str).str.strip().isin(['', 'None', 'nan'])
                )
                if mask_still.any() and name_col in df.columns:
                    df.loc[mask_still, id_col] = df.loc[mask_still, name_col]
    
    # ===== 数値変換 =====
    numeric_cols = [
        'bracket_number', 'horse_number', 'jockey_weight', 'burden_weight', 'odds', 'popularity',
        'horse_weight', 'age', 'finish', 'finish_position', 'distance',
        'last_3f_time', 'last_3f_rank', 'weight_change', 'horse_weight_change', 'prize_money',
        'num_horses',
        # 新スクレイパーで追加されたフィールド
        'kai', 'day',
        'corner_1', 'corner_2', 'corner_3', 'corner_4',
        'horse_total_runs', 'horse_total_wins', 'horse_total_prize_money',
        'prev_race_distance', 'prev_race_finish', 'prev_race_weight', 'prev_race_time',
        'prev2_race_distance', 'prev2_race_finish', 'prev2_race_weight', 'prev2_race_time',
        # 配当情報 (return_tables_ultimate)
        'tansho_payout', 'fukusho_min_payout', 'fukusho_max_payout', 'sanrentan_payout',
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    
    # last_3f が文字列 "34.9" の場合、数値に変換
    if 'last_3f' in df.columns and df['last_3f'].dtype == object:
        df['last_3f'] = pd.to_numeric(df['last_3f'], errors='coerce')
    
    # horse_weight と weight の統合（どちらかが欠損していたら補完）
    # weight が "474(+10)" 形式の場合は数値部分を抽出
    if 'weight' in df.columns:
        if df['weight'].dtype == object:
            df['weight'] = df['weight'].str.extract(r'^(\d+)')[0]
            df['weight'] = pd.to_numeric(df['weight'], errors='coerce')
    if 'horse_weight' in df.columns and 'weight' not in df.columns:
        df['weight'] = df['horse_weight']
    elif 'weight' in df.columns and ('horse_weight' not in df.columns or df['horse_weight'].isna().all()):
        df['horse_weight'] = df['weight']
    
    # ===== corner_positions の解析 =====
    # "7-7-2-2" → [7, 7, 2, 2] の変換（corner_positions_listが無い場合）
    if 'corner_positions' in df.columns and 'corner_positions_list' not in df.columns:
        def parse_corners(s):
            try:
                if pd.isna(s) or s == '':
                    return []
                return [int(x) for x in str(s).split('-') if x.strip().isdigit()]
            except:
                return []
        df['corner_positions_list'] = df['corner_positions'].apply(parse_corners)
    
    # ===== finish_time を秒数に変換 =====
    def parse_time(t):
        try:
            if pd.isna(t) or t == '':
                return np.nan
            t = str(t)
            if ':' in t:
                parts = t.split(':')
                return float(parts[0]) * 60 + float(parts[1])
            return float(t)
        except:
            return np.nan
    
    if 'time' in df.columns:
        df['time_seconds'] = df['time'].apply(parse_time)
    
    # ===== sex_age のパース（"牡6" → sex="牡", age=6 で補完） =====
    if 'sex_age' in df.columns:
        if 'sex' not in df.columns or df['sex'].isna().all():
            df['sex'] = df['sex_age'].str.extract(r'^([牡牝セ])')[0]
        if 'age' not in df.columns or df['age'].isna().all():
            df['age'] = pd.to_numeric(df['sex_age'].str.extract(r'(\d+)$')[0], errors='coerce')
    
    # ===== race_class の表記統一 =====
    # スクレイパーが "OP" / "オープン" を混在して格納するケースを統一
    _RACE_CLASS_NORM = {
        'OP': 'オープン',
        'op': 'オープン',
        'Open': 'オープン',
        'OPEN': 'オープン',
    }
    if 'race_class' in df.columns:
        df['race_class'] = df['race_class'].replace(_RACE_CLASS_NORM)

    # ===== surface の日本語 → 英語変換（feature_engineering.get_course_featuresへ渡す用） =====
    # LightGBM encoding では元の日本語 surface_ja を保持し、
    # コース特性マスター参照用に surface_en を別途作成する
    _SURFACE_EN = {'芝': 'turf', 'ダート': 'dirt', 'ばんえい': 'dirt', 'sand': 'dirt'}
    if 'surface' in df.columns:
        # 元の日本語値を surface_ja に保存（LightGBM カテゴリ用）
        df['surface_ja'] = df['surface']
        # コースマスター参照用の英語値を surface_en に追加
        df['surface_en'] = df['surface'].map(lambda v: _SURFACE_EN.get(str(v), 'turf') if pd.notna(v) else 'turf')

    # 最終的なカラム数を表示
    n_numeric = len(df.select_dtypes(include='number').columns)
    n_object = len(df.select_dtypes(include='object').columns)
    print(f"  ✓ 型変換完了: 数値={n_numeric}列, 文字列={n_object}列, 合計={len(df.columns)}列")
    if 'distance' in df.columns:
        print(f"  ✓ distance: {df['distance'].notna().sum()}件取得")
    if 'surface' in df.columns:
        print(f"  ✓ surface: {df['surface'].notna().sum()}件取得")
    
    return df

