#!/usr/bin/env python3
"""
auto_commit.py - ステージされた変更を自動分析してコミットメッセージを生成する
Usage:
  python scripts/auto_commit.py           # 対話モード
  python scripts/auto_commit.py --yes     # 確認なしで即コミット
  python scripts/auto_commit.py --dry-run # メッセージ生成のみ（コミットしない）
"""
import subprocess
import sys
import os
import re


def run_git(args):
    result = subprocess.run(
        ["git"] + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


def get_staged_files():
    """ステージ済みファイルを status 別に取得"""
    output = run_git(["diff", "--staged", "--name-status"])
    files = {"A": [], "M": [], "D": [], "R": []}
    for line in output.split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if parts:
            status = parts[0][0]
            filename = parts[-1]
            if status in files:
                files[status].append(filename)
    return files


def get_staged_diff():
    """ステージ済みの差分テキストを取得"""
    return run_git(["diff", "--staged", "--unified=2"])


# ──────────────────────────────────────────
# コミットタイプの判定
# ──────────────────────────────────────────
TYPE_RULES = [
    # (マッチ条件, コミットタイプ)
    (lambda f, d: any(".github" in x for x in f["A"] + f["M"]),       "ci"),
    (lambda f, d: all(x.endswith(".md") for x in f["A"] + f["M"] + f["D"] if x), "docs"),
    (lambda f, d: any("test" in x.lower() or "spec" in x.lower() for x in f["A"] + f["M"]), "test"),
    (lambda f, d: any(re.search(r"fix|error|bug|crash|typo", d, re.I) for _ in [1]), "fix"),
    (lambda f, d: bool(f["A"]) and not f["M"] and not f["D"],          "feat"),
    (lambda f, d: bool(f["D"]) and not f["A"] and not f["M"],          "chore"),
]

def detect_type(files, diff):
    all_files = files["A"] + files["M"] + files["D"] + files["R"]
    for rule, t in TYPE_RULES:
        try:
            if rule(files, diff):
                return t
        except Exception:
            pass
    return "feat"


# ──────────────────────────────────────────
# 説明文の生成
# ──────────────────────────────────────────
PATH_LABELS = {
    "src/app": "ページ",
    "src/components": "コンポーネント",
    "python-api": "APIサーバー",
    "keiba/keiba_ai": "AIモジュール",
    ".github/workflows": "CIワークフロー",
    "supabase": "DB設定",
    "scripts": "スクリプト",
    "docs": "ドキュメント",
}

def label_for(filepath):
    for path, label in PATH_LABELS.items():
        if path in filepath:
            basename = os.path.splitext(os.path.basename(filepath))[0]
            # page.tsx → ディレクトリ名をページ名として使用
            if basename == "page":
                parts = filepath.replace("\\", "/").split("/")
                idx = next((i for i, p in enumerate(parts) if p == "app"), -1)
                if idx != -1 and idx + 1 < len(parts) - 1:
                    return f"{parts[idx + 1]}{label}"
            return f"{basename}({label})"
    return os.path.splitext(os.path.basename(filepath))[0]

def action_word(files):
    if files["A"] and not files["M"] and not files["D"]:
        return "追加"
    if files["D"] and not files["A"] and not files["M"]:
        return "削除"
    return "更新"

def generate_description(files):
    all_files = files["A"] + files["M"] + files["D"] + files["R"]
    labels = []
    seen = set()
    for f in all_files:
        lbl = label_for(f)
        if lbl not in seen:
            seen.add(lbl)
            labels.append(lbl)

    action = action_word(files)

    if len(labels) == 0:
        return f"{len(all_files)}ファイルを{action}"
    elif len(labels) <= 2:
        return f"{'、'.join(labels)}を{action}"
    else:
        return f"{'、'.join(labels[:2])} 他{len(labels) - 2}件を{action}"


# ──────────────────────────────────────────
# メイン処理
# ──────────────────────────────────────────
def main():
    dry_run = "--dry-run" in sys.argv
    auto_yes = "--yes" in sys.argv or "-y" in sys.argv

    # 変更があるか確認 → なければ git add -A
    files = get_staged_files()
    all_files = files["A"] + files["M"] + files["D"] + files["R"]

    if not all_files:
        print("[INFO] ステージ済みファイルがないため git add -A を実行します...")
        subprocess.run(["git", "add", "-A"])
        files = get_staged_files()
        all_files = files["A"] + files["M"] + files["D"] + files["R"]

    if not all_files:
        print("[INFO] 変更はありません。")
        sys.exit(0)

    # 変更ファイル一覧を表示
    print(f"\n変更ファイル ({len(all_files)}件):")
    for f in files["A"]:  print(f"  [+] {f}")
    for f in files["M"]:  print(f"  [M] {f}")
    for f in files["D"]:  print(f"  [-] {f}")
    for f in files["R"]:  print(f"  [R] {f}")

    # コミットメッセージ生成
    diff = get_staged_diff()
    commit_type = detect_type(files, diff)
    description = generate_description(files)
    commit_message = f"{commit_type}: {description}"

    print(f"\n自動生成メッセージ: {commit_message}")

    if dry_run:
        print("\n[dry-run] コミットは実行されませんでした。")
        return

    # 確認プロンプト
    if not auto_yes:
        try:
            answer = input("\n[Y] コミット / [e] メッセージ編集 / [n] キャンセル : ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nキャンセルしました。")
            sys.exit(0)

        if answer == "n":
            print("キャンセルしました。")
            sys.exit(0)
        elif answer in ("e", "edit"):
            try:
                new_msg = input(f"新しいメッセージ [{commit_message}]: ").strip()
            except (EOFError, KeyboardInterrupt):
                new_msg = ""
            if new_msg:
                commit_message = new_msg

    # コミット実行
    result = subprocess.run(["git", "commit", "-m", commit_message])
    if result.returncode == 0:
        print(f"\n[OK] コミット完了: {commit_message}")
    else:
        print("[ERROR] コミットに失敗しました。")
        sys.exit(1)


if __name__ == "__main__":
    main()
