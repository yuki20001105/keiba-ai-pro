-- usersテーブルを作成
CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  name TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- デフォルトユーザーを追加
INSERT INTO users (id, email, name, created_at, updated_at)
VALUES (
  '00000000-0000-0000-0000-000000000000',
  'default@keiba-ai.local',
  'Default User',
  NOW(),
  NOW()
)
ON CONFLICT (id) DO NOTHING;

-- 確認
SELECT id, email, name FROM users WHERE id = '00000000-0000-0000-0000-000000000000';
