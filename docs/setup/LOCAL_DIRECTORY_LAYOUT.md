# ローカルディレクトリ構成

更新日: 2026-09-11

この文書は、Gitで管理するソース・確定証跡と、ローカルで再構築または再生成する実行データを分けるための基準です。

## 基本構成

```text
keiba-ai-pro/
├─ src/                         # Git追跡: Next.jsアプリ
├─ python-api/                  # Git追跡: FastAPIアプリ
│  ├─ .venv/                   # Git対象外: 唯一のローカルPython環境
│  └─ models/                  # 基準ファイルは追跡、生成モデルはGit対象外
├─ keiba/
│  ├─ keiba_ai/                # Git追跡: ML・研究コード
│  └─ data/                    # Git対象外: SQLite DBと実行時入力
│     ├─ keiba_ultimate.db     # 現在の研究DB
│     └─ live-validation-inputs/
├─ notebooks/                  # Git追跡: 00〜08のNotebookソース
│  ├─ data/                    # Git対象外: 特徴量・中間データ
│  └─ reports/                 # Git対象外: Notebook生成物
├─ reports/
│  ├─ README.md                # Git追跡: 運用規約
│  ├─ evidence/                # Git追跡: レビュー済み確定証跡
│  └─ generated/               # Git対象外: 再生成可能なレポート
├─ scripts/                    # Git追跡: 起動・検証・運用スクリプト
├─ docs/                       # Git追跡: 文書
├─ node_modules/               # Git対象外: npm ciで再構築
└─ .next/                      # Git対象外: npm run build等で再生成
```

## 依存環境

- Node依存はルートの`package-lock.json`を基準に`npm ci`で再構築します。
- PythonはPython 3.11を使用し、`python-api/.venv`だけを正式なローカル環境とします。
- APIの基本依存は`python-api/requirements.txt`、研究・Notebook用の追加依存は`keiba/requirements.txt`で管理します。後者は前者を参照します。
- WindowsではUTF-8モードが必要です。リポジトリの起動・テスト設定は`python-api/.venv/Scripts/python.exe -X utf8`を使用します。
- API実行だけなら`npm run setup:api`、研究・Notebookも使う場合は`npm run setup:research`で同じ仮想環境へ追加依存を導入します。どちらもPlaywrightのChromiumを含みます。
- Next.jsのstandalone出力はDockerビルドだけが`NEXT_STANDALONE_BUILD=1`で明示的に有効化します。ローカルビルドにはdotenvファイルやリポジトリ全体を複製しません。

## データと成果物の扱い

| 区分 | 置き場所 | Git | 扱い |
|---|---|---:|---|
| 研究DB | `keiba/data/keiba_ultimate.db` | 対象外 | 原本または正式な再生成手順から復元 |
| ライブ検証入力 | `keiba/data/live-validation-inputs/` | 対象外 | サーバー所有の実行時入力として保持 |
| 確定証跡 | `reports/evidence/` | 追跡 | 生成プログラムで上書きしない |
| 生成レポート | `reports/generated/` | 対象外 | 必要時に再生成 |
| Notebookソース | `notebooks/*.ipynb`、`notebooks/utils/` | 追跡 | 出力と実行回数を含めない |
| Notebook中間物 | `notebooks/data/`、`notebooks/reports/` | 対象外 | 再生成または外部退避 |
| キャッシュ・ログ | `.next/`、`cache/`、各種`*.log` | 対象外 | 原則として再生成し、長期保管しない |

SQLiteが使用中の`keiba_ultimate.db-wal`と`keiba_ultimate.db-shm`はSQLite自身に管理させます。DB稼働中に手動削除しません。
復元元、ハッシュ、監査項目は[研究DBの復元基準](RESEARCH_DB_RECOVERY.md)を参照してください。

## 再構築と確認

```powershell
npm ci
npm run setup:research
npm run build
python-api\.venv\Scripts\python.exe -X utf8 -m pip check
npm run test:python
```

ローカル起動はリポジトリ直下の`start-keiba-ai-pro.bat`、停止は`stop-keiba-ai-pro.bat`を使用します。起動スクリプトは依存、環境設定、DBが不足している場合に処理を停止します。

## 削除前の原則

1. Git追跡物と`reports/evidence/`を削除対象に含めない。
2. DB、生成モデル、特徴量など再生成コストが高いものは、必要に応じてリポジトリ外へ退避してハッシュを記録する。
3. 削除前に対象を一覧化し、`.env*`、`python-api/.venv`、`node_modules`、現DBを除外する。
4. 空のWAL/SHMであっても、現DBに対応するものはSQLiteに任せる。
