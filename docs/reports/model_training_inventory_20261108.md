# 現在のモデル作成・特徴量・パラメータ総覧

調査日: 2026年11月8日。保存済みモデル、公開台帳、現在の学習コード、研究成果物を読み取り専用で確認した。**この調査では学習・モデル切替・DB更新をしていない。**

## 1. 最初に押さえる違い

| 対象 | 現在確認できる内容 |
|---|---|
| 既定の保存モデル | **46特徴量の速度偏差回帰**。重みのTRAINは2016/1/5〜2022/6/4 |
| そのモデルの校正・評価 | CALは2022/6/5〜2024/8/10、TESTは2024/8/11〜2026/9/27 |
| 直近の拡張特徴量研究 | 13構成・異なる入力141項目を比較し、**search101**を選定。研究用で、本番には切り替えていない |
| 現在の画面から新規学習 | 初期選択は「候補46項目」。現在のルータは**競馬場・馬場・天候も加えた速度基準④の試験モデル**を作る |
| 同じ46項目でも異なる点 | 現在の既定モデルの基準①と、新規作成コードの基準④は別。`feature_pipeline`だけでなく保存`schema`・教師値の契約を見る |
| 保存上の`metrics.auc` | この速度回帰モデルでは**順位相関**。通常の分類AUCではない |
| 保存上の`metrics.logloss` | このモデルでは**速度RMSE（m/s）**。勝率NLLとは別 |
| 利益の証明 | 勝率校正の合格、過去の回収率、実際に購入可能な期待値はそれぞれ別。現在モデルの市場優位・利益は未証明 |

「データが2026年まである」「2026年まで重みを学習した」「2026年を評価した」は同義ではない。画面で個別モデルを選ぶ場合は既定ポインタとは異なるモデルが使われ得る。この報告はブラウザの個別選択状態を確認したものではない。

### 添付資料と調査範囲

| 資料 | 内容 |
|---|---|
| [機械可読JSON](model_training_inventory_20261108.json) | ローカルモデル21個＋選定済み研究モデル2個。保存メタデータ、実特徴量順、明示パラメータ、ネイティブ解決済みパラメータ、校正、基準統計、ソースSHA |
| [特徴量CSV](model_training_inventory_20261108_features.csv) | 17プロファイルの全入力名・順序・型・定義。既定46項目はgain/splitも収録。Excel向けUTF-8 BOM付き |
| [拡張研究の詳細HTML](..\..\reports\generated\feature-selection\20261107-expanded-v1\report.html) | 13構成の比較、診断、選定結果 |
| [拡張研究の全特徴量分析](..\..\reports\generated\feature-selection\20261107-expanded-v1\all-feature-analysis.html) | 追加特徴量・既存カタログとの対応 |

中間fold・試行モデルをすべて再評価した報告ではない。別途、旧`.pkl`と主要研究ディレクトリの未展開モデルをJSONの`other_artifact_filename_catalog`にファイル名・サイズで一覧化した。対象外の試行結果は各研究レポートを参照する。モデルの全木・重み、個々の馬の履歴、大量のカテゴリ実体、個人情報は転載していない。大きな辞書は件数のみを記録する。欠けた保存情報はファイル名やAPI初期値から補完していない。

## 2. 既定モデルの実体

| 項目 | 保存・実測値 |
|---|---|
| ポインタ | `python-api\models\.active_model.json` |
| モデルID | `model_speed_deviation_lightgbm_dbshared_20160105_20260927_20261012_2145_44322916` |
| ファイル | 上記IDの`.joblib`、`python-api\models`内 |
| 作成時刻 | 2026-10-12T21:45:44.322916+09:00 |
| ファイルサイズ | 645,566 bytes |
| SHA-256 | `9be2396e49f06db80bc4d556a61c7366bd6fe4b9beb7ad6e14a6444ac43a4966` |
| モデル型 | `lightgbm.Booster` |
| 保存schema | `local-db-shared-optimized-model-v1` |
| 特徴量pipeline | `local-db-shared-optimized-v1` |
| 特徴量profile | `db-shared-46-without-past5-top3-v1` |
| 教師値 | `speed_deviation` |
| 入力 | 数値40＋カテゴリ6＝46。Boosterの実入力順と保存列順が一致 |
| 木の数 | 200 |
| TRAIN行数・レース数 | 275,294行・19,559レース |
| 公開状態 | 公開台帳の`published`＋ジョブ`completed`＋成果物SHA一致を確認 |
| 校正器 | `RaceTemperatureCalibrator`、温度`0.2551041300244362` |

### データの選定と時系列

保存監査上の最終対象集団は**32,625レース・455,229行**、2016/1/5〜2026/9/27。これはSQLite全体の行数ではない。

