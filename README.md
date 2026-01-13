# 競馬AI Pro - Next.js × Supabase（完全版）

StreamlitからNext.jsへ完全移行した、競馬予測AI・OCR馬券スキャン・資金管理・回収率分析を統合したフルスタックアプリケーション。

## 🎯 主な機能

### 🤖 AI予測システム（完全実装）
- **5種類の機械学習モデル**:
  1. Logistic Regression
  2. Random Forest
  3. Gradient Boosting
  4. LightGBM (Standard)
  5. LightGBM + Optuna (Ultimate)
- **Ultimate版特徴量**: 過去10走統計・騎手統計・調教師統計（約90特徴量）
- **Optunaハイパーパラメータ最適化**: 全モデル対応（100試行、5分割CV、AUC最大化）
- **一括予測**: 複数レースを同時に予測
- **期待値計算**: オッズ × 予測確率で期待値を自動計算
- **馬券種別推奨**: 単勝・馬連・ワイド・三連複を自動判定

### 📊 データ取得（完全修正）
- **動的APIスクレイピング**: race_list_get_date_list.html APIに直接アクセス
- **5パターンrace_id抽出**: 複数の正規表現で確実に取得
- **フォールバック機構**: API失敗時はメインページへ自動切り替え
- **レート制限対応**: 2-3秒ランダム間隔での安全なスクレイピング
- **自動DB保存**: races, race_results, race_payouts テーブルへ自動保存

### 💰 プロ資金管理
- **ケリー基準**: 最適賭け額を数学的に計算
- **レースレベル判定**: 見送り/通常/勝負の3段階評価
- **動的単価調整**: レースレベルに応じて100円～1,000円に自動調整
- **リスクモード**: 保守的(2%) / バランス(3.5%) / 積極的(5%)
- **シーズン分析**: 春夏秋冬でボーナス/ペナルティを自動適用

### 📸 OCR機能
- **OCR馬券スキャン**: Google Vision APIで馬券を自動認識
- **AI補正**: GPT/Geminiでボックス・フォーメーション対応
- **月間利用制限**: Free 10回 / Premium 1,000回

### 💳 課金システム
- **Stripe統合**: Free/Premiumプラン対応
- **自動アップグレード**: 支払い後、即座にOCR制限が拡張

### 📈 分析ダッシュボード
- **Rechartsグラフ**: 日次収支・馬券タイプ別分布
- **統計サマリー**: 回収率・ROI・勝率を自動計算
- **購入履歴**: 過去の賭け履歴を一覧表示

### 🔒 セキュリティ
- **Supabase Auth**: メール認証
- **RLS**: Row Level Securityで完全なマルチユーザー対応
- **データ分離**: ユーザーごとに完全分離

## 📦 技術スタック

- **Frontend**: Next.js 14, React, TypeScript, Tailwind CSS
- **Backend**: Next.js API Routes
- **Database**: Supabase (PostgreSQL + RLS)
- **Authentication**: Supabase Auth
- **Payment**: Stripe
- **OCR**: Google Vision API
- **AI**: OpenAI GPT-4, Google Gemini
- **Charts**: Recharts

## 🚀 セットアップ

### 1. 依存関係のインストール

```bash
npm install
```

### 2. 環境変数の設定

`.env.local.example`を`.env.local`にコピーして、必要な情報を入力:

```bash
cp .env.local.example .env.local
```

必要な環境変数:
- Supabase URL & Keys
- Stripe Keys & Webhook Secret
- Google Vision API Key
- OpenAI API Key
- Google Gemini API Key

### 3. Supabaseのセットアップ

