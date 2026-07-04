"""
Cell P_Notion のガード条件を正しく修正するスクリプト。
elif/_report_text ブロックを:
  1. メインブロック前の「読み込みプレアンブル」に移動
  2. 元の elif を「elif not _report_text:」に変更（ファイル未発見時の警告）
"""
import json, pathlib

NB = pathlib.Path("docs/features/feature_inspection.ipynb")

with open(NB, encoding="utf-8") as f:
    nb = json.load(f)

TARGET_MARKER = "LLM \u30ec\u30dd\u30fc\u30c8\u304c\u672a\u751f\u6210"

for ci, cell in enumerate(nb["cells"]):
    src = cell.get("source", [])
    if TARGET_MARKER in "".join(src):
        # "# ── 実行" コメント行のインデックスを探す
        exec_line_idx = None
        elif_line_idx = None
        for i, line in enumerate(src):
            if "# ── 実行" in line:
                exec_line_idx = i
            if 'elif "_report_text" not in dir()' in line:
                elif_line_idx = i

        print(f"exec_line_idx={exec_line_idx}, elif_line_idx={elif_line_idx}")
        if exec_line_idx is None or elif_line_idx is None:
            print("ERROR: could not find markers")
            break

        # elif ブロックの終わりを見つける（次の空行 or 別トップレベル行）
        elif_end_idx = elif_line_idx + 1
        while elif_end_idx < len(src) and (src[elif_end_idx].startswith(" ") or src[elif_end_idx] == "\n"):
            elif_end_idx += 1

        print(f"elif block: {elif_line_idx} to {elif_end_idx-1}")
        for l in src[elif_line_idx:elif_end_idx]:
            print("  OLD:", repr(l))

        # プレアンブル（実行ブロックの前に挿入）
        preamble = [
            "# _report_text がカーネルにない場合はファイルから読み込む\n",
            'if "_report_text" not in dir() or not _report_text:\n',
            '    _md_file = ROOT / "docs" / "reports" / "feature_llm_report.md"\n',
            '    if _md_file.exists():\n',
            '        _report_text = _md_file.read_text(encoding="utf-8")\n',
            '        print(f"\u2139 \u30d5\u30a1\u30a4\u30eb\u304b\u3089\u8aad\u307f\u8fbc\u307f: {_md_file.name} ({len(_report_text):,} \u6587\u5b57)")\n',
            '    else:\n',
            '        _report_text = ""\n',
            '\n',
        ]

        # 新しい elif 行（ファイル未発見 or 空の場合の警告）
        new_elif = [
            'elif not _report_text:\n',
            '    print("\u26a0 LLM \u30ec\u30dd\u30fc\u30c8\u304c\u672a\u751f\u6210\u3067\u3059\u3002\u5148\u306b Cell M3 \u3092\u5b9f\u884c\u3057\u3066\u304f\u3060\u3055\u3044")\n',
            '\n',
        ]

        # 変更を適用:
        # 1. elif ブロック全体を new_elif に置き換える
        src[elif_line_idx:elif_end_idx] = new_elif
        # 2. 実行ブロックの前にプレアンブルを挿入
        src[exec_line_idx:exec_line_idx] = preamble

        cell["source"] = src
        print(f"Done. Updated cell {ci}")
        print("Preamble inserted at:", exec_line_idx)
        print("elif replaced at:", elif_line_idx + len(preamble))
        break

with open(NB, "w", encoding="utf-8") as f:
    json.dump(nb, f, ensure_ascii=False, indent=1)
print("Saved.")