対象期間の資格判定は35,811レース・501,420行を入力とし、判定後35,744レース・497,147行。その後、完全な対象レース集団として32,625レースを保持した。不完全レースの追加除外は3,119レース。理由別カウンタは重複し得るため、その単純合計を除外総数にしてはいけない。

主データはSQLiteの`races_ultimate / race_results_ultimate`。Supabaseの認証・購入記録を教師データとして混ぜたものではない。適格な結果・関連過去レースから特徴量を生成し、対象結果を入力から除外して固定列へ投影、TRAIN-only Encoder、回帰、独立CAL校正へ進む。教師/評価の結果列とモデル入力は別に保持する。

履歴用集団は対象集団と別で、2013年開始の観測履歴を含む。保存監査は41,324レース・581,931行を記録するが、**全生涯履歴の完全性は未検証**。対象月にデータが存在することも、全開催・全出走馬を網羅した証明ではない。

| 用途 | 最初の日 | 最後の日 | 開催日数 | レース | 行 |
|---|---|---|---:|---:|---:|
| TRAIN | 2016/1/5 | 2022/6/4 | 698 | 19,559 | 275,294 |
| CAL | 2022/6/5 | 2024/8/10 | 233 | 6,637 | 91,210 |
| TEST | 2024/8/11 | 2026/9/27 | 233 | 6,429 | 88,725 |
| TRAIN内・候補比較の学習 | 2016/1/5 | 2021/2/7 | 558 | 15,508 | 219,178 |
| TRAIN内・候補比較の検証 | 2021/2/13 | 2022/6/4 | 140 | 4,051 | 56,116 |

分割は開催日単位。初期設定でCAL20%・TEST20%・残りTRAIN約60%だが、**日数の比率であり行数の厳密な比率ではない**。同じ開催日をTRAIN/CAL/TESTにまたがせない。

## 3. 教師値・学習手順

既定保存モデルの教師値は、TRAIN全体から作った「芝/ダート×距離」の基準に対する速度の標準化値。

```text
v = distance / time_seconds                         # m/s
y = (v - TRAINグループ平均速度) / TRAINグループ標本標準偏差
予測速度 = 予測y × 保存グループ標準偏差 + 保存グループ平均
```

TRAINに適格30行以上あり、有限で正の標準偏差を持つグループを使用。成立しない場合はTRAIN全体基準へ戻す。実保存値は成立30グループ、全体平均16.290982365389787 m/s、全体標準偏差0.592480587228727 m/s、基準サンプル275,294行。

これは勝ち/負けの直接分類でも、一般的な「平均50・標準偏差10」の偏差値でもない。時計は教師値・評価用に使い、対象レースの時計を予測入力には使わない。基準の推定はTRAINだけで、CAL/TESTの時計で更新しない。共通fitの回帰損失は馬ごとの等重みで、別研究のレース均等重みやlegacyの重み設定とは区別する。現在レースの速度表示や別研究の365日基準と混同しない。

### 固定候補の比較

| 候補 | rounds | 葉数 | 最小葉行数 | L2 | TRAIN内検証・順位相関 | RMSE m/s |
|---|---:|---:|---:|---:|---:|---:|
| lean | 120 | 15 | 60 | 1.0 | 0.409501 | 0.227341 |
| standard（選定） | 200 | 31 | 30 | 1.0 | 0.429033 | 0.218724 |

TRAIN内の後ろ20%の日付で順位相関を比較し、最良との差0.002以内を候補に残す。その中で物理速度RMSE、rounds、候補IDの順に選定。選定後TRAIN全体で最終fit、CALだけで温度を推定、TESTで評価する。

**この経路はOptuna探索ではない。** `use_optimizer=true`は共通経路の実行条件だが、既定保存bundleの`optimizer`は`None`。`cv_folds=5`も共通経路で5回fold学習した意味ではなく、ここでは固定2候補＋TRAIN内の時系列検証である。

保存`cv_auc_mean=0.4290329773927865`は選定候補のTRAIN内検証順位相関。`cv_auc_std=0.0`は保存用の固定値で、5foldの実測ばらつきが0だったという意味ではない。

## 4. 実際のLightGBMパラメータ

保存Boosterの明示設定に加え、モデルテキストに保存された**ネイティブ解決済み121項目**をJSONの`resolved_native_parameters`に全件収録した。ライブラリの一般的な初期値を推測して書いたものではない。

