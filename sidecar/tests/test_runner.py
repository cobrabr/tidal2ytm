from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from typing import Any

from tidal2ytm_sidecar import runner


def _run(lines: str) -> list[dict[str, Any]]:
    stdout = StringIO()
    runner.run(StringIO(lines), stdout)
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


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
