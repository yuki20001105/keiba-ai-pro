# 競馬AI Pro - AI競馬予測システム

機械学習による競馬予測・資金管理・データ収集を一気通貫で実現するフルスタックアプリケーション。

## 🎯 主な機能

### 🤖 AI予測システム
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

### 📊 データ取得
- **動的APIスクレイピング**: netkeiba.com race_list APIに直接アクセス
- **5パターンrace_id抽出**: 複数の正規表現で確実に取得
- **フォールバック機構**: API失敗時はメインページへ自動切り替え
- **レート制限対応**: 2-3秒ランダム間隔での安全なスクレイピング
- **自動DB保存**: races, race_results, race_payouts テーブルへ自動保存

### 💰 プロ資金管理
- **ケリー基準**: 最適賭け額を数学的に計算
- **レースレベル判定**: 見送り/通常/勝負の3段階評価
- **動的単価調整**: レースレベルに応じて100円～1,000円に自動調整
- **リスクモード**: 保守的(2%) / バランス(3.5%) / 積極的(5%)

### 📈 分析ダッシュボード
- **Rechartsグラフ**: 日次収支・馬券タイプ別分布
- **統計サマリー**: 回収率・ROI・勝率を自動計算
- **購入履歴**: 過去の賭け履歴を一覧表示

### 🔒 セキュリティ
- **Supabase Auth**: メール認証
- **RLS**: Row Level Securityで完全なマルチユーザー対応

### ✅ 自動システム検証
- **validate_system.py**: 全フロー自動検証スクリプト
- **ヘルスチェック**: Supabase・FastAPI・データベース接続確認
- **フロー検証**: データ収集→訓練→予測の完全テスト
- **自動修復**: エラー検出時の修復案提示
- **詳細レポート**: JSON形式で検証結果を保存
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

## � プロジェクト構造

```
keiba-ai-pro/
├── src/                    # Next.jsアプリケーション
├── python-api/            # FastAPI機械学習サーバー
├── keiba/                 # Python仮想環境（keiba_aiモジュール）
├── scripts/               # 起動・管理スクリプト（⭐新規整理）
├── docs/                  # ドキュメント（⭐カテゴリ別整理）
│   ├── setup/            # セットアップガイド
│   ├── deployment/       # デプロイガイド
│   ├── features/         # 機能仕様
│   ├── development/      # 開発者向け
│   └── reports/          # 実装レポート
├── archive/              # 検証用スクリプト・ログ（⭐整理済み）
├── supabase/             # Supabaseスキーマ・RLSポリシー
├── data/                 # データ・モデル保存
└── public/               # 静的ファイル
```

## 🚀 クイックスタート

### 🎯 1コマンドで起動する方法

#### ⚡ 方法1: npm コマンド（最もシンプル・推奨）

**🚀 起動**
```bash
npm run up
```

**🛑 停止**
```bash
npm run down
```

#### ⚡ 方法2: PowerShellスクリプト
```powershell
# 起動
.\scripts\start-all.ps1

# 停止
.\scripts\stop-all.ps1
```

#### ⚡ 方法3: VS Code キーボードショートカット
VS Codeでプロジェクトを開いて：

**🚀 起動**
```
F7
```

**🛑 停止**
```
Shift+F7
```

両方のサーバーが自動起動/停止します。

#### ⚡ 方法3: バッチファイル
```cmd
.\scripts\start-all.bat
```

### 📱 起動後のアクセス

- **Next.js Frontend**: http://localhost:3000
- **Python API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs

### 🛑 サーバーの停止

#### 方法1: キーボードショートカット（推奨）
```
Ctrl+Shift+X
```

#### 方法2: PowerShellスクリプト
```powershell
.\scripts\stop-all.ps1
```

#### 方法3: 手動停止
```powershell
Get-Process node,python -ErrorAction SilentlyContinue | Stop-Process -Force
```

### 🔍 サーバー状態確認

```powershell
.\scripts\check_server.ps1
```

---

詳細は [docs/setup/QUICKSTART.md](docs/setup/QUICKSTART.md) を参照

### デスクトップショートカット（オプション）

```powershell
# デスクトップに起動アイコンを作成（初回のみ）
.\scripts\create-desktop-shortcut.ps1
```

## 📚 ドキュメント

- 🚀 **[セットアップガイド](docs/setup/QUICKSTART.md)** - 最速起動方法
- ⚡ **[機能一覧](docs/features/FEATURES.md)** - 全機能の説明
- 🚢 **[デプロイガイド](docs/deployment/DEPLOYMENT_COMPLETE_GUIDE.md)** - 本番環境構築
- 🛠️ **[開発ガイド](docs/development/)** - API・スクレイピング・DB設計
- 📊 **[レポート](docs/reports/)** - 最適化・テスト結果

## 🚀 セットアップ（詳細）

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
4. **管理者権限のセットアップ**:
   ```sql
   -- SQL Editorで実行
   ALTER TABLE public.profiles 
   ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'user' 
   CHECK (role IN ('admin', 'user'));
   
   UPDATE public.profiles SET role = 'user' WHERE role IS NULL;
   ```
5. **自分を管理者に設定** (3つの方法):

   **方法1: VS Code Task（最も簡単）**
   ```
   1. Ctrl+Shift+P → "Tasks: Run Task"
   2. "Set Admin (Prompt Email)" を選択
   3. 自分のメールアドレスを入力
   ```

   **方法2: コマンドライン**
   ```powershell
   python set_admin.py your-email@example.com
   ```

   **方法3: Supabase SQL Editor**
   ```sql
   UPDATE public.profiles 
   SET role = 'admin' 
   WHERE email = 'your-email@example.com';
   ```

6. **確認**: ブラウザをリロードして `/home` で「👑 管理者ダッシュボード」カードが表示されることを確認

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