| 項目 | 実値 | 意味 |
|---|---|---|
| objective / metric | `regression` / `rmse` | 速度偏差の回帰 |
| boosting | `gbdt` | 勾配ブースティング木 |
| learning_rate | 0.05 | 学習率 |
| num_iterations | 200 | 実際に200木 |
| num_leaves / max_depth | 31 / -1 | 最大葉数31、深さの明示上限なし |
| min_data_in_leaf | 30 | 最小葉行数 |
| min_sum_hessian_in_leaf | 0.001 | 最小葉Hessian |
| lambda_l1 / lambda_l2 | 0 / 1 | L1なし、L2あり |
| min_gain_to_split | 0 | 分割利得の下限 |
| max_bin | 63 | 数値binの上限 |
| feature_fraction | 1.0 | 木単位の列間引きなし |
| bagging_fraction / bagging_freq | 1.0 / 0 | 行baggingなし |
| early_stopping_round | 0 | 早期停止なし |
| seed | 42 | 基本乱数seed |
| data_random_seed / feature_fraction_seed / bagging_seed | 各42 | 明示した補助seed。他の解決済みseedはJSON参照 |
| deterministic / force_col_wise | true / true | 決定的設定、列方向 |
| device_type / num_threads | cpu / 4 | 最終モデルCPU、native4スレッド |
| categorical_column | `[10,11,12,13,14,15]` | 0始まりのカテゴリ列位置 |
| min_data_per_group | 100 | カテゴリ集団の下限 |
| max_cat_threshold / max_cat_to_onehot | 32 / 4 | カテゴリ分割設定 |
| cat_l2 / cat_smooth | 10 / 10 | カテゴリ正則化 |
| feature_pre_filter | 1 | LightGBM内部の前処理設定 |
| use_missing / zero_as_missing | 1 / 0 | 欠損を扱う。正当な0は欠損にしない |
| linear_tree / extra_trees | 0 / 0 | 線形葉・Extra Treesは使わない |
| verbosity | -1 | ログ抑制 |

特徴量選択処理は固定46列を維持し、定数/全欠損列も列契約から削除しない。実Encoderの`dropped_columns={}`。木がその列を使うことまで保証する設定ではない。

### 実行資源・時間・保存時環境

保存実行はCPU要求・CPU実行。候補比較は2並列、各native4スレッド、最終fitは4スレッド。現在のコードは資源状況で候補並列数を1/2、native予算を制御するため、常に同じ並列数になるとは限らない。UIのバッチ予測並列数とは別の設定。

| 保存時間 | 秒 |
|---|---:|
| 学習処理全体 | 92.294 |
| 特徴量準備 | 65.136 |
| 候補fit・校正・評価 | 27.136 |
| 準備内のDB読み込み | 29.887 |
| 準備内の特徴量生成 | 20.602 |
| 準備内のcache公開・検証 | 8.048 |

準備の内訳を準備合計に再加算しない。保存receiptはLightGBM4.7.0、NumPy2.4.6、pandas3.0.5、SciPy1.17.1、joblib1.6.0を記録している。Python実行ファイルとLightGBM DLLのSHAも保存されている。調査時のインストール版はJSONの別項目であり、保存時receiptと区別する。

## 5. 現在の46特徴量：全項目・実入力順

数値列に一律標準化を施しているわけではない。数値の不明値はNaN、カテゴリ辞書はTRAINだけで適合する。未来のカテゴリから辞書を作らない。

| 順 | 入力名 | 内容・単位 |
|---:|---|---|
| 1 | `safe_distance` | 対象レース距離 m |
| 2 | `safe_num_horses` | 出走頭数 |
| 3 | `safe_horse_number` | 馬番 |
| 4 | `safe_bracket_number` | 枠番 |
| 5 | `safe_age` | 馬齢 |
| 6 | `safe_burden_weight` | 斤量 kg |
| 7 | `safe_horse_weight` | 公開馬体重 kg |
| 8 | `safe_weight_change` | 公開馬体重増減 kg |
| 9 | `safe_month` | 対象日付の月、1〜12 |
| 10 | `safe_race_number` | race_id末尾2桁のレース番号 |
| 11 | `safe_surface` | 芝/ダート、カテゴリ |
| 12 | `safe_venue` | 競馬場、カテゴリ |
| 13 | `safe_sex` | 性別、カテゴリ |
| 14 | `safe_field_condition` | 馬場状態、カテゴリ |
| 15 | `safe_weather` | 天候、カテゴリ |
| 16 | `safe_race_class` | レースクラス、カテゴリ |
| 17 | `safe_prior_starts` | 対象日より前の、DBで観測できた適格出走数 |
| 18 | `safe_days_since_last` | 最後の観測適格出走からの日数 |
| 19 | `safe_prev_finish` | 観測上の前走着順 |
| 20 | `safe_prev_time_seconds` | 観測上の前走走破秒 |
| 21 | `safe_prev_distance` | 観測上の前走距離 m |
| 22 | `safe_prev_speed_mps` | 観測上の前走距離÷走破秒、m/s |
| 23 | `safe_prev2_finish` | 観測上の2走前着順 |
| 24 | `safe_prev2_time_seconds` | 観測上の2走前走破秒 |
| 25 | `safe_prev2_distance` | 観測上の2走前距離 m |
| 26 | `safe_prev2_speed_mps` | 観測上の2走前速度 m/s |
| 27 | `safe_past3_count` | 観測過去最大3走の件数 |
| 28 | `safe_past3_observed_coverage` | 上記件数÷3 |
| 29 | `safe_past3_avg_finish` | 観測過去最大3走の平均着順 |
| 30 | `safe_past3_avg_speed_mps` | 同・平均速度 m/s |
| 31 | `safe_past3_avg_last3f_time` | 同・取得できた上がり3F秒の平均 |
| 32 | `safe_past3_win_rate` | 同・1着数÷観測件数 |
| 33 | `safe_past3_top3_rate` | 同・3着以内数÷観測件数 |
| 34 | `safe_past5_count` | 観測過去最大5走の件数 |
| 35 | `safe_past5_observed_coverage` | 上記件数÷5 |
| 36 | `safe_past5_avg_finish` | 観測過去最大5走の平均着順 |
| 37 | `safe_past5_avg_speed_mps` | 同・平均速度 m/s |
| 38 | `safe_past5_avg_last3f_time` | 同・取得できた上がり3F秒の平均 |
| 39 | `safe_past5_win_rate` | 同・1着数÷観測件数 |
| 40 | `safe_past10_count` | 観測過去最大10走の件数 |
| 41 | `safe_past10_observed_coverage` | 上記件数÷10 |
| 42 | `safe_past10_avg_finish` | 観測過去最大10走の平均着順 |
| 43 | `safe_past10_avg_speed_mps` | 同・平均速度 m/s |
| 44 | `safe_past10_avg_last3f_time` | 同・取得できた上がり3F秒の平均 |
| 45 | `safe_past10_win_rate` | 同・1着数÷観測件数 |
| 46 | `safe_past10_top3_rate` | 同・3着以内数÷観測件数 |

