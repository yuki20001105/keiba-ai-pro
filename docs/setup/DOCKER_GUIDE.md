# Docker 環境構築ガイド — keiba-ai-pro

このドキュメントでは、keiba-ai-pro を Docker で完全に再現できる環境として管理する方法を説明します。  
**Windows 11 + WSL2 + Docker Engine（無償）** を使用します。Docker Desktop は 2021年以降有償化されているため使用しません。

参考: [【図解】Windows11でWSL2＋DockerによるPython開発環境を構築する手順](https://zenn.dev/stockdatalab/articles/20250519_tech_env_docker)

---

## 全体像

```
Windows 11
└── WSL2 (Ubuntu 24.04)
    └── Docker Engine
        └── docker-compose
            ├── fastapi コンテナ (:8000)  ← python-api/
            └── nextjs  コンテナ (:3000)  ← src/
                    ↓ volume mount
            keiba/data/ (SQLite DB)
```

外部サービス: Supabase（認証・購入記録）

---

## ファイル構成（Docker 関連）

```
keiba-ai-pro/
├── Dockerfile                  ← FastAPI 用（Python 3.11.9-slim ピン済み）
├── Dockerfile.nextjs           ← Next.js 用（下記を参照して作成）
├── docker-compose.yml          ← 開発用（下記を参照して作成）
├── docker-compose.prod.yml     ← 本番用オーバーライド
├── python-api/
│   ├── requirements.txt        ← >= 指定（人間が読む用）
│   └── requirements-lock.txt   ← == 完全ピン（Dockerビルドで優先使用）★
├── .env                        ← 実際の環境変数（.gitignore 済み）
├── .env.example                ← テンプレート
└── .dockerignore               ← 下記を参照して作成
```

---

## Phase 1 — WSL2 のセットアップ（初回のみ）

### 1-1. WSL2 を有効化する

コントロールパネル → プログラム → **「Windows の機能の有効化または無効化」** を開き、  
「Linux 用 Windows サブシステム」「仮想マシン プラットフォーム」にチェックを入れて再起動。

### 1-2. Ubuntu 24.04 をインストールする

PowerShell（管理者）で実行:

```powershell
wsl --install         # 実行後 PC を再起動
wsl --update
wsl --install -d Ubuntu-24.04
```

インストール後に Ubuntu が起動してユーザー名・パスワードを設定。  
設定後は `exit` で一度閉じる。

### 1-3. WSL バージョンを確認する

```powershell
wsl -l -v
```

`Ubuntu-24.04` が `VERSION 2` で `*`（デフォルト）になっていれば OK。  
なっていない場合:

```powershell
wsl --set-default Ubuntu-24.04
wsl --set-default-version 2
```

---

## Phase 2 — Docker Engine を Ubuntu にインストール（初回のみ）

スタートメニューから **「Ubuntu 24.04 LTS」** の Bash を起動して実行。

### 2-1. apt リポジトリを登録する

```bash
sudo apt-get update
sudo apt-get install ca-certificates curl

sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
```

### 2-2. Docker Engine をインストールして確認する

```bash
sudo apt-get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# 動作確認（"Hello from Docker!" が出れば成功）
sudo docker run hello-world
```

### 2-3. sudo なしで docker を使えるようにする

```bash
sudo usermod -aG docker $USER
newgrp docker

# sudo なしで動作確認
docker run hello-world
```

### 2-4. Git をインストールする

```bash
sudo apt install git
```

---

## Phase 3 — VS Code のセットアップ（初回のみ）

### 3-1. 拡張機能をインストールする

Windows 側の VS Code に以下をインストール:

- **WSL** （Microsoft）
- **Dev Containers** （Microsoft）
- **Docker** （Microsoft）

### 3-2. VS Code を Ubuntu 側から起動する

Ubuntu Bash で実行:

```bash
code .
```

初回は自動的に VS Code Server が Ubuntu にインストールされます。  
左下に `WSL: Ubuntu-24.04` と表示されれば接続成功。

---

## Phase 4 — プロジェクトの準備

### 4-1. Ubuntu 内にリポジトリを clone する

```bash
# Ubuntu Bash 内で実行（Windows側のパスではなく、Ubuntu のホームに置く）
cd ~
git clone https://github.com/your-org/keiba-ai-pro.git
cd keiba-ai-pro
```

> **⚠ Windows 側のディレクトリ（`/mnt/c/...`）に置くと I/O が低速になります。**  
> Ubuntu のホームディレクトリ（`~/`）に clone することを推奨します。

### 4-2. 設定ファイルと SQLite DB を移行する（既存環境がある場合）

元マシン（Windows）から `.env` と DB を転送:

```powershell
# .env を転送
Copy-Item C:\Users\yuki2\Documents\ws\keiba-ai-pro\.env `
  \\wsl$\Ubuntu-24.04\home\<username>\keiba-ai-pro\.env

# SQLite DB を転送
Copy-Item C:\Users\yuki2\Documents\ws\keiba-ai-pro\keiba\data\keiba_ultimate.db `
  \\wsl$\Ubuntu-24.04\home\<username>\keiba-ai-pro\keiba\data\keiba_ultimate.db
```

DB がない場合はスクレイピングで再構築できますが、数時間〜数日かかります。

---

## Phase 5 — Docker ファイルの準備

### 5-1. .env ファイルを作成する

`.env` はすでにプロジェクトルートに作成済みです（git 管理外）。  
別マシンに移行する場合は、元マシンの `.env` を **そのままコピー**してください。

```powershell
# Windows → Ubuntu への転送例
Copy-Item C:\Users\yuki2\Documents\ws\keiba-ai-pro\.env \\wsl$\Ubuntu-24.04\home\<username>\keiba-ai-pro\.env
```

`.env` に設定されているキーの種類（実際の値は `.env` ファイル本体を参照）:

| キー | 用途 |
|---|---|
| `SUPABASE_URL` | Supabase プロジェクト URL |
| `SUPABASE_SERVICE_KEY` | Service Role Key（FastAPI 用）|
| `SUPABASE_ANON_KEY` | Anon Key（FastAPI 用）|
| `JWT_SECRET_KEY` | JWT 署名キー（自動生成済み）|
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase URL（Next.js / ブラウザ用）|
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Anon Key（Next.js / ブラウザ用）|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000`（ブラウザ → FastAPI）|
| `ML_API_URL` | `http://fastapi:8000`（コンテナ内部通信）★ |
| `SCRAPE_API_URL` | `http://fastapi:8000`（コンテナ内部通信）★ |

> **★ コンテナ内では `localhost:8000` は Next.js 自身を指します。**  
> `ML_API_URL` / `SCRAPE_API_URL` は必ず `http://fastapi:8000`（サービス名）にしてください。

### 5-2. .dockerignore を作成する

```
# .dockerignore
**/.venv
**/node_modules
**/__pycache__
**/*.pyc
**/*.pyo
.git
.github
.env
.env.local
*.log
data/logs/
python-api/logs/
python-api/tests/
e2e/
test-results/
docs/
README.md
```

### 5-3. Dockerfile（FastAPI 用）の確認

`Dockerfile` はすでに更新済みです。主な設定:

```dockerfile
FROM python:3.11.9-slim          # パッチバージョンまで固定

# Playwright (chromium) に必要な OS ライブラリ
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 ...

# requirements-lock.txt が存在すれば優先使用
RUN if [ -f python-api/requirements-lock.txt ]; then \
      pip install --no-cache-dir -r python-api/requirements-lock.txt; \
    else \
      pip install --no-cache-dir -r python-api/requirements.txt; \
    fi

# Playwright chromium をインストール
RUN python -m playwright install chromium
```

### 5-4. Dockerfile.nextjs を新規作成する

```dockerfile
# Dockerfile.nextjs
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci                         # package-lock.json で完全ピン

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production

COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]
```

standalone モードを有効にするために `next.config.js` に追記:

```js
const nextConfig = {
  output: 'standalone',  // ← 追加
  // ... 既存設定 ...
}
```

> standalone を使わない場合は最終ステージを `CMD ["npm", "start"]` に変更してください。

### 5-5. docker-compose.yml を作成する

```yaml
# docker-compose.yml（開発用）
services:
  fastapi:
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "8000:8000"
    env_file:
      - .env
    environment:
      - PYTHONUNBUFFERED=1
      - PYTHONPATH=/app/keiba
      - TZ=Asia/Tokyo
    volumes:
      - ./keiba/data:/app/keiba/data               # SQLite DB 永続化 ★必須
      - ./python-api/models:/app/python-api/models  # 学習済みモデル
      - ./data/logs:/app/python-api/logs            # API ログ
    restart: unless-stopped

  nextjs:
    build:
      context: .
      dockerfile: Dockerfile.nextjs
    ports:
      - "3000:3000"
    env_file:
      - .env
    environment:
      - NEXT_PUBLIC_API_URL=http://localhost:8000   # ブラウザから
      - ML_API_URL=http://fastapi:8000              # コンテナ内部
      - SCRAPE_API_URL=http://fastapi:8000
      - TZ=Asia/Tokyo
    depends_on:
      - fastapi
    restart: unless-stopped
```

---

## Phase 6 — 起動・動作確認

### 起動

```bash
# Ubuntu Bash または VS Code のターミナル（WSL接続状態）で実行
cd ~/keiba-ai-pro

# 初回 or コード変更後（イメージを再ビルド）
docker compose up --build

# 通常起動（バックグラウンド）
docker compose up -d
```

### 動作確認

```bash
# FastAPI ヘルスチェック
curl http://localhost:8000/

# DB レコード数確認
docker compose exec fastapi python -c "
import sqlite3
conn = sqlite3.connect('/app/keiba/data/keiba_ultimate.db')
print('races:', conn.execute('SELECT COUNT(*) FROM races').fetchone())
"

# Playwright が動くか確認
docker compose exec fastapi python -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    print('Playwright OK:', b.version)
    b.close()
"
```

ブラウザで `http://localhost:3000` にアクセスして UI が表示されれば完了。

### 停止

```bash
docker compose down
```

### WSL ごと終了する（作業終了時）

```powershell
# PowerShell で実行
docker compose down   # 先にコンテナを停止
wsl --shutdown        # WSL をシャットダウン
```

---

## ログ確認・操作コマンド

```bash
docker compose logs -f           # 全サービスのログ
docker compose logs -f fastapi   # FastAPI のみ
docker compose logs -f nextjs    # Next.js のみ

docker compose exec fastapi bash  # FastAPI コンテナ内に入る
docker ps -a                      # コンテナ一覧
docker images                     # イメージ一覧
```

---

## データの永続化まとめ

| ホストパス（Ubuntu内） | コンテナパス | git管理 | 用途 |
|---|---|---|---|
| `./keiba/data/` | `/app/keiba/data/` | **対象外** | SQLite DB、スクレイプ済みHTML |
| `./python-api/models/` | `/app/python-api/models/` | 対象内 | LightGBM モデル (.joblib) |
| `./data/logs/` | `/app/python-api/logs/` | 対象外 | API ログ |

```bash
# DB バックアップ（Ubuntu Bash）
cp keiba/data/keiba_ultimate.db keiba/data/keiba_ultimate.$(date +%Y%m%d).bak
```

---

## 再現性のポイント

| 要素 | 方法 |
|---|---|
| Python バージョン | `python:3.11.9-slim`（パッチまで固定）|
| Python パッケージ | `requirements-lock.txt`（pip freeze で全パッケージ `==` ピン）|
| Node.js パッケージ | `package-lock.json`（`npm ci` で厳密インストール）|
| SQLite DB | ボリュームマウント（コンテナ削除時も消えない）|
| 学習済みモデル | git 管理内（`.joblib` を追跡）|
| タイムゾーン | `TZ=Asia/Tokyo`（docker-compose で明示）|

**`requirements-lock.txt` の更新方法（パッケージを変更したとき）:**

```bash
# Ubuntu Bash 内、もしくは venv のある環境で実行
python-api/.venv/bin/python -m pip freeze > python-api/requirements-lock.txt
git add python-api/requirements-lock.txt
git commit -m "chore: requirements-lock.txt を更新"
```

---

## 本番環境への対応

```yaml
# docker-compose.prod.yml
services:
  fastapi:
    restart: always
    environment:
      - PORT=8000
      - SCHEDULER_ENABLED=true

  nextjs:
    restart: always
    environment:
      - NODE_ENV=production
```

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

---

## よくあるトラブル

### `permission denied while trying to connect to Docker`

```bash
sudo usermod -aG docker $USER
newgrp docker
```

### FastAPI が DB を見つけられない

```
sqlite3.OperationalError: unable to open database file
```

```bash
mkdir -p keiba/data
```

### Next.js から FastAPI に繋がらない

`ML_API_URL` と `SCRAPE_API_URL` が `http://fastapi:8000` になっているか確認。  
（コンテナ内では `localhost:8000` は自分自身を指す）

### Playwright が "Executable doesn't exist" エラー

```bash
docker compose up --build   # Dockerfile から再ビルド
```

### scikit-learn のモデル読み込みエラー

`requirements-lock.txt` で学習時と同じバージョンを固定する。  
ロックファイルを更新してから `docker compose up --build` で再ビルド。

---

## 再現性チェックリスト

別マシンにゼロから構築する前に確認:

- [ ] WSL2 + Ubuntu 24.04 をインストールした
- [ ] Docker Engine をインストールした（`docker run hello-world` 成功）
- [ ] VS Code に WSL / Dev Containers 拡張を入れた
- [ ] リポジトリを Ubuntu のホームに clone した（`~/keiba-ai-pro/`）
- [ ] `.env` を作成し Supabase・JWT キーを設定した
- [ ] `keiba/data/keiba_ultimate.db` を配置した
- [ ] `python-api/requirements-lock.txt` が git に含まれている
- [ ] `package-lock.json` が git に含まれている
- [ ] `Dockerfile.nextjs` を作成した
- [ ] `docker-compose.yml` を作成した
- [ ] `docker compose up --build` が成功した
- [ ] `curl http://localhost:8000/` が応答を返した
- [ ] `http://localhost:3000` にアクセスできた

---

## 環境変数一覧

| 変数名 | サービス | 説明 |
|---|---|---|
| `SUPABASE_URL` | FastAPI | Supabase プロジェクト URL |
| `SUPABASE_SERVICE_KEY` | FastAPI | Service Role Key |
| `JWT_SECRET_KEY` | FastAPI | JWT 署名キー（必ず変更） |
| `PORT` | FastAPI | ポート番号（デフォルト 8000） |
| `SCHEDULER_ENABLED` | FastAPI | スクレイプ自動実行 on/off |
| `NEXT_PUBLIC_SUPABASE_URL` | Next.js | Supabase プロジェクト URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Next.js | Anon Key |
| `NEXT_PUBLIC_API_URL` | Next.js | FastAPI URL（ブラウザ用: `http://localhost:8000`） |
| `ML_API_URL` | Next.js | FastAPI URL（コンテナ内: `http://fastapi:8000`） |
| `SCRAPE_API_URL` | Next.js | スクレイプ API URL（コンテナ内: `http://fastapi:8000`） |
