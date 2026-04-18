FROM python:3.11.9-slim

WORKDIR /app

# OS レベルの Playwright 依存ライブラリをインストール
# (chromium を使用したスクレイピングに必要)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libasound2 libpangocairo-1.0-0 \
    wget ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# keiba ライブラリをコピー
COPY keiba/ ./keiba/

# python-api をコピー
COPY python-api/ ./python-api/

# 依存パッケージをインストール（ロックファイルがあれば優先）
RUN if [ -f python-api/requirements-lock.txt ]; then \
      pip install --no-cache-dir -r python-api/requirements-lock.txt; \
    else \
      pip install --no-cache-dir -r python-api/requirements.txt; \
    fi

# Playwright ブラウザ（chromium のみ）をインストール
RUN python-api/.venv/bin/python -m playwright install chromium 2>/dev/null || \
    python -m playwright install chromium

WORKDIR /app/python-api

ENV PYTHONPATH=/app/keiba:$PYTHONPATH
ENV PYTHONUNBUFFERED=1

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