1. [Supabase](https://supabase.com)でプロジェクトを作成
2. `supabase/schema.sql`を実行してテーブルを作成
3. Authentication > Providers でEmail認証を有効化

### 4. Stripeのセットアップ

1. [Stripe](https://stripe.com)でアカウントを作成
2. Productsで「Premium」プランを作成（月額¥1,980）
3. WebhookエンドポイントにECT`https://your-domain.com/api/stripe/webhook`を追加
4. イベント: `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`

### 5. Google Vision APIのセットアップ

1. [Google Cloud Console](https://console.cloud.google.com)でプロジェクトを作成
2. Vision APIを有効化
3. サービスアカウントを作成してJSONキーをダウンロード
4. `GOOGLE_APPLICATION_CREDENTIALS`にパスを設定

### 6. 開発サーバーの起動

#### 🚀 ワンコマンド起動（推奨）

**方法1: デスクトップショートカット（最も簡単）**

初回のみ実行してショートカットを作成：
```powershell
.\create-desktop-shortcut.ps1
```

デスクトップに「競馬AI Pro.lnk」が作成されます。
以降はダブルクリックで起動！

**方法2: npm scripts**
```bash
npm run dev:all
```

**方法3: PowerShellスクリプト**
```powershell
.\start-dev.ps1
```

**方法4: バッチファイル（Windows）**
```cmd
start-dev.bat
```

上記コマンドで以下のサービスが同時起動します：
- ✅ Next.js開発サーバー (http://localhost:3000)
- ✅ Python APIサーバー (http://localhost:8001)
- ✅ PATH自動リフレッシュ（npmコマンドエラー回避）

#### 個別起動

**Next.jsのみ起動:**
```bash
npm run dev
```

**Python APIのみ起動:**
```bash
npm run dev:api
# または
python scraping_service_ultimate_fast.py
```

http://localhost:8000 で起動（機械学習API）

## 🆕 最新機能（完全版）

### ✅ スクレイピング完全修正
- **動的API対応**: race_list_get_date_list.html APIに直接アクセス
- **5パターン正規表現**: 複数のrace_id抽出パターンで確実に取得
- **フォールバック機構**: API失敗時は自動でメインページへ切り替え
- **レート制限**: 2-3秒ランダム間隔でサーバー負荷を軽減

### ✅ Ultimate版特徴量完全実装
- **過去10走統計** (13特徴量):
  - 平均着順、標準偏差、最高/最低着順
  - 勝率、連対率、複勝率
  - 最近3走の平均着順
  - 一貫性スコア、調子スコア
- **騎手統計** (5特徴量):
  - 直近180日の勝率、連対率、複勝率
  - 平均着順、レース数
- **調教師統計** (4特徴量):
  - 直近180日の勝率、連対率、複勝率
  - レース数

### ✅ 全モデルOptuna最適化
- **Logistic Regression**:
  - C (正則化強度)
  - penalty (l1/l2/elasticnet)
  - solver (liblinear/saga)
  - max_iter, class_weight
- **Random Forest**:
  - n_estimators (50-500)
  - max_depth (3-20)
  - min_samples_split, min_samples_leaf
  - max_features, bootstrap, class_weight
- **Gradient Boosting**:
  - learning_rate (0.001-0.3)
  - n_estimators (50-500)
  - max_depth (3-10)
  - subsample, max_features
- **LightGBM** (既存):
  - 15パラメータを最適化
  - TPESampler + MedianPruner
  - 100試行、5分割CV

## 🧪 統合テスト

```bash
python test_integration.py
```

以下を自動テスト:
1. スクレイピングサービス（ヘルスチェック、race_id取得）
2. Ultimate特徴量計算（モジュールインポート、特徴量生成）
3. 全モデルOptuna最適化（LR, RF, GB）
4. Python API（ヘルスチェック、データ統計）

## 📂 プロジェクト構造

```
keiba-ai-pro/
├── src/
│   ├── app/
│   │   ├── api/
│   │   │   ├── predict/         # AI予測API
│   │   │   ├── ocr/             # OCRスキャンAPI
│   │   │   ├── ai-correct/      # AI補正API
│   │   │   └── stripe/          # Stripe決済API
│   │   ├── auth/
│   │   │   ├── login/           # ログイン画面
│   │   │   └── signup/          # 新規登録画面
│   │   ├── dashboard/           # ダッシュボード
│   │   ├── layout.tsx
│   │   ├── page.tsx             # トップページ
│   │   └── globals.css
│   └── lib/
│       ├── supabase.ts          # Supabaseクライアント
│       ├── stripe.ts            # Stripeクライアント
│       └── keiba-ai.ts          # 競馬AI予測エンジン
├── supabase/
│   └── schema.sql               # データベーススキーマ
├── package.json
├── tsconfig.json
├── tailwind.config.ts
└── next.config.js
```

## 💡 使い方

### 1. アカウント作成
トップページから「新規登録」でアカウントを作成

### 2. AI予測
1. ダッシュボードから「AI予測」を選択
2. 馬のデータを入力（馬番、馬名、騎手、オッズなど）
3. 予測結果と推奨馬券を確認

### 3. OCR馬券スキャン
1. 「OCRスキャン」を選択
2. 馬券の写真をアップロード
3. 自動認識された情報を確認
4. 必要に応じてAI補正を実行

### 4. 資金管理
- 賭け金と払戻しを記録
- 回収率、ROI、勝率を自動計算
- ダッシュボードでグラフ表示

### 5. プレミアムプランへのアップグレード
- 月額¥1,980でOCR月間1,000回に拡張
- ダッシュボードから「アップグレード」を選択

## 🔒 セキュリティ

- Supabase RLS (Row Level Security) で完全なマルチユーザー対応
- すべてのデータはユーザーごとに分離
- Stripe Webhookで安全な課金管理

## 📊 料金プラン

| プラン | 月額 | OCR回数 |
|--------|------|---------|
| Free | ¥0 | 10回 |
| Premium | ¥1,980 | 1,000回 |

## 🐛 トラブルシューティング

### OCRが動作しない
- Google Vision APIの認証情報を確認
- サービスアカウントキーのパスが正しいか確認

### Stripe Webhookが動作しない
- Webhook URLが正しく設定されているか確認
- Webhook署名シークレットが正しいか確認

### データベースエラー
- Supabaseのスキーマが正しく適用されているか確認
- RLSポリシーが有効化されているか確認

## 📝 ライセンス

MIT License

## 👨‍💻 開発者

Created with ❤️ for 競馬ファン
