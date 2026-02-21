## サーバー起動・停止コマンド

> PowerShell プロファイルに登録済み。どのディレクトリからでも実行可能。

### 起動

| コマンド | 内容 |
|---|---|
| `keiba-start` | FastAPI + Next.js を**同時起動**（推奨） |
| `keiba-api` | FastAPI のみ起動（ポート 8000） |
| `keiba-next` | Next.js のみ起動（ポート 3000） |

```powershell
keiba-start   # 両方まとめて起動
```

- `keiba-start` は FastAPI を別ウィンドウで起動後、3秒待って Next.js を起動
- FastAPI：`http://localhost:8000`
- Next.js ：`http://localhost:3000`

### 停止

| コマンド | 内容 |
|---|---|
| `keiba-stop` | FastAPI + Next.js を**同時停止** |

```powershell
keiba-stop    # 両方まとめて停止
```

### 開発時の典型的な流れ

```powershell
# 1. サーバー起動
keiba-start

# 2. コードを書いて動作確認（ブラウザで http://localhost:3000）

# 3. 変更をコミット
gac

# 4. 作業終了時にサーバー停止
keiba-stop
```

### アクセス先まとめ

| サービス | URL |
|---|---|
| Next.js（フロント） | http://localhost:3000 |
| FastAPI（バックエンド） | http://localhost:8000 |
| FastAPI ドキュメント | http://localhost:8000/docs |


# Git 簡易コマンドリファレンス

## ブランチ構成

```
feature/* → deploy → main → release
   開発       確認     統合     本番（Render/Vercel が監視）
```

---

## 日常コマンド早見表

| コマンド | 意味 |
|---|---|
| `git info` | 現在の状態 + 直近10コミットのグラフを表示 |
| `git s` | 変更ファイルの一覧を表示（status の短縮） |
| `git tree` | 全ブランチのコミット履歴をグラフ表示 |
| `git tree -20` | 直近20件に絞って表示 |
| `gac` | 変更を自動検出 → メッセージ生成 → コミット |
| `gac -y` | 確認なしで即コミット |
| `gac -DryRun` | メッセージ生成のみ（コミットしない） |

---

## 開発フロー（毎回の手順）

### ① 新機能を始めるとき

```powershell
git checkout deploy
git pull origin deploy               # 最新を取得
git checkout -b feature/機能名       # feature ブランチを作成
```

### ② 開発中（何回でもOK）

```powershell
# 変更を書いたら
gac          # 自動でメッセージ生成 → 確認 → コミット
gac -y       # 確認なしで即コミット
```

### ③ GitHub に push して PR 作成

```powershell
git push origin feature/機能名
# → GitHub で PR 作成：feature → deploy
# → 「Squash and merge」を選ぶ
```

### ④ deploy → main → release と順番にPR

```powershell
# GitHub 上で PR 作成・マージ（各ステップで CI が自動実行）
# feature → deploy : Squash merge
# deploy  → main   : Squash merge
# main    → release: Merge commit

# release にマージしたらタグを打つ
git checkout release
git pull origin release
git tag v1.x.x
git push origin v1.x.x
# → Render/Vercel が自動デプロイ
```

---

## gac の動作

```
変更ファイルを自動検出（git add -A も自動実行）
        ↓
変更内容を分析してコミットタイプを判定
  feat  : 新ファイル追加
  fix   : バグ修正・エラーワードを含む変更
  ci    : .github/workflows の変更
  docs  : .md ファイルのみの変更
  test  : テストファイルの変更
  chore : ファイル削除のみ
        ↓
コミットメッセージを自動生成
  例: feat: train(ページ)を更新
  例: fix: main(APIサーバー)を更新
  例: ci: ci(CIワークフロー)を追加
        ↓
確認プロンプト
  Y → そのままコミット
  e → メッセージを手動で書き直す
  n → キャンセル
```

---

## ブランチ操作

```powershell
git branch -a                        # 全ブランチ一覧（リモート含む）
git branch -d feature/機能名         # ローカルのブランチ削除（マージ済み）
git branch -D feature/機能名         # ローカルのブランチ強制削除
git checkout -                       # 直前のブランチに戻る
```

---

## 差分確認

```powershell
git diff                             # 未ステージの変更
git diff --staged                    # ステージ済みの変更
git diff main..release               # ブランチ間の差分
git diff main..release --name-only   # ファイル名だけ表示
git show HEAD                        # 直前のコミット内容
git log --oneline src/app/page.tsx   # 特定ファイルの変更履歴
```

---

## コミットの取り消し

```powershell
# push する前（ローカルのみ）
git reset --soft HEAD~1   # 直前のコミットを取り消し（変更は残る）
git reset --hard HEAD~1   # 直前のコミットを完全に取り消し（変更も消える）

# push した後（履歴を残しつつ取り消す）
git revert HEAD           # 取り消しコミットを作成
```

---

## 緊急 hotfix

```powershell
# 本番バグ発生時
git checkout release
git checkout -b feature/hotfix-バグ内容
# 修正後
git push origin feature/hotfix-バグ内容
# → GitHub で PR: feature/hotfix → release に直接マージ
# → main にも反映（cherry-pick）
git checkout main
git cherry-pick <コミットhash>
git push origin main
```

---

## Git Graph（VS Code 拡張）

- `Ctrl+Shift+P` → `Git Graph: View Git Graph`
- 左サイドバーのソース管理アイコン上部にある Git Graph ボタン

---

## status の記号

| 記号 | 意味 |
|---|---|
| `M` | 変更済み（Modified） |
| `A` | 追加・ステージ済み（Added） |
| `D` | 削除（Deleted） |
| `??` | 未追跡の新規ファイル（Untracked） |
| `HEAD ->` | 現在いるブランチ |
| `tag:` | タグが打たれているコミット |

---

