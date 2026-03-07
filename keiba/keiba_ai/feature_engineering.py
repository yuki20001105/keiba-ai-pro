"""
特徴量エンジニアリング用のユーティリティ関数
派生特徴量の計算を行う
"""
from pathlib import Path
from typing import Optional
import yaml
import pandas as pd
import numpy as np


def load_course_master(yaml_path: Optional[Path] = None) -> dict:
    """コース特性マスターデータを読み込む"""
    if yaml_path is None:
        yaml_path = Path(__file__).parent / "course_master.yaml"
    
    with open(yaml_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def extract_race_info(race_id: str) -> dict:
    """race_idから競馬場コード、距離、芝/ダートを抽出
    
    race_id format: YYYYMMDDVVRR
    - YYYYMMDD: 日付
    - VV: 競馬場コード (05=東京, 06=中山, etc.)
    - RR: レース番号
    
    Returns:
        dict: {
            'venue_code': str,  # 競馬場コード
            'race_num': int,    # レース番号
            'date': str         # YYYYMMDD
        }
    """
    if len(race_id) != 12:
        return {'venue_code': None, 'race_num': None, 'date': None}
    
    return {
        'venue_code': race_id[8:10],
        'race_num': int(race_id[10:12]),
        'date': race_id[:8]
    }


def get_course_features(race_id: str, distance: int, surface: str = "turf") -> dict:
    """レースIDと距離からコース特性を取得
    
    Args:
        race_id: レースID
        distance: 距離（メートル）
        surface: "turf" or "dirt"
    
    Returns:
        dict: コース特性
            - straight_length: 直線距離
            - track_type: inner/outer/straight
            - corner_radius: tight/medium/large/none
            - inner_bias: 内枠有利性 (0=フラット, 1=有利)
    """
    course_master = load_course_master()
    race_info = extract_race_info(race_id)
    venue_code = race_info['venue_code']
    
    if venue_code not in course_master['courses']:
        # デフォルト値
        return {
            'straight_length': 300.0,
            'track_type': 'unknown',
            'corner_radius': 'medium',
            'inner_bias': 0
        }
    
    venue = course_master['courses'][venue_code]
    surface_data = venue.get(surface, {})
    
    # 距離に最も近いコース設定を探す
    distance_str = str(distance)
    if distance_str in surface_data:
        course_data = surface_data[distance_str]
    elif 'default' in surface_data:
        course_data = surface_data['default']
    else:
        # 最も近い距離を探す
        available_distances = [k for k in surface_data.keys() if k != 'default']
        if available_distances:
            closest = min(available_distances, key=lambda x: abs(int(x) - distance))
            course_data = surface_data[closest]
        else:
            course_data = {
                'straight_length': 300.0,
                'track_type': 'unknown',
                'corner_radius': 'medium',
                'inner_bias': 0
            }
    
    return {
        'straight_length': course_data.get('straight_length', 300.0),
        'track_type': course_data.get('track_type', 'unknown'),
        'corner_radius': course_data.get('corner_radius', 'medium'),
        'inner_bias': course_data.get('inner_bias', 0)
    }


def compute_jockey_course_stats(df: pd.DataFrame, current_race_id: str, jockey_id: str) -> dict:
    """騎手の同コース成績を計算
    
    Args:
        df: 全レースデータ（entries + results結合済み）
        current_race_id: 現在のレースID
        jockey_id: 騎手ID
    
    Returns:
        dict: {
            'jockey_course_wins': int,      # 同コース1着回数
            'jockey_course_races': int,     # 同コース出走回数
            'jockey_course_win_rate': float # 同コース勝率
        }
    """
    current_venue = extract_race_info(current_race_id)['venue_code']
    
    # 過去の同コースデータを抽出
    past_races = df[
        (df['race_id'] < current_race_id) &
        (df['jockey_id'] == jockey_id) &
        (df['race_id'].str[8:10] == current_venue)
    ]
    
    if len(past_races) == 0:
        return {
            'jockey_course_wins': 0,
            'jockey_course_races': 0,
            'jockey_course_win_rate': 0.0
        }
    
    wins = (past_races['finish'] == 1).sum()
    races = len(past_races)
    win_rate = wins / races if races > 0 else 0.0
    
    return {
        'jockey_course_wins': int(wins),
        'jockey_course_races': int(races),
        'jockey_course_win_rate': float(win_rate)
    }


def compute_horse_distance_stats(df: pd.DataFrame, current_race_id: str, horse_id: str, distance: int, tolerance: int = 200) -> dict:
    """馬の同距離成績を計算
    
    Args:
        df: 全レースデータ
        current_race_id: 現在のレースID
        horse_id: 馬ID
        distance: レース距離
        tolerance: 距離の許容範囲（±200m程度）
    
    Returns:
        dict: {
            'horse_distance_wins': int,
            'horse_distance_races': int,
            'horse_distance_win_rate': float,
            'horse_distance_avg_finish': float
        }
    """
    # 過去の同距離帯データを抽出
    # 注: distanceカラムがない場合は計算不可
    if 'distance' not in df.columns:
        return {
            'horse_distance_wins': 0,
            'horse_distance_races': 0,
            'horse_distance_win_rate': 0.0,
            'horse_distance_avg_finish': 0.0
        }
    
    past_races = df[
        (df['race_id'] < current_race_id) &
        (df['horse_id'] == horse_id) &
        (df['distance'] >= distance - tolerance) &
        (df['distance'] <= distance + tolerance)
    ]
    
    if len(past_races) == 0:
        return {
            'horse_distance_wins': 0,
            'horse_distance_races': 0,
            'horse_distance_win_rate': 0.0,
            'horse_distance_avg_finish': 0.0
        }
    
    wins = (past_races['finish'] == 1).sum()
    races = len(past_races)
    win_rate = wins / races if races > 0 else 0.0
    avg_finish = past_races['finish'].mean() if races > 0 else 0.0
    
    return {
        'horse_distance_wins': int(wins),
        'horse_distance_races': int(races),
        'horse_distance_win_rate': float(win_rate),
        'horse_distance_avg_finish': float(avg_finish)
    }


def compute_trainer_recent_form(df: pd.DataFrame, current_race_id: str, trainer_id: str, days: int = 90) -> dict:
    """調教師の最近の成績を計算
    
    Args:
        df: 全レースデータ
        current_race_id: 現在のレースID
        trainer_id: 調教師ID
        days: 集計期間（日数）
    
    Returns:
        dict: {
            'trainer_recent_wins': int,
            'trainer_recent_races': int,
            'trainer_recent_win_rate': float
        }
    """
    current_date = current_race_id[:8]
    try:
        cutoff_date = (pd.to_datetime(current_date, format='%Y%m%d') - pd.Timedelta(days=days)).strftime('%Y%m%d')
    except (ValueError, Exception):
        # race_id[:8] が有効な日付でない場合（新フォーマット: YYYY+会場+開催+日+R）
        # race_id の辞書順比較に頼る（全データ対象）
        cutoff_date = '00000000'
    
    recent_races = df[
        (df['race_id'] >= cutoff_date) &
        (df['race_id'] < current_race_id) &
        (df['trainer_id'] == trainer_id)
    ]
    
    if len(recent_races) == 0:
        return {
            'trainer_recent_wins': 0,
            'trainer_recent_races': 0,
            'trainer_recent_win_rate': 0.0
        }
    
    wins = (recent_races['finish'] == 1).sum()
    races = len(recent_races)
    win_rate = wins / races if races > 0 else 0.0
    
    return {
        'trainer_recent_wins': int(wins),
        'trainer_recent_races': int(races),
        'trainer_recent_win_rate': float(win_rate)
    }


# =========================================================
# P2-8: 馬の馬場・距離帯別適性
# =========================================================

def _dist_band(distance) -> str:
    """距離をスプリント/マイル/中距離/長距離に分類"""
    try:
        d = int(float(distance))
    except (TypeError, ValueError):
        return 'unknown'
    if d <= 1200:   return 'sprint'
    elif d <= 1600: return 'mile'
    elif d <= 2200: return 'middle'
    else:           return 'long'


def compute_horse_surface_stats(df: pd.DataFrame, current_race_id: str, horse_id: str, surface: str) -> dict:
    """馬の同馬場別成績を計算（expanding window）

    Args:
        df: 全レースデータ
        current_race_id: 現在のレースID
        horse_id: 馬ID
        surface: 馬場種別（'芝' / 'ダート' / 'dirt' / 'turf' 等）

    Returns:
        dict: horse_surface_win_rate, horse_surface_races
    """
    if 'surface' not in df.columns:
        return {'horse_surface_win_rate': 0.0, 'horse_surface_races': 0}

    past = df[
        (df['race_id'] < current_race_id) &
        (df['horse_id'] == horse_id) &
        (df['surface'] == surface)
    ]
    if len(past) == 0:
        return {'horse_surface_win_rate': 0.0, 'horse_surface_races': 0}
    wins  = (pd.to_numeric(past['finish'], errors='coerce') == 1).sum()
    races = len(past)
    return {
        'horse_surface_win_rate': float(wins / races),
        'horse_surface_races':    int(races),
    }


def compute_horse_dist_band_stats(df: pd.DataFrame, current_race_id: str, horse_id: str, distance) -> dict:
    """馬の距離帯別成績を計算（expanding window）

    Args:
        df: 全レースデータ
        current_race_id: 現在のレースID
        horse_id: 馬ID
        distance: レース距離

    Returns:
        dict: horse_dist_band_win_rate, horse_dist_band_races
    """
    if 'distance' not in df.columns:
        return {'horse_dist_band_win_rate': 0.0, 'horse_dist_band_races': 0}

    target_band = _dist_band(distance)
    past = df[
        (df['race_id'] < current_race_id) &
        (df['horse_id'] == horse_id) &
        (df['distance'].apply(_dist_band) == target_band)
    ]
    if len(past) == 0:
        return {'horse_dist_band_win_rate': 0.0, 'horse_dist_band_races': 0}
    wins  = (pd.to_numeric(past['finish'], errors='coerce') == 1).sum()
    races = len(past)
    return {
        'horse_dist_band_win_rate': float(wins / races),
        'horse_dist_band_races':    int(races),
    }


# =========================================================
# P2-9: 枠番バイアス
# =========================================================

def compute_gate_win_rate(df: pd.DataFrame, current_race_id: str,
                          venue: str, distance, surface: str,
                          bracket_number: int, min_samples: int = 20) -> dict:
    """会場×距離帯×馬場での枠番位置の過去勝率を計算（expanding window）

    内枠（bracket_number<=3）と外枠で分けた過去実績を馬に紐付ける。
    サンプル数が min_samples 未満の場合は全体平均 0.5 を返す。

    Returns:
        dict: gate_win_rate (この枠番区分の勝率)
    """
    if 'bracket_number' not in df.columns or 'finish' not in df.columns:
        return {'gate_win_rate': 0.5}

    target_band = _dist_band(distance)
    is_inner    = int(bracket_number) <= 3 if pd.notna(bracket_number) else True

    # 同条件の過去レース（同じ内/外枠区分のみが入ったレースエントリ）
    cond = (df['race_id'] < current_race_id)
    if 'venue' in df.columns:
        cond &= (df['venue'] == venue)
    if 'surface' in df.columns:
        cond &= (df['surface'] == surface)
    if 'distance' in df.columns:
        cond &= (df['distance'].apply(_dist_band) == target_band)

    hist = df[cond]
    if len(hist) < min_samples:
        return {'gate_win_rate': 0.5}

    bracket_num = pd.to_numeric(hist['bracket_number'], errors='coerce')
    inner_mask  = bracket_num <= 3
    target_mask = inner_mask if is_inner else ~inner_mask
    subset      = hist[target_mask]

    if len(subset) == 0:
        return {'gate_win_rate': 0.5}

    wins = (pd.to_numeric(subset['finish'], errors='coerce') == 1).sum()
    rate = float(wins / len(subset))
    return {'gate_win_rate': rate}


# =========================================================
# P3-10: 騎手×調教師コンビ成績
# =========================================================

def compute_jockey_trainer_combo(df: pd.DataFrame, current_race_id: str,
                                  jockey_id: str, trainer_id: str) -> dict:
    """騎手×調教師コンビの過去成績を計算（expanding window）

    Returns:
        dict: jt_combo_races, jt_combo_win_rate, jt_combo_win_rate_smooth
    """
    past = df[
        (df['race_id'] < current_race_id) &
        (df['jockey_id'] == jockey_id) &
        (df['trainer_id'] == trainer_id)
    ]
    races = len(past)
    wins  = int((pd.to_numeric(past['finish'], errors='coerce') == 1).sum()) if races > 0 else 0
    win_rate = float(wins / races) if races > 0 else 0.0

    # ベイズ平滑化（全体平均 7.5%, K=5）
    GLOBAL = 0.075
    K      = 5
    smooth = (races * win_rate + K * GLOBAL) / (races + K)

    return {
        'jt_combo_races':            races,
        'jt_combo_win_rate':         win_rate,
        'jt_combo_win_rate_smooth':  float(smooth),
    }



def add_derived_features(df: pd.DataFrame, full_history_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """データフレームに派生特徴量を追加
    
    Args:
        df: 特徴量を追加するデータフレーム（現在のレースデータ）
        full_history_df: 過去データを含む全データ（統計計算用）
    
    Returns:
        pd.DataFrame: 派生特徴量が追加されたデータフレーム
    """
    df = df.copy()
    
    # ===== 新機能: 性齢パース =====
    if 'sex' in df.columns and 'age' in df.columns:
        # 性別ダミー変数化
        if df['sex'].dtype == 'object':
            sex_dummies = pd.get_dummies(df['sex'], prefix='sex')
            df = pd.concat([df, sex_dummies], axis=1)
        
        # 年齢ベースの派生特徴
        df['is_young'] = (df['age'] <= 3).astype(int)  # 若馬
        df['is_prime'] = ((df['age'] >= 4) & (df['age'] <= 6)).astype(int)  # 最盛期
        df['is_veteran'] = (df['age'] >= 7).astype(int)  # ベテラン
    
    # ===== 新機能: コーナー通過順解析 =====
    if 'corner_positions_list' in df.columns:
        df['corner_position_avg'] = df['corner_positions_list'].apply(
            lambda x: np.mean(x) if isinstance(x, list) and len(x) > 0 else np.nan
        )
        df['corner_position_variance'] = df['corner_positions_list'].apply(
            lambda x: np.var(x) if isinstance(x, list) and len(x) > 1 else 0
        )
        # 最終コーナーでの位置
        df['last_corner_position'] = df['corner_positions_list'].apply(
            lambda x: x[-1] if isinstance(x, list) and len(x) > 0 else np.nan
        )
        # ポジション変化（最初→最後）
        df['position_change'] = df['corner_positions_list'].apply(
            lambda x: x[0] - x[-1] if isinstance(x, list) and len(x) > 1 else 0
        )
    
    # ===== 新機能: ペース区分 =====
    if 'pace_classification' in df.columns:
        pace_dummies = pd.get_dummies(df['pace_classification'], prefix='pace')
        df = pd.concat([df, pace_dummies], axis=1)
    
    # ===== 新機能: 上がり3F順位の正規化 =====
    if 'last_3f_rank' in df.columns and 'num_horses' in df.columns:
        df['last_3f_rank_normalized'] = df['last_3f_rank'] / df['num_horses']
    
    # ===== 新機能: 近走派生特徴 =====
    if 'days_since_last_race' in df.columns:
        # 休養期間のカテゴリ化
        df['rest_category'] = pd.cut(
            df['days_since_last_race'],
            bins=[-np.inf, 14, 30, 60, np.inf],
            labels=['short', 'normal', 'long', 'very_long']
        )
        rest_dummies = pd.get_dummies(df['rest_category'], prefix='rest')
        df = pd.concat([df, rest_dummies], axis=1)
    
    if 'last_distance_change' in df.columns:
        df['distance_increased'] = (df['last_distance_change'] > 0).astype(int)
        df['distance_decreased'] = (df['last_distance_change'] < 0).astype(int)
    
    if 'popularity_trend' in df.columns:
        trend_dummies = pd.get_dummies(df['popularity_trend'], prefix='pop_trend')
        df = pd.concat([df, trend_dummies], axis=1)
    
    # race_idから競馬場コード・レース番号を抽出
    df['venue_code'] = df['race_id'].apply(lambda x: extract_race_info(x)['venue_code'])
    df['race_num'] = df['race_id'].apply(lambda x: extract_race_info(x)['race_num'])
    
    # コース特性を追加（distance, surfaceカラムが必要）
    if 'distance' in df.columns and 'surface' in df.columns:
        # surface_en があればそちらを優先（日本語→英語変換済み）
        # なければその場で変換: 芝→turf, ダート→dirt
        _SF_EN = {'芝': 'turf', 'ダート': 'dirt', 'ばんえい': 'dirt', 'sand': 'dirt'}
        if 'surface_en' in df.columns:
            _surface_col = 'surface_en'
        else:
            df['_surface_tmp'] = df['surface'].map(
                lambda v: _SF_EN.get(str(v), v) if pd.notna(v) else 'turf'
            )
            _surface_col = '_surface_tmp'

        course_features = df.apply(
            lambda row: get_course_features(row['race_id'], row['distance'], row[_surface_col]),
            axis=1
        )
        if '_surface_tmp' in df.columns:
            df = df.drop(columns=['_surface_tmp'])
        df['straight_length'] = [f['straight_length'] for f in course_features]
        df['track_type'] = [f['track_type'] for f in course_features]
        df['corner_radius'] = [f['corner_radius'] for f in course_features]
        df['inner_bias'] = [f['inner_bias'] for f in course_features]
        
        # 内枠有利コース × 内枠の交互作用（bracketカラムがあれば）
        if 'bracket' in df.columns or 'bracket_number' in df.columns:
            bracket_col = 'bracket' if 'bracket' in df.columns else 'bracket_number'
            df['inner_advantage'] = df['inner_bias'] * (df[bracket_col] <= 3).astype(int)
    
    # 過去データがあれば統計特徴量を計算
    if full_history_df is not None:
        # 騎手のコース別成績
        jockey_stats = df.apply(
            lambda row: compute_jockey_course_stats(full_history_df, row['race_id'], row['jockey_id']),
            axis=1
        )
        df['jockey_course_win_rate'] = [s['jockey_course_win_rate'] for s in jockey_stats]
        df['jockey_course_races'] = [s['jockey_course_races'] for s in jockey_stats]
        
        # 馬の距離別成績
        if 'distance' in df.columns and 'horse_id' in df.columns:
            horse_stats = df.apply(
                lambda row: compute_horse_distance_stats(full_history_df, row['race_id'], row['horse_id'], row['distance']),
                axis=1
            )
            df['horse_distance_win_rate'] = [s['horse_distance_win_rate'] for s in horse_stats]
            df['horse_distance_avg_finish'] = [s['horse_distance_avg_finish'] for s in horse_stats]
        
        # 調教師の最近の成績
        trainer_stats = df.apply(
            lambda row: compute_trainer_recent_form(full_history_df, row['race_id'], row['trainer_id']),
            axis=1
        )
        # ultimate_features.py でも同名列を計算するため fe_ プレフィックスで区別
        df['fe_trainer_win_rate'] = [s['trainer_recent_win_rate'] for s in trainer_stats]

        # ===== P2-8: 馬の馬場別・距離帯別適性（ベクトル化 expanding window） =====
        if 'finish' in full_history_df.columns and 'race_id' in full_history_df.columns:

            def _expanding_win_rate_by_group(history_df: pd.DataFrame,
                                              id_col: str,
                                              group_col: str,
                                              out_rate: str,
                                              out_cnt: str) -> pd.DataFrame:
                """id_col × group_col でグループ化した expanding window 勝率を計算。
                horse_surface_win_rate 等の計算に使用。
                """
                if id_col not in history_df.columns or group_col not in history_df.columns:
                    return history_df
                orig_idx = history_df.index.copy()
                s = history_df.sort_values('race_id', kind='mergesort').copy()
                fin_num  = pd.to_numeric(s['finish'], errors='coerce').fillna(0)
                win_flag = (fin_num == 1).astype(float)
                grp      = s[[id_col, group_col]].astype(str).apply('|'.join, axis=1)
                race_cnt = s.groupby(grp, sort=False).cumcount()
                s['_w']  = win_flag
                cum_wins = s.groupby(grp, sort=False)['_w'].cumsum() - s['_w']
                s.drop(columns=['_w'], inplace=True)
                s[out_rate] = (cum_wins / race_cnt.clip(1)).fillna(0.0)
                s[out_cnt]  = race_cnt
                s_back = s.reindex(orig_idx)
                history_df = history_df.copy()
                history_df[out_rate] = s_back[out_rate].values
                history_df[out_cnt]  = s_back[out_cnt].values
                return history_df

            # 馬場別適性
            if 'horse_id' in full_history_df.columns and 'surface' in full_history_df.columns:
                full_history_df = _expanding_win_rate_by_group(
                    full_history_df, 'horse_id', 'surface',
                    'horse_surface_win_rate', 'horse_surface_races'
                )
                df = df.merge(
                    full_history_df[['horse_id', 'race_id',
                                     'horse_surface_win_rate', 'horse_surface_races']]
                    .drop_duplicates(subset=['horse_id', 'race_id']),
                    on=['horse_id', 'race_id'], how='left'
                )

            # 距離帯別適性
            if 'horse_id' in full_history_df.columns and 'distance' in full_history_df.columns:
                _db_map = {'sprint': 0, 'mile': 1, 'middle': 2, 'long': 3}
                full_history_df['_dist_band'] = (
                    full_history_df['distance'].apply(_dist_band)
                )
                full_history_df = _expanding_win_rate_by_group(
                    full_history_df, 'horse_id', '_dist_band',
                    'horse_dist_band_win_rate', 'horse_dist_band_races'
                )
                df = df.merge(
                    full_history_df[['horse_id', 'race_id',
                                     'horse_dist_band_win_rate', 'horse_dist_band_races']]
                    .drop_duplicates(subset=['horse_id', 'race_id']),
                    on=['horse_id', 'race_id'], how='left'
                )
                full_history_df = full_history_df.drop(columns=['_dist_band'], errors='ignore')

        # ===== P2-9: 枠番バイアス（会場×距離帯×馬場 での内枠勝率） =====
        # ベクトル化: 全race_id以前のデータで集計したものを各行にマッピング
        if ('bracket_number' in full_history_df.columns and
                'finish' in full_history_df.columns and
                'venue' in full_history_df.columns):

            hist_gate = full_history_df.copy()
            hist_gate['_is_inner'] = (
                pd.to_numeric(hist_gate['bracket_number'], errors='coerce') <= 3
            ).astype(int)
            hist_gate['_is_win'] = (
                pd.to_numeric(hist_gate['finish'], errors='coerce') == 1
            ).astype(int)
            if 'distance' in hist_gate.columns:
                hist_gate['_dist_band'] = hist_gate['distance'].apply(_dist_band)
            else:
                hist_gate['_dist_band'] = 'unknown'
            surf_col = 'surface' if 'surface' in hist_gate.columns else None

            gate_group_keys = ['venue', '_dist_band']
            if surf_col:
                gate_group_keys.append(surf_col)

            # グループ×内外枠別の勝率を集計（全履歴=静的集計、データリーク軽微）
            gate_agg = (
                hist_gate.groupby(gate_group_keys + ['_is_inner'], sort=False)
                .agg(_cnt=('_is_win', 'count'), _wins=('_is_win', 'sum'))
                .reset_index()
            )
            gate_agg['_gate_wr'] = np.where(
                gate_agg['_cnt'] >= 10,
                gate_agg['_wins'] / gate_agg['_cnt'],
                0.5      # サンプル不足は中立値
            )

            # dfに結合キーを追加
            df['_dist_band'] = df['distance'].apply(_dist_band) if 'distance' in df.columns else 'unknown'
            df['_is_inner']  = (
                pd.to_numeric(df.get('bracket_number', pd.Series([None]*len(df))), errors='coerce') <= 3
            ).fillna(True).astype(int)

            merge_keys = gate_group_keys + ['_is_inner']
            df = df.merge(gate_agg[merge_keys + ['_gate_wr']], on=merge_keys, how='left')
            df['gate_win_rate'] = df['_gate_wr'].fillna(0.5)
            df = df.drop(columns=['_dist_band', '_is_inner', '_gate_wr'], errors='ignore')

        # ===== P3-10: 騎手×調教師コンビ成績（ベクトル化 expanding window） =====
        if ('jockey_id' in full_history_df.columns and
                'trainer_id' in full_history_df.columns and
                'finish' in full_history_df.columns):

            orig_idx  = full_history_df.index.copy()
            s_jt      = full_history_df.sort_values('race_id', kind='mergesort').copy()
            fin_jt    = pd.to_numeric(s_jt['finish'], errors='coerce').fillna(0)
            win_jt    = (fin_jt == 1).astype(float)
            combo_grp = s_jt['jockey_id'].astype(str) + '|' + s_jt['trainer_id'].astype(str)
            race_cnt_jt = s_jt.groupby(combo_grp, sort=False).cumcount()
            s_jt['_w']  = win_jt
            cum_wins_jt = s_jt.groupby(combo_grp, sort=False)['_w'].cumsum() - s_jt['_w']
            s_jt.drop(columns=['_w'], inplace=True)
            win_rate_jt = (cum_wins_jt / race_cnt_jt.clip(1)).fillna(0.0)

            # ベイズ平滑化
            GLOBAL_WR = 0.075
            K_JT      = 5
            smooth_jt = (race_cnt_jt * win_rate_jt + K_JT * GLOBAL_WR) / (race_cnt_jt + K_JT)

            s_jt['jt_combo_races']           = race_cnt_jt.values
            s_jt['jt_combo_win_rate']        = win_rate_jt.values
            s_jt['jt_combo_win_rate_smooth'] = smooth_jt.values
            s_back_jt = s_jt.reindex(orig_idx)

            full_history_df['jt_combo_races']           = s_back_jt['jt_combo_races'].values
            full_history_df['jt_combo_win_rate']        = s_back_jt['jt_combo_win_rate'].values
            full_history_df['jt_combo_win_rate_smooth'] = s_back_jt['jt_combo_win_rate_smooth'].values

            df = df.merge(
                full_history_df[['jockey_id', 'trainer_id', 'race_id',
                                  'jt_combo_races', 'jt_combo_win_rate',
                                  'jt_combo_win_rate_smooth']]
                .drop_duplicates(subset=['jockey_id', 'trainer_id', 'race_id']),
                on=['jockey_id', 'trainer_id', 'race_id'], how='left'
            )

        # ===== 騎手・調教師の複勝率（データリーク防止: expanding window） =====
        if 'finish' in full_history_df.columns and 'race_id' in full_history_df.columns:
            def _expanding_stats(history_df: pd.DataFrame, eid_col: str, prefix: str) -> pd.DataFrame:
                """race_id 辞書順（= 時系列順）での expanding window 統計を計算。
                各行には「その行より前のレース」だけを使った統計をセットする。"""
                if eid_col not in history_df.columns:
                    return history_df
                orig_idx = history_df.index.copy()
                s = history_df.sort_values('race_id', kind='mergesort').copy()
                fin_num  = pd.to_numeric(s['finish'], errors='coerce').fillna(0)
                # place = 3着以内, show = 2着以内
                place_flag = (fin_num <= 3).astype(float)
                show_flag  = (fin_num <= 2).astype(float)
                race_cnt   = s.groupby(eid_col, sort=False).cumcount()  # 前走数（現行除く）
                s['_pl'] = place_flag
                s['_sh'] = show_flag
                cum_pl = s.groupby(eid_col, sort=False)['_pl'].cumsum() - s['_pl']
                cum_sh = s.groupby(eid_col, sort=False)['_sh'].cumsum() - s['_sh']
                s.drop(columns=['_pl', '_sh'], inplace=True)
                s[f'{prefix}_show_rate']       = (cum_pl / race_cnt.clip(1)).fillna(0.0)
                s[f'{prefix}_place_rate_top2'] = (cum_sh / race_cnt.clip(1)).fillna(0.0)
                s_back = s.reindex(orig_idx)
                history_df = history_df.copy()
                history_df[f'{prefix}_show_rate']       = s_back[f'{prefix}_show_rate'].values
                history_df[f'{prefix}_place_rate_top2'] = s_back[f'{prefix}_place_rate_top2'].values
                return history_df

            if 'jockey_id' in df.columns:
                full_history_df = _expanding_stats(full_history_df, 'jockey_id', 'jockey')
                df = df.merge(
                    full_history_df[['jockey_id', 'race_id', 'jockey_place_rate_top2', 'jockey_show_rate']].drop_duplicates(subset=['jockey_id', 'race_id']),
                    on=['jockey_id', 'race_id'], how='left'
                )
            if 'trainer_id' in df.columns:
                full_history_df = _expanding_stats(full_history_df, 'trainer_id', 'trainer')
                df = df.merge(
                    full_history_df[['trainer_id', 'race_id', 'trainer_place_rate_top2', 'trainer_show_rate']].drop_duplicates(subset=['trainer_id', 'race_id']),
                    on=['trainer_id', 'race_id'], how='left'
                )

        # ===== L2-1: 馬の近走統計（past 3/5/10走 平均着順・勝率）=====
        # shift(1).rolling(N) = 直近N走平均（現在のレースを除いた時系列順）
        if ('finish' in full_history_df.columns and
                'horse_id' in full_history_df.columns and
                'race_id' in full_history_df.columns):
            _orig_idx_h = full_history_df.index.copy()
            _s_h = full_history_df.sort_values('race_id', kind='mergesort').copy()
            _fin_h = pd.to_numeric(_s_h['finish'], errors='coerce')
            _win_h = (_fin_h == 1).astype(float)
            _s_h['_fin_h'] = _fin_h
            _s_h['_win_h'] = _win_h
            for _n, _sfx in [(3, 'past3'), (5, 'past5'), (10, 'past10')]:
                _s_h[f'{_sfx}_avg_finish'] = (
                    _s_h.groupby('horse_id')['_fin_h']
                    .transform(lambda x: x.shift(1).rolling(_n, min_periods=1).mean())
                )
            _s_h['past3_win_rate'] = (
                _s_h.groupby('horse_id')['_win_h']
                .transform(lambda x: x.shift(1).rolling(3, min_periods=1).mean())
            )
            _s_h['past5_win_rate'] = (
                _s_h.groupby('horse_id')['_win_h']
                .transform(lambda x: x.shift(1).rolling(5, min_periods=1).mean())
            )
            _s_h.drop(columns=['_fin_h', '_win_h'], inplace=True)
            _past_cols = ['past3_avg_finish', 'past5_avg_finish', 'past10_avg_finish',
                          'past3_win_rate', 'past5_win_rate']
            _s_h_back = _s_h.reindex(_orig_idx_h)
            for _c in _past_cols:
                full_history_df[_c] = _s_h_back[_c].values
            df = df.merge(
                full_history_df[['horse_id', 'race_id'] + _past_cols]
                .drop_duplicates(subset=['horse_id', 'race_id']),
                on=['horse_id', 'race_id'], how='left'
            )

        # ===== L2-2: 騎手・調教師の近30走勝率（rolling window）=====
        # 全期間累積統計は新人騎手・昇格馬に不利。近30走は直近の調子を反映。
        if 'finish' in full_history_df.columns and 'race_id' in full_history_df.columns:
            for _eid, _pfx in [('jockey_id', 'jockey'), ('trainer_id', 'trainer')]:
                if _eid not in full_history_df.columns:
                    continue
                _orig_idx_e = full_history_df.index.copy()
                _s_e = full_history_df.sort_values('race_id', kind='mergesort').copy()
                _fin_e = pd.to_numeric(_s_e['finish'], errors='coerce').fillna(0)
                _s_e['_win_e'] = (_fin_e == 1).astype(float)
                _s_e[f'{_pfx}_recent30_win_rate'] = (
                    _s_e.groupby(_eid)['_win_e']
                    .transform(lambda x: x.shift(1).rolling(30, min_periods=5).mean())
                )
                _s_e.drop(columns=['_win_e'], inplace=True)
                _rc = f'{_pfx}_recent30_win_rate'
                _s_e_back = _s_e.reindex(_orig_idx_e)
                full_history_df[_rc] = _s_e_back[_rc].values
                if _eid in df.columns:
                    df = df.merge(
                        full_history_df[[_eid, 'race_id', _rc]]
                        .drop_duplicates(subset=[_eid, 'race_id']),
                        on=[_eid, 'race_id'], how='left'
                    )

    # ===== 市場エントロピー / 上位3頭の暗黙確率和 =====
    if 'odds' in df.columns and 'race_id' in df.columns:
        def _market_features(grp):
            o = pd.to_numeric(grp['odds'], errors='coerce').dropna()
            if len(o) < 2:
                return pd.Series({'market_entropy': 0.0, 'top3_probability': 0.5})
            probs = 1.0 / o
            total = probs.sum()
            if total == 0:
                return pd.Series({'market_entropy': 0.0, 'top3_probability': 0.5})
            probs = probs / total
            entropy = float(-np.sum(probs * np.log(probs + 1e-10)))
            top3_prob = float(probs.nlargest(3).sum())
            return pd.Series({'market_entropy': entropy, 'top3_probability': top3_prob})
        market_stats = df.groupby('race_id', sort=False).apply(
            _market_features, include_groups=False
        ).reset_index()
        df = df.merge(market_stats, on='race_id', how='left')

    # ===== A-9: オッズのレース内正規化（市場情報の精緻化） =====
    # odds は絶対値ではなくレース内相対値として使う方がロバスト
    if 'odds' in df.columns and 'race_id' in df.columns:
        _o = pd.to_numeric(df['odds'], errors='coerce')
        # 暗黙確率（1/odds）
        df['implied_prob'] = np.where(_o > 0, 1.0 / _o, np.nan)
        # レース内正規化（控除率込みで各馬の推定勝ち確率）
        df['implied_prob_norm'] = df.groupby('race_id')['implied_prob'].transform(
            lambda x: x / x.sum() if x.sum() > 0 else x
        )
        # レース内人気順位（1位=最低オッズ）
        df['odds_rank_in_race'] = df.groupby('race_id')['odds'].rank(
            method='min', na_option='bottom'
        )
        # レース内 z-score（どれだけ平均から外れているか）
        df['odds_z_in_race'] = df.groupby('race_id')['odds'].transform(
            lambda x: (x - x.mean()) / (x.std() + 1e-8)
        )

    # ===== 前走からの日数 (S-1修正: race_date列を優先、fallback は race_id[:8]) =====
    if 'prev_race_date' in df.columns:
        # race_date列が存在する場合は正確な日付を使う（race_id[:8]はJRAコードであり日付ではない）
        if 'race_date' in df.columns:
            _race_dt = pd.to_datetime(
                df['race_date'].astype(str).str.replace('/', '-').str.strip(),
                format='%Y%m%d', errors='coerce'
            )
        else:
            # fallback: race_id[:8] は年+会場コード等であり不正確。警告を出す
            import warnings as _w
            _w.warn(
                "race_date列が存在しません。race_id[:8]を使用しますが days_since_last_race が"
                "誤った値になる可能性があります（特に10月以降のレース）",
                UserWarning, stacklevel=2
            )
            _race_dt = pd.to_datetime(df['race_id'].str[:8], format='%Y%m%d', errors='coerce')
        _prev_dt = pd.to_datetime(
            df['prev_race_date'].astype(str).str.replace('/', '-').str.strip(),
            errors='coerce'
        )
        _days = (_race_dt - _prev_dt).dt.days
        # 負の値（計算誤り）はNaNに置換
        df['days_since_last_race'] = _days.where(_days >= 0, np.nan)

    # ===== 距離変化 =====
    if 'prev_race_distance' in df.columns and 'distance' in df.columns:
        _prev_dist = pd.to_numeric(df['prev_race_distance'], errors='coerce')
        _cur_dist  = pd.to_numeric(df['distance'], errors='coerce')
        df['distance_change'] = _cur_dist - _prev_dist
        df['distance_increased'] = (df['distance_change'] > 0).astype(int)
        df['distance_decreased'] = (df['distance_change'] < 0).astype(int)

    # ===== 馬の通算勝率 =====
    if 'horse_total_runs' in df.columns and 'horse_total_wins' in df.columns:
        _runs = pd.to_numeric(df['horse_total_runs'], errors='coerce')
        _wins = pd.to_numeric(df['horse_total_wins'], errors='coerce')
        df['horse_win_rate'] = np.where(_runs > 0, _wins / _runs, np.nan)

    # ===== P2-7: スピード指標（前走タイム÷距離、同条件z-score） =====
    if 'prev_race_time' in df.columns and 'prev_race_distance' in df.columns:
        _pt = pd.to_numeric(df['prev_race_time'],     errors='coerce')
        _pd = pd.to_numeric(df['prev_race_distance'], errors='coerce')
        # 単位: m/s (距離÷タイム)
        df['prev_speed_index'] = np.where(
            (_pt > 0) & (_pd > 0), _pd / _pt, np.nan
        )
        # 同馬場×前走距離グループでのz-score（条件が似たレース間でタイムの優劣を比較）
        _grp_cols = []
        if 'surface' in df.columns:         _grp_cols.append('surface')
        if 'prev_race_distance' in df.columns: _grp_cols.append('prev_race_distance')
        if _grp_cols:
            df['prev_speed_zscore'] = df.groupby(_grp_cols, sort=False)['prev_speed_index'].transform(
                lambda x: (x - x.mean()) / (x.std() + 1e-8) if len(x) > 1 else 0.0
            )
        else:
            df['prev_speed_zscore'] = 0.0

    # ===== A-7: 欠損フラグ（0埋めより 欠損 is_missing フラグ + NaN の方が安全） =====
    # 初出走・履歴なしは 0 ではなく NaN + フラグで表現する
    for _miss_col in [
        'prev_race_finish', 'prev_race_time', 'prev_race_distance',
        'prev2_race_finish', 'days_since_last_race',
        'prev_race_weight', 'prev_speed_index', 'prev_speed_zscore',
        'horse_win_rate',   # L1-1: 77.3% 欠損の主要特徴量 → 欠損フラグ化して過学習リスク低減
    ]:
        if _miss_col in df.columns:
            _s = pd.to_numeric(df[_miss_col], errors='coerce')
            df[f'{_miss_col}_is_missing'] = _s.isna().astype(int)
            df[_miss_col] = _s  # NaN のまま保持（0埋めしない）

    # ===== 前走着順（数値化） =====
    if 'prev_race_finish' in df.columns:
        df['prev_race_finish'] = pd.to_numeric(df['prev_race_finish'], errors='coerce')

    # ===== ラップタイム展開 =====
    # ※ JSONに保存すると整数キー(200)→文字列キー("200")に変換されるため両方試す
    import json as _json_fe
    def _lap_get(x, dist):
        d = _json_fe.loads(x) if isinstance(x, str) else (x or {})
        v = d.get(dist)  # int key (Python直接)
        if v is None:
            v = d.get(str(dist))  # str key (JSON経由)
        return v

    if 'lap_cumulative' in df.columns:
        for _dist in [200, 400, 600, 800, 1000, 1200, 1400, 1600, 1800, 2000, 2200, 2400]:
            df[f'lap_{_dist}m'] = df['lap_cumulative'].apply(lambda x, d=_dist: _lap_get(x, d))
            df[f'lap_{_dist}m'] = pd.to_numeric(df[f'lap_{_dist}m'], errors='coerce')
        df = df.drop(columns=['lap_cumulative'], errors='ignore')
    if 'lap_sectional' in df.columns:
        for _dist in [200, 400, 600, 800, 1000, 1200, 1400, 1600, 1800, 2000, 2200, 2400]:
            df[f'lap_sect_{_dist}m'] = df['lap_sectional'].apply(lambda x, d=_dist: _lap_get(x, d))
            df[f'lap_sect_{_dist}m'] = pd.to_numeric(df[f'lap_sect_{_dist}m'], errors='coerce')
        df = df.drop(columns=['lap_sectional'], errors='ignore')

    return df
