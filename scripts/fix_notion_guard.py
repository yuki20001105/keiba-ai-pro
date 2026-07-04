"""Cell P_Notion のガード条件を修正するスクリプト"""
import json, pathlib

NB = pathlib.Path("docs/features/feature_inspection.ipynb")

with open(NB, encoding="utf-8") as f:
    nb = json.load(f)

for ci, cell in enumerate(nb["cells"]):
    src = cell.get("source", [])
    src_text = "".join(src)
    if "LLM \u30ec\u30dd\u30fc\u30c8\u304c\u672a\u751f\u6210" in src_text:
        for li, line in enumerate(src):
            if "LLM \u30ec\u30dd\u30fc\u30c8\u304c\u672a\u751f\u6210" in line:
                idx_elif = li - 1
                new_lines = [
                    src[idx_elif],  # elif行はそのまま
                    '    _md_file = ROOT / "docs" / "reports" / "feature_llm_report.md"\n',
                    '    if _md_file.exists():\n',
                    '        _report_text = _md_file.read_text(encoding="utf-8")\n',
                    '        print(f"\u2139 \u30d5\u30a1\u30a4\u30eb\u304b\u3089\u8aad\u307f\u8fbc\u307f: {_md_file.name} ({len(_report_text):,} \u6587\u5b57)")\n',
                    '    else:\n',
                    '        print("\u26a0 LLM \u30ec\u30dd\u30fc\u30c8\u304c\u672a\u751f\u6210\u3067\u3059\u3002\u5148\u306b Cell M3 \u3092\u5b9f\u884c\u3057\u3066\u304f\u3060\u3055\u3044")\n',
                ]
                src[idx_elif : li + 1] = new_lines
                cell["source"] = src
                print(f"Updated cell {ci}, replaced lines {idx_elif}-{li}")
                for l in new_lines:
                    print("  >>", repr(l))
                break

with open(NB, "w", encoding="utf-8") as f:
    json.dump(nb, f, ensure_ascii=False, indent=1)
print("Saved.")
