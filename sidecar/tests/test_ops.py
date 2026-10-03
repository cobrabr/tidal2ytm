from __future__ import annotations

import json
from pathlib import Path

from tidal2ytm_sidecar import ops

_SIGNED_OUT = {"valid": False, "expires_at": None}


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
    assert tidal["valid"] is True
    assert tidal["expires_at"] == "2099-01-01T00:00:00.000000+00:00"


def test_auth_status_reports_a_fresh_ytm_token(isolated_data_dir: Path) -> None:
    (isolated_data_dir / "ytm_auth.json").write_text(
        json.dumps({"refresh_token": "r", "expires_at": 4102444800}), encoding="utf-8"
    )
    ytm = ops.auth_status()["ytm"]
    assert ytm["valid"] is True
    # The expiry is asserted, not just the validity: `ytmusicapi` records it as
    # epoch seconds, and forwarding that integer made the Rust host refuse the
    # whole answer ("invalid type: integer, expected a string"), so a signed-in
    # user saw an error on the application's only screen. A test that checked
    # `valid` alone passed for the whole of Phase 2.
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
    assert ytm["valid"] is True
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


def test_auth_status_survives_a_corrupt_ytm_token(isolated_data_dir: Path) -> None:
    (isolated_data_dir / "ytm_auth.json").write_text("{{{{", encoding="utf-8")
    assert ops.auth_status()["ytm"] == _SIGNED_OUT


def test_auth_status_survives_an_unreadable_token(isolated_data_dir: Path) -> None:
    (isolated_data_dir / "tidal_token.json").mkdir()
    assert ops.auth_status()["tidal"] == _SIGNED_OUT
