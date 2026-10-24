from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import uuid

REPO_ROOT = Path(__file__).resolve().parents[2]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def json_sha256(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    os.replace(temporary, path)


def read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def source_manifest() -> dict[str, str]:
    roots = [REPO_ROOT / 'python-api/experiments', REPO_ROOT / 'python-api/training/eligibility.py',
             REPO_ROOT / 'keiba/keiba_ai/speed_deviation.py', REPO_ROOT / 'keiba/keiba_ai/constants.py',
             REPO_ROOT / 'scripts/run_model_experiment.py', REPO_ROOT / 'scripts/run_model_search.py']
    paths = []
    for root in roots:
        paths.extend(root.rglob('*.py') if root.is_dir() else [root])
    return {str(path.relative_to(REPO_ROOT)).replace('\\','/'):file_sha256(path) for path in sorted(paths)}


def active_pointer_hash() -> str | None:
    path = REPO_ROOT / 'python-api/models/.active_model.json'
    return file_sha256(path) if path.exists() else None
