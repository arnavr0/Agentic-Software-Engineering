"""Structured logging redaction coverage."""

from agentic_tester.logging import redact_sensitive


def test_logging_redacts_credentials_bodies_and_nested_sensitive_values() -> None:
    event = redact_sensitive(
        None,
        "info",
        {
            "api_key": "secret-key",
            "request_body": {"password": "secret-password"},
            "page_text": "sensitive page content",
            "safe_count": 3,
        },
    )

    assert event["api_key"] == "[REDACTED]"
    assert event["request_body"] == "[REDACTED]"
    assert event["page_text"] == "[REDACTED]"
    assert event["safe_count"] == 3
