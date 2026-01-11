# Python機械学習API

Streamlit版と同じ機械学習パイプラインをFastAPIで提供します。

## セットアップ

### 1. 依存関係のインストール

```bash
cd python-api
pip install -r requirements.txt
```

### 2. サーバー起動

```bash
python main.py
```

サーバーは `http://localhost:8000` で起動します。

### 3. APIドキュメント

サーバー起動後、以下のURLでSwagger UIが利用できます:
- http://localhost:8000/docs

## APIエンドポイント

### POST /api/train
モデル学習を実行

**リクエスト例:**
```json
{
  "target": "win",
  "model_type": "logistic_regression",
  "test_size": 0.2,
  "cv_folds": 5
}
```

**レスポンス例:**
```json
{
  "success": true,
  "model_id": "20260110_123456",
  "model_path": "models/model_win_logistic_regression_20260110_123456.joblib",
  "metrics": {
    "auc": 0.7845,
    "logloss": 0.4521,
    "cv_auc_mean": 0.7723,
    "cv_auc_std": 0.0234
  },
  "data_count": 12500,
  "race_count": 325,
  "feature_count": 68,
  "training_time": 15.3,
  "message": "モデル学習完了 (AUC: 0.7845, LogLoss: 0.4521)"
}
```

### POST /api/predict
予測を実行

**リクエスト例:**
```json
{
  "model_id": "20260110_123456",
  "horses": [
    {
      "horse_no": 1,
      "horse_name": "サンプル1号",
      "age": 4,
      "sex": "牡",
      "handicap": 56.0,
      "weight": 480,
      "weight_diff": 2,
      "entry_odds": 3.5,
      "entry_popularity": 2,
      "jockey_id": "12345",
      "trainer_id": "54321"
    }
  ]
}
```

**レスポンス例:**
```json
{
  "success": true,
  "predictions": [
    {
      "index": 0,
      "horse_number": 1,
      "horse_name": "サンプル1号",
      "predicted_probability": 0.285,
      "expected_value": 0.9975,
      "predicted_rank": 1
    }
  ],
  "model_id": "20260110_123456",
  "message": "1頭の予測が完了しました"
}
```

### GET /api/models
保存済みモデルの一覧を取得

**レスポンス例:**
```json
{
  "success": true,
  "models": [
    {
      "model_id": "20260110_123456",
      "model_path": "models/model_win_logistic_regression_20260110_123456.joblib",
      "created_at": "20260110_123456",
      "target": "win",
      "model_type": "logistic_regression",
      "metrics": {
        "auc": 0.7845,
        "logloss": 0.4521
      },
      "data_count": 12500,
      "race_count": 325
    }
  ],
  "count": 1
}
```

## Streamlit版との互換性

このAPIは以下のStreamlit版の機能を完全に再現しています:

1. ✅ load_training_frame() → SQLiteから訓練データ読み込み
2. ✅ add_derived_features() → 60+次元の特徴量生成
3. ✅ ColumnTransformer + Pipeline構築
   - 数値特徴量: SimpleImputer
   - カテゴリカル特徴量: OneHotEncoder
4. ✅ LogisticRegression or LightGBM
5. ✅ train_test_split + 5-fold CV
6. ✅ AUC, LogLoss評価
7. ✅ joblib保存

## Next.jsからの使用

Next.jsから以下のAPIエンドポイントを通じて使用できます:

- `POST /api/ml/train` - モデル学習
- `POST /api/ml/predict` - 予測実行
- `GET /api/ml/models` - モデル一覧取得

## トラブルシューティング

### モジュールが見つからないエラー
```
ModuleNotFoundError: No module named 'keiba_ai'
```

→ `keiba/keiba_ai/`ディレクトリが正しい位置にあることを確認してください。

### 訓練データが見つからないエラー
```
訓練データが見つかりません
```

→ 先にStreamlitの「1_データ取得.py」でデータを収集してください。

### ポートが使用中のエラー
```
Address already in use
```

→ 別のポートで起動: `uvicorn main:app --host 0.0.0.0 --port 8001`
