from __future__ import annotations

import os
from pathlib import Path

# All user-specific runtime files live here. The GUI names the directory
# explicitly through TIDAL2YTM_DATA_DIR, because the sidecar's working directory
# is the application data directory while a CLI run belongs to the checkout, and
# a relative path resolves to whichever of those the process happened to be
# started in. An unset or empty variable falls back to the checkout-relative
# directory. Either way the directory is created lazily via ensure_data_dir(), so
# importing this module never touches disk.
DATA_DIR = Path(os.environ.get("TIDAL2YTM_DATA_DIR") or "data")
LOGS_DIR = Path("logs")

TIDAL_TOKEN_FILE = DATA_DIR / "tidal_token.json"
YTM_AUTH_FILE = DATA_DIR / "ytm_auth.json"
PLAN_FILE = DATA_DIR / "transfer_plan.toml"


def ensure_data_dir() -> Path:
    """Create the runtime data directory if needed; returns it."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def ensure_logs_dir() -> Path:
    """Create the log directory if needed; returns it. Never called on import."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR
