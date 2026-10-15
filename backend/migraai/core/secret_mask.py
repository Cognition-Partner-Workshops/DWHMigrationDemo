from __future__ import annotations

import re

_MASKED = "***MASKED***"
_QUOTED_VALUE = r"""(?P<quote>['"])(?!\*\*\*MASKED\*\*\*)(?:\\.|(?!(?P=quote)).)*(?P=quote)"""
_BARE_VALUE = r"""(?!(?:\*\*\*MASKED\*\*\*|['"]))[^\s,;)}\]]+"""
_VALUE = rf"(?P<value>{_QUOTED_VALUE}|{_BARE_VALUE})"
_VALUE_PATTERNS = (
    re.compile(rf"(?i)(?P<prefix>\bPASSWORD\b\s+)(?!=)\s*{_VALUE}"),
    re.compile(rf"(?i)(?P<prefix>\bIDENTIFIED\s+BY\s+VALUES\s+){_VALUE}"),
    re.compile(rf"(?i)(?P<prefix>\bIDENTIFIED\s+BY\s+(?!VALUES\b)){_VALUE}"),
    re.compile(
        rf"(?i)(?P<prefix>\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|secret[_-]?key|client[_-]?secret)\b\s*[=:]\s*){_VALUE}"
    ),
    re.compile(rf"(?i)(?P<prefix>(?<!\S)--password(?:\s+|=)){_VALUE}"),
    re.compile(rf"(?i)(?P<prefix>(?<![\w-])-[pP]){_VALUE}"),
    re.compile(rf"(?i)(?P<prefix>(?<!\S)-[pP](?:w)?\s+){_VALUE}"),
    re.compile(rf"(?i)(?P<prefix>\bCONNECT\s+TO\b[^\r\n;]*?\bUSING\s+){_VALUE}"),
)
_LOGON_PATTERN = re.compile(r"(?im)(?P<prefix>^\s*\.LOGON\s+)(?!\*\*\*MASKED\*\*\*)[^\r\n]+")
_AWS_PATTERN = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_PEM_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)",
    re.DOTALL,
)
_URL_PATTERN = re.compile(
    r"(?i)(?P<scheme>[a-z][a-z0-9+.-]*://)(?!\*\*\*:\*\*\*@)"
    r"(?P<username>[^/\s:@?#]+):(?P<password>[^/\s@?#]+)@"
)

_SECRET_PATTERNS = (
    *_VALUE_PATTERNS,
    _LOGON_PATTERN,
    _AWS_PATTERN,
    _PEM_PATTERN,
    _URL_PATTERN,
)


class SecretAttachmentError(ValueError):
    pass


def _mask_value(match: re.Match[str]) -> str:
    quote = match.group("quote")
    value = f"{quote}{_MASKED}{quote}" if quote else _MASKED
    return f"{match.group('prefix')}{value}"


def mask_secrets(text: str) -> str:
    for pattern in _VALUE_PATTERNS:
        text = pattern.sub(_mask_value, text)
    text = _LOGON_PATTERN.sub(lambda match: f"{match.group('prefix')}{_MASKED}", text)
    text = _AWS_PATTERN.sub(_MASKED, text)
    text = _PEM_PATTERN.sub(_MASKED, text)
    text = _URL_PATTERN.sub(lambda match: f"{match.group('scheme')}***:***@", text)
    return text


def contains_secret(text: str) -> bool:
    return mask_secrets(text) != text or any(pattern.search(text) for pattern in _SECRET_PATTERNS)


__all__ = ["SecretAttachmentError", "_SECRET_PATTERNS", "contains_secret", "mask_secrets"]