baseline47との差は**`safe_past5_top3_rate`だけ**。46項目に騎手・調教師・父/母父・脚質・対象レースのオッズ/人気は入っていない。

### 履歴の意味と欠損

履歴dequeは最大10走、総観測出走数は別途累積。`prev`は真の直前出走とは限らず、取得・資格判定後に残った観測の直前出走である。

件数0とcoverage0は正当な0。履歴がなければ平均・率・前走値はNaNで、「0着」「平均速度0」とはしない。平均上がりは取得できた有限値だけで計算する。観測3走中に勝利なしの勝率0と、履歴自体がなく勝率不明は別。

同じ日の全対象行の特徴量を生成してから、その日の結果を履歴に追加する。厳密な条件は`source_race_date < target_race_date`。同日結果や当該レース結果を混ぜない。ただし、DBの現在値を再計算しただけでは公開当時の出馬表・馬体重・条件の存在時刻まで証明したことにならない。

### 保存木の重要度

| 特徴量 | gain全体に占める割合 | split回数 |
|---|---:|---:|
| `safe_race_class` | 40.001% | 307 |
| `safe_field_condition` | 9.428% | 807 |
| `safe_surface` | 7.306% | 519 |
| `safe_past10_avg_finish` | 7.252% | 78 |
| `safe_past10_top3_rate` | 6.253% | 71 |
| `safe_venue` | 5.095% | 967 |
| `safe_race_number` | 3.774% | 241 |
| `safe_distance` | 3.551% | 731 |
| `safe_month` | 2.788% | 515 |
| `safe_prev_finish` | 2.338% | 131 |

全46列のgain・splitはCSV/JSONに収録。全木のsplit合計6,000。これは**保存木の訓練利得**であり、SHAP、因果効果、削除時の評価差ではない。レース全体で共通のクラス・馬場などが速度水準を説明する利得と、同じレースの馬の並べ替えへの効果も別。

## 6. 保存モデルの評価・勝率校正

| TEST指標 | 実値 |
|---|---:|
| レース平均Spearman順位相関 | 0.4250359700 |
| 速度RMSE m/s | 0.2478514504 |
| 速度MAE m/s | 0.1845056646 |
| 速度R² | 0.8409169608 |
| 標準化教師値のRMSE | 0.7398999302 |
| 最高スコア馬の1着率 | 22.227407% |
| 勝率NLL | 2.2958822329 |
| Brier | 0.8693752178 |
| 最高確率馬のselection ECE | 0.0259154168 |
| selection absolute bias | 0.0190122373 |
| 一様確率のNLL / Brier | 2.5975036493 / 0.9230374600 |
| 回収率・人気馬との差 | 保存評価では未算出、null |

順位相関は各レースで`Spearman(予測速度スコア, -着順)`を計算して平均。定数ベクトルのレースは0扱い。物理速度RMSEは馬単位の重みで、別研究の「レース内平均→レース平均RMSE」とは集計が異なる。

