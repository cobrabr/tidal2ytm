"""NDJSON envelopes for the sidecar protocol.

`contracts/*.json` holds a canonical example of every envelope this module
produces; the Rust suite asserts against the same files, so a rename here fails
`tidal2ytm-ipc`'s tests rather than production.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast

from . import ops

PROTOCOL_VERSION = 1

# The ops this build implements. The host requires at least `auth.status` and
# refuses to start against a sidecar that does not advertise it.
CAPABILITIES: tuple[str, ...] = ("auth.status",)

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


def dispatch(op: str, params: Mapping[str, Any], rid: int | None = None) -> dict[str, Any]:
    """Run one op and return its response envelope.

    An op this build does not implement is a `bad_request`, not a crash: the
    host can name an op ahead of the sidecar, and a stale packaged sidecar is a
    normal thing to run into. `params` is accepted and ignored because
    `auth.status` takes none; ops added later will read it here.
    """
    _ = params
    if rid is None:
        return error_response(0, BAD_REQUEST, "Request is missing an integer `id`")
    if op == "auth.status":
        return success_response(rid, ops.auth_status())
    return error_response(rid, BAD_REQUEST, f"Sidecar does not implement op {op!r}")
