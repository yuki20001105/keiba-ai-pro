# validation/ — データ品質・特徴量検証スクリプト

本番運用ではなく、**開発・デバッグ・品質確認**のために使うスクリプト群です。

## スクリプト一覧

| ファイル | 用途 | 実行タイミング |
|---|---|---|
| `check_null_rates3.py` | DB の全 JSON キー充填率を確認（欠損率一覧） | パッチ実行後・再学習前 |
| `check_date_leakage.py` | 特徴量の日付ズレ・データリーク診断 | 特徴量修正後の確認時 |
| `check_features_detail.py` | 生特徴量 vs モデル入力特徴量の詳細比較 | 特徴量エンジニアリング変更後 |
| `check_final_features.py` | モデルに入る最終特徴量のカラム一覧確認 | モデル再学習前の確認 |

## 実行方法

すべてプロジェクトルート（`keiba-ai-pro/`）から実行してください。

```powershell
# 充填率確認（パッチ完了後に必ず実行）
.venv\Scripts\python.exe validation\check_null_rates3.py

# データリーク診断
.venv\Scripts\python.exe validation\check_date_leakage.py

# 特徴量詳細確認
.venv\Scripts\python.exe validation\check_features_detail.py

# 最終特徴量一覧
.venv\Scripts\python.exe validation\check_final_features.py
```

## 注意

- これらのスクリプトは DB を**読み取り専用**で参照します（更新はしません）
- パスは `__file__` ベースで解決しているため、どのディレクトリからでも実行可能
