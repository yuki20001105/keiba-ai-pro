FROM python:3.11-slim

WORKDIR /app

# keiba ライブラリをコピー
COPY keiba/ ./keiba/

# python-api をコピー
COPY python-api/ ./python-api/

# 依存パッケージをインストール
RUN pip install --no-cache-dir -r python-api/requirements.txt

WORKDIR /app/python-api

ENV PYTHONPATH=/app/keiba:$PYTHONPATH
ENV PYTHONUNBUFFERED=1

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
