from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from tidal2ytm_sidecar import protocol

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"


def _contract(name: str) -> Any:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def test_hello_matches_the_shared_contract() -> None:
    assert protocol.hello() == _contract("hello.json")


def test_success_response_matches_the_shared_contract() -> None:
    contract = _contract("auth_status.json")
    assert protocol.success_response(1, contract["data"]) == contract


def test_error_response_matches_the_shared_contract() -> None:
    assert protocol.error_response(
        1, "auth_expired", "YouTube Music token expired; re-authenticate."
    ) == _contract("error_auth_expired.json")


def test_read_message_rejects_a_json_array() -> None:
    with pytest.raises(ValueError):
        protocol.read_message("[1, 2, 3]")


def test_read_message_rejects_a_non_json_line() -> None:
    with pytest.raises(ValueError):
        protocol.read_message("not json at all")


# Review Focus #4: the host can name an op the sidecar has not grown yet, and a
# stale packaged sidecar is a normal thing to run into. That has to come back
# as bad_request, not a crash and not a silent empty success.
def test_dispatch_rejects_an_unimplemented_op() -> None:
    response = protocol.dispatch("library.add", {}, rid=7)
    assert response == {
        "id": 7,
        "ok": False,
        "error": {
            "kind": "bad_request",
            "message": "Sidecar does not implement op 'library.add'",
        },
    }


def test_dispatch_rejects_a_request_with_no_id() -> None:
    response = protocol.dispatch("auth.status", {}, rid=None)
    assert response["ok"] is False
    assert response["error"]["kind"] == "bad_request"


def test_error_response_refuses_an_unknown_kind() -> None:
    with pytest.raises(ValueError):
        protocol.error_response(1, "quota_exceeded", "nope")