勝率NLLは単独勝者の`−log(p)`のレース平均。Brierはレースごとの`Σ馬(p−実勝者フラグ)²`の平均で、馬ごとの二値Brierと尺度が異なる。selection ECEは最高確率馬の予測確率と実際の1着率を10binで比較するもので、全馬の任意選択に対する校正を一括保証する指標ではない。

順位/速度評価は6,429レース・88,725行。勝率評価は同着9レースを除いた**6,420レース・88,589行**。CALも同着7レースを除いて6,630レースで温度を適合。これらの件数差は欠落や二重計上と同義ではない。

| TEST年区分 | レース | 順位相関 | RMSE m/s | Top1 |
|---|---:|---:|---:|---:|
| 2024年・評価開始日以降 | 1,134 | 0.401105 | 0.236619 | 21.7813% |
| 2025年 | 3,009 | 0.424067 | 0.243941 | 22.5324% |
| 2026年・9/27まで | 2,286 | 0.438183 | 0.258156 | 22.0472% |

### 勝率への変換と合格条件

```text
p_i = exp(score_i / T) / Σ同じレースのj exp(score_j / T)
T = CALだけで多項勝者NLLを最小化した単一温度
```

温度探索範囲0.02〜10、log境界許容0.01。CAL100レース以上・10日以上、TEST200レース以上・20日以上、一様予測よりNLL/Brierが悪化しない、selection ECE≤0.08、absolute bias≤0.05、10binを要求する。既定保存モデルは全10条件に合格し、保存値は`calibrated=true / validated=true / eligible_for_ev=true`。

一方で`market_edge_verified=false`、`profitability_verified=false`、入力契約の`live_capture_timing_verified=false`。**確率の合格だけで実運用の期待値や購入を承認しない**。推論側ではさらに入力契約・オッズの根拠・戦略側の条件を確認する。

単勝の控除前リターン倍率としての基本式は`p×decimal_odds`、1円あたりの純期待収益は`p×decimal_odds−1`。無校正の参考スコア、実オッズの確定/途中状態、発走前に取得できた証拠の違いを無視して適用してはいけない。単勝確率からワイド・馬連・三連単の同時確率が検証済みになるわけでもない。

## 7. 46以外の特徴量と実際の研究

### 比較した13構成

同一開発集団7,317レース・101,783行で比較。DEV2020、DEV2021、DEV2022partial（2022/5/21まで）を使用。各foldのTRAINは直前年末まで。表の値はこの研究内の開発集団に限り比較可能。

| 構成 | 項目数 | 内容 | 開発順位相関 | 開発RMSE m/s |
|---|---:|---|---:|---:|
| baseline46 | 46 | 現行入力の再fit対照 | 0.422531 | 0.223094 |
| aptitude59 | 59 | 46＋馬の条件適性13 | 0.428431 | 0.222825 |
| entities58 | 58 | 46＋騎手/調教師/父/母父12 | 0.454499 | 0.220593 |
| canonical72 | 72 | baseline47＋適性13＋人物/血統12 | 0.460392 | 0.220255 |
| condition_weight60 | 60 | 46＋条件8＋体重6 | 0.423448 | 0.222750 |
| gate_style59 | 59 | 46＋枠4＋脚質/展開9 | 0.422292 | 0.223223 |
| pedigree53 | 53 | 46＋条件付き血統7 | 0.423333 | 0.222815 |
| context80 | 80 | 46＋context34 | 0.427803 | 0.222499 |
| **search101** | **101** | canonical72＋派生29 | **0.461509** | **0.220171** |
| combined106 | 106 | canonical72＋context34 | 0.458793 | 0.220459 |
| combined135 | 135 | search101＋context34 | 0.459658 | 0.220558 |
| observed_backoff78 | 78 | canonical72＋補完実施フラグ6 | 0.459666 | 0.220378 |
| observed_backoff141 | 141 | combined135＋補完実施フラグ6 | 0.460339 | 0.220386 |

入力の和集合は135既存項目＋6補完フラグ＝141。最多列の141構成ではなく101構成が選定された。「多いほど良い」ではない。13構成の全列・順序はCSV、選定条件・比較詳細はJSONの`expanded_feature_study`。

### canonical72に増える25項目

馬の適性13項目は、`horse_surface / horse_distance_band / horse_course`の各群について`starts / win_rate / top3_rate / avg_speed_mps`の4項目、計12＋`safe_distance_change`。コースは競馬場×芝/ダート、距離帯は1400m未満/1900m未満/2300m未満/それ以上。率・平均は厳密な過去観測だけで作る。

人物・血統12項目は`jockey / trainer / sire / damsire`ごとの`starts / win_rate / top3_rate`。人物ID・父母父名そのものをそのまま目的変数代わりに入れるのではなく、観測された過去実績へ変換する。canonical72は46ではなく**baseline47を土台**にしている。

