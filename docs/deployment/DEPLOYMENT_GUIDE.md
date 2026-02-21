# 🚀 デプロイメントガイド

## 📋 プロジェクト構成

- **フロントエンド:** Next.js → Vercel (https://keiba-ai-pro.vercel.app)
- **バックエンド:** FastAPI → Railway (https://keibaai-tsuda-production.up.railway.app)
- **GitHub:** https://github.com/yuki20001105/keiba-ai-pro.git

---

## 🔄 今後の更新手順

### 1️⃣ フロントエンド（Next.js）を変更した場合

```powershell
# 1. コードを修正する (src/ 配下のファイル)
# 例: src/app/page.tsx, src/components/*, src/lib/* など

# 2. ローカルで動作確認
npm run dev  # http://localhost:3000 で確認

# 3. GitHubにプッシュ
git add .
git commit -m "フロントエンド: 〇〇機能を追加"
git push origin main

# 4. 自動デプロイ
# Vercelが自動的に検知して1-2分でデプロイ完了
# https://keiba-ai-pro.vercel.app で確認
```

**✅ 家族への共有:** 変更なし（同じURL）
- https://keiba-ai-pro.vercel.app

---

### 2️⃣ バックエンド（FastAPI）を変更した場合

```powershell
# 1. コードを修正する (python-api/ 配下のファイル)
# 例: python-api/main.py, keiba/keiba_ai/* など

# 2. ローカルで動作確認 (オプション)
cd python-api
$env:PYTHONPATH="C:\Users\yuki2\Documents\ws\keiba-ai-pro"
C:\Users\yuki2\.pyenv\pyenv-win\versions\3.10.11\python.exe -m uvicorn main:app --reload

# 3. GitHubにプッシュ
cd ..
git add .
git commit -m "バックエンド: 〇〇APIを追加"
git push origin main

# 4. 自動デプロイ
# Railwayが自動的に検知して2-3分でデプロイ完了
# Railway Dashboard (https://railway.app) で状態確認可能
```

**✅ 家族への共有:** 変更なし（フロントエンドURLは同じ）
- https://keiba-ai-pro.vercel.app

---

### 3️⃣ 両方を変更した場合

```powershell
# 1. フロントエンドとバックエンド両方を修正

# 2. GitHubに一度にプッシュ
git add .
git commit -m "フロント・バックエンド: 〇〇機能を実装"
git push origin main

# 3. 自動デプロイ
# VercelとRailway両方が自動デプロイ（並行処理）
# 2-3分で両方完了
```

---

## 🎯 重要ポイント

### ✅ 自動デプロイの仕組み

1. **GitHub に push すると:**
   - Vercel が main ブランチを監視 → 自動デプロイ
   - Railway が main ブランチを監視 → 自動デプロイ

2. **手動操作は不要:**
   - `vercel deploy` コマンド不要
   - `railway up` コマンド不要
   - ブラウザで再デプロイボタンを押す必要なし

3. **デプロイ状態の確認:**
   - **Vercel:** https://vercel.com/yuki20001105s-projects/keiba-ai-pro/deployments
   - **Railway:** https://railway.com/project/41bb6e2c-a2f4-4a92-8064-6aa600850542

---

## 📱 家族への共有方法

### 初回共有（今回）

```
競馬AI予測アプリが完成しました！

📱 アクセスURL:
https://keiba-ai-pro.vercel.app

💡 使い方:
1. 上記URLをブラウザで開く
2. アカウント作成（メールアドレス登録）
3. ログイン後、予測機能が使えます

📲 スマホでアプリ化:
1. ブラウザで開く
2. Safari: 共有 → ホーム画面に追加
3. Chrome: メニュー → ホーム画面に追加
```

### 更新後の通知（オプション）

```
アプリを更新しました！

🆕 更新内容:
- 〇〇機能を追加
- △△を改善

📱 使い方:
https://keiba-ai-pro.vercel.app
（URLは変わりません。アクセスするだけで最新版が表示されます）
```

---

## 🔍 トラブルシューティング

### デプロイが失敗した場合

#### Vercel の場合:
1. https://vercel.com にログイン
2. keiba-ai-pro プロジェクトを選択
3. Deployments タブで失敗したデプロイをクリック
4. エラーログを確認
5. 問題を修正して再度 `git push`

#### Railway の場合:
1. https://railway.app にログイン
2. keibaAI tsuda プロジェクトを選択
3. Deployments タブで失敗したデプロイをクリック
4. Deploy Logs でエラーを確認
5. 問題を修正して再度 `git push`

---

## 📊 デプロイ履歴の確認

```powershell
# 最近のコミットを確認
git log --oneline -10

# 特定のコミットの変更内容を確認
git show <commit-hash>

# 現在のブランチとリモート状態を確認
git status
git remote -v
```

---

## 🎉 まとめ

**通常の更新フロー:**
```
コード修正 → git push → 自動デプロイ → 完了！
```

**家族に伝えること:**
- URLは常に https://keiba-ai-pro.vercel.app
- 更新時もURLは変わらない
- ブラウザを開くだけで最新版が使える
- アプリ化すればスマホアプリのように使える

---

## 📚 参考リンク

- **Vercel ダッシュボード:** https://vercel.com/yuki20001105s-projects/keiba-ai-pro
- **Railway ダッシュボード:** https://railway.com/project/41bb6e2c-a2f4-4a92-8064-6aa600850542
- **GitHub リポジトリ:** https://github.com/yuki20001105/keiba-ai-pro
- **本番サイト:** https://keiba-ai-pro.vercel.app

---

**作成日:** 2026-01-12  
**最終更新:** 2026-01-12
