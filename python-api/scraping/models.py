from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class DownloadResult:
    url: str
    status_code: int
    html: str | None
    cache_hit: bool = False
    error: str | None = None


@dataclass
class ValidationResult:
    ok: bool
    reason: str | None = None
    error_code: str | None = None
    details: dict[str, Any] | None = None
