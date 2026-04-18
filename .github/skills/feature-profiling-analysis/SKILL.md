---
name: feature-profiling-analysis
description: '特徴量プロファイリング分析・最適化スキル。Use when: ydata-profiling レポートを解析して特徴量を最適化したい / 相関の高い特徴量ペアを特定したい / 欠損率・ゼロ率が高い特徴量を整理したい / 10イテレーション反復最適化パイプラインを実行したい / プロファイリングレポートの添付から自動で特徴量エンジニアリングを修正したい。Keywords: プロファイリング, 相関, 欠損, 特徴量整理, ydata-profiling, Pearson, 反復最適化, iterative, 高相関除去, 冗長特徴量'
argument-hint: 'プロファイリングHTMLパス または "auto"（自動生成）'
---

# 特徴量プロファイリング分析・最適化スキル

ydata-profiling レポートを解析し、相関・欠損・重要度の3軸で特徴量を整理して
モデル品質を向上させる。10イテレーション反復最適化パイプライン（ITR-01〜ITR-10）も定義する。

---

## 【実行方法】

### A. プロファイリングレポートを新規生成する
```bash
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe generate_profiling_report.py
# → profiling_report.html が生成される（5〜15分）
```

### B. 既存 HTML を分析する（添付ファイルから）
```bash
python-api\.venv\Scripts\python.exe -c "
from bs4 import BeautifulSoup
with open(r'PATH_TO_REPORT.html', encoding='utf-8') as f:
    soup = BeautifulSoup(f.read(), 'html.parser')

# 1. 統計サマリー
overview = soup.find(id='overview')
print(overview.get_text()[:3000] if overview else 'no overview')

# 2. アラート一覧
alerts_div = soup.find(id='alerts')
print(alerts_div.get_text()[:5000] if alerts_div else 'no alerts')

# 3. 相関テーブル
ct = soup.find(id='correlation-table-container')
if ct:
    rows = ct.find_all('tr')
    headers = [td.get_text(strip=True) for td in rows[0].find_all(['th','td'])]
    for row in rows[1:]:
        cells = [td.get_text(strip=True) for td in row.find_all(['th','td'])]
        # 0.85超のペアを抽出
        for i, v in enumerate(cells[1:], 1):
            try:
                if abs(float(v)) >= 0.85 and cells[0] != headers[i]:
                    print(f'{cells[0]} <-> {headers[i]}: r={v}')
            except ValueError:
                pass
"
```

### C. 10イテレーション自動化スクリプト
```bash
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python-api\.venv\Scripts\python.exe python-api/iterative_optimize.py --iterations 10
```

---

## 【分析の3軸】

### 軸1: 相関分析（Pearson）

**閾値と判断基準:**

| 相関係数 r | 判断 | アクション |
|-----------|------|-----------|
| ≥ 0.95 | ほぼ同一情報 | 特徴量重要度が低い方を **即削除** |
| 0.90〜0.95 | 高相関 | 両方のGain重要度を確認 → 低い方を削除 |
| 0.80〜0.90 | 要注意 | 意味的に独立しているか確認。両方残す場合も |
| < 0.80 | 許容範囲 | 原則そのまま |

**削除決定のルール:**
1. 高相関ペア `(A, B)` で `r ≥ 0.90` のとき:
   - Gain重要度 A < B → A を削除
   - どちらか一方が**派生特徴量**（ratio, z-score, index）なら **原変数を削除** して派生を残す
   - 両方が原変数なら**より解釈しやすい**方（体重変化 > 絶対体重）を残す
2. `burden_weight` (斤量) は `horse_weight` と r≥0.97 かつ Top30圏外 → 削除
3. `prev_race_time_seconds` は `prev_race_distance` と r≥0.97 → `prev_speed_index` で代替して削除

**コード変更パターン — constants.py:**
```python
# keiba/keiba_ai/constants.py の UNNECESSARY_COLUMNS 末尾に追記
# ──────────────────────────────────────────────────────────
# ⚠ 高相関特徴量（YYYY-MM-DD 追加）r=X.XX with Y
"feature_name_to_remove",
"feature_name_to_remove_is_missing",  # 関連フラグも必ず削除
```

