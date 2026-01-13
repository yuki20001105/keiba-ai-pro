# カラム完全対応マッピング

## 1. races テーブル

### APIコード → SQLスキーマ

| APIコード (route.ts) | データ型 | SQLスキーマ | データ型 | ソース | 備考 |
|---------------------|---------|------------|---------|--------|------|
| `race_id` | string | `race_id` | TEXT | raceId | Primary Key |
| `race_name` | string | `race_name` | TEXT | scrapeData.race_info.race_name | レース名 |
| `venue` | string | `venue` | TEXT | scrapeData.race_info.venue | 競馬場 |
| `distance` | number | `distance` | INTEGER | scrapeData.race_info.distance | 距離(m) |
| `track_type` | string | `track_type` | TEXT | scrapeData.race_info.track_type | 芝/ダート |
| `weather` | string | `weather` | TEXT | scrapeData.race_info.weather | 天気 |
| `field_condition` | string | `field_condition` | TEXT | scrapeData.race_info.field_condition | 馬場状態 |
| `user_id` | UUID | `user_id` | UUID | userId | ユーザーID |
| - | - | `date` | TEXT | - | ❌ 未使用 |
| - | - | `race_class` | TEXT | - | ❌ 未使用 |
| - | - | `num_horses` | INTEGER | - | ❌ 未使用 |
| - | - | `surface` | TEXT | - | ❌ 未使用 |

**✅ 使用されているカラム:** 8個  
**❌ 未使用カラム:** 4個 (date, race_class, num_horses, surface)

---

## 2. race_results テーブル

### APIコード → SQLスキーマ

| APIコード (route.ts) | データ型 | SQLスキーマ | データ型 | ソース | 備考 |
|---------------------|---------|------------|---------|--------|------|
| `race_id` | string | `race_id` | TEXT | raceId | Foreign Key |
| `finish_position` | number | `finish_position` | INTEGER | result.finish_position | 着順 |
| `bracket_number` | number | `bracket_number` | INTEGER | result.bracket_number | 枠番 |
| `horse_number` | number | `horse_number` | INTEGER | result.horse_number | 馬番 |
| `horse_name` | string | `horse_name` | TEXT | result.horse_name | 馬名 |
| `sex` | string | `sex` | TEXT | result.sex_age[0] | 性別 (牡/牝/セ) |
| `age` | number | `age` | INTEGER | result.sex_age[1:] | 年齢 |
| `jockey_weight` | number | `jockey_weight` | REAL | result.jockey_weight | 斤量 |
| `jockey_name` | string | `jockey_name` | TEXT | result.jockey_name | 騎手名 |
| `finish_time` | string | `finish_time` | TEXT | result.finish_time | タイム (文字列) |
| `odds` | number | `odds` | REAL | result.odds | 単勝オッズ |
| `popularity` | number | `popularity` | INTEGER | result.popularity | 人気 |
| `user_id` | UUID | `user_id` | UUID | userId | ユーザーID |
| - | - | `trainer_name` | TEXT | - | ❌ 未使用 |
| - | - | `owner_name` | TEXT | - | ❌ 未使用 |
| - | - | `margin` | TEXT | - | ❌ 未使用 |
| - | - | `corner_positions` | TEXT | - | ❌ 未使用 |
| - | - | `last_3f_time` | REAL | - | ❌ 未使用 |
| - | - | `horse_weight` | INTEGER | - | ❌ 未使用 |
| - | - | `weight_change` | INTEGER | - | ❌ 未使用 |
| - | - | `prize_money` | INTEGER | - | ❌ 未使用 |

**✅ 使用されているカラム:** 13個  
**❌ 未使用カラム:** 8個 (trainer_name, owner_name, margin, corner_positions, last_3f_time, horse_weight, weight_change, prize_money)

---

## 3. race_odds テーブル

### SQLスキーマのみ（APIコードで未使用）

