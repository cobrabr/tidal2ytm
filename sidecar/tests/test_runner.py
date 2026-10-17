from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Mapping
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import requests
from tidal2ytm_sidecar import protocol, runner

from tidal2ytm import auth

# Long enough that a stub which does its work always finishes inside it, short
# enough that a leaked thread fails this test rather than hanging the job.
_JOIN_TIMEOUT = 10.0


def _no_browser(**_kwargs: Any) -> None:
    """`setup_oauth` without the browser it would open, and without a network.

    The flow is stubbed at both of its edges rather than in the middle, so the
    code between them — the post-flow token check — is the production one.
    """


def _refusing_session(*_args: object, **_kwargs: object) -> object:
    """A `YTMusic` whose cached token will not load.

    What the post-flow check reads, and the only part of the session this test
    needs. The refusal is a `requests` failure because that is what a token the
    provider will not honour looks like from here, and it is one of the narrow
    `_AUTH_ERRORS` the check catches — the point is that the check raises an
    ordinary exception rather than exiting.
    """

    class _Token:
        @property
        def access_token(self) -> str:
            raise requests.RequestException("the provider will not load this token")

    return SimpleNamespace(_token=_Token())


def _run(lines: str) -> list[dict[str, Any]]:
    """Feed `lines` to the loop and return every envelope it wrote.

    Replies come from request threads, so the wait for them belongs here rather
    than in `run`: production must not delay its exit on an op still in flight,
    while this helper is what reads captured output. Every thread the run
    started is alive by the time `run` returns, so the ones left afterwards are
    exactly the replies still owed.

    The join is bounded because an op can now wait on a human for minutes: a
    thread that outlives the wait is a stub that stopped answering, and the
    assertion says so instead of leaving the suite to hang with no diagnostic.
    """
    stdout = StringIO()
    before = set(threading.enumerate())
    runner.run(StringIO(lines), stdout)
    for thread in set(threading.enumerate()) - before:
        thread.join(timeout=_JOIN_TIMEOUT)
        assert not thread.is_alive(), (
            "a request thread outlived the wait for its reply, so what it was "
            f"answering never reached the pipe: {thread.name}"
        )
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def _logged(lines: str, caplog: pytest.LogCaptureFixture) -> list[str]:
    with caplog.at_level(logging.INFO, logger="tidal2ytm.sidecar"):
        _run(lines)
    return [r.getMessage() for r in caplog.records if r.name == "tidal2ytm.sidecar"]


def test_handshake_is_the_first_thing_written() -> None:
    envelopes = _run("")
    assert envelopes[0]["event"] == "hello"
    assert envelopes[0]["protocol"] == 1
    assert envelopes[0]["capabilities"] == ["auth.status", "auth.begin"]


def test_auth_status_is_answered_inline(isolated_data_dir: Path) -> None:
    envelopes = _run('{"id": 1, "op": "auth.status", "params": {}}\n')
    assert envelopes[1]["id"] == 1
    assert envelopes[1]["ok"] is True


def test_a_blank_line_is_ignored(isolated_data_dir: Path) -> None:
    envelopes = _run('\n{"id": 2, "op": "auth.status", "params": {}}\n\n')
    assert len(envelopes) == 2


def test_an_unparseable_line_does_not_end_the_session(isolated_data_dir: Path) -> None:
    envelopes = _run('garbage\n{"id": 3, "op": "auth.status", "params": {}}\n')
    assert envelopes[1]["id"] == 3


def test_a_request_with_a_non_string_op_is_answered_not_dropped(
    isolated_data_dir: Path,
) -> None:
    envelopes = _run('{"id": 4, "op": 5, "params": {}}\n')
    assert envelopes[1]["ok"] is False
    assert envelopes[1]["error"]["kind"] == "bad_request"


# The host is a different process on a different machine. `params` is logged
# before dispatch, so a scalar there must not take the whole session down.
def test_a_scalar_params_field_does_not_end_the_session(isolated_data_dir: Path) -> None:
    envelopes = _run('{"id": 5, "op": "auth.status", "params": 7}\n')
    assert envelopes[1]["id"] == 5
    assert envelopes[1]["ok"] is True


