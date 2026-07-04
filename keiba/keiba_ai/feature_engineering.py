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
    orig_idx = history_df.index
    # [高速化] 必要列のみ抽出してソート（全列コピーを回避: 200列→4列で ~50x メモリ削減）
    _needed = [c for c in ['race_id', 'finish', id_col, group_col]
               if c in history_df.columns]
    s = history_df[_needed].sort_values('race_id', kind='mergesort')
    fin_num  = pd.to_numeric(s['finish'], errors='coerce').fillna(0)
    win_flag = (fin_num == 1).astype(float)
    # [高速化] ベクトル化文字列連結（apply('|'.join) より 10-20x 高速）
    grp      = s[id_col].astype(str) + '|' + s[group_col].astype(str)
    race_cnt = s.groupby(grp, sort=False).cumcount()
    s = s.assign(_w=win_flag.values)
    cum_wins = s.groupby(grp, sort=False)['_w'].cumsum() - s['_w']
    s = s.drop(columns=['_w'])
    s = s.assign(**{out_rate: (cum_wins / race_cnt.clip(1)).fillna(0.0).values,
                    out_cnt:  race_cnt.values})
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
    orig_idx = history_df.index
    # [高速化] 必要列のみ抽出してソート
    s = history_df[needed].sort_values('race_id', kind='mergesort')
    fin_num  = pd.to_numeric(s['finish'], errors='coerce').fillna(0)
    win_flag = (fin_num == 1).astype(float)
    # [高速化] ベクトル化文字列連結
    grp      = s[id_col].astype(str) + '|' + s[group_col].astype(str)
    race_cnt = s.groupby(grp, sort=False).cumcount()
    s = s.assign(_w=win_flag.values, _f=fin_num.values)
    cum_wins = s.groupby(grp, sort=False)['_w'].cumsum() - s['_w']
    cum_fin  = s.groupby(grp, sort=False)['_f'].cumsum() - s['_f']
    s = s.assign(**{
        f'{out_prefix}_win_rate':   (cum_wins / race_cnt.clip(1)).fillna(0.0).values,
        f'{out_prefix}_avg_finish': (cum_fin  / race_cnt.clip(1)).fillna(0.0).values,
        f'{out_prefix}_races':      race_cnt.values,
    })
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
    orig_idx = history_df.index
    # [高速化] 必要列のみ抽出してソート
    _needed = [c for c in ['race_id', 'finish', eid_col] if c in history_df.columns]
    s = history_df[_needed].sort_values('race_id', kind='mergesort')
    fin_num    = pd.to_numeric(s['finish'], errors='coerce').fillna(0)
    win_flag   = (fin_num == 1).astype(float)
    place_flag = (fin_num <= 3).astype(float)   # 3着以内
    show_flag  = (fin_num <= 2).astype(float)   # 2着以内
    race_cnt   = s.groupby(eid_col, sort=False).cumcount()
    s = s.assign(_w=win_flag.values, _pl=place_flag.values, _sh=show_flag.values)
    cum_w  = s.groupby(eid_col, sort=False)['_w'].cumsum()  - s['_w']
    cum_pl = s.groupby(eid_col, sort=False)['_pl'].cumsum() - s['_pl']
    cum_sh = s.groupby(eid_col, sort=False)['_sh'].cumsum() - s['_sh']
    s = s.assign(**{
        f'{prefix}_win_rate':        (cum_w  / race_cnt.clip(1)).fillna(0.0).values,
        f'{prefix}_show_rate':       (cum_pl / race_cnt.clip(1)).fillna(0.0).values,
        f'{prefix}_place_rate_top2': (cum_sh / race_cnt.clip(1)).fillna(0.0).values,
    })
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

    # 開催回次（kai）・開催日次（day）— コース傷み・馬場変化の代理変数
    # kai: 第1回～第5回（0-4）, day: 開催1日目～8日目（0-7）
    # cos/sin 変換でモデルが連続性・周期性を学習しやすくする
    if 'kai' in df.columns:
        _kai = pd.to_numeric(df['kai'], errors='coerce').fillna(0)
        df['kai_num'] = _kai
        # 年間最大5回×8日 = 40日スパンの周期性をエンコード
        df['kai_cos'] = np.cos(2.0 * np.pi * _kai / 5.0)
        df['kai_sin'] = np.sin(2.0 * np.pi * _kai / 5.0)
    if 'day' in df.columns:
        _day = pd.to_numeric(df['day'], errors='coerce').fillna(0)
        df['day_num'] = _day
        df['day_cos'] = np.cos(2.0 * np.pi * _day / 8.0)
        df['day_sin'] = np.sin(2.0 * np.pi * _day / 8.0)
        # 開催後半フラグ（5日目以降はコースが傷みやすい）
        df['is_late_opening'] = (_day >= 5).astype(int)

    # ── 牝馬限定レースフラグ ─────────────────────────────────────────────────
    # race_name に「牝」を含むレースを牝馬限定として扱う
    # （牝馬ステークス系グレードレース・条件戦どちらも対象）
    if 'race_name' in df.columns:
        _rname = df['race_name'].fillna('').astype(str)
        df['is_female_only_race'] = _rname.str.contains('牝', na=False).astype('int8')

        # 3歳限定フラグ（race_name に "3歳" かつ "4歳" を含まないもの）
        _is_3yo_only = (
            _rname.str.contains('3歳', na=False) &
            ~_rname.str.contains('3歳以上|3（4）歳|3.4.歳', na=False, regex=True)
        )
        df['is_3yo_limited'] = _is_3yo_only.astype('int8')

        # 年齢区分エンコード（2歳専用=0, 3歳専用=1, 3歳以上=2, 4歳以上=3）
        _age_cond = pd.Series(2, index=df.index, dtype='int8')  # デフォルト: 3歳以上
        _age_cond = _age_cond.where(~_rname.str.contains('4歳以上|4歳上', na=False), 3)
        _age_cond = _age_cond.where(~_is_3yo_only, 1)
        _age_cond = _age_cond.where(~_rname.str.contains('2歳', na=False), 0)
        df['race_age_condition'] = _age_cond

    # ── クラスランク補正（牝馬限定は実質0.5クラス分難易度が低い）────────────
    if 'race_class_num' in df.columns and 'is_female_only_race' in df.columns:
        _rcn_adj = pd.to_numeric(df['race_class_num'], errors='coerce')
        df['class_rank_adj'] = _rcn_adj - (df['is_female_only_race'].astype(float) * 0.5)

    # ── 騎手負担重量 vs 性齢標準重量（基準差分）───────────────────────────────
    # JRA標準: 5歳以上牡せん=58kg / 牝=56kg。3歳馬は月ごとに軽減。
    # 負担重量 - 基準重量 > 0 → ハンデ上乗せ or 実力評価高い
    if 'jockey_weight' in df.columns:
        _jw = pd.to_numeric(df['jockey_weight'], errors='coerce')
        # 性別ベース標準（牝=56, 牡/セン=58 で近似）
        _std = pd.Series(58.0, index=df.index)
        if 'sex' in df.columns:
            _std = _std.where(
                ~df['sex'].isin(['牝', 'female']), 56.0
            )
        if 'age' in df.columns:
            _age = pd.to_numeric(df['age'], errors='coerce').fillna(5)
            # 3歳馬は平均で2kg軽い（別定規定に準拠）
            _std = _std - ((_age <= 3).astype(float) * 2.0)
        df['weight_vs_standard'] = _jw - _std

    # ── 馬柱記号特徴量（Task3）─────────────────────────────────────────────
    # 性別フラグ（sex_code から派生）
    if 'sex_code' in df.columns:
        _sc = pd.to_numeric(df['sex_code'], errors='coerce').fillna(0)
        df['is_female']  = (_sc == 1).astype('int8')   # 牝馬フラグ
        df['is_gelding'] = (df['sex'].isin(['セ', 'セン', 'gelding'])
                            if 'sex' in df.columns
                            else pd.Series(0, index=df.index, dtype='int8'))
    elif 'sex' in df.columns:
        df['is_female']  = df['sex'].isin(['牝', 'female']).astype('int8')
        df['is_gelding'] = df['sex'].isin(['セ', 'セン', 'gelding']).astype('int8')

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
    _feat_rows = [
        _get_course_features_by_venue(v, d, s)
        for v, d, s in unique_combos[['_vcol', 'distance', _surface_col]].itertuples(index=False, name=None)
    ]
    combo_feats = pd.DataFrame(_feat_rows, index=unique_combos.index)
    unique_combos[['straight_length', 'track_type', 'corner_radius', 'inner_bias']] = combo_feats[
        ['straight_length', 'track_type', 'corner_radius', 'inner_bias']
    ]
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
    df['log_odds']         = np.log1p(_o)       # log(1+odds): 単一市場シグナル。odds の単調変換だが数値スケールが安定
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
        # 市場エントロピー / 上位3頭の暗黙確率和（groupby.applyを使わないベクトル化版）
        _prob_raw = np.where(_o > 0, 1.0 / _o, np.nan)
        _prob_raw_s = pd.Series(_prob_raw, index=df.index)
        _prob_sum = _prob_raw_s.groupby(df['race_id'], sort=False).transform('sum')
        _prob_norm = (_prob_raw_s / _prob_sum).replace([np.inf, -np.inf], np.nan)

        _entropy_term = -_prob_norm * np.log(_prob_norm + 1e-10)
        _entropy_by_race = _entropy_term.groupby(df['race_id'], sort=False).transform('sum')

        _rank_prob = _prob_norm.groupby(df['race_id'], sort=False).rank(method='first', ascending=False)
        _top3_term = _prob_norm.where(_rank_prob <= 3, 0.0)
        _top3_by_race = _top3_term.groupby(df['race_id'], sort=False).transform('sum')

        _race_size = df.groupby('race_id', sort=False)['race_id'].transform('size')
        _valid_market = (_race_size >= 2) & (_prob_sum > 0)
        df['market_entropy'] = np.where(_valid_market, _entropy_by_race, 0.0)
        df['top3_probability'] = np.where(_valid_market, _top3_by_race, 0.5)
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

        # ── 直近2走の加重平均スピード指数（直近重視: 0.6×前走 + 0.4×前々走）
        df['speed_avg_weighted'] = np.where(
            _spi1.notna() & _spi2.notna(), 0.6 * _spi1 + 0.4 * _spi2,
            np.where(_spi1.notna(), _spi1, _spi2)
        )

        # ── 直近2走の最高スピード指数（ベスト出力の指標）
        df['speed_best_2'] = np.where(
            _spi1.notna() & _spi2.notna(), np.maximum(_spi1, _spi2),
            np.where(_spi1.notna(), _spi1, _spi2)
        )

        # ── スピード改善方向フラグ（1=前走の方が速い/改善, 0=低下, NaN=片方欠損）
        df['speed_improving'] = np.where(
            _spi1.notna() & _spi2.notna(),
            (_spi1 > _spi2).astype(float),
            np.nan
        )

    # ── 前走スピード指数のクラス内 z-score（race_class_encoded を利用）
    if 'prev_speed_index' in df.columns and 'race_class_encoded' in df.columns:
        _spi = pd.to_numeric(df['prev_speed_index'], errors='coerce')
        _cls = df['race_class_encoded']
        df['prev_speed_index_vs_class'] = df.groupby(_cls, sort=False)['prev_speed_index'].transform(
            lambda x: (pd.to_numeric(x, errors='coerce') - pd.to_numeric(x, errors='coerce').mean())
                      / (pd.to_numeric(x, errors='coerce').std() + 1e-8)
        )

    # ── 3〜5走前スピード指数（prev3-5 が補完済みの場合に有効）
    for _n in (3, 4, 5):
        _t_col = f'prev{_n}_race_time'
        _d_col = f'prev{_n}_race_distance'
        _si_col = f'prev{_n}_speed_index'
        if _t_col in df.columns and _d_col in df.columns:
            _pt = pd.to_numeric(df[_t_col], errors='coerce')
            _pd_n = pd.to_numeric(df[_d_col], errors='coerce')
            df[_si_col] = np.where((_pt > 0) & (_pd_n > 0), _pd_n / _pt, np.nan)

    # ── 直近5走の集計特徴量
    _finish_cols = []
    for _n in (1, 2, 3, 4, 5):
        _c = f'prev{"" if _n == 1 else str(_n)}_race_finish' if _n == 1 else f'prev{_n}_race_finish'
        _c = 'prev_race_finish' if _n == 1 else f'prev{_n}_race_finish'
        if _c in df.columns:
            _finish_cols.append((_n, _c))
    _speed_cols = []
    for _n in (1, 2, 3, 4, 5):
        _sc = 'prev_speed_index' if _n == 1 else f'prev{_n}_speed_index'
        if _sc in df.columns:
            _speed_cols.append((_n, _sc))

    if len(_finish_cols) >= 3:
        # 近走フォーム（5走分加重平均、直近重視: 1走=0.35, 2=0.25, 3=0.20, 4=0.12, 5=0.08）
        _weights = {1: 0.35, 2: 0.25, 3: 0.20, 4: 0.12, 5: 0.08}
        _wsum   = np.zeros(len(df))
        _wval   = np.zeros(len(df))
        _valid  = np.zeros(len(df), dtype=bool)
        for _n, _c in _finish_cols:
            _v = pd.to_numeric(df[_c], errors='coerce').values
            _mask = ~np.isnan(_v)
            _wsum  = np.where(_mask, _wsum + _weights[_n], _wsum)
            _wval  = np.where(_mask, _wval + _weights[_n] * _v, _wval)
            _valid = _valid | _mask
        df['recent_form_5race'] = np.where(_valid & (_wsum > 0), _wval / _wsum, np.nan)

        # 一貫性（着順の標準偏差: 小ほど安定）
        _fmat = np.column_stack([
            pd.to_numeric(df[_c], errors='coerce').values for _, _c in _finish_cols
        ])
        df['form_consistency'] = np.nanstd(_fmat, axis=1)
        df['form_consistency'] = np.where(
            np.sum(~np.isnan(_fmat), axis=1) >= 2, df['form_consistency'], np.nan
        )

        # 5走内勝利数・複勝数
        df['win_count_5']  = np.nansum((_fmat == 1).astype(float) * ~np.isnan(_fmat), axis=1)
        df['top3_count_5'] = np.nansum((_fmat <= 3).astype(float) * ~np.isnan(_fmat), axis=1)

    if len(_speed_cols) >= 3:
        _smat = np.column_stack([
            pd.to_numeric(df[_sc], errors='coerce').values for _, _sc in _speed_cols
        ])
        # 5走ベストスピード（最大値）
        df['speed_best_5'] = np.nanmax(_smat, axis=1)
        df['speed_best_5'] = np.where(np.all(np.isnan(_smat), axis=1), np.nan, df['speed_best_5'])
        # スピード指数トレンド（最新 - 最古の有効値）
        _first_valid = np.array([
            _smat[i, next((j for j in range(_smat.shape[1]) if not np.isnan(_smat[i, j])), -1)]
            if np.any(~np.isnan(_smat[i])) else np.nan
            for i in range(len(_smat))
        ])
        _last_valid = np.array([
            _smat[i, next((j for j in range(_smat.shape[1]-1, -1, -1) if not np.isnan(_smat[i, j])), -1)]
            if np.any(~np.isnan(_smat[i])) else np.nan
            for i in range(len(_smat))
        ])
        df['speed_trend_5'] = np.where(
            ~np.isnan(_first_valid) & ~np.isnan(_last_valid),
            _first_valid - _last_valid,  # 最新 - 最古（正 = 改善）
            np.nan
        )

    # ── 前走クラス数値化（prev_race_class → prev_race_class_num）
    # 今走クラスとの差分: class_change = race_class_num - prev_race_class_num
    # 正 = 格上挑戦、負 = 格下げ
    if 'prev_race_class' in df.columns:
        df['prev_race_class_num'] = [
            _race_class_to_num('', c) for c in df['prev_race_class'].fillna('')
        ]
        if 'race_class_num' in df.columns:
            _rcn  = pd.to_numeric(df['race_class_num'],      errors='coerce')
            _prcn = pd.to_numeric(df['prev_race_class_num'], errors='coerce')
            _diff = _rcn - _prcn  # 正=格上挑戦, 負=格下げ
            df['class_change']   = _diff
            df['is_class_up']    = (_diff > 0).astype('int8')
            df['is_class_down']  = (_diff < 0).astype('int8')
            df['is_same_class']  = (_diff == 0).astype('int8')
            df['class_diff_abs'] = _diff.abs()
            # ── クラスランク特徴量（Task2: レースクラス体系の明示的特徴量化）──────
            # race_class_rank: 今走のクラス数値（race_class_num のエイリアス）
            # prev_class_rank: 前走のクラス数値（prev_race_class_num のエイリアス）
            # class_drop / class_up: 降級/昇級フラグ（is_class_down/up のエイリアス）
            df['race_class_rank'] = _rcn.clip(lower=-1, upper=8)
            df['prev_class_rank'] = _prcn.clip(lower=-1, upper=8)
            # class_drop / class_up: magnitude（何段階の昇降級か）
            df['class_drop']      = np.where(_diff < 0, _diff.abs(), 0.0)
            df['class_up']        = np.where(_diff > 0, _diff.abs(), 0.0)

    # ── 馬柱記号特徴量（Task3）─────────────────────────────────────────────
    # days_since_last_race から休み明け・叩き2走目・連闘を計算
    if 'days_since_last_race' in df.columns:
        _dslr = pd.to_numeric(df['days_since_last_race'], errors='coerce')
        # 連闘: 前走から7日以内（ほぼ週替わり同競馬場続戦）
        df['is_consecutive_week'] = ((_dslr > 0) & (_dslr <= 7)).astype('int8')
        # 休み明け: 前走から60日超
        _is_rest = _dslr > 60
        df['is_fresh'] = _is_rest.astype('int8')
        # 叩き2走目: 前走が休み明け（60日超）後の2戦目 → prev_race自体がfreshかどうかは
        # 展開上判定困難なため、21-59日間を「叩き2走目候補」として近似する
        df['is_second_after_rest'] = ((_dslr >= 21) & (_dslr <= 59)).astype('int8')

    # 騎手乗り替わり: 前走騎手IDと今走騎手IDが異なる場合
    # prev_jockey_id がDBにない場合は history から計算
    if 'jockey_id' in df.columns:
        _jid = df['jockey_id'].astype(str)
        # prev_jockey_id はスクレイプ値が存在しないため、
        # 前走騎手情報がある場合のみ計算（not available → NaN のまま）
        if 'prev_jockey_id' in df.columns:
            _pjid = df['prev_jockey_id'].astype(str)
            df['jockey_changed'] = ((_jid != _pjid) & (_pjid != 'nan') & (_pjid != '')).astype('int8')
            df['first_jockey']   = ((_jid != _pjid) | _pjid.isin(['nan', ''])).astype('int8')
        else:
            # prev_jockey_id が存在しない場合はフラグを NaN で埋める
            df['jockey_changed'] = np.nan
            df['first_jockey']   = np.nan

    return df

