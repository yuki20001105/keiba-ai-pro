"""Create a new evidence-only receipt; never fetch or calculate an index."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import zipfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "python-api"))
from feature_provenance.speed import REVISION, formula_audit, sha, source_objects


def exclusive_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def raw_inventory():
    cache = REPO / "keiba/data/fetch_cache.db"
    result = {"cache_path": str(cache), "response_bodies_read": 0, "archive_members_decoded": 0}
    db = sqlite3.connect(cache.as_uri() + "?mode=ro", uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        result["cache_url_metadata_rows"] = db.execute("SELECT COUNT(*) FROM http_cache INDEXED BY idx_http_cache_freshness").fetchone()[0]
        result["speed_url_candidates"] = [dict(zip(("url", "status", "fetched_at", "expires_at"), row)) for row in db.execute(
            "SELECT normalized_url,status,fetched_at,expires_at FROM http_cache INDEXED BY idx_http_cache_freshness "
            "WHERE lower(normalized_url) LIKE '%speed.html%' OR lower(normalized_url) LIKE '%speed_figure%' OR lower(normalized_url) LIKE '%time_index%'")]
    finally:
        db.rollback()
        db.close()
    result["raw_directories"] = []
    for relative in ("data/html", "keiba/data/html", "python-api/data/html"):
        path = REPO / relative
        result["raw_directories"].append({"path": relative, "exists": path.exists(),
                                           "entries": sorted(p.name for p in path.iterdir()) if path.exists() else []})
    result["archives"] = []
    for relative in ("keiba/data/db_export_for_chat.zip", "keiba/data/keiba_ultimate.zip"):
        path = REPO / relative
        if path.is_file():
            with zipfile.ZipFile(path) as archive:
                result["archives"].append({"path": relative, "member_names": archive.namelist(), "members_decoded": False})
    result["not_examined"] = ["arbitrary external/unconfigured storage", "keiba/data/db_quality_audit.7z contents"]
    result["conclusion_scope"] = "No speed endpoint response or per-start raw index archive identified in the examined stores; unexamined stores are not claimed empty."
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    targets = [root / "speed_figure_formula_audit.json", root / "speed_figure_provenance.md", root / "speed_provenance_manifest.json"]
    if any(path.exists() for path in targets) or (root / "provenance/sources").exists():
        raise ValueError("new artifact paths required; old receipts cannot be overwritten")
    sources = source_objects(REPO)
    current = REPO / "keiba/keiba_ai/feature_engineering.py"
    report = formula_audit(sources, current_feature_source=current.read_text(encoding="utf-8"),
                           current_producer_exists=(REPO / "python-api/scraping/speed_figure.py").exists())
    report["local_raw_inventory"] = raw_inventory()
    source_dir = root / "provenance/sources" / REVISION
    source_dir.mkdir(parents=True)
    files = []
    for path, item in sources.items():
        destination = source_dir / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(item["text"].encode("utf-8"))
        item["archive_path"] = str(destination.relative_to(root))
        report["source_objects"][path]["archive_path"] = item["archive_path"]
        files.append(destination)
    report["current_source_sha256"] = {str(current.relative_to(REPO)): sha(current.read_bytes()),
                                       "keiba/keiba_ai/speed_deviation.py": sha((REPO / "keiba/keiba_ai/speed_deviation.py").read_bytes())}
    exclusive_json(targets[0], report)
    lines = ["# Speed Figure provenance / formula audit", "", "分類: **B（外部指数の取得）**。再現検証: **未成立**。追加学習・評価: **停止**。", "",
             "netkeiba `race/speed.html` の `SpeedIndex_Table` に表示された7整数をそのまま保存する旧実装をGitオブジェクトから確認した。現行treeにはproducerがなく、互換APIの `speed_figures_df` は未使用。", "",
             "## 確認できたこと", "", "- 非表示のソート用spanを除去し、末尾 `*` を落として整数化する。欠損・変換不能はNoneで、0補完や独自の丸めは行わない。",
             "- 保存キーはrace_id＋horse_number。INSERT OR REPLACEであり、created_atは保存時刻である。元走・provider時刻・式version・raw hashは記録しない。",
             "- 旧学習側は6列を直接joinし、前走−2走前の差のみ作る。avg_5_indexは使っていない。履歴時点の厳密な境界検査はない。", "",
             "## 公式公開説明と未確定部分", "", "公式ガイドでも最高は過去1年、5走平均は直近5走のうち今回と同じ芝/ダート、距離/コースは当該条件の最高と説明されている。ただし平均のFAQには異条件を含む平均への注意印の説明もあり、例外・版・丸め・欠損時分母は確定しない。旧parserが除去した末尾*はDBから復元できない。",
             "公式FAQは同じ走破タイムでも展開・位置取り・不利で指数が変わると説明する。確認した資料には1走の正式式・係数・丸めの完全仕様はなかった。『どこにも公表されていない』『知的財産のため非公開』とは断定しない。", "",
             "距離÷タイムや速度偏差は別の特徴量であり、この外部指数の再現式として代用しない。DBの個別行には生成ソースの証跡がないため、復元したコードが各行を実際に生成したという完全な行単位証明もない。", "",
             "## 結論", "", "`RECONSTRUCTION_VALIDATED=false`。再構築レコード・追加特徴・2026比較は作らず、比較値はnullとする。理由は `formula_unavailable`。raw項目の保有率が高くても正式な必要入力一覧自体が未確定なので、再構築可能率と呼ばない。", "",
             "将来の解決には、外部指数の正式な式・補正値・版と過去時点の入力証跡、または取得時点と元走の紐付いた外部指数履歴が必要。原DB、モデル、99特徴量、Track Aを変更せず、新規成果物のみ作成した。", "",
             "## ソース証拠", ""]
    for item in report["evidence"]:
        lines.append(f"- `{item['revision']}:{item['path']}` `{item['function']}` lines {item['line_start']}–{item['line_end']}（このrootのprovenance/sourcesに同一内容を保存）。")
    lines += ["", "## 公式公開資料（2026-09-20確認）", ""]
    for item in report["official_public_sources"]:
        lines.append(f"- [{item['title']}]({item['url']})")
    with targets[1].open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
    files += targets[:2]
    manifest = {"source_revision": REVISION,
                "files": {str(path.relative_to(root)): {"sha256": sha(path.read_bytes()), "size": path.stat().st_size} for path in files},
                "audit_source_sha256": {str(path.relative_to(REPO)): sha(path.read_bytes()) for path in [Path(__file__), REPO / "python-api/feature_provenance/speed.py"]},
                "production_writes": False, "script_network_requests": 0, "official_documentation_browsed_separately": True, "training_runs": 0}
    exclusive_json(targets[2], manifest)
    print(json.dumps({"output": str(root), "classification": report["classification"], "RECONSTRUCTION_VALIDATED": False,
                      "status": report["reconstruction"]["status"], "source_revision": REVISION}))


if __name__ == "__main__":
    main()