起動方法は [🚀 クイックスタート](#-クイックスタート) セクションを参照してください。

最も簡単な方法：
- **VS Code**: `Ctrl+Shift+B` を押すだけ
- **PowerShell**: `.\scripts\start-all.ps1`
- **バッチ**: `.\scripts\start-all.bat`

詳細は [docs/setup/QUICKSTART.md](docs/setup/QUICKSTART.md) を参照

## 🆕 最新機能

詳細は [docs/features/FEATURES.md](docs/features/FEATURES.md) と [docs/reports/](docs/reports/) を参照してください。

### ハイライト
- ✅ **5種類の機械学習モデル** + Optuna最適化
- ✅ **Ultimate版特徴量**（90+特徴量）
- ✅ **動的スクレイピング** + フォールバック機構
- ✅ **OCR馬券認識** + AI補正
- ✅ **資金管理**（ケリー基準）+ 回収率分析

## 📂 詳細なプロジェクト構造

```
keiba-ai-pro/
├── src/                    # Next.jsアプリケーション
│   ├── app/               # App Router
│   │   ├── api/          # API Routes
│   │   │   ├── predict/  # AI予測API
│   │   │   ├── ocr/      # OCRスキャンAPI
│   │   │   └── stripe/   # Stripe決済API
│   │   ├── auth/         # 認証ページ
│   │   └── dashboard/    # ダッシュボード
│   └── lib/              # ユーティリティ
├── python-api/           # FastAPI機械学習サーバー
├── keiba/                # Python環境（keiba_aiモジュール）
├── scripts/              # 起動・管理スクリプト
├── docs/                 # ドキュメント（カテゴリ別）
├── supabase/             # データベーススキーマ
└── public/               # 静的ファイル
```

詳細な構造とドキュメントは [docs/README.md](docs/README.md) を参照

## 💡 使い方

### 📋 運用フロー全体像

```
① データ収集 → ② モデル学習 → ③ AI予測 → ④ 馬券購入 → ⑤ 結果記録 → ⑥ 統計分析
   (管理者)      (管理者)      (全ユーザー)   (全ユーザー)   (全ユーザー)   (全ユーザー)
```

### 🎯 UIとフローの対応関係

| ステップ | UI画面 | 操作内容 | 使用API | 権限 |
|---------|--------|---------|---------|------|
| **① データ収集** | `/data-collection` | 日付を選択してレースデータをスクレイピング | `POST /api/netkeiba/scrape` | 🔐 管理者のみ |
| **② モデル学習** | `/train` | 5種類のモデル + Optuna最適化を実行 | `POST http://localhost:8000/api/train` | 🔐 管理者のみ |
| **③ AI予測** | `/predict-batch` | race_idを入力して予測実行 | `POST http://localhost:8000/api/predict` | ✅ 全ユーザー |
| **④ 購入推奨** | `/predict-batch` | 期待値・推奨馬券を確認 | 予測APIの結果から自動計算 | ✅ 全ユーザー |
| **⑤ 結果記録** | `/dashboard` (OCR) | 馬券をスキャンして払戻を記録 | `POST /api/ocr/scan` | ✅ 全ユーザー |
| **⑥ 統計分析** | `/dashboard` | 回収率・ROI・日次収支グラフ | Supabase `bets`, `bank_records` | ✅ 全ユーザー |

### 🏠 ホーム画面 (`/home`)

ログイン後のメインメニュー。以下のカードから各機能へアクセス：

- **📊 データ取得** → `/data-collection` (管理者のみ)
- **🧠 モデル学習** → `/train` (管理者のみ)
- **🎯 予測実行** → `/predict-batch`
- **💰 購入推奨** → `/predict-batch`
- **📈 履歴・統計** → `/dashboard`

### 📱 詳細な使い方

#### 1. アカウント作成
- トップページ (`/`) から「新規登録」
- メールアドレスでアカウント作成
- 確認メール内のリンクをクリック
- ログイン後、`/home` にリダイレクト

#### 2. データ収集（管理者のみ）
- `/data-collection` ページを開く
- カレンダーで日付を選択
- 「データ取得開始」をクリック
- Supabaseの `races`, `race_results`, `race_payouts` テーブルに自動保存

#### 3. モデル学習（管理者のみ）
- `/train` ページを開く
- モデルタイプを選択:
  - **Logistic Regression** - 線形ベースライン
  - **Random Forest** - アンサンブル
  - **Gradient Boosting** - 勾配ブースティング
  - **LightGBM Standard** - 高速GBDT
  - **LightGBM + Optuna** - ハイパーパラメータ最適化（推奨）
- Ultimate版ON/OFF を切り替え（90特徴量）
- 「学習開始」をクリック
- AUC, LogLoss, 学習データ数を確認
- モデルは `data/models/` に保存

#### 4. AI予測実行
- `/predict-batch` ページを開く
- race_id を入力（例: `202406050211`）
- 「予測実行」をクリック
- 結果を確認:
  - **勝率**: 1着になる確率
  - **複勝率**: 3着以内に入る確率
  - **期待値**: オッズ × 勝率（> 1.0 なら賭ける価値あり）
  - **信頼度**: high / medium / low
  - **推奨馬券**: 単勝 / 馬連 / ワイド / 三連複

#### 5. 資金管理・購入推奨
- 予測結果画面で「ケリー基準で賭け金計算」
- リスクモードを選択:
  - **保守的**: 資金の 2% まで
  - **バランス**: 資金の 3.5% まで
  - **積極的**: 資金の 5% まで
- レースレベルに応じた単価:
  - **見送り**: 期待値 < 1.0 → ¥0
  - **通常**: 期待値 1.0~1.2 → ¥100
  - **勝負**: 期待値 > 1.2 → ¥1,000
- 推奨馬券・金額を確認して実際に購入

#### 6. OCR馬券スキャン
- `/dashboard` から「OCRスキャン」を選択
- 馬券の写真をアップロード
- Google Vision API で自動認識
- 必要に応じて GPT/Gemini で補正（ボックス・フォーメーション対応）
- 払戻金を自動計算
- `bets` テーブルに保存

#### 7. 履歴・統計確認
- `/dashboard` で以下を確認:
  - **回収率**: (総払戻 / 総賭け金) × 100
  - **ROI**: (総利益 / 総賭け金) × 100
  - **勝率**: (的中回数 / 総賭け回数) × 100
  - **日次収支グラフ**: Rechartsで可視化
  - **馬券タイプ別分布**: 単勝/馬連/ワイド/三連複
  - **購入履歴一覧**: 最新10件

#### 8. プレミアムプランへのアップグレード
- `/dashboard` から「アップグレード」
- 月額¥1,980でOCR月間1,000回に拡張
- Stripe決済画面へ遷移
- 支払い完了後、即座に制限解除

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
� さらに詳しく

- 📖 **[ドキュメント一覧](docs/README.md)** - すべてのドキュメント
- 🚀 **[クイックスタート](docs/setup/QUICKSTART.md)** - 最速起動方法
- ⚡ **[機能詳細](docs/features/FEATURES.md)** - 全機能の説明
- 🚢 **[デプロイガイド](docs/deployment/DEPLOYMENT_COMPLETE_GUIDE.md)** - 本番環境構築
- 🛠️ **[開発ガイド](docs/development/)** - API・スクレイピング・DB設計

## �