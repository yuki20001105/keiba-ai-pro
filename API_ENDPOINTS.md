# FastAPI競馬予測システム - エンドポイント仕様書

## 📡 ベースURL

```
http://localhost:8000
```

---

## 🎯 エンドポイント一覧

### 1. ヘルスチェック

#### `GET /`

サーバーの稼働状況を確認

**レスポンス例:**
```json
{
  "status": "ok",
  "service": "Keiba AI - Machine Learning API",
  "version": "1.0.0"
}
```

---

### 2. モデル学習

#### `POST /api/train`

機械学習モデルのトレーニングを実行

**リクエストボディ:**
```json
{
  "target": "win",
  "model_type": "logistic_regression",
  "test_size": 0.2,
  "cv_folds": 5,
  "use_sqlite": true
}
```

**パラメータ:**
- `target` (string): `"win"` (単勝) or `"place3"` (複勝)
- `model_type` (string): `"logistic_regression"` or `"lightgbm"`
- `test_size` (float): テストデータ分割比率 (0.1-0.4)
- `cv_folds` (int): クロスバリデーションのfold数
- `use_sqlite` (bool): SQLiteからデータ読み込み

**レスポンス例:**
```json
{
  "success": true,
  "model_id": "20260111_153045",
  "model_path": "models/model_win_20260111_153045.joblib",
  "metrics": {
    "auc": 0.85,
    "log_loss": 0.32,
    "accuracy": 0.78
  },
  "data_count": 1480,
  "race_count": 100,
  "feature_count": 68,
  "training_time": 12.5,
  "message": "モデルの学習が完了しました"
}
```

---

### 3. 予測実行

#### `POST /api/predict`

学習済みモデルで予測を実行

**リクエストボディ:**
```json
{
  "model_id": null,
  "horses": [
    {
      "horse_no": 1,
      "bracket": 1,
      "age": 3,
      "handicap": 54.0,
      "weight": 460,
      "weight_diff": 2,
      "entry_odds": 3.5,
      "entry_popularity": 2
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
      "horse_no": 1,
      "win_probability": 0.25,
      "place_probability": 0.45
    }
  ],
  "model_id": "20260111_153045",
  "message": "予測が完了しました"
}
```

---

### 4. 🔥 レース分析・購入推奨 (重要)

#### `POST /api/analyze_race`

**Streamlit 3_予測_batch.py の Tab1~Tab2 機能を統合**

レース情報から以下を自動実行:
1. 予測実行（全出走馬の勝率計算）
2. 期待値計算
3. プロ戦略スコア評価
4. 馬券種別候補生成（単勝/馬連/ワイド/三連複/馬単/三連単）
5. ケリー基準・動的単価計算
6. 購入推奨金額・点数決定

**リクエストボディ:**
```json
{
  "race_id": "202401010101",
  "bankroll": 10000,
  "risk_mode": "balanced",
  "use_kelly": true,
  "dynamic_unit": true,
  "min_ev": 1.2,
  "model_id": null
}
```

**パラメータ:**
- `race_id` (string): レースID (必須)
- `bankroll` (int): 総資金 (¥10,000-¥10,000,000)
- `risk_mode` (string): リスクモード
  - `"conservative"`: 保守的 (2%)
  - `"balanced"`: バランス (3.5%)
  - `"aggressive"`: 積極的 (5%)
- `use_kelly` (bool): ケリー基準を使用
- `dynamic_unit` (bool): 動的単価調整
- `min_ev` (float): 最低期待値フィルタ (1.0-3.0)
- `model_id` (string, optional): 使用するモデルID（null=最新）

