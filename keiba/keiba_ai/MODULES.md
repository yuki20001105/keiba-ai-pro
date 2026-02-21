# keiba_ai/ — AIコアモジュール 分類表

## 本番運用で使うモジュール

### データ取得・保存
| ファイル | 役割 |
|---|---|
| `netkeiba/client.py` | netkeiba へのHTTPスクレイピング（通常モード） |
| `netkeiba/browser_client.py` | Playwright によるブラウザスクレイピング（IP制限回避） |
| `netkeiba/parsers.py` | HTML パース処理 |
| `ingest.py` | レースデータ取得 → DB保存のメインロジック |
| `db_ultimate.py` | `keiba_ultimate.db` スキーマ定義・接続管理 |
| `db_ultimate_loader.py` | `keiba_ultimate.db` からトレーニングデータ読み込み |
| `db.py` | 旧 `keiba.db` 接続管理（後方互換） |

### 特徴量エンジニアリング
| ファイル | 役割 | 主な出力特徴量 |
|---|---|---|
| `feature_engineering.py` | 派生特徴量の計算 | コース特性・騎手/調教師統計（expanding window）・ペース特徴量 |
| `ultimate_features.py` | 過去10走ベース特徴量（expanding window） | `past_10_*`, `jockey_recent_*`, `trainer_recent_*` |
| `lightgbm_feature_optimizer.py` | エンティティ統計・LightGBM用特徴量整備 | 父馬/母父馬 win_rate, race_count（expanding window）|

| `course_master.yaml` | コース特性マスタ（直線距離・内枠有利度など） | — |

### モデル学習・最適化
| ファイル | 役割 |
|---|---|
| `train.py` | LightGBM/ロジスティック回帰の学習 |
| `optuna_all_models.py` | 全モデルの Optuna ハイパーパラメータ最適化 |
| `optuna_optimizer.py` | Optuna ユーティリティ |

### 予測
| ファイル | 役割 |
|---|---|
| `predict.py` | モデルを使った予測 CLI（`python -m keiba_ai.predict`） |
| `pipeline_daily.py` | 予測対象レースの特徴量作成 |
| `extract_odds.py` | JRA公式サイトからリアルタイムオッズ取得 |

### 設定・ユーティリティ
| ファイル | 役割 |
|---|---|
| `config.py` | YAML設定ファイル読み込み |
| `utils.py` | 共通ユーティリティ |
| `schema_ultimate.sql` | DB スキーマ定義 SQL |
| `__init__.py` | パッケージ初期化 |
| `__main__.py` | `python -m keiba_ai` エントリポイント |

---

## 特徴量のデータリーク対策（2026/02/21 修正済み）

**問題**: 学習データに未来情報が混入していた（expanding window でなく全データ集計）

**修正済みファイル**:
- `feature_engineering.py` — `_expanding_stats()` で騎手/調教師統計を expanding window に変更
- `lightgbm_feature_optimizer.py` — `_add_entity_statistics()` を expanding window に変更
- `ultimate_features.py` — `race_id < current_race_id` フィルタは元から正しく実装済み

---

## 削除済みファイル（参考）
| ファイル | 理由 |
|---|---|
| `ingest_broken.py` | 開発中の壊れたバージョン（不要） |
| `ingest.py.backup` | バックアップコピー（不要） |