def _fe_opponent(df: pd.DataFrame) -> pd.DataFrame:
    """レース内相手関係特徴量を追加する（P-5）。

    race_id グループ内で前走スピード指数・着順の統計を計算し、
    各馬の「このレースでの相対的な強さ」を捉える。
    全て prev_* 系（過去レース情報）に基づくため未来情報混入なし。

    追加特徴量:
      - race_avg_prev_speed   : レース内全馬の前走スピード指数の平均（相手の総合レベル）
      - race_max_prev_speed   : レース内全馬の前走スピード指数の最大（最強馬レベル）
      - speed_vs_race_avg     : 自馬スピード指数 - レース内平均（プラス＝有利）
      - horse_speed_rank_pct  : レース内スピード順位パーセンタイル（0=最速, 1に近い＝最遅）
      - race_avg_prev_finish  : レース内全馬の前走着順の平均（相手の着順レベル）
    """
    if 'race_id' not in df.columns:
        return df

    if 'prev_speed_index' in df.columns:
        _spi = pd.to_numeric(df['prev_speed_index'], errors='coerce')
        df['_tmp_spi'] = _spi
        _grp_spi = df.groupby('race_id', sort=False)['_tmp_spi']
        df['race_avg_prev_speed'] = _grp_spi.transform('mean')
        df['race_max_prev_speed'] = _grp_spi.transform('max')
        # 自馬のスピード指数 - レース内平均（プラス＝相手より速い）
        df['speed_vs_race_avg'] = np.where(
            _spi.notna() & df['race_avg_prev_speed'].notna(),
            _spi - df['race_avg_prev_speed'],
            np.nan,
        )
        # レース内スピード順位パーセンタイル（0=最速, 1に近いほど遅い）
        _n = df.groupby('race_id', sort=False)['_tmp_spi'].transform('size').clip(lower=1)
        _rank = df.groupby('race_id', sort=False)['_tmp_spi'].rank(
            ascending=False, method='min', na_option='bottom'
        )
        df['horse_speed_rank_pct'] = (_rank - 1) / _n
        df = df.drop(columns=['_tmp_spi'])

    if 'prev_race_finish' in df.columns:
        _pf = pd.to_numeric(df['prev_race_finish'], errors='coerce')
        df['_tmp_pf'] = _pf
        df['race_avg_prev_finish'] = df.groupby('race_id', sort=False)['_tmp_pf'].transform('mean')
        df = df.drop(columns=['_tmp_pf'])

    return df


