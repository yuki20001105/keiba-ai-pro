# Keiba AI Pro - 実装課題まとめ

**分析日**: 2026年4月12日  
**対象ファイル**: 8ファイル  
**検出課題数**: 50+

---

## 🔴 緊急 (Critical)

### C1: SQLインジェクションリスク
- **ファイル**: `python-api/scraping/jobs.py` (L198, L356)
- **問題**: レースIDをregexで抽出後、フォーマット検証なしにSQLで使用
- **影響**: DBへの不正アクセス、データ漏洩
- **対策**: SQL実行前に `re.fullmatch(r"\d{12}", race_id)` で検証を追加

### C2: 予測エンドポイントの認証不足
- **ファイル**: `python-api/routers/predict.py` (L85, L194)
- **問題**: `/api/predict`, `/api/analyze_race` に明示的な認証デコレータがない
- **影響**: 無制限アクセス、戦略・モデルの漏洩、DDoS脆弱性
- **対策**: `@require_auth` デコレータを追加

### C3: JWTペイロードのスキーマ検証なし
- **ファイル**: `python-api/routers/predict.py` (L219)
- **問題**: `json.loads(_rrow[0])` でレースデータをPydantic検証なしで読み込み
- **影響**: サービスクラッシュ、データ破損リスク
- **対策**: `RaceDataSchema` を定義してPydanticで検証

### C4: asyncio未インポートによるNameError
- **ファイル**: `python-api/routers/predict.py` (L252)
- **問題**: `asyncio.TimeoutError` 参照時に `asyncio` がインポートされていない
- **影響**: タイムアウト発生時にNameErrorでクラッシュ
- **対策**: ファイル先頭に `import asyncio` を追加

### C5: ケリー基準の範囲検証なし
- **ファイル**: `python-api/betting_strategy.py` (L47-68)
- **問題**: `calculate_kelly_bet` で probability∉[0,1]、odds≤1.0 の場合を未検証
- **影響**: 無意味な賭け金計算、inf/NaN値の発生
- **対策**: 入力範囲の検証ロジックを追加（既に `probability <= 0 or odds <= 1.0` チェックあり。但しNoneチェックが不足）

---

## 🟠 高 (High)

### H1: N+1クエリ / DataFrame iterrows によるパフォーマンス問題
- **ファイル**: `python-api/routers/predict.py` (L275-290)
- **問題**: `df.iterrows()` で1行ずつ処理（O(n)）
- **影響**: 1回の予測で500ms以上のレイテンシ
- **対策**: pandasのベクトル化操作に置き換え

### H2: モデルバンドルの不完全なエラーハンドリング
- **ファイル**: `python-api/routers/predict.py` (L143)
- **問題**: `load_model_bundle()` でキーの存在検証なし
- **影響**: KeyError による不明瞭なクラッシュ
- **対策**: 必須フィールドのassertionを追加

### H3: 騎手回収率のハードコーディング
- **ファイル**: `python-api/betting_strategy.py` (L426-434)
- **問題**: 武豊、川田将雅などの回収率データがコードに埋め込まれ
- **影響**: データが陳腐化しても更新にデプロイが必要
- **対策**: DBテーブルから動的に取得

### H4: メモリリーク（DataFrameの解放なし）
- **ファイル**: `python-api/routers/predict.py` (L235-250)
- **問題**: 大きなDataFrameが作成後に解放されない
- **影響**: 同時予測リクエスト時のOOM
- **対策**: 処理後に `del df; gc.collect()` を呼ぶ

### H5: 日付フォーマット検証なし
- **ファイル**: `python-api/routers/predict.py` (L170)
- **問題**: `date_hint` パラメータのフォーマット未検証
- **影響**: サイレントな失敗、デバッグが困難
- **対策**: `re.match(r"\d{8}", date_hint)` で検証

### H6: フロントエンドのレスポンス構造検証なし
- **ファイル**: `src/app/predict-batch/page.tsx` (L153-167)
- **問題**: バックエンドのレスポンス構造を仮定
- **影響**: 「Cannot read property of undefined」UIクラッシュ
- **対策**: TypeScriptの型定義とzodスキーマ検証を追加

### H7: キャッシュキーにmodel_idが含まれていない ⚠️ データ整合性バグ
- **ファイル**: `python-api/routers/predict.py` (L48-51)
- **問題**: キャッシュキーが `race_id:bankroll:risk_mode` のみで `model_id` を含まない
- **影響**: ユーザーAがmodel_1で予測 → キャッシュ → ユーザーBがmodel_2で同レース予測 → model_1の結果が返る（誤り!）
- **対策**: キャッシュキーに `model_id` を含める

