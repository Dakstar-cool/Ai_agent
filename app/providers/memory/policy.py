from __future__ import annotations

import json
import re


_SENSITIVE_RE = re.compile(
    r"("
    r"api_key\s*=|"
    r"\bapi_key\b|"
    r"\bpassword\b|"
    r"\bpasswd\b|"
    r"\btoken\b|"
    r"\baccess_token\b|"
    r"\brefresh_token\b|"
    r"authorization\s*:|"
    r"bearer\s+\S+|"
    r"\bsecret\b|"
    r"-----BEGIN PRIVATE KEY-----|"
    r"-----BEGIN RSA PRIVATE KEY-----|"
    r"-----BEGIN OPENSSH PRIVATE KEY-----"
    r")",
    re.IGNORECASE,
)


def contains_sensitive_data(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return _contains_sensitive_text(value)
    if isinstance(value, bytes):
        return _contains_sensitive_text(value.decode("utf-8", errors="ignore"))
    if isinstance(value, dict):
        return any(
            contains_sensitive_data(key) or contains_sensitive_data(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(contains_sensitive_data(item) for item in value)

    try:
        serialized = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        serialized = str(value)
    return _contains_sensitive_text(serialized)


def _contains_sensitive_text(value: str) -> bool:
    return bool(_SENSITIVE_RE.search(value))