def _fe_holding_time(df: pd.DataFrame) -> pd.DataFrame:
    """持ちタイム（AplFreqSum API）から派生特徴量を追加する。

    - has_just_data: 同コース・距離の出走経験フラグ（0/1）
    - holding_just_speed: just タブのタイム秒から速度指数（distance / time_sec）を計算
    - holding_just_time_rank: レース内での速度ランク（速いほど小さい値）
    - holding_just_finish_rank: just 着順のレース内ランク
    - holding_*_babasa: 数値型に統一（馬場指数; short/middle/long の time_sec は距離依存で比較不能なため不使用）
    - holding_just_time_sec 等の欠損フラグは _fe_missing_flags に委ねる
    """
    # has_just_data フラグ
    if 'holding_just_time_sec' in df.columns:
        _jt = pd.to_numeric(df['holding_just_time_sec'], errors='coerce')
        df['has_just_data'] = _jt.notna().astype(int)

        # just 速度指数（distance / just_time_sec）
        if 'distance' in df.columns:
            _d = pd.to_numeric(df['distance'], errors='coerce')
            df['holding_just_speed'] = np.where(
                _jt.notna() & (_jt > 0) & _d.notna(), _d / _jt, np.nan
            )
            # レース内速度ランク（速いほど順位が小さい = 高速）
            if 'race_id' in df.columns:
                df['holding_just_time_rank'] = df.groupby('race_id', sort=False)['holding_just_speed'].rank(
                    method='min', ascending=False, na_option='bottom'
                )
    else:
        df['has_just_data'] = 0

    # just 着順のレース内ランク
    if 'holding_just_finish' in df.columns and 'race_id' in df.columns:
        _jf = pd.to_numeric(df['holding_just_finish'], errors='coerce')
        df['_holding_just_finish_num'] = _jf
        df['holding_just_finish_rank'] = df.groupby('race_id', sort=False)['_holding_just_finish_num'].rank(
            method='min', ascending=True, na_option='bottom'
        )
        df = df.drop(columns=['_holding_just_finish_num'])

    # babasa (馬場指数) を全タブ数値型に統一
    for tab in ('just', 'short', 'middle', 'long'):
        col = f'holding_{tab}_babasa'
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    return df


