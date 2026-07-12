import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SECRET_SCANNER = REPO_ROOT / "scripts" / "security" / "scan_new_secrets.py"
WEAKENING_SCANNER = REPO_ROOT / "scripts" / "security" / "scan_test_weakening.py"


def _run(cmd: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\nstdout={proc.stdout}\nstderr={proc.stderr}")
    return proc


def _init_temp_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)

    _run(["git", "init"], repo)
    _run(["git", "config", "user.email", "scanner-test@example.com"], repo)
    _run(["git", "config", "user.name", "scanner-test"], repo)

    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _run(["git", "add", "README.md"], repo)
    _run(["git", "commit", "-m", "base"], repo)
    _run(["git", "update-ref", "refs/remotes/origin/develop", "HEAD"], repo)

    return repo


def _run_scanner(scanner_path: Path, repo: Path) -> tuple[int, dict]:
    proc = _run([sys.executable, str(scanner_path)], repo, check=False)
    assert proc.stdout.strip(), f"scanner produced no output: {scanner_path}"
    summary = json.loads(proc.stdout.strip().splitlines()[-1])
    report_path = repo / summary["report"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return proc.returncode, report


def _reset_origin_develop_to_head(repo: Path) -> None:
    _run(["git", "update-ref", "refs/remotes/origin/develop", "HEAD"], repo)


def _fake_secret() -> str:
    return "".join(["sk", "_live_", "A" * 20])


def _allowlist_dummy_service_role() -> str:
    return "".join(["e2e", "-dummy", "-service", "-role", "-key"])


def _skip_call_source() -> str:
    return "".join(["test", ".", "skip", "('demo', async () => {})"])


@pytest.mark.parametrize("scanner", [SECRET_SCANNER, WEAKENING_SCANNER])
def test_fail_closed_when_changes_exist_but_scanner_input_zero(tmp_path: Path, scanner: Path) -> None:
    repo = _init_temp_repo(tmp_path)

    tracked = repo / "src" / "plain.txt"
    tracked.parent.mkdir(parents=True, exist_ok=True)
    tracked.write_text("line1\nline2\n", encoding="utf-8")
    _run(["git", "add", "src/plain.txt"], repo)
    _run(["git", "commit", "-m", "add tracked file"], repo)
    _reset_origin_develop_to_head(repo)

    # Delete-only change leaves zero added lines and no untracked files.
    tracked.write_text("line1\n", encoding="utf-8")

    code, report = _run_scanner(scanner, repo)
    if scanner == SECRET_SCANNER:
        assert code == 1
        assert report["coverage_error"] is not None
    else:
        assert code == 0
        assert report["coverage_error"] is None
    assert report["has_working_tree_changes"] is True
    assert report["scanned_line_count"] == 0


def test_weakening_scanner_docs_only_changes_pass(tmp_path: Path) -> None:
    repo = _init_temp_repo(tmp_path)

    doc = repo / "docs" / "note.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("doc update\n", encoding="utf-8")

    code, report = _run_scanner(WEAKENING_SCANNER, repo)
    assert code == 0
    assert report["has_working_tree_changes"] is True
    assert report["has_test_scope_changes"] is False
    assert report["test_scope_file_count"] == 0
    assert report["scanned_line_count"] == 0
    assert report["coverage_error"] is None
    assert report["weakening_count"] == 0


def test_weakening_scanner_production_code_only_changes_pass(tmp_path: Path) -> None:
    repo = _init_temp_repo(tmp_path)

    app = repo / "src" / "main.ts"
    app.parent.mkdir(parents=True, exist_ok=True)
    app.write_text("export const x = 1\n", encoding="utf-8")

    code, report = _run_scanner(WEAKENING_SCANNER, repo)
    assert code == 0
    assert report["has_working_tree_changes"] is True
    assert report["has_test_scope_changes"] is False
    assert report["test_scope_file_count"] == 0
    assert report["scanned_line_count"] == 0
    assert report["coverage_error"] is None
    assert report["weakening_count"] == 0


def test_weakening_scanner_fail_closed_when_test_scope_changes_but_input_zero(tmp_path: Path) -> None:
    repo = _init_temp_repo(tmp_path)

    test_file = repo / "python-api" / "tests" / "test_comment_only.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("# comment only\n", encoding="utf-8")

    code, report = _run_scanner(WEAKENING_SCANNER, repo)
    assert code == 1
    assert report["has_working_tree_changes"] is True
    assert report["has_test_scope_changes"] is True
    assert report["test_scope_file_count"] >= 1
    assert report["scanned_line_count"] == 0
    assert report["coverage_error"] is not None


def test_secret_scanner_detects_tracked_unstaged_secret(tmp_path: Path) -> None:
    repo = _init_temp_repo(tmp_path)

    tracked = repo / "src" / "config.txt"
    tracked.parent.mkdir(parents=True, exist_ok=True)
    tracked.write_text("safe\n", encoding="utf-8")
    _run(["git", "add", "src/config.txt"], repo)
    _run(["git", "commit", "-m", "add config"], repo)

    tracked.write_text("safe\napi_key=" + _fake_secret() + "\n", encoding="utf-8")

    code, report = _run_scanner(SECRET_SCANNER, repo)
    assert code == 1
    assert report["tracked_added_line_count"] > 0
    assert report["secret_candidate_count"] >= 1


def test_secret_scanner_detects_untracked_secret(tmp_path: Path) -> None:
    repo = _init_temp_repo(tmp_path)

    untracked = repo / "secrets.txt"
    untracked.write_text("token=" + _fake_secret() + "\n", encoding="utf-8")

    code, report = _run_scanner(SECRET_SCANNER, repo)
    assert code == 1
    assert report["untracked_file_count"] == 1
    assert report["untracked_line_count"] > 0
    assert report["secret_candidate_count"] >= 1


def test_weakening_scanner_detects_untracked_test_skip(tmp_path: Path) -> None:
    repo = _init_temp_repo(tmp_path)

    test_file = repo / "e2e" / "new.spec.ts"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(_skip_call_source() + "\n", encoding="utf-8")

    code, report = _run_scanner(WEAKENING_SCANNER, repo)
    assert code == 1
    assert report["untracked_file_count"] == 1
    assert report["weakening_count"] >= 1
    assert any(hit["kind"] == "test.skip" for hit in report["hits"])


def test_weakening_scanner_detects_deleted_test_function(tmp_path: Path) -> None:
    repo = _init_temp_repo(tmp_path)

    test_file = repo / "python-api" / "tests" / "test_demo.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(
        "def test_demo_case():\n"
        "    assert 1 == 1\n",
        encoding="utf-8",
    )
    _run(["git", "add", "python-api/tests/test_demo.py"], repo)
    _run(["git", "commit", "-m", "add test file"], repo)
    _reset_origin_develop_to_head(repo)

    test_file.write_text("# removed\n", encoding="utf-8")

    code, report = _run_scanner(WEAKENING_SCANNER, repo)
    assert code == 1
    assert report["weakening_count"] >= 1
    assert any(hit["kind"] == "deleted_py_test_function" for hit in report["hits"])


@pytest.mark.parametrize(
    ("line_to_delete", "expected_kind"),
    [
        ("    expect(value).toBe(1)\n", "deleted_expect"),
        ("    assert value == 1\n", "deleted_assert"),
        ("    with pytest.raises(ValueError):\n", "deleted_pytest_raises"),
    ],
)
def test_weakening_scanner_detects_deleted_assert_like_patterns(
    tmp_path: Path,
    line_to_delete: str,
    expected_kind: str,
) -> None:
    repo = _init_temp_repo(tmp_path)

    test_file = repo / "python-api" / "tests" / "test_patterns.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(
        "import pytest\n"
        "\n"
        "def test_patterns():\n"
        "    value = 1\n"
        "    expect(value).toBe(1)\n"
        "    assert value == 1\n"
        "    with pytest.raises(ValueError):\n"
        "        raise ValueError('x')\n",
        encoding="utf-8",
    )
    _run(["git", "add", "python-api/tests/test_patterns.py"], repo)
    _run(["git", "commit", "-m", "add pattern test"], repo)
    _reset_origin_develop_to_head(repo)

    updated = test_file.read_text(encoding="utf-8").replace(line_to_delete, "")
    test_file.write_text(updated, encoding="utf-8")

    code, report = _run_scanner(WEAKENING_SCANNER, repo)
    assert code == 1
    assert report["weakening_count"] >= 1
    assert any(hit["kind"] == expected_kind for hit in report["hits"])


def test_normal_diff_passes_both_scanners(tmp_path: Path) -> None:
    repo = _init_temp_repo(tmp_path)

    test_file = repo / "src" / "__tests__" / "ok.test.ts"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(
        "test('ok', () => {\n"
        "  const value = 1 + 1\n"
        "  expect(value).toBe(2)\n"
        "})\n",
        encoding="utf-8",
    )

    secret_code, secret_report = _run_scanner(SECRET_SCANNER, repo)
    weak_code, weak_report = _run_scanner(WEAKENING_SCANNER, repo)

    assert secret_code == 0
    assert weak_code == 0
    assert secret_report["scanned_line_count"] > 0
    assert weak_report["scanned_line_count"] > 0
    assert secret_report["secret_candidate_count"] == 0
    assert weak_report["weakening_count"] == 0


def test_secret_scanner_allowlist_only_line_passes(tmp_path: Path) -> None:
    repo = _init_temp_repo(tmp_path)

    test_file = repo / "src" / "dummy.ts"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(
        "const service_role_key = '" + _allowlist_dummy_service_role() + "'\n",
        encoding="utf-8",
    )

    code, report = _run_scanner(SECRET_SCANNER, repo)
    assert code == 0
    assert report["secret_candidate_count"] == 0
    assert report["excluded_count"] >= 1


def test_secret_scanner_allowlist_bypass_same_line_fails(tmp_path: Path) -> None:
    repo = _init_temp_repo(tmp_path)

    test_file = repo / "src" / "mixed.ts"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    secret_value = _fake_secret()
    test_file.write_text(
        "const service_role_key='" + _allowlist_dummy_service_role() + "'; token='" + secret_value + "'\n",
        encoding="utf-8",
    )

    code, report = _run_scanner(SECRET_SCANNER, repo)
    assert code == 1
    assert report["secret_candidate_count"] >= 1
    assert report["excluded_count"] >= 1


def test_secret_scanner_detects_two_candidates_in_one_line(tmp_path: Path) -> None:
    repo = _init_temp_repo(tmp_path)

    test_file = repo / "src" / "multi.ts"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    secret_1 = _fake_secret()
    secret_2 = "".join(["sk", "_live_", "B" * 20])
    test_file.write_text(
        "const x = token='" + secret_1 + "'; password='" + secret_2 + "'\n",
        encoding="utf-8",
    )

    code, report = _run_scanner(SECRET_SCANNER, repo)
    assert code == 1
    assert report["secret_candidate_count"] >= 2


def test_secret_scanner_report_does_not_leak_secret_values(tmp_path: Path) -> None:
    repo = _init_temp_repo(tmp_path)

    test_file = repo / "src" / "leak-check.ts"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    secret_value = _fake_secret()
    test_file.write_text("token='" + secret_value + "'\n", encoding="utf-8")

    code, report = _run_scanner(SECRET_SCANNER, repo)
    assert code == 1
    report_json = json.dumps(report, ensure_ascii=False)
    assert secret_value not in report_json
    for candidate in report["candidates"]:
        assert "value_length" in candidate
        assert "fingerprint" in candidate
        assert "snippet" not in candidate


def test_secret_scanner_detects_secret_on_process_env_line(tmp_path: Path) -> None:
    repo = _init_temp_repo(tmp_path)

    test_file = repo / "src" / "env-mixed.ts"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    secret_value = _fake_secret()
    test_file.write_text(
        "const y = process.env.AUTH; token='" + secret_value + "'\n",
        encoding="utf-8",
    )

    code, report = _run_scanner(SECRET_SCANNER, repo)
    assert code == 1
    assert report["secret_candidate_count"] >= 1
