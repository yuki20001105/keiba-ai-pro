"""Seal the isolated, partial authenticated member pilot without app mutations."""
from __future__ import annotations

import json
from pathlib import Path
import re

from training_reconstruction_common import (
    ROOT, invariant_projection, now, protection, read, sha, write,
)
from verify_market_segments_outputs import verify_files

PILOT = ROOT / "reports/generated/model-experiments/netkeiba-authenticated-pilot-v1"
PRIOR = {
    "training-data-reconstruction-v1": "4442ce3e50a2fab3b80ba079f4afdb0e3e206543e8ba75f2528c9302163047cf",
    "training-data-reconstruction-v2": "9812dd3d4823716e3e0e0cf7eedf543f25352ceb879239e8e9aaa4627cc9e1f4",
    "netkeiba-authenticated-source-audit-v1": "0b1bb50884a806b3dac0542ec61d56ce279d06f528b1752f59d4ef8865422224",
}
CODE = [
    "scripts/audit_authenticated_training_pilot.py",
    "scripts/audit_authenticated_speed_pilot.py",
    "scripts/report_authenticated_member_pilot.py",
    "scripts/seal_authenticated_member_pilot.py",
    "python-api/tests/test_authenticated_training_pilot.py",
    "python-api/tests/test_authenticated_speed_pilot.py",
]


