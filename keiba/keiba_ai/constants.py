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
    # コーナー通過（生データ）
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
    # コーナー通過派生特徴量（当該レース結果から生成 → post-race リーク）
    # _fe_corner_position() が corner_1/2/3/4 から生成する特徴量。
    # 歴史的脚質は running_style_mean_5 / running_style_std_5 (_fe_history) で代替。
    "corner_first",
    "corner_last",
    "corner_gain",
    "running_style_code",
    # 上記の欠損フラグ（リーク特徴量の付随フラグ）
    "corner_first_is_missing",
    "corner_last_is_missing",
    "corner_gain_is_missing",
    "running_style_code_is_missing",
    # タイム指数（netkeiba の結果ページから取得 → レース後に確定する値）
    "time_index",
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
    "prev3_race_venue",
    "prev4_race_venue",
    "prev5_race_venue",
    "prev_race_date",
    "prev2_race_date",
    "prev3_race_date",
    "prev4_race_date",
    "prev5_race_date",
    "race_name",
    # 持ちタイム（非数値フィールド）

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
    # オッズ系列の除外（speed_deviation モデルを市場予測に依存させないため）
    # odds/log_odds は speed_deviation gain の 84.9% を占めており、
    # モデルが馬の実力特徴量（スピード指数等）を学習できていなかった。
    # 除外することで prev_speed_index / speed_vs_race_avg 等に学習が向かう。
    # ──────────────────────────────────────────────────────────────────────────
    "odds",                      # gain 61.5%: 市場予測に過依存（除外）
    "log_odds",                  # gain 23.3%: odds の対数変換（同様に除外）
    "implied_prob",              # = 1/odds の変換（情報重複）
    "popularity",                # 人気順（odds と高相関）
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
    # 馬場状態の重複: weather_encoded は field_condition_encoded で代替可能、とされていたが
    # 同じ「良」でも晴・乾燥と曇・湿度高めは異なるため weather は独立情報として復活
    # "weather_encoded",              # 復活: field_condition と相関あるが独立シグナル
    # race_class_num と race_class_encoded は同一情報（クラス順序）
    "race_class_num",               # race_class_encoded（0.83%ゲイン）と冗長
    # ──────────────────────────────────────────────────────────────────────────
    # ITR-10: 高相関特徴量の除去（ITR-08 profiling 相関マトリクス解析 2026-04-26 に基づく）
    # speed_deviation ターゲットの精度向上のため、多重共線性を持つ列を整理する。
    # ──────────────────────────────────────────────────────────────────────────
    # オッズ関連の冗長ペア（odds/log_odds を残し、その派生を除去）
    "odds_z_in_race",       # r=0.861 with popularity_normalized, r=0.793 with odds → log_odds で代替済み
    "popularity_normalized", # r=0.862 with odds_z_in_race, r=0.715 with implied_prob → log_odds が優先
    "top3_probability",     # r=0.681 (inverse) with market_entropy → market_entropy が情報量大
    # ──────────────────────────────────────────────────────────────────────────
    # ITR-11: implied_prob / popularity の除去（2026-05-08 市場情報重複解消）
    # odds(53%) + implied_prob(21%) + popularity(4.8%) で市場 Gain 74%超 → log_odds 1列に統一
    # implied_prob = 1/odds（単調変換。LightGBM 木モデルには同一情報）
    # popularity   = rank(odds)（順序情報。odds から自明）
    # log_odds = log1p(odds) を唯一の市場シグナルとして使用
    # ──────────────────────────────────────────────────────────────────────────
    "implied_prob",         # = 1/odds → log_odds に統一
    "popularity",           # = rank(odds) → odds から自明
    # 前々走スピード指標（prev_speed_index / speed_index_change で情報を保持済み）
    # speed_index_change = prev_speed_index - prev2_speed_index として ITR-04 で生成済み
    # → prev2 を除去しても speed_index_change が変化トレンドを捉えている
    "prev2_speed_index",    # r=0.813 with prev_speed_index → speed_index_change で差分を保持
    "prev2_speed_zscore",   # r=0.582 with prev_speed_zscore, r=0.676 with prev2_speed_index
    # 騎手・調教師統計の冗長ペア（直近30日レートの方が新鮮な情報を含む）
    "jockey_show_rate",     # r=0.697 with jockey_recent30_win_rate → jockey_recent30_win_rate を優先
    "trainer_show_rate",    # r=0.630 with trainer_recent30_win_rate → trainer_recent30_win_rate を優先
    # ──────────────────────────────────────────────────────────────────────────
    # 重複・問題カラムの整理（2026-05-09）
    # ──────────────────────────────────────────────────────────────────────────
    # [1] データソースフラグ（出馬表スクレイプ時のみ True → 学習データでは常に NaN）
    "_shutuba",             # 推論/学習間で値が非対称 → 情報なし
    # [2] 性別の3重表現 → sex_code(-1/0/1) に一本化
    "sex",                  # 生文字列。sex_code で代替済み
    "sex_セ",               # get_dummies one-hot。sex_code で代替済み
    "sex_牝",               # get_dummies one-hot。sex_code で代替済み
    "sex_牡",               # get_dummies one-hot。sex_code で代替済み
    # [3] sf_index_last ≈ prev_speed_index（前走スピード指数の2重表現）
    # speed_figures テーブルのカバレッジは ~10%（3086行/29778行）
    # prev_speed_index は prev_race_distance/time から常時計算可能 → こちらを優先
    "sf_index_last",              # prev_speed_index と重複、かつカバレッジ10%未満
    "sf_index_last_is_missing",   # ↑削除に伴い不要
    # [4] running_style の2重表現 → running_style_num(数値) に一本化
    "running_style",        # 生文字列('逃げ'/'先行'/...)。running_style_num で代替済み
    # [5] 騎手・調教師の生文字列（高カーディナリティ、統計量で代替）
    "jockey_name",          # 生文字列。jockey_recent30_win_rate 等の統計量を使用
    "trainer_name",         # 生文字列。trainer_recent30_win_rate 等の統計量を使用
    # [6] weight_diff はDBに存在せず（DB内は weight_change）、推論時も [A-6] で無視済み
    "weight_diff",          # DBに存在しない。推論時は weight_change を使用
    # [7] 前走クラス文字列（prev_race_class_num として数値化済み）
    "prev_race_class",      # 生文字列。prev_race_class_num で代替済み
    # [8] 前走馬場文字列（object 型。surface_encoded で現レース馬場は既に捉えている）
    # prev_race_surface / prev2_race_surface は low_card_categorical で LabelEncoding 可能だが
    # prev3〜5 のカバレッジが低く、surface_changed フラグで代替可能なため除外
    "prev_race_surface",    # 生文字列（"芝"/"ダ"）
    "prev2_race_surface",   # 生文字列
    "prev3_race_surface",   # 生文字列
    "prev4_race_surface",   # 生文字列
    "prev5_race_surface",   # 生文字列
    # ──────────────────────────────────────────────────────────────────────────
    # 完全一致・完全逆相関ペアの除去（correlation_analysis 2026-06-14 に基づく）
    # high_correlation.csv で |r| = 1.0 が確認された冗長列を削除する。
    # ──────────────────────────────────────────────────────────────────────────
    "kai_num",                         # kai と r=1.0（完全一致）
    "day_num",                         # day と r=1.0（完全一致）
    "race_avg_prev_finish_is_missing", # race_max_prev_speed_is_missing と r=1.0
    "race_max_prev_speed_is_missing",  # race_avg_prev_speed_is_missing と r=1.0
    "holding_just_finish_is_missing",  # has_just_data と r=-1.0（完全逆相関）
    "holding_just_speed_is_missing",   # has_just_data / holding_just_time_sec_is_missing と r=1.0
    "holding_just_time_sec_is_missing",# holding_just_speed_is_missing と r=1.0
    "last_training_time_3f_is_missing",# has_training_data と r=-1.0（完全逆相関）
)
