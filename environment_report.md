# environment_report

## Runtime Versions

- python: 3.11.9
- jupyter core summary:
  - IPython 9.14.1
  - ipykernel 7.3.0
  - jupyter_client 8.9.1
  - jupyter_core 5.9.1
  - nbclient 0.11.0
  - nbformat 5.10.4
  - jupyter_server/notebook/jupyterlab/nbconvert: not installed

## Key Package Versions

- ipykernel: 7.3.0
- pyzmq: 27.1.0
- tornado: 6.5.7
- jupyter_client: 8.9.1
- jupyter_core: 5.9.1
- nbclient: 0.11.0
- nbformat: 5.10.4
- matplotlib: 3.10.0
- pandas: 2.3.3
- numpy: 2.3.5
- psutil: 7.2.2
- matplotlib backend: tkagg

## Windows Specific Checks

- MsMpEng.exe (Defender): running=True
- OneDrive.exe: running=True
- workspace path: C:\Users\yuki2\Documents\ws\keiba-ai-pro
- workspace contains OneDrive path: False

## SQLite Lock Check

- DB: keiba/data/keiba_ultimate.db
- journal_mode: wal
- locking_mode: normal
- BEGIN IMMEDIATE: ok
- race_results_ultimate_count: 575346

## Observed Runtime Warnings/Errors During Notebook Audit

- ipykernel warning repeated:
  - Kernel is running over TCP without encryption
- intermittent kernel communication error:
  - zmq.error.ZMQError: not a socket

## Assessment (Windows)

- antivirus impact:
  - Defender稼働中でI/O遅延の可能性はあるが、単独で主因と断定する証拠は不足
- OneDrive impact:
  - OneDriveプロセスは稼働中だが、workspaceはOneDrive配下ではないため同期干渉リスクは低い
- sqlite lock:
  - BEGIN IMMEDIATEが成功しており、恒常的なDBロック待ちは主因ではない
- matplotlib backend:
  - tkagg。今回のCell3は描画前の読み込みでtimeoutしており主因ではない
- ipykernel/pyzmq/tornado:
  - not-a-socket例外が観測され、Windows上のJupyter通信不安定要素として二次的リスクあり

## Conclusion

Cell3 timeoutの一次要因は重いデータ読み込み処理（長時間 + 高メモリ）。
Windowsのkernel通信エラー（pyzmq周辺）は再試行失敗を増幅しうる二次要因。

## Artifacts

- raw freeze file: environment_freeze.txt
- raw jupyter version file: environment_jupyter_version.txt
- raw python version file: environment_python_version.txt