---

### 軸2: 欠損率分析

**閾値と判断基準:**

| 欠損率 | 判断 | アクション |
|--------|------|-----------|
| ≥ 90% | 使用不可 | 削除 (optimizerが自動除去) |
| 50〜90% | 要注意 | `_is_missing` フラグ + LightGBM NaN で処理していれば許容 |
| 20〜50% | 前走情報 | `is_first_race` フラグが有効なら許容 |
| < 20% | 正常 | 対応不要 |

**欠損率が高くても削除しない特徴量:**
- `prev_*`, `prev2_*` 系 → 新馬・初出走（46%）は自然な欠損。`is_first_race=1` で代替
- `prev_speed_index`, `prev_speed_zscore` → 上記と同じ
- `days_since_last_race` → 新馬のみ欠損

**0埋め禁止**: これらの列に `fillna(0)` を使うと新馬とタイム=0秒を混同する。
LightGBM の NaN 自動処理に任せること。

---

### 軸3: ゼロ比率分析

| ゼロ比率 | 列 | 判断 |
|---------|-----|------|
| ~71% | `distance_change` | 自然（同距離が多い）。重要度 Top30 にあれば保持 ✅ |
| ~54% | `is_first_race` | 自然（初出走でないケースが多い）✅ |
| ~54% | `race_class_num` | ゼロ = 未勝利クラス。正常 ✅ |
| ~15% | `horse_weight_change` | 体重変化なし。正常 ✅ |
| ~100% | 任意列 | 定数列 → 即削除 |

---

## 【特徴量重要度との照合】

プロファイリングレポートを **feature_importance_report スキル** と組み合わせて使う。

**照合手順:**
1. feature_importance スキルでレポートを生成 → Gain Top30 を確認
2. プロファイリングで高相関として フラグされた列を照合:
   - **高相関 かつ Gain < 1.0%** → 即削除候補
   - **高相関 かつ Gain > 1.0%** → 相関相手と比較して低い方のみ削除

**基準スコア（参考）:**
```
Gain >= 2.0%  : Top tier  → 削除禁止
Gain 1.0~2.0% : Mid tier  → 相関相手次第
Gain 0.5~1.0% : Low tier  → 相関あれば削除
Gain < 0.5%   : Noise     → 原則削除（ただし欠損フラグは例外）
```

---

## 【コードベース変更手順】

### Step 1: `keiba/keiba_ai/constants.py` の `UNNECESSARY_COLUMNS` を更新

```python
# 末尾の "rest_category", の直後に追記
# ──────────────────────────────────────────────────────────────────────────
# ⚠ 高相関特徴量（ITR-XX YYYY-MM-DD 追加）
# ──────────────────────────────────────────────────────────────────────────
"column_a",              # r=X.XX with column_b (Gain=X.X%)
"column_a_is_missing",   # ↑削除に伴い不要
```

### Step 2: `keiba/keiba_ai/feature_engineering.py` を更新

削除した列が **中間計算の素材**だった場合、より良い派生特徴量に置き換える:

```python
# 例: prev2_race_time (削除) → prev2_speed_index (新規追加)
if 'prev2_race_time' in df.columns and 'prev2_race_distance' in df.columns:
    _p2t = pd.to_numeric(df['prev2_race_time'], errors='coerce')
    _p2d = pd.to_numeric(df['prev2_race_distance'], errors='coerce')
    df['prev2_speed_index'] = np.where((_p2t > 0) & (_p2d > 0), _p2d / _p2t, np.nan)
```

### Step 3: `keiba/keiba_ai/lightgbm_feature_optimizer.py` の `numeric_features` を更新

```python
# 削除: 'column_to_remove',  # コメントアウト + 理由記載
# 追加: 'new_derived_feature',  # 追加した派生特徴量

# _has_missing_flag セットも同様に更新
_has_missing_flag = {
    # 'column_to_remove',  # 削除済み
    'new_derived_feature',  # 追加
    ...
}
```

### Step 4: 変更の検証