| SQLスキーマ | データ型 | 状態 | 備考 |
|------------|---------|------|------|
| `id` | UUID | ❌ 未使用 | Primary Key |
| `race_id` | TEXT | ❌ 未使用 | Foreign Key |
| `umaban` | INTEGER | ❌ 未使用 | 馬番 |
| `tansho_odds` | REAL | ❌ 未使用 | 単勝オッズ |
| `fukusho_odds_min` | REAL | ❌ 未使用 | 複勝オッズ下限 |
| `fukusho_odds_max` | REAL | ❌ 未使用 | 複勝オッズ上限 |
| `user_id` | UUID | ❌ 未使用 | ユーザーID |

**⚠️ このテーブルはAPIコードで全く使用されていません**

---

## 4. race_payouts テーブル

### APIコード → SQLスキーマ

| APIコード (route.ts) | データ型 | SQLスキーマ | データ型 | ソース | 備考 |
|---------------------|---------|------------|---------|--------|------|
| `race_id` | string | `race_id` | TEXT | raceId | Foreign Key |
| `bet_type` | string | `bet_type` | TEXT | payout.type or payout.bet_type | 券種 |
| `combination` | string | `combination` | TEXT | payout.numbers or payout.combination | 組み合わせ |
| `payout` | number | `payout` | INTEGER | payout.amount (円、カンマ削除) | 払戻金 |
| `user_id` | UUID | `user_id` | UUID | userId | ユーザーID |
| - | - | `popularity` | INTEGER | - | ❌ 未使用 |

**✅ 使用されているカラム:** 5個  
**❌ 未使用カラム:** 1個 (popularity)

---

## Python API レスポンス構造

### /scrape/ultimate エンドポイント

```json
{
  "success": true,
  "race_info": {
    "race_name": "3歳未勝利",
    "venue": "中山",
    "distance": 1200,
    "track_type": "芝",
    "weather": "晴",
    "field_condition": "良"
  },
  "results": [
    {
      "finish_position": "1",
      "bracket_number": "3",
      "horse_number": "5",
      "horse_name": "トップガン",
      "sex_age": "牡3",
      "jockey_weight": "56.0",
      "jockey_name": "横山武史",
      "finish_time": "1:10.5",
      "odds": "2.5",
      "popularity": "1"
    }
  ],
  "payouts": [
    {
      "type": "単勝",
      "numbers": "5",
      "amount": "250円"
    }
  ]
}
```

---

## データフロー図

```
Python API (scraping_service_ultimate_fast.py)
    ↓ /scrape/ultimate
Next.js API Route (src/app/api/netkeiba/race/route.ts)
    ↓ データ変換・マッピング
Supabase PostgreSQL (races, race_results, race_payouts)
```

---

## 重要な変換処理

### 1. sex_age の分解
```typescript
const sexAge = result.sex_age || ''  // "牡3"
const sex = sexAge.charAt(0)          // "牡"
const age = parseInt(sexAge.substring(1))  // 3
```

### 2. 払戻金額の変換
```typescript
const amountStr = payout.amount  // "1,250円"
const amount = parseInt(amountStr.replace(/[円,]/g, ''))  // 1250
```

### 3. finish_time の型
- **Python API:** String型 (例: "1:10.5")
- **SQLスキーマ:** TEXT型 ✅
- **変換不要:** そのまま保存

---

## 推奨事項

### 未使用カラムの削除を検討

**race_results:**
- `trainer_name`, `owner_name`, `margin`, `corner_positions`
- `last_3f_time`, `horse_weight`, `weight_change`, `prize_money`

**races:**
- `date`, `race_class`, `num_horses`, `surface`

**race_payouts:**
- `popularity`

**race_odds:**
- テーブル全体が未使用

### または、今後の拡張のために保持
- Python APIスクレイパーでこれらのデータを取得するように拡張可能
- 現在は最小限のデータのみ保存

---

## 完全性チェック

✅ **races:** 8/12カラム使用 (66%)  
✅ **race_results:** 13/21カラム使用 (62%)  
✅ **race_payouts:** 5/6カラム使用 (83%)  
❌ **race_odds:** 0/7カラム使用 (0%) - 未使用テーブル

**カラム名の不一致:** ✅ 修正完了
- `finish_time`: REAL → TEXT
- `winning_numbers` → `combination`
