import logging

from logging_security import SecretRedactionFilter, redact_secrets, suppress_sensitive_dependency_logs


def test_secret_values_are_redacted() -> None:
    fake_secret = "sb_" + "secret_" + "example_123"
    assert "sb_secret_" not in redact_secrets(f"apikey: {fake_secret}")
    assert "token-value" not in redact_secrets("Authorization: Bearer token-value")
    assert "[REDACTED]" in redact_secrets("Authorization: Bearer token-value")


def test_filter_redacts_formatted_log_record() -> None:
    fake_secret = "sb_" + "secret_" + "example_123"
    record = logging.LogRecord(
        "test", logging.DEBUG, __file__, 1, "key=%s", (fake_secret,), None
    )
    assert SecretRedactionFilter().filter(record)
    assert "sb_secret_" not in record.getMessage()


def test_sensitive_dependency_loggers_never_run_at_debug() -> None:
    suppress_sensitive_dependency_logs()
    for name in ("httpx", "httpcore", "hpack", "h2", "urllib3"):
        assert logging.getLogger(name).level >= logging.WARNING
