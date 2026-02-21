# 🔧 Scripts

このディレクトリには、プロジェクトで使用する各種スクリプトが含まれています。

## 🚀 起動スクリプト

### start-all (.ps1 / .bat)
**すべてのサーバーを同時起動**
- FastAPI サーバー (ポート8000)
- Next.js 開発サーバー (ポート3000)

```powershell
# PowerShell
.\scripts\start-all.ps1

# バッチファイル
.\scripts\start-all.bat
```

### stop-all.ps1
**すべてのサーバーを停止**
- Node.js (Next.js)
- Python (FastAPI)

```powershell
.\scripts\stop-all.ps1
```

### start-dev (.ps1 / .bat)
**開発モードで起動**
- Next.js 開発サーバーのみ起動

```powershell
# PowerShell
.\scripts\start-dev.ps1

# バッチファイル
.\scripts\start-dev.bat
```

## 🎭 Playwright関連

### start_playwright_server.ps1
Playwrightサーバーを起動

```powershell
.\scripts\start_playwright_server.ps1
```

### stop_playwright_server.ps1
Playwrightサーバーを停止

```powershell
.\scripts\stop_playwright_server.ps1
```

## 🔍 確認・チェック

### check_server.ps1
サーバーの起動状態を確認

```powershell
.\scripts\check_server.ps1
```

## 🖥️ ユーティリティ

### create-desktop-shortcut.ps1
デスクトップショートカットを作成

```powershell
.\scripts\create-desktop-shortcut.ps1
```

## 💡 使い方

### Windows PowerShell
```powershell
cd c:\Users\yuki2\Documents\ws\keiba-ai-pro
.\scripts\<スクリプト名>.ps1
```

### コマンドプロンプト
```cmd
cd c:\Users\yuki2\Documents\ws\keiba-ai-pro
.\scripts\<スクリプト名>.bat
```

## ⚠️ 注意事項

- PowerShellスクリプトの実行には、実行ポリシーの設定が必要な場合があります
- 初回実行時は管理者権限が必要な場合があります
- すでにサーバーが起動している場合は、一度停止してから再起動してください
