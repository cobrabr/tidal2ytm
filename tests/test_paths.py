from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import tidal2ytm.paths as paths

DATA_DIR_VAR = "TIDAL2YTM_DATA_DIR"


@pytest.fixture(autouse=True)
def default_data_dir_after_each_test() -> Iterator[None]:
    """Leave `paths` resolved against the unset default once a test is done.

    `monkeypatch` undoes the environment, and only the reload undoes the module:
    a test that leaves it pointing at a `tmp_path` that has since been removed
    hands the next test a data directory that does not exist. Autouse, because
    the leak is the kind of thing that makes an unrelated test fail.
    """
    yield
    os.environ.pop(DATA_DIR_VAR, None)
    importlib.reload(paths)


def test_data_dir_is_relative_when_the_variable_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DATA_DIR_VAR, raising=False)
    importlib.reload(paths)
    assert Path("data") == paths.DATA_DIR


def test_data_dir_follows_the_variable_when_it_is_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(DATA_DIR_VAR, str(tmp_path))
    importlib.reload(paths)
    assert tmp_path == paths.DATA_DIR


def test_an_empty_variable_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATA_DIR_VAR, "")
    importlib.reload(paths)
    assert Path("data") == paths.DATA_DIR


def test_the_token_paths_follow_the_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(DATA_DIR_VAR, str(tmp_path / "elsewhere"))
    importlib.reload(paths)
    assert (tmp_path / "elsewhere" / "tidal_token.json") == paths.TIDAL_TOKEN_FILE


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
