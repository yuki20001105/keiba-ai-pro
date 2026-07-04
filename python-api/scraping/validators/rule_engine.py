from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from scraping.models import ValidationResult
from scraping.validators.rule_plugins import iter_rule_plugins


def _load_rules(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def validate_race_ids_by_rules(race_ids: list[str], rules_path: Path | None = None) -> ValidationResult:
    path = rules_path or (Path(__file__).parent / "rules.yaml")
    rules = _load_rules(path).get("race_ids", {})
    if not isinstance(rules, dict):
        return ValidationResult(ok=True)

    for key, plugin in iter_rule_plugins():
        if key not in rules:
            continue
        result = plugin.validate(race_ids, rules.get(key))
        if not result.ok:
            return result

    return ValidationResult(ok=True)
