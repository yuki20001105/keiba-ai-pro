# VSCodeでのデバッグガイド

## 🎯 デバッグの開始方法

### 1. **デバッグパネルを開く**
- サイドバーの虫アイコン（🐛）をクリック
- または `Ctrl+Shift+D` (Windows/Linux) / `Cmd+Shift+D` (Mac)

### 2. **デバッグ構成を選択**
上部のドロップダウンから選択：
- **🚀 Streamlit: Debug UI (推奨)** - メインUIのデバッグ
- **🔍 デバッグ: 予測ページのみ** - 予測ページ単体
- **🐍 Python: 現在のファイルを実行** - 開いているファイルを実行

### 3. **F5キーでデバッグ開始**
- または緑の▶️ボタンをクリック

---

## 🔧 デバッグ機能の使い方

### ブレークポイントの設定
```python
# 行番号の左をクリックして赤丸を表示
def predict_race(race_id):
    # ← ここにブレークポイントを設定
    df = load_data(race_id)
    predictions = model.predict(df)
    return predictions
```

### デバッグコントロール
- **F5** - 続行（次のブレークポイントまで）
- **F10** - ステップオーバー（次の行へ）
- **F11** - ステップイン（関数の中に入る）
- **Shift+F11** - ステップアウト（関数から出る）
- **Shift+F5** - デバッグ停止

### 変数の確認
デバッグ中、左パネルで以下を確認できます：
- **変数** - ローカル/グローバル変数の値
- **ウォッチ式** - 特定の式を監視
- **コールスタック** - 関数呼び出しの履歴
- **ブレークポイント** - 設定済みのブレークポイント一覧

---

## 📱 VSCode内でUIを表示

### Simple Browserで表示
1. デバッグ開始後、URLが表示される（例: `http://localhost:8501`）
2. `Ctrl+Shift+P` → "Simple Browser: Show" を検索
3. URLを入力して開く

### または、自動で開く設定（既に設定済み）
`launch.json`の`serverReadyAction`により、自動的にブラウザが開きます

---

## 🎯 デバッグのベストプラクティス

### 予測ページのデバッグ例

#### 1. ブレークポイントを設定
[pages/3_予測.py](pages/3_予測.py):
```python
def run_prediction():
    race_id = st.session_state.get('race_id')
    # ← ここにブレークポイント
    
    df_features = create_features(race_id)
    # ← ここにもブレークポイント
```

#### 2. デバッグ開始
- F5でデバッグ開始
- UIで「予測実行」ボタンをクリック
- ブレークポイントで停止

#### 3. 変数を確認
- `race_id`の値を確認
- `df_features`の内容を確認
- `st.session_state`の状態を確認

### データ取得処理のデバッグ例

[pages/1_データ取得.py](pages/1_データ取得.py):
```python
def fetch_one(rid: str):
    # ← ここにブレークポイント
    session = requests.Session()
    df = get_result_table(session, rid, cfg)
    # ← df の内容を確認
    return df
```

---

## 🐛 トラブルシューティング

### Streamlitが起動しない場合
```bash
# ターミナルで手動確認
python -m streamlit run ui_app.py
```

### ブレークポイントで停止しない場合
- `launch.json`の`justMyCode`を`false`に変更（済み）
- Pythonインタープリタが正しく設定されているか確認

### ポートが使用中の場合
```powershell
# 既存のプロセスを確認・終了
Get-Process | Where-Object {$_.ProcessName -eq "streamlit"}
```

---

## 💡 便利なTips

### ログポイント
ブレークポイントの代わりに、コードを変更せずにログ出力：
1. 行番号を右クリック
2. "ログポイントの追加"を選択
3. `race_id={race_id}, count={len(df)}`のように記述

### 条件付きブレークポイント
特定の条件でのみ停止：
1. ブレークポイントを右クリック
2. "条件付きブレークポイントの編集"
3. 例: `race_id == "202406050811"`

### デバッグコンソールで実行
デバッグ中、下部の「デバッグコンソール」タブで：
```python
# 変数の値を確認
print(df.head())

# 式を評価
len(predictions)

# 新しい変数を作成
test_value = df['odds'].mean()
```

---

## 📚 推奨ワークフロー

1. **コードを書く**
   - 通常通りコードを記述

2. **ブレークポイントを設定**
   - 疑わしい箇所、確認したい箇所に設定

3. **F5でデバッグ開始**
   - Streamlit UIが開く

4. **UIで操作**
   - ボタンクリックなどで処理を実行

5. **ブレークポイントで停止**
   - 変数を確認、ステップ実行

6. **問題を修正**
   - コードを修正して再実行（Streamlitは自動リロード）

7. **ログ確認**
   - ターミナルやデバッグコンソールでログ確認
