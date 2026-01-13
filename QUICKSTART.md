# 🚀 競馬AI Pro - クイックスタートガイド

## ⚡ 最速起動方法（3ステップ）

### 1️⃣ デスクトップショートカットを作成（初回のみ）

PowerShellで以下を実行：

```powershell
.\create-desktop-shortcut.ps1
```

デスクトップに「**競馬AI Pro.lnk**」が作成されます。

### 2️⃣ ダブルクリックで起動

デスクトップの「**競馬AI Pro**」アイコンをダブルクリック！

以下が自動起動します：
- ✅ Next.js開発サーバー (http://localhost:3000)
- ✅ Python APIサーバー (http://localhost:8001)  
- ✅ PATH自動リフレッシュ（npmエラー回避）

### 3️⃣ ブラウザでアクセス

自動的にブラウザが開きます。開かない場合は：

**http://localhost:3000** にアクセス

---

## 📝 その他の起動方法

### 方法A: PowerShellスクリプト

```powershell
.\start-dev.ps1
```

### 方法B: バッチファイル（Windows）

```cmd
start-dev.bat
```

### 方法C: npm scripts

```bash
npm run dev:all
```

---

## 🛠️ トラブルシューティング

### npm が見つからない

→ **解決済み！** start-dev.ps1 と start-dev.bat が自動的にPATHをリフレッシュします。

### ポートが使用中

```powershell
# すべてのサーバーを停止
Stop-Process -Name node,python -Force

# 再起動
.\start-dev.ps1
```

### スクリプト実行ポリシーエラー

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

---

## 📊 データ収集の開始

1. http://localhost:3000/data-collection にアクセス
2. 日付を選択（例: 2024-01-13）
3. 「レース一覧取得」ボタンをクリック
4. レースを選択して「スクレイピング開始」

---

## 🔗 主要URL

| サービス | URL | 説明 |
|---------|-----|------|
| **Next.js Frontend** | http://localhost:3000 | メインアプリケーション |
| **Python API** | http://localhost:8001 | スクレイピングAPI |
| **API Docs** | http://localhost:8001/docs | FastAPI自動ドキュメント |
| **データ収集** | http://localhost:3000/data-collection | netkeiba.comスクレイピング |
| **AI予測** | http://localhost:3000/predict | 機械学習予測 |
| **分析** | http://localhost:3000/analysis | 収支分析 |

---

## 💡 便利なコマンド

```powershell
# 両方のサーバーを停止
Stop-Process -Name node,python -Force

# Next.jsのみ起動
npm run dev

# Python APIのみ起動
npm run dev:api

# ビルド（本番用）
npm run build

# 本番モードで起動
npm start
```

---

## ✨ 次のステップ

1. ✅ **データ収集**: http://localhost:3000/data-collection
   - 過去のレースデータを取得
   - Supabaseに自動保存

2. ✅ **AI予測**: http://localhost:3000/predict
   - 5種類のモデルで予測
   - Optuna最適化

3. ✅ **分析**: http://localhost:3000/analysis
   - 回収率・ROI分析
   - グラフ可視化

4. ✅ **OCRスキャン**: http://localhost:3000/ocr
   - 馬券画像を自動認識
   - 購入履歴に自動登録

---

## 🎯 よくある質問

**Q: 初回起動が遅い**  
A: 初回はNext.jsのコンパイルに時間がかかります。2回目以降は高速です。

**Q: データベースエラー**  
A: Supabaseで `supabase/setup_scraping_tables.sql` を実行してください。

**Q: スクレイピングが失敗する**  
A: undetected-chromedriver がインストールされているか確認：
```bash
pip install undetected-chromedriver
```

**Q: デスクトップショートカットが動かない**  
A: PowerShellの実行ポリシーを確認：
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

---

**🎉 準備完了！ 楽しい競馬AI開発を！**