def fnv_utf16(text):
    data = text.encode("utf-16-le")
    value = 2166136261
    for offset in range(0, len(data), 2):
        value = ((value ^ int.from_bytes(data[offset:offset + 2], "little")) * 16777619) & 0xffffffff
    return {"length_utf16": len(data) // 2, "fnv1a_utf16": format(value, "x")}


def verify_transfers():
    checked = []
    for race, length, digest in (("202106010201", 2463, "3e7a5304"),
                                ("202206010101", 2547, "c0053f25")):
        path = PILOT / f"source/{race}-training-projection.json"
        source = read(path)
        fields = ["horse_number", "horse_name", "horse_href", "training_date", "course",
                  "condition", "rider", "slots", "pace", "grade"]
        canonical = json.dumps([[row[name] for name in fields] for row in source["rows"]],
                               ensure_ascii=False, separators=(",", ":"))
        measured = fnv_utf16(canonical)
        assert measured == {"length_utf16": length, "fnv1a_utf16": digest}
        checked.append({"source_file": path.relative_to(PILOT).as_posix(),
                        "source_sha256": sha(path), "field_order": fields,
                        "canonicalization": "Compact JSON array of rows as field-ordered arrays; UTF-16 code units",
                        "matches_browser_observation": True, **measured})
    path = PILOT / "source/202609030411-speed-cells.json"
    fields = ["horse_number", "horse_name", "horse_href", "cells"]
    canonical = json.dumps([[row[name] for name in fields] for row in read(path)["rows"]],
                           ensure_ascii=False, separators=(",", ":"))
    measured = fnv_utf16(canonical)
    assert measured == {"length_utf16": 14934, "fnv1a_utf16": "94279653"}
    checked.append({"source_file": path.relative_to(PILOT).as_posix(),
                    "source_sha256": sha(path), "field_order": fields,
                    "matches_browser_observation": True, **measured})
    path = PILOT / "source/202606040611-training-table.html"
    measured = fnv_utf16(path.read_bytes().decode("utf-8"))
    assert measured == {"length_utf16": 15575, "fnv1a_utf16": "6c4cccb5"}
    checked.append({"source_file": path.relative_to(PILOT).as_posix(),
                    "source_sha256": sha(path), "canonicalization": "Saved sanitized DOM fragment plus final LF",
                    "matches_browser_observation": True, **measured})
    return checked


def main():
    assert not (PILOT / "artifact_manifest.json").exists(), "append-only seal exists"
    assert (PILOT / "report/summary.md").is_file(), "report not yet complete"
    before = read(PILOT / "protection-before.json")["state"]
    after = protection()
    assert invariant_projection(before) == invariant_projection(after), "protected state changed"
    prior_verified = {}
    for name, expected in PRIOR.items():
        base = PILOT.parent / name
        manifest_path = base / "artifact_manifest.json"
        assert sha(manifest_path) == expected, f"old manifest changed: {name}"
        manifest = read(manifest_path)
        count = verify_files(base, manifest["files"])
        verify_files(ROOT, manifest.get("code_and_tests_sha256", {}))
        prior_verified[name] = {"manifest_sha256": expected, "verified_files": count}

    mapped = read(PILOT / "comparison-mapped/parser-comparison-summary.json")
    speed = read(PILOT / "speed-comparison/summary.json")
    assert mapped["experimental_record_count"] == 13
    assert mapped["finding_counts"]["DISTANCE_LABEL_CONFLICT_WITH_CONTRACT"] == 21
    assert mapped["finding_counts"]["LAP_LABEL_CONFLICT_WITH_CONTRACT"] == 21
    assert speed["compared_cells"] == 126 and speed["mismatched_cells"] == 0
    assert speed["cell_relation_counts"] == {"equal_integer": 114, "both_missing": 12}
    assert read(PILOT / "test-receipt.json")["passed"] == 38
    transfers = verify_transfers()
    historical_identity = read(PILOT / "historical-identity-audit.json")
    assert len(historical_identity["sources"]) == 2
    assert all(row["horse_id_match_count"] == 16 and row["horse_name_match_count"] == 16
               and not row["identity_mismatches"] for row in historical_identity["sources"])

    # Bounded content checks; report paths/names only, never offending content.
    secret_patterns = [
        re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
        re.compile(r"(?i)bearer\s+[A-Za-z0-9_.-]{20,}"),
        re.compile(r"(?i)(?:access_token|session_cookie|password)\s*[=:]\s*['\"]?[A-Za-z0-9+/_.-]{12,}"),
    ]
    scanned = []
    for path in sorted(PILOT.rglob("*")):
        if not path.is_file():
            continue
        body = path.read_text(encoding="utf-8")
        assert not any(pattern.search(body) for pattern in secret_patterns), (
            "possible sensitive data; review locally: " + path.relative_to(PILOT).as_posix())
        if path.suffix == ".html":
            assert not re.search(r"(?i)<(?:script|form|input|iframe)\b|\bonclick=", body)
        scanned.append(path.relative_to(PILOT).as_posix())

    write(PILOT / "protection-after.json", {
        "observed_at": now(), "state": after,
        "invariant_projection_matches": True,
        "prior_manifests_verified": prior_verified,
        "collector_dynamic_state_excluded_from_equality": True,
        "no_production_or_frozen_research_mutations": True,
    })
    write(PILOT / "security-check.json", {
        "observed_at": now(), "bounded_pattern_scan": "PASS", "scanned_files": scanned,
        "raw_http_responses_saved": False, "session_or_credentials_exported": False,
        "limits": "Pattern checks support minimization; they are not a proof against every possible secret format.",
    })
    write(PILOT / "transfer-verification.json", {
        "observed_at": now(), "status": "PASS", "captures": transfers,
        "limits": "Reproduces browser-recorded noncryptographic transfer checks. Not a publisher authenticity attestation or historical publication proof.",
    })
    with (PILOT / "validation-addendum.md").open("x", encoding="utf-8") as stream:
        stream.write("# 親タスクの最終検証追補\n\n"
                     "report/audit.md作成後、2021・2022年サンプル計32頭の馬番・馬ID・馬名を固定DBと独立照合し、全件一致しました。"
                     "historical-identity-audit.jsonを参照してください。\n\n"
                     "4ページ分の保存物について、ブラウザで記録した転送チェック値を再現しました。"
                     "正規化する項目順とUTF-16 FNV実装はseal_authenticated_member_pilot.py、結果はtransfer-verification.jsonにあります。"
                     "report/audit.mdの外部馬照合・転送再現未実施という記載を、この追加検証で補います。"
                     "非暗号学的チェックは発行元の真正性や当時の公開日時を証明しません。\n\n"
                     "固定研究成果物・H04関連保護対象・使用中モデル・Track Aプロトコル・既存の未コミット変更は照合前後で不変です。"
                     "常駐collectorの正常な時刻/進捗変化だけは一致条件から除外しました。\n\n"
                     "Track Aの保護時間帯に追加ブラウザ移動を見送りました。全フェーズは未完了の限定パイロットです。"
                     "大量取得・学習・既存DB修正・commit・pushはしていません。\n")
    files = {p.relative_to(PILOT).as_posix(): {"sha256": sha(p), "bytes": p.stat().st_size}
             for p in sorted(PILOT.rglob("*")) if p.is_file()}
    manifest = {
        "schema": "authenticated-member-pilot-v1", "recorded_at": now(),
        "status": "PARTIAL_PILOT_VERIFIED", "phase_complete": False,
        "TRAINING_FEATURE_RESEARCH_READY": False, "SPEED_FEATURE_RESEARCH_READY": False,
        "bulk_backfill_started": False, "models_trained": 0, "committed_or_pushed": False,
        "files": files,
        "code_and_tests_sha256": {name: {"sha256": sha(ROOT / name), "bytes": (ROOT / name).stat().st_size}
                                  for name in CODE},
    }
    write(PILOT / "artifact_manifest.json", manifest)
    verify_files(PILOT, files)
    verify_files(ROOT, manifest["code_and_tests_sha256"])
    print(json.dumps({"status": manifest["status"], "verified_artifacts": len(files),
                      "verified_code_and_tests": len(CODE), "protected_state_unchanged": True,
                      "manifest_sha256": sha(PILOT / "artifact_manifest.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
