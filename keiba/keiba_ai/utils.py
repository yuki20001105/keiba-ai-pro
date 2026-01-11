from __future__ import annotations
import random
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

def now_jst() -> datetime:
    return datetime.now(tz=JST)

def yyyymmdd(d: datetime) -> str:
    return d.astimezone(JST).strftime("%Y%m%d")

def sleep_jitter(min_s: float, max_s: float) -> None:
    if max_s < min_s:
        max_s = min_s
    if max_s > 0:
        time.sleep(random.uniform(min_s, max_s))

def safe_filename(s: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in s)

def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
