# SKILL: 実環境E2Eインテグレーションテスト

## 概要

モックを一切使わず、実際に起動している Next.js + FastAPI サーバーに対して Playwright でブラウザ操作を行い、**データ取得 → モデル学習 → 予測実行 → 統計確認** のフルワークフローを検証するスキル。

---

## 適用条件（このスキルを読み込むべき場面）

- 「モックなし」「実際の動作」「本番同等環境」「本当に動くか確認」というキーワードがある
- データ収集・学習・予測をまとめてE2Eで検証したい
- 既存のモックベーステストでは再現できないバグを調査したい

---

## ファイル構成

| ファイル | 説明 |
|---|---|
| `e2e/real-workflow.spec.ts` | 本番同等E2Eテスト本体（モックなし） |
| `e2e/helpers/mock-api.ts` | 既存モックヘルパー（このスキルでは使用しない） |
| `.env.test` | テスト用認証情報（git管理外） |

---

## 前提条件の確認と準備

### 1. サーバー起動

```bash
# FastAPI (port 8000)
cd python-api && .venv\Scripts\python.exe main.py

# Next.js (port 3000) — 別ターミナルで
npm run dev
```

または VS Code タスク「Start All Servers」を実行。

### 2. 環境変数の設定

プロジェクトルートに `.env.test` を作成（存在しない場合）:

```env
E2E_EMAIL=your@email.com
E2E_PASSWORD=yourpassword
```

> **注意**: このファイルを `.gitignore` に追加し、リポジトリにコミットしないこと。

Playwright は `.env.test` を自動ロードしないため、コマンドで明示的に渡す:

```bash
# Windows PowerShell
$env:E2E_EMAIL="your@email.com"; $env:E2E_PASSWORD="yourpassword"; npx playwright test e2e/real-workflow.spec.ts --reporter=list
```

または `playwright.config.ts` に dotenv ロードを追加:

```ts
// playwright.config.ts の先頭に追加
import dotenv from 'dotenv'
dotenv.config({ path: '.env.test' })
```

### 3. ポート確認

```bash
netstat -ano | findstr ":3000 :8000"
```

---

## テスト実行コマンド

### フルフロー（全5ステップ）

```bash
npx playwright test e2e/real-workflow.spec.ts --reporter=list
```

### ヘッドフルモード（ブラウザを表示）

```bash
npx playwright test e2e/real-workflow.spec.ts --reporter=list --headed
```

### 特定ステップのみ実行

```bash
# Step2（データ取得）のみ
npx playwright test e2e/real-workflow.spec.ts -g "Step2"

# Step3（モデル学習）のみ
npx playwright test e2e/real-workflow.spec.ts -g "Step3"
```

---

## テストの各ステップ詳細

### Step 1: ログイン

- `/login` へ遷移
- Supabase メールアドレス・パスワードで認証
- `/home` へリダイレクト確認

### Step 2: データ取得（起動確認モード）

- `/data-collection` へ遷移
- ローカルAPI ステータスが表示されるまで待機
- API が「停止中」の場合は `test.skip()` で自動スキップ
- 開始年月: `2015-01`、終了年月: `2016-03`
- 「強制再取得」チェックボックスをON
- confirm ダイアログを自動承認してから「取得開始」クリック
- 「取得中...」ボタンが表示されることを確認（起動確認のみ）
- 完全な取得完了を確認したい場合はコメントアウトを解除

### Step 3: モデル学習（起動確認モード）

- `/train` へ遷移
- 予測ターゲット: `speed_deviation`（速度偏差・回帰）
- モデルタイプ: `lightgbm`
- 詳細設定を開く → Optuna トグルON
- 試行回数スライダー: `100`（`nativeInputValueSetter` + `InputEvent('input')` + `Event('change')` で React state を更新）
- `waitForFunction` で `試行回数: 100` がページに出るまで待機
- 「学習開始」クリック
- 「学習中」インジケーター表示を確認（起動確認のみ・完了は待たない）
- 完全な学習完了を確認したい場合はコメントアウトを解除

### Step 4: 予測実行

- `/predict-batch` へ遷移
- 「レース一覧を取得」クリック
- 当日レースがない場合はスキップ（テストをパスとして扱う）
- レースを1件選択 → 「予測実行」クリック
- 予測結果（確率・オッズ・Kelly基準）が表示されることを確認

