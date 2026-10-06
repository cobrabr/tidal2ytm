"""The PyInstaller entry script for the sidecar.

`python -m tidal2ytm_sidecar` runs `tidal2ytm_sidecar/__main__.py`, and that
works because the interpreter sets `__package__` for a package it was asked to
run. PyInstaller does not: it freezes the entry script as a bare `__main__`, so
the relative import in `__main__.py` raises `ImportError: attempted relative
import with no known parent package` inside the built executable — a failure that
only appears when the artefact is run, never while it is built.

This module is a flat script that reaches the same `main()` through an absolute
import, which is all the frozen entry point needs. It is not shipped in the
wheel (`sidecar/pyproject.toml` packages `tidal2ytm_sidecar` only) and nothing
imports it at runtime; `tidal2ytm-sidecar.spec` names it as the entry script.
"""

from __future__ import annotations

import sys

from tidal2ytm_sidecar.runner import main

if __name__ == "__main__":
    sys.exit(main())
