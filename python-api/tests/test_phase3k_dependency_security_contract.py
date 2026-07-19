from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _numeric_version(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("-")[0].split("."))


def _locked_versions(package_name: str) -> list[str]:
    lock = json.loads((REPO_ROOT / "package-lock.json").read_text(encoding="utf-8"))
    suffix = f"node_modules/{package_name}"
    return sorted(
        {
            package["version"]
            for path, package in lock["packages"].items()
            if (path == suffix or path.endswith(f"/{suffix}")) and "version" in package
        }
    )


def test_node_runtime_and_direct_security_floors_are_explicit() -> None:
    package_json = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))

    assert package_json["engines"]["node"] == "24.x"
    assert package_json["dependencies"]["next"] == "^16.2.10"
    assert package_json["devDependencies"]["concurrently"] == "^9.2.4"
    assert package_json["devDependencies"]["picomatch"] == "^4.0.5"
    assert package_json["devDependencies"]["postcss"] == "^8.5.10"
    assert "lucide-react" not in package_json["dependencies"]
    assert "overrides" not in package_json


def test_lockfile_contains_only_fixed_critical_and_high_versions() -> None:
    minimums = {
        "@grpc/grpc-js": (1, 14, 4),
        "@tootallnate/once": (2, 0, 1),
        "lodash": (4, 18, 0),
        "next": (16, 2, 10),
        "protobufjs": (7, 6, 3),
        "shell-quote": (1, 9, 0),
        "undici": (7, 28, 0),
        "vite": (8, 0, 16),
        "ws": (8, 21, 0),
    }
    for package_name, minimum in minimums.items():
        versions = _locked_versions(package_name)
        assert versions, package_name
        assert all(_numeric_version(version) >= minimum for version in versions), (
            package_name,
            versions,
        )

    for version in _locked_versions("form-data"):
        parsed = _numeric_version(version)
        assert parsed >= ((2, 5, 6) if parsed[0] == 2 else (4, 0, 6))

    for version in _locked_versions("picomatch"):
        parsed = _numeric_version(version)
        assert parsed >= ((2, 3, 2) if parsed[0] == 2 else (4, 0, 4))


def test_ci_enforces_node24_and_fail_closed_dependency_audits() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert workflow.count("node-version: '24'") == 3
    assert "Dependency security (release-blocking)" in workflow
    assert "npm ci --ignore-scripts --omit=optional --no-fund" in workflow
    assert "npm ls --all --omit=optional" in workflow
    assert "npm audit --audit-level=high --json" in workflow
    assert "npm audit --omit=dev --audit-level=high --json" in workflow
    assert "name: dependency-security-reports" in workflow
    assert "if-no-files-found: error" in workflow
    assert "npm audit fix" not in workflow
    assert "npm audit fix --force" not in workflow


def test_next_container_uses_the_same_node_major() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile.nextjs").read_text(encoding="utf-8")
    docker_guide = (REPO_ROOT / "docs" / "setup" / "DOCKER_GUIDE.md").read_text(
        encoding="utf-8"
    )

    assert dockerfile.count("FROM node:24-alpine") == 3
    assert "FROM node:20" not in dockerfile
    assert docker_guide.count("FROM node:24-alpine") == 3
    assert "FROM node:20" not in docker_guide


def test_playwright_uses_webpack_dev_server_for_next16_hydration_stability() -> None:
    config = (REPO_ROOT / "playwright.config.ts").read_text(encoding="utf-8")

    assert "next dev --webpack -H 127.0.0.1" in config
    assert "reuseExistingServer: false" in config
