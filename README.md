# 競馬AI Pro - Next.js × Supabase

StreamlitからNext.jsへ完全移行した、競馬予測AI・OCR馬券スキャン・資金管理・回収率分析を統合したフルスタックアプリケーション。

## 🎯 主な機能

### 🤖 AI予測システム
- **機械学習予測**: ランダムフォレスト風のスコアリングモデル
- **一括予測**: 複数レースを同時に予測
- **期待値計算**: オッズ × 予測確率で期待値を自動計算
- **馬券種別推奨**: 単勝・馬連・ワイド・三連複を自動判定

### 📊 データ取得
- **netkeibaスクレイピング**: 開催日・レースID・結果を自動取得
- **レート制限対応**: 3秒間隔での安全なスクレイピング
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

```bash
npm run dev
```

http://localhost:3000 でアクセス可能

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
