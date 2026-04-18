"""
LightGBM最適化: 全特徴量の前処理戦略
=====================================

全ての特徴量に対してLightGBMに最適な前処理を施す包括的なモジュール
"""

from typing import Dict, List, Tuple, Optional
import pandas as pd
import numpy as np
from sklearn.preprocessing import LabelEncoder

from .constants import FUTURE_FIELDS, UNNECESSARY_COLUMNS  # 共通定数

# 後方互換エイリアス（外部コードが FUTURE_INFO_BLACKLIST を参照している場合）
# =========================================================
# L3-1: 未来情報列ブラックリスト = constants.FUTURE_FIELDS の別名
# =========================================================
FUTURE_INFO_BLACKLIST: frozenset = FUTURE_FIELDS

# =========================================================
# L1-3: venue名正規化辞書（表記揺れ→正式名称に統一）
# VENUE_MAP（constants.py）の値に合わせる
# =========================================================
VENUE_NORMALIZE_MAP: dict = {
    # JRA 「〇〇競馬場」→ 短縮形
    "\u672d\u5e4c\u7af6\u99ac\u5834": "\u672d\u5e4c",
    "\u51fd\u9928\u7af6\u99ac\u5834": "\u51fd\u9928",
    "\u798f\u5cf6\u7af6\u99ac\u5834": "\u798f\u5cf6",
    "\u65b0\u6f5f\u7af6\u99ac\u5834": "\u65b0\u6f5f",
    "\u6771\u4eac\u7af6\u99ac\u5834": "\u6771\u4eac",
    "\u4e2d\u5c71\u7af6\u99ac\u5834": "\u4e2d\u5c71",
    "\u4e2d\u4eac\u7af6\u99ac\u5834": "\u4e2d\u4eac",
    "\u4eac\u90fd\u7af6\u99ac\u5834": "\u4eac\u90fd",
    "\u962a\u795e\u7af6\u99ac\u5834": "\u962a\u795e",
    "\u5c0f\u5009\u7af6\u99ac\u5834": "\u5c0f\u5009",
    # 半角カッコ→全角カッコ（帯広）
    "\u5e2f\u5e83(\u3070)": "\u5e2f\u5e83\uff08\u3070\uff09",
    "\u5e2f\u5e83(\u3070\u3093\u3048\u3044)": "\u5e2f\u5e83(\u3070\u3093\u3048\u3044)",
}


