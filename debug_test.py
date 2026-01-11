"""
FastAPI + Optuna デバッグテスト
コンソール出力を直接確認できるように設計
"""
import subprocess
import requests
import time
import sys

# FastAPIを起動
print("\n" + "=" * 80)
print("=== FastAPI 起動 ===")
print("=" * 80)

try:
    # 既存プロセスを終了
    subprocess.run(
        ["taskkill", "/F", "/IM", "python.exe"],
        capture_output=True,
        timeout=10
    )
    time.sleep(3)
except:
    pass

# FastAPIプロセス起動
import os
os.chdir(r"C:\Users\yuki2\Documents\ws\keiba-ai-pro\python-api")
os.environ["PYTHONPATH"] = r"C:\Users\yuki2\Documents\ws\keiba-ai-pro"

python_exe = r"C:\Users\yuki2\.pyenv\pyenv-win\versions\3.10.11\python.exe"

# FastAPIをフォアグラウンドで起動（出力を見える）
print("FastAPI 起動中...\n")
process = subprocess.Popen(
    [python_exe, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    bufsize=1
)

# FastAPI起動を待つ（5秒）
time.sleep(7)

# リクエスト送信スレッド
def send_request():
    time.sleep(2)
    print("\n" + "=" * 80)
    print("=== テストリクエスト送信 ===")
    print("=" * 80 + "\n")
    
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
    
    try:
        response = requests.post(
            "http://localhost:8000/api/train",
            json=request_data,
            timeout=120
        )
        result = response.json()
        print(f"\nリクエスト成功")
        print(f"optuna_executed: {result.get('optuna_executed')}")
        print(f"training_time: {result.get('training_time')}")
    except Exception as e:
        print(f"リクエスト失敗: {e}")

# リクエストスレッド実行
import threading
request_thread = threading.Thread(target=send_request, daemon=False)
request_thread.start()

# FastAPI出力を表示
try:
    for line in process.stdout:
        if line:
            print(line, end='', flush=True)
        time.sleep(0.01)
except KeyboardInterrupt:
    pass
finally:
    process.terminate()
    process.wait()
