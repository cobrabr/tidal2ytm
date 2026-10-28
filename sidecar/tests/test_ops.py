from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from tidal2ytm_sidecar import ops, protocol, refresh

from tidal2ytm import auth, tidal_source
from tidal2ytm.models import SourceTrack

_SIGNED_OUT = {"status": "signed_out", "expires_at": None, "has_client_secret": True}


def test_auth_status_reports_signed_out_when_no_token_is_cached(
    isolated_data_dir: Path,
) -> None:
    assert ops.auth_status() == {"tidal": _SIGNED_OUT, "ytm": _SIGNED_OUT}


def test_auth_status_reports_a_fresh_tidal_token(isolated_data_dir: Path) -> None:
    (isolated_data_dir / "tidal_token.json").write_text(
        json.dumps(
            {
                "token_type": "Bearer",
                "access_token": "a",
                "refresh_token": "r",
                "expiry_time": "2099-01-01T00:00:00.000000+00:00",
                "user_id": 1,
                "country_code": "US",
            }
        ),
        encoding="utf-8",
    )
    tidal = ops.auth_status()["tidal"]
    assert tidal["status"] == "valid"
    assert tidal["expires_at"] == "2099-01-01T00:00:00.000000+00:00"


def test_auth_status_reports_a_fresh_ytm_token(isolated_data_dir: Path) -> None:
    (isolated_data_dir / "ytm_auth.json").write_text(
        json.dumps({"refresh_token": "r", "expires_at": 4102444800}), encoding="utf-8"
    )
    ytm = ops.auth_status()["ytm"]
    assert ytm["status"] == "valid"
    # The expiry is asserted, not just the validity: `ytmusicapi` records it as
    # epoch seconds, and forwarding that integer made the Rust host refuse the
    # whole answer ("invalid type: integer, expected a string"), so a signed-in
    # user saw an error on the application's only screen. A test that checked
    # the status alone passed for the whole of Phase 2.
    assert ytm["expires_at"] == "2100-01-01T00:00:00+00:00"


# An epoch no platform can represent is a fact about the cache file. If
# `fromtimestamp` raised, `dispatch` would turn it into an `upstream` failure and
# the user would lose Tidal's status too, over one unusable number. The session is
# still valid — the timestamp is far in the future — so the answer is signed in
# with no expiry shown rather than an error.
def test_auth_status_survives_an_unrepresentable_ytm_expiry(isolated_data_dir: Path) -> None:
    (isolated_data_dir / "ytm_auth.json").write_text(
        json.dumps({"refresh_token": "r", "expires_at": 10**30}), encoding="utf-8"
    )
    ytm = ops.auth_status()["ytm"]
    assert ytm["status"] == "valid"
    assert ytm["expires_at"] is None


# A boolean is an `int` in Python. Reading it as epoch second 0 or 1 would report
# an expiry for a token that has none.
def test_a_boolean_ytm_expiry_is_not_an_epoch(isolated_data_dir: Path) -> None:
    (isolated_data_dir / "ytm_auth.json").write_text(
        json.dumps({"refresh_token": "r", "expires_at": True}), encoding="utf-8"
    )
    assert ops.auth_status()["ytm"]["expires_at"] is None


# Review Focus #5: a token file truncated by a crash mid-write must degrade to
# "not signed in". If this raises, one bad file makes the whole app
# unstartable and the only remedy is deleting the data directory by hand.
def test_auth_status_survives_a_corrupt_tidal_token(isolated_data_dir: Path) -> None:
    (isolated_data_dir / "tidal_token.json").write_text('{"access_token": "tr', encoding="utf-8")
    assert ops.auth_status()["tidal"] == _SIGNED_OUT


# The Tidal side of the same hole YouTube Music had. `expiry_time` is written as
# an ISO string, so a number in it is implausible — but it reaches one wire
# field, and forwarding it verbatim fails the host's `expires_at`, which takes
# YouTube Music's status down with the answer. `auth.tidal_expiry_iso` is the
# guard; this is what says it is still there.
def test_auth_status_does_not_forward_a_non_string_tidal_expiry(isolated_data_dir: Path) -> None:
    (isolated_data_dir / "tidal_token.json").write_text(
        json.dumps(
            {
                "token_type": "Bearer",
                "access_token": "a",
                "refresh_token": "r",
                "expiry_time": 4102444800,
                "user_id": 1,
                "country_code": "US",
            }
        ),
        encoding="utf-8",
    )
    tidal = ops.auth_status()["tidal"]
    # `refreshable`, not `signed_out`: the unusable number is this cache's
    # expiry, and the refresh token beside it is still good for months. What
    # this test is about is the field below it, not the standing.
    assert tidal["status"] == "refreshable"
    assert tidal["expires_at"] is None
    # The other provider is unaffected: one unusable number in one cache file
    # must not cost the user the provider that is fine.
    assert ops.auth_status()["ytm"] == _SIGNED_OUT