### search101の追加29項目

| 群 | 数 | 定義 |
|---|---:|---|
| レース内相対値 | 16 | 前走/過去3/5/10走速度、過去3/5走着順、過去5走勝率、騎手勝率の8ソース×rank/delta |
| 傾向差 | 4 | 3走−10走速度、5走−10走速度、10走−3走着順、3走−10走勝率 |
| 欠損フラグ | 3 | 前走速度、2走前速度、騎手勝率の不明フラグ |
| 適応 | 2 | `log1p(休養日数)`、`(今回距離−前走距離)/前走距離` |
| 支持件数 | 4 | 馬の過去走数・騎手・調教師・コース走数の`log1p` |

相対値は観測馬2頭以上を要求。着順は符号を反転して「大きいほど良い」を揃える。平均同順位rankを0〜1に正規化し、全員同値なら0.5。deltaは同じ対象レースの観測平均との差。今回の着順・時計・オッズ・人気・払い戻しはソースにしない。

### context80の追加34項目

| 群 | 入力名（`ctx_`接頭辞） | 内容 |
|---|---|---|
| 条件適性8 | `condition_starts`, `condition_rank_mean`, `condition_speed_delta_mean`, `course_starts`, `course_rank_mean`, `course_speed_delta_mean`, `surface_changed`, `distance_change_ratio` | 同条件/コースの過去成績・レース内速度差、条件変更 |
| 体重6 | `weight_history_count5`, `weight_delta_last`, `weight_delta_mean5`, `weight_std5`, `weight_change_pct`, `weight_trend5` | 過去5走の記録体重と今回との差・ばらつき・傾向 |
| 血統7 | `sire_surface_starts`, `sire_surface_rank_mean`, `sire_condition_starts`, `sire_condition_rank_mean`, `damsire_surface_starts`, `damsire_surface_rank_mean`, `pedigree_verified_prior` | 同条件の父/母父産駒成績、対象馬の血統表記の過去一貫性 |
| 枠4 | `gate_fraction`, `gate_prior_count`, `gate_rank_mean`, `gate_rank_delta` | 馬番相対位置、同条件の馬番帯の過去成績 |
| 脚質/展開9 | `style_count5`, `early_position_mean5`, `early_position_std5`, `front_rate5`, `late_gain_mean5`, `field_front_pressure`, `other_front_pressure`, `field_style_coverage`, `style_pressure_interaction` | 過去通過順位と今回メンバーの前方傾向・充足率 |

各列の個別ラベルはCSVに収録。条件実績は少数観測を扱う収縮強度20を使用。血統不明を推測せず、通過順位欠損を「後方脚質」とみなさない。

### 脚質・展開モデルの現在位置

`pace_features.py`には46＋脚質9＝55、さらに隊列proxy6＝61の**研究用特徴量**がある。6追加項目は`pace_expected_position_rank / pace_front_rival_count / pace_front_dominance / pace_lone_front / pace_closing_pressure_interaction / pace_pressure_coverage`。定義はCSVに収録した。

先行タイプ確認は過去通過順位3観測以上・前方率0.6以上。単騎先行判定は全出走馬に3観測以上ある場合だけ成立する。通過順位は「最初に記録された通過地点」であり、必ず第1コーナーとは限らない。

コードは`supervised_pace_forecast=False`を明示する。**前半時計や展開そのものを教師にした監督付きペース予測器が運用済み、という意味ではない。** 既定46モデルにもこれらの脚質項目はない。

### search101の保存モデル2個は別物

| 研究成果物 | TRAIN | CAL | 評価 | 扱い |
|---|---|---|---|---|
| `selected-calibrated.research.joblib` | 2016/1/5〜2024/12/28、27,346レース・382,175行 | 2025/1/5〜12/28、3,034レース・42,148行 | 2026/1/4〜10/17、2,446レース・33,772行 | 温度0.2560777247539163、研究用 |
| `selected-full-period.research.joblib` | 2016/1/5〜2026/10/17、32,826レース・458,095行 | なし | 独立holdoutなし | **無校正**。全期間fitの研究用 |

どちらも回帰・200木・31葉・最小葉30・学習率0.05・L2=1・max_bin63・seed42・CPU2スレッド。校正済み研究モデルの後段評価は順位相関0.468947、速度RMSE0.247178 m/s、Top1 24.488962%、勝率NLL2.247987、Brier0.861677。勝率評価だけは同着2レースを除く2,444レース・33,740行。

この数値を既定モデルの異なる期間/学習集団の数値と直接引いて改善量にしない。2026年データは過去の研究でも観測済みで、未知の未来検証ではない。

校正済み研究bundle内の確率gateは合格だが、外側の研究契約は`reference_only=true / production_eligible=false / purchase_eligible=false`。内側の`eligible_for_ev=true`だけを抜き出して運用承認と解釈しない。全期間モデルには独立評価も校正もなく、別モデルの評価を移植できない。

