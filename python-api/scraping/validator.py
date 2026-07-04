from __future__ import annotations

import re

from scraping.models import ValidationResult
from scraping.quality_codes import E005_RACE_ID_FORMAT


def validate_race_ids(race_ids: list[str]) -> ValidationResult:
    if race_ids is None:
        return ValidationResult(ok=False, reason="race_ids is None", error_code=E005_RACE_ID_FORMAT)
    bad = [r for r in race_ids if not re.fullmatch(r"\d{12}", str(r))]
    if bad:
        return ValidationResult(
            ok=False,
            reason=f"invalid race_id detected: {bad[:3]}",
            error_code=E005_RACE_ID_FORMAT,
            details={"examples": [str(x) for x in bad[:3]], "count": len(bad)},
        )
    return ValidationResult(ok=True)