def _fe_corner_position(df: pd.DataFrame) -> pd.DataFrame:
    """コーナー通過順位から脚質特徴量を生成する。

    ソース列: corner_1, corner_2, corner_3, corner_4（race_results_ultimate.data より展開済み）
    生成特徴量:
      - corner_last          : 最終コーナー通過順（4角 or 存在する最後の角）
      - corner_first         : 先頭コーナー通過順（1角 or 2角）
      - corner_gain          : 最終角 - 先頭角（負 = 捲り上げ・差し）
      - running_style_code   : 脚質コード  1=逃げ/2=先行/3=差し/4=追い込み（corner_last ÷ num_horses で判定）
      - running_style_mean_5 : 過去5走の running_style_code 平均（expanding window は _fe_history で担当）
                               ※ ここでは当走の running_style_code のみを計算し、
                                  rolling 統計は _fe_history が行う

    カバレッジ: corner_4 が ~56%, corner_3 が ~67%。corner_last は最大 ~94%。
    """
    # ── 先頭・最終コーナー通過順の取得 ──────────────────────────────────
    # df.get(col, np.nan) は列が存在しない場合スカラー nan を返す → .notna() が AttributeError になるため
    # 列が存在しない場合は index 揃いの NaN Series を使う
    _nan_s = pd.Series(np.nan, index=df.index)
    _c1 = pd.to_numeric(df['corner_1'] if 'corner_1' in df.columns else _nan_s, errors='coerce')
    _c2 = pd.to_numeric(df['corner_2'] if 'corner_2' in df.columns else _nan_s, errors='coerce')
    _c3 = pd.to_numeric(df['corner_3'] if 'corner_3' in df.columns else _nan_s, errors='coerce')
    _c4 = pd.to_numeric(df['corner_4'] if 'corner_4' in df.columns else _nan_s, errors='coerce')

    # 先頭コーナー（1角→2角の順に取得）
    df['corner_first'] = np.where(_c1.notna(), _c1, _c2)

    # 最終コーナー（4角→3角→2角の順に取得）
    _last = _c4.copy()
    _last = np.where(_last.isna(), _c3, _last)
    _last = np.where(pd.isna(_last), _c2, _last)
    _last = np.where(pd.isna(_last), _c1, _last)
    df['corner_last'] = pd.to_numeric(_last, errors='coerce')

    # コーナー上がり（正 = 追い込み、負 = 逃げ）
    _first = pd.to_numeric(df['corner_first'], errors='coerce')
    _last_s = pd.to_numeric(df['corner_last'], errors='coerce')
    df['corner_gain'] = np.where(
        _first.notna() & _last_s.notna(),
        _last_s - _first,
        np.nan,
    )

    # ── 脚質コード（num_horses で正規化した相対通過順） ────────────────
    # 1=逃げ(top 10%), 2=先行(~30%), 3=差し(~60%), 4=追い込み(>60%)
    _num = pd.to_numeric(df['num_horses'] if 'num_horses' in df.columns else _nan_s, errors='coerce').replace(0, np.nan)
    _rel = _last_s / _num  # 0〜1 の相対順位（1 = 最後方）

    _style = np.select(
        [
            _rel.notna() & (_rel <= 0.10),
            _rel.notna() & (_rel <= 0.30),
            _rel.notna() & (_rel <= 0.60),
            _rel.notna() & (_rel > 0.60),
        ],
        [1, 2, 3, 4],
        default=np.nan,
    )
    df['running_style_code'] = pd.to_numeric(_style, errors='coerce')

    return df


def _fe_speed_figures(df: pd.DataFrame, speed_figures_df: "pd.DataFrame | None") -> pd.DataFrame:
    """speed_figures テーブルの速度指数特徴量を追加する。

    対象特徴量:
      - sf_index_last       : 前走スピード指数
      - sf_index_2ago       : 2走前スピード指数
      - sf_index_3ago       : 3走前スピード指数
      - sf_max_index        : 過去最大スピード指数
      - sf_course_max_index : 同コース最大スピード指数
      - sf_dist_max_index   : 同距離最大スピード指数
      - sf_index_trend      : 直近トレンド（index_last - index_2ago）

    カバレッジ ~8% のため大半が NaN。LightGBM の use_missing=True でネイティブに扱う。
    """
    _SF_COLS = ['index_last', 'index_2ago', 'index_3ago', 'max_index', 'course_max_index', 'dist_max_index']
    _SF_RENAME = {c: f'sf_{c}' for c in _SF_COLS}

    if speed_figures_df is None or speed_figures_df.empty or 'horse_number' not in df.columns:
        for dst_c in _SF_RENAME.values():
            df[dst_c] = np.nan
        df['sf_index_trend'] = np.nan
        return df

    _sf = speed_figures_df.copy()
    _sf['horse_number'] = pd.to_numeric(_sf['horse_number'], errors='coerce')
    for c in _SF_COLS:
        if c in _sf.columns:
            _sf[c] = pd.to_numeric(_sf[c], errors='coerce')

    _merge_cols = ['race_id', 'horse_number'] + [c for c in _SF_COLS if c in _sf.columns]
    _sf_merge = (
        _sf[_merge_cols]
        .drop_duplicates(subset=['race_id', 'horse_number'], keep='first')
    )

    _df_keys = pd.DataFrame({
        'race_id': df['race_id'].values if 'race_id' in df.columns else '',
        'horse_number': pd.to_numeric(df['horse_number'], errors='coerce').values,
        '_pos': np.arange(len(df)),
    })
    _merged = _df_keys.merge(_sf_merge, on=['race_id', 'horse_number'], how='left')
    _merged = _merged.sort_values('_pos').reset_index(drop=True)

    for src_c, dst_c in _SF_RENAME.items():
        df[dst_c] = _merged[src_c].values if src_c in _merged.columns else np.nan

    # 指数トレンド（前走 - 2走前、上昇なら正値）
    _last = pd.to_numeric(df.get('sf_index_last', np.nan), errors='coerce')
    _2ago = pd.to_numeric(df.get('sf_index_2ago', np.nan), errors='coerce')
    df['sf_index_trend'] = np.where(_last.notna() & _2ago.notna(), _last - _2ago, np.nan)

    return df