### H8: スクレイプ操作のタイムアウト未設定
- **ファイル**: `python-api/scraping/jobs.py` (L350)
- **問題**: 個別の `scrape_race_full()` 呼び出しにタイムアウトなし
- **影響**: 1レースが詰まるとバッチ全体がブロック
- **対策**: `asyncio.wait_for()` でタイムアウトを設定

---

## 🟡 中 (Medium)

| # | ファイル | 問題 | 影響 |
|---|---------|-----|-----|
| M1 | `routers/predict.py` | カラムマッピングが複数ファイルに重複 | スキーマ変更時に複数箇所の修正が必要 |
| M2 | `src/app/predict-batch/page.tsx` | TypeScriptの`any`型を多用 | 型安全性の欠如、ランタイムエラーを見逃す |
| M3 | `betting_strategy.py` | expected_value がNoneになりうる箇所が残存 | サイレントな計算エラー |
| M4 | `scraping/jobs.py` | `_persist_job()` がDBエラーを無視 | メモリとDBの状態不一致 |
| M5 | `scraping/jobs.py` | レースID抽出のregexがURL変更に脆弱 | スクレイパーがサイレントに停止 |
| M6 | `src/app/data-collection/page.tsx` | 日付範囲入力のフロントエンド検証なし | UXが悪く、サーバーログにゴミが溜まる |
| M7 | `routers/predict.py` | レートリミットデコレータが欠如 | 未認証ユーザーが制限をバイパスできる |
| M8 | `routers/predict.py` | 重複カラム削除処理が必要な根本原因が不明 | 上流のデータ問題が隠蔽されている |
| M9 | `routers/predict.py` | `assert_feature_columns` と `verify_feature_columns` が二重呼び出し | 冗長なチェックでパフォーマンス低下 |
| M10 | `routers/predict.py` | 履歴DataFrame全件ロード（100K+行） | 1レース予測のために全データをメモリに展開 |

---

## 🟢 低 (Low)

| 問題 | ファイル | 内容 |
|-----|---------|-----|
| マジックナンバー | `betting_strategy.py` | 0.25, 0.05, 0.7 などが直書き |
| 言語の不統一 | 全体 | 日本語・英語エラーメッセージが混在 |
| discardされたボイスーメール | `predict.py` | モデル評価指標（AUC以外）が記録されない |
| 未使用インポート | 各所 | デッドコードによる混乱 |
| Nullチェックの不統一 | 全体 | `is None` / `== ""` / 真偽値判定が混在 |
| 構造化ログの不足 | `scraping/jobs.py` | `logger`, `print`, サイレントエラーが混在 |

---

## 📊 優先度別サマリー

| カテゴリ | Critical | High | Medium | Low | 合計 |
|---------|:-------:|:----:|:------:|:---:|:---:|
| セキュリティ | 3 | 1 | 2 | - | **6** |
| パフォーマンス | - | 2 | 3 | - | **5** |
| データ品質 | 2 | 1 | 3 | 1 | **7** |
| アーキテクチャ | - | 2 | 3 | - | **5** |
| 保守性 | 1 | 2 | 3 | 3 | **9** |
| ビジネスロジック | 1 | 1 | 1 | 2 | **5** |
| フロントエンド | - | 1 | 2 | 1 | **4** |
| **合計** | **7** | **10** | **17** | **7** | **50** |

---

## ⚡ 修正ロードマップ

### 🚨 今すぐ対応（本番デプロイ前に必須）
1. **C2** - 予測エンドポイントへの認証追加
2. **H7** - キャッシュキーにmodel_idを含める
3. **C4** - asyncioインポートの追加
4. **C5** - Kelly基準のNone入力チェック強化

### ⚠️ 次のスプリント
5. **C1** - SQLインジェクション対策
6. **H1** - iterrowsの除去（ベクトル化）
7. **H3** - 騎手データをDB管理に移行
8. **H4** - DataFrameのメモリ解放
9. **M10** - 必要な期間のみ履歴ロード

### 📋 バックログ
10. M1-M9 + L1-L6 - コード品質改善

---

## 🌟 現状の強み（課題でない部分）

- **モデル精度**: Optuna + LightGBM、AUC=0.8865は良好
- **スクレイピング基盤**: 非同期・Rate limit付きで設計は堅牢
- **RLockによるデッドロック対策**: 前セッションで修正済み
- **AbortSignal.timeout**: 全Next.jsルートで実装済み
- **model_idのフォールバック**: created_at/timestamp両対応済み
- **bet_types 6種**: 単勝・馬連・ワイド・三連複・馬単・三連単 全対応
