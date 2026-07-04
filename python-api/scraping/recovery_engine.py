from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from scraping.quality_codes import (
    E001_RACE_IDS_EMPTY,
    E002_RACE_ID_DUPLICATE,
    E003_RACE_ID_TYPE,
    E004_RACE_ID_LENGTH,
    E005_RACE_ID_FORMAT,
    E201_TASK_EMPTY_OR_SAVE_FAILED,
    E202_TASK_EXEC_EXCEPTION,
    E099_UNKNOWN,
)


SEVERITY_INFO = "INFO"
SEVERITY_WARNING = "WARNING"
SEVERITY_ERROR = "ERROR"
SEVERITY_FATAL = "FATAL"

POLICY_CONTINUE = "CONTINUE"
POLICY_RETRY = "RETRY"
POLICY_SKIP = "SKIP"
POLICY_ABORT = "ABORT"


@dataclass(frozen=True)
class RecoveryDecision:
    error_code: str
    severity: str
    action: str
    policy: str
    retry_limit: int | None = None
    plugins: tuple[str, ...] = ()



def _policy_path() -> Path:
    return Path(__file__).with_name("recovery_policy.yaml")


def _safe_load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        obj = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _default_decisions() -> dict[str, RecoveryDecision]:
    return {
        E001_RACE_IDS_EMPTY: RecoveryDecision(E001_RACE_IDS_EMPTY, SEVERITY_ERROR, POLICY_RETRY, POLICY_RETRY, 2, ("INVALIDATE_CACHE",)),
        E002_RACE_ID_DUPLICATE: RecoveryDecision(E002_RACE_ID_DUPLICATE, SEVERITY_WARNING, POLICY_CONTINUE, POLICY_CONTINUE, 0, ()),
        E003_RACE_ID_TYPE: RecoveryDecision(E003_RACE_ID_TYPE, SEVERITY_ERROR, POLICY_RETRY, POLICY_RETRY, 2, ("INVALIDATE_CACHE",)),
        E004_RACE_ID_LENGTH: RecoveryDecision(E004_RACE_ID_LENGTH, SEVERITY_ERROR, POLICY_RETRY, POLICY_RETRY, 2, ("INVALIDATE_CACHE",)),
        E005_RACE_ID_FORMAT: RecoveryDecision(E005_RACE_ID_FORMAT, SEVERITY_ERROR, POLICY_RETRY, POLICY_RETRY, 2, ("INVALIDATE_CACHE",)),
        E201_TASK_EMPTY_OR_SAVE_FAILED: RecoveryDecision(E201_TASK_EMPTY_OR_SAVE_FAILED, SEVERITY_WARNING, POLICY_RETRY, POLICY_RETRY, 2, ()),
        E202_TASK_EXEC_EXCEPTION: RecoveryDecision(E202_TASK_EXEC_EXCEPTION, SEVERITY_ERROR, POLICY_RETRY, POLICY_RETRY, 2, ("RECONNECT",)),
        E099_UNKNOWN: RecoveryDecision(E099_UNKNOWN, SEVERITY_FATAL, POLICY_ABORT, POLICY_ABORT, 0, ()),
    }


def _parse_decision(error_code: str, node: Any, defaults: RecoveryDecision) -> RecoveryDecision:
    if not isinstance(node, dict):
        return defaults
    severity = str(node.get("severity") or defaults.severity).upper()
    action = str(node.get("action") or node.get("policy") or defaults.action).upper()
    retry_raw = node.get("retry", defaults.retry_limit)
    retry_limit = int(retry_raw) if isinstance(retry_raw, int) else defaults.retry_limit
    plugins_raw = node.get("plugins")
    plugins: tuple[str, ...]
    if isinstance(plugins_raw, list):
        plugins = tuple(str(x).upper() for x in plugins_raw)
    else:
        plugins = defaults.plugins
    return RecoveryDecision(
        error_code=error_code,
        severity=severity,
        action=action,
        policy=action,
        retry_limit=retry_limit,
        plugins=plugins,
    )


def _load_decisions() -> dict[str, RecoveryDecision]:
    base = _default_decisions()
    raw = _safe_load_yaml(_policy_path())
    defaults_node = raw.get("defaults", {}) if isinstance(raw.get("defaults"), dict) else {}
    default_decision = _parse_decision(E099_UNKNOWN, defaults_node, base[E099_UNKNOWN])
    codes = raw.get("codes", {}) if isinstance(raw.get("codes"), dict) else {}

    out: dict[str, RecoveryDecision] = dict(base)
    for code, payload in codes.items():
        key = str(code)
        seed = out.get(key, default_decision)
        out[key] = _parse_decision(key, payload, seed)

    if E099_UNKNOWN not in out:
        out[E099_UNKNOWN] = default_decision
    return out


_DECISIONS = _load_decisions()


def resolve_recovery(error_code: str | None) -> RecoveryDecision:
    if not error_code:
        return _DECISIONS[E099_UNKNOWN]
    return _DECISIONS.get(error_code, _DECISIONS[E099_UNKNOWN])