### Step 5: 統計確認

- `/dashboard` へ遷移
- 統計数値（レース数・モデル数）が表示されることを確認

---

## タイムアウト設定

各テストは `test.setTimeout()` で個別にタイムアウトを設定している:

| ステップ | タイムアウト | 根拠 |
|---|---|---|
| Step1 ログイン | 30秒 | 標準認証時間 |
| Step2 データ取得 | 60分 | 14ヶ月 × ~4分/月 + 余裕 |
| Step3 モデル学習 | 120分 | 100試行 × 5fold × ~0.4分 + 余裕 |
| Step4 予測実行 | 5分 | 当日レーススクレイプ |
| Step5 統計確認 | 1分 | API応答時間 |

> **INV-05 準拠**: UI→API は 180,000ms 以上、Next.js→FastAPI は 300,000ms 以上必須。

---

## よくあるエラーと対処法

### ログインに失敗する（401 Unauthorized）

```
原因: E2E_EMAIL / E2E_PASSWORD が未設定
対処: .env.test を確認し、環境変数を正しく設定する
```

### ローカルAPI「停止中」のままになる

```
原因: FastAPI (port 8000) が未起動
対処: VS Code タスク「Start FastAPI」を実行
     または: cd python-api && .venv\Scripts\python.exe main.py
```

### スクレイプが途中でタイムアウトする

```
原因: IP ブロックまたはネットワーク遅延
対処: VPN を切断/変更してから再試行
     INV-07 のインターバル（1.0秒）は変更しないこと
```

### `confirm` ダイアログが閉じない

```
原因: page.on('dialog') の登録前にクリックした
対処: dialog ハンドラを startBtn.click() より前に登録すること（実装済み）
```

### 試行回数が 50 のまま変わらない

```
原因: range input の値変更が React state に反映されない
対処: dispatchEvent('input') + dispatchEvent('change') の両方を発火（実装済み）
```

### 学習完了トーストが表示されない（speed_deviation）

```
原因1: 速度偏差の回帰モデルはAUCではなくRMSEを返す — トースト文言が異なる可能性
対処1: python-api/routers/ml_train.py の完了レスポンスを確認し、
       テストのローカライズ文字列を修正する

原因2: 学習データが不足（2015-2016年のデータのみでは特徴量が生成できない）
対処2: keiba/keiba_ai/feature_engineering.py の expanding window 計算の
       min_periods を確認する
```

---

## 実装上の制約（守ること）

### INV-04 並列処理禁止

`CONCURRENCY = 1` を維持。テスト内で複数ジョブを同時起動しないこと。

### INV-07 スクレイピングインターバル

自動テストがインターバルをバイパスしないよう注意。
各リクエスト間 1.0 秒のスリープは `python-api/scraping/` 側で実装されている。

### モックテストと分離

このテストファイル (`real-workflow.spec.ts`) は既存のモックベーステスト群と完全に分離する。
`e2e/helpers/mock-api.ts` は**使用しない**。

---

## 既存モックテストとの共存

```bash
# モックテストのみ実行（通常のCI向け）
npx playwright test --ignore e2e/real-workflow.spec.ts

# 実環境テストのみ
npx playwright test e2e/real-workflow.spec.ts

# 全テスト（CI環境では実環境テストをスキップ推奨）
npx playwright test
```

---

## 保守ポイント

- ログインのセレクタ変更時: `getByLabel('メールアドレス')` → ラベル文字列で検索
- Optuna トグルのセレクタ: `button[class*="rounded-full"]` — CSS クラスが変わったら修正
- トースト文言が変わったら: `getByText(/学習完了/)` の正規表現を更新
- 完了判定: `page.waitForURL('**/home')` — リダイレクト先が変わったら更新

---

## 参照ファイル

| ファイル | 用途 |
|---|---|
| `e2e/real-workflow.spec.ts` | テスト実装 |
| `src/app/login/page.tsx` | ログインUI |
| `src/app/data-collection/page.tsx` | データ取得UI |
| `src/app/train/page.tsx` | モデル学習UI |
| `src/app/predict-batch/page.tsx` | 予測実行UI |
| `python-api/routers/scrape.py` | スクレイプAPI |
| `python-api/routers/ml_train.py` | 学習API |
| `docs/specs/SYSTEM.md` | 不変条件 INV-01〜INV-08 |
