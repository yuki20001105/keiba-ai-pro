# 特徴量分析レポート

- モデル  : `qwen3:4b`
- 生成日時: 2026-06-13 10:31

---

## 🏆 1. 特徴量重要度ランキング（TOP 20）

> Spearman・SHAP・Gain を統合した総合ランキング

| 順位 | 特徴量名 | 説明 | グループ | 評価 | Spearman | Gain | SHAP |
|---|---|---|---|---|---|---|---|
| 1 | `recent_form_weighted` | 近走成績加重平均（直近ほど高ウェイト） | ①馬能力 | S | -0.296 | 0.000 | 0.000 |
| 2 | `recent_form_5race` | 近5走着順の単純平均 | ①馬能力 | S | -0.291 | 0.000 | 0.000 |
| 3 | `horse_speed_rank_pct` | 同クラス内での速度パーセンタイル順位 | ①馬能力 | A | -0.190 | 0.000 | 0.000 |
| 4 | `horse_weight` | 馬体重(kg) | ⑪その他 | A | 0.066 | 0.000 | 0.000 |
| 5 | `top3_count_5` | 近5走で3着以内に入った回数 | ①馬能力 | A | 0.220 | 0.000 | 0.000 |
| 6 | `speed_vs_race_avg` | 前走速度 − レース平均速度の差 | ①馬能力 | A | 0.210 | 0.000 | 0.000 |
| 7 | `weight_vs_standard` | 馬体重 − 標準体重の差(kg) | ⑩クラス情報 | A | 0.044 | 0.000 | 0.000 |
| 8 | `prev_race_finish` | 前走着順 | ⑪その他 | A | -0.182 | 0.000 | 0.000 |
| 9 | `prev_speed_zscore` | 前走速度のZスコア（標準化） | ①馬能力 | B | 0.164 | 0.000 | 0.000 |
| 10 | `distance_change` | 前走からの距離変化(m)（正=距離延長） | ⑤距離適性 | B | -0.051 | 0.000 | 0.000 |
| 11 | `race_avg_prev_finish` | 同レース出走馬の前走平均着順 | ①馬能力 | B | 0.012 | 0.000 | 0.000 |
| 12 | `prev2_race_finish` | 2走前の着順 | ⑪その他 | B | -0.103 | 0.000 | 0.000 |
| 13 | `prev_speed_index` | 前走速度指数 | ①馬能力 | B | 0.101 | 0.000 | 0.000 |
| 14 | `win_count_5` | 近5走の勝利数 | ①馬能力 | B | 0.091 | 0.000 | 0.000 |
| 15 | `prev_race_distance` | 前走の距離(m) | ⑪その他 | B | 0.056 | 0.000 | 0.000 |
| 16 | `race_avg_prev_speed` | 同レース出走馬の前走平均速度 | ①馬能力 | B | 0.022 | 0.000 | 0.000 |
| 17 | `speed_best_5` | 近5走の最高速度指数 | ①馬能力 | B | 0.095 | 0.000 | 0.000 |
| 18 | `speed_avg_weighted` | 速度指数の加重平均（近走ほど高ウェイト） | ①馬能力 | B | 0.104 | 0.000 | 0.000 |
| 19 | `prev2_race_distance` | 2走前の距離(m) | ⑪その他 | B | 0.069 | 0.000 | 0.000 |
| 20 | `holding_just_speed` | 同開催・同距離での速度指数 | ⑪その他 | B | 0.072 | 0.000 | 0.000 |

---

## 🗑️ 2. 削除候補特徴量

| 特徴量名 | 説明 | 評価 | NaN率 | Gain | リーク |
|---|---|---|---|---|---|
| `class_rank_adj` | クラス調整後のランク | C | 7.9% | 0.0000 | MEDIUM |
| `prev5_race_time` | 5走前のタイム（秒） | C | 42.0% | 0.0000 | LOW |
| `race_avg_prev_finish_is_missing` | 前走平均着順の欠損フラグ | C | 0.0% | 0.0000 | MEDIUM |
| `prev5_race_finish` | 5走前の着順 | C | 42.0% | 0.0000 | MEDIUM |
| `holding_middle_l3f` | 同開催・中距離の上がり3ハロン | C | 69.2% | 0.0000 | LOW |
| `holding_middle_finish` | 同開催・中距離の着順 | C | 69.2% | 0.0000 | MEDIUM |
| `holding_just_l3f` | 同開催・同距離の上がり3ハロン | C | 77.1% | 0.0000 | LOW |
| `holding_long_l3f` | 同開催・長距離の上がり3ハロン | C | 79.8% | 0.0000 | LOW |
| `holding_short_l3f` | 同開催・短距離の上がり3ハロン | C | 81.1% | 0.0000 | LOW |
| `holding_middle_babasa` | 同開催・中距離の馬場差 | C | 70.3% | 0.0000 | LOW |
| `holding_just_babasa` | 同開催・同距離の馬場差 | C | 78.1% | 0.0000 | LOW |
| `holding_short_babasa` | 同開催・短距離の馬場差 | C | 82.0% | 0.0000 | LOW |
| `holding_long_babasa` | 同開催・長距離の馬場差 | C | 80.8% | 0.0000 | LOW |
| `holding_long_finish` | 同開催・長距離の着順 | C | 79.8% | 0.0000 | MEDIUM |
| `holding_short_finish` | 同開催・短距離の着順 | C | 81.1% | 0.0000 | MEDIUM |

---

## ⚠️ 3. データリーク疑惑特徴量

| 特徴量名 | 説明 | リスク | Spearman | Gain | 対処 |
|---|---|---|---|---|---|
| `horse_speed_rank_pct` | 同クラス内での速度パーセンタイル順位 | MEDIUM | -0.190 | 0.000 | 本番運用前に除外検討 |
| `prev_race_finish` | 前走着順 | MEDIUM | -0.182 | 0.000 | 本番運用前に除外検討 |
| `race_avg_prev_finish` | 同レース出走馬の前走平均着順 | MEDIUM | 0.012 | 0.000 | 本番運用前に除外検討 |
| `prev2_race_finish` | 2走前の着順 | MEDIUM | -0.103 | 0.000 | 本番運用前に除外検討 |
| `prev3_race_finish` | 3走前の着順 | MEDIUM | -0.151 | 0.000 | 本番運用前に除外検討 |
| `holding_just_time_sec` | 同開催・同距離でのタイム（秒） | MEDIUM | 0.067 | 0.000 | 本番運用前に除外検討 |
| `holding_just_finish` | 同開催・同距離での着順 | MEDIUM | 0.052 | 0.000 | 本番運用前に除外検討 |
| `holding_just_time_rank` | 同開催・同距離でのタイムランク | MEDIUM | -0.047 | 0.000 | 本番運用前に除外検討 |
| `holding_just_finish_rank` | 同開催・同距離での着順ランク | MEDIUM | -0.054 | 0.000 | 本番運用前に除外検討 |
| `holding_just_finish_is_missing` | 同開催・同距離着順の欠損フラグ | MEDIUM | -0.067 | 0.000 | 本番運用前に除外検討 |

---

## 🔗 4. 多重共線性疑惑特徴量ペア（上位 15 件）

| 特徴量 A | 説明 A | 特徴量 B | 説明 B | 相関係数 | 対処 |
|---|---|---|---|---|---|
| `kai` | 開催回（第〇回） | `kai_num` | 開催回の数値 | 1.000 | 後者を削除推奨 |
| `day` | 開催日（曜日・連続開催日） | `day_num` | 開催日の数値 | 1.000 | 後者を削除推奨 |
| `holding_just_speed_is_missing` | 同開催・同距離速度の欠損フラグ | `holding_just_finish_is_missing` | 同開催・同距離着順の欠損フラグ | 1.000 | 後者を削除推奨 |
| `race_avg_prev_speed_is_missing` | 前走平均速度の欠損フラグ | `race_max_prev_speed_is_missing` | 前走最高速度の欠損フラグ | 1.000 | 後者を削除推奨 |
| `race_avg_prev_speed_is_missing` | 前走平均速度の欠損フラグ | `race_avg_prev_finish_is_missing` | 前走平均着順の欠損フラグ | 1.000 | 後者を削除推奨 |
| `holding_just_time_sec_is_missing` | 同開催・同距離タイムの欠損フラグ | `holding_just_speed_is_missing` | 同開催・同距離速度の欠損フラグ | 1.000 | 後者を削除推奨 |
| `has_just_data` | 同開催・同距離データの有無フラグ | `holding_just_finish_is_missing` | 同開催・同距離着順の欠損フラグ | -1.000 | 後者を削除推奨 |
| `has_just_data` | 同開催・同距離データの有無フラグ | `holding_just_speed_is_missing` | 同開催・同距離速度の欠損フラグ | -1.000 | 後者を削除推奨 |
| `holding_just_time_sec_is_missing` | 同開催・同距離タイムの欠損フラグ | `holding_just_finish_is_missing` | 同開催・同距離着順の欠損フラグ | 1.000 | 後者を削除推奨 |
| `has_just_data` | 同開催・同距離データの有無フラグ | `holding_just_time_sec_is_missing` | 同開催・同距離タイムの欠損フラグ | -1.000 | 後者を削除推奨 |
| `race_max_prev_speed_is_missing` | 前走最高速度の欠損フラグ | `race_avg_prev_finish_is_missing` | 前走平均着順の欠損フラグ | 1.000 | 後者を削除推奨 |
| `speed_avg_weighted` | 速度指数の加重平均（近走ほど高ウェイト） | `speed_best_2` | 近2走の最高速度指数 | 1.000 | 後者を削除推奨 |
| `race_avg_prev_speed` | 同レース出走馬の前走平均速度 | `race_max_prev_speed` | 同レース出走馬の前走最高速度 | 0.999 | 後者を削除推奨 |
| `holding_just_speed` | 同開催・同距離での速度指数 | `holding_just_finish_is_missing` | 同開催・同距離着順の欠損フラグ | -0.999 | 後者を削除推奨 |
| `holding_just_speed` | 同開催・同距離での速度指数 | `holding_just_speed_is_missing` | 同開催・同距離速度の欠損フラグ | -0.999 | 後者を削除推奨 |

