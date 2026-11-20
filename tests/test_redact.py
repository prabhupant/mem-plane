"""Unit tests for secret redaction (Section 17.2, M4.1)."""

from __future__ import annotations

from mem_plane.core.redact import redact_secrets


def test_redact_disabled() -> None:
    text = "AKIA1234567890ABCDEF"
    assert redact_secrets(text, enabled=False) == text


def test_redact_empty() -> None:
    assert redact_secrets("", enabled=True) == ""


def test_redact_aws_key() -> None:
    text = "export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
    redacted = redact_secrets(text)
    assert "AKIAIOSFODNN7EXAMPLE" not in redacted
    assert "[REDACTED:aws_key]" in redacted


def test_redact_github_token() -> None:
    for prefix in ["ghp_", "gho_", "ghu_", "ghs_", "ghr_"]:
        token = f"{prefix}1234567890abcdefghijklmnopqrstuvwxyz12"
        text = f"git remote set-url origin https://{token}@github.com/repo"
        redacted = redact_secrets(text)
        assert token not in redacted
        assert "[REDACTED:github_token]" in redacted


def test_redact_anthropic_key() -> None:
    key = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890_ABCD"
    text = f"ANTHROPIC_API_KEY={key}"
    redacted = redact_secrets(text)
    assert key not in redacted
    assert "[REDACTED:anthropic_key]" in redacted


def test_redact_openai_key() -> None:
    key = "sk-proj-1234567890abcdefghijklmnopqrstuvwxyz123456"
    text = f"OPENAI_API_KEY={key}"
    redacted = redact_secrets(text)
    assert key not in redacted
    assert "[REDACTED:openai_key]" in redacted


def test_redact_slack_token() -> None:
    token = "xoxb-123456789012-1234567890123-abcdefghijklmnopqrstuv"
    text = f"SLACK_BOT_TOKEN={token}"
    redacted = redact_secrets(text)
    assert token not in redacted
    assert "[REDACTED:slack_token]" in redacted


def test_redact_jwt() -> None:
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    text = f"Bearer {jwt}"
    redacted = redact_secrets(text)
    assert jwt not in redacted
    assert "[REDACTED:jwt]" in redacted


def test_redact_pem_private_key() -> None:
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEA0Y1+abcdef...\n"
        "-----END RSA PRIVATE KEY-----"
    )
    text = f"SSL key:\n{pem}\nNext line"
    redacted = redact_secrets(text)
    assert "MIIEowIBAAKCAQEA0Y1+abcdef..." not in redacted
    assert "[REDACTED:private_key]" in redacted
    assert "Next line" in redacted


def test_redact_assignments() -> None:
    samples = [
        ("password = superSecretPassword123!", "password = [REDACTED:assignment]"),
        ('passwd: "anotherLongPassword999"', 'passwd: "[REDACTED:assignment]"'),
        ("secret='verySecretTokenValue'", "secret='[REDACTED:assignment]'"),
        ("api_key = topSecretKey123456", "api_key = [REDACTED:assignment]"),
        ("token: myAccessToken123456", "token: [REDACTED:assignment]"),
    ]
    for raw, expected in samples:
        redacted = redact_secrets(raw)
        assert "[REDACTED:assignment]" in redacted, f"Failed for {raw}: got {redacted}"


def test_redact_does_not_double_redact() -> None:
    text = "api_key = [REDACTED:anthropic_key]"
    redacted = redact_secrets(text)
    assert "[REDACTED:assignment]" not in redacted
    assert text in redacted
