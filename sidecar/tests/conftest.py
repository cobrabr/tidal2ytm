from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _reset_sidecar_logging() -> Iterator[None]:
    """`runner.run` configures the root logger; that state must not leak.

    `tests/test_logging_setup.py` does the same for the CLI's file handler.
    """
    root = logging.getLogger()
    before = list(root.handlers)
    level = root.level
    yield
    for handler in root.handlers[:]:
        if handler not in before and getattr(handler, "_tidal2ytm_stderr", False):
            root.removeHandler(handler)
    root.setLevel(level)
