from __future__ import annotations

import json
import logging
import threading
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from tidal2ytm_sidecar import protocol, runner

# Long enough that a stub which does its work always finishes inside it, short
# enough that a leaked thread fails this test rather than hanging the job.
_JOIN_TIMEOUT = 10.0


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
# immediately instead of waiting on a timeout that is not its own.
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
    assert answered[cap + 1]["error"]["kind"] == "bad_request"
    assert all(answer["ok"] for answer in answered.values() if answer["id"] <= cap)
