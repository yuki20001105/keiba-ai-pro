"""
Ultimate版データ用のDB読み込み関数
race_results_ultimateテーブルからJSON形式でデータを読み込む
"""
import sys
import sqlite3
import pandas as pd
import numpy as np
import json
import time
from pathlib import Path

# Windows cp932 環境で ⚠/✓/✗ 等の Unicode 記号が print で失敗しないよう
# stdout を UTF-8 に再設定（Python 3.7+）
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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


def _repo_root_from_db_path(db_path: Path) -> Path:
    """Infer repository root from db path like keiba/data/keiba_ultimate.db."""
    p = Path(db_path).resolve()
    for parent in [p.parent] + list(p.parents):
        if (parent / "keiba").exists() and (parent / "notebooks").exists():
            return parent
    return Path.cwd()


def _optimize_dataframe_memory(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Apply downcast/category optimization and return before/after stats."""
    before_mb = float(df.memory_usage(deep=True).sum() / 1024**2)
    stats = {
        "before_mb": round(before_mb, 2),
        "after_mb": round(before_mb, 2),
        "reduction_mb": 0.0,
        "reduction_pct": 0.0,
        "int_downcast_cols": 0,
        "float_downcast_cols": 0,
        "object_to_category_cols": 0,
    }

    for col in df.columns:
        s = df[col]
        if pd.api.types.is_integer_dtype(s):
            df[col] = pd.to_numeric(s, downcast="integer", errors="coerce")
            stats["int_downcast_cols"] += 1
        elif pd.api.types.is_float_dtype(s):
            df[col] = pd.to_numeric(s, downcast="float", errors="coerce")
            stats["float_downcast_cols"] += 1
        elif s.dtype == object:
            try:
                cat_s = s.astype("category")
                add_cats = [c for c in ["", "unknown", "None", "nan"] if c not in cat_s.cat.categories]
                if add_cats:
                    cat_s = cat_s.cat.add_categories(add_cats)
                df[col] = cat_s
                stats["object_to_category_cols"] += 1
            except Exception:
                # list/dict-like object columns cannot always be categorized safely.
                pass

    after_mb = float(df.memory_usage(deep=True).sum() / 1024**2)
    reduction_mb = before_mb - after_mb
    reduction_pct = (reduction_mb / before_mb * 100.0) if before_mb > 0 else 0.0
    stats["after_mb"] = round(after_mb, 2)
    stats["reduction_mb"] = round(reduction_mb, 2)
    stats["reduction_pct"] = round(reduction_pct, 2)
    return df, stats


def _save_training_cache(df: pd.DataFrame, cache_dir: Path) -> dict:
    """Save cache as parquet/pickle; parquet may fail when pyarrow/fastparquet is unavailable."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = cache_dir / "ultimate_frame.parquet"
    pkl_path = cache_dir / "ultimate_frame.pkl"
    legacy_parquet_path = cache_dir / "training_frame.parquet"
    legacy_pkl_path = cache_dir / "training_frame.pkl"
    out = {"parquet": False, "pickle": False}
    try:
        df.to_parquet(parquet_path, index=False)
        # backward compatible mirror
        try:
            df.to_parquet(legacy_parquet_path, index=False)
        except Exception:
            pass
        out["parquet"] = True
    except Exception:
        out["parquet"] = False
    try:
        df.to_pickle(pkl_path)
        # backward compatible mirror
        try:
            df.to_pickle(legacy_pkl_path)
        except Exception:
            pass
        out["pickle"] = True
    except Exception:
        out["pickle"] = False
    return out


def ensure_intermediate_normalized_cache(db_path: Path, cache_dir: Path) -> dict:
    """Export intermediate normalized tables to cache for faster repeated analysis workflows."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    targets = {
        "race_results_ultimate": cache_dir / "race_results_ultimate.parquet",
        "horse_history": cache_dir / "horse_history.parquet",
        "training_data": cache_dir / "training_data.parquet",
    }
    status: dict[str, bool] = {}
    conn = sqlite3.connect(str(db_path))
    try:
        for tbl, out_path in targets.items():
            try:
                q = f"SELECT * FROM {tbl}"
                tdf = pd.read_sql_query(q, conn)
                if tdf.empty:
                    status[tbl] = False
                    continue
                try:
                    tdf.to_parquet(out_path, index=False)
                    status[tbl] = True
                except Exception:
                    # parquet not available -> keep workflow alive
                    status[tbl] = False
            except Exception:
                status[tbl] = False
    finally:
        conn.close()
    return status


def load_ultimate_training_frame_cached(
    db_path: Path,
    *,
    cache_dir: Path | None = None,
    prefer_parquet: bool = True,
    profile: bool = False,
    optimize_memory: bool = True,
) -> pd.DataFrame:
    """Load training frame from cache when available, otherwise build and cache it.

    Cache policy:
      - if parquet exists (default preference), load parquet
      - else if pickle exists, load pickle
      - else build from DB and save both formats where possible
    """
    if not isinstance(db_path, Path):
        db_path = Path(db_path)

    root = _repo_root_from_db_path(db_path)
    cdir = cache_dir or (root / "cache")
    parquet_path = cdir / "ultimate_frame.parquet"
    pkl_path = cdir / "ultimate_frame.pkl"
    legacy_parquet_path = cdir / "training_frame.parquet"
    legacy_pkl_path = cdir / "training_frame.pkl"

    if prefer_parquet and parquet_path.exists():
        print(f"→ cache hit: {parquet_path}")
        return pd.read_parquet(parquet_path)
    if prefer_parquet and legacy_parquet_path.exists():
        print(f"→ cache hit: {legacy_parquet_path}")
        return pd.read_parquet(legacy_parquet_path)
    if pkl_path.exists():
        print(f"→ cache hit: {pkl_path}")
        return pd.read_pickle(pkl_path)
    if legacy_pkl_path.exists():
        print(f"→ cache hit: {legacy_pkl_path}")
        return pd.read_pickle(legacy_pkl_path)
    if parquet_path.exists():
        print(f"→ cache hit: {parquet_path}")
        return pd.read_parquet(parquet_path)
    if legacy_parquet_path.exists():
        print(f"→ cache hit: {legacy_parquet_path}")
        return pd.read_parquet(legacy_parquet_path)

    print("→ cache miss: build from DB")
    norm_status = ensure_intermediate_normalized_cache(db_path, cdir)
    print(
        "  ✓ intermediate cache: "
        f"race_results_ultimate={norm_status.get('race_results_ultimate', False)}, "
        f"horse_history={norm_status.get('horse_history', False)}, "
        f"training_data={norm_status.get('training_data', False)}"
    )
    df = load_ultimate_training_frame(
        db_path,
        profile=profile,
        optimize_memory=optimize_memory,
    )
    saved = _save_training_cache(df, cdir)
    print(f"  ✓ cache saved: parquet={saved['parquet']}, pickle={saved['pickle']}")
    return df


def load_ultimate_training_frame(
    db_path: Path,
    *,
    profile: bool = False,
    optimize_memory: bool = True,
) -> pd.DataFrame:
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
    
    stage: dict[str, float] = {
        "sqlite_read": 0.0,
        "merge": 0.0,
        "astype": 0.0,
        "fillna": 0.0,
        "sort": 0.0,
        "feature_creation": 0.0,
        "memory_opt": 0.0,
    }

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
    _t0_sql = time.perf_counter()
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
    stage["sqlite_read"] += time.perf_counter() - _t0_sql

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

    # ===== holding_times_cache から持ちタイムをロード（2013-2018 バックフィル用） =====
    # race_results_ultimate の JSON に holding_* フィールドがない古いレースを補完する。
    # 構造: {race_id: {horse_id: {just/short/middle/long: {time, l3f, jyuni, babasa}}}}
    holding_cache: dict = {}  # race_id → {horse_id → best_time_detail}

    def _to_sec(t: str):
        if not t:
            return None
        try:
            if ":" in str(t):
                m_s, s_s = str(t).split(":", 1)
                return round(int(m_s) * 60 + float(s_s), 1)
            return round(float(t), 1)
        except (ValueError, TypeError):
            return None

    try:
        _hconn = sqlite3.connect(db_path)
        _hcursor = _hconn.cursor()
        _hcursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='holding_times_cache'"
        )
        if _hcursor.fetchone():
            _hcursor.execute("SELECT race_id, data FROM holding_times_cache")
            for _hrid, _hdata in _hcursor.fetchall():
                try:
                    holding_cache[_hrid] = json.loads(_hdata)
                except Exception:
                    pass
            print(f"  ✓ holding_times_cache: {len(holding_cache)}レース（バックフィル用）")
        _hconn.close()
    except Exception as _he:
        print(f"  ⚠ holding_times_cache 読み込みスキップ: {_he}")

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
            # holding_times_cache バックフィル:
            # race_results_ultimate に holding_just_time_sec が存在しない場合にキャッシュから補完
            if race_id in holding_cache and not data.get('holding_just_time_sec'):
                _horse_id = str(data.get('horse_id', '') or '')
                _btd = holding_cache[race_id].get(_horse_id, {})
                if isinstance(_btd, dict):
                    for _tab in ('just', 'short', 'middle', 'long'):
                        _entry = _btd.get(_tab)
                        if not isinstance(_entry, dict) or not _entry.get('time'):
                            continue
                        _pfx = f'holding_{_tab}_'
                        data[_pfx + 'time_sec'] = _to_sec(_entry.get('time', ''))
                        try:
                            data[_pfx + 'l3f'] = float(_entry['l3f']) if _entry.get('l3f') else None
                        except (ValueError, TypeError):
                            data[_pfx + 'l3f'] = None
                        try:
                            data[_pfx + 'finish'] = int(_entry['jyuni']) if _entry.get('jyuni') else None
                        except (ValueError, TypeError):
                            data[_pfx + 'finish'] = None
                        try:
                            _bs = _entry.get('babasa', '')
                            data[_pfx + 'babasa'] = float(_bs) if _bs else None
                        except (ValueError, TypeError):
                            data[_pfx + 'babasa'] = None
            records.append(data)
        except json.JSONDecodeError:
            print(f"  ⚠ JSON解析エラー: race_id={race_id}")
            continue
    if skipped_invalid:
        print(f"  ⚠ _invalid_distance エントリをスキップ: {skipped_invalid} 件")
    
    _t0_merge = time.perf_counter()
    df = pd.DataFrame(records)
    stage["merge"] += time.perf_counter() - _t0_merge
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
    _t0_fillna = time.perf_counter()
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
    stage["fillna"] += time.perf_counter() - _t0_fillna

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
        'prev3_race_distance', 'prev3_race_finish', 'prev3_race_weight', 'prev3_race_time',
        'prev4_race_distance', 'prev4_race_finish', 'prev4_race_weight', 'prev4_race_time',
        'prev5_race_distance', 'prev5_race_finish', 'prev5_race_weight', 'prev5_race_time',
        # 持ちタイム (just=当該距離・コース, short/middle/long=他距離)
        'holding_just_time_sec', 'holding_just_l3f', 'holding_just_finish', 'holding_just_babasa',
        'holding_short_time_sec', 'holding_short_l3f', 'holding_short_finish', 'holding_short_babasa',
        'holding_middle_time_sec', 'holding_middle_l3f', 'holding_middle_finish', 'holding_middle_babasa',
        'holding_long_time_sec', 'holding_long_l3f', 'holding_long_finish', 'holding_long_babasa',
        # 配当情報 (return_tables_ultimate)
        'tansho_payout', 'fukusho_min_payout', 'fukusho_max_payout', 'sanrentan_payout',
    ]
    _t0_astype = time.perf_counter()
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    
    # last_3f が文字列 "34.9" の場合、数値に変換
    if 'last_3f' in df.columns and df['last_3f'].dtype == object:
        df['last_3f'] = pd.to_numeric(df['last_3f'], errors='coerce')
    stage["astype"] += time.perf_counter() - _t0_astype
    
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

    # ===== prev_race_class: ラグ計算（馬ごとの直前レースクラスを自動生成）=====
    # race_results_ultimate には prev_race_class フィールドが存在しないため、
    # 同一馬の過去レース記録を race_id (日付順) でソートしてシフトする。
    # race_id = YYYYMMDDVVRRR 形式 → 文字列ソートで時系列が一致する。
    _t0_sort = time.perf_counter()
    if 'horse_id' in df.columns and 'race_class' in df.columns and 'race_id' in df.columns:
        _hid = df['horse_id'].fillna('').astype(str)
        _rid = df['race_id'].astype(str)
        # 既に prev_race_class が存在する行（スクレイプ時に格納済み）は上書きしない
        _need_fill = ~df.columns.isin(['prev_race_class']) if 'prev_race_class' not in df.columns else df['prev_race_class'].isna()
        _sort_idx  = (_hid + '_' + _rid).argsort()
        _df_sorted = df.iloc[_sort_idx].copy()
        _lagged    = _df_sorted.groupby(_df_sorted['horse_id'].fillna('').astype(str), sort=False)['race_class'].shift(1)
        _lagged    = _lagged.reindex(df.index)  # 元のインデックス順に戻す
        if 'prev_race_class' not in df.columns:
            df['prev_race_class'] = _lagged
        else:
            df['prev_race_class'] = df['prev_race_class'].where(df['prev_race_class'].notna(), _lagged)
        _covered = df['prev_race_class'].notna().sum()
        print(f"  ✓ prev_race_class (ラグ): {_covered:,}/{len(df):,}件 ({_covered/len(df)*100:.1f}%)")
    stage["sort"] += time.perf_counter() - _t0_sort


    # ===== surface の日本語 → 英語変換（feature_engineering.get_course_featuresへ渡す用） =====
    # LightGBM encoding では元の日本語 surface_ja を保持し、
    # コース特性マスター参照用に surface_en を別途作成する
    _SURFACE_EN = {'芝': 'turf', 'ダート': 'dirt', 'ばんえい': 'dirt', 'sand': 'dirt'}
    if 'surface' in df.columns:
        # 元の日本語値を surface_ja に保存（LightGBM カテゴリ用）
        df['surface_ja'] = df['surface']
        # コースマスター参照用の英語値を surface_en に追加
        df['surface_en'] = df['surface'].map(lambda v: _SURFACE_EN.get(str(v), 'turf') if pd.notna(v) else 'turf')

    # feature_creation は loader 内部での派生補完処理の合算
    stage["feature_creation"] = max(
        0.0,
        stage["merge"] + stage["fillna"] + stage["sort"],
    )

    mem_stats = None
    if optimize_memory:
        _t0_mem = time.perf_counter()
        df, mem_stats = _optimize_dataframe_memory(df)
        stage["memory_opt"] = time.perf_counter() - _t0_mem

    # 最終的なカラム数を表示
    n_numeric = len(df.select_dtypes(include='number').columns)
    n_object = len(df.select_dtypes(include='object').columns)
    n_category = len(df.select_dtypes(include='category').columns)
    print(f"  ✓ 型変換完了: 数値={n_numeric}列, 文字列={n_object}列, 合計={len(df.columns)}列")
    if n_category:
        print(f"  ✓ category: {n_category}列")
    if 'distance' in df.columns:
        print(f"  ✓ distance: {df['distance'].notna().sum()}件取得")
    if 'surface' in df.columns:
        print(f"  ✓ surface: {df['surface'].notna().sum()}件取得")

    if mem_stats is not None:
        print(
            "  ✓ メモリ最適化: "
            f"{mem_stats['before_mb']}MB -> {mem_stats['after_mb']}MB "
            f"({mem_stats['reduction_mb']}MB, {mem_stats['reduction_pct']}%)"
        )

    if profile:
        df.attrs['stage_profile'] = {k: round(v, 4) for k, v in stage.items()}
        df.attrs['memory_optimization'] = mem_stats or {}
        print("\n=== load_ultimate_training_frame stage profile (sec) ===")
        for key in [
            'sqlite_read', 'merge', 'astype', 'fillna',
            'sort', 'feature_creation', 'memory_opt',
        ]:
            print(f"  {key:16s}: {stage[key]:.4f}")
    
    return df