class LightGBMFeatureOptimizer:
    """LightGBM用の包括的な特徴量最適化クラス
    
    全特徴量を以下のカテゴリに分類し、最適な前処理を施す:
    1. カテゴリカル変数（低カーディナリティ）: Label Encoding
    2. カテゴリカル変数（高カーディナリティ）: 統計特徴量化
    3. 数値変数: そのまま or スケーリング
    4. リスト型変数: 統計値に変換
    5. 日時変数: 数値化
    """
    
    def __init__(self):
        self.label_encoders = {}
        self.categorical_features = []
        self.feature_stats = {}
        self.fitted = False
    
    def fit_transform(self, df: pd.DataFrame, target_col: Optional[str] = None) -> Tuple[pd.DataFrame, List[str]]:
        """学習データに対して特徴量最適化を実行
        
        Args:
            df: 入力データフレーム
            target_col: 目的変数のカラム名（統計計算時に除外）
        
        Returns:
            (最適化後のデータフレーム, カテゴリカル特徴量リスト)
        """
        df = df.copy()
        self.categorical_features = []

        # ===== L3-1: 未来情報列を強制除外（データリーク防止） =====
        _bl_present = [c for c in FUTURE_INFO_BLACKLIST if c in df.columns]
        if _bl_present:
            print(f"  ⚠ [L3-1] 未来情報列を除外 ({len(_bl_present)}列): {_bl_present}")
            df = df.drop(columns=_bl_present)

        # ===== 0. 欠損値補完（学習・推論共通） =====
        df = self._fill_missing_values(df)

        print("\n" + "="*80)
        print("【LightGBM特徴量最適化】学習モード")
        print("="*80)
        
        # ===== 1. カテゴリカル変数（低カーディナリティ） =====
        # Label Encoding + categorical_feature指定
        low_card_categorical = {
            # レース条件系（10種類前後）
            'venue': 'venue_encoded',              # 競馬場: 東京/中山/阪神など10箇所
            'venue_code': 'venue_code_encoded',    # 競馬場コード
            'track_type': 'track_type_encoded',    # 芝/ダート
            'surface': 'surface_encoded',          # 芝/ダート (track_typeの別名)
            'weather': 'weather_encoded',          # 晴/曇/雨
            'track_condition': 'track_condition_encoded',  # 良/稍重/重/不良
            'field_condition': 'field_condition_encoded',  # 良/稍重/重/不良(Ultimate版)
            'race_class': 'race_class_encoded',    # 新馬/未勝利/1勝/2勝...
            'course_direction': 'course_direction_encoded',  # 右/左/直線
            
            # 馬の属性（数種類）
            'sex': 'sex_encoded',                  # 牡/牝/セ
            
            # コース特性（数種類）
            'corner_radius': 'corner_radius_encoded',  # tight/medium/large
            'track_type_detailed': 'track_type_detailed_encoded',  # inner/outer/straight
            
            # ペース・脚質（数種類）
            'pace_classification': 'pace_encoded',  # H/M/S
            'predicted_pace': 'predicted_pace_encoded',  # 出馬表の予想ペース
            # running_style → running_style_encoded は running_style_num(11%gain)と冗長（ITR-02で除去）
            # 'running_style': 'running_style_encoded',  # 逃/先/差/追
            
            # その他
            'coat_color': 'coat_color_encoded',    # 毛色
        }
        
        print("\n【1. 低カーディナリティ カテゴリカル変数】")
        print("処理: Label Encoding → LightGBMのcategorical_feature指定")
        
        # 元のカテゴリカルカラム名を記録（後で削除用）
        original_categorical_cols = []
        
        for original_col, encoded_col in low_card_categorical.items():
            if original_col in df.columns:
                original_categorical_cols.append(original_col)
                df, encoded_col_name = self._label_encode_column(df, original_col, encoded_col)
                if encoded_col_name:
                    self.categorical_features.append(encoded_col_name)
                    unique_count = df[encoded_col_name].nunique()
                    print(f"  ✓ {original_col:30s} → {encoded_col_name:30s} ({unique_count}種類)")
        
        # 元のカテゴリカルカラム（object型）を削除
        print(f"\n  元のカテゴリカルカラムを削除: {original_categorical_cols}")
        df = df.drop(columns=[col for col in original_categorical_cols if col in df.columns])
        
        # ===== 2. 高カーディナリティ文字列列の削除 =====
        # feature_engineering.py の _feh_entity_career / _feh_entity_recent30 が
        # expanding window で騎手・調教師・血統の統計量（jockey_place_rate_top2,
        # sire_win_rate 等）を計算済みのため、名前文字列そのものは不要。
        # ※ _add_entity_statistics は全行がゼロ分散になる問題があり廃止。
        print("\n【2. 高カーディナリティ文字列列】")
        print("処理: 削除（統計特徴量は feature_engineering.py で計算済み）")
        _hc_name_cols = [
            'jockey_name', 'trainer_name', 'horse_name',
            'sire', 'dam', 'damsire', 'sire_name', 'dam_name', 'dam_sire_name',
        ]
        for _nc in _hc_name_cols:
            if _nc in df.columns:
                print(f"  ✓ {_nc} → 削除")
                df = df.drop(_nc, axis=1)
        
        # ===== 3. 数値変数 =====
        print("\n【3. 数値変数】")
        print("処理: そのまま使用（LightGBMは自動でスケーリング不要）")
        numeric_features = [
            'horse_number',           # 馬番
            'bracket_number',         # 枠番
            'horse_weight',           # 馬体重
            'horse_weight_change',    # 馬体重変化
            'weight_change',          # 馬体重変化（別名）
            'age',                    # 年齢
            # burden_weight → UNNECESSARY_COLUMNS (r=0.970 with horse_weight)
            'odds',                   # オッズ
            'popularity',             # 人気
            'distance',               # 距離
            'num_horses',             # 出走頭数
            'straight_length',        # 直線距離
            'inner_bias',             # 内枠有利性
            'race_num',               # レース番号
            'kai',                    # 開催回数
            'day',                    # 開催日目

            # 近走派生特徴
            'days_since_last_race',   # 前走からの日数
            'last_distance_change',   # 距離変化（旧形式）
            'distance_change',        # 距離変化（新形式）
            'prev_race_finish',       # 前走着順
            'prev_race_distance',     # 前走距離
            # prev_race_weight → UNNECESSARY_COLUMNS (r=0.947 with horse_weight)
            'prev2_race_finish',      # 前々走着順
            # prev2_race_time → UNNECESSARY_COLUMNS (r=0.980 with prev2_race_distance)

            # 馬の通算成績: UNNECESSARY_COLUMNS に移動（スクレイプ時点値のリークリスク）
            # horse_total_runs / horse_total_wins / horse_total_prize_money / horse_win_rate
            # → 再スクレイプ時に現在（2026年）の値が入り過去レースに未来情報が混入するため除外
            # → 代替: expanding window の past_10_win_rate / past_10_races_count を使用

            # 市場分析
            'market_entropy',         # 市場エントロピー（混戦度）
            'top3_probability',       # 上位3頭の暗黙確率和

            # 騎手・調教師の複勝率
            'jockey_place_rate_top2', # 騎手複勝率（2着以内）
            'jockey_show_rate',       # 騎手複勝率（3着以内）
            'trainer_place_rate_top2',# 調教師複勝率（2着以内）
            'trainer_show_rate',      # 調教師複勝率（3着以内）

            # 統計特徴（feature_engineeringで生成）
            'jockey_course_win_rate',
            'jockey_course_races',
            'horse_distance_win_rate',
            'horse_distance_avg_finish',
            'trainer_recent_win_rate',
            'jockey_win_rate',        # lightgbm_feature_optimizerで生成
            'jockey_avg_finish',
            'jockey_race_count',
            'trainer_win_rate',
            'trainer_avg_finish',
            'trainer_race_count',
            'sire_win_rate',          # 父馬統計
            'sire_avg_finish',
            'sire_race_count',
            'damsire_win_rate',       # 母父馬統計
            'damsire_avg_finish',
            'damsire_race_count',

            # コーナー派生特徴 ─── NOTE: 当該レース結果（リーク）のため unnecessary_cols で削除
            # 過去N走平均コーナー位置を UltimateFeatureCalculator で実装予定（P2以降）
            # 'corner_position_avg',
            # 'corner_position_variance',
            # 'last_corner_position',
            # 'position_change',
            # 'corner_1', 'corner_2', 'corner_3', 'corner_4',

            # ラップタイム（距離別）
            'lap_200m', 'lap_400m', 'lap_600m', 'lap_800m',
            'lap_1000m', 'lap_1200m', 'lap_1400m', 'lap_1600m',
            'lap_1800m', 'lap_2000m', 'lap_2200m', 'lap_2400m',
            'lap_sect_200m', 'lap_sect_400m', 'lap_sect_600m', 'lap_sect_800m',
            'lap_sect_1000m', 'lap_sect_1200m', 'lap_sect_1400m', 'lap_sect_1600m',
            'lap_sect_1800m', 'lap_sect_2000m', 'lap_sect_2200m', 'lap_sect_2400m',

            # その他派生特徴 ─── NOTE: last_3f系・time_seconds はリーク → unnecessary_cols で削除
            # 'last_3f_rank',
            # 'last_3f_rank_normalized',
            'inner_advantage',
            # 'time_seconds',           # リーク → unnecessary_cols で削除

            # P2-7: スピード指標（前走タイム÷距離）
            'prev_speed_index',       # 前走速度指標 (m/s)
            'prev_speed_zscore',      # 前走速度の同条件zスコア
            'prev2_speed_index',      # 前々走速度指標 (m/s)  ← prev2_race_time の代替
            'prev2_speed_zscore',     # 前々走速度の同条件zスコア

            # P2-8: 馬場・距離帯適性
            'horse_surface_win_rate',      # 馬の同馬場勝率
            'horse_surface_races',         # 馬の同馬場出走数
            'horse_dist_band_win_rate',    # 馬の距離帯別勝率
            'horse_dist_band_races',       # 馬の距離帯別出走数
            'horse_venue_win_rate',        # 馬の競馬場別勝率
            'horse_venue_races',           # 馬の競馬場別出走数

            # P2-9: 枠番バイアス
            'gate_win_rate',          # (会場×距離帯×馬場)での枠番勝率

            # P3-10: 騎手×調教師コンビ成績
            'jt_combo_races',         # 騎手×調教師コンビ出走数
            'jt_combo_win_rate',      # 騎手×調教師コンビ勝率
            'jt_combo_win_rate_smooth', # ベイズ平滑化後

            # A-9: オッズのレース内正規化（市場情報の精緻化）
            'implied_prob',           # 暗黙確率 (1/odds)
            # 'implied_prob_norm',    # UNNECESSARY_COLUMNS に移動（popularity と高相関）
            # 'odds_rank_in_race',    # UNNECESSARY_COLUMNS に移動（popularity と重複）
            'odds_z_in_race',         # レース内オッズ z-score

            # A-7: 欠損フラグ（0埋めより NaN+フラグの方が安定）
            'prev_race_finish_is_missing',
            'prev_race_time_is_missing',
            'prev_race_distance_is_missing',
            'prev2_race_finish_is_missing',
            'days_since_last_race_is_missing',
            # prev_race_weight_is_missing → UNNECESSARY_COLUMNS (prev_race_weight 削除に伴い不要)
            # prev2_race_time_is_missing  → UNNECESSARY_COLUMNS (prev2_race_time 削除に伴い不要)
            # prev2_race_weight_is_missing→ UNNECESSARY_COLUMNS (prev2_race_weight 削除に伴い不要)
            'prev_speed_index_is_missing',
            'prev_speed_zscore_is_missing',
            'prev2_speed_index_is_missing',  # prev2_speed_index 欠損フラグ
            'prev2_speed_zscore_is_missing', # prev2_speed_zscore 欠損フラグ
            # horse_win_rate_is_missing: UNNECESSARY_COLUMNS に移動（horse_win_rate 削除に伴い不要）
            # A-7: オッズ・人気 欠損フラグ（最重要特徴の欠落を安全に扱う）
            'odds_is_missing',
            'popularity_is_missing',

            # L2-1: 馬の近走統計（近 3/5/10 走の平均着順・勝率）
            'past3_avg_finish',       # 近3走平均着順
            'past5_avg_finish',       # 近5走平均着順
            'past10_avg_finish',      # 近10走平均着順
            'past3_win_rate',         # 近3走勝率
            'past5_win_rate',         # 近5走勝率

            # L2-2: 騎手・調教師の直近30走勝率（近況パフォーマンス）
            'jockey_recent30_win_rate',   # 騎手の近30走勝率
            'trainer_recent30_win_rate',  # 調教師の近30走勝率

            # L3-1: 馬体重トレンド（過去5走の体重変化 slope / 平均変化量）
            'past_5_weight_slope',        # 体重の増減トレンド (kg/走、正=増加傾向)
            'past_5_weight_avg_change',   # 平均体重変化量 (kg)
        ]
        
        available_numeric = [col for col in numeric_features if col in df.columns]
        print(f"  ✓ 数値特徴量: {len(available_numeric)}個")
        for col in available_numeric[:10]:  # 最初の10個だけ表示
            print(f"    - {col}")
        if len(available_numeric) > 10:
            print(f"    ... 他{len(available_numeric)-10}個")
        
        # ===== 4. バイナリ特徴量（0/1） =====
        print("\n【4. バイナリ特徴量】")
        print("処理: そのまま使用（0/1エンコード済み）")
        binary_features = [
            'is_young',               # 若馬フラグ
            'is_prime',               # 最盛期フラグ
            'is_veteran',             # ベテランフラグ
            'distance_increased',     # 距離延長フラグ
            'distance_decreased',     # 距離短縮フラグ
            'surface_changed',        # 芝ダ変更フラグ
        ]
        available_binary = [col for col in binary_features if col in df.columns]
        print(f"  ✓ バイナリ特徴量: {len(available_binary)}個")
        
        # ===== 5. リスト型変数 =====
        print("\n【5. リスト型変数】")
        print("処理: 統計値（平均、分散など）に変換")
        
        # corner_positions_list: [5, 5, 4, 3] → 統計値
        if 'corner_positions_list' in df.columns:
            # これは既にfeature_engineeringで処理済み
            # (corner_position_avg, corner_position_variance, etc.)
            print(f"  ✓ corner_positions_list → 統計値に変換済み（feature_engineeringで処理）")
            # 元のリストは削除
            if 'corner_positions_list' in df.columns:
                df = df.drop('corner_positions_list', axis=1)
        
        # past_performances（リスト）も同様に処理済み
        if 'past_performances' in df.columns:
            print(f"  ✓ past_performances → 削除（days_since_last_raceなどに変換済み）")
            df = df.drop('past_performances', axis=1)
        
        # ===== 6. ダミー変数（get_dummies済み） =====
        print("\n【6. ダミー変数】")
        print("処理: そのまま使用（既にバイナリ化済み）")
        
        # sex_牡, sex_牝, sex_セ
        sex_dummies = [col for col in df.columns if col.startswith('sex_')]
        if sex_dummies:
            print(f"  ✓ 性別ダミー: {sex_dummies}")
        
        # pace_H, pace_M, pace_S
        pace_dummies = [col for col in df.columns if col.startswith('pace_')]
        if pace_dummies:
            print(f"  ✓ ペースダミー: {pace_dummies}")
        
        # rest_short, rest_normal, rest_long, rest_very_long
        rest_dummies = [col for col in df.columns if col.startswith('rest_')]
        if rest_dummies:
            print(f"  ✓ 休養期間ダミー: {rest_dummies}")
        
        # pop_trend_improving, pop_trend_declining, pop_trend_stable
        trend_dummies = [col for col in df.columns if col.startswith('pop_trend_')]
        if trend_dummies:
            print(f"  ✓ 人気トレンドダミー: {trend_dummies}")
        
        # ===== 7. ID系変数 =====
        print("\n【7. ID系変数】")
        print("処理: そのまま保持（統計計算に使用、学習時は除外推奨）")
        id_features = [
            'race_id',
            'horse_id',
            'jockey_id',
            'trainer_id',
            'owner_id',
        ]
        available_ids = [col for col in id_features if col in df.columns]
        print(f"  ✓ ID特徴量: {available_ids}")
        print(f"    → 学習時にはこれらを除外してください")
        
        # ===== 8. 日時変数 =====
        print("\n【8. 日時変数】")
        print("処理: 数値化（年/月/日/曜日など）")
        
        if 'date' in df.columns:
            df = self._process_date_column(df, 'date')
            print(f"  ✓ date → date_year, date_month, date_day, date_dayofweek")
        
        if 'birth_date' in df.columns:
            df = self._process_date_column(df, 'birth_date', prefix='birth')
            print(f"  ✓ birth_date → birth_year, birth_month")
        
        # ===== 9. 不要な変数 =====
        # constants.UNNECESSARY_COLUMNS で一元管理。
        # FUTURE_FIELDS で既に除去済みの列が含まれる場合は単なる no-op になる。
        print("\n【9. 不要な変数（削除推奨）】")
        # constants.UNNECESSARY_COLUMNS で一元管理（fit_transform / transform 共通）
        unnecessary_cols = list(UNNECESSARY_COLUMNS)
        
        for col in unnecessary_cols:
            if col in df.columns:
                print(f"  ✓ {col} → 削除")
                df = df.drop(col, axis=1)

        # sex_牡/sex_牝/sex_セ ダミーは sex_encoded と重複するため削除
        sex_dummy_cols = [c for c in df.columns if c.startswith('sex_') and c != 'sex_encoded']
        if sex_dummy_cols:
            df = df.drop(columns=sex_dummy_cols, errors='ignore')
            print(f"  ✓ 性別ダミー（重複）削除: {sex_dummy_cols}")

        # ===== 10. 特徴量変換（対数変換・ベイズ平滑化・信頼度フラグ）======
        df = self._add_feature_transforms(df)

        # ===== 欠損率が高すぎる列を除去（閾値: 90%以上欠損） =====
        # 全件 NaN の列（lap_xxxm, kai, day など JS レンダリング列）はモデルに悪影響
        if len(df) > 0:
            miss_rate = df.isnull().mean()
            high_miss_cols = miss_rate[miss_rate >= 0.90].index.tolist()
            # ID/target 列は除外対象から除く
            keep_cols = ['race_id', 'horse_id', 'jockey_id', 'trainer_id',
                         'win', 'place', 'finish', 'finish_position']
            drop_miss = [c for c in high_miss_cols if c not in keep_cols]
            if drop_miss:
                print(f"\n  ⚠️  欠損率90%超の列を除去 ({len(drop_miss)}列): {drop_miss[:8]}{'...' if len(drop_miss)>8 else ''}")
                df = df.drop(columns=drop_miss, errors='ignore')

        # ===== 重複列の除去 =====
        dup_cols = df.columns[df.columns.duplicated()].tolist()
        if dup_cols:
            print(f"  ⚠️  重複列を除去: {dup_cols}")
            df = df.loc[:, ~df.columns.duplicated()]

        # ===== fix-D: ゼロ分散列の除去（死に特徴 = モデルノイズになる）=====
        _exclude_from_var_check = {'race_id', 'horse_id', 'jockey_id', 'trainer_id',
                                   'win', 'place', 'finish', 'finish_position'}
        _num_cols = df.select_dtypes(include='number').columns
        _var_cols = [c for c in _num_cols if c not in _exclude_from_var_check]
        if _var_cols:
            _std = df[_var_cols].std(ddof=0)
            _zero_var = _std[_std < 1e-6].index.tolist()
            if _zero_var:
                print(f"  ⚠️  [fix-D] ゼロ分散列を除去 ({len(_zero_var)}列): {_zero_var}")
                df = df.drop(columns=_zero_var, errors='ignore')
                # 学習済みモデルのバンドルに記録しておく
                self._zero_var_cols = getattr(self, '_zero_var_cols', []) + _zero_var

        # ===== S-2チェック: track_type_encoded と corner_radius_encoded の独立性検証 =====
        if 'track_type_encoded' in df.columns and 'corner_radius_encoded' in df.columns:
            _corr = df['track_type_encoded'].corr(df['corner_radius_encoded'].fillna(-1))
            if abs(_corr) > 0.95:
                print(f"  ⚠️  S-2警告: track_type_encoded と corner_radius_encoded の相関が {_corr:.3f}")
                print(f"       → これらは本来別の意味を持つ特徴量のはずです")
                print(f"       → track_type: {df['track_type_encoded'].value_counts().to_dict()}")
                print(f"       → corner_radius: {df['corner_radius_encoded'].value_counts().to_dict()}")
            else:
                print(f"  ✓ S-2確認: track_type_encoded と corner_radius_encoded は独立 (corr={_corr:.3f})")
        # surface_encoded (芝/ダ) の確認
        if 'surface_encoded' in df.columns:
            print(f"  ✓ S-2確認: surface_encoded (芝/ダ) = {df['surface_encoded'].value_counts().to_dict()}")

        # ===== 最終統計 =====
        print("\n" + "="*80)
        print("【最適化完了】")
        print("="*80)
        print(f"  元のカラム数: {len(df.columns)}個")
        print(f"  カテゴリカル特徴量: {len(self.categorical_features)}個")
        print(f"    → LightGBMのcategorical_feature引数に指定: {self.categorical_features[:5]}...")
        print(f"\n  推奨LightGBMパラメータ:")
        print(f"    'categorical_feature': {self.categorical_features}")
        print(f"    'max_cat_to_onehot': 4  # 4種類以下は自動でワンホット化")
        print("="*80 + "\n")
        
        self.fitted = True
        return df, self.categorical_features
    
    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """テスト/予測データに対して同じ変換を適用
        
        Args:
            df: 入力データフレーム
        
        Returns:
            最適化後のデータフレーム
        """
        if not self.fitted:
            raise ValueError("先にfit_transform()を実行してください")
        
        df = df.copy()

        # ===== L3-1: 未来情報列を強制除外（データリーク防止） =====
        _bl_present = [c for c in FUTURE_INFO_BLACKLIST if c in df.columns]
        if _bl_present:
            df = df.drop(columns=_bl_present)

        # ===== 0. 欠損値補完（学習・推論共通） =====
        df = self._fill_missing_values(df)

        print("\n【LightGBM特徴量最適化】推論モード")

        # 学習時と同じ変換を適用
        # Label Encoding
        for original_col, encoded_col in [
            ('venue', 'venue_encoded'),
            ('venue_code', 'venue_code_encoded'),
            ('track_type', 'track_type_encoded'),
            ('surface', 'surface_encoded'),
            ('weather', 'weather_encoded'),
            ('track_condition', 'track_condition_encoded'),
            ('field_condition', 'field_condition_encoded'),
            ('race_class', 'race_class_encoded'),
            ('course_direction', 'course_direction_encoded'),
            ('sex', 'sex_encoded'),
            ('corner_radius', 'corner_radius_encoded'),
            ('pace_classification', 'pace_encoded'),
            ('predicted_pace', 'predicted_pace_encoded'),
            # running_style_encoded は ITR-02 で除去（running_style_num と冗長）
            # ('running_style', 'running_style_encoded'),
            ('coat_color', 'coat_color_encoded'),
        ]:
            if original_col in df.columns and original_col in self.label_encoders:
                le = self.label_encoders[original_col]
                # A-8: 未知カテゴリは NaN にする（-1 は LightGBM が既知値として扱うため）
                # NaN → LightGBM が「欠損」として両枝を探索 = 最も安全な未知カテゴリ処理
                df[encoded_col] = df[original_col].map(
                    lambda x, _le=le: float(_le.transform([x])[0]) if x in _le.classes_ else np.nan
                )
        
        # 高カーディナリティ文字列列を削除（fit_transform と同じセット）
        _hc_cols_t = [
            'jockey_name', 'trainer_name', 'horse_name',
            'sire', 'dam', 'damsire', 'sire_name', 'dam_name', 'dam_sire_name',
        ]
        for col in _hc_cols_t:
            if col in df.columns:
                df = df.drop(col, axis=1)
        
        # リスト型変数を削除
        if 'corner_positions_list' in df.columns:
            df = df.drop('corner_positions_list', axis=1)
        if 'past_performances' in df.columns:
            df = df.drop('past_performances', axis=1)
        
        # 日時変数を処理
        if 'date' in df.columns:
            df = self._process_date_column(df, 'date')
        if 'birth_date' in df.columns:
            df = self._process_date_column(df, 'birth_date', prefix='birth')
        
        # 不要な変数を削除（constants.UNNECESSARY_COLUMNS で fit_transform と統一）
        for col in UNNECESSARY_COLUMNS:
            if col in df.columns:
                df = df.drop(col, axis=1)

        # sex_牡/sex_牝/sex_セ ダミーは sex_encoded と重複するため削除
        sex_dummy_cols = [c for c in df.columns if c.startswith('sex_') and c != 'sex_encoded']
        if sex_dummy_cols:
            df = df.drop(columns=sex_dummy_cols, errors='ignore')

        # 特徴量変換（対数変換・ベイズ平滑化）
        df = self._add_feature_transforms(df)

        print(f"  ✓ 変換完了: {len(df.columns)}カラム")

        return df
    
    def _label_encode_column(self, df: pd.DataFrame, col: str, new_col: str) -> Tuple[pd.DataFrame, Optional[str]]:
        """カラムをLabel Encodingする"""
        if col not in df.columns:
            return df, None
        
        # 欠損値を'Unknown'で埋める
        df[col] = df[col].fillna('Unknown')
        
        le = LabelEncoder()
        df[new_col] = le.fit_transform(df[col].astype(str))
        self.label_encoders[col] = le
        
        return df, new_col
    
    def _add_entity_statistics(self, df: pd.DataFrame, id_col: str, name_col: str, 
                              target_col: Optional[str], prefix: str) -> pd.DataFrame:
        """エンティティ（騎手/調教師）の統計特徴量を追加（データリーク防止版）

        race_id の辞書順 = 時系列順を利用し、各行に対して「それより前のレース」
        だけで集計した expanding window 統計を使用する。
        これにより未来のレース結果が過去の特徴量に混入するデータリークを防ぐ。
        
        Args:
            df: データフレーム（race_id カラムが必須）
            id_col: IDカラム（例: 'jockey_id'）
            name_col: 名前カラム（例: 'jockey_name'）
            target_col: 目的変数カラム（win, placeなど）
            prefix: 特徴量の接頭辞（例: 'jockey'）
        """
        finish_col = ('finish_position' if 'finish_position' in df.columns
                      else 'finish' if 'finish' in df.columns else None)

        if 'race_id' in df.columns and finish_col and id_col in df.columns:
            # ── Expanding window（時系列リーク防止） ──────────────────────────
            orig_idx = df.index.copy()
            # race_id 辞書順 = 時系列順でソート（安定ソートで同一race_id内の順序を保持）
            df_sorted = df.sort_values('race_id', kind='mergesort').copy()

            fin_num  = pd.to_numeric(df_sorted[finish_col], errors='coerce').fillna(0)
            win_flag = (fin_num == 1).astype(float)

            # groupby().cumcount() = グループ内で現在行より前の行数（0始まり）= 「前走数」
            race_cnt = df_sorted.groupby(id_col, sort=False).cumcount()

            # cumsum() はこの行を含む累積。current を引いて「前走までの累積」に変換
            df_sorted['_w'] = win_flag
            cum_wins = df_sorted.groupby(id_col, sort=False)['_w'].cumsum() - df_sorted['_w']
            df_sorted.drop(columns=['_w'], inplace=True)

            df_sorted['_f'] = fin_num
            cum_fin  = df_sorted.groupby(id_col, sort=False)['_f'].cumsum() - df_sorted['_f']
            df_sorted.drop(columns=['_f'], inplace=True)

            df_sorted[f'{prefix}_win_rate']   = (cum_wins / race_cnt.clip(1)).fillna(0.0)
            df_sorted[f'{prefix}_race_count'] = race_cnt
            df_sorted[f'{prefix}_avg_finish'] = (cum_fin  / race_cnt.clip(1)).fillna(8.0)

            # 元の行順に戻して代入
            df_back = df_sorted.reindex(orig_idx)
            df[f'{prefix}_win_rate']   = df_back[f'{prefix}_win_rate'].values
            df[f'{prefix}_race_count'] = df_back[f'{prefix}_race_count'].values
            df[f'{prefix}_avg_finish'] = df_back[f'{prefix}_avg_finish'].values

        else:
            # race_id / finish 列がない場合（推論時など）はデフォルト値
            df[f'{prefix}_win_rate']   = 0.0
            df[f'{prefix}_race_count'] = 0
            df[f'{prefix}_avg_finish'] = 8.0

        return df
    
    def _fill_missing_values(self, df: pd.DataFrame) -> pd.DataFrame:
        """欠損値を補完する（LightGBM投入前の前処理）

        補完方針:
          - 前走/前々走情報 (prev_race_*): 0埋め + is_first_race フラグ追加
          - コーナー通過 (corner_1~4, 派生): 0埋め（未計測コーナーなし扱い）
          - 血統 (sire, damsire, dam): "Unknown" 埋め（統計変換で処理）
          - 通算成績 (horse_total_*): 0埋め（新馬扱い）
          - horse_win_rate: L1-1 NaN のまま保持（_is_missing フラグで処理）
          - 体重・オッズ: LightGBMがNaNを自動処理するため補完不要
        """
        # ── L1-3: venue名正規化（表記揺れ→VENUE_MAP準拠の正式名に統一）──────
        if 'venue' in df.columns:
            df['venue'] = (
                df['venue'].astype(str).str.strip()
                .map(lambda v: VENUE_NORMALIZE_MAP.get(v, v))
            )

        # ── 初出走フラグ（前走情報が全欠損の馬）──────────────────────────────
        if 'prev_race_finish' in df.columns:
            df['is_first_race'] = df['prev_race_finish'].isna().astype(int)
        else:
            df['is_first_race'] = 0

        # ── 前走情報: A-7 / L1-1 の _is_missing フラグがある列は NaN のまま ──
        # _is_missing フラグを持つ列は 0 埋めしない（LightGBM が欠損として両枝探索）
        _has_missing_flag = {
            'prev_race_finish', 'prev_race_time', 'prev_race_distance',
            'prev2_race_finish', 'prev2_race_distance',
            'days_since_last_race',
            'prev_speed_index', 'prev_speed_zscore',
            'prev2_speed_index', 'prev2_speed_zscore',
            'horse_win_rate',  # L1-1: 77.3% 欠損 → NaN 保持
            # prev_race_weight / prev2_race_weight / burden_weight / prev_race_time_seconds /
            # prev2_race_time は UNNECESSARY_COLUMNS で除去済み → _is_missing フラグも不要
        }
        prev_numeric_cols = [
            'distance_change',
        ]
        for col in prev_numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        # _is_missing フラグがある列は数値変換のみ（NaN を保持）
        for col in _has_missing_flag:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        # ── コーナー通過: 0 埋め（短距離・通過なし扱い）──────────────────────
        corner_cols = [
            'corner_1', 'corner_2', 'corner_3', 'corner_4',
            'corner_position_avg', 'corner_position_variance',
            'last_corner_position', 'position_change',
        ]
        for col in corner_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        # ── 血統: "Unknown" 埋め（_add_entity_statistics が文字列を期待）────
        for col in ['sire', 'damsire', 'dam']:
            if col in df.columns:
                df[col] = df[col].fillna('Unknown')

        # ── 通算成績: 0 埋め（新馬・データ未取得馬）── horse_win_rate は除き NaN 保持
        career_fill0_cols = [
            'horse_total_runs', 'horse_total_wins', 'horse_total_prize_money',
        ]
        for col in career_fill0_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        # ── A-7 フォールバック: odds/popularity 欠損フラグ ────────────────────
        # feature_engineering.add_derived_features で生成されるが、
        # 単独で optimizer を呼んだ場合のフォールバックとしてここでも生成する
        for _miss_col, _src_col in [('odds_is_missing', 'odds'),
                                     ('popularity_is_missing', 'popularity')]:
            if _miss_col not in df.columns and _src_col in df.columns:
                _s = pd.to_numeric(df[_src_col], errors='coerce')
                df[_miss_col] = _s.isna().astype(int)

        # ── コーナー通過列・上がり3F列は unnecessary_cols で削除するため補完不要 ──
        # corner_1~4, corner_position_*, last_corner_position, position_change,
        # last_3f_rank, last_3f_rank_normalized, last_3f_time
        # → 当該レースの結果データ（予測前には存在しない）= リーク → 削除

        # ── fix-A: distance=0/NaN の安全網 ─────────────────────────────────────
        # 本来は tools/fix_distance_zero.py で DB を修正し
        # db_ultimate_loader が _invalid_distance レースをスキップするので
        # ここに到達する件数はゼロが理想。残った場合のみ中央値で補完。
        if 'distance' in df.columns:
            _dist = pd.to_numeric(df['distance'], errors='coerce')
            _invalid = _dist.isna() | (_dist <= 0)
            if _invalid.any():
                _med = _dist[~_invalid].median()
                if np.isnan(_med):
                    _med = 1600.0  # データが全滅した場合の最終フォールバック
                n_inv = _invalid.sum()
                print(
                    f"  ⚠ [fix-A][要調査] distance=0/NaN が残存: {n_inv} 件"
                    f" → 中央値 {_med:.0f}m で緊急補完（本来はスキップ対象）"
                    f"\n     tools/fix_distance_zero.py --dry-run で原因を確認してください"
                )
                df['distance'] = _dist.where(~_invalid, _med)

        # ── track_type → surface フォールバック（二重安全網）────────────────────────────
        # races_ultimate JSON は track_type='芝'/'ダート' で保存, surface=None のため
        # predict.py / regen_pipeline_output.py で fillna するが、optimizer でも保証する
        if 'surface' in df.columns and 'track_type' in df.columns:
            df['surface'] = df['surface'].fillna(df['track_type'])
        elif 'track_type' in df.columns and 'surface' not in df.columns:
            df['surface'] = df['track_type']

        # ── 馬場変更フラグ（ここで生成→ track_type/surfaceはラベルエンコード後に削除されるため）──
        _surf_map = {'苝': 'turf', 'ダート': 'dirt', '花嵐': 'dirt', 'dirt': 'dirt', 'turf': 'turf'}
        if 'prev_race_surface' in df.columns:
            prev_surf = df['prev_race_surface'].map(
                lambda v: _surf_map.get(str(v), 'unknown') if pd.notna(v) else 'unknown'
            )
            cur_surf_col = ('track_type' if 'track_type' in df.columns
                            else 'surface' if 'surface' in df.columns else None)
            if cur_surf_col:
                cur_surf = df[cur_surf_col].map(
                    lambda v: _surf_map.get(str(v), 'unknown') if pd.notna(v) else 'unknown'
                )
                df['is_surface_change'] = (
                    (prev_surf != 'unknown') &
                    (cur_surf  != 'unknown') &
                    (prev_surf != cur_surf)
                ).astype(int)
            else:
                df['is_surface_change'] = 0

        return df

    def _add_feature_transforms(self, df: pd.DataFrame) -> pd.DataFrame:
        """特徴量変換を追加する

        変換メニュー:
          A. 対数変換: 右歪み分布の特徴量を正規化
             - log_odds           : log1p(odds)  オッズは 1〜682 の極端右歪み
             - log_prize          : log1p(horse_total_prize_money)  賞金も右歪み
             - log_total_runs     : log1p(horse_total_runs)  出走数も右歪み
          B. ベイズ平滑化勝率: 少数サンプル騎手/調教師/父馬の勝率ノイズを抑制
             - {prefix}_win_rate_smooth : (count*rate + k*global) / (count + k)
          C. 信頼度フラグ: 統計が信頼できるか（サンプル数 >= 閾値）
             - {prefix}_has_history : race_count >= 3 → 1
          D. 休養区分: days_since_last_race を 4段階に離散化
             - rest_category : 0=初出走, 1=近走(1-21日), 2=通常(22-90日), 3=休み明け(91日+)
        """
        # ── A. 対数変換 ──────────────────────────────────────────────────────
        # log_odds: LightGBM 木モデルで odds と等価（単調変換不変）→ 削除
        # log_prize / log_total_runs: 基底列 (horse_total_*) が UNNECESSARY_COLUMNS に
        # 追加されたため生成不要（スクレイプ時点値のリークリスク対策）

        # ── B & C. ベイズ平滑化勝率 + 信頼度フラグ ──────────────────────────
        # 地方競馬の平均勝率 ≈ 7.5%（1/頭数平均 ≈ 1/13）
        GLOBAL_WIN_RATE = 0.075
        K = 5  # 平滑化強度（K レース分の事前分布）

        for prefix in ['jockey', 'trainer', 'sire', 'damsire']:
            rate_col  = f'{prefix}_win_rate'
            count_col = f'{prefix}_race_count'
            if rate_col in df.columns and count_col in df.columns:
                rate = pd.to_numeric(df[rate_col],  errors='coerce').fillna(0)
                cnt  = pd.to_numeric(df[count_col], errors='coerce').fillna(0)
                # ベイズ推定: 事前分布を全体平均として平滑化
                df[f'{prefix}_win_rate_smooth'] = (
                    cnt * rate + K * GLOBAL_WIN_RATE
                ) / (cnt + K)
                # 信頼度フラグ: 3レース以上のデータがある→信頼できる統計
                df[f'{prefix}_has_history'] = (cnt >= 3).astype(int)

        # ── D. 休養区分 ──────────────────────────────────────────────────────
        if 'days_since_last_race' in df.columns:
            # fix-C: NaN を fillna(0) せず、LightGBM が NaN を欠損枝として扱えるよう保持
            # 初出走(NaN) と 短期休養(0-1日) を誤同一視しない
            d = pd.to_numeric(df['days_since_last_race'], errors='coerce')  # NaN 保持
            df['rest_category'] = pd.cut(
                d,
                bins=[-1, 0, 21, 90, float('inf')],
                labels=[0, 1, 2, 3]
            ).astype('Float64')  # nullable float → NaN がそのまま NaN で残る

        # ── E. 马場変更フラグ ───────────────────────────────────────────────
        # prev_race_surface: 山・ダート など の日本語文字列
        # ── E. 馬場変更フラグ ──────────────────────────────────────────────
        # is_surface_change の生成は _fill_missing_values 内（ラベルエンコード前）で実施済み
        # ここでは不要な文字列列のクリーンアップのみ行う
        for _col in ['prev_race_surface', 'prev2_race_surface']:
            if _col in df.columns:
                df = df.drop(columns=[_col], errors='ignore')

        return df

    def _process_date_column(self, df: pd.DataFrame, col: str, prefix: str = 'date') -> pd.DataFrame:
        """日付カラムを数値特徴量に変換"""
        if col not in df.columns:
            return df
        
        try:
            df[col] = pd.to_datetime(df[col], errors='coerce')
            df[f'{prefix}_year'] = df[col].dt.year
            df[f'{prefix}_month'] = df[col].dt.month
            df[f'{prefix}_day'] = df[col].dt.day
            df[f'{prefix}_dayofweek'] = df[col].dt.dayofweek  # 0=月曜, 6=日曜
            
            # 元のカラムは削除
            df = df.drop(col, axis=1)
        except Exception as e:
            print(f"    警告: {col}の日付変換に失敗 - {e}")
        
        return df
    
    def get_feature_importance_groups(self) -> Dict[str, List[str]]:
        """特徴量を重要度グループに分類
        
        Returns:
            特徴量グループの辞書
        """
        return {
            'critical': [
                'odds', 'popularity', 'horse_weight',
                'jockey_course_win_rate', 'horse_distance_win_rate'
            ],
            'high': [
                'track_type_encoded', 'track_condition_encoded', 'distance',
                'corner_position_avg', 'last_corner_position', 'age'
            ],
            'medium': [
                'venue_encoded', 'weather_encoded', 'race_class_encoded',
                'days_since_last_race', 'last_3f_rank_normalized'
            ],
            'low': [
                'race_num', 'bracket_number', 'horse_number',
                'straight_length', 'inner_bias'
            ]
        }


