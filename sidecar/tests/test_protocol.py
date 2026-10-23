from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from tidal2ytm_sidecar import ops, protocol, refresh

from tidal2ytm import auth, tidal_source
from tidal2ytm.models import SourceTrack

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"


def _contract(name: str) -> Any:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def test_hello_matches_the_shared_contract() -> None:
    assert protocol.hello() == _contract("hello.json")


def test_success_response_matches_the_shared_contract() -> None:
    contract = _contract("auth_status.json")
    assert protocol.success_response(1, contract["data"]) == contract


# The envelope test above is tautological about the payload: it feeds the
# contract's own `data` back in as the argument. This one runs the real op, so
# a rename of `expires_at` or a dropped half of the response fails here and in
# `tidal2ytm-ipc`'s tests rather than in production. Signed-out is the case
# where both languages can produce the same bytes without a token file.
def test_the_sidecar_auth_status_payload_matches_the_shared_contract(
    isolated_data_dir: Path,
) -> None:
    # The contract records YouTube Music signed out *and* with no OAuth client
    # file, so clearing the directory is the only way the real op produces it.
    # `isolated_data_dir` seeds a client secret for the sign-in tests, and it is
    # the one thing here this answer must not contain.
    for secret in isolated_data_dir.glob("client_secret_*.json"):
        secret.unlink()
    assert ops.auth_status() == _contract("auth_status.json")["data"]


# The other half of the wire shape, and the only one both languages can produce
# without a token file or a browser: the answer to a sign-in that was started
# and stopped. `auth.begin` runs a real OAuth flow, so this stubs the flow and
# asserts the envelope the op writes around it — the echo of the provider is
# what lets a caller pair a late answer with the request that asked for it.
def test_the_auth_begin_envelope_matches_the_shared_contract(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth, "sign_in_ytm", lambda: isolated_data_dir / "ytm_auth.json")

    assert protocol.dispatch("auth.begin", {"provider": "ytm"}, rid=1) == _contract(
        "auth_begin.json"
    )


# The page itself, the third wire shape both languages can produce without a
# network: `favorites.list` answering with raw Tidal tracks. The session and the
# one page it fetches are stubbed, so what runs here is the op's own serialisation
# — and `tidal2ytm-ipc` asserts the same file, which is what makes a key renamed
# on one side a failure on both.
def test_the_favorites_list_payload_matches_the_shared_contract(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracks = [
        SourceTrack(
            tidal_id=174839201,
            title="Paper Tide",
            artist="Ilse Vandermeer",
            artists=["Ilse Vandermeer", "Bram Oduya"],
            album="Lantern Season",
            album_id=91247733,
            album_year=2014,
            duration_sec=217,
            track_num=4,
            disc_num=1,
            version=None,
        ),
        SourceTrack(
            tidal_id=174839202,
            title="Salt Bell (Reprise)",
            artist="Bram Oduya",
            artists=["Bram Oduya"],
            album="Fen Hymns",
            album_id=91247734,
            album_year=None,
            duration_sec=141,
            track_num=11,
            disc_num=2,
            version="Reprise",
        ),
    ]

    def _load(_token_file: Path) -> object:
        return object()

    def _fetch(_session: object, offset: int, limit: int) -> list[SourceTrack]:
        del offset, limit
        return tracks

    monkeypatch.setattr(refresh, "load_session", _load)
    monkeypatch.setattr(tidal_source, "fetch_favorite_page", _fetch)

    response = protocol.dispatch("favorites.list", {"offset": 0, "limit": 3}, rid=1)

    assert response == _contract("favorites_list.json")


# `bad_request` is a claim about the request, so only a refusal an op raises on
# purpose may carry it. A bug inside an op that happens to raise `ValueError` is
# the sidecar's own failure, and reporting it as a bad request would send
# whoever reads it to debug the host instead. `dispatch` lets it through for the
# runner to report as `upstream`.
def test_an_op_failing_for_its_own_reason_is_not_a_bad_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken() -> dict[str, str]:
        raise ValueError("a bug, not a request")

    monkeypatch.setattr(ops, "auth_status", broken)

    with pytest.raises(ValueError):
        protocol.dispatch("auth.status", {}, rid=8)


# The six kinds are a closed enum the host branches on, and a typo such as
# `sidecar-lost` would sail through: `_ERROR_KINDS` would contain the typo too,
# and the Rust suite pins its own literals independently. Pin the wire names.
def test_every_error_kind_has_its_documented_wire_name() -> None:
    assert protocol.AUTH_EXPIRED == "auth_expired"
    assert protocol.RATE_LIMITED == "rate_limited"
    assert protocol.NOT_FOUND == "not_found"
    assert protocol.UPSTREAM == "upstream"
    assert protocol.BAD_REQUEST == "bad_request"
    assert protocol.SIDECAR_LOST == "sidecar_lost"


@pytest.mark.parametrize(
    "kind",
    [
        "auth_expired",
        "rate_limited",
        "not_found",
        "upstream",
        "bad_request",
        "sidecar_lost",
    ],
)
def test_every_error_kind_is_accepted(kind: str) -> None:
    assert protocol.error_response(1, kind, "m")["error"]["kind"] == kind


# The three normalized shapes, read here rather than restated so that a change
# to either side's field names is a diff against `contracts/` instead of a
# failure pointing at production code nobody thought to look at. `test_envelope.py`
# is what proves the normalizers produce them; this proves the files exist in the
# shape the Rust suite parses, which is a different failure and can be caught on
# this side alone.
def test_the_candidate_contracts_are_success_envelopes() -> None:
    for name in ("candidate.json", "album_summary.json", "album_detail.json"):
        contract = _contract(name)
        assert contract["id"] == 1, name
        assert contract["ok"] is True, name
        assert isinstance(contract["data"], dict), name
        assert "error" not in contract, name


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
