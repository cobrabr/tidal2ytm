from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import tidal2ytm.plan_io as plan_io  # pyright: ignore[reportPrivateUsage]


def test_extract_video_id_forms() -> None:
    cases = {
        "dQw4w9WgXcQ": "dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ": "dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?t=10": "dQw4w9WgXcQ",
        "https://www.youtube.com/v/dQw4w9WgXcQ?foo=1": "dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ&list=PL": "dQw4w9WgXcQ",
    }
    for raw, expected in cases.items():
        assert plan_io._extract_video_id(raw) == expected  # pyright: ignore[reportPrivateUsage]


def test_extract_video_id_invalid_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        plan_io._extract_video_id("not-a-url")  # pyright: ignore[reportPrivateUsage]


def test_load_plan_normalizes(isolated_data_dir: Path, tmp_path: Path) -> None:
    # write a plan with a full URL as yt_video_id, load_plan should normalize to bare ID
    src = Path("tests/fixtures/sample_plan.toml").read_text(encoding="utf-8")
    plan_path = isolated_data_dir / "transfer_plan.toml"
    plan_path.write_text(
        src.replace("dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ"), encoding="utf-8"
    )
    plan = plan_io.load_plan(plan_path)
    vids = [t["yt_video_id"] for t in plan_io.iter_tracks(plan) if t["yt_video_id"]]
    assert vids[0] == "dQw4w9WgXcQ"


def test_save_plan_writes_header_and_backup(tmp_path: Path) -> None:
    plan: dict[str, Any] = {"meta": {"generated_at": "2026-08-29T00:00:00"}, "artists": []}
    p = tmp_path / "plan.toml"
    plan_io.save_plan(plan, p)
    assert p.read_text(encoding="utf-8").startswith("# tidal2ytm transfer plan")
    p.write_text("x", encoding="utf-8")
    backup = plan_io.backup_plan(p)
    assert re.match(r"transfer_plan\.\d{8}_\d{6}\.toml", backup.name)


def test_update_plan_meta_recomputes() -> None:
    plan: dict[str, Any] = {
        "artists": [
            {
                "match_id": "a",
                "albums": [
                    {
                        "match_id": "a/b",
                        "tracks": [
                            {"status": "pending"},
                            {"status": "transferred"},
                            {"status": "needs_review"},
                        ],
                    }
                ],
            }
        ]
    }
    plan_io.update_plan_meta(plan)
    assert plan["meta"]["total_tracks"] == 3  # type: ignore
    assert plan["meta"]["pending"] == 1  # type: ignore
    assert plan["meta"]["transferred"] == 1  # type: ignore
    assert plan["meta"]["needs_review"] == 1  # type: ignore


def test_iter_tracks_filtered_combos() -> None:
    plan = {
        "artists": [
            {
                "match_id": "a",
                "albums": [
                    {
                        "match_id": "a/b",
                        "tracks": [
                            {"tidal_id": 1, "status": "pending"},
                            {"tidal_id": 2, "status": "skip"},
                        ],
                    },
                    {"match_id": "a/c", "tracks": [{"tidal_id": 3, "status": "pending"}]},
                ],
            }
        ]
    }
    from tidal2ytm.models import TrackStatus

    assert (
        len(
            list(
                plan_io.iter_tracks_filtered(plan, status=TrackStatus.PENDING, album_match_id="a/b")
            )
        )
        == 1
    )
    assert len(list(plan_io.iter_tracks_filtered(plan, artist_match_id="a"))) == 3
