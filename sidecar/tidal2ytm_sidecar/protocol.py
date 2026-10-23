"""NDJSON envelopes for the sidecar protocol.

`contracts/*.json` holds a canonical example of every envelope this module
produces; the Rust suite asserts against the same files, so a rename here fails
`tidal2ytm-ipc`'s tests rather than production.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast

from tidal2ytm.logging_setup import redact_text

from . import ops

PROTOCOL_VERSION = 1

# The ops this build implements. The host requires at least `auth.status`,
# `auth.begin` and `favorites.list`, and refuses to start against a sidecar that
# does not advertise them — a sign-in is something a person waits on, and a sync
# is what the application is for, and finding out minutes into either that this
# build cannot offer one is not worth the wait.
CAPABILITIES: tuple[str, ...] = ("auth.status", "auth.begin", "favorites.list")

# The closed set of failure categories the host understands.
AUTH_EXPIRED = "auth_expired"
RATE_LIMITED = "rate_limited"
NOT_FOUND = "not_found"
UPSTREAM = "upstream"
BAD_REQUEST = "bad_request"
SIDECAR_LOST = "sidecar_lost"

_ERROR_KINDS = frozenset(
    {AUTH_EXPIRED, RATE_LIMITED, NOT_FOUND, UPSTREAM, BAD_REQUEST, SIDECAR_LOST}
)


def hello() -> dict[str, Any]:
    """The handshake, emitted once on startup before any request is answered."""
    return {"event": "hello", "protocol": PROTOCOL_VERSION, "capabilities": list(CAPABILITIES)}


def read_message(line: str) -> dict[str, Any]:
    """Parse one request line. Raises ValueError on anything that is not a JSON object."""
    try:
        message = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {line!r}") from exc
    if not isinstance(message, dict):
        raise ValueError(f"expected a JSON object, got {type(message).__name__}: {line!r}")
    return cast("dict[str, Any]", message)


def success_response(rid: int, data: object) -> dict[str, Any]:
    return {"id": rid, "ok": True, "data": data}


def error_response(rid: int, kind: str, message: str) -> dict[str, Any]:
    if kind not in _ERROR_KINDS:
        raise ValueError(f"unknown error kind: {kind!r}")
    return {"id": rid, "ok": False, "error": {"kind": kind, "message": message}}


def _int_param(params: Mapping[str, Any], name: str) -> int:
    """One integer parameter of a `favorites.list` request, or a refusal.

    A request that does not carry it as an integer is refused here rather than
    coerced: `str` and `None` reaching `tidalapi` would ask Tidal for a page
    nobody can describe, and the op's own range checks cannot see through a
    value handed to them as the wrong type. A boolean is refused too — it is an
    `int` in Python, and `{"limit": true}` would otherwise fetch a page of one.
    """
    value = params.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ops.BadRequestError(f"`favorites.list` requires an integer `{name}`")
    return value


def _paging(params: Mapping[str, Any]) -> tuple[int, int]:
    """The `offset` and `limit` a `favorites.list` request asked for."""
    return _int_param(params, "offset"), _int_param(params, "limit")


def dispatch(op: str, params: Mapping[str, Any], rid: int | None = None) -> dict[str, Any]:
    """Run one op and return its response envelope.

    An op this build does not implement is a `bad_request`, not a crash: the
    host can name an op ahead of the sidecar, and a stale packaged sidecar is a
    normal thing to run into.

    `ops.BadRequestError` is the same answer for the same reason: an op raises it
    when the request it was handed is one it cannot carry out, so this is the
    caller's mistake rather than the sidecar's or the provider's.
    `ops.AuthExpiredError` is `auth_expired`, which is the one failure whose remedy is
    a person doing something rather than the caller retrying. Anything else
    propagates to the runner, which reports it as `upstream`.
    """
    if rid is None:
        return error_response(0, BAD_REQUEST, "Request is missing an integer `id`")
    try:
        if op == "auth.status":
            return success_response(rid, ops.auth_status())
        if op == "auth.begin":
            provider = params.get("provider")
            if not isinstance(provider, str):
                raise ops.BadRequestError("`auth.begin` requires a string `provider`")
            return success_response(rid, ops.auth_begin(provider))
        if op == "favorites.list":
            return success_response(rid, ops.favorites_list(*_paging(params)))
    except ops.BadRequestError as exc:
        return error_response(rid, BAD_REQUEST, redact_text(str(exc)))
    except ops.AuthExpiredError as exc:
        return error_response(rid, AUTH_EXPIRED, redact_text(str(exc)))
    return error_response(rid, BAD_REQUEST, f"Sidecar does not implement op {op!r}")