### 「以前もっと特徴量があった」の内訳

比較時カタログのlegacy定義は168項目。分類は、検証済み共通profileに未所属74、無効/冗長39、当該結果で禁止31、対応する過去入力を比較済み12、市場取得時刻未検証12。

**全168定義を安全な入力として学習したわけではない。** 保存された古いlegacyモデルは実入力116/117列で、現在の46列とは別経路。コメントの「87特徴量モード固定」、カタログ定義数、学習候補の和集合141、実モデル入力数は別の数である。

補完78/141構成の6フラグは、コース/距離帯の勝率・Top3率・平均速度が研究上のbackoffで埋まったかを表す。補完は過去記録の修復や、その条件が初経験であることの証明ではない。

## 8. 現在の画面・APIの作成設定

| 設定 | API schema初期値 | 学習画面の初期値/送信 |
|---|---|---|
| target | `win` | **`speed_deviation`を送信** |
| model_type | `lightgbm`のみ | `lightgbm` |
| feature_pipeline | `legacy` | `local-db-shared-optimized-v1` |
| test_size | 0.2、範囲0.1〜0.5 | 初期0.2 |
| cv_folds | 5、範囲2〜10 | 5。ただし共通固定候補経路では上記のTRAIN内分割 |
| use_sqlite / ultimate_mode | true / true | true / true |
| use_optimizer | true | true |
| use_optuna | false | false |
| optuna_trials / timeout | 50 / 300秒、範囲1〜1000 / 30〜3600秒 | 共通経路では探索を使わない |
| compute_device | auto、cpu/gpuも可 | auto。GPU能力確認に応じて制御 |
| reuse_features | true | true |
| training_date_from/to | null、YYYY-MM | 空欄はnull、保存UI設定があれば復元 |
| force_sync | true | リクエストで明示しない |

開始/終了月の形式・実在月・順序を検証する。共有入力の開始未指定は2016/1/1、終了未指定は実行時の日付。実際のレース日は取得データ/資格判定に依存する。

### 新規46学習の速度基準④

現在の`db_normalized_registry.fit_module()`はoptimized46要求を`training.db_shared_grouped`へ送る。保存schemaは`local-db-shared-grouped-model-v1`、教師値契約は`local-db-conditioned-speed-v1`、armは`course_going_weather`。

基準①の芝ダ×距離に、競馬場、馬場2分類（良/稍重・重・不良）、天候2分類（晴・曇/降水）を加える。TRAINだけで基準を作り、子グループ30件未満/標準偏差不成立/条件不明なら近い親へ戻す。3つの追加階層と元の芝ダ×距離基準を保存する。未知条件を良馬場・晴に決め打ちしない。

固定候補比較と日付分割の枠組みは同じだが、教師値が異なる。校正していても`reference_only=true`、`validated=false`、`eligible_for_ev=false`、理由`conditioned_baseline_trial_live_validation_pending`となる試験経路。

既定モデルのschemaは旧基準①のoptimized-modelであり、**今の作成コードに変わっただけで保存済み重みや基準が書き換わるわけではない**。11/1の基準比較報告では④の将来優位を確定していない。現在の④試験経路と、その比較報告の採否は区別する。

### legacy/Optuna経路

legacy経路では`add_derived_features`、未来情報除外、不要列・特徴量最適化などを使う。現在の共通46/47/80経路と同じ処理だとみなさない。APIが複数targetを受理できても、共通経路は速度偏差・Optunaなし・optimizer有効を要求し、不一致は409。

旧分類Optunaの探索空間は次のとおり。**既定モデルをこの全範囲から探索して得た値ではない。**

| パラメータ | 旧分類探索 |
|---|---|
| boosting_type | gbdt / dart |
| num_leaves / max_depth | 20〜200 / 3〜12 |
| min_child_samples | 5〜100 |
| min_child_weight | 1e-4〜1e-1、log |
| learning_rate | 0.001〜0.1、log |
| n_estimators | 50〜500 |
| reg_alpha / reg_lambda | 各1e-8〜10、log |
| subsample / colsample_bytree | 各0.5〜1.0 |
| subsample_freq | 0〜7 |
| max_cat_to_onehot | 2〜10 |
| DARTのdrop_rate / skip_drop | 各0〜0.5 |

独立した反復optimizerスクリプトや別モデル用Optuna定義も存在する。ファイルがあることと、現在の画面・保存モデルで実行したことは同じではない。

## 9. 保存モデルの種類・互換性・運用制約

今回読み取ったローカル`.joblib`21個の内訳は、baseline47が9、optimized46が6、context80が1、legacy116/117が各1、参考モデルが3。各モデルの全列順・実パラメータ・保存評価はJSONの`artifacts`に記録した。新しい作成日だけで既定モデルと判断しない。

