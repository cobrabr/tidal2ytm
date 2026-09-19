from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest


def test_import_has_no_side_effects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing tidal2ytm.paths creates nothing on disk."""
    monkeypatch.chdir(tmp_path)
    assert not (tmp_path / "data").exists()
    import tidal2ytm.paths as paths

    importlib.reload(paths)
    assert not (tmp_path / "data").exists()
    importlib.reload(paths)


def test_ensure_data_dir_creates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import tidal2ytm.paths as paths

    target = tmp_path / "data"
    monkeypatch.setattr(paths, "DATA_DIR", target)
    assert not target.exists()
    assert paths.ensure_data_dir() == target
    assert target.is_dir()


def test_save_plan_creates_missing_parent(tmp_path: Path, monkeypatch: Any) -> None:
    """plan_io.save_plan ensures its own parent dir (no import-time mkdir needed)."""
    import tidal2ytm.paths as paths

    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    import tidal2ytm.plan_io as plan_io

    dest = tmp_path / "fresh" / "transfer_plan.toml"
    plan_io.save_plan({"meta": {}, "artists": []}, dest)
    assert dest.exists()
