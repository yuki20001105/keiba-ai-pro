from __future__ import annotations

from datetime import datetime
from datetime import timedelta


def _parse_date_local(s: str) -> datetime:
    for fmt in ("%Y%m%d", "%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise ValueError(f"invalid date format: {s}")


def build_date_range(start_date: str, end_date: str) -> list[str]:
    """Build inclusive date list in YYYYMMDD format."""
    s_dt = _parse_date_local(start_date)
    e_dt = _parse_date_local(end_date)
    out: list[str] = []
    cur = s_dt
    while cur <= e_dt:
        out.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return out
