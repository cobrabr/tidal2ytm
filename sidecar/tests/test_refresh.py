from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import requests
from tidal2ytm_sidecar import refresh
from tidalapi.exceptions import AuthenticationError

_FUTURE = "2099-01-01T00:00:00.000000+00:00"
_LAPSED = "2020-01-01T00:00:00.000000+00:00"

# The six keys `tidal2ytm.auth` guarantees, named here because they are the
# contract: `tidalapi.Session.save_session_to_file` writes five different ones,
# wrapped in `{"data": ...}`, and a file written that way reads as no credentials
# at all — which is the failure this module exists to prevent.
_SIX_KEYS = {
    "token_type",
    "access_token",
    "refresh_token",
    "expiry_time",
    "user_id",
    "country_code",
}


def _token_file(data_dir: Path, expiry: str = _FUTURE) -> Path:
    token_file = data_dir / "tidal_token.json"
    token_file.write_text(
        json.dumps(
            {
                "token_type": "Bearer",
                "access_token": "cached-access",
                "refresh_token": "cached-refresh",
                "expiry_time": expiry,
                "user_id": 4242,
                "country_code": "CA",
            }
        ),
        encoding="utf-8",
    )
    return token_file


@dataclass
class FakeSession:
    """Every member of a `tidalapi.Session` that `refresh` drives, and no fewer.

    `persist_session` reads four token fields back off the session, so a double
    carrying only `load_oauth_session` would be stubbed narrower than the
    collaborator the module writes from. Standing in for the session and raising
    what the caller chose is the whole behaviour of this double.
    """

    token_type: str | None = "Bearer"
    access_token: str | None = "cached-access"
    refresh_token: str | None = "cached-refresh"
    expiry_time: datetime.datetime | None = None
    accepts: bool = True
    refreshes: bool = True
    raises: BaseException | None = None
    loaded: list[tuple[str, str, str, datetime.datetime | None]] = field(
        default_factory=list[tuple[str, str, str, datetime.datetime | None]]
    )

    def load_oauth_session(
        self,
        token_type: str,
        access_token: str,
        refresh_token: str | None = None,
        expiry_time: datetime.datetime | None = None,
        is_pkce: bool = False,
    ) -> bool:
        del is_pkce
        self.loaded.append((token_type, access_token, refresh_token or "", expiry_time))
        if self.raises is not None:
            raise self.raises
        # What a real refresh leaves behind: a new access token and a new expiry.
        if self.refreshes:
            self.access_token = "refreshed-access"
            self.expiry_time = datetime.datetime(2099, 1, 1, tzinfo=datetime.UTC)
        return self.accepts


def _use_session(monkeypatch: pytest.MonkeyPatch, session: FakeSession) -> FakeSession:
    monkeypatch.setattr(refresh.tidalapi, "Session", lambda: session)
    return session


