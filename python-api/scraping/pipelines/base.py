from __future__ import annotations

from typing import Any


class BaseDomainPipeline:
    name = "base"

    async def process_day(self, **kwargs) -> dict[str, Any]:
        return {"processed": 0}
