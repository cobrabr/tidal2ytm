from __future__ import annotations

import pytest
from pathlib import Path


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    import tidal2ytm.paths as paths

    monkeypatch.setattr(paths, "DATA_DIR", data_dir)
    monkeypatch.setattr(paths, "YTM_AUTH_FILE", data_dir / "ytm_auth.json")
    monkeypatch.setattr(paths, "TIDAL_TOKEN_FILE", data_dir / "tidal_token.json")
    monkeypatch.setattr(paths, "PLAN_FILE", data_dir / "transfer_plan.toml")
    # seed minimal client_secret for tests that need _ytm_login parsing
    (data_dir / "client_secret_test.json").write_text(
        '{"installed":{"client_id":"id123","client_secret":"sec123"}}', encoding="utf-8"
    )
    return data_dir
