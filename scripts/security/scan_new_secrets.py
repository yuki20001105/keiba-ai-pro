#!/usr/bin/env python3
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Set, Tuple

BASE_REF = "origin/develop"

ALLOWLIST_EXACT: Dict[str, str] = {
    "e2e-dummy-service-role-key": "E2E dummy credential value",
    "e2e-dummy-anon-key": "E2E dummy credential value",
    "sk_test_e2e_dummy": "E2E dummy Stripe test placeholder",
}

TOKEN_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"(?P<value>AKIA[0-9A-Z]{16})")),
    ("github_token", re.compile(r"(?P<value>ghp_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("slack_token", re.compile(r"(?P<value>xox[baprs]-[A-Za-z0-9-]{20,})")),
    ("stripe_live_key", re.compile(r"(?P<value>sk_live_[A-Za-z0-9]{16,})")),
    ("private_key_block", re.compile(r"(?P<value>-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----)")),
    ("jwt_like_secret", re.compile(r"(?P<value>eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,})")),
    (
        "credential_context_high_entropy",
        re.compile(
            r"(?i)(?:secret|token|api[_-]?key|password|passwd|private[_-]?key|access[_-]?key|service[_-]?role[_-]?key)"
            r"\s*[:=]\s*['\"]?(?P<value>[A-Za-z0-9+/=_-]{20,})['\"]?"
        ),
    ),
]


@dataclass
class Finding:
    file: str
    line: int
    kind: str
    value_length: int
    fingerprint: str


def _run(cmd: List[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "command failed")
    return proc.stdout


def _git_has_changes() -> bool:
    return bool(_run(["git", "status", "--porcelain"]).strip())


def _tracked_diff_text() -> str:
    return _run(["git", "diff", "--unified=0", BASE_REF, "--", "."])


def _untracked_files() -> List[str]:
    out = _run(["git", "ls-files", "--others", "--exclude-standard"])
    return [line.strip() for line in out.splitlines() if line.strip()]


def _collect_added_lines(diff_text: str) -> List[Tuple[str, int, str]]:
    added: List[Tuple[str, int, str]] = []
    current_file = ""
    current_new_line = 0
    for raw in diff_text.splitlines():
        if raw.startswith("+++ b/"):
            current_file = raw[6:]
            continue
        if raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            current_new_line = int(m.group(1)) if m else 0
            continue
        if not current_file:
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            added.append((current_file, current_new_line, raw[1:]))
            current_new_line += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            continue
        else:
            if not raw.startswith("\\"):
                current_new_line += 1
    return added


def _collect_untracked_lines(files: List[str]) -> List[Tuple[str, int, str]]:
    out: List[Tuple[str, int, str]] = []
    for file_path in files:
        path = Path(file_path)
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        for idx, line in enumerate(text.splitlines(), start=1):
            out.append((file_path, idx, line))
    return out


def _is_allowlisted(value: str) -> Tuple[bool, str]:
    reason = ALLOWLIST_EXACT.get(value)
    return (reason is not None, reason or "")


def _fingerprint12(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _coverage_error(has_changes: bool, scanned_file_count: int, scanned_line_count: int) -> str | None:
    if has_changes and (scanned_file_count == 0 or scanned_line_count == 0):
        return "fail-closed: changes exist but scanner input coverage is zero"
    return None


def main() -> int:
    has_changes = _git_has_changes()
    tracked_diff = _tracked_diff_text()
    tracked_added_lines = _collect_added_lines(tracked_diff)
    untracked_files = _untracked_files()
    untracked_added_lines = _collect_untracked_lines(untracked_files)
    added_lines = tracked_added_lines + untracked_added_lines

    scanned_files: Set[str] = set()
    scanned_line_count = 0

    detected = 0
    excluded = 0
    candidates: List[Finding] = []
    exclusions: List[Dict[str, str]] = []
    seen: Set[Tuple[str, int, str, str]] = set()

    for file_path, line_no, text in added_lines:
        scanned_files.add(file_path)
        scanned_line_count += 1
        for kind, pattern in TOKEN_PATTERNS:
            for match in pattern.finditer(text):
                matched_value = match.group("value")
                fp = _fingerprint12(matched_value)
                key = (file_path, line_no, kind, fp)
                if key in seen:
                    continue
                seen.add(key)

                detected += 1
                allow, reason = _is_allowlisted(matched_value)
                if allow:
                    excluded += 1
                    exclusions.append({
                        "file": file_path,
                        "line": str(line_no),
                        "reason": reason,
                        "value": matched_value,
                    })
                    continue

                candidates.append(
                    Finding(
                        file=file_path,
                        line=line_no,
                        kind=kind,
                        value_length=len(matched_value),
                        fingerprint=fp,
                    )
                )

    coverage_error = _coverage_error(
        has_changes=has_changes,
        scanned_file_count=len(scanned_files),
        scanned_line_count=scanned_line_count,
    )

    should_fail = len(candidates) > 0 or coverage_error is not None

    report = {
        "base": BASE_REF,
        "tracked_added_line_count": len(tracked_added_lines),
        "untracked_file_count": len(untracked_files),
        "untracked_line_count": len(untracked_added_lines),
        "scanned_file_count": len(scanned_files),
        "scanned_line_count": scanned_line_count,
        "has_working_tree_changes": has_changes,
        "coverage_error": coverage_error,
        "detected_count": detected,
        "excluded_count": excluded,
        "secret_candidate_count": len(candidates),
        "allowlist": [{"value": k, "reason": v} for k, v in ALLOWLIST_EXACT.items()],
        "excluded_items": exclusions,
        "candidates": [f.__dict__ for f in candidates],
    }

    out = Path("reports") / "security_secret_scan.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "tracked_added_line_count": len(tracked_added_lines),
        "untracked_file_count": len(untracked_files),
        "untracked_line_count": len(untracked_added_lines),
        "scanned_file_count": len(scanned_files),
        "scanned_line_count": scanned_line_count,
        "detected_count": detected,
        "excluded_count": excluded,
        "secret_candidate_count": len(candidates),
        "coverage_error": coverage_error,
        "report": str(out),
    }, ensure_ascii=False))

    return 0 if not should_fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
