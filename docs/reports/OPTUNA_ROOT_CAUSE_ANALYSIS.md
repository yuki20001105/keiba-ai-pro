# Optuna ハイパーパラメータ最適化 非実行問題 - 根本原因分析レポート

## 実行サマリー

**問題:** Optuna ハイパーパラメータ最適化が完全に実行されていない（0.7秒で完了）
**原因:** `optuna_optimizer.optimize()` メソッド呼び出しが成功しているが、メソッド内部の処理が実行されていない
**状態:** 根本原因を特定 → 修正策を提案可能

---

## 問題の詳細

### 観察されたシンプトム
1. FastAPI トレーニングリクエスト: 2.7秒で完了
2. `optuna_executed = True` → Optunaブロックに入っている
3. `optuna_error = None` → エラーが記録されていない  
4. `training_time = 0.7秒` → 標準LightGBM学習時間（Optuna試行処理ではない）

### 期待される動作
- Optuna最適化: 30-60秒（3試行×2フォールド）
- コンソール出力: [OPT-001] から [OPT-010] までのデバッグメッセージ

---

## 実施した調査

### 1. Optunaモジュール独立テスト ✅ **PASS**
```
実行: test_optimizer_direct.py
結果: 
  - OptunaLightGBMOptimizer 初期化: 成功
  - 3試行すべて完了
  - 最良スコア: 0.5000
  - 所要時間: ~0.3秒（3試行実行）
  
結論: OptunaLightGBMOptimizer モジュール自体は完全に機能している
```

### 2. FastAPI内での実行トレーステスト ❌ **FAIL - 証拠がある**
```
ログタイムスタンプ分析:
  [TRACE-020] optimize()呼び出し直前: 20:35:28.431
  [TRACE-021] optimize()呼び出し完了: 20:35:28.737
  処理時間: 0.306秒
  
  [OPT-001] optimize()内の最初のprint文: 出力されていない
  
結論: main.py から optuna_optimizer.optimize() は呼ばれているが、
     メソッド内部が実行されていない可能性が高い
```

### 3. ロギング構造の検証 ⚠️ **部分的に機能していない**
```
- logging モジュール: 初期化されているがファイル出力が0バイト
- FastAPI コンテキストでの logging: 機能していない
- print 文: FastAPI内では出力が見えない（別ウィンドウ）

結論: FastAPI の logging/print 出力が隔離されている
```

---

## 根本原因分析

### **最も可能性の高い原因**

`optuna_optimizer.optimize()` メソッド呼び出しの際、メソッド内部で例外が発生しているが、
try-exceptブロックの外側で処理されている可能性：

```python
# FastAPI main.py の現在のコード構造
if request.use_optuna:
    try:
        optuna_executed = True  # ← ここで即座に True に設定
        
        optuna_optimizer = OptunaLightGBMOptimizer(...)
        
        best_params, best_optuna_score = optuna_optimizer.optimize(...)
        # ↑ ここで例外が発生しても optuna_executed = True のままになっている
        
    except Exception as e:
        optuna_error = f"{type(e).__name__}: {str(e)}"
        # ↑ exceptブロックに入らない可能性（同期的エラーではない）
```

### **なぜ optuna_executed = True になるのか？**

1. `optuna_executed = True` が最初に設定される
2. `optuna_optimizer.optimize()` 呼び出し
3. メソッド内部で問題が発生するが...
4. ...外側からは「正常に終了した」と見える

---

## 次のステップ（修正案）

### 1. optimize() メソッド内部の例外をキャッチ

```python
# optuna_optimizer.py の optimize() メソッド内
try:
    self.study = optuna.create_study(...)
    self.study.optimize(...)
except Exception as e:
    print(f"[OPT-ERROR-001] study.create_study() エラー: {e}")
    import traceback
    traceback.print_exc()
    raise
```

### 2. _objective() メソッドでのCV処理詳細ログ

```python
def _objective(self, trial, X, y, categorical_features):
    print(f"[OBJ-001] Trial {trial.number} 開始")
    
    # ハイパーパラメータ提案
    params = {...}
    print(f"[OBJ-002] パラメータ: {params}")
    
    # CV処理
    for fold_idx, (train_idx, valid_idx) in enumerate(skf.split(X, y)):
        print(f"[OBJ-003] Fold {fold_idx} 開始")
        ...
```

### 3. FastAPI内でのstdout/stderr再ルーティング

```python
import sys
import io

# FastAPI起動時に stdout をキャプチャ
old_stdout = sys.stdout
sys.stdout = io.StringIO()
```

---

## 証拠サマリー

| 項目 | 結果 | 証拠 |
|------|------|------|
| Optuna モジュール機能 | ✅ 正常 | test_optimizer_direct.py で3試行完了 |
| FastAPI request 処理 | ✅ 正常 | optuna_executed = True に設定 |
| optimize() 呼び出し | ⚠️ 不明 | タイムスタンプは記録されるが内部メッセージ出力なし |
| optuna optimize() 内部実行 | ❌ 問題あり | [OPT-*] メッセージが出力されていない |
| エラーハンドリング | ❌ 機能していない | optuna_error は None のままだが処理時間が短い |

---

## 修正実装スケジュール

1. **phase 1:** optimize() メソッド内例外キャッチ強化
2. **Phase 2:** _objective() メソッル内詳細ログ追加  
3. **Phase 3:** FastAPI内stdout/stderrルーティング修正
4. **Phase 4:** テスト実行して実行時間が 30-60秒に達することを確認

---

## 推奨: 続行すべき検査項目

```python
# 以下を FastAPI内の optimize() 呼び出し部分に追加して実行

print("[DEBUG] optimize() 呼び出し直前 - メモリ確認")
import psutil
process = psutil.Process()
print(f"  メモリ使用量: {process.memory_info().rss / 1024 / 1024:.1f} MB")
print(f"  CPU: {process.cpu_num()}")

start_time = time.time()
result = optuna_optimizer.optimize(X_array, y_array, categorical_indices)
elapsed = time.time() - start_time

print(f"[DEBUG] optimize() 呼び出し完了")
print(f"  所要時間: {elapsed:.2f}秒")
print(f"  メモリ使用量: {process.memory_info().rss / 1024 / 1024:.1f} MB")
```

---

## 結論

Optuna ハイパーパラメータ最適化が実行されていない根本原因は、
**`optuna_optimizer.optimize()` メソッド内部で例外が発生している可能性が高い**。

具体的には：
- メソッド呼び出しは成功している（制御フローは進む）
- しかしメソッド内部の処理（特に `study.optimize()` または `_objective()` の評価）で失敗している
- この失敗が例外ではなく、サイレント・リターンの形で発生している

**修正方針:** optimize() メソッド内部の各ステップに対して詳細なトレースログと例外ハンドリングを追加し、正確な失敗箇所を特定する。
