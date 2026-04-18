"""
特徴量エンジニアリング用のユーティリティ関数
派生特徴量の計算を行う
"""
from pathlib import Path
from typing import Optional
import functools
import re
import yaml
import pandas as pd
import numpy as np


# ===========================================================================
# 脚質分類ユーティリティ（動画: コーナー通過順位 → 逃げ/先行/差し/追込）
# ===========================================================================

def classify_running_style(corners: list, n_horses: object) -> Optional[str]:
    """コーナー通過順位リストから脚質を判定する。

    Args:
        corners:   コーナー通過順位のリスト（例: [2, 3, 3, 2]）。
        n_horses:  出走頭数。1/3・2/3 の境界計算に使用。

    Returns:
        "逃げ" / "先行" / "差し" / "追込" のいずれか、または None（データ不足時）。
    """
    if not isinstance(corners, list) or len(corners) == 0:
        return None
    try:
        nh = max(int(float(n_horses or 8)), 2)
    except (TypeError, ValueError):
        nh = 8

    first    = corners[0]
    mean_pos = sum(corners) / len(corners)
    third    = nh / 3.0

    if first <= 2 and mean_pos <= third:
        return "逃げ"
    elif mean_pos <= third:
        return "先行"
    elif mean_pos <= nh * 2 / 3.0:
        return "差し"
    else:
        return "追込"


# ===========================================================================
# レースクラス数値化ユーティリティ（動画: regex ベース G1〜新馬マッピング）
# ===========================================================================

_RUNNING_STYLE_NUM = {"逃げ": 0, "先行": 1, "差し": 2, "追込": 3}

def _race_class_to_num(race_name: object, race_class: object) -> float:
    """レース名・クラス文字列から数値グレードを返す（高いほど格上）。

    マッピング（JRA）:
      G1=8, G2=7, G3=6, Listed/L=5, OP/オープン=4,
      3勝クラス=3, 2勝クラス=2, 1勝クラス=1, 未勝利=0, 新馬=-1
    マッピング（地方競馬クラス）:
      A/A1/重賞=4, A2/A3=3, B/B1=2, B2/B3=1, C/C1=0.5, C2/C3=0, 年齢限定=0
    """
    name     = str(race_name  or '').strip()
    cls      = str(race_class or '').strip()
    combined = name + ' ' + cls

    if re.search(r'G[Ⅰ1]|\(G1\)|GI(?!I)',  combined):          return 8.0
    if re.search(r'G[Ⅱ2]|\(G2\)|GII(?!I)', combined):          return 7.0
    if re.search(r'G[Ⅲ3]|\(G3\)|GIII',     combined):          return 6.0
    if re.search(r'\(L\)|Listed|リステッド', combined):          return 5.0
    if re.search(r'オープン|Open|\(OP\)|OP\b', combined,
                 re.IGNORECASE):                                  return 4.0
    if re.search(r'3勝',                     combined):          return 3.0
    if re.search(r'2勝',                     combined):          return 2.0
    if re.search(r'1勝',                     combined):          return 1.0
    if re.search(r'未勝利',                  combined):          return 0.0
    if re.search(r'新馬',                    combined):          return -1.0
    # ── 地方競馬クラス（race_class のみで判定）────────────────────────────
    if re.match(r'^重賞$',          cls):                        return 4.0
    if re.match(r'^A1?$',           cls):                        return 4.0
    if re.match(r'^A[23]$',         cls):                        return 3.0
    if re.match(r'^B1?$',           cls):                        return 2.0
    if re.match(r'^B[23]$',         cls):                        return 1.0
    if re.match(r'^C1?$',           cls):                        return 0.5
    if re.match(r'^C[23]|^C1[二三四]?', cls):                   return 0.0
    # 年齢限定（3歳/4歳 等）: 未勝利と同等扱い
    if re.match(r'^\d+歳',          cls):                        return 0.0
    return np.nan


@functools.lru_cache(maxsize=1)
def load_course_master(yaml_path: Optional[Path] = None) -> dict:
    """コース特性マスターデータを読み込む（初回のみファイル読み込み、以降はキャッシュ）"""
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


def _get_course_features_by_venue(venue_code: str, distance: int, surface: str = "turf") -> dict:
    """venue_code を直接受け取るコース特性取得（vectorize用）。

    get_course_features と同ロジックだが extract_race_info / load_course_master の
    重複呼び出しを省くためにインライン化している。
    """
    course_master = load_course_master()
    if venue_code not in course_master['courses']:
        return {'straight_length': 300.0, 'track_type': 'unknown',
                'corner_radius': 'medium', 'inner_bias': 0}
    venue_data = course_master['courses'][venue_code]
    surface_data = venue_data.get(str(surface), {})
    distance_str = str(distance)
    if distance_str in surface_data:
        course_data = surface_data[distance_str]
    elif 'default' in surface_data:
        course_data = surface_data['default']
    else:
        available = [k for k in surface_data if k != 'default']
        if available:
            closest = min(available, key=lambda x: abs(int(x) - int(distance)))
            course_data = surface_data[closest]
        else:
            course_data = {}
    return {
        'straight_length': course_data.get('straight_length', 300.0),
        'track_type':      course_data.get('track_type', 'unknown'),
        'corner_radius':   course_data.get('corner_radius', 'medium'),
        'inner_bias':      course_data.get('inner_bias', 0),
    }



def parse_race_time_to_seconds(time_val) -> float:
    """'1:34.5' や '94.5' 形式のタイム文字列・数値を秒数（float）に変換する。

    動画#16で紹介された手法: pd.to_datetimeを使わず、正規表現で '分:秒.端数' を
    直接パースすることで秒単位への変換を確実に行う。

    Args:
        time_val: タイム値。'1:34.5', '1:34', 94.5, None 等を受け付ける。
    Returns:
        float: 秒数。変換不能な場合は np.nan を返す。
    """
    if time_val is None:
        return np.nan
    if isinstance(time_val, (int, float)):
        return float(time_val) if not np.isnan(float(time_val)) else np.nan
    s = str(time_val).strip()
    if not s or s in ('nan', 'None', '-', ''):
        return np.nan
    # '分:秒' または '分:秒.端数' 形式
    if ':' in s:
        m = re.match(r'^(\d+):(\d+)(?:\.(\d+))?$', s)
        if m:
            minutes = int(m.group(1))
            seconds = int(m.group(2))
            frac    = float('0.' + m.group(3)) if m.group(3) else 0.0
            return float(minutes * 60 + seconds + frac)
        return np.nan
    # 既に数値文字列
    try:
        return float(s)
    except ValueError:
        return np.nan


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


# =========================================================
# ベクトル化 expanding window ヘルパー（モジュールレベル）
# =========================================================