---

## 🏇 5. クラス情報特徴量 / 📈 6. 人気依存の評価

### グループ別平均重要度

| グループ名 | 特徴量数 | 平均Spearman | Gain寄与率 |
|---|---|---|---|
| ①馬能力 | 19 | 0.108 | 84.8% |
| ⑪その他 | 75 | 0.039 | 11.0% |
| ⑤距離適性 | 1 | 0.051 | 2.1% |
| ⑩クラス情報 | 8 | 0.023 | 2.0% |
| ⑨オッズ | 1 | 0.025 | 0.2% |
| ⑦枠順 | 1 | 0.008 | 0.0% |

> **注意**: オッズ・人気系特徴量（`odds_rank_in_race` 等）は UNNECESSARY_COLUMNS に除外済み。
> クラス特徴量（`race_class_num`, `class_change`）は学習データに含まれており有効だが、
> 予測時に利用可能か事前確認が必要。

---

## 💰 7. ROI・回収率向上に有効な特徴量

### テストセット成績

| 指標 | 値 | 備考 |
|---|---|---|
| 単勝的中率（Top1→1着） | 32.6% | ランダム比較で有意 |
| 複勝的中率（Top1→3着内） | 92.6% | |
| Top3 包含率 | 57.5% | |
| ROI（理論単勝） | +64.0% | JRA 控除 −25% が実運用の損益分岐 |
| テストレース数 | 24,431 レース | |

### 穴馬検出に有効な上位特徴量

| 特徴量名 | 説明 | グループ | Spearman |
|---|---|---|---|
| `recent_form_weighted` | 近走成績加重平均（直近ほど高ウェイト） | ①馬能力 | -0.296 |
| `recent_form_5race` | 近5走着順の単純平均 | ①馬能力 | -0.291 |
| `horse_speed_rank_pct` | 同クラス内での速度パーセンタイル順位 | ①馬能力 | -0.190 |
| `top3_count_5` | 近5走で3着以内に入った回数 | ①馬能力 | 0.220 |
| `speed_vs_race_avg` | 前走速度 − レース平均速度の差 | ①馬能力 | 0.210 |
| `prev_speed_zscore` | 前走速度のZスコア（標準化） | ①馬能力 | 0.164 |

---

## 💡 8. 追加すべき特徴量の提案

| 特徴量案 | カテゴリ | 期待効果 | 実装方法 |
|---|---|---|---|
| `jockey_win_rate_by_distance` | 騎手×距離 | 距離別騎手適性 | 距離帯別騎手勝率を集計 |
| `trainer_course_win_rate` | 調教師×コース | コース適性精度向上 | 競馬場×厩舎の勝率 |
| `horse_weight_change_pct` | 馬体重変化率 | 調子・疲労度把握 | 前走比の体重変化率 |
| `speed_index_ma3` | 速度指数 | 近3走の実力推定 | speed_deviation の移動平均 |
| `rest_days_vs_optimal` | 休養日数 | 最適休養からの乖離 | 厩舎別平均休養日数との差分 |
| `age_distance_interaction` | 年齢×距離 | 成長に応じた距離適性 | 年齢・距離の交互作用特徴量 |