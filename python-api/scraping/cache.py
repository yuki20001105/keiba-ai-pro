from __future__ import annotations

from pathlib import Path


def cache_root() -> Path:
    root = Path(__file__).parent.parent.parent
    return root / "cache" / "html"


def html_cache_path(category: str, key: str) -> Path:
    safe_key = "".join(ch for ch in key if ch.isalnum() or ch in ("-", "_"))
    return cache_root() / category / f"{safe_key}.html"


def read_html(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def write_html(path: Path, html: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
