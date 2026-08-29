from __future__ import annotations

from pathlib import Path

# All user-specific runtime files live here. The directory is created on first
# import so callers never have to think about it.
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

TIDAL_TOKEN_FILE = DATA_DIR / "tidal_token.json"
YTM_AUTH_FILE = DATA_DIR / "ytm_auth.json"
PLAN_FILE = DATA_DIR / "transfer_plan.toml"
# Kept for reference; no longer written to. Remove in a follow-up cleanup.
STATE_FILE = DATA_DIR / "transfer_state.json"
REVIEW_FILE = DATA_DIR / "review.json"
