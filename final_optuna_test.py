"""
Optuna最終テスト
FastAPIを起動し、リクエストを送信して、コンソール出力を確認
"""
import subprocess
import requests
import time
import sys
import threading

print("\n" + "=" * 80)
print("=== Optuna 最終テスト ===")
print("=" * 80)

# 既存プロセスを終了
try:
    subprocess.run(["taskkill", "/F", "/IM", "python.exe"], capture_output=True, timeout=5)
    time.sleep(2)
except:
    pass

# FastAPI起動
import os
os.chdir(r"C:\Users\yuki2\Documents\ws\keiba-ai-pro\python-api")
os.environ["PYTHONPATH"] = r"C:\Users\yuki2\Documents\ws\keiba-ai-pro"

python_exe = r"C:\Users\yuki2\.pyenv\pyenv-win\versions\3.10.11\python.exe"

print("\n[1] FastAPI起動中...")
print("-" * 80)

# FastAPIをサブプロセスで起動（出力をキャプチャ）
process = subprocess.Popen(
    [python_exe, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    bufsize=1,
    universal_newlines=True
)

# 出力を別スレッドで表示
output_lines = []
def read_output():
    for line in iter(process.stdout.readline, ''):
        if line:
            print(line.rstrip())
            output_lines.append(line)
            sys.stdout.flush()

output_thread = threading.Thread(target=read_output, daemon=True)
output_thread.start()

# FastAPI起動を待つ
print("\nFastAPI起動待機中（8秒）...")
time.sleep(8)

# 接続確認
print("\n[2] FastAPI接続確認...")
print("-" * 80)
try:
    response = requests.get("http://localhost:8000/", timeout=5)
    print(f"✓ 接続成功: {response.json()['status']}")
except Exception as e:
    print(f"❌ 接続失敗: {e}")
    process.terminate()
    sys.exit(1)

# リクエスト送信
print("\n[3] Optunaテストリクエスト送信...")
print("-" * 80)
print("パラメータ: use_optuna=True, optuna_trials=3, cv_folds=2")
print()

request_data = {
    "target": "win",
    "model_type": "lightgbm",
    "use_optimizer": True,
    "use_optuna": True,
    "optuna_trials": 3,
    "cv_folds": 2,
    "test_size": 0.2,
    "use_sqlite": True
}

start_time = time.time()

try:
    response = requests.post(
        "http://localhost:8000/api/train",
        json=request_data,
        timeout=180
    )
    
    end_time = time.time()
    duration = end_time - start_time
    
    result = response.json()
    
    print("\n" + "=" * 80)
    print("=== 結果 ===")
    print("=" * 80)
    print(f"HTTP Status: {response.status_code}")
    print(f"実行時間: {duration:.2f} 秒")
    print(f"optuna_executed: {result.get('optuna_executed')}")
    print(f"optuna_error: {result.get('optuna_error', 'なし')}")
    print(f"training_time: {result.get('training_time')}")
    
    # 判定
    print("\n" + "=" * 80)
    print("=== 判定 ===")
    print("=" * 80)
    
    # 出力から[OPT-*]メッセージをチェック
    opt_messages = [line for line in output_lines if '[OPT-' in line]
    
    if len(opt_messages) > 0:
        print(f"✓ [OPT-*] メッセージ検出: {len(opt_messages)}個")
        print("\n最初の5個:")
        for msg in opt_messages[:5]:
            print(f"  {msg.rstrip()}")
        
        if '[OPT-012]' in ''.join(output_lines) and '[OPT-016]' in ''.join(output_lines):
            print("\n✓ study.optimize() が実行されました！")
            
            if duration > 10:
                print(f"✓ 実行時間も妥当です: {duration:.2f}秒")
                print("\n🎉 Optuna最適化が正常に実行されています！")
            else:
                print(f"⚠ 実行時間が短い: {duration:.2f}秒（期待: >10秒）")
        else:
            print("\n❌ study.optimize() の呼び出しが確認できません")
    else:
        print("❌ [OPT-*] メッセージが出力されていません")
        print("   optimize()メソッドが実行されていない可能性があります")
        
    if duration < 5 and result.get('optuna_executed'):
        print(f"\n⚠ 実行時間が短すぎます: {duration:.2f}秒")
        print("   3試行×2フォールド = 6回の学習が必要なため、10秒以上かかるはずです")
    
except requests.exceptions.Timeout:
    end_time = time.time()
    duration = end_time - start_time
    print(f"\n⏱ タイムアウト（{duration:.2f}秒）")
    print("   これは正常な場合があります（Optunaが長時間実行中）")
    
except Exception as e:
    print(f"\n❌ エラー: {e}")
    import traceback
    traceback.print_exc()

finally:
    print("\n" + "=" * 80)
    print("FastAPIを終了します...")
    process.terminate()
    try:
        process.wait(timeout=5)
    except:
        process.kill()
    print("テスト完了")
    print("=" * 80)