# The defect these three cases exist for: a boolean cannot say "signed in" and
# "signed out but I hold a refresh token". A Tidal access token lives for hours
# and the refresh token beside it for months, so a person signed in yesterday is
# told they are signed out today through no action of their own — and the only
# remedy the screen can offer them is to sign in again, which is the wrong work.
def test_an_expired_tidal_token_holding_a_refresh_token_is_refreshable(
    isolated_data_dir: Path,
) -> None:
    (isolated_data_dir / "tidal_token.json").write_text(
        json.dumps(
            {
                "token_type": "Bearer",
                "access_token": "a",
                "refresh_token": "r",
                "expiry_time": "2020-01-01T00:00:00.000000+00:00",
                "user_id": 1,
                "country_code": "US",
            }
        ),
        encoding="utf-8",
    )
    assert ops.auth_status()["tidal"]["status"] == "refreshable"


# The other half of that cell, and the one that gives the first its meaning: an
# expired token with nothing to refresh it from is genuinely signed out, and
# reporting it as refreshable would send the user to wait for a refresh that
# never arrives.
def test_an_expired_tidal_token_with_nothing_to_refresh_is_signed_out(
    isolated_data_dir: Path,
) -> None:
    (isolated_data_dir / "tidal_token.json").write_text(
        json.dumps(
            {
                "token_type": "Bearer",
                "access_token": "a",
                "expiry_time": "2020-01-01T00:00:00.000000+00:00",
                "user_id": 1,
                "country_code": "US",
            }
        ),
        encoding="utf-8",
    )
    assert ops.auth_status()["tidal"]["status"] == "signed_out"


def _track(tidal_id: int) -> SourceTrack:
    return SourceTrack(
        tidal_id=tidal_id,
        title=f"Track {tidal_id}",
        artist="Wren Halloway",
        artists=["Wren Halloway"],
        album="Fen Hymns",
        album_id=11,
        album_year=2019,
        duration_sec=200,
        track_num=1,
        disc_num=1,
        version=None,
    )


def _one_page(
    monkeypatch: pytest.MonkeyPatch,
    tracks: list[SourceTrack],
) -> list[tuple[int, int]]:
    """Stub the session and the one page fetch; return the paging calls made."""

    def _load(_token_file: Path) -> object:
        return object()

    asked: list[tuple[int, int]] = []

    def _fetch(_session: object, offset: int, limit: int) -> list[SourceTrack]:
        asked.append((offset, limit))
        return tracks

    monkeypatch.setattr(refresh, "load_session", _load)
    monkeypatch.setattr(tidal_source, "fetch_favorite_page", _fetch)
    return asked