**レスポンス例:**
```json
{
  "success": true,
  "race_info": {
    "race_id": "202401010101",
    "race_name": "東京新聞杯",
    "venue": "東京",
    "date": "2024-01-01",
    "distance": 1600,
    "track_type": "芝",
    "weather": "晴",
    "field_condition": "良",
    "num_horses": 16
  },
  "pro_evaluation": {
    "difficulty_score": 0.75,
    "recommended_action": "勝負",
    "nakaana_chance": {
      "horse_no": 7,
      "horse_name": "サンプル馬",
      "odds": 12.5,
      "expected_value": 3.2,
      "popularity": 6
    },
    "season_bonus": 1.0,
    "jockey_bonus": {
      "has_high_recovery_jockey": true,
      "jockeys": [
        {
          "jockey": "武豊",
          "recovery_rate": 1.25,
          "horse_no": 3
        }
      ],
      "bonus": 1.15
    },
    "confidence_level": "high"
  },
  "predictions": [
    {
      "horse_no": 3,
      "horse_name": "サンプル馬A",
      "jockey_name": "武豊",
      "trainer_name": "藤沢和雄",
      "sex": "牡",
      "age": 4,
      "weight": 478,
      "odds": 4.5,
      "popularity": 2,
      "win_probability": 0.28,
      "expected_value": 1.26
    }
  ],
  "bet_types": {
    "単勝": [
      {
        "combination": "3",
        "expected_value": 1.26,
        "probability": 0.28,
        "odds": 4.5
      }
    ],
    "馬連": [
      {
        "combination": "3-5",
        "expected_value": 1.15,
        "probability": 0.14
      }
    ],
    "ワイド": [
      {
        "combination": "3-5",
        "expected_value": 1.15,
        "probability": 0.14
      }
    ],
    "三連複": [
      {
        "combination": "3-5-7",
        "expected_value": 1.22,
        "probability": 0.08
      }
    ],
    "馬単": [
      {
        "combination": "3→5",
        "expected_value": 1.18,
        "probability": 0.07
      }
    ],
    "三連単": [
      {
        "combination": "3→5→7",
        "expected_value": 1.25,
        "probability": 0.04
      }
    ]
  },
  "best_bet_type": "三連複",
  "best_bet_info": {
    "平均期待値": 1.15,
    "最大期待値": 1.22,
    "候補数": 10,
    "最高確率": 0.08
  },
  "race_level": "decisive",
  "recommendation": {
    "unit_price": 500,
    "purchase_count": 10,
    "total_cost": 5000,
    "budget": 8000,
    "budget_usage_rate": 62.5,
    "kelly_recommended_amount": 700,
    "strategy_explanation": "🔥 勝負レース！ - 三連複 10点 @¥500\n難易度スコア 0.75 - 高信頼度予測！"
  }
}
```

---

### 5. 🛒 購入履歴保存

#### `POST /api/purchase`

**Streamlit 3_予測_batch.py の Tab3 購入ボタン機能**

購入した馬券をtracking.dbに記録

**リクエストボディ:**
```json
{
  "race_id": "202401010101",
  "bet_type": "三連複",
  "combinations": ["3-5-7", "3-5-8", "3-7-9"],
  "strategy_type": "勝負レース",
  "purchase_count": 3,
  "unit_price": 500,
  "total_cost": 1500,
  "expected_value": 1.22,
  "expected_return": 1830
}
```

**パラメータ:**
- `race_id` (string): レースID
- `bet_type` (string): 馬券種（単勝/馬連/ワイド/三連複/馬単/三連単）
- `combinations` (array): 購入組み合わせリスト
- `strategy_type` (string): 戦略名（通常/勝負/見送り）
- `purchase_count` (int): 購入点数
- `unit_price` (int): 1点単価
- `total_cost` (int): 総投資額
- `expected_value` (float): 期待値
- `expected_return` (float): 期待払戻

**レスポンス例:**
```json
{
  "success": true,
  "purchase_id": 42,
  "message": "購入履歴を保存しました (ID: 42)"
}
```

---

### 6. 📊 購入履歴取得

#### `GET /api/purchase_history?limit=50`

**Streamlit 3_予測_batch.py の Tab4 検証結果表示機能**

保存された購入履歴と統計サマリーを取得

**クエリパラメータ:**
- `limit` (int, optional): 取得件数 (デフォルト: 50)

**レスポンス例:**
```json
{
  "success": true,
  "history": [
    {
      "id": 42,
      "race_id": "202401010101",
      "purchase_date": "2026-01-11",
      "season": "冬",
      "bet_type": "三連複",
      "combinations": ["3-5-7", "3-5-8"],
      "strategy_type": "勝負レース",
      "purchase_count": 2,
      "unit_price": 500,
      "total_cost": 1000,
      "expected_value": 1.22,
      "expected_return": 1220,
      "actual_return": 0,
      "is_hit": false,
      "recovery_rate": 0.0,
      "created_at": "2026-01-11 15:30:45"
    }
  ],
  "count": 42,
  "summary": {
    "total_cost": 50000,
    "total_return": 58000,
    "recovery_rate": 116.0,
    "hit_count": 12,
    "hit_rate": 28.6
  }
}
```

---

### 7. 📈 統計サマリー

#### `GET /api/statistics`

馬券種別・シーズン別の統計データを取得

**レスポンス例:**
```json
{
  "success": true,
  "statistics": {
    "by_bet_type": [
      {
        "bet_type": "三連複",
        "count": 15,
        "total_cost": 15000,
        "total_return": 18500,
        "recovery_rate": 123.3,
        "hit_count": 4,
        "hit_rate": 26.7
      },
      {
        "bet_type": "ワイド",
        "count": 10,
        "total_cost": 10000,
        "total_return": 9500,
        "recovery_rate": 95.0,
        "hit_count": 5,
        "hit_rate": 50.0
      }
    ],
    "by_season": [
      {
        "season": "春",
        "count": 20,
        "total_cost": 20000,
        "total_return": 24000,
        "recovery_rate": 120.0
      },
      {
        "season": "冬",
        "count": 5,
        "total_cost": 5000,
        "total_return": 4000,
        "recovery_rate": 80.0
      }
    ]
  }
}
```

---

### 8. モデル一覧取得

#### `GET /api/models`

訓練済みモデル一覧を取得

**レスポンス例:**
```json
{
  "success": true,
  "models": [
    {
      "model_id": "20260111_153045",
      "model_path": "models/model_win_20260111_153045.joblib",
      "created_at": "2026-01-11 15:30:45",
      "target": "win",
      "model_type": "logistic_regression",
      "metrics": {
        "auc": 0.85
      }
    }
  ],
  "count": 5
}
```

---

### 9. モデル詳細取得

#### `GET /api/models/{model_id}`

特定モデルの詳細情報を取得

**パスパラメータ:**
- `model_id` (string): モデルID

**レスポンス例:**
```json
{
  "success": true,
  "model_id": "20260111_153045",
  "model_path": "models/model_win_20260111_153045.joblib",
  "created_at": "2026-01-11 15:30:45",
  "target": "win",
  "model_type": "logistic_regression",
  "metrics": {
    "auc": 0.85,
    "log_loss": 0.32,
    "accuracy": 0.78
  },
  "data_count": 1480,
  "race_count": 100,
  "feature_count": 68
}
```

---

## 🔄 典型的なワークフロー

### シナリオ1: 初回セットアップ

```bash
# 1. モデル学習
curl -X POST http://localhost:8000/api/train \
  -H "Content-Type: application/json" \
  -d '{"target":"win","model_type":"logistic_regression"}'

# 2. モデル確認
curl http://localhost:8000/api/models
```

### シナリオ2: レース分析と購入（重要）

```bash
# 1. レース分析・購入推奨取得
curl -X POST http://localhost:8000/api/analyze_race \
  -H "Content-Type: application/json" \
  -d '{
    "race_id": "202401010101",
    "bankroll": 10000,
    "risk_mode": "balanced",
    "use_kelly": true,
    "dynamic_unit": true,
    "min_ev": 1.2
  }'

# 2. 推奨内容を確認後、購入履歴保存
curl -X POST http://localhost:8000/api/purchase \
  -H "Content-Type: application/json" \
  -d '{
    "race_id": "202401010101",
    "bet_type": "三連複",
    "combinations": ["3-5-7", "3-5-8"],
    "strategy_type": "勝負レース",
    "purchase_count": 2,
    "unit_price": 500,
    "total_cost": 1000,
    "expected_value": 1.22,
    "expected_return": 1220
  }'

# 3. 購入履歴確認
curl http://localhost:8000/api/purchase_history?limit=10

# 4. 統計確認
curl http://localhost:8000/api/statistics
```

---

## 📝 エラーレスポンス

すべてのエンドポイントで共通のエラーフォーマット:

```json
{
  "detail": "エラーメッセージ"
}
```

**HTTPステータスコード:**
- `200`: 成功
- `400`: リクエストエラー（パラメータ不正など）
- `404`: リソースが見つからない（モデル/レース未存在）
- `500`: サーバーエラー

---

## 🎯 Streamlit → FastAPI 機能対応表

| Streamlit機能 | FastAPIエンドポイント | 実装状況 |
|--------------|---------------------|---------|
| ページ1: データ取得 | scraping_service_*.py | ✅ 既存 |
| ページ2: 学習 | POST /api/train | ✅ 完了 |
| ページ3 Tab1: レース選択 | POST /api/analyze_race | ✅ 完了 |
| ページ3 Tab2: レース詳細 | POST /api/analyze_race | ✅ 完了 |
| ページ3 Tab3: 購入推奨 | POST /api/purchase | ✅ 完了 |
| ページ3 Tab4: 検証結果 | GET /api/purchase_history | ✅ 完了 |
| ページ4: DB確認 | (直接SQLiteクエリ) | - |

---

## 🚀 使用例（Python）

```python
import requests

# レース分析
response = requests.post(
    "http://localhost:8000/api/analyze_race",
    json={
        "race_id": "202401010101",
        "bankroll": 10000,
        "risk_mode": "balanced",
        "use_kelly": True,
        "dynamic_unit": True,
        "min_ev": 1.2
    }
)

result = response.json()

# 推奨情報表示
print(f"レース: {result['race_info']['race_name']}")
print(f"推奨: {result['best_bet_type']} {result['recommendation']['purchase_count']}点")
print(f"単価: ¥{result['recommendation']['unit_price']}")
print(f"総額: ¥{result['recommendation']['total_cost']}")
print(f"レベル: {result['race_level']}")
```

---

## 📖 APIドキュメント（Swagger UI）

FastAPI起動後、以下にアクセス:

```
http://localhost:8000/docs
```

インタラクティブなAPI仕様書で各エンドポイントをテスト可能
