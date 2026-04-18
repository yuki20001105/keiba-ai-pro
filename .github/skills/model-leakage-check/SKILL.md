---
name: model-leakage-check
description: 'データリーク監査スキル。Use when: モデルが未来情報(レース結果・タイム・着順)を使って学習していないか確認したい / データリーク(data leakage)がないか調査したい / モデルの精度が不審なほど高い / FUTURE_FIELDS除外が正しく機能しているか確認したい / 特徴量エンジニアリングのexpanding windowが正しく実装されているか確認したい。Keywords: データリーク, leakage, 未来情報, 特徴量漏洩, モデル精度確認, POST_RACE_FIELDS, FUTURE_FIELDS, 過学習'
---

# モデル データリーク 監査スキル

競馬AI予測モデルが**レース終了後の情報（着順・タイム等）を特徴量に使っていないか**を体系的に確認する手順書。

## 監査対象の3レイヤー

| レイヤー | ファイル | 確認ポイント |
|---------|---------|------------|
| 1. 学習パイプライン | `python-api/routers/train.py` | FUTURE_FIELDS を drop するタイミング |
| 2. 特徴量エンジニアリング | `keiba/keiba_ai/feature_engineering.py` | expanding window が自己レース結果を含まないか |
| 3. 学習済みモデル | `python-api/models/model_*.joblib` | feature_columns に FUTURE_FIELDS が含まれないか |

---

## 手順 1 — FUTURE_FIELDS の定義確認

```python
# keiba/keiba_ai/constants.py
from keiba_ai.constants import FUTURE_FIELDS
print(FUTURE_FIELDS)
```

**確認点**: `finish`, `finish_time`, `last_3f_time`, `corner_1〜4`, `prize_money`, `margin` などを含む frozenset (21フィールド)。

---

## 手順 2 — 学習パイプライン監査

`python-api/routers/train.py` の `train_model()` 関数を読み、以下の順序を確認:

```
1. _make_target(df, request.target)  ← 目的変数 y を抽出（この時点で finish 列が必要）
2. add_derived_features(df, ...)     ← 派生特徴量を生成
3. drop_train = [c for c in FUTURE_FIELDS if c in df.columns]
4. df.drop(columns=drop_train)       ← ここで未来情報を除外
5. prepare_for_lightgbm_ultimate()  ← オプティマイザ内でも FUTURE_INFO_BLACKLIST を除外（二重保護）
```

**INV-01 チェック**: ステップ3・4が必ずステップ5より前にあることを確認。

---

## 手順 3 — Expanding Window 関数の監査

`keiba/keiba_ai/feature_engineering.py` の以下の3パターンを確認:

### パターン A: `cumsum() - s['_w']`（自己除外 cumsum）
```python
# 正しい実装例
cum_wins = s.groupby(grp)['_w'].cumsum() - s['_w']  # ← 自分自身を引く
race_cnt = s.groupby(grp).cumcount()                  # ← 0-indexed = 自分より前の件数
```

### パターン B: `.shift(1).rolling(N)`（シフト+ローリング）
```python
# 正しい実装例
result = df.groupby('horse_id')['finish'].transform(
    lambda x: x.shift(1).rolling(N, min_periods=1).mean()  # ← shift(1) で自己除外
)
```

**チェック**: `cumsum()` に続く `- s['_w']` または `.shift(1)` がないものは**リーク**。

---

## 手順 4 — 学習済みモデルの feature_columns チェック

```python
import joblib, glob
FUTURE_FIELDS = {
    'time_seconds','finish_time','last_3f','last_3f_time','last_3f_rank',
    'last_3f_rank_normalized','corner_1','corner_2','corner_3','corner_4',
    'corner_positions','corner_positions_list','corner_position_avg',
    'corner_position_variance','last_corner_position','position_change',
    'margin','prize_money','finish','finish_position','actual_finish'
}

models = sorted(glob.glob('python-api/models/model_win_lightgbm_*.joblib'))
for mpath in models[-3:]:  # 最新3件
    bundle = joblib.load(mpath)
    if isinstance(bundle, dict):
        fcols = bundle.get('feature_columns', [])
        leaked = [c for c in fcols if c in FUTURE_FIELDS]
        status = f"LEAKED: {leaked}" if leaked else f"CLEAN ({len(fcols)} features)"
        print(f"{mpath}: {status}")
```

---

## 手順 5 — 要注意フィールドの個別確認

以下のフィールドは名前が似ているが**リークではない**：

| フィールド名 | 理由 |
|------------|------|
| `prev_race_finish` | 前走（1レース前）の着順 — 過去情報 ✅ |
| `prev2_race_finish` | 2走前の着順 — 過去情報 ✅ |
| `past_10_avg_finish` | 過去10走平均 (`race_id < current_race_id`) ✅ |
| `horse_total_prize_money` | スクレイプ時点の累積賞金（出走前確定）✅ |
| `log_prize` | `log1p(horse_total_prize_money)` の変換 ✅ |
| `finish_consistency` | `WHERE race_id < current` クエリで計算 ✅ |
| `corner_radius_encoded` | コース設備の固定値（結果ではない）✅ |

---

## 確認済みステータス（最終監査: 2026-04）

- ✅ `FUTURE_FIELDS` (21フィールド) — 学習前に確実に除外 (3重保護)
- ✅ `_expanding_win_rate_by_group()` — `cumsum() - row` で自己除外
- ✅ `_expanding_grouped_stats()` — `cumsum() - row` で自己除外
- ✅ `_expanding_stats()` — `cumsum() - row` で自己除外  
- ✅ `_feh_recent_form()` — `.shift(1).rolling()` で自己除外
- ✅ `_feh_entity_recent30()` — `.shift(1).rolling()` で自己除外
- ✅ `_feh_last_3f()` — `.shift(1).rolling()` で自己除外
- ✅ `calculate_horse_past_10_races()` — `WHERE race_id < ?` で自己除外
- ✅ 本番モデル (110 features) — FUTURE_FIELDS ゼロ件 (最終確認済み)

---

## 新モデル再訓練後の監査チェックリスト

新しいモデルを訓練した後は以下を確認してください:

```
□ 1. model_*.joblib の feature_columns に FUTURE_FIELDS が含まれないこと
□ 2. 検証 AUC が 0.95 超の場合はリークを強く疑うこと（正常範囲: 0.65〜0.80）
□ 3. 特徴量重要度で finish/time/corner 系が上位に来ていないこと
□ 4. 新規追加した特徴量が expanding window を使う場合は自己除外パターンを実装すること
```
