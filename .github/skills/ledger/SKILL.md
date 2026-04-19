---
name: ledger
description: 'Ledger（レジャー）スキル — 購入管理・損益分析・成績追跡担当。Use when: 馬券購入履歴の管理・CRUD操作をしたい / 的中/外れの結果入力に関するバグを修正したい / 回収率・損益の計算ロジックを変更したい / ダッシュボードの表示を改修したい / 予測履歴ページを改修したい / 購入記録の削除・更新が動かない / purchase_history テーブルの調査。Keywords: dashboard, 購入履歴, purchase_history, 的中, 外れ, 回収率, 損益, P&L, 払戻, prediction-history, 成績確認, 結果入力, Supabase purchase, PATCH, DELETE'
---

# Ledger（レジャー）— 購入管理・損益分析・成績追跡

馬券購入履歴の管理から損益計算・予測精度の追跡まで、成績に関するすべてを担当。

---

## 担当ページ・ファイル

| 種別 | パス | 役割 |
|---|---|---|
| UI | `src/app/dashboard/page.tsx` | 購入履歴・結果入力・損益チャート |
| UI | `src/app/prediction-history/page.tsx` | 過去予測 vs 実績の一覧・的中率 |
| API (Next.js) | `src/app/api/purchase/route.ts` | 購入記録 GET / POST |
| API (Next.js) | `src/app/api/purchase/[id]/route.ts` | 購入記録 PATCH / DELETE |
| API (FastAPI) | `python-api/routers/purchase.py` | 購入記録 CRUD（Supabase + SQLite） |
| API (FastAPI) | `python-api/routers/prediction_history.py` | 予測履歴・精度集計 |
| API (FastAPI) | `python-api/routers/stats.py` | 統計情報（馬券種別・回収率等） |

---

## データソース構成

```
Supabase (purchase_history テーブル)   ← 購入記録の正（UUID主キー）
    ↕ 同期
SQLite  (purchase_history テーブル)    ← ローカルキャッシュ・集計用
SQLite  (prediction_log テーブル)      ← 予測記録（Oracle が書き込む）
SQLite  (results テーブル)             ← 実際の着順（Harvester が書き込む）
```

---

## purchase_history テーブル構造

```sql
-- Supabase 側
CREATE TABLE purchase_history (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID REFERENCES auth.users(id),
    race_id         TEXT,
    race_name       TEXT,
    venue           TEXT,
    race_date       TEXT,
    bet_type        TEXT,      -- 'win' | 'place' | 'exacta' | 'quinella' | ...
    selection       TEXT,      -- 馬番（JSON配列など）
    amount          INTEGER,   -- 購入金額（円）
    is_hit          BOOLEAN DEFAULT FALSE,
    actual_return   INTEGER,   -- 払戻金額（円）
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 主要 API エンドポイント

| エンドポイント | メソッド | 説明 |
|---|---|---|
| `/api/purchase` | GET | 購入履歴一覧取得 |
| `/api/purchase` | POST | 購入記録作成 |
| `/api/purchase/{id}` | PATCH | 結果入力（的中/外れ・払戻更新） |
| `/api/purchase/{id}` | DELETE | 購入記録削除 |
| `/api/purchase-history` | GET | FastAPI直接（limit付き） |
| `/api/statistics` | GET | 馬券種別統計 |
| `/api/prediction-history` | GET | 全予測履歴（Premium必須） |
| `/api/prediction-history/{race_id}` | GET | 特定レース予測 vs 実績 |

---

## ダッシュボード 2セクション構成

```
┌─────────────────────────────────┐
│  結果未入力（amber枠）           │
│  条件: is_hit=false AND actual_return=null/0 AND 結果未入力ID でない |
│  → 「結果を入力 →」ボタン        │
├─────────────────────────────────┤
│  入力済み                        │
│  → ✓ 的中（緑）/ ✕ 外れ（赤）  │
│  → 払戻・P&L表示                │
└─────────────────────────────────┘
```

### 入力済み状態の管理

```typescript
// dashboard/page.tsx
const [resultEnteredIds, setResultEnteredIds] = useState<Set<string>>(new Set())

// 結果保存後
onSave = (updated) => {
  setResultEnteredIds(prev => new Set(prev).add(String(updated.id)))
}
```

---

## 統計計算ロジック

| 指標 | 計算方法 |
|---|---|
| 的中率 | `hit_count / total_bets * 100` |
| 回収率 | `total_return / total_amount * 100` |
| P&L | `total_return - total_amount` |
| 馬券種別集計 | `bet_type` でグループ化して集計 |

---

## よくあるトラブル

### DELETE 405 Method Not Allowed
```
原因: FastAPI サーバーが旧コードのまま（reload=False のため）
対処: FastAPI を再起動すれば解消
     main.py: uvicorn.run(app, reload=False)
```

### PATCH が反映されない
```
確認: Next.js プロキシ → FastAPI への認証ヘッダー転送を確認
     src/app/api/purchase/[id]/route.ts で authFetch 使用確認
```

### Supabase の UUID と SQLite の id が混在する
```
Supabase: id は UUID 型 → eq('id', uuid_str) で照合
SQLite:   int_id は INTEGER → WHERE int_id = ? で照合
purchase.py が両方対応済み（is_uuid() で判定）
```

### prediction-history で Premium エラー
```
原因: Supabase profiles.subscription_tier が 'free'
対処: python-api 経由で subscription_tier を 'premium' に更新
     deps/auth.py の require_premium が profiles テーブルを確認
```

---

## 関連スキル

| タスク | 参照スキル |
|---|---|
| 予測結果の照合ロジック | `oracle` スキル参照（prediction_log + results JOIN） |
| git コミット | `sysop` / `git-workflow` スキル参照 |
