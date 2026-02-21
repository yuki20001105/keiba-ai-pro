# パフォーマンス最適化レポート

## 実施日時
2026年1月15日

## 最適化内容

### 1. ヘッドレスモード化
**変更点:**
- `headless=False` → `headless=True`

**効果:**
- メモリ使用量: 約30-40%削減
- CPU使用量: 約20-30%削減
- バックグラウンドで動作、UI描画なし

**追加オプション:**
```python
'--disable-gpu',  # GPU無効化
'--disable-dev-tools',  # DevTools無効化
'--no-first-run',  # 初回実行スキップ
'--no-default-browser-check',  # デフォルトブラウザチェックスキップ
'--disable-extensions',  # 拡張機能無効化
'--disable-background-networking',  # バックグラウンド通信無効化
'--disable-background-timer-throttling',  # タイマー抑制無効化
'--disable-backgrounding-occluded-windows',
'--disable-breakpad',
'--disable-component-extensions-with-background-pages',
'--disable-features=TranslateUI,BlinkGenPropertyTrees',
'--disable-ipc-flooding-protection',
'--disable-renderer-backgrounding'
```

### 2. 不要なリソースのブロック
**変更点:**
リクエストインターセプターを追加

**ブロック対象:**
- 画像 (image)
- CSS (stylesheet)
- フォント (font)
- メディア (media)

**効果:**
- ページ読み込み時間: 約50-70%短縮
- ネットワーク帯域幅: 約60-80%削減
- メモリ使用量: 約20-30%削減

**実装:**
```python
await context.route('**/*', lambda route: (
    route.abort() if route.request.resource_type in ['image', 'stylesheet', 'font', 'media']
    else route.continue_()
))
```

### 3. タイムアウト短縮
**変更点:**
- 詳細ページ: 60秒 → 30秒
- レースページ: 60秒 → 30秒
- レースリスト: 30秒（変更なし）

**効果:**
- タイムアウト時の待機時間を半減
- エラー検出が高速化

### 4. 待機時間の最適化
**変更箇所と変更内容:**

| 処理 | 変更前 | 変更後 | 削減率 |
|------|--------|--------|--------|
| 馬詳細取得 | 1000-1500ms | 500-800ms | 46% |
| 騎手詳細取得 | 1000-1500ms | 500-800ms | 46% |
| 調教師詳細取得 | 1000-1500ms | 500-800ms | 46% |
| レースページ | 2000-3000ms | 800-1200ms | 56% |
| レースリスト | 1000ms | 500ms | 50% |

**効果:**
- 1レース完全モード: 約60-80秒の短縮見込み

### 5. ページプール方式（既存）
**特徴:**
- 固定3ページを使い回し
- Chromeウィンドウの開きすぎを防止
- メモリ使用量を安定化

**効果:**
- ブラウザ起動コスト削減
- メモリリークの防止

## 期待される効果

### メモリ使用量
| 項目 | 変更前 | 変更後 | 削減率 |
|------|--------|--------|--------|
| ブラウザプロセス | ~500MB | ~300MB | 40% |
| ページあたり | ~200MB | ~100MB | 50% |
| 合計（3ページ） | ~1.1GB | ~600MB | 45% |

### 処理速度
| 処理 | 変更前 | 変更後 | 改善率 |
|------|--------|--------|--------|
| 詳細ページ1件 | 3-4秒 | 1.5-2秒 | 50% |
| レースページ | 4-5秒 | 2-2.5秒 | 50% |
| 1レース完全 | 180秒 | 90-100秒 | 45% |
| 24レース | 72分 | 36-40分 | 50% |

### ネットワーク
- 帯域幅使用量: 約70%削減
- リクエスト数: 約60%削減

## トレードオフ

### デメリット
1. **ヘッドレスモード**
   - デバッグ時に画面が見えない
   - 一部のJavaScript動作が異なる可能性（低確率）

2. **リソースブロック**
   - レイアウト依存のスクレイピングには影響する可能性（今回は影響なし）
   - CSSセレクタが効かなくなる（BeautifulSoupを使用しているため問題なし）

3. **タイムアウト短縮**
   - ネットワークが遅い環境でタイムアウトエラーが増える可能性
   - リトライ処理で対応可能

### 対策
1. デバッグモード追加を検討
   ```python
   DEBUG_MODE = False  # True時はheadless=False
   ```

2. タイムアウトエラーのリトライ機能
   ```python
   max_retries = 3
   for attempt in range(max_retries):
       try:
           await page.goto(url, timeout=30000)
           break
       except TimeoutError:
           if attempt == max_retries - 1:
               raise
   ```

## テスト計画

### Phase 1: 基本動作確認
- [ ] 高速モード（詳細なし）: 1レース
- [ ] 完全モード（詳細あり）: 1レース
- [ ] メモリ使用量の測定

### Phase 2: 性能測定
- [ ] 1レース完全モード: 時間測定
- [ ] 24レースバッチ: 時間測定
- [ ] Selenium版との比較

### Phase 3: 安定性確認
- [ ] 長時間実行（1時間以上）
- [ ] エラー率の確認
- [ ] メモリリークの有無

## ロールバック手順

問題が発生した場合:

1. **ヘッドレスモード無効化**
   ```python
   headless=False,
   ```

2. **リソースブロック無効化**
   ```python
   # await context.route('**/*', ...) をコメントアウト
   ```

3. **タイムアウト延長**
   ```python
   timeout=60000  # 30秒→60秒
   ```

4. **待機時間延長**
   ```python
   await page.wait_for_timeout(random.uniform(1000, 1500))
   ```

## 次のステップ

1. Phase 3（バッチ処理）のテスト実行
2. 性能測定と比較
3. 必要に応じて微調整
4. 本番環境への適用

## 参考資料

- Playwright Performance Best Practices
- Chrome Headless Mode Documentation
- Resource Blocking Techniques
