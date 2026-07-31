from __future__ import annotations


PROTOCOL_VERSION = "0.3.0"
PROTOCOL_MAJOR = 0
CONTRACTS_SCHEMA_SHA256 = (
    "3826a44036f0e340deac63cf9faf452d67651ada812034bec355982c20ac6104"
)


def is_protocol_compatible(version: str) -> bool:
    """Return whether a SemVer-like protocol version shares the pinned major."""

    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return False
    return int(parts[0]) == PROTOCOL_MAJOR
