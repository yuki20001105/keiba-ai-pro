"""
Optunaトレースログテストスクリプト
FastAPIを起動し、リクエストを送信してログを確認
"""
import requests
import time
import subprocess
import os
import signal
from pathlib import Path

# ログファイルパス
log_file = Path(r"C:\Users\yuki2\Documents\ws\keiba-ai-pro\optuna_debug.log")

# ログファイルをクリア
if log_file.exists():
    log_file.unlink()
    print(f"✓ ログファイルをクリアしました: {log_file}")

# FastAPI起動
print("\n=== FastAPI起動 ===")
os.chdir(r"C:\Users\yuki2\Documents\ws\keiba-ai-pro\python-api")
os.environ["PYTHONPATH"] = r"C:\Users\yuki2\Documents\ws\keiba-ai-pro"

# 既存のプロセスを終了
try:
    subprocess.run(
        ["taskkill", "/F", "/IM", "python.exe"],
        capture_output=True,
        text=True
    )
    time.sleep(2)
except:
    pass

# FastAPIをバックグラウンドで起動
python_exe = r"C:\Users\yuki2\.pyenv\pyenv-win\versions\3.10.11\python.exe"
fastapi_process = subprocess.Popen(
    [python_exe, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True
)

print(f"FastAPI起動中 (PID: {fastapi_process.pid})...")
time.sleep(8)

# FastAPI起動確認
try:
    response = requests.get("http://localhost:8000/", timeout=5)
    print(f"✓ FastAPI起動成功: {response.json()['status']}")
except Exception as e:
    print(f"❌ FastAPI起動失敗: {e}")
    fastapi_process.terminate()
    exit(1)

# Optunaテストリクエスト送信
print("\n=== Optunaテストリクエスト送信 ===")
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

print(f"リクエスト: {request_data}")
print("送信中...")

start_time = time.time()
try:
    response = requests.post(
        "http://localhost:8000/api/train",
        json=request_data,
        timeout=180
    )
    end_time = time.time()
    duration = end_time - start_time
    
    print(f"\n✓ リクエスト成功")
    print(f"実行時間: {duration:.2f} 秒")
    
    result = response.json()
    print(f"optuna_executed: {result.get('optuna_executed', 'N/A')}")
    print(f"optuna_error: {result.get('optuna_error', 'なし')}")
    print(f"training_time: {result.get('training_time', 'N/A')}")
    print(f"auc: {result.get('auc', 'N/A')}")
    
except Exception as e:
    print(f"\n❌ エラー: {e}")

# ログファイルを読み取り
print("\n" + "=" * 80)
print("=== ログファイル内容 ===")
print("=" * 80)

if log_file.exists():
    with open(log_file, 'r', encoding='utf-8') as f:
        logs = f.read()
    print(logs)
    
    # TRACEメッセージを抽出
    print("\n" + "=" * 80)
    print("=== TRACE メッセージ抽出 ===")
    print("=" * 80)
    trace_lines = [line for line in logs.split('\n') if 'TRACE' in line]
    for line in trace_lines:
        print(line)
else:
    print("❌ ログファイルが見つかりません")

# FastAPIを終了
print("\n=== FastAPI終了 ===")
fastapi_process.terminate()
try:
    fastapi_process.wait(timeout=5)
    print("✓ FastAPI正常終了")
except:
    fastapi_process.kill()
    print("✓ FastAPI強制終了")
