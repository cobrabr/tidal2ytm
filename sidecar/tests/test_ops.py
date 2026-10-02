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
    assert ops.auth_status()["ytm"]["valid"] is True


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
