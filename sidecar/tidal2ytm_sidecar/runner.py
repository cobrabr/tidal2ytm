"""The NDJSON read/answer loop."""

from __future__ import annotations

import json
import logging
import sys
import threading
from collections.abc import Mapping
from typing import IO, Any, cast

from tidal2ytm.logging_setup import redact_payload, redact_text

from . import protocol
from .sidecar_logging import level_from_env, setup_stderr_logging

_LOG = logging.getLogger("tidal2ytm.sidecar")

# stdout is the IPC channel and is shared by every answering thread. The lock
# lives inside `_write` rather than at the call sites so a caller cannot forget
# it: an interleaved write would corrupt a frame the host then fails to parse.
_WRITE_LOCK = threading.Lock()

# How many ops may be answering at once. One thread per request is unbounded,
# and `auth.begin` holds its thread — and an OAuth child process — for as long
# as a browser and a person take, so the count is a resource limit rather than a
# detail. It is set well above what one person can start, because the frontend
# offers one sign-in at a time.
#
# A cap rather than a queue: the host asks `auth.status` every few seconds while
# a sign-in is in flight and gives it five seconds, so a request that waited for
# a slot would spend that budget behind a human being slow and arrive as a sidecar
# that is not answering. Refusing the extra request instead leaves the read loop
# non-blocking and lets the caller see at once that it asked for more than the
# sidecar will run at once.
#
# `rate_limited` is the kind for it, because it is the one whose remedy is to
# come back in a moment, and the job-level retry policy already backs off on it.
# `bad_request` would mean the caller sent something wrong, and it did not: a
# person whose Home said "bad request" while a sign-in was in flight would be
# told something false about their own action.
#
# Public because the refusal names it and the test that saturates it reads it
# rather than restating the number.
MAX_CONCURRENT_OPS = 8
_OP_SLOTS = threading.Semaphore(MAX_CONCURRENT_OPS)


def _write(stream: IO[str], envelope: dict[str, object]) -> None:
    with _WRITE_LOCK:
        stream.write(json.dumps(envelope, separators=(",", ":")) + "\n")
        stream.flush()


def _answer(stream: IO[str], op: str, params: Mapping[str, Any], rid: int | None) -> None:
    """Run one op and write its answer, on a thread of its own.

    An op that blocks on a human — the OAuth flow `auth.begin` needs — would
    otherwise hold up every other request behind it, and the host gives
    `auth.status` five seconds. So an op is dispatched off the reading thread,
    which stays a plain iteration over stdin. The wire keys every reply by id,
    so answering out of order costs nothing.
    """
    try:
        _write(stream, protocol.dispatch(op, params, rid))
    except Exception as exc:  # one bad op must not end a long-lived session
        _LOG.exception("op %s failed", op)
        _write(
            stream,
            protocol.error_response(rid or 0, protocol.UPSTREAM, redact_text(str(exc))),
        )
    finally:
        # The slot was taken by the reader rather than here, so that the read
        # loop never waits. Returning it is this thread's last act: the op is
        # finished, whatever became of it.
        _OP_SLOTS.release()


def _logged_params(op: object, params: dict[str, Any]) -> object:
    """What may be logged for this request's params.

    `auth.*` ops carry credentials by definition, so they log shape only: the
    key names and nothing else. Every other op logs real values, because the
    matching and debugging data the GUI needs has to stay visible. Modelled on
    the CLI's payload logging in `ytm_client`.
    """
    if isinstance(op, str) and op.startswith("auth."):
        return {key: "[shape]" for key in params}
    return redact_payload(params)


def run(stdin: IO[str], stdout: IO[str]) -> int:
    """Answer requests until stdin closes. Returns the process exit code."""
    setup_stderr_logging(level_from_env())
    _LOG.info(
        "sidecar up, protocol %s, capabilities %s",
        protocol.PROTOCOL_VERSION,
        list(protocol.CAPABILITIES),
    )
    _write(stdout, protocol.hello())

    for line in stdin:
        if not line.strip():
            continue
        try:
            message = protocol.read_message(line)
        except ValueError as exc:
            _LOG.warning("unreadable request line dropped: %s", redact_text(str(exc)))
            continue

        raw_id = message.get("id")
        rid = raw_id if isinstance(raw_id, int) else None
        op = message.get("op")
        # `params` is whatever the host sent. The protocol says an object, but a
        # malformed request must not end the session and neither `dispatch` nor
        # `redact_payload` can take a scalar, so narrow before handing it on.
        raw_params: object = message.get("params", {})
        params: Mapping[str, Any] = {}
        shown: object = raw_params
        if isinstance(raw_params, Mapping):
            params = cast("Mapping[str, Any]", raw_params)
            shown = _logged_params(op, cast("dict[str, Any]", raw_params))
        elif isinstance(raw_params, list):
            shown = redact_payload(cast("list[Any]", raw_params))
        elif isinstance(raw_params, str):
            # A scalar has no keys for redact_payload to match on, so the only
            # thing standing between a bare token and the log file is this.
            shown = redact_text(raw_params)
        _LOG.info("rid=%s op=%s params=%s", rid, op, json.dumps(shown))

        if not isinstance(op, str):
            _write(
                stdout,
                protocol.error_response(
                    rid or 0, protocol.BAD_REQUEST, "Request is missing a string `op`"
                ),
            )
            continue

        # `op` and `rid` are passed as arguments rather than read from the loop's
        # variables, so each thread answers the request it was started for.
        # Nothing joins them: EOF means the host is shutting down and has stopped
        # reading, so an op still in flight is abandoned rather than made to
        # delay the exit — one may be blocking on a human for minutes.
        if not _OP_SLOTS.acquire(blocking=False):
            _write(
                stdout,
                protocol.error_response(
                    rid or 0,
                    protocol.RATE_LIMITED,
                    f"the sidecar is already answering {MAX_CONCURRENT_OPS} requests",
                ),
            )
            continue
        try:
            threading.Thread(target=_answer, args=(stdout, op, params, rid), daemon=True).start()
        except RuntimeError as exc:
            # The slot was taken above, and the thread that would have returned it
            # never ran. Give it back here: one leaked slot per failure and eight
            # failures wedge the sidecar into refusing every request while
            # answering none, reporting a capacity it is not using. `upstream`
            # rather than the cap's `rate_limited`, because nothing is at
            # capacity — the sidecar could not start the work.
            _OP_SLOTS.release()
            _write(
                stdout,
                protocol.error_response(
                    rid or 0,
                    protocol.UPSTREAM,
                    redact_text(f"could not start a thread to answer this request: {exc}"),
                ),
            )

    _LOG.info("stdin closed, exiting")
    return 0


def main() -> int:
    try:
        return run(sys.stdin, sys.stdout)
    except ValueError as exc:
        # An unusable TIDAL2YTM_LOG_LEVEL is a configuration error the host has
        # to hear about, so it goes to stderr and a non-zero exit rather than
        # silently defaulting.
        print(f"tidal2ytm-sidecar: {exc}", file=sys.stderr)
        return 2
