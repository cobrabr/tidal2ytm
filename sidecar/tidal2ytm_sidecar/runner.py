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

    answering: list[threading.Thread] = []

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
        worker = threading.Thread(target=_answer, args=(stdout, op, params, rid), daemon=True)
        worker.start()
        answering.append(worker)

    # Every request that was read gets answered before this returns; a daemon
    # thread abandoned at process exit would truncate a reply mid-write, and the
    # host would read the missing frame as a lost sidecar.
    for worker in answering:
        worker.join()

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
