# models/archive/ — 旧モデルアーカイブ

現在の本番推論で**使用しない**旧世代のモデルを保管しています。

## 保管されているモデル

| 期間 | モデル種別 | 備考 |
|---|---|---|
| 2026/01/11 | `model_win_lightgbm_*` | ultimate特徴量なし（旧版） |
| 2026/01/11 | `model_win_logistic_regression_*` | ロジスティック回帰（精度低） |
| 2026/01/12–13 | `model_win_logistic_regression_*` | 同上 |
| 2026/02/15–17 | `model_win_lightgbm_*_optimized` | ultimate特徴量なし（旧版） |

## 現役モデル（models/ 直下）

`models/model_win_lightgbm_202602**_*_optimized_ultimate.joblib`

- `_ultimate` サフィックス付きが現行
- `main.py` の `get_latest_model()` が更新日時の最新を自動選択

## 復元方法

```powershell
# アーカイブから復元したい場合
Copy-Item "python-api\models\archive\<ファイル名>" "python-api\models\"
```
