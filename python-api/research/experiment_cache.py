from __future__ import annotations

import hashlib
import json
from typing import Any


def build_experiment_cache_key(spec: dict[str, Any], *, prefix: str = "exp") -> str:
    payload = json.dumps(spec or {}, ensure_ascii=False, sort_keys=True, default=str)
    h = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{prefix}:{h}"
