from __future__ import annotations


PROTOCOL_VERSION = "0.2.0"
PROTOCOL_MAJOR = 0
CONTRACTS_SCHEMA_SHA256 = (
    "7e98ee5247d030399db0e96a38583579d820b20763f9b381c4d3ab64f6ffabbe"
)


def is_protocol_compatible(version: str) -> bool:
    """Return whether a SemVer-like protocol version shares the pinned major."""

    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return False
    return int(parts[0]) == PROTOCOL_MAJOR
