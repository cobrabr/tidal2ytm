from __future__ import annotations

import json
from pathlib import Path

from tidal2ytm_sidecar import ops

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
