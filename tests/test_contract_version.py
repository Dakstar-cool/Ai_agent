import json
from pathlib import Path

from app.contracts import (
    CONTRACTS_SCHEMA_SHA256,
    PROTOCOL_VERSION,
    is_protocol_compatible,
)


def test_worker_pins_protocol_0_3_0() -> None:
    assert PROTOCOL_VERSION == "0.3.0"


def test_contract_lock_matches_runtime_pin() -> None:
    lock = json.loads(
        (Path(__file__).resolve().parents[1] / "contracts.lock").read_text(
            encoding="utf-8"
        )
    )

    assert lock["protocol_version"] == PROTOCOL_VERSION
    assert lock["schema_sha256"] == CONTRACTS_SCHEMA_SHA256


def test_compatible_minor_and_patch_share_major() -> None:
    assert is_protocol_compatible("0.1.0") is True
    assert is_protocol_compatible("0.9.42") is True


def test_incompatible_major_and_invalid_versions_are_rejected() -> None:
    assert is_protocol_compatible("1.0.0") is False
    assert is_protocol_compatible("0.1") is False
    assert is_protocol_compatible("not-a-version") is False
