from __future__ import annotations

from pathlib import Path

# All user-specific runtime files live here. The directory is created lazily
# via ensure_data_dir() so importing this module never touches disk.
DATA_DIR = Path("data")

TIDAL_TOKEN_FILE = DATA_DIR / "tidal_token.json"
YTM_AUTH_FILE = DATA_DIR / "ytm_auth.json"
PLAN_FILE = DATA_DIR / "transfer_plan.toml"


def ensure_data_dir() -> Path:
    """Create the runtime data directory if needed; returns it."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR
