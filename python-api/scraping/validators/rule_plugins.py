from __future__ import annotations

import re
from typing import Any

from scraping.models import ValidationResult
from scraping.quality_codes import (
    E001_RACE_IDS_EMPTY,
    E002_RACE_ID_DUPLICATE,
    E003_RACE_ID_TYPE,
    E004_RACE_ID_LENGTH,
    E005_RACE_ID_FORMAT,
)


class RulePlugin:
    key = ""

    def validate(self, values: list[str], rule_value: Any) -> ValidationResult:
        return ValidationResult(ok=True)


class RequiredRule(RulePlugin):
    key = "required"

    def validate(self, values: list[str], rule_value: Any) -> ValidationResult:
        if bool(rule_value) and not values:
            return ValidationResult(ok=False, reason="required rule violation: race_ids is empty", error_code=E001_RACE_IDS_EMPTY)
        return ValidationResult(ok=True)


class UniqueRule(RulePlugin):
    key = "unique"

    def validate(self, values: list[str], rule_value: Any) -> ValidationResult:
        if bool(rule_value) and len(set(values)) != len(values):
            return ValidationResult(ok=False, reason="unique rule violation: duplicate race_ids", error_code=E002_RACE_ID_DUPLICATE)
        return ValidationResult(ok=True)


class TypeRule(RulePlugin):
    key = "item_type"

    def validate(self, values: list[str], rule_value: Any) -> ValidationResult:
        if rule_value == "str":
            bad = [x for x in values if not isinstance(x, str)]
            if bad:
                return ValidationResult(
                    ok=False,
                    reason=f"type rule violation: non-str values {bad[:3]}",
                    error_code=E003_RACE_ID_TYPE,
                    details={"examples": [str(x) for x in bad[:3]], "count": len(bad)},
                )
        return ValidationResult(ok=True)


class LengthRule(RulePlugin):
    key = "item_length"

    def validate(self, values: list[str], rule_value: Any) -> ValidationResult:
        if isinstance(rule_value, int):
            bad_len = [x for x in values if len(str(x)) != rule_value]
            if bad_len:
                return ValidationResult(
                    ok=False,
                    reason=f"length rule violation: {bad_len[:3]}",
                    error_code=E004_RACE_ID_LENGTH,
                    details={"examples": [str(x) for x in bad_len[:3]], "count": len(bad_len)},
                )
        return ValidationResult(ok=True)


class RegexRule(RulePlugin):
    key = "item_regex"

    def validate(self, values: list[str], rule_value: Any) -> ValidationResult:
        if not isinstance(rule_value, str) or not rule_value:
            return ValidationResult(ok=True)
        pat = re.compile(rule_value)
        bad = [x for x in values if not pat.match(str(x))]
        if bad:
            return ValidationResult(
                ok=False,
                reason=f"regex rule violation: {bad[:3]}",
                error_code=E005_RACE_ID_FORMAT,
                details={"examples": [str(x) for x in bad[:3]], "count": len(bad), "pattern": rule_value},
            )
        return ValidationResult(ok=True)


_PLUGINS: dict[str, RulePlugin] = {
    RequiredRule.key: RequiredRule(),
    UniqueRule.key: UniqueRule(),
    TypeRule.key: TypeRule(),
    LengthRule.key: LengthRule(),
    RegexRule.key: RegexRule(),
}


def iter_rule_plugins() -> list[tuple[str, RulePlugin]]:
    return list(_PLUGINS.items())
