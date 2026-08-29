"""Logging guards that prevent credentials from reaching local/provider logs."""

from __future__ import annotations

import logging
import re


_SECRET_PATTERNS = (
    re.compile(r"\bsb_secret_[A-Za-z0-9_-]+\b"),
    re.compile(r"(?i)(authorization[^\r\n]{0,40}?bearer\s+)[A-Za-z0-9._~-]+"),
    re.compile(r"(?i)(apikey[^\r\n]{0,20}?[=:,]\s*[b]?['\"]?)[A-Za-z0-9._~-]+"),
)


def redact_secrets(value: object) -> str:
    text = str(value)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(r"\1[REDACTED]" if pattern.groups else "[REDACTED]", text)
    return text


class SecretRedactionFilter(logging.Filter):
    """Redact known credential forms before any handler formats a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        rendered = record.getMessage()
        redacted = redact_secrets(rendered)
        if redacted != rendered:
            record.msg = redacted
            record.args = ()
        return True


def suppress_sensitive_dependency_logs() -> None:
    """Keep HTTP/2 header encoders from logging auth headers at DEBUG."""
    for logger_name in ("httpx", "httpcore", "hpack", "h2", "urllib3"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)