| 参考モデル | 実入力数 | 保存情報と注意 |
|---|---:|---|
| `model_win_reference_nll46_2026_20261012` | **50** | 46＋`normalized_market_probability / log_odds / market_rank / field_size`。名前のnll46だけで46列と判断しない。TRAIN指定2013/1/1〜2024/12/31。保存data_count513,613・race_count36,374。reference-only |
| `model_monthly_reference_forward_2026_20261105` | 46 | 保存TRAIN2016/1/5〜2025/6/29、403,040行・28,806レース。reference-only |
| `model_monthly_reference_random_20261105` | 46 | 保存TRAIN日付範囲2016/1/5〜2026/10/17、294,528行・21,072レース。ランダム分割なので未来汎化の証明にしない |

3参考モデルの公開manifestは`purchase_authorized=false`。データ件数は保存フィールドの値で、名称から推測したものではない。欠けた校正/評価期間をファイル名から補わない。

### 保存時ソースと現在ソース

既定モデルは11モジュールのSHAを拘束している。現在のファイルと差があるのは`experiments.dataset / training.db_shared_features / training.db_shared_fit`。特徴量生成本体と教師基準の固定helperについては保存側の拘束も確認した。

**ソース差がある＝ただちにモデルが使えない、ではない。** 既定モデルについて、現在ファイル/ローカルGit履歴から11モジュールを回収し、全SHAが保存値と一致することを読み取り専用で確認した。実予測はこの調査では実行していない。

運用時は公開台帳・SHAをunpickle前に確認し、対応する保存schemaを検証。対応した旧共通モデルのソース差は、回収した学習時ソースを隔離プロセスで使う経路がある。回収不能・契約違いを現在の式で勝手に置き換えない。context/groupedにもそれぞれ別契約がある。

### オッズ・評価表示との境界

46/72/80/101の安全入力に対象レースのオッズ/人気を足して学習し直したものではない。明示的な現在オッズ更新は**推論後**の価格情報取得で、保存特徴量・モデル・過去履歴は維持する。価格取得の証拠と、発走前特徴量の保存時刻の証拠は別。

学習TESTの順位・速度・勝率評価、研究バックテスト回収率、運用予測の参考成績、実購入の収支は別スコープ。既定bundleの`win_roi=null`に研究上の最良回収率を転記しない。確定オッズを使った過去最適化を、予測時点に取得できたオッズで実現済みと表現しない。

## 10. 根拠ファイル・追加調査が必要な点

主な根拠（行番号は調査時点）:

| 内容 | 根拠 |
|---|---|
| API初期値・制限 | `python-api\models.py:15–59` |
| UI初期値・実送信 | `src\app\train\page.tsx:69–96, 338–360` |
| 共有要求の制約・fit dispatch | `python-api\routers\train.py:538–590` |
| 固定候補・確率gate | `python-api\training\db_shared_fit.py:35–56` |
| TRAIN内比較・校正・TEST評価 | `python-api\training\db_shared_fit.py:407–499` |
| 過去履歴・72入力の生成 | `python-api\experiments\features.py:20–55, 99–197` |
| 29派生項目・式 | `python-api\experiments\search_features.py:30–71, 97–136` |
| context34個別定義 | `python-api\training\db_context_features.py:33–95` |
| ペースproxy・監督付きではない明示 | `python-api\training\pace_features.py:21–75` |
| 新規46の基準④へのdispatch | `python-api\training\db_normalized_registry.py:10–14` |
| 基準④の教師値・参考扱い | `python-api\training\db_shared_grouped.py:20–22, 87–118` |
| 基準④の階層・fallback | `python-api\training\grouped_speed_baseline.py:15–64` |
| 公開台帳・ソース admission | `python-api\services\db_shared_input.py:29–100` |
| 学習時ソース回収 | `python-api\services\trained_model_runtime.py:27–101` |
| 旧Optuna探索 | `keiba\keiba_ai\optuna_optimizer.py:113–147` |
| 実モデル値 | 添付JSON内の当該artifact。ライブラリ初期値/ファイル名より優先 |

研究の原資料は`reports\generated\feature-selection\20261107-expanded-v1`内の`protocol.json / candidate-lock.json / completion.json / calibrated-evaluation.json / feature-inventory.json`。前の目的変数比較は[6目的比較](training_target_comparison_20261101.md)、基準比較は[速度基準4方式比較](speed_baseline_grouping_comparison_20261101.md)。

未確定なのは、未知の将来でのsearch101/基準④の改善、全生涯履歴の完全性、対象特徴量の発走前公開時刻、各券種の検証済み同時確率、実際の購入収益。追加学習やモデル切替なしに、これらを「確認済み」とはしない。