```python
# 変更後に必ず実行
import sys
sys.path.insert(0, 'keiba')
from keiba_ai.constants import UNNECESSARY_COLUMNS
from keiba_ai.feature_engineering import add_derived_features
import pandas as pd

# 削除列が UNNECESSARY_COLUMNS に入っているか確認
targets = ['column_a', 'column_b']
for t in targets:
    print('✓' if t in UNNECESSARY_COLUMNS else '✗', t)
```

---

## 【10イテレーション反復最適化（ITR プロシージャ）】

各イテレーションの標準フロー:

```
ITR-XX
  ├─ [DATA]    2016-01〜2026-03 のデータが DB に揃っているか確認
  │             足りない月は /api/scrape/start で追加取得
  ├─ [TRAIN]   python-api/iterative_optimize.py --iter N でモデル学習
  │             → docs/reports/iter_XX_metrics.json に保存
  ├─ [PROFILE] generate_profiling_report.py を実行
  │             → docs/reports/profiling_ITR-XX_YYYYMMDD.html に保存
  ├─ [ANALYZE] 本スキルの手順で HTML を解析
  │             → 相関アラート・欠損アラート・Gain順位 を照合
  ├─ [CHANGE]  constants.py / feature_engineering.py / optimizer.py を修正
  │             → テスト: python -m pytest keiba/keiba_ai/tests/ -v
  └─ [NEXT]    次のイテレーションへ
```

### 終了条件（10回または早期終了）:
- 連続2イテレーションで CV AUC の改善差 < 0.001 → 収束と判断して終了
- CV AUC が前イテレーション比で 0.005 以上**低下** → 変更を revert して終了

### イテレーション優先順序（一般則）:
| イテレーション | 主な作業 |
|--------------|---------|
| ITR-01~02 | 高相関ペア（r≥0.90）の除去 |
| ITR-03~04 | 低重要度特徴量（Gain<0.3%）の除去 |
| ITR-05~06 | 新規派生特徴量の追加（速度指標・組み合わせ等） |
| ITR-07~08 | カテゴリエンコーディングの見直し |
| ITR-09~10 | 正規化・非線形変換の最適化 |

---

## 【よくある改善パターン】

### パターン1: 体重グループの整理
```
horse_weight (保持) + horse_weight_change (保持)
  → prev_race_weight (r=0.947, 削除)
  → prev2_race_weight (r=0.948, 削除)
  → burden_weight (r=0.970, 削除)
```

### パターン2: タイム→スピード指標化
```
prev_speed_index = prev_race_distance / prev_race_time_seconds (保持)
  → prev_race_time_seconds (r=0.978 with distance, 削除)
prev2_speed_index = prev2_race_distance / prev2_race_time (保持)
  → prev2_race_time (r=0.980 with distance, 削除)
```

### パターン3: オッズ四重化の整理
```
odds (保持) + implied_prob (保持) + odds_z_in_race (保持)
  → implied_prob_norm (≈popularity, 削除)
  → odds_rank_in_race (≈popularity, 削除)
  → log_odds: r(odds, log_odds)が高い → 削除候補
```

### パターン4: 枠番・馬番の整理
```
bracket_number (枠番) vs horse_number (馬番): r=0.866 → 枠番の方が gate_win_rate と共起するため保持
  → horse_number のみを削除する場合もある（Gates重視なら bracket を保持）
```

---

## 【生成スクリプト: generate_profiling_report.py の使い方】

```python
# タイムスタンプ付きで保存する場合（デフォルトは profiling_report.html）
# generate_profiling_report.py の out_path を変更:
from datetime import datetime
timestamp = datetime.now().strftime('%Y%m%d_%H%M')
out_path = f"docs/reports/profiling_ITR-{iter_num:02d}_{timestamp}.html"
```

---

## 【注意事項（INV との関係）】

- **INV-01**: 特徴量削除は `UNNECESSARY_COLUMNS` 経由で行い、`add_derived_features` や `_fe_*` 関数の呼び出し順序を変えないこと
- **INV-02**: `odds`, `implied_prob`, `odds_z_in_race` の3列は絶対に削除しないこと
- **INV-04**: スクレイピング並列度 `CONCURRENCY = 1` を維持すること
- **NaN方針**: 欠損は `fillna(0)` せず LightGBM の native missing 処理に委ねること
