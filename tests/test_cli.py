from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import tidal2ytm.cli as cli_mod
import tidal2ytm.paths as paths


def test_cli_help_and_status_offline(tmp_path: Path, monkeypatch, capsys):
    # status without plan file should not require network (offline)
    missing = tmp_path / "nonexistent.toml"
    monkeypatch.setattr(paths, "PLAN_FILE", missing)
    monkeypatch.setattr(cli_mod, "PLAN_FILE", missing)
    cli_mod.cmd_status(MagicMock(artist=None, album=None))
    out = capsys.readouterr().out
    assert "No transfer plan" in out or "Transfer plan" in out


def test_cli_main_parses_help(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tidal2ytm", "--help"])
    with pytest.raises(SystemExit) as e:
        cli_mod.main()
    assert e.value.code == 0


def test_cli_status_offline_no_plan_prints_message(tmp_path: Path, monkeypatch, capsys):
    missing = tmp_path / "missing.toml"
    monkeypatch.setattr(paths, "PLAN_FILE", missing)
    monkeypatch.setattr(cli_mod, "PLAN_FILE", missing)
    cli_mod.cmd_status(MagicMock(artist=None, album=None))
    out = capsys.readouterr().out
    assert "No transfer plan" in out


def test_cli_status_with_plan_prints_meta(isolated_data_dir: Path, monkeypatch, capsys):
    plan_path = isolated_data_dir / "transfer_plan.toml"
    import tidal2ytm.plan_io as plan_io

    plan = {
        "meta": {"generated_at": "2026-08-29T00:00:00", "total_tracks": 1, "transferred": 0, "pending": 1, "needs_review": 0, "skip": 0, "failed": 0},
        "artists": [{"name": "A", "match_id": "a", "albums": [{"name": "B", "match_id": "a/b", "tracks": [{"tidal_id": 1, "title": "Song", "status": "pending", "yt_video_id": "AAAAAAAAAAA"}]}]}],
    }
    plan_io.save_plan(plan, plan_path)
    monkeypatch.setattr(paths, "PLAN_FILE", plan_path)
    monkeypatch.setattr(cli_mod, "PLAN_FILE", plan_path)
    cli_mod.cmd_status(MagicMock(artist=None, album=None))
    out = capsys.readouterr().out
    assert "Transfer plan" in out or "Total tracks" in out


def test_cli_status_scoped_counts(isolated_data_dir: Path, monkeypatch, capsys):
    plan_path = isolated_data_dir / "transfer_plan.toml"
    import tidal2ytm.plan_io as plan_io

    plan = {
        "meta": {"generated_at": "2026-08-29T00:00:00"},
        "artists": [
            {"name": "A", "match_id": "a", "albums": [{"name": "B", "match_id": "a/b", "tracks": [{"tidal_id": 1, "title": "S1", "status": "pending", "yt_video_id": "AAAAAAAAAAA"}, {"tidal_id": 2, "title": "S2", "status": "transferred", "yt_video_id": "BBBBBBBBBBB"}]}]},
            {"name": "C", "match_id": "c", "albums": [{"name": "D", "match_id": "c/d", "tracks": [{"tidal_id": 3, "title": "S3", "status": "pending", "yt_video_id": "CCCCCCCCCCC"}]}]},
        ],
    }
    plan_io.save_plan(plan, plan_path)
    monkeypatch.setattr(paths, "PLAN_FILE", plan_path)
    monkeypatch.setattr(cli_mod, "PLAN_FILE", plan_path)
    cli_mod.cmd_status(MagicMock(artist="a", album=None))
    out = capsys.readouterr().out
    assert "Total tracks" in out


def test_cli_transfer_scope_mutually_exclusive_required():
    # verify that the transfer parser uses mutually_exclusive_group(required=True)
    import argparse

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p_t = sub.add_parser("transfer")
    scope = p_t.add_mutually_exclusive_group(required=True)
    scope.add_argument("--track", metavar="VIDEO_ID")
    scope.add_argument("--album", metavar="MATCH_ID")
    scope.add_argument("--artist", metavar="MATCH_ID")
    scope.add_argument("--all", action="store_true")
    # ensure required=True enforced
    with pytest.raises(SystemExit):
        parser.parse_args(["transfer"])
    # multiple scopes should fail
    with pytest.raises(SystemExit):
        parser.parse_args(["transfer", "--track", "AAAAAAAAAAA", "--all"])
    # exactly one should parse
    args = parser.parse_args(["transfer", "--track", "AAAAAAAAAAA"])
    assert args.track == "AAAAAAAAAAA"

    # also verify real cli parser has required=True by inspecting it
    import inspect

    src = inspect.getsource(cli_mod.main)
    assert "mutually_exclusive_group(required=True)" in src


def test_cli_main_transfer_requires_scope(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tidal2ytm", "transfer"])
    with pytest.raises(SystemExit) as e:
        cli_mod.main()
    assert e.value.code == 2  # argparse error


def test_cli_main_transfer_rejects_multiple_scopes(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tidal2ytm", "transfer", "--track", "AAAAAAAAAAA", "--all"])
    with pytest.raises(SystemExit) as e:
        cli_mod.main()
    assert e.value.code == 2


def test_cli_main_review_filters(monkeypatch):
    # review with status filter should delegate to run_review
    monkeypatch.setattr(sys, "argv", ["tidal2ytm", "review", "--needs-review"])
    with patch("tidal2ytm.review.run_review") as mock_review:
        monkeypatch.setattr("tidal2ytm.cli._tidal_login", lambda: MagicMock())
        monkeypatch.setattr("tidal2ytm.cli._ytm_login", lambda: MagicMock())
        # avoid needing real plan file by mocking run_review directly via cmd_review path
        # call main and verify run_review called with correct filter
        try:
            cli_mod.main()
        except SystemExit:
            pass
        # Instead test cmd_review mapping directly
    # direct cmd_review test
    with patch("tidal2ytm.review.run_review") as mock:
        cli_mod.cmd_review(MagicMock(needs_review=True, pending=False, failed=False, skip=False, transferred=False, all_statuses=False, artist=None, album=None))
        mock.assert_called_once()
        assert mock.call_args.kwargs["status_filter"] is not None


def test_cli_plan_force_flag(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tidal2ytm", "plan", "--force"])
    with patch("tidal2ytm.cli._tidal_login", return_value=MagicMock()):
        with patch("tidal2ytm.cli._ytm_login", return_value=MagicMock()):
            with patch("tidal2ytm.plan.run_plan") as mock_plan:
                cli_mod.main()
                mock_plan.assert_called_once()
                assert mock_plan.call_args.kwargs["force"] is True


def test_cli_transfer_dry_run_flag(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tidal2ytm", "transfer", "--all", "--dry-run"])
    with patch("tidal2ytm.cli._ytm_login", return_value=MagicMock()):
        with patch("tidal2ytm.transfer.run_transfer") as mock_t:
            cli_mod.main()
            mock_t.assert_called_once()
            assert mock_t.call_args.kwargs["dry_run"] is True


def test_cli_main_unknown_command_exits(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tidal2ytm", "unknown"])
    with pytest.raises(SystemExit) as e:
        cli_mod.main()
    assert e.value.code == 2


def test_cli_help_and_subcommand_help(monkeypatch):
    for args in [["tidal2ytm", "plan", "--help"], ["tidal2ytm", "transfer", "--help"], ["tidal2ytm", "review", "--help"], ["tidal2ytm", "status", "--help"]]:
        monkeypatch.setattr(sys, "argv", args)
        with pytest.raises(SystemExit) as e:
            cli_mod.main()
        assert e.value.code == 0
