from pathlib import Path

import pytest

from migraai.core.secret_mask import (
    _SECRET_PATTERNS,
    MASKED_PLACEHOLDER_TOKEN,
    blank_masked_secret_placeholders,
    contains_secret,
    mask_secrets,
)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("PASSWORD = 'plain-secret'", "PASSWORD = '***MASKED***'"),
        ('PASSWORD "plain-secret"', 'PASSWORD "***MASKED***"'),
        ("IDENTIFIED BY plain-secret", "IDENTIFIED BY ***MASKED***"),
        (
            "IDENTIFIED BY VALUES 'hash-value'",
            "IDENTIFIED BY VALUES '***MASKED***'",
        ),
        ("password=plain-secret", "password=***MASKED***"),
        ('passwd: "plain-secret"', 'passwd: "***MASKED***"'),
        ("pwd='plain-secret'", "pwd='***MASKED***'"),
        ('secret = "plain-secret"', 'secret = "***MASKED***"'),
        ("token: plain-secret", "token: ***MASKED***"),
        ("api_key='plain-secret'", "api_key='***MASKED***'"),
        ("access_key: 'plain-secret'", "access_key: '***MASKED***'"),
        ("secret_key='plain-secret'", "secret_key='***MASKED***'"),
        ('client_secret: "plain-secret"', 'client_secret: "***MASKED***"'),
        ("run-tool -pplain-secret", "run-tool -p***MASKED***"),
        ("run-tool --password plain-secret", "run-tool --password ***MASKED***"),
        ("run-tool --password='plain-secret'", "run-tool --password='***MASKED***'"),
        ("aws_key=AKIAABCDEFGHIJKLMNOP", "aws_key=***MASKED***"),
        (
            "-----BEGIN PRIVATE KEY-----\nsecret-key\n-----END PRIVATE KEY-----",
            "***MASKED***",
        ),
        (
            "jdbc://etl-user:plain-secret@warehouse.example/db",
            "jdbc://***:***@warehouse.example/db",
        ),
        (
            "CONNECT TO warehouse USER etl USING plain-secret;",
            "CONNECT TO warehouse USER etl USING ***MASKED***;",
        ),
        (".LOGON warehouse/etl-user,plain-secret", ".LOGON ***MASKED***"),
    ],
)
def test_mask_secrets(source: str, expected: str) -> None:
    assert mask_secrets(source) == expected
    assert not any(pattern.search(mask_secrets(source)) for pattern in _SECRET_PATTERNS)
    assert contains_secret(source)
    assert not contains_secret(mask_secrets(source))


def test_neutral_mask_placeholder_does_not_match_secret_patterns() -> None:
    masked = blank_masked_secret_placeholders("PASSWORD = '***MASKED***'")

    assert masked == f"PASSWORD = '{MASKED_PLACEHOLDER_TOKEN}'"
    assert all(not pattern.search(MASKED_PLACEHOLDER_TOKEN) for pattern in _SECRET_PATTERNS)
    assert all(not pattern.search(masked) for pattern in _SECRET_PATTERNS)


def test_masked_testpacks_have_no_secret_pattern_matches() -> None:
    testpacks = Path(__file__).resolve().parents[3] / "testpacks"

    for path in testpacks.rglob("*"):
        if not path.is_file():
            continue
        masked = blank_masked_secret_placeholders(mask_secrets(path.read_text(encoding="utf-8")))
        assert not any(pattern.search(masked) for pattern in _SECRET_PATTERNS), path