def _expanding_win_rate_by_group(
    history_df: pd.DataFrame,
    id_col: str,
    group_col: str,
    out_rate: str,
    out_cnt: str,
) -> pd.DataFrame:
    """id_col × group_col でグループ化した expanding window 勝率を計算。

    各行には「その行より前のレース（race_id 昇順）」だけを使った勝率をセットする。
    horse_surface_win_rate, jockey_course_win_rate 等の計算に使用。
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


def _expanding_grouped_stats(
    history_df: pd.DataFrame,
    id_col: str,
    group_col: str,
    out_prefix: str,
) -> pd.DataFrame:
    """id_col × group_col で expanding window の勝率・平均着順・レース数を計算。

    `_expanding_win_rate_by_group` の拡張版で win_rate / avg_finish / races を同時に生成。
    horse_distance_win_rate, horse_distance_avg_finish 等の計算に使用。
    """
    needed = ['race_id', 'finish', id_col, group_col]
    if any(c not in history_df.columns for c in needed):
        return history_df
    orig_idx = history_df.index.copy()
    s = history_df.sort_values('race_id', kind='mergesort').copy()
    fin_num  = pd.to_numeric(s['finish'], errors='coerce').fillna(0)
    win_flag = (fin_num == 1).astype(float)
    grp      = s[[id_col, group_col]].astype(str).apply('|'.join, axis=1)
    race_cnt = s.groupby(grp, sort=False).cumcount()
    s['_w'] = win_flag
    s['_f'] = fin_num
    cum_wins = s.groupby(grp, sort=False)['_w'].cumsum() - s['_w']
    cum_fin  = s.groupby(grp, sort=False)['_f'].cumsum() - s['_f']
    s.drop(columns=['_w', '_f'], inplace=True)
    s[f'{out_prefix}_win_rate']   = (cum_wins / race_cnt.clip(1)).fillna(0.0)
    s[f'{out_prefix}_avg_finish'] = (cum_fin  / race_cnt.clip(1)).fillna(0.0)
    s[f'{out_prefix}_races']      = race_cnt
    s_back = s.reindex(orig_idx)
    history_df = history_df.copy()
    for col in [f'{out_prefix}_win_rate', f'{out_prefix}_avg_finish', f'{out_prefix}_races']:
        history_df[col] = s_back[col].values
    return history_df


def _expanding_stats(
    history_df: pd.DataFrame,
    eid_col: str,
    prefix: str,
) -> pd.DataFrame:
    """騎手・調教師の race_id 時系列での expanding window 統計を計算。

    各行には「その行より前のレース」だけを使った統計をセット。
    出力列:
      - {prefix}_win_rate        : 通算勝率
      - {prefix}_show_rate       : 3着以内率
      - {prefix}_place_rate_top2 : 2着以内率
    """
    if eid_col not in history_df.columns:
        return history_df
    orig_idx   = history_df.index.copy()
    s          = history_df.sort_values('race_id', kind='mergesort').copy()
    fin_num    = pd.to_numeric(s['finish'], errors='coerce').fillna(0)
    win_flag   = (fin_num == 1).astype(float)
    place_flag = (fin_num <= 3).astype(float)   # 3着以内
    show_flag  = (fin_num <= 2).astype(float)   # 2着以内
    race_cnt   = s.groupby(eid_col, sort=False).cumcount()
    s['_w']  = win_flag
    s['_pl'] = place_flag
    s['_sh'] = show_flag
    cum_w  = s.groupby(eid_col, sort=False)['_w'].cumsum()  - s['_w']
    cum_pl = s.groupby(eid_col, sort=False)['_pl'].cumsum() - s['_pl']
    cum_sh = s.groupby(eid_col, sort=False)['_sh'].cumsum() - s['_sh']
    s.drop(columns=['_w', '_pl', '_sh'], inplace=True)
    s[f'{prefix}_win_rate']        = (cum_w  / race_cnt.clip(1)).fillna(0.0)
    s[f'{prefix}_show_rate']       = (cum_pl / race_cnt.clip(1)).fillna(0.0)
    s[f'{prefix}_place_rate_top2'] = (cum_sh / race_cnt.clip(1)).fillna(0.0)
    s_back = s.reindex(orig_idx)
    history_df = history_df.copy()
    for col in [f'{prefix}_win_rate', f'{prefix}_show_rate', f'{prefix}_place_rate_top2']:
        history_df[col] = s_back[col].values
    return history_df


# ===========================================================================
# Private pipeline stages for add_derived_features
# ===========================================================================

def _fe_days_from_history(df: pd.DataFrame, full_history_df: pd.DataFrame) -> pd.DataFrame:
    """[P3-1] DB全履歴から馬ごとに days_since_last_race を計算して付与する。

    prev_race_date はスクレイプ時点の最新レース日のため負になるケースがある。
    DB全履歴を使って horse 別・時系列順に正確に計算し、
    rest_category / is_missing フラグより前に適用する。
    """
    if not ('horse_id'  in full_history_df.columns and
            'race_date' in full_history_df.columns and
            'race_id'   in full_history_df.columns and
            'horse_id'  in df.columns and
            'race_id'   in df.columns):
        return df

    _hist = full_history_df[['horse_id', 'race_id', 'race_date']].copy()
    _hist['_rdt'] = pd.to_datetime(
        _hist['race_date'].astype(str).str.strip(), format='%Y%m%d', errors='coerce'
    )
    if _hist['_rdt'].isna().all():
        _hist['_rdt'] = pd.to_datetime(
            _hist['race_date'].astype(str).str.strip(), errors='coerce'
        )
    _hist = (_hist
             .sort_values(['horse_id', '_rdt'])
             .drop_duplicates(subset=['horse_id', 'race_id']))
    _hist['_prev_rdt'] = _hist.groupby('horse_id', sort=False)['_rdt'].shift(1)
    _hist['_days_db']  = (_hist['_rdt'] - _hist['_prev_rdt']).dt.days
    _hist = _hist[['horse_id', 'race_id', '_days_db']].dropna(subset=['_days_db'])
    _hist = _hist[_hist['_days_db'] > 0]

    df = df.merge(_hist, on=['horse_id', 'race_id'], how='left')
    if 'days_since_last_race' not in df.columns:
        df['days_since_last_race'] = df['_days_db'].where(df['_days_db'] >= 0)
    else:
        _bad = df['days_since_last_race'].isna() | (df['days_since_last_race'] < 0)
        df.loc[_bad, 'days_since_last_race'] = (
            df.loc[_bad, '_days_db'].where(df.loc[_bad, '_days_db'] >= 0, np.nan)
        )
    return df.drop(columns=['_days_db'])


def _fe_horse_category(df: pd.DataFrame) -> pd.DataFrame:
    """性齢・コーナー通過・ペース・上がり順位・休養カテゴリ派生特徴量を追加する。"""
    # 性別ダミー + 年齢カテゴリ
    if 'sex' in df.columns and 'age' in df.columns:
        if df['sex'].dtype == 'object':
            df = pd.concat([df, pd.get_dummies(df['sex'], prefix='sex')], axis=1)
        df['is_young']   = (df['age'] <= 3).astype(int)
        df['is_prime']   = ((df['age'] >= 4) & (df['age'] <= 6)).astype(int)
        df['is_veteran'] = (df['age'] >= 7).astype(int)

    # コーナー通過順位
    if 'corner_positions_list' in df.columns:
        _cp = df['corner_positions_list']
        df['corner_position_avg']      = _cp.apply(lambda x: np.mean(x) if isinstance(x, list) and x else np.nan)
        df['corner_position_variance'] = _cp.apply(lambda x: np.var(x)  if isinstance(x, list) and len(x) > 1 else 0)
        df['last_corner_position']     = _cp.apply(lambda x: x[-1]      if isinstance(x, list) and x else np.nan)
        df['position_change']          = _cp.apply(lambda x: x[0] - x[-1] if isinstance(x, list) and len(x) > 1 else 0)

        # 脚質分類（逃げ/先行/差し/追込）
        _nh_col = df['n_horses'] if 'n_horses' in df.columns else pd.Series([8] * len(df), index=df.index)
        df['running_style'] = [
            classify_running_style(c, nh)
            for c, nh in zip(_cp, _nh_col)
        ]
        df['running_style_num'] = df['running_style'].map(_RUNNING_STYLE_NUM)

    # ペース区分ダミー
    if 'pace_classification' in df.columns:
        df = pd.concat([df, pd.get_dummies(df['pace_classification'], prefix='pace')], axis=1)

    # 上がり3F順位の正規化
    if 'last_3f_rank' in df.columns and 'num_horses' in df.columns:
        df['last_3f_rank_normalized'] = df['last_3f_rank'] / df['num_horses']

    # 休養期間カテゴリ（days_since_last_race が P3-1 で設定済みの場合）
    if 'days_since_last_race' in df.columns:
        df['rest_category'] = pd.cut(
            df['days_since_last_race'],
            bins=[-np.inf, 14, 30, 60, np.inf],
            labels=['short', 'normal', 'long', 'very_long'],
        )
        df = pd.concat([df, pd.get_dummies(df['rest_category'], prefix='rest')], axis=1)

    if 'last_distance_change' in df.columns:
        df['distance_increased'] = (df['last_distance_change'] > 0).astype(int)
        df['distance_decreased'] = (df['last_distance_change'] < 0).astype(int)

    if 'popularity_trend' in df.columns:
        df = pd.concat([df, pd.get_dummies(df['popularity_trend'], prefix='pop_trend')], axis=1)

    return df


def _fe_id_season(df: pd.DataFrame) -> pd.DataFrame:
    """race_id から venue_code / race_num / n_horses と季節・性別交互作用特徴量を追加する。"""
    # race_id → venue_code / race_num（O(n) str スライス）
    df['venue_code'] = df['race_id'].str[8:10]
    df['race_num']   = pd.to_numeric(df['race_id'].str[10:12], errors='coerce')

    # 出走頭数（同一race_id内の行数）
    df['n_horses'] = df.groupby('race_id', sort=False)['race_id'].transform('count')

    # 季節性特徴量（cos_date / sin_date / seasonal_sex）
    _rdate_src = df['race_date'] if 'race_date' in df.columns else df['race_id'].str[:8]
    _rdt_parsed = pd.to_datetime(_rdate_src.astype(str).str.strip(), format='%Y%m%d', errors='coerce')
    if _rdt_parsed.isna().mean() > 0.5:
        _rdt_parsed = pd.to_datetime(_rdate_src.astype(str).str.strip(), errors='coerce')
    _doy = _rdt_parsed.dt.dayofyear.astype(float)
    df['cos_date'] = np.cos(2.0 * np.pi * _doy / 365.0)
    df['sin_date'] = np.sin(2.0 * np.pi * _doy / 365.0)

    # 季節区分（0=冬[12-2月], 1=春[3-5月], 2=夏[6-8月], 3=秋[9-11月]）
    # sin_date/cos_date の補完的な離散特徴量（日本競馬のシーズナル G1 パターンを捉える）
    _month = _rdt_parsed.dt.month
    _season_map = {1: 0, 2: 0, 3: 1, 4: 1, 5: 1, 6: 2, 7: 2, 8: 2, 9: 3, 10: 3, 11: 3, 12: 0}
    df['season'] = _month.map(_season_map).fillna(0).astype(int)

    _SEX_CODE = {'牡': -1, '牝': 1, 'セ': -1, 'セン': -1, 'male': -1, 'female': 1, 'gelding': -1}
    if 'sex' in df.columns:
        _sc = df['sex'].map(_SEX_CODE).fillna(0.0)
        df['sex_code']     = _sc
        df['seasonal_sex'] = df['cos_date'] * _sc

    # 馬場種別 × 枠番 交互作用（frame_race_type）
    _surf_col = df.get('surface') if 'surface' in df.columns else None
    _brk_col  = (df['bracket'] if 'bracket' in df.columns
                 else df['bracket_number'] if 'bracket_number' in df.columns
                 else None)
    if _surf_col is not None and _brk_col is not None:
        _SURF_CODE = {'ダート': 1, 'dirt': 1, '芝': -1, 'turf': -1, 'sand': 1, 'ばんえい': 1}
        df['frame_race_type'] = _surf_col.map(_SURF_CODE).fillna(0.0) * pd.to_numeric(_brk_col, errors='coerce').fillna(0.0)

    # レースクラス数値化（動画: regex ベース G1〜新馬マッピング）
    _race_name_col  = df['race_name']  if 'race_name'  in df.columns else pd.Series([''] * len(df), index=df.index)
    _race_class_col = df['race_class'] if 'race_class' in df.columns else pd.Series([''] * len(df), index=df.index)
    df['race_class_num'] = [
        _race_class_to_num(n, c)
        for n, c in zip(_race_name_col, _race_class_col)
    ]

    return df


def _fe_course(df: pd.DataFrame) -> pd.DataFrame:
    """コース特性（直線長・コーナー半径・内枠バイアス）を venue_code × distance × surface ごとに付与する。"""
    if 'distance' not in df.columns or 'surface' not in df.columns:
        return df

    _SF_EN = {'芝': 'turf', 'ダート': 'dirt', 'ばんえい': 'dirt', 'sand': 'dirt'}
    if 'surface_en' in df.columns:
        _surface_col = 'surface_en'
    else:
        df['_surface_tmp'] = df['surface'].map(lambda v: _SF_EN.get(str(v), v) if pd.notna(v) else 'turf')
        _surface_col = '_surface_tmp'

    # venue_code はすでに _fe_id_season() で付与済み。なければ抽出する。
    if '_vcol' not in df.columns:
        df['_vcol'] = df['race_id'].str[8:10]
    else:
        df['_vcol'] = df['venue_code']

    # ユニークな組み合わせのみ計算してマージ（O(行数) → O(ユニーク数)）
    unique_combos = df[['_vcol', 'distance', _surface_col]].drop_duplicates().copy()
    combo_feats = unique_combos.apply(
        lambda row: pd.Series(_get_course_features_by_venue(row['_vcol'], row['distance'], row[_surface_col])),
        axis=1,
    )
    unique_combos[['straight_length', 'track_type', 'corner_radius', 'inner_bias']] = combo_feats
    # マージ時のサフィックス衝突を防ぐ: db_ultimate_loader で surface にコピー済みの
    # track_type 列（芝/ダート）は不要なので、コースマスター由来の track_type を優先する
    if 'track_type' in df.columns:
        df = df.drop(columns=['track_type'])
    df = df.merge(unique_combos, on=['_vcol', 'distance', _surface_col], how='left')
    df = df.drop(columns=['_vcol'], errors='ignore')
    if '_surface_tmp' in df.columns:
        df = df.drop(columns=['_surface_tmp'])

    # 内枠有利コース × 内枠 の交互作用
    if 'inner_bias' in df.columns:
        brk_col = 'bracket' if 'bracket' in df.columns else 'bracket_number' if 'bracket_number' in df.columns else None
        if brk_col:
            df['inner_advantage'] = df['inner_bias'] * (pd.to_numeric(df[brk_col], errors='coerce') <= 3).astype(int)

    return df


def _fe_market(df: pd.DataFrame) -> pd.DataFrame:
    """オッズ系特徴量（implied_prob / 市場エントロピー / レース内z-score 等）を追加する。"""
    if 'odds' not in df.columns:
        return df

    _o = pd.to_numeric(df['odds'], errors='coerce')
    df['implied_prob']     = np.where(_o > 0, 1.0 / _o, np.nan)
    df['odds_is_missing']  = _o.isna().astype(int)

    if 'race_id' in df.columns:
        # レース内正規化・順位・z-score
        df['implied_prob_norm'] = df.groupby('race_id')['implied_prob'].transform(
            lambda x: x / x.sum() if x.sum() > 0 else x
        )
        df['odds_rank_in_race'] = df.groupby('race_id')['odds'].rank(method='min', na_option='bottom')
        df['odds_z_in_race']    = df.groupby('race_id')['odds'].transform(
            lambda x: (x - x.mean()) / (x.std() + 1e-8)
        )
        # 市場エントロピー / 上位3頭の暗黙確率和
        def _market_stats(grp: pd.DataFrame) -> pd.Series:
            o = pd.to_numeric(grp['odds'], errors='coerce').dropna()
            if len(o) < 2:
                return pd.Series({'market_entropy': 0.0, 'top3_probability': 0.5})
            probs = 1.0 / o
            total = probs.sum()
            if total == 0:
                return pd.Series({'market_entropy': 0.0, 'top3_probability': 0.5})
            probs /= total
            return pd.Series({
                'market_entropy':   float(-np.sum(probs * np.log(probs + 1e-10))),
                'top3_probability': float(probs.nlargest(3).sum()),
            })
        market_agg = df.groupby('race_id', sort=False).apply(_market_stats, include_groups=False).reset_index()
        df = df.merge(market_agg, on='race_id', how='left')
    else:
        # 単レース予測（race_id なし）
        df['implied_prob_norm'] = df['implied_prob']
        df['odds_rank_in_race'] = _o.rank(method='min', na_option='bottom')
        df['odds_z_in_race']    = (_o - _o.mean()) / (_o.std() + 1e-8)

    if 'popularity' in df.columns:
        df['popularity_is_missing'] = pd.to_numeric(df['popularity'], errors='coerce').isna().astype(int)
        # ITR-07: 出走頭数で割った相対人気順位（レース規模不変の市場シグナル）
        if 'num_horses' in df.columns:
            _pop = pd.to_numeric(df['popularity'], errors='coerce')
            _nh  = pd.to_numeric(df['num_horses'],  errors='coerce').replace(0, np.nan)
            df['popularity_normalized'] = _pop / _nh

    return df


def _fe_prev_race(df: pd.DataFrame) -> pd.DataFrame:
    """前走日由来の days_since_last_race 補完・距離変化・馬の通算勝率・スピード指数を追加する。"""
    # prev_race_date → days 補完（DB 計算値が優先、こちらは残った NaN を埋める）
    if 'prev_race_date' in df.columns:
        if 'race_date' in df.columns:
            _race_dt = pd.to_datetime(df['race_date'].astype(str).str.strip(), format='%Y%m%d', errors='coerce')
            if _race_dt.isna().mean() > 0.5:
                _race_dt = pd.to_datetime(df['race_date'].astype(str).str.strip(), errors='coerce')
        else:
            import warnings as _w
            _w.warn("race_date 列がありません。race_id[:8] で代替しますが精度が低下します。", UserWarning, stacklevel=3)
            _race_dt = pd.to_datetime(df['race_id'].str[:8], format='%Y%m%d', errors='coerce')
        _prev_dt     = pd.to_datetime(df['prev_race_date'].astype(str).str.replace('/', '-').str.strip(), errors='coerce')
        _scraped_days = (_race_dt - _prev_dt).dt.days.where(lambda d: d >= 1, np.nan)
        if 'days_since_last_race' not in df.columns:
            df['days_since_last_race'] = _scraped_days
        else:
            df['days_since_last_race'] = df['days_since_last_race'].fillna(_scraped_days)

    # 前走からの距離変化
    if 'prev_race_distance' in df.columns and 'distance' in df.columns:
        _pd_val = pd.to_numeric(df['prev_race_distance'], errors='coerce')
        _cd_val = pd.to_numeric(df['distance'], errors='coerce')
        df['distance_change']    = _cd_val - _pd_val
        df['distance_increased'] = (df['distance_change'] > 0).astype(int)
        df['distance_decreased'] = (df['distance_change'] < 0).astype(int)

    # 馬の通算勝率
    if 'horse_total_runs' in df.columns and 'horse_total_wins' in df.columns:
        _runs = pd.to_numeric(df['horse_total_runs'], errors='coerce')
        _wins = pd.to_numeric(df['horse_total_wins'], errors='coerce')
        df['horse_win_rate'] = np.where(_runs > 0, _wins / _runs, np.nan)

    # スピード指数（前走タイム ÷ 距離、同条件 z-score）
    if 'prev_race_time' in df.columns and 'prev_race_distance' in df.columns:
        df['prev_race_time_seconds'] = df['prev_race_time'].apply(parse_race_time_to_seconds)
        _pt = df['prev_race_time_seconds']
        _pd2 = pd.to_numeric(df['prev_race_distance'], errors='coerce')
        df['prev_speed_index'] = np.where((_pt > 0) & (_pd2 > 0), _pd2 / _pt, np.nan)
        _grp = [c for c in ('surface', 'prev_race_distance') if c in df.columns]
        if _grp:
            df['prev_speed_zscore'] = df.groupby(_grp, sort=False, dropna=False)['prev_speed_index'].transform(
                lambda x: (x - x.mean()) / (x.std() + 1e-8) if len(x) > 1 else 0.0
            )
        else:
            df['prev_speed_zscore'] = 0.0

    # 前々走スピード指数（prev2_race_time は DB で秒数で格納、距離との高相関 r=0.980 を解消）
    if 'prev2_race_time' in df.columns and 'prev2_race_distance' in df.columns:
        _p2t = pd.to_numeric(df['prev2_race_time'], errors='coerce')
        _p2d = pd.to_numeric(df['prev2_race_distance'], errors='coerce')
        df['prev2_speed_index'] = np.where((_p2t > 0) & (_p2d > 0), _p2d / _p2t, np.nan)
        _grp2 = [c for c in ('surface', 'prev2_race_distance') if c in df.columns]
        if _grp2:
            df['prev2_speed_zscore'] = df.groupby(_grp2, sort=False, dropna=False)['prev2_speed_index'].transform(
                lambda x: (x - x.mean()) / (x.std() + 1e-8) if len(x) > 1 else 0.0
            )
        else:
            df['prev2_speed_zscore'] = 0.0

    # 近走フォーム加重平均（ITR-04: 直近重視のモメンタム特徴量）
    # 単純な prev_race_finish(1.82%) より時系列的な改善/悪化トレンドを捉える
    if 'prev_race_finish' in df.columns and 'prev2_race_finish' in df.columns:
        _pf1 = pd.to_numeric(df['prev_race_finish'], errors='coerce')
        _pf2 = pd.to_numeric(df['prev2_race_finish'], errors='coerce')
        # 0.6×前走 + 0.4×前々走（直近を重視）、片方欠損の場合は有効な方のみ使用
        df['recent_form_weighted'] = np.where(
            _pf1.notna() & _pf2.notna(), 0.6 * _pf1 + 0.4 * _pf2,
            np.where(_pf1.notna(), _pf1, _pf2)
        )
        # 改善/悪化トレンド（前走 - 前々走、負 = 改善、正 = 悪化）
        df['form_trend'] = np.where(
            _pf1.notna() & _pf2.notna(), _pf1 - _pf2, np.nan
        )

    # ── ITR-04: スピード指数変化（prev_speed_index - prev2_speed_index）
    # 正 = スピード改善、負 = 低下; form_trend（着順）の補完
    if 'prev_speed_index' in df.columns and 'prev2_speed_index' in df.columns:
        _spi1 = pd.to_numeric(df['prev_speed_index'], errors='coerce')
        _spi2 = pd.to_numeric(df['prev2_speed_index'], errors='coerce')
        df['speed_index_change'] = np.where(
            _spi1.notna() & _spi2.notna(), _spi1 - _spi2, np.nan
        )

    return df


def _fe_missing_flags(df: pd.DataFrame) -> pd.DataFrame:
    """数値欠損フラグ（{col}_is_missing）を生成し、対象列を数値型に統一する。"""
    _FLAG_COLS = [
        'prev_race_finish', 'prev_race_time', 'prev_race_distance',
        'prev2_race_finish', 'prev2_race_distance',
        'days_since_last_race',
        'prev_speed_index', 'prev_speed_zscore',
        'prev2_speed_index', 'prev2_speed_zscore',
        'horse_win_rate', 'race_class_num',
    ]
    for col in _FLAG_COLS:
        if col in df.columns:
            _s = pd.to_numeric(df[col], errors='coerce')
            df[f'{col}_is_missing'] = _s.isna().astype(int)
            df[col] = _s  # NaN のまま保持

    if 'prev_race_finish' in df.columns:
        df['prev_race_finish'] = pd.to_numeric(df['prev_race_finish'], errors='coerce')

    return df


def _fe_lap(df: pd.DataFrame) -> pd.DataFrame:
    """ラップタイム展開（lap_Xm / lap_sect_Xm）とペース要約特徴量を追加する。"""
    import json as _json_fe
    _DISTS = [200, 400, 600, 800, 1000, 1200, 1400, 1600, 1800, 2000, 2200, 2400]

    def _lap_get(x, dist):
        d = _json_fe.loads(x) if isinstance(x, str) else (x or {})
        return d.get(dist) if d.get(dist) is not None else d.get(str(dist))

    if 'lap_cumulative' in df.columns:
        for _d in _DISTS:
            df[f'lap_{_d}m'] = pd.to_numeric(
                df['lap_cumulative'].apply(lambda x, d=_d: _lap_get(x, d)), errors='coerce'
            )
        df = df.drop(columns=['lap_cumulative'])

    if 'lap_sectional' in df.columns:
        for _d in _DISTS:
            df[f'lap_sect_{_d}m'] = pd.to_numeric(
                df['lap_sectional'].apply(lambda x, d=_d: _lap_get(x, d)), errors='coerce'
            )
        df = df.drop(columns=['lap_sectional'])

    # ペース要約（前半 / 後半 平均 + 差分）
    _sect_cols = [c for c in df.columns if c.startswith('lap_sect_') and c.endswith('m')]
    if not _sect_cols or 'distance' not in df.columns:
        return df

    _all_dists_sorted = sorted(int(c.replace('lap_sect_', '').replace('m', '')) for c in _sect_cols)

    def _pace_summary(row) -> pd.Series:
        try:
            dist = int(float(row.get('distance', 0)))
        except (TypeError, ValueError):
            dist = 0
        valid = [d for d in _all_dists_sorted if d <= dist]
        if len(valid) < 2:
            return pd.Series({'race_pace_front': np.nan, 'race_pace_back': np.nan,
                               'race_pace_diff': np.nan, 'race_pace_ratio': np.nan})
        mid   = len(valid) // 2
        front = [v for d in valid[:mid]  if pd.notna(v := row.get(f'lap_sect_{d}m')) and v > 0]
        back  = [v for d in valid[mid:]  if pd.notna(v := row.get(f'lap_sect_{d}m')) and v > 0]
        if not front or not back:
            return pd.Series({'race_pace_front': np.nan, 'race_pace_back': np.nan,
                               'race_pace_diff': np.nan, 'race_pace_ratio': np.nan})
        fp, bp = float(np.mean(front)), float(np.mean(back))
        return pd.Series({
            'race_pace_front': fp,
            'race_pace_back':  bp,
            'race_pace_diff':  fp - bp,
            'race_pace_ratio': fp / bp if bp > 0 else np.nan,
        })

    _pace_df = df.apply(_pace_summary, axis=1)
    for col in ['race_pace_front', 'race_pace_back', 'race_pace_diff', 'race_pace_ratio']:
        df[col] = _pace_df[col]

    return df


def _fe_payout(df: pd.DataFrame) -> pd.DataFrame:
    """配当情報（tansho / sanrentan）から派生特徴量を追加する。"""
    if 'tansho_payout' in df.columns:
        _tp = pd.to_numeric(df['tansho_payout'], errors='coerce')
        df['tansho_implied_prob']    = np.where(_tp > 0, 100.0 / _tp, np.nan)
        df['tansho_payout_log']      = np.log1p(_tp.fillna(0))
        df['tansho_payout_is_missing'] = _tp.isna().astype(int)

    if 'sanrentan_payout' in df.columns:
        _stp = pd.to_numeric(df['sanrentan_payout'], errors='coerce')
        df['sanrentan_payout_log']      = np.log1p(_stp.fillna(0))
        df['sanrentan_payout_is_missing'] = _stp.isna().astype(int)
        if 'race_id' in df.columns:
            df['sanrentan_z_in_races'] = df.groupby('race_id')['sanrentan_payout_log'].transform(
                lambda x: (x - x.mean()) / (x.std() + 1e-8) if len(x) > 1 else 0.0
            )

    return df


def _fe_history(df: pd.DataFrame, full_history_df: pd.DataFrame) -> pd.DataFrame:
    """full_history_df を用いる全 expanding window / rolling 統計を追加する。

    内部的に以下のサブ関数を順に呼び出す:
      _feh_jockey_course      — 騎手×コース別勝率
      _feh_horse_aptitude     — 馬の距離/馬場/競馬場/複合条件別適性
      _feh_gate_bias          — 枠番バイアス（会場×距離帯×馬場）
      _feh_jt_combo           — 騎手×調教師コンビ（ベイズ平滑化）
      _feh_entity_career      — 騎手・調教師・血統の通算成績
      _feh_recent_form        — 馬の近走（past3/5/10）統計
      _feh_entity_recent30    — 騎手・調教師の近30走勝率
      _feh_last_3f            — 上がり3F rolling 統計
      _feh_payout_history     — 過去単勝配当 rolling 統計
      _feh_running_style      — 脚質 rolling 統計
    """
    df, full_history_df = _feh_jockey_course(df, full_history_df)
    df, full_history_df = _feh_horse_aptitude(df, full_history_df)
    df, full_history_df = _feh_gate_bias(df, full_history_df)
    df, full_history_df = _feh_jt_combo(df, full_history_df)
    df, full_history_df = _feh_entity_career(df, full_history_df)
    df, full_history_df = _feh_recent_form(df, full_history_df)
    df, full_history_df = _feh_entity_recent30(df, full_history_df)
    df, full_history_df = _feh_last_3f(df, full_history_df)
    df, full_history_df = _feh_payout_history(df, full_history_df)
    df, full_history_df = _feh_running_style(df, full_history_df)
    return df


# ---------------------------------------------------------------------------
# _fe_history サブ関数群
# 各関数は (df, h) を受け取り (df, h) を返す。
# df  = 現在対象の DataFrame（特徴量を追加する）
# h   = full_history_df（expanding window の計算ベース。一部のサブ関数が列を追加する）
# ---------------------------------------------------------------------------

def _feh_jockey_course(
    df: pd.DataFrame, h: pd.DataFrame
) -> tuple:
    """騎手×コース別の expanding window 勝率を付与する。"""
    if 'jockey_id' in h.columns and 'venue' in h.columns:
        h = _expanding_win_rate_by_group(h, 'jockey_id', 'venue', 'jockey_course_win_rate', 'jockey_course_races')
        df = df.merge(
            h[['jockey_id', 'race_id', 'jockey_course_win_rate', 'jockey_course_races']]
            .drop_duplicates(subset=['jockey_id', 'race_id']),
            on=['jockey_id', 'race_id'], how='left')
    return df, h


def _feh_horse_aptitude(
    df: pd.DataFrame, h: pd.DataFrame
) -> tuple:
    """馬の距離別 / 馬場別 / 距離帯別 / 競馬場別 / 複合条件別 適性を付与する。"""
    if 'horse_id' in h.columns and 'distance' in h.columns:
        h = _expanding_grouped_stats(h, 'horse_id', 'distance', 'horse_distance')
        df = df.merge(
            h[['horse_id', 'race_id', 'horse_distance_win_rate', 'horse_distance_avg_finish', 'horse_distance_races']]
            .drop_duplicates(subset=['horse_id', 'race_id']),
            on=['horse_id', 'race_id'], how='left')

    if 'finish' not in h.columns or 'race_id' not in h.columns:
        return df, h

    # 馬場別
    if 'horse_id' in h.columns and 'surface' in h.columns:
        h = _expanding_win_rate_by_group(h, 'horse_id', 'surface', 'horse_surface_win_rate', 'horse_surface_races')
        df = df.merge(
            h[['horse_id', 'race_id', 'horse_surface_win_rate', 'horse_surface_races']]
            .drop_duplicates(subset=['horse_id', 'race_id']),
            on=['horse_id', 'race_id'], how='left')

    # 距離帯別
    if 'horse_id' in h.columns and 'distance' in h.columns:
        h['_dist_band'] = h['distance'].apply(_dist_band)
        h = _expanding_win_rate_by_group(h, 'horse_id', '_dist_band', 'horse_dist_band_win_rate', 'horse_dist_band_races')
        df = df.merge(
            h[['horse_id', 'race_id', 'horse_dist_band_win_rate', 'horse_dist_band_races']]
            .drop_duplicates(subset=['horse_id', 'race_id']),
            on=['horse_id', 'race_id'], how='left')
        h = h.drop(columns=['_dist_band'], errors='ignore')

    # 競馬場別
    if 'horse_id' in h.columns and 'venue' in h.columns:
        h = _expanding_win_rate_by_group(h, 'horse_id', 'venue', 'horse_venue_win_rate', 'horse_venue_races')
        df = df.merge(
            h[['horse_id', 'race_id', 'horse_venue_win_rate', 'horse_venue_races']]
            .drop_duplicates(subset=['horse_id', 'race_id']),
            on=['horse_id', 'race_id'], how='left')

    # 競馬場 × 馬場 複合
    if all(c in h.columns for c in ('horse_id', 'venue', 'surface')):
        h['_venue_surface'] = h['venue'].astype(str) + '×' + h['surface'].astype(str)
        h = _expanding_win_rate_by_group(
            h, 'horse_id', '_venue_surface',
            'horse_venue_surface_win_rate', 'horse_venue_surface_races')
        df = df.merge(
            h[['horse_id', 'race_id', 'horse_venue_surface_win_rate', 'horse_venue_surface_races']]
            .drop_duplicates(subset=['horse_id', 'race_id']),
            on=['horse_id', 'race_id'], how='left')
        h = h.drop(columns=['_venue_surface'], errors='ignore')

    # 距離帯 × 馬場 複合
    if all(c in h.columns for c in ('horse_id', 'distance', 'surface')):
        h['_dist_surface'] = h['distance'].apply(_dist_band) + '×' + h['surface'].astype(str)
        h = _expanding_win_rate_by_group(
            h, 'horse_id', '_dist_surface',
            'horse_dist_surface_win_rate', 'horse_dist_surface_races')
        df = df.merge(
            h[['horse_id', 'race_id', 'horse_dist_surface_win_rate', 'horse_dist_surface_races']]
            .drop_duplicates(subset=['horse_id', 'race_id']),
            on=['horse_id', 'race_id'], how='left')
        h = h.drop(columns=['_dist_surface'], errors='ignore')

    return df, h


def _feh_gate_bias(
    df: pd.DataFrame, h: pd.DataFrame
) -> tuple:
    """枠番バイアス（会場×距離帯×馬場）を静的集計で付与する。"""
    if not all(c in h.columns for c in ('bracket_number', 'finish', 'venue')):
        return df, h

    _hg = h.copy()
    _hg['_is_inner'] = (pd.to_numeric(_hg['bracket_number'], errors='coerce') <= 3).astype(int)
    _hg['_is_win']   = (pd.to_numeric(_hg['finish'],         errors='coerce') == 1).astype(int)
    _hg['_dist_band'] = _hg['distance'].apply(_dist_band) if 'distance' in _hg.columns else 'unknown'
    _gate_keys = ['venue', '_dist_band'] + (['surface'] if 'surface' in _hg.columns else [])
    _gate_agg = (
        _hg.groupby(_gate_keys + ['_is_inner'], sort=False)
        .agg(_cnt=('_is_win', 'count'), _wins=('_is_win', 'sum'))
        .reset_index()
    )
    _gate_agg['_gate_wr'] = np.where(_gate_agg['_cnt'] >= 10,
                                      _gate_agg['_wins'] / _gate_agg['_cnt'], 0.5)
    df['_dist_band'] = df['distance'].apply(_dist_band) if 'distance' in df.columns else 'unknown'
    df['_is_inner']  = (
        pd.to_numeric(df.get('bracket_number', pd.Series([None] * len(df))), errors='coerce') <= 3
    ).fillna(True).astype(int)
    _merge_keys = _gate_keys + ['_is_inner']
    df = df.merge(_gate_agg[_merge_keys + ['_gate_wr']], on=_merge_keys, how='left')
    df['gate_win_rate'] = df['_gate_wr'].fillna(0.5)
    df = df.drop(columns=['_dist_band', '_is_inner', '_gate_wr'], errors='ignore')

    return df, h


def _feh_jt_combo(
    df: pd.DataFrame, h: pd.DataFrame
) -> tuple:
    """騎手×調教師コンビのベイズ平滑化 expanding window 勝率を付与する。"""
    if not all(c in h.columns for c in ('jockey_id', 'trainer_id', 'finish')):
        return df, h

    _orig = h.index.copy()
    _s    = h.sort_values('race_id', kind='mergesort').copy()
    _fin  = pd.to_numeric(_s['finish'], errors='coerce').fillna(0)
    _grp  = _s['jockey_id'].astype(str) + '|' + _s['trainer_id'].astype(str)
    _cnt  = _s.groupby(_grp, sort=False).cumcount()
    _s['_w'] = (_fin == 1).astype(float)
    _wins = _s.groupby(_grp, sort=False)['_w'].cumsum() - _s['_w']
    _s.drop(columns=['_w'], inplace=True)
    _wr     = (_wins / _cnt.clip(1)).fillna(0.0)
    _smooth = (_cnt * _wr + 5 * 0.075) / (_cnt + 5)   # ベイズ平滑化 (K=5, global_wr=0.075)
    _s[['jt_combo_races', 'jt_combo_win_rate', 'jt_combo_win_rate_smooth']] = (
        pd.DataFrame({'jt_combo_races': _cnt.values, 'jt_combo_win_rate': _wr.values,
                      'jt_combo_win_rate_smooth': _smooth.values}, index=_s.index))
    _back = _s.reindex(_orig)
    for _col in ['jt_combo_races', 'jt_combo_win_rate', 'jt_combo_win_rate_smooth']:
        h[_col] = _back[_col].values
    df = df.merge(
        h[['jockey_id', 'trainer_id', 'race_id',
           'jt_combo_races', 'jt_combo_win_rate', 'jt_combo_win_rate_smooth']]
        .drop_duplicates(subset=['jockey_id', 'trainer_id', 'race_id']),
        on=['jockey_id', 'trainer_id', 'race_id'], how='left')

    return df, h


def _feh_entity_career(
    df: pd.DataFrame, h: pd.DataFrame
) -> tuple:
    """騎手・調教師・血統の通算成績（expanding window）を付与する。"""
    if 'finish' not in h.columns or 'race_id' not in h.columns:
        return df, h

    if 'jockey_id' in df.columns:
        h = _expanding_stats(h, 'jockey_id', 'jockey')
        df = df.merge(
            h[['jockey_id', 'race_id', 'jockey_place_rate_top2', 'jockey_show_rate']]
            .drop_duplicates(subset=['jockey_id', 'race_id']),
            on=['jockey_id', 'race_id'], how='left')

    if 'trainer_id' in df.columns:
        h = _expanding_stats(h, 'trainer_id', 'trainer')
        df = df.merge(
            h[['trainer_id', 'race_id', 'trainer_win_rate', 'trainer_place_rate_top2', 'trainer_show_rate']]
            .drop_duplicates(subset=['trainer_id', 'race_id'])
            .rename(columns={'trainer_win_rate': 'fe_trainer_win_rate'}),
            on=['trainer_id', 'race_id'], how='left')

    for _sid in ('sire', 'damsire'):
        if _sid in h.columns and _sid in df.columns:
            h = _expanding_stats(h, _sid, _sid)
            df = df.merge(
                h[[_sid, 'race_id', f'{_sid}_win_rate', f'{_sid}_show_rate']]
                .drop_duplicates(subset=[_sid, 'race_id']),
                on=[_sid, 'race_id'], how='left')

    return df, h


def _feh_recent_form(
    df: pd.DataFrame, h: pd.DataFrame
) -> tuple:
    """馬の近走（past3/5/10）平均着順・勝率の rolling 統計を付与する。"""
    if not all(c in h.columns for c in ('finish', 'horse_id', 'race_id')):
        return df, h

    _oi   = h.index.copy()
    _s    = h.sort_values('race_id', kind='mergesort').copy()
    _fin  = pd.to_numeric(_s['finish'], errors='coerce')
    _win  = (_fin == 1).astype(float)
    _s['_fin'], _s['_win'] = _fin, _win
    # transform(lambda) の代わりに shift + groupby.rolling でベクトル化
    _fin_sh = _s.groupby('horse_id')['_fin'].shift(1)
    _win_sh = _s.groupby('horse_id')['_win'].shift(1)
    for _n, _sfx in [(3, 'past3'), (5, 'past5'), (10, 'past10')]:
        _s[f'{_sfx}_avg_finish'] = (
            _fin_sh.groupby(_s['horse_id'])
            .rolling(_n, min_periods=1).mean()
            .droplevel(0).reindex(_s.index))
    for _n, _sfx in [(3, 'past3'), (5, 'past5')]:
        _s[f'{_sfx}_win_rate'] = (
            _win_sh.groupby(_s['horse_id'])
            .rolling(_n, min_periods=1).mean()
            .droplevel(0).reindex(_s.index))
    _s.drop(columns=['_fin', '_win'], inplace=True)
    _past_cols = ['past3_avg_finish', 'past5_avg_finish', 'past10_avg_finish',
                  'past3_win_rate', 'past5_win_rate']
    _back = _s.reindex(_oi)
    for _c in _past_cols:
        h[_c] = _back[_c].values
    df = df.merge(
        h[['horse_id', 'race_id'] + _past_cols]
        .drop_duplicates(subset=['horse_id', 'race_id']),
        on=['horse_id', 'race_id'], how='left')

    return df, h


def _feh_entity_recent30(
    df: pd.DataFrame, h: pd.DataFrame
) -> tuple:
    """騎手・調教師の近30走勝率（rolling window）を付与する。"""
    if 'finish' not in h.columns or 'race_id' not in h.columns:
        return df, h

    for _eid, _pfx in [('jockey_id', 'jockey'), ('trainer_id', 'trainer')]:
        if _eid not in h.columns or _eid not in df.columns:
            continue
        _oi   = h.index.copy()
        _s    = h.sort_values('race_id', kind='mergesort').copy()
        _fin  = pd.to_numeric(_s['finish'], errors='coerce').fillna(0)
        _s['_win'] = (_fin == 1).astype(float)
        _rc  = f'{_pfx}_recent30_win_rate'
        _win_sh = _s.groupby(_eid)['_win'].shift(1)
        _s[_rc] = (
            _win_sh.groupby(_s[_eid])
            .rolling(30, min_periods=5).mean()
            .droplevel(0).reindex(_s.index))
        _s.drop(columns=['_win'], inplace=True)
        h[_rc] = _s.reindex(_oi)[_rc].values
        df = df.merge(
            h[[_eid, 'race_id', _rc]].drop_duplicates(subset=[_eid, 'race_id']),
            on=[_eid, 'race_id'], how='left')

    return df, h


def _feh_last_3f(
    df: pd.DataFrame, h: pd.DataFrame
) -> tuple:
    """馬の上がり3F タイム・順位の rolling 統計を付与する。"""
    for _src_col, _out_cols in [
        ('last_3f_time', ['past3_avg_last3f_time', 'past5_avg_last3f_time']),
        ('last_3f_rank', ['past3_avg_last3f_rank']),
    ]:
        if _src_col not in h.columns or 'horse_id' not in h.columns:
            continue
        _oi  = h.index.copy()
        _s   = h.sort_values('race_id', kind='mergesort').copy()
        _val = pd.to_numeric(_s[_src_col], errors='coerce')
        _s['_val'] = _val
        _val_sh = _s.groupby('horse_id')['_val'].shift(1)
        for _n, _oc in zip([3, 5] if len(_out_cols) > 1 else [3], _out_cols):
            _s[_oc] = (
                _val_sh.groupby(_s['horse_id'])
                .rolling(_n, min_periods=1).mean()
                .droplevel(0).reindex(_s.index))
        _s.drop(columns=['_val'], inplace=True)
        _back = _s.reindex(_oi)
        for _oc in _out_cols:
            h[_oc] = _back[_oc].values
        df = df.merge(
            h[['horse_id', 'race_id'] + _out_cols]
            .drop_duplicates(subset=['horse_id', 'race_id']),
            on=['horse_id', 'race_id'], how='left')

    return df, h


def _feh_payout_history(
    df: pd.DataFrame, h: pd.DataFrame
) -> tuple:
    """馬の過去単勝配当の rolling 統計（log変換）を付与する。"""
    if 'tansho_payout' not in h.columns or 'horse_id' not in h.columns:
        return df, h

    _oi  = h.index.copy()
    _s   = h.sort_values('race_id', kind='mergesort').copy()
    _s['_tpv'] = np.log1p(pd.to_numeric(_s['tansho_payout'], errors='coerce').fillna(0))
    _tpv_sh = _s.groupby('horse_id')['_tpv'].shift(1)
    _s['past5_avg_tansho_log'] = (
        _tpv_sh.groupby(_s['horse_id'])
        .rolling(5, min_periods=1).mean()
        .droplevel(0).reindex(_s.index))
    _s.drop(columns=['_tpv'], inplace=True)
    h['past5_avg_tansho_log'] = _s.reindex(_oi)['past5_avg_tansho_log'].values
    df = df.merge(
        h[['horse_id', 'race_id', 'past5_avg_tansho_log']]
        .drop_duplicates(subset=['horse_id', 'race_id']),
        on=['horse_id', 'race_id'], how='left')

    return df, h


def _feh_running_style(
    df: pd.DataFrame, h: pd.DataFrame
) -> tuple:
    """馬の過去5走における脚質（逃げ/先行/差し/追込）の rolling 統計を付与する。"""
    if 'running_style_num' not in h.columns or 'horse_id' not in h.columns:
        return df, h

    _oi  = h.index.copy()
    _s   = h.sort_values('race_id', kind='mergesort').copy()
    _val = pd.to_numeric(_s['running_style_num'], errors='coerce')
    _s['_rsv'] = _val
    _rsv_sh = _s.groupby('horse_id')['_rsv'].shift(1)
    _s['running_style_mean_5'] = (
        _rsv_sh.groupby(_s['horse_id'])
        .rolling(5, min_periods=1).mean()
        .droplevel(0).reindex(_s.index))
    _s['running_style_std_5'] = (
        _rsv_sh.groupby(_s['horse_id'])
        .rolling(5, min_periods=2).std()
        .droplevel(0).reindex(_s.index)
        .fillna(0.0))
    _s.drop(columns=['_rsv'], inplace=True)
    _back = _s.reindex(_oi)
    for _c in ['running_style_mean_5', 'running_style_std_5']:
        h[_c] = _back[_c].values
    df = df.merge(
        h[['horse_id', 'race_id', 'running_style_mean_5', 'running_style_std_5']]
        .drop_duplicates(subset=['horse_id', 'race_id']),
        on=['horse_id', 'race_id'], how='left')

    return df, h

    return df


# ===========================================================================
# Public API
# ===========================================================================

def add_derived_features(df: pd.DataFrame, full_history_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """データフレームに派生特徴量を追加する（公開 API）。

    内部的には以下の順序でパイプラインを実行する:
      1. _fe_days_from_history  — DB全履歴から days_since_last_race を計算
      2. _fe_horse_category     — 性齢・コーナー・ペース・休養カテゴリ
      3. _fe_id_season          — race_id 分解・季節性・馬場×枠番交互作用
      4. _fe_course             — コース特性（直線長・内枠バイアス）
      5. _fe_market             — オッズ・市場エントロピー
      6. _fe_prev_race          — 前走日・距離変化・スピード指数
      7. _fe_lap                — ラップタイム展開・ペース要約
      8. _fe_payout             — 配当派生特徴量
      9. _fe_missing_flags      — 欠損フラグ付与・型統一
     10. _fe_history            — 全 expanding/rolling 統計（full_history_df 必要）

    Args:
        df: 現在のレースデータ。
        full_history_df: 過去データ全体（統計計算用）。省略時は step 1/10 をスキップ。

    Returns:
        pd.DataFrame: 派生特徴量が追加されたデータフレーム。
    """
    df = df.copy()
    if full_history_df is not None:
        df = _fe_days_from_history(df, full_history_df)
    df = _fe_horse_category(df)
    df = _fe_id_season(df)
    df = _fe_course(df)
    df = _fe_market(df)
    df = _fe_prev_race(df)
    df = _fe_lap(df)
    df = _fe_payout(df)
    df = _fe_missing_flags(df)
    if full_history_df is not None:
        df = _fe_history(df, full_history_df)
    # ── ITR-05: 騎手コース得意度（_fe_history後に計算 jockey_course_win_rateが必要）
    # 正 = このコースで平均より高勝率、負 = 苦手コース
    if 'jockey_course_win_rate' in df.columns and 'jockey_recent30_win_rate' in df.columns:
        _jcwr = pd.to_numeric(df['jockey_course_win_rate'], errors='coerce')
        _j30wr = pd.to_numeric(df['jockey_recent30_win_rate'], errors='coerce')
        df['jockey_venue_advantage'] = np.where(
            _jcwr.notna() & _j30wr.notna(), _jcwr - _j30wr, np.nan
        )
    return df