def test_a_full_page_is_reported_as_incomplete_and_paging_is_passed_through(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _one_page(monkeypatch, [_track(1), _track(2)])

    result = ops.favorites_list(100, 2)

    # Unchanged: the host owns the loop, and an op that adjusted either number
    # would page a list nobody asked it to page.
    assert asked == [(100, 2)]
    rows = cast("list[dict[str, object]]", result["tracks"])
    assert [row["tidal_id"] for row in rows] == [1, 2]
    assert result["page_complete"] is False


def test_a_short_page_is_reported_as_complete(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _one_page(monkeypatch, [_track(1)])

    # The host breaks its loop on this flag rather than asking for a page it
    # already knows is the last one.
    assert ops.favorites_list(0, 2)["page_complete"] is True


# A `limit` of zero is not an empty page: the op would answer with a page of no
# tracks, report it complete, and the host's loop would end having fetched
# nothing. A negative offset asks Tidal for a page that cannot exist.
def test_paging_parameters_the_op_cannot_use_are_refused(
    isolated_data_dir: Path,
) -> None:
    for offset, limit in ((0, 0), (0, -1), (-1, 10)):
        with pytest.raises(ops.BadRequestError):
            ops.favorites_list(offset, limit)


def test_a_refresh_failure_is_reported_as_auth_expired(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(_file: Path) -> object:
        raise refresh.RefreshFailedError("no usable Tidal token cache")

    monkeypatch.setattr(refresh, "load_session", refused)

    # Over the wire, not as an exception: the host branches on this kind and
    # offers a sign-in for it, where a generic failure offers nothing.
    response = protocol.dispatch("favorites.list", {"offset": 0, "limit": 10}, rid=9)
    assert response["ok"] is False
    assert response["error"]["kind"] == "auth_expired"


# A request the sidecar cannot read at all — a missing or non-integer paging
# parameter — is the caller's mistake, and saying so sends whoever reads it to
# debug the host. A boolean is an `int` in Python, and `{"limit": true}` would
# otherwise be a request for a page of one.
def test_favorites_list_requires_integer_paging(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _one_page(monkeypatch, [])
    for params in (
        {},
        {"offset": 0},
        {"offset": "0", "limit": 10},
        {"offset": 0, "limit": True},
        {"offset": 0.0, "limit": 10},
    ):
        response = protocol.dispatch("favorites.list", params, rid=10)
        assert response["ok"] is False, params
        assert response["error"]["kind"] == "bad_request", params


# YouTube Music already decided this one: `auth.ytm_cache_valid` has always
# counted a stored refresh token as usable. So this guards the mapping — the
# literal the sidecar sends — rather than the rule, which `tests/test_auth.py`
# covers where the rule lives.
def test_an_expired_ytm_token_holding_a_refresh_token_is_refreshable(
    isolated_data_dir: Path,
) -> None:
    (isolated_data_dir / "ytm_auth.json").write_text(
        json.dumps({"refresh_token": "r", "expires_at": 1577836800}), encoding="utf-8"
    )
    assert ops.auth_status()["ytm"]["status"] == "refreshable"


# YouTube Music's sign-in cannot even be offered without a Google Cloud OAuth
# client file, so this flag is what decides whether the screen offers to import
# one or to sign in. It follows the directory and not the cache file's standing:
# a signed-out provider still has to answer, and it has to answer honestly.
def test_the_client_secret_flag_follows_the_data_directory(isolated_data_dir: Path) -> None:
    assert ops.auth_status()["ytm"]["has_client_secret"] is True
    for secret in isolated_data_dir.glob("client_secret_*.json"):
        secret.unlink()
    assert ops.auth_status()["ytm"]["has_client_secret"] is False


def test_auth_status_survives_a_corrupt_ytm_token(isolated_data_dir: Path) -> None:
    (isolated_data_dir / "ytm_auth.json").write_text("{{{{", encoding="utf-8")
    assert ops.auth_status()["ytm"] == _SIGNED_OUT


def test_auth_status_survives_an_unreadable_token(isolated_data_dir: Path) -> None:
    (isolated_data_dir / "tidal_token.json").mkdir()
    assert ops.auth_status()["tidal"] == _SIGNED_OUT


def _never_started(*args: object, **kwargs: object) -> Path:
    raise AssertionError("a sign-in flow started without a credential or a known provider")


# A missing OAuth client file has to be an answer on the wire, and it is a
# question of what a `sys.exit` costs here: `SystemExit` is a `BaseException`, so
# a credential check that printed and exited would escape the runner's
# `except Exception` and end the sidecar process — taking every other request
# with it, and leaving the host to respawn into the same failure. No flow may have
# started either: `auth.sign_in_ytm` opens a browser and reaches the network.
def test_auth_begin_without_a_client_secret_is_a_readable_error(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for secret in isolated_data_dir.glob("client_secret_*.json"):
        secret.unlink()
    monkeypatch.setattr(auth, "sign_in_ytm", _never_started)

    response = protocol.dispatch("auth.begin", {"provider": "ytm"}, rid=4)

    assert response["ok"] is False
    assert response["error"]["kind"] == "bad_request"
    assert response["id"] == 4
    # What says this op ran and refused, rather than this build not having it:
    # the refusal is the credential the flow cannot start without.
    assert "client_secret" in response["error"]["message"]


# `auth.begin` takes the provider as its only parameter, so an unrecognised one
# is a request the sidecar cannot carry out. Falling through to a sign-in would
# start a browser flow and block on a human for a provider nobody asked for.
def test_auth_begin_refuses_an_unknown_provider(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth, "run_tidal_auth", _never_started)
    monkeypatch.setattr(auth, "sign_in_tidal", _never_started)
    monkeypatch.setattr(auth, "sign_in_ytm", _never_started)

    response = protocol.dispatch("auth.begin", {"provider": "deezer"}, rid=5)

    assert response["ok"] is False
    assert response["error"]["kind"] == "bad_request"
    assert "deezer" in response["error"]["message"]


# A request the sidecar cannot read at all — no `provider`, or one that is not a
# string — is the same answer, and reaches it the same way. Without this the
# missing key raises `KeyError`, which the runner can only report as `upstream`
# and so blames the sidecar or the provider for a typo in the host.
def test_auth_begin_requires_a_provider(isolated_data_dir: Path) -> None:
    for params in ({}, {"provider": 7}):
        response = protocol.dispatch("auth.begin", params, rid=6)
        assert response["error"]["kind"] == "bad_request", params
        assert "provider" in response["error"]["message"], params