def _fe_training(df: pd.DataFrame, training_df: "pd.DataFrame | None") -> pd.DataFrame:
    """最終追い切り（training_data テーブル）の特徴量を追加する。

    対象特徴量:
      - last_training_time_3f       : 最終追い切り上がり3F秒（例: 25.3）
      - last_training_grade_encoded : 評価ランク A=4, B=3, C=2, D=1
      - has_training_data           : 調教データ存在フラグ（0/1）
      - training_comment_score      : コメントのポジ/ネガキーワード差分スコア

    カバレッジ ~6% のため大半が NaN。LightGBM の use_missing=True でネイティブに扱う。
    """
    _GRADE_MAP = {'A': 4, 'B': 3, 'C': 2, 'D': 1}
    # 厩舎コメントキーワード（ポジティブ）
    _POS_KW = ['好仕上', '好気配', '充実', '活発', '力強', 'シャープ', '抜群',
               '好調', '一番時計', '自己ベスト', '好ムード', '圧倒', '良好',
               '好感', '好内容', '増した', '別格', 'まるで違']
    # 厩舎コメントキーワード（ネガティブ）
    _NEG_KW = ['物足り', '不満', '疑問', '地味', '及第点', '遅れ', 'スロー',
               'ひと息', '心配', '目立たず', '精彩欠', '不安', '良化']

    if training_df is None or training_df.empty or 'horse_number' not in df.columns:
        df['last_training_time_3f'] = np.nan
        df['last_training_grade_encoded'] = np.nan
        df['has_training_data'] = 0
        df['training_comment_score'] = np.nan
        return df

    # is_last_training=1 のみ使用（念のためフィルタ）
    if 'is_last_training' in training_df.columns:
        _td = training_df[pd.to_numeric(training_df['is_last_training'], errors='coerce').fillna(0) == 1].copy()
    else:
        _td = training_df.copy()

    if _td.empty:
        df['last_training_time_3f'] = np.nan
        df['last_training_grade_encoded'] = np.nan
        df['has_training_data'] = 0
        df['training_comment_score'] = np.nan
        return df

    _td['last_training_time_3f'] = pd.to_numeric(_td['time_3f'], errors='coerce')
    _td['last_training_grade_encoded'] = _td['grade'].map(_GRADE_MAP)
    _td['horse_number'] = pd.to_numeric(_td['horse_number'], errors='coerce')

    # コメントスコア（ポジキーワード数 - ネガキーワード数）
    if 'comment' in _td.columns:
        def _score_comment(txt: str) -> float:
            if not txt or not isinstance(txt, str):
                return np.nan
            pos = sum(1 for kw in _POS_KW if kw in txt)
            neg = sum(1 for kw in _NEG_KW if kw in txt)
            return float(pos - neg)
        _td['training_comment_score'] = _td['comment'].apply(_score_comment)
    else:
        _td['training_comment_score'] = np.nan

    # 同一 race_id + horse_number で複数行ある場合は最初の行を使用
    _td_merge = (
        _td[['race_id', 'horse_number', 'last_training_time_3f',
             'last_training_grade_encoded', 'training_comment_score']]
        .drop_duplicates(subset=['race_id', 'horse_number'], keep='first')
    )

    # df のインデックスを保存して安全に merge
    _df_keys = pd.DataFrame({
        'race_id': df['race_id'].values if 'race_id' in df.columns else '',
        'horse_number': pd.to_numeric(df['horse_number'], errors='coerce').values,
        '_pos': np.arange(len(df)),
    })
    _merged = _df_keys.merge(_td_merge, on=['race_id', 'horse_number'], how='left')
    _merged = _merged.sort_values('_pos').reset_index(drop=True)

    df['last_training_time_3f'] = _merged['last_training_time_3f'].values
    df['last_training_grade_encoded'] = _merged['last_training_grade_encoded'].values
    df['has_training_data'] = (~_merged['last_training_time_3f'].isna()).astype(int).values
    df['training_comment_score'] = _merged['training_comment_score'].values

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
        'holding_just_time_sec', 'holding_just_speed', 'holding_just_finish',
        'race_avg_prev_speed', 'race_max_prev_speed',
        'speed_vs_race_avg', 'horse_speed_rank_pct', 'race_avg_prev_finish',
        # P-9: フィールド強度
        'horse_avg_class_num', 'class_drop',
        # P-7: 枠番×馬場詳細
        'gate_bracket_win_rate',
        # P-8: 血統×条件相性
        'sire_surface_win_rate', 'sire_dist_band_win_rate',
        'damsire_surface_win_rate', 'damsire_dist_band_win_rate',
        # P-10: 騎手×脚質相性
        'jockey_front_win_rate', 'jockey_close_win_rate',
        # 脚質 rolling 統計（6.6%/100%カバレッジ → is_missing フラグで管理）
        'running_style_mean_5', 'running_style_std_5',
        # 馬場状態別・天気別相性
        'horse_field_win_rate', 'horse_field_races',
        'horse_weather_win_rate', 'horse_weather_races',
        'jockey_field_win_rate', 'jockey_field_races',
        'sire_field_win_rate', 'sire_field_races',
        'damsire_field_win_rate', 'damsire_field_races',
        # 開催回次・日次
        'kai_num', 'day_num',
        # 調教タイム・評価（カバレッジ ~6%）
        'last_training_time_3f', 'last_training_grade_encoded',
        'training_comment_score',
        # 速度指数（speed_figures テーブル、カバレッジ ~8%）
        'sf_index_last', 'sf_index_2ago', 'sf_index_3ago',
        'sf_max_index', 'sf_course_max_index', 'sf_dist_max_index',
        'sf_index_trend',
        # 馬の通算スピード expanding window（_feh_horse_speed）
        'horse_speed_exp_mean', 'horse_speed_exp_std', 'horse_speed_vs_exp',
        # ※ corner_first/last/gain/running_style_code は当該レースの corner_1/2/3/4
        #    から生成する post-race フィールドのため FUTURE_FIELDS に移動。
        #    欠損フラグも不要になったので _FLAG_COLS から削除。
    ]
    # 欠損フィルにデフォルト値が必要な列（0 以外）
    _FLAG_FILLNA: dict = {
        'running_style_mean_5': 1.5,   # 中央値（先行 = 中立）
    }
    # 着順系は「欠損そのもの」を下流で判定したいケースがあるため埋めない。
    _NO_FILL_COLS = {
        'prev_race_finish',
        'prev2_race_finish',
    }
    for col in _FLAG_COLS:
        if col in df.columns:
            _s = pd.to_numeric(df[col], errors='coerce')
            df[f'{col}_is_missing'] = _s.isna().astype(int)
            if col in _NO_FILL_COLS:
                df[col] = _s
            else:
                # NaN を埋める（_is_missing フラグで欠損情報は保持済み）
                _fill_val = _FLAG_FILLNA.get(col, 0)
                df[col] = _s.fillna(_fill_val)

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

    # distanceごとに有効セクショナル列が異なるため、距離単位でベクトル計算する
    _dist_num = pd.to_numeric(df['distance'], errors='coerce')
    df['race_pace_front'] = np.nan
    df['race_pace_back'] = np.nan

    _unique_dist = sorted(d for d in _dist_num.dropna().unique())
    for _dval in _unique_dist:
        valid = [d for d in _all_dists_sorted if d <= int(_dval)]
        if len(valid) < 2:
            continue
        mid = len(valid) // 2
        front_cols = [f'lap_sect_{d}m' for d in valid[:mid] if f'lap_sect_{d}m' in df.columns]
        back_cols = [f'lap_sect_{d}m' for d in valid[mid:] if f'lap_sect_{d}m' in df.columns]
        if not front_cols or not back_cols:
            continue
        _mask = (_dist_num == _dval)
        _front_src = df.loc[_mask, front_cols].where(df.loc[_mask, front_cols] > 0)
        _back_src = df.loc[_mask, back_cols].where(df.loc[_mask, back_cols] > 0)
        df.loc[_mask, 'race_pace_front'] = _front_src.mean(axis=1, skipna=True)
        df.loc[_mask, 'race_pace_back'] = _back_src.mean(axis=1, skipna=True)

    df['race_pace_diff'] = df['race_pace_front'] - df['race_pace_back']
    df['race_pace_ratio'] = np.where(
        pd.to_numeric(df['race_pace_back'], errors='coerce') > 0,
        df['race_pace_front'] / df['race_pace_back'],
        np.nan,
    )

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
      _feh_gate_bias          — 枠番バイアス（会場×距離帯×馬場）+ P-7 granular
      _feh_jt_combo           — 騎手×調教師コンビ（ベイズ平滑化）
      _feh_entity_career      — 騎手・調教師・血統の通算成績 + P-8 血統×条件
      _feh_recent_form        — 馬の近走（past3/5/10）統計
      _feh_entity_recent30    — 騎手・調教師の近30走勝率
      _feh_last_3f            — 上がり3F rolling 統計
      _feh_payout_history     — 過去単勝配当 rolling 統計
      _feh_running_style      — 脚質 rolling 統計
      _feh_field_strength     — P-9 フィールド強度（格下り/格上り指数）
      _feh_race_dynamics      — P-6 ペース期待値・位置取り相性
      _feh_jockey_running_style — P-10 騎手×脚質相性
    """
    h = full_history_df

    # h に running_style_num / race_class_num が無い場合は計算する
    # （full_history_df は _fe_horse_category / _fe_id_season 前の生データのため）
    _need_copy = (
        ('running_style_num' not in h.columns and 'corner_positions_list' in h.columns)
        or ('race_class_num' not in h.columns and 'race_name' in h.columns)
    )
    if _need_copy:
        h = h.copy()

    if 'running_style_num' not in h.columns and 'corner_positions_list' in h.columns:
        _nh_h = (
            h['n_horses']
            if 'n_horses' in h.columns
            else h.groupby('race_id', sort=False)['race_id'].transform('count')
        )
        h['running_style'] = [
            classify_running_style(c, nh)
            for c, nh in zip(h['corner_positions_list'], _nh_h)
        ]
        h['running_style_num'] = h['running_style'].map(_RUNNING_STYLE_NUM)

    if 'race_class_num' not in h.columns and 'race_name' in h.columns:
        _rcn_col = h.get('race_name',  pd.Series([''] * len(h), index=h.index))
        _rcc_col = h.get('race_class', pd.Series([''] * len(h), index=h.index))
        h['race_class_num'] = [
            _race_class_to_num(n, c) for n, c in zip(_rcn_col, _rcc_col)
        ]

    # [高速化] 各 _feh_* 関数は df/h の独立したコピーで動作するため
    # ThreadPoolExecutor で並列実行できる（max_workers=4 で h コピーを 4 つに抑制）。
    # pandas groupby/cumsum は C 拡張内で GIL を解放するためスレッド並列が有効。
    _feh_funcs = [
        _feh_jockey_course,
        _feh_horse_aptitude,
        _feh_gate_bias,
        _feh_jt_combo,
        _feh_entity_career,
        _feh_recent_form,
        _feh_entity_recent30,
        _feh_last_3f,
        _feh_payout_history,
        _feh_running_style,
        _feh_field_strength,       # P-9
        _feh_race_dynamics,         # P-6
        _feh_jockey_running_style,  # P-10
        _feh_horse_speed,
    ]
    _orig_df_cols = set(df.columns)
    _h_snapshot = h  # ワーカーが参照するスナップショット（各ワーカーは copy() を使用）

    def _run_feh(func):
        try:
            _df_w = df.copy()           # df は 18 行程度なので安価
            _h_w  = _h_snapshot.copy()  # h は独立コピー（各ワーカーが in-place 変更するため必須）
            _df_out, _ = func(_df_w, _h_w)
            new_cols = [c for c in _df_out.columns if c not in _orig_df_cols]
            return _df_out[new_cols].copy() if new_cols else None
        except Exception:
            return None

    try:
        from concurrent.futures import ThreadPoolExecutor as _TPE
        with _TPE(max_workers=4) as _pool:
            _results = list(_pool.map(_run_feh, _feh_funcs))
        _new_dfs = [r for r in _results if r is not None]
        if _new_dfs:
            df = pd.concat([df] + _new_dfs, axis=1)
            df = df.loc[:, ~df.columns.duplicated()]  # 万が一の重複列除去
    except Exception:
        # フォールバック: 並列化失敗時は逐次実行
        for _fn in _feh_funcs:
            try:
                df, h = _fn(df, h)
            except Exception:
                pass

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

    # 馬場状態別（良/稍重/重/不良）
    if 'horse_id' in h.columns and 'field_condition' in h.columns:
        h = _expanding_win_rate_by_group(
            h, 'horse_id', 'field_condition',
            'horse_field_win_rate', 'horse_field_races')
        df = df.merge(
            h[['horse_id', 'race_id', 'horse_field_win_rate', 'horse_field_races']]
            .drop_duplicates(subset=['horse_id', 'race_id']),
            on=['horse_id', 'race_id'], how='left')

    # 天気別（晴/曇/雨/小雨 等）
    if 'horse_id' in h.columns and 'weather' in h.columns:
        h = _expanding_win_rate_by_group(
            h, 'horse_id', 'weather',
            'horse_weather_win_rate', 'horse_weather_races')
        df = df.merge(
            h[['horse_id', 'race_id', 'horse_weather_win_rate', 'horse_weather_races']]
            .drop_duplicates(subset=['horse_id', 'race_id']),
            on=['horse_id', 'race_id'], how='left')

    return df, h


def _feh_gate_bias(
    df: pd.DataFrame, h: pd.DataFrame
) -> tuple:
    """枠番バイアス（会場×距離帯×馬場）を静的集計で付与する。

    gate_win_rate       : 内/外枠二値バイアス（従来）
    gate_bracket_win_rate : P-7 枠番1〜8 × venue × surface × dist_band の静的勝率
    """
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

    # --- P-7: 枠番1〜8 × venue × surface × dist_band 静的集計（より細かいバイアス）---
    _hg2 = h.copy()
    _hg2['_is_win2']   = (pd.to_numeric(_hg2['finish'], errors='coerce') == 1).astype(int)
    _hg2['_dist_band2'] = _hg2['distance'].apply(_dist_band) if 'distance' in _hg2.columns else 'unknown'
    _surf_h2 = _hg2['surface'].astype(str) if 'surface' in _hg2.columns else pd.Series(['?'] * len(_hg2), index=_hg2.index)
    _hg2['_brk_key'] = (
        pd.to_numeric(_hg2['bracket_number'], errors='coerce').fillna(-1).astype(int).astype(str)
        + '|' + _hg2['venue'].astype(str) + '×' + _surf_h2 + '×' + _hg2['_dist_band2']
    )
    _brk_agg = (
        _hg2.groupby('_brk_key', sort=False)
        .agg(_cnt2=('_is_win2', 'count'), _wins2=('_is_win2', 'sum'))
        .reset_index()
    )
    _brk_agg['gate_bracket_win_rate'] = np.where(
        _brk_agg['_cnt2'] >= 20,
        _brk_agg['_wins2'] / _brk_agg['_cnt2'],
        np.nan,
    )
    _dist_band_df = df['distance'].apply(_dist_band) if 'distance' in df.columns else 'unknown'
    _surf_df = df['surface'].astype(str) if 'surface' in df.columns else '?'
    df['_brk_key'] = (
        pd.to_numeric(df.get('bracket_number', pd.Series([None] * len(df))), errors='coerce')
        .fillna(-1).astype(int).astype(str)
        + '|' + df['venue'].astype(str) + '×' + _surf_df + '×' + _dist_band_df
    )
    df = df.merge(_brk_agg[['_brk_key', 'gate_bracket_win_rate']], on='_brk_key', how='left')
    df = df.drop(columns=['_brk_key'], errors='ignore')

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

        # 騎手 × 馬場状態別 expanding window 勝率
        if 'field_condition' in h.columns:
            h = _expanding_win_rate_by_group(
                h, 'jockey_id', 'field_condition',
                'jockey_field_win_rate', 'jockey_field_races')
            df = df.merge(
                h[['jockey_id', 'race_id', 'jockey_field_win_rate', 'jockey_field_races']]
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
        if _sid not in h.columns or _sid not in df.columns:
            continue
        h = _expanding_stats(h, _sid, _sid)
        df = df.merge(
            h[[_sid, 'race_id', f'{_sid}_win_rate', f'{_sid}_show_rate']]
            .drop_duplicates(subset=[_sid, 'race_id']),
            on=[_sid, 'race_id'], how='left')

        # P-8: 血統 × 馬場別 expanding window 勝率
        if 'surface' in h.columns:
            h['_sid_surf'] = h['surface'].astype(str)
            h = _expanding_win_rate_by_group(
                h, _sid, '_sid_surf',
                f'{_sid}_surface_win_rate', f'{_sid}_surface_races')
            df = df.merge(
                h[[_sid, 'race_id', f'{_sid}_surface_win_rate']]
                .drop_duplicates(subset=[_sid, 'race_id']),
                on=[_sid, 'race_id'], how='left')
            h = h.drop(columns=['_sid_surf'], errors='ignore')

        # P-8: 血統 × 距離帯別 expanding window 勝率
        if 'distance' in h.columns:
            h['_sid_dist'] = h['distance'].apply(_dist_band)
            h = _expanding_win_rate_by_group(
                h, _sid, '_sid_dist',
                f'{_sid}_dist_band_win_rate', f'{_sid}_dist_band_races')
            df = df.merge(
                h[[_sid, 'race_id', f'{_sid}_dist_band_win_rate']]
                .drop_duplicates(subset=[_sid, 'race_id']),
                on=[_sid, 'race_id'], how='left')
            h = h.drop(columns=['_sid_dist'], errors='ignore')

        # P-8 拡張: 血統 × 馬場状態別 expanding window 勝率
        if 'field_condition' in h.columns:
            h['_sid_field'] = h['field_condition'].astype(str)
            h = _expanding_win_rate_by_group(
                h, _sid, '_sid_field',
                f'{_sid}_field_win_rate', f'{_sid}_field_races')
            df = df.merge(
                h[[_sid, 'race_id', f'{_sid}_field_win_rate', f'{_sid}_field_races']]
                .drop_duplicates(subset=[_sid, 'race_id']),
                on=[_sid, 'race_id'], how='left')
            h = h.drop(columns=['_sid_field'], errors='ignore')

        # P-8 拡張: 血統 × 競馬場別 expanding window 勝率
        if 'venue' in h.columns:
            h['_sid_venue'] = h['venue'].astype(str)
            h = _expanding_win_rate_by_group(
                h, _sid, '_sid_venue',
                f'{_sid}_venue_win_rate', f'{_sid}_venue_races')
            df = df.merge(
                h[[_sid, 'race_id', f'{_sid}_venue_win_rate']]
                .drop_duplicates(subset=[_sid, 'race_id']),
                on=[_sid, 'race_id'], how='left')
            h = h.drop(columns=['_sid_venue'], errors='ignore')

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


def _feh_field_strength(
    df: pd.DataFrame, h: pd.DataFrame
) -> tuple:
    """P-9: フィールド強度（馬の通常クラス vs 今回レース）を付与する。

    horse_avg_class_num : 馬の過去走の平均クラス水準（expanding window）
    class_drop          : horse_avg_class_num - 今回レース class_num
                          正 = 格下り（有利）、負 = 格上り（不利）
    """
    if 'race_class_num' not in h.columns or 'horse_id' not in h.columns:
        return df, h

    _oi  = h.index.copy()
    _s   = h.sort_values('race_id', kind='mergesort').copy()
    _cls = pd.to_numeric(_s['race_class_num'], errors='coerce')
    _s['_cls'] = _cls
    _cls_sh = _s.groupby('horse_id', sort=False)['_cls'].shift(1)
    _s['horse_avg_class_num'] = (
        _cls_sh.groupby(_s['horse_id'])
        .expanding(min_periods=1).mean()
        .droplevel(0).reindex(_s.index))
    _s.drop(columns=['_cls'], inplace=True)
    _back = _s.reindex(_oi)
    h['horse_avg_class_num'] = _back['horse_avg_class_num'].values
    df = df.merge(
        h[['horse_id', 'race_id', 'horse_avg_class_num']]
        .drop_duplicates(subset=['horse_id', 'race_id']),
        on=['horse_id', 'race_id'], how='left')

    if 'race_class_num' in df.columns and 'horse_avg_class_num' in df.columns:
        _rcn  = pd.to_numeric(df['race_class_num'],      errors='coerce')
        _hacn = pd.to_numeric(df['horse_avg_class_num'], errors='coerce')
        df['class_drop'] = np.where(_hacn.notna() & _rcn.notna(), _hacn - _rcn, np.nan)

    return df, h


def _feh_race_dynamics(
    df: pd.DataFrame, h: pd.DataFrame
) -> tuple:
    """P-6: ペース期待値・位置取り相性を付与する（_feh_running_style 後に呼ぶこと）。

    race_front_runner_count : レース内の逃げ/先行馬の数（running_style_mean_5 < 1.5）
    race_front_runner_pct   : 逃げ/先行馬の割合（0=差し天国、1=ハイペース）
    pace_advantage          : 自馬の脚質とペース環境の適合スコア（0〜1）
                              逃げ/先行 → front_runner が少ないほど高い（単独逃げ有利）
                              差し/追込 → front_runner が多いほど高い（ハイペース恩恵）
    """
    if 'race_id' not in df.columns or 'running_style_mean_5' not in df.columns:
        return df, h

    _rs = pd.to_numeric(df['running_style_mean_5'], errors='coerce').fillna(1.5)
    df['_tmp_is_front'] = (_rs < 1.5).astype(float)

    _grp = df.groupby('race_id', sort=False)
    df['race_front_runner_count'] = _grp['_tmp_is_front'].transform('sum')
    _n_horses = _grp['_tmp_is_front'].transform('count').clip(lower=1)
    df['race_front_runner_pct'] = df['race_front_runner_count'] / _n_horses

    # 逃げ/先行: front が少ないほど有利 → 1 - front_pct
    # 差し/追込: front が多いほど有利 → front_pct
    _is_front = (_rs < 1.5).values
    df['pace_advantage'] = np.where(
        _is_front,
        1.0 - df['race_front_runner_pct'],
        df['race_front_runner_pct'],
    )
    df = df.drop(columns=['_tmp_is_front'], errors='ignore')

    return df, h


def _feh_jockey_running_style(
    df: pd.DataFrame, h: pd.DataFrame
) -> tuple:
    """P-10: 騎手×脚質相性（逃げ先行 vs 差し追込）の expanding window 勝率を付与する。

    jockey_front_win_rate : 騎手が逃げ/先行馬を騎乗して勝った率（expanding window）
    jockey_close_win_rate : 騎手が差し/追込馬を騎乗して勝った率（expanding window）
    """
    needed = ('jockey_id', 'running_style_num', 'finish', 'race_id')
    if any(c not in h.columns for c in needed) or 'jockey_id' not in df.columns:
        return df, h

    _oi  = h.index.copy()
    _s   = h.sort_values('race_id', kind='mergesort').copy()
    _fin = pd.to_numeric(_s['finish'], errors='coerce').fillna(0)
    _rs  = pd.to_numeric(_s['running_style_num'], errors='coerce').fillna(1.5)
    _win = (_fin == 1).astype(float)
    _jk  = _s['jockey_id']

    for _is_front, _out in [(True, 'jockey_front_win_rate'), (False, 'jockey_close_win_rate')]:
        _valid = (_rs < 1.5) if _is_front else (_rs >= 1.5)
        _s['_w']   = (_win * _valid.astype(float))
        _s['_cnt'] = _valid.astype(float)
        _cum_w   = _s.groupby(_jk, sort=False)['_w'].cumsum()   - _s['_w']
        _cum_cnt = _s.groupby(_jk, sort=False)['_cnt'].cumsum() - _s['_cnt']
        _s[_out]  = np.where(_cum_cnt >= 3, _cum_w / _cum_cnt.clip(1), np.nan)
        _s.drop(columns=['_w', '_cnt'], inplace=True)

    _out_cols = ['jockey_front_win_rate', 'jockey_close_win_rate']
    _back = _s.reindex(_oi)
    for _c in _out_cols:
        h[_c] = _back[_c].values
    df = df.merge(
        h[['jockey_id', 'race_id'] + _out_cols]
        .drop_duplicates(subset=['jockey_id', 'race_id']),
        on=['jockey_id', 'race_id'], how='left')

    return df, h


def _feh_horse_speed(
    df: pd.DataFrame, h: pd.DataFrame
) -> tuple:
    """馬の通算スピード指数 expanding window 統計を付与する。

    full_history_df の prev_race_time / prev_race_distance から horse_id 別の
    expanding mean / std を計算し、馬の歴史的速度ベースラインを特徴量として提供する。
    leave-current-out 方式（row k では rows 0..k-1 の統計）を採用。

    出力列:
      - horse_speed_exp_mean : 過去走の prev_speed_index expanding mean
      - horse_speed_exp_std  : 過去走の prev_speed_index expanding std
      - horse_speed_vs_exp   : 当走 prev_speed_index - expanding mean（自己比較）
    """
    needed = ('horse_id', 'race_id', 'prev_race_time', 'prev_race_distance')
    if any(c not in h.columns for c in needed):
        return df, h

    _oi = h.index.copy()
    _s  = h.sort_values('race_id', kind='mergesort').copy()

    _pt = pd.to_numeric(_s['prev_race_time'], errors='coerce')
    _pd = pd.to_numeric(_s['prev_race_distance'], errors='coerce')
    _s['_spi'] = np.where((_pt > 0) & (_pd > 0), _pd / _pt, np.nan)

    # NaN-aware expanding mean / std (leave-current-out)
    _s['_spi_f']  = _s['_spi'].fillna(0.0)
    _s['_spi_v']  = _s['_spi'].notna().astype(float)
    _s['_spi_sq'] = (_s['_spi'] ** 2).fillna(0.0)

    _cum_sum = _s.groupby('horse_id', sort=False)['_spi_f'].cumsum()  - _s['_spi_f']
    _cum_cnt = _s.groupby('horse_id', sort=False)['_spi_v'].cumsum()  - _s['_spi_v']
    _cum_sq  = _s.groupby('horse_id', sort=False)['_spi_sq'].cumsum() - _s['_spi_sq']

    _s['horse_speed_exp_mean'] = np.where(_cum_cnt > 0, _cum_sum / _cum_cnt, np.nan)

    _denom = np.where(_cum_cnt > 1, _cum_cnt, np.nan)
    _E_x   = _cum_sum / np.where(_cum_cnt > 0, _cum_cnt, np.nan)
    _E_x2  = _cum_sq  / _denom
    _var   = _E_x2 - _E_x ** 2
    _s['horse_speed_exp_std'] = np.where(
        _cum_cnt > 1, np.sqrt(np.maximum(_var, 0.0)), np.nan
    )

    _s = _s.drop(columns=['_spi', '_spi_f', '_spi_v', '_spi_sq'], errors='ignore')
    _back = _s.reindex(_oi)
    h = h.copy()
    h['horse_speed_exp_mean'] = _back['horse_speed_exp_mean'].values
    h['horse_speed_exp_std']  = _back['horse_speed_exp_std'].values

    df = df.merge(
        h[['horse_id', 'race_id', 'horse_speed_exp_mean', 'horse_speed_exp_std']]
        .drop_duplicates(subset=['horse_id', 'race_id']),
        on=['horse_id', 'race_id'], how='left',
    )

    # 自己比較：当走 prev_speed_index - 自馬の歴史平均
    if 'prev_speed_index' in df.columns:
        _spi_cur = pd.to_numeric(df['prev_speed_index'], errors='coerce')
        df['horse_speed_vs_exp'] = np.where(
            _spi_cur.notna() & df['horse_speed_exp_mean'].notna(),
            _spi_cur - df['horse_speed_exp_mean'],
            np.nan,
        )

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

def add_derived_features(
    df: pd.DataFrame,
    full_history_df: Optional[pd.DataFrame] = None,
    training_df: Optional[pd.DataFrame] = None,
    speed_figures_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """データフレームに派生特徴量を追加する（公開 API）。

    内部的には以下の順序でパイプラインを実行する:
      1. _fe_days_from_history  — DB全履歴から days_since_last_race を計算
      2. _fe_horse_category     — 性齢・コーナー・ペース・休養カテゴリ
      3. _fe_id_season          — race_id 分解・季節性・馬場×枠番交互作用
      4. _fe_course             — コース特性（直線長・内枠バイアス）
      5. _fe_market             — オッズ・市場エントロピー
      6. _fe_prev_race          — 前走日・距離変化・スピード指数
      7. _fe_opponent           — レース内相手関係特徴量（P-5）
      8. _fe_holding_time       — 持ちタイム（AplFreqSum）派生特徴量
      9. _fe_lap                — ラップタイム展開・ペース要約
     10. _fe_payout             — 配当派生特徴量
     11. _fe_corner_position    — コーナー通過順→脚質コード（corner_first/last/gain/running_style_code）
     12. _fe_speed_figures      — speed_figures テーブルの速度指数（speed_figures_df 必要）
     13. _fe_training           — 最終追い切り特徴量（training_df 必要）
     14. _fe_missing_flags      — 欠損フラグ付与・型統一
     15. _fe_history            — 全 expanding/rolling 統計 + P-6/P-7/P-8/P-9/P-10（full_history_df 必要）

    Args:
        df: 現在のレースデータ。
        full_history_df: 過去データ全体（統計計算用）。省略時は step 1/14 をスキップ。
        training_df: training_data テーブルの DataFrame。省略時は調教特徴量が NaN になる。
        speed_figures_df: speed_figures テーブルの DataFrame。省略時は速度指数が NaN になる。

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
    df = _fe_opponent(df)
    df = _fe_holding_time(df)
    df = _fe_lap(df)
    df = _fe_payout(df)
    df = _fe_corner_position(df)
    df = _fe_speed_figures(df, speed_figures_df)
    df = _fe_training(df, training_df)
    if full_history_df is not None:
        df = _fe_history(df, full_history_df)
    # _fe_missing_flags は _fe_history 後（P-6〜P-10 の列が揃ってから）
    df = _fe_missing_flags(df)
    # ── ITR-05: 騎手コース得意度（_fe_history後に計算 jockey_course_win_rateが必要）
    # 正 = このコースで平均より高勝率、負 = 苦手コース
    if 'jockey_course_win_rate' in df.columns and 'jockey_recent30_win_rate' in df.columns:
        _jcwr = pd.to_numeric(df['jockey_course_win_rate'], errors='coerce')
        _j30wr = pd.to_numeric(df['jockey_recent30_win_rate'], errors='coerce')
        df['jockey_venue_advantage'] = np.where(
            _jcwr.notna() & _j30wr.notna(), _jcwr - _j30wr, np.nan
        )
    # Rebuild contiguous blocks to reduce fragmentation warnings in downstream ops.
    return df.copy()


