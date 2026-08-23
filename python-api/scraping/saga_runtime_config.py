"""Fail-closed configuration for the Phase 3J disposable saga runtime.

Phase 3J is deliberately *not* a production feature switch.  The only
executable mode is an explicitly selected, disposable CI/local SQLite file.
No configuration accepted by this module can enable a remote effect, worker
dispatch, or execution unlock.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping


class SagaRuntimeConfigError(ValueError):
    """Raised when a caller attempts to widen the Phase 3J safety boundary."""


class SagaRuntimeMode(str, Enum):
    DISABLED = "disabled"
    CI_DISPOSABLE = "ci-disposable"


_PRODUCTION_ENVIRONMENTS = frozenset({"prod", "production", "prd", "live"})
_EXECUTABLE_ENVIRONMENTS = frozenset({"ci", "test", "local"})
_FALSE_VALUES = frozenset({"", "0", "false", "no", "off"})


def _is_false(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower() in _FALSE_VALUES


def _canonical_environment(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SagaRuntimeConfigError("environment-invalid")
    normalized = value.strip().lower()
    if len(normalized) > 32 or not normalized.replace("-", "").isalnum():
        raise SagaRuntimeConfigError("environment-invalid")
    return normalized


def _disposable_path(value: object) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise SagaRuntimeConfigError("sqlite-path-required")
    candidate = Path(value).expanduser().resolve(strict=False)
    temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
    try:
        candidate.relative_to(temporary_root)
    except ValueError as exc:
        raise SagaRuntimeConfigError("sqlite-path-not-disposable") from exc
    if candidate == temporary_root or candidate.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
        raise SagaRuntimeConfigError("sqlite-path-invalid")
    return candidate


@dataclass(frozen=True)
class SagaRuntimeConfig:
    mode: SagaRuntimeMode = SagaRuntimeMode.DISABLED
    environment: str = "local"
    sqlite_path: Path | None = None
    busy_timeout_ms: int = 5_000
    remote_effects_enabled: bool = False
    worker_dispatch_enabled: bool = False
    execution_unlock_enabled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.mode, SagaRuntimeMode):
            raise SagaRuntimeConfigError("mode-invalid")
        normalized_environment = _canonical_environment(self.environment)
        object.__setattr__(self, "environment", normalized_environment)

        if type(self.busy_timeout_ms) is not int or not 1 <= self.busy_timeout_ms <= 60_000:
            raise SagaRuntimeConfigError("busy-timeout-invalid")
        for value, code in (
            (self.remote_effects_enabled, "remote-effects-forbidden"),
            (self.worker_dispatch_enabled, "worker-dispatch-forbidden"),
            (self.execution_unlock_enabled, "execution-unlock-forbidden"),
        ):
            if value is not False:
                raise SagaRuntimeConfigError(code)

        if normalized_environment in _PRODUCTION_ENVIRONMENTS and self.mode is not SagaRuntimeMode.DISABLED:
            raise SagaRuntimeConfigError("production-runtime-forbidden")

        if (
            self.mode is SagaRuntimeMode.CI_DISPOSABLE
            and normalized_environment not in _EXECUTABLE_ENVIRONMENTS
        ):
            raise SagaRuntimeConfigError("executable-environment-forbidden")

        if self.mode is SagaRuntimeMode.DISABLED:
            if self.sqlite_path is not None:
                raise SagaRuntimeConfigError("disabled-runtime-has-sqlite-path")
        else:
            object.__setattr__(self, "sqlite_path", _disposable_path(self.sqlite_path))

    @property
    def executable(self) -> bool:
        return self.mode is SagaRuntimeMode.CI_DISPOSABLE

    @classmethod
    def ci_disposable(
        cls,
        sqlite_path: str | os.PathLike[str],
        *,
        environment: str = "ci",
        busy_timeout_ms: int = 5_000,
    ) -> "SagaRuntimeConfig":
        return cls(
            mode=SagaRuntimeMode.CI_DISPOSABLE,
            environment=environment,
            sqlite_path=Path(sqlite_path),
            busy_timeout_ms=busy_timeout_ms,
        )


def load_saga_runtime_config(environ: Mapping[str, str] | None = None) -> SagaRuntimeConfig:
    """Load strict environment configuration; unsafe values never coerce true."""

    values = os.environ if environ is None else environ
    raw_mode = values.get("PHASE3J_SAGA_RUNTIME_MODE", SagaRuntimeMode.DISABLED.value)
    try:
        mode = SagaRuntimeMode(raw_mode)
    except ValueError as exc:
        raise SagaRuntimeConfigError("mode-invalid") from exc

    environment = values.get("APP_ENV", "local")
    forbidden_flags = (
        ("PHASE3J_REMOTE_EFFECTS_ENABLED", "remote-effects-forbidden"),
        ("PHASE3J_WORKER_DISPATCH_ENABLED", "worker-dispatch-forbidden"),
        ("PHASE3J_EXECUTION_UNLOCK_ENABLED", "execution-unlock-forbidden"),
    )
    for name, code in forbidden_flags:
        raw = values.get(name, "false")
        if not _is_false(raw):
            raise SagaRuntimeConfigError(code)

    if mode is SagaRuntimeMode.DISABLED:
        return SagaRuntimeConfig(mode=mode, environment=environment)

    path = values.get("PHASE3J_SAGA_SQLITE_PATH")
    raw_timeout = values.get("PHASE3J_SAGA_BUSY_TIMEOUT_MS", "5000")
    try:
        timeout = int(raw_timeout)
    except (TypeError, ValueError) as exc:
        raise SagaRuntimeConfigError("busy-timeout-invalid") from exc
    return SagaRuntimeConfig.ci_disposable(
        path or "",
        environment=environment,
        busy_timeout_ms=timeout,
    )
