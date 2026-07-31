from __future__ import annotations

PROTOCOL_VERSION = "0.3.0"
PROTOCOL_MAJOR = 0
CONTRACTS_SCHEMA_SHA256 = (
    "9fdf093f0005b9459e3c7ae6e30c02238555f3a6406f4eacd08a62f15c00afc2"
)


def is_protocol_compatible(version: str) -> bool:
    """Return whether a SemVer-like protocol version shares the pinned major."""

    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return False
    return int(parts[0]) == PROTOCOL_MAJOR
