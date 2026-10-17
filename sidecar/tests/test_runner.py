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


def _run(lines: str) -> list[dict[str, Any]]:
    stdout = StringIO()
    runner.run(StringIO(lines), stdout)
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def _logged(lines: str, caplog: pytest.LogCaptureFixture) -> list[str]:
    with caplog.at_level(logging.INFO, logger="tidal2ytm.sidecar"):
        _run(lines)
    return [r.getMessage() for r in caplog.records if r.name == "tidal2ytm.sidecar"]


def test_handshake_is_the_first_thing_written() -> None:
    envelopes = _run("")
    assert envelopes[0]["event"] == "hello"
    assert envelopes[0]["protocol"] == 1
    assert envelopes[0]["capabilities"] == ["auth.status"]


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