def _http_error(status: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(f"HTTP {status}", response=response)


def _cached(token_file: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(token_file.read_text(encoding="utf-8"))
    return data


def test_persist_session_keeps_the_six_keys_auth_guarantees(data_dir: Path) -> None:
    token_file = _token_file(data_dir)
    session = FakeSession(
        access_token="refreshed-access",
        refresh_token="refreshed-refresh",
        expiry_time=datetime.datetime(2099, 1, 1, tzinfo=datetime.UTC),
    )

    refresh.persist_session(token_file, session)  # type: ignore[arg-type]

    assert set(_cached(token_file)) == _SIX_KEYS


# The write has to carry the refreshed values, not merely the six keys: a file
# that keeps the lapsed access token is read as `refreshable` forever, and every
# fetch then pays for a refresh it already did.
def test_persist_session_writes_the_sessions_tokens(data_dir: Path) -> None:
    token_file = _token_file(data_dir)
    session = FakeSession(
        access_token="refreshed-access",
        refresh_token="refreshed-refresh",
        expiry_time=datetime.datetime(2099, 1, 1, tzinfo=datetime.UTC),
    )

    refresh.persist_session(token_file, session)  # type: ignore[arg-type]

    cached = _cached(token_file)
    assert cached["access_token"] == "refreshed-access"
    assert cached["refresh_token"] == "refreshed-refresh"
    assert cached["expiry_time"] == "2099-01-01T00:00:00+00:00"


def test_load_session_refreshes_a_lapsed_token_and_writes_it_back(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = _token_file(data_dir, expiry=_LAPSED)
    session = _use_session(monkeypatch, FakeSession())

    refresh.load_session(token_file)

    assert session.loaded == [
        (
            "Bearer",
            "cached-access",
            "cached-refresh",
            datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC),
        )
    ]
    cached = _cached(token_file)
    assert cached["access_token"] == "refreshed-access"
    # The refresh token outlives the access token, and it is the one thing here
    # whose loss costs a person a sign-in: it is written, never cleared.
    assert cached["refresh_token"] == "cached-refresh"
    assert cached["user_id"] == 4242
    assert cached["country_code"] == "CA"


def test_a_token_that_is_still_good_is_never_rewritten(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = _token_file(data_dir)
    before = token_file.read_bytes()
    _use_session(monkeypatch, FakeSession())

    refresh.load_session(token_file)

    # Untouched rather than equal: a fetch spans many pages, and writing
    # credentials once per page is both needless and a chance to lose them.
    assert token_file.read_bytes() == before


def test_tidal_refusing_the_refresh_token_is_a_refresh_failure(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = _token_file(data_dir, expiry=_LAPSED)
    _use_session(monkeypatch, FakeSession(raises=AuthenticationError("refresh token revoked")))
    before = token_file.read_bytes()

    with pytest.raises(refresh.RefreshFailedError):
        refresh.load_session(token_file)

    assert token_file.read_bytes() == before


# The boundary of the narrow catch. A 401 is Tidal refusing the credentials; a
# 500 is Tidal being unavailable, and reporting that as "sign in again" would
# send a person to re-authenticate over an outage and achieve nothing.
def test_an_unauthorized_response_is_a_refresh_failure_and_nothing_else_is(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = _token_file(data_dir, expiry=_LAPSED)

    _use_session(monkeypatch, FakeSession(raises=_http_error(401)))
    with pytest.raises(refresh.RefreshFailedError):
        refresh.load_session(token_file)

    _use_session(monkeypatch, FakeSession(raises=_http_error(500)))
    with pytest.raises(requests.HTTPError):
        refresh.load_session(token_file)


# The project's rule: a transport failure is a bug or an outage to propagate,
# never "signed out". A person told to sign in again over a dropped connection
# loses their session standing and signs back in to the same failure.
def test_a_transport_failure_is_not_a_refresh_failure(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = _token_file(data_dir, expiry=_LAPSED)
    _use_session(monkeypatch, FakeSession(raises=requests.ConnectionError("no route")))

    with pytest.raises(requests.ConnectionError):
        refresh.load_session(token_file)


def test_a_session_tidal_will_not_accept_is_a_refresh_failure(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = _token_file(data_dir, expiry=_LAPSED)
    _use_session(monkeypatch, FakeSession(accepts=False))

    with pytest.raises(refresh.RefreshFailedError):
        refresh.load_session(token_file)


@pytest.mark.parametrize("contents", ["", '{"access_token": "tr', '{"token_type": "Bearer"}'])
def test_an_unusable_token_file_is_a_refresh_failure_that_keeps_the_file(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, contents: str
) -> None:
    token_file = data_dir / "tidal_token.json"
    token_file.write_text(contents, encoding="utf-8")
    _use_session(monkeypatch, FakeSession())

    with pytest.raises(refresh.RefreshFailedError):
        refresh.load_session(token_file)

    # The refusal is about the file, and nothing here may remove it: the next
    # read is a sign-in that can succeed, where a deleted file is a lost refresh
    # token and a lost session standing.
    assert token_file.exists()


def test_a_session_with_nothing_to_write_back_is_refused(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = _token_file(data_dir, expiry=_LAPSED)
    # A refresh that returned no access token must not replace a working one.
    _use_session(monkeypatch, FakeSession(access_token=None, refreshes=False))
    before = token_file.read_bytes()

    with pytest.raises(refresh.RefreshFailedError):
        refresh.load_session(token_file)

    assert token_file.read_bytes() == before
