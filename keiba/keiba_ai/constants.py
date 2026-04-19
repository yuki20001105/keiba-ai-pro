"""keiba_ai 共通定数
==================
FUTURE_FIELDS, UNNECESSARY_COLUMNS, ID_COLUMNS, COLUMN_ALIASES を一元管理し、
各モジュールはここからインポートする。

変更履歴:
  - 2026-04-11: 初版。lightgbm_feature_optimizer / routers/train / routers/predict
                に散在していた 5 つのリストを統合。
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# 未来情報フィールド（当該レース結果 → 予測前には存在しない）
# ---------------------------------------------------------------------------
# lightgbm_feature_optimizer の FUTURE_INFO_BLACKLIST と
# routers/train.py の TRAIN_POST_RACE_DROP と
# routers/predict.py の POST_RACE_FIELDS を統合
FUTURE_FIELDS: frozenset = frozenset({
    # 走破タイム系
    "time_seconds",
    "finish_time",
    # 上がり3F系
    "last_3f",
    "last_3f_time",
    "last_3f_rank",
    "last_3f_rank_normalized",
    # コーナー通過
    "corner_1",
    "corner_2",
    "corner_3",
    "corner_4",
    "corner_positions",
    "corner_positions_list",
    "corner_position_avg",
    "corner_position_variance",
    "last_corner_position",
    "position_change",
    # 最終結果
    "margin",
    "prize_money",
    "finish",
    "finish_position",
    "actual_finish",
})

# ---------------------------------------------------------------------------
# ID列（統計計算では使うが学習入力からは除く）
# ---------------------------------------------------------------------------
ID_COLUMNS: frozenset = frozenset({
    "race_id",
    "horse_id",
    "jockey_id",
    "trainer_id",
    "owner_id",
})

# ---------------------------------------------------------------------------
# 列エイリアス（DBロード後・推論前に適用するリネームマッピング）
# ---------------------------------------------------------------------------
# db_ultimate_loader.py と predict.py の _col_map を統一
COLUMN_ALIASES: dict[str, str] = {
    "finish_position": "finish",
    "finish_time": "time",
    "track_type": "surface",
    "last_3f": "last_3f_time",
    "weight_kg": "horse_weight",
}

# ---------------------------------------------------------------------------
# 不要列（学習・推論の両フェーズで削除）
# ---------------------------------------------------------------------------
# lightgbm_feature_optimizer.py の fit_transform / transform 内でそれぞれ
# 定義されていた 2 種類の unnecessary_cols リストを統合。
# FUTURE_FIELDS に含まれる列も一部重複して含む（ベルト+サスペンダー方式）。
# 高カーディナリティ文字列列（jockey_name 等）は lightgbm_feature_optimizer の
# セクション 2 で明示的に処理するためここには含まない。
UNNECESSARY_COLUMNS: tuple[str, ...] = (
    # URL / タイムスタンプ
    "post_time",
    "result_url",
    "horse_url",
    "jockey_url",
    "trainer_url",
    # 走破タイム文字列（time_seconds は FUTURE_FIELDS と重複）
    "time",
    "finish_time",
    # 着差・賞金（FUTURE_FIELDS と重複）
    "margin",
    "last_3f",
    "prize_money",
    "finish",
    "finish_position",
    # 馬詳細文字列（予測に不要）
    "owner_name",
    "horse_owner",
    "horse_breeder",
    "horse_breeding_farm",
    "horse_birth_date",
    "horse_coat_color",
    "surface_ja",
    "surface_en",
    # 変換済み・不要カラム
    "sex_age",
    "race_date",
    "created_at",
    "weight",
    "corner_positions",
    "id",
    "prev_race_venue",
    "prev2_race_venue",
    "prev_race_date",
    "prev2_race_date",
    "race_name",
    # リーク（当該レース結果 — FUTURE_FIELDS と重複）
    "time_seconds",
    "corner_1",
    "corner_2",
    "corner_3",
    "corner_4",
    "corner_position_avg",
    "corner_position_variance",
    "last_corner_position",
    "position_change",
    "last_3f_rank",
    "last_3f_rank_normalized",
    "last_3f_time",
    # 極端クラス不均衡（99% 以上が同一値）
    "horse_distance_win_rate",
    "horse_distance_avg_finish",
    "distance_increased",
    "distance_decreased",
    # 重複列（同一情報の二重表現）
    "weight_kg",
    "weight_change",
    "jockey_weight",
    "n_horses",
    "prev_race_time",
    "track_type_x",
    "track_type_y",
    # 配当情報（78% 超欠損）
    "tansho_payout",
    "sanrentan_payout",
    "fukusho_min_payout",
    "fukusho_max_payout",
    "tansho_payout_log",
    "sanrentan_payout_log",
    "sanrentan_z_in_races",
    "tansho_implied_prob",
    "tansho_payout_is_missing",
    "sanrentan_payout_is_missing",
    # prev_race_surface 未収録 → 常に 0 になるフラグ
    "is_surface_change",
    # ──────────────────────────────────────────────────────────────────────────
    # 冗長オッズ変換（odds/implied_prob で十分。LightGBM 木モデルは単調変換に不変）
    # ──────────────────────────────────────────────────────────────────────────
    "implied_prob_norm",         # = implied_prob をレース内正規化 ≈ popularity と高相関
    "odds_rank_in_race",         # ≈ popularity の rank 表現（情報重複）
    # ──────────────────────────────────────────────────────────────────────────
    # スクレイプ時点の馬プロフィール統計（再スクレイプ後に未来情報混入リスク）
    # horse.py が db_prof_table「通算成績」から取得 → スクレイプ日時点の通算値
    # 歴史的レースを現在（2026年）に再スクレイプすると 10年先の成績が混入する
    # ──────────────────────────────────────────────────────────────────────────
    "horse_win_rate",            # 通算勝率（プロフィールの現在値 → 過去レースに未来混入）
    "horse_win_rate_is_missing", # ↑の欠損フラグ（削除対象列の付随フラグ）
    "horse_total_wins",          # 通算勝利数（プロフィールの現在値）
    "horse_total_runs",          # 通算出走数（プロフィールの現在値）
    "horse_total_prize_money",   # 通算獲得賞金（プロフィールの現在値）
    # ──────────────────────────────────────────────────────────────────────────
    # 欠損フラグ系（Temporal holdout で gain=0%。欠損が実質的に存在しないため無効）
    # ──────────────────────────────────────────────────────────────────────────
    "popularity_is_missing",            # odds/popularityは常に取得できるため不要
    "odds_is_missing",
    "prev_race_finish_is_missing",      # 前走欠損は first_race フラグで代替
    "prev2_race_finish_is_missing",
    "prev_race_distance_is_missing",
    "prev2_race_distance_is_missing",
    "prev_race_time_is_missing",
    "prev2_race_time_is_missing",
    "prev2_race_weight_is_missing",
    "prev_race_weight_is_missing",
    "prev_speed_index_is_missing",
    "prev_speed_zscore_is_missing",
    "race_class_num_is_missing",
    "days_since_last_race_is_missing",
    # ──────────────────────────────────────────────────────────────────────────
    # 年齢区分フラグ（gain=0%。age/running_style で既に捉えられている）
    # ──────────────────────────────────────────────────────────────────────────
    "is_young",      # age < 4 と等価
    "is_prime",      # age 4-6 と等価
    "is_veteran",    # age > 6 と等価
    # ──────────────────────────────────────────────────────────────────────────
    # コース内枠順バイアス（gain=0%。venue×distance 統計で代替可能）
    # ──────────────────────────────────────────────────────────────────────────
    "inner_advantage",   # 内枠有利フラグ
    "inner_bias",        # 内枠バイアス値
    # ──────────────────────────────────────────────────────────────────────────
    # 休養期間カテゴリ文字列（rest_short/normal/long/very_long バイナリで代替済み）
    # ──────────────────────────────────────────────────────────────────────────
    "rest_category",     # "short"/"normal"/"long"/"very_long" 文字列
    # ──────────────────────────────────────────────────────────────────────────
    # ITR-09: split=0 の完全未使用特徴量を除去（特徴量重要度分析 2026-04-19 に基づく）
    # LightGBM がスプリットに一切使用しなかった特徴量。モデルに影響なく削除可能。
    # ──────────────────────────────────────────────────────────────────────────
    # 休養期間バイナリフラグ（rest_category の分解版だが全てsplit=0）
    "rest_short",        # split=0
    "rest_normal",       # split=0
    "rest_long",         # split=0
    "rest_very_long",    # split=0
    # 季節（sin_date/cos_date で既にエンコード済みのため冗長）
    "season",            # split=0 (sin_date が 0.01% gain を持つ)
    # 年齢（running_style_num/prev_race_finish で間接的に捉えられている）
    "age",               # split=0
    # 前々走速度偏差欠損フラグ（prev2_speed_zscore_is_missing と完全重複）
    "prev2_speed_zscore_is_missing",  # split=0, r=1.000 with prev2_speed_index_is_missing（既削除）
    # ──────────────────────────────────────────────────────────────────────────
    # 高相関特徴量の除去（プロファイリング解析 2026-04-16 に基づく）
    # Pearson 相関係数 > 0.90 のペアで、より情報量の少ない側を削除する
    # ──────────────────────────────────────────────────────────────────────────
    # 馬体重グループ (horse_weight との相関 r > 0.94)
    # horse_weight + horse_weight_change で十分。prev 体重は冗長。
    "prev_race_weight",          # r=0.947 with horse_weight
    "prev2_race_weight",         # r=0.948 with horse_weight
    "prev_race_weight_is_missing",  # ↑削除に伴い不要
    "prev2_race_weight_is_missing", # ↑削除に伴い不要
    # 斤量 (burden_weight との相関 r=0.970 with horse_weight)
    # 特徴量重要度 Top30 圏外かつ horse_weight と共線性が高い
    "burden_weight",             # r=0.970 with horse_weight
    # 前走タイム（prev_race_distance との相関 r=0.978）
    # prev_speed_index = prev_race_distance / prev_race_time_seconds で代替済み
    "prev_race_time_seconds",    # r=0.978 with prev_race_distance
    # 前々走タイム（prev2_race_distance との相関 r=0.980）
    # prev2_speed_index = prev2_race_distance / prev2_race_time で代替（feature_engineering で生成）
    "prev2_race_time",           # r=0.980 with prev2_race_distance
    "prev2_race_time_is_missing",   # ↑削除に伴い不要
    # ──────────────────────────────────────────────────────────────────────────
    # ITR-02: gain=0% 特徴量の除去（ITR-01モデル解析 2026-04-17 に基づく）
    # LightGBM が一切使用しなかった特徴量＝データ不足またはより良い代替がある
    # ──────────────────────────────────────────────────────────────────────────
    # 【枠番】gate_win_rate (0.33%) が枠番バイアスを既に捉えている
    "bracket_number",
    # 【直線距離】コース特性として会場×距離で代替可能。0%ゲイン。
    "straight_length",
    # 【馬の条件別成績】2021-2023 データ欠落により expanding window が疎→ほぼ全て NaN
    "horse_surface_win_rate",
    "horse_surface_races",
    "horse_dist_band_win_rate",
    "horse_dist_band_races",
    "horse_venue_win_rate",
    "horse_venue_races",
    "horse_distance_races",
    # 以下は feature_engineering.py で生成されるが 0% ゲイン
    "horse_venue_surface_win_rate",
    "horse_venue_surface_races",
    "horse_dist_surface_win_rate",
    "horse_dist_surface_races",
    # 【近N走統計】データギャップで疎。past3(0.01%)のみ有効、5/10走は 0%。
    "past5_avg_finish",
    "past10_avg_finish",
    "past3_win_rate",
    "past5_win_rate",
    "past3_avg_last3f_rank",
    "past5_avg_last3f_time",
    "past3_avg_last3f_time",
    # 【体重トレンド】データギャップで 0%ゲイン
    "past_5_weight_slope",
    "past_5_weight_avg_change",
    # 【初出走フラグ】prev_race_finish の欠損パターンから model が推測可能
    "is_first_race",
    # ──────────────────────────────────────────────────────────────────────────
    # ITR-04: 高相関ペアの低重要度側を除去（ITR-02 profiling HTML の相関マトリクス解析）
    # ──────────────────────────────────────────────────────────────────────────
    # 騎手・調教師統計の冗長ペア（相関 r > 0.84）
    "fe_trainer_win_rate",          # r=0.945 with trainer_recent30_win_rate（低 gain 側）
    "jt_combo_win_rate",            # r=0.846 with jt_combo_win_rate_smooth（raw → smooth で代替）
    "trainer_place_rate_top2",      # r=0.848 with trainer_show_rate（近い gain → show_rate を優先）
    "jockey_place_rate_top2",       # r=0.881 with jockey_show_rate（低 gain 0.031% vs 0.159%）
    # 追加 _is_missing フラグ（削除列に伴い不要）
    "trainer_place_rate_top2_is_missing",
    "jockey_place_rate_top2_is_missing",
    # コース形状・馬場種別エンコード（ITR-01 で 0%ゲイン。venue_code/field_condition で代替）
    "surface_encoded",              # track_type_encoded とほぼ同一、0%ゲイン
    "track_type_encoded",           # surface_encoded と重複、0%ゲイン
    "course_direction_encoded",     # distance との高相関・0%ゲイン
    "corner_radius_encoded",        # 0%ゲイン、コース形状はvenue_codeで代替可能
    # track_type_detailed_encoded: 列が存在しない場合が多いが念のため追加
    "track_type_detailed_encoded",
    # 欠損フラグ重複（r=1.000）: prev2_speed_index_is_missing と同一内容
    "prev2_speed_index_is_missing", # prev2_speed_zscore_is_missing と r=1.000（完全重複）
    # jt_combo の冗長列（race count は jt_combo_win_rate_smooth に内包済み）
    "jt_combo_races",               # jt_combo_win_rate_smooth のベイズ平滑に使用済み、直接追加は不要
    # 馬場状態の重複: weather_encoded は field_condition_encoded で代替可能
    "weather_encoded",              # field_condition_encoded（良/稍重/重/不良）で十分
    # race_class_num と race_class_encoded は同一情報（クラス順序）
    "race_class_num",               # race_class_encoded（0.83%ゲイン）と冗長
)
