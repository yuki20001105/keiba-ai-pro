-- Supabase テーブル作成 (Python API 永続化用)
-- Supabase SQL Editor で実行してください

-- レース情報テーブル
CREATE TABLE IF NOT EXISTS races_ultimate (
    race_id TEXT PRIMARY KEY,
    data    JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- レース結果テーブル（馬ごとのレコード）
CREATE TABLE IF NOT EXISTS race_results_ultimate (
    id         BIGSERIAL PRIMARY KEY,
    race_id    TEXT NOT NULL,
    data       JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_race_results_ultimate_race_id
    ON race_results_ultimate (race_id);

-- モデルメタデータテーブル
CREATE TABLE IF NOT EXISTS model_metadata (
    model_id     TEXT PRIMARY KEY,
    storage_path TEXT NOT NULL,
    metadata     JSONB,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- RLS を無効化（FastAPI サーバーサイドのみアクセス）
ALTER TABLE races_ultimate        DISABLE ROW LEVEL SECURITY;
ALTER TABLE race_results_ultimate DISABLE ROW LEVEL SECURITY;
ALTER TABLE model_metadata        DISABLE ROW LEVEL SECURITY;
