#!/usr/bin/env python3
import json
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Set, Tuple

BASE_REF = "origin/develop"

PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    ("test.skip", re.compile(r"\btest\.skip\s*\(")),
    ("describe.skip", re.compile(r"\bdescribe\.skip\s*\(")),
    ("it.skip", re.compile(r"\bit\.skip\s*\(")),
    ("pytest.skip", re.compile(r"\bpytest\.skip\s*\(")),
    ("pytest.mark.skip", re.compile(r"@pytest\.mark\.skip\b")),
    ("pytest.mark.xfail", re.compile(r"@pytest\.mark\.xfail\b")),
    ("xfail", re.compile(r"\bxfail\s*\(")),
    ("assert_true", re.compile(r"\bassert\s+True\b")),
    ("except_pass", re.compile(r"except\s+Exception\s*:\s*pass\b")),
]

TEST_CODE_PATH = re.compile(r"(^e2e/)|(^src/__tests__/)|(^python-api/tests/)|(_test\.py$)|(\.test\.[jt]sx?$)|(\.spec\.[jt]sx?$)")

DELETED_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    ("deleted_js_test_call", re.compile(r"\b(?:test|it)\s*\(")),
    ("deleted_py_test_function", re.compile(r"^\s*def\s+test_[A-Za-z0-9_]*\s*\(")),
    ("deleted_expect", re.compile(r"\bexpect\s*\(")),
    ("deleted_assert", re.compile(r"\bassert\b")),
    ("deleted_pytest_raises", re.compile(r"\bpytest\.raises\s*\(")),
]

ALLOWLIST_EXACT: Dict[str, str] = {}


def _run(cmd: List[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "command failed")
    return proc.stdout


def _git_has_changes() -> bool:
    return bool(_run(["git", "status", "--porcelain"]).strip())


def _tracked_diff_text() -> str:
    return _run(["git", "diff", "--unified=0", BASE_REF, "--", "."])


def _deleted_files() -> List[str]:
    out = _run(["git", "diff", "--name-only", "--diff-filter=D", BASE_REF, "--", "."])
    return [line.strip() for line in out.splitlines() if line.strip()]


def _untracked_files() -> List[str]:
    out = _run(["git", "ls-files", "--others", "--exclude-standard"])
    return [line.strip() for line in out.splitlines() if line.strip()]


def _collect_diff_lines(diff_text: str) -> Tuple[List[Tuple[str, int, str]], List[Tuple[str, int, str]]]:
    added: List[Tuple[str, int, str]] = []
    deleted: List[Tuple[str, int, str]] = []
    current_file = ""
    current_new_line = 0
    current_old_line = 0
    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            current_file = ""
            current_new_line = 0
            current_old_line = 0
            continue
        if raw.startswith("+++ b/"):
            current_file = raw[6:]
            continue
        if raw.startswith("--- a/"):
            continue
        if raw.startswith("@@"):
            m_new = re.search(r"\+(\d+)", raw)
            m_old = re.search(r"-(\d+)", raw)
            current_new_line = int(m_new.group(1)) if m_new else 0
            current_old_line = int(m_old.group(1)) if m_old else 0
            continue
        if not current_file:
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            added.append((current_file, current_new_line, raw[1:]))
            current_new_line += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            deleted.append((current_file, current_old_line, raw[1:]))
            current_old_line += 1
        else:
            if not raw.startswith("\\"):
                current_new_line += 1
                current_old_line += 1
    return added, deleted


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


def _is_comment_or_doc(line: str) -> bool:
    s = line.strip()
    return (
        not s
        or s.startswith("//")
        or s.startswith("#")
        or s.startswith("*")
        or s.startswith("/*")
        or s.startswith("*/")
        or s.startswith("-")
    )


def _is_allowlisted(file_path: str, line: str) -> Tuple[bool, str]:
    key = f"{file_path}:{line.strip()}"
    reason = ALLOWLIST_EXACT.get(key)
    return (reason is not None, reason or "")


def _coverage_error(has_changes: bool, scanned_file_count: int, scanned_line_count: int) -> str | None:
    if has_changes and (scanned_file_count == 0 or scanned_line_count == 0):
        return "fail-closed: changes exist but scanner input coverage is zero"
    return None


def main() -> int:
    has_changes = _git_has_changes()
    tracked_diff = _tracked_diff_text()
    tracked_added, tracked_deleted = _collect_diff_lines(tracked_diff)
    untracked_files = _untracked_files()
    untracked_added = _collect_untracked_lines(untracked_files)
    deleted_files = _deleted_files()
    added = tracked_added + untracked_added

    hits = []
    exclusions = []
    scanned_files: Set[str] = set()
    scanned_line_count = 0

    for file_path in deleted_files:
        if TEST_CODE_PATH.search(file_path):
            hits.append({
                "file": file_path,
                "line": 1,
                "kind": "deleted_test_file",
                "snippet": "<file deleted>",
            })

    for file_path, line_no, line in added:
        if not TEST_CODE_PATH.search(file_path):
            continue
        if _is_comment_or_doc(line):
            continue
        scanned_files.add(file_path)
        scanned_line_count += 1
        for label, pattern in PATTERNS:
            if pattern.search(line):
                allow, reason = _is_allowlisted(file_path, line)
                if allow:
                    exclusions.append({
                        "file": file_path,
                        "line": line_no,
                        "kind": label,
                        "reason": reason,
                    })
                    break
                hits.append({
                    "file": file_path,
                    "line": line_no,
                    "kind": label,
                    "snippet": line[:180],
                })
                break

    for file_path, line_no, line in tracked_deleted:
        if not TEST_CODE_PATH.search(file_path):
            continue
        if _is_comment_or_doc(line):
            continue
        for label, pattern in DELETED_PATTERNS:
            if pattern.search(line):
                allow, reason = _is_allowlisted(file_path, line)
                if allow:
                    exclusions.append({
                        "file": file_path,
                        "line": line_no,
                        "kind": label,
                        "reason": reason,
                    })
                    break
                hits.append({
                    "file": file_path,
                    "line": line_no,
                    "kind": label,
                    "snippet": line[:180],
                })
                break

    coverage_error = _coverage_error(
        has_changes=has_changes,
        scanned_file_count=len(scanned_files),
        scanned_line_count=scanned_line_count,
    )

    should_fail = len(hits) > 0 or coverage_error is not None

    report = {
        "base": BASE_REF,
        "tracked_added_line_count": len(tracked_added),
        "untracked_file_count": len(untracked_files),
        "untracked_line_count": len(untracked_added),
        "scanned_file_count": len(scanned_files),
        "scanned_line_count": scanned_line_count,
        "has_working_tree_changes": has_changes,
        "coverage_error": coverage_error,
        "allowlist_exact": ALLOWLIST_EXACT,
        "exclusions": exclusions,
        "weakening_count": len(hits),
        "hits": hits,
    }

    out = Path("reports") / "test_weakening_scan.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "tracked_added_line_count": len(tracked_added),
        "untracked_file_count": len(untracked_files),
        "untracked_line_count": len(untracked_added),
        "scanned_file_count": len(scanned_files),
        "scanned_line_count": scanned_line_count,
        "coverage_error": coverage_error,
        "weakening_count": len(hits),
        "report": str(out),
    }, ensure_ascii=False))
    return 0 if not should_fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