def prepare_for_lightgbm_ultimate(df: pd.DataFrame, target_col: Optional[str] = None, 
                                   is_training: bool = True, 
                                   optimizer: Optional[LightGBMFeatureOptimizer] = None
                                   ) -> Tuple[pd.DataFrame, LightGBMFeatureOptimizer, List[str]]:
    """LightGBM用にデータを最適化する統合関数
    
    Args:
        df: 入力データフレーム
        target_col: 目的変数のカラム名
        is_training: 学習モードかどうか
        optimizer: 既存のOptimizer（推論時に使用）
    
    Returns:
        (最適化後のデータフレーム, Optimizer, カテゴリカル特徴量リスト)
    
    使用例:
        # 学習時
        df_train_opt, optimizer, cat_features = prepare_for_lightgbm_ultimate(
            df_train, target_col='win', is_training=True
        )
        
        # モデル学習
        import lightgbm as lgb
        X = df_train_opt.drop(['win', 'race_id', 'horse_id', ...], axis=1)
        y = df_train_opt['win']
        
        params = {
            'objective': 'binary',
            'categorical_feature': cat_features,
            'max_cat_to_onehot': 4,
        }
        train_data = lgb.Dataset(X, y, categorical_feature=cat_features)
        model = lgb.train(params, train_data)
        
        # 推論時
        df_test_opt, _, _ = prepare_for_lightgbm_ultimate(
            df_test, is_training=False, optimizer=optimizer
        )
    """
    if is_training:
        if optimizer is None:
            optimizer = LightGBMFeatureOptimizer()
        df_optimized, cat_features = optimizer.fit_transform(df, target_col)
    else:
        if optimizer is None:
            raise ValueError("推論時にはoptimizerを指定してください")
        df_optimized = optimizer.transform(df)
        cat_features = optimizer.categorical_features
    
    return df_optimized, optimizer, cat_features


