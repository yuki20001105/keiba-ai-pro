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
    cutoff_date = (pd.to_datetime(current_date) - pd.Timedelta(days=days)).strftime('%Y%m%d')
    
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
        course_features = df.apply(
            lambda row: get_course_features(row['race_id'], row['distance'], row['surface']),
            axis=1
        )
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
        df['trainer_recent_win_rate'] = [s['trainer_recent_win_rate'] for s in trainer_stats]
    
    return df
