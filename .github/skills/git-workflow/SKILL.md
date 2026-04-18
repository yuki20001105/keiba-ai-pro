# Git ワークフロー スキル — keiba-ai-pro

## ブランチ戦略

```
develop  ← 日々の開発はここで行う
  ↓ 機能完成・あるいは一定のまとまりができたら
main     ← 動作確認済みの安定コード
  ↓ GitHub PR でマージ
release  ← 本番リリース（v2, v3, ... タグを打つ）
```

| ブランチ | 役割 | タグ |
|---|---|---|
| `develop` | 日常開発・実験 | なし |
| `main` | 動作確認済み安定版 | なし |
| `release` | 本番リリース | `v2`, `v3`, ... |

---

## 日常の開発フロー（develop ブランチ）

### 1. ブランチを確認してからコードを書く

```powershell
git branch              # * develop になっていることを確認
git status              # 変更ファイルの一覧
git diff                # 未ステージの差分
```

### 2. コミット

```powershell
# 変更をすべてステージ
git add -A

# 特定ファイルだけステージ
git add python-api/routers/predict.py src/app/predict-batch/page.tsx

# コミット
git commit -m "feat: 新機能の説明"

# リモートに反映
git push origin develop
```

### 3. コミットメッセージのルール

| プレフィックス | 使う場面 |
|---|---|
| `feat:` | 新機能追加 |
| `fix:` | バグ修正 |
| `refactor:` | リファクタリング（動作変更なし） |
| `chore:` | 設定・依存関係・ツール変更 |
| `docs:` | ドキュメント更新 |
| `ci:` | GitHub Actions ワークフロー変更 |
| `test:` | テスト追加・修正 |

**例:**
```
feat: 予測バッチページに日付フィルターを追加
fix: オッズ0.0がfalsy判定されるバグを修正
chore: .gitignore に test-results/ を追加
```

---

## リリースフロー（機能完成時）

```powershell
# 1. main にマージ
git checkout main
git merge develop --ff-only
git push origin main

# 2. GitHub PR を作成: main → release
#    https://github.com/yuki20001105/keiba-ai-pro/compare/release...main

# 3. PR マージ後、タグを打つ（releaseブランチで）
git checkout release
git pull origin release
git tag -a v3 -m "v3: 機能の説明"
git push origin v3

# 4. 開発に戻る
git checkout develop
```

> タグを push すると GitHub Actions（`release.yml`）が自動で **GitHub Releases ページ**を作成する。

---

## よく使うコマンド一覧

```powershell
git status                     # 変更ファイルの確認
git log --oneline -10          # 最近10件のコミット履歴
git diff                       # 未ステージの差分
git diff --staged              # ステージ済みの差分
git branch                     # ブランチ一覧（* が現在）
git branch -a                  # リモート含む全ブランチ一覧

git checkout develop           # develop に切り替え
git checkout main              # main に切り替え

git add -A                     # 全変更をステージ
git commit -m "feat: ..."      # コミット
git push origin develop        # develop を GitHub に反映

git log --oneline main..develop  # develop のみにあるコミット確認
git tag -l                     # タグ一覧
```

---

## GitHub Actions との連携

| 操作 | 自動実行される Action |
|---|---|
| `develop` に push | **なし**（開発中は静かに） |
| `develop → main` PR 作成 | `ci.yml` → フロントビルド + バックエンドimportチェック |
| `main → release` PR 作成 | `ci.yml` → 同上 |
| `release` に push（PRマージ）| `release.yml` → リリース通知ログ |
| `v*` タグを push | `release.yml` → **GitHub Releases 自動作成** |
| 毎日 JST 7:00 | `daily-scrape.yml` → スクレイプ自動実行 |

---

## 注意事項

- `git push --force` は**禁止**（`release` / `main` ブランチへは特に厳禁）
- `main` / `release` への直接 commit は避け、必ず `develop` 経由にする
- コミット前に `git status` で意図しないファイルが含まれていないか確認すること