# A string scalar has no keys for the key-pattern filter to match on, so the
# free-text filter is the only thing between it and the log file. A bare token
# must never reach a record, whatever the host called the field.
def test_a_string_scalar_params_never_reaches_the_log(
    isolated_data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    token = "ya29.A0ARrdmockSecretValue1234567890abcdef"  # noqa: S105
    messages = _logged(
        json.dumps({"id": 9, "op": "favorites.list", "params": token}) + "\n", caplog
    )
    assert any(token in m for m in messages) is False


# `auth.*` ops carry credentials by definition, so they log shape only. This is
# vacuous while auth.status takes no params and load-bearing the moment
# auth.begin lands, which is the op that carries token material.
#
# `client_id` is deliberately a key the pattern denylist does NOT match: with
# it, the test fails if the runner ever falls back to plain `redact_payload`,
# which would keep the value. That is the whole difference between the two.
def test_an_auth_op_logs_param_shape_not_values(
    isolated_data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "ya29.A0ARrdmockSecretValue1234567890abcdef"  # noqa: S105
    request = json.dumps(
        {"id": 10, "op": "auth.begin", "params": {"client_id": secret, "force": True}}
    )
    messages = _logged(request + "\n", caplog)
    assert any("client_id" in m for m in messages), "the key names must stay visible"
    assert any(secret in m for m in messages) is False


# Matching and debugging data has to stay legible; over-redaction is a defect,
# not a safe default. Both of these are real values the GUI and logs rely on.
def test_logged_values_keep_matching_data_visible(
    isolated_data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    slug = "tangerine-dream-deluxe-edition-2"
    request = json.dumps({"id": 11, "op": "favorites.list", "params": {"artist_slug": slug}})
    messages = _logged(request + "\n", caplog)
    assert any(slug in m for m in messages)


# An op may block on a human for minutes — `auth.begin` runs an OAuth flow that
# waits on a browser — while the host keeps asking `auth.status`, which it gives
# five seconds. The wire keys every reply by id, so out-of-order answers were
# always safe; what has to hold is that the read loop never waits on an op. A
# serial loop answers the first request and does not even read the second until
# the first returns, so the second op here would never run at all.
#
# The overlap is recorded in the stub rather than in a `Mock`'s call count,
# which is only meaningful once everything has finished.
def test_a_blocked_operation_does_not_hold_up_the_next(monkeypatch: pytest.MonkeyPatch) -> None:
    first_returned = threading.Event()
    release_first = threading.Event()
    overlapped: list[bool] = []

    def fake_dispatch(op: str, params: Mapping[str, Any], rid: int | None = None) -> dict[str, Any]:
        if rid == 1:
            # The wait is what a blocking op looks like from the loop's side.
            # Its timeout is what turns a serial loop into a failure rather
            # than a hang: the loop would never read line two to release it.
            release_first.wait(timeout=5)
            first_returned.set()
        else:
            overlapped.append(not first_returned.is_set())
            release_first.set()
        return protocol.success_response(rid or 0, {"op": op})

    monkeypatch.setattr(protocol, "dispatch", fake_dispatch)

    envelopes = _run(
        '{"id": 1, "op": "auth.status", "params": {}}\n'
        '{"id": 2, "op": "auth.status", "params": {}}\n'
    )
    assert overlapped == [True], "the second op ran while the first was still blocked"
    # Both answered, each under its own id: a reply key captured late from the
    # loop's variable would hand every op the last id.
    assert sorted(e["id"] for e in envelopes[1:]) == [1, 2]


# An unhandled failure inside a request thread is invisible by default: the
# thread ends, the session carries on, and the one request it was serving is
# simply never answered. The host sees that as its own timeout and shows
# "the sidecar is not answering" for a bug that is in one op. This arm is the
# only thing that turns it into an error carrying the request's own id.
def test_an_op_that_raises_answers_upstream_under_its_own_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_dispatch(op: str, params: Mapping[str, Any], rid: int | None = None) -> dict[str, Any]:
        raise RuntimeError("the op fell over")

    monkeypatch.setattr(protocol, "dispatch", fake_dispatch)

    envelopes = _run('{"id": 12, "op": "auth.status", "params": {}}\n')

    assert envelopes[1] == {
        "id": 12,
        "ok": False,
        "error": {"kind": "upstream", "message": "the op fell over"},
    }


# One thread per request, unbounded, means one thread and one OAuth child
# process per sign-in, held for as long as a browser and a human take. The cap
# is set well above what one person can start — the frontend offers one sign-in
# at a time — and it refuses rather than queues, because a queued `auth.status`
# would spend the host's five-second budget behind a sign-in and be reported as
# a sidecar that is not answering. The caller of the refused request learns
# immediately instead of waiting on a timeout that is not its own, and the kind
# says it was the sidecar's capacity rather than the caller's mistake.
def test_a_request_past_the_cap_is_refused_rather_than_queued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cap = runner.MAX_CONCURRENT_OPS
    release = threading.Event()

    class SignallingStdout(StringIO):
        """The pipe, saying when a refusal reaches it."""

        refused = threading.Event()

        def write(self, text: str) -> int:
            if '"ok":false' in text:
                self.refused.set()
            return super().write(text)

    def fake_dispatch(op: str, params: Mapping[str, Any], rid: int | None = None) -> dict[str, Any]:
        release.wait(timeout=_JOIN_TIMEOUT)
        return protocol.success_response(rid or 0, {"op": op})

    monkeypatch.setattr(protocol, "dispatch", fake_dispatch)

    busy = "".join(
        json.dumps({"id": i, "op": "auth.begin", "params": {"provider": "ytm"}}) + "\n"
        for i in range(1, cap + 1)
    )
    lines = busy + json.dumps({"id": cap + 1, "op": "auth.status"}) + "\n"
    stdout = SignallingStdout()
    # Run the loop on a thread of the test's own, so the busy ops are provably
    # still blocked when the last request is refused — releasing them from here,
    # after the refusal, is what makes the saturation a fact rather than a race.
    before = set(threading.enumerate())
    loop = threading.Thread(target=runner.run, args=(StringIO(lines), stdout), daemon=True)
    loop.start()
    try:
        assert stdout.refused.wait(timeout=_JOIN_TIMEOUT), "the request past the cap was never"
    finally:
        release.set()
    # The loop returns at EOF whether or not the ops it started have answered, so
    # the threads are waited for here as well — otherwise the last reply can
    # still be on its way to the pipe when it is read.
    for thread in set(threading.enumerate()) - before:
        thread.join(timeout=_JOIN_TIMEOUT)
        assert not thread.is_alive()

    written = (json.loads(line) for line in stdout.getvalue().splitlines())
    answered = {e["id"]: e for e in written if "id" in e}
    assert sorted(answered) == list(range(1, cap + 2)), "every request got exactly one answer"
    assert answered[cap + 1]["error"]["kind"] == "rate_limited"
    assert all(answer["ok"] for answer in answered.values() if answer["id"] <= cap)


# A missing client file used to answer its request by ending the process: the
# check printed to stderr and called `sys.exit(1)`, and `SystemExit` is a
# `BaseException` that `except Exception` cannot catch. Nothing above this level
# would notice — a unit test of the check passes either way, because raising is
# not exiting — so this drives the real op through the real loop with no stub
# anywhere. The request has to come back as an error frame, and the next request
# has to be answered as well, which is what "the session survived" means: a
# regression to the exit path leaves the first request unanswered for ever and
# this fails on the missing id rather than on an exception.
def test_a_missing_client_credential_answers_and_the_session_survives(
    isolated_data_dir: Path,
) -> None:
    for secret in isolated_data_dir.glob("client_secret_*.json"):
        secret.unlink()

    envelopes = _run(
        '{"id": 20, "op": "auth.begin", "params": {"provider": "ytm"}}\n'
        '{"id": 21, "op": "auth.status", "params": {}}\n'
    )

    answered = {e["id"]: e for e in envelopes[1:]}
    assert sorted(answered) == [20, 21], "both requests were answered on one session"
    assert answered[20]["error"]["kind"] == "bad_request"
    assert answered[21]["ok"] is True


# The second `sys.exit` this task had to kill: the check on the token *after* the
# flow, which used to print and exit when the token would not load. Same shape of
# defect as the credential one and just as invisible from above — raising is not
# exiting, and a `SystemExit` on this thread leaves the request unanswered while
# the host's invoke, which waits without a timeout, never settles. The stub is
# required rather than convenient: the real flow opens a browser.
def test_a_token_that_will_not_load_answers_and_the_session_survives(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth, "setup_oauth", _no_browser)
    monkeypatch.setattr(auth, "YTMusic", _refusing_session)

    envelopes = _run(
        '{"id": 22, "op": "auth.begin", "params": {"provider": "ytm"}}\n'
        '{"id": 23, "op": "auth.status", "params": {}}\n'
    )

    answered = {e["id"]: e for e in envelopes[1:]}
    assert sorted(answered) == [22, 23], "the failing sign-in took the session with it"
    assert answered[22]["error"]["kind"] == "upstream"
    assert answered[23]["ok"] is True


# The slot is taken in the read loop and returned by the thread, so a thread that
# never starts leaves it held. One leak per failure and eight failures would wedge
# the sidecar into refusing every request it could easily have answered, reporting
# a capacity it was not using. Every start fails here, one more request than the
# cap allows, so the last answer is the one that proves the slots came back: had
# one leaked per failure, that request would arrive as the cap's `rate_limited`
# instead of as the failure it is.
def test_a_thread_that_cannot_start_gives_its_slot_back(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_thread = threading.Thread

    class _NeverStarts:
        def __init__(
            self,
            target: Callable[..., object] | None,
            args: tuple[Any, ...],
            daemon: bool,
        ) -> None:
            self._inner = real_thread(target=target, args=args, daemon=daemon)

        def start(self) -> None:
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(runner.threading, "Thread", _NeverStarts)

    requests = "".join(
        json.dumps({"id": i, "op": "auth.status", "params": {}}) + "\n"
        for i in range(1, runner.MAX_CONCURRENT_OPS + 2)
    )
    answered = {e["id"]: e for e in _run(requests)[1:]}

    assert sorted(answered) == list(range(1, runner.MAX_CONCURRENT_OPS + 2)), (
        "a request whose thread never started was never answered"
    )
    kinds = {e["error"]["kind"] for e in answered.values()}
    assert kinds == {"upstream"}, f"a leaked slot answered a request as {kinds}"