# ===== 使用例 =====
if __name__ == "__main__":
    print("""
    ■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■
      LightGBM最適化: 全特徴量の前処理戦略
    ■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■
    
    【特徴量カテゴリと処理方法】
    
    1. 低カーディナリティ カテゴリカル（15種類）
       ┌─────────────────────────────────────────────┐
       │ 競馬場, 天候, 馬場状態, クラス, 性別, ペース │
       │ → Label Encoding + categorical_feature指定   │
       └─────────────────────────────────────────────┘
       
       処理後: venue='東京' → venue_encoded=0
       LightGBM側: categorical_feature=['venue_encoded', ...]
       
       メリット:
         ✓ 自動的に最適な分岐を見つける
         ✓ メモリ効率的
         ✓ カテゴリ間の順序関係を自動学習
    
    2. 高カーディナリティ カテゴリカル（3種類）
       ┌─────────────────────────────────────────────┐
       │ 騎手名, 調教師名, 馬名                       │
       │ → 統計特徴量化（勝率、平均着順など）        │
       └─────────────────────────────────────────────┘
       
       処理後: 
         jockey_name='C.ルメール' → 削除
         追加: jockey_win_rate=0.25, jockey_avg_finish=3.2
       
       メリット:
         ✓ 特徴量爆発を防ぐ
         ✓ 汎化性能向上（新人騎手にも対応）
         ✓ 情報量を保持
    
    3. 数値変数（30種類以上）
       ┌─────────────────────────────────────────────┐
       │ 馬番, 馬体重, 斤量, オッズ, 距離など         │
       │ → そのまま使用（スケーリング不要）          │
       └─────────────────────────────────────────────┘
       
       理由: LightGBMは決定木ベースなのでスケール不変
    
    4. バイナリ変数（6種類）
       ┌─────────────────────────────────────────────┐
       │ is_young, distance_increased, surface_changed │
       │ → そのまま使用（0/1エンコード済み）         │
       └─────────────────────────────────────────────┘
    
    5. リスト型変数（2種類）
       ┌─────────────────────────────────────────────┐
       │ corner_positions_list, past_performances      │
       │ → 統計値（平均、分散）に変換               │
       └─────────────────────────────────────────────┘
       
       処理後: [5,5,4,3] → corner_position_avg=4.25, variance=0.69
    
    6. ダミー変数（10種類以上）
       ┌─────────────────────────────────────────────┐
       │ sex_牡, pace_H, rest_short など              │
       │ → そのまま使用（get_dummies済み）           │
       └─────────────────────────────────────────────┘
    
    7. ID系変数（5種類）
       ┌─────────────────────────────────────────────┐
       │ race_id, horse_id, jockey_id など            │
       │ → 学習時には除外（統計計算には使用）        │
       └─────────────────────────────────────────────┘
    
    8. 日時変数（2種類）
       ┌─────────────────────────────────────────────┐
       │ date, birth_date                             │
       │ → 年/月/日/曜日に分解                       │
       └─────────────────────────────────────────────┘
       
       処理後: date='2023-05-01' → 
               date_year=2023, date_month=5, date_day=1, date_dayofweek=0
    
    9. 不要な変数（8種類）
       ┌─────────────────────────────────────────────┐
       │ time, margin, post_time, URL系               │
       │ → 削除                                       │
       └─────────────────────────────────────────────┘
    
    
    【使用方法】
    
    ```python
    from keiba_ai.lightgbm_feature_optimizer import prepare_for_lightgbm_ultimate
    import lightgbm as lgb
    
    # 1. データ最適化
    df_train_opt, optimizer, cat_features = prepare_for_lightgbm_ultimate(
        df_train,
        target_col='win',
        is_training=True
    )
    
    # 2. 学習データ準備
    exclude_cols = ['win', 'race_id', 'horse_id', 'jockey_id', 'trainer_id']
    X_train = df_train_opt.drop(exclude_cols, axis=1)
    y_train = df_train_opt['win']
    
    # 3. LightGBMモデル学習
    params = {
        'objective': 'binary',
        'metric': 'auc',
        'categorical_feature': cat_features,  # ← 重要！
        'max_cat_to_onehot': 4,
        'learning_rate': 0.05,
        'num_leaves': 31,
        'verbose': -1
    }
    
    train_data = lgb.Dataset(
        X_train, y_train,
        categorical_feature=cat_features  # ← ここでも指定
    )
    
    model = lgb.train(params, train_data, num_boost_round=100)
    
    # 4. 推論
    df_test_opt, _, _ = prepare_for_lightgbm_ultimate(
        df_test,
        is_training=False,
        optimizer=optimizer  # ← 学習時のoptimizerを使用
    )
    X_test = df_test_opt.drop(exclude_cols, axis=1, errors='ignore')
    predictions = model.predict(X_test)
    ```
    
    
    【期待される効果】
    
    ✅ メモリ使用量: 90%削減
       - ワンホット: 1000+ カラム → 最適化: 100カラム
    
    ✅ 学習速度: 5-10倍高速化
       - カテゴリカル特徴の効率的処理
    
    ✅ 予測精度: 2-5%向上
       - 過学習の抑制
       - カテゴリ間の関係性を自動学習
    
    ✅ 汎化性能: 大幅向上
       - 新規騎手/調教師への対応
       - 統計特徴による安定化
    
    ■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■
    """)
