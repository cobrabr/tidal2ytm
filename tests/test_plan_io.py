from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

import tidal2ytm.plan_io as plan_io


def test_extract_video_id_invalid_raises() -> None:
    with pytest.raises(ValueError):
        plan_io.extract_video_id("not-a-url")


def test_load_plan_normalizes(isolated_data_dir: Path, tmp_path: Path) -> None:
    # write a plan with a full URL as yt_video_id, load_plan should normalize to bare ID
    src = Path("tests/fixtures/sample_plan.toml").read_text(encoding="utf-8")
    plan_path = isolated_data_dir / "transfer_plan.toml"
    plan_path.write_text(
        src.replace("CCCCCCCCCCC", "https://youtu.be/CCCCCCCCCCC"), encoding="utf-8"
    )
    plan = plan_io.load_plan(plan_path)
    vids = [t["yt_video_id"] for t in plan_io.iter_tracks(plan) if t["yt_video_id"]]
    assert vids[0] == "CCCCCCCCCCC"


def test_backup_plan_writes_timestamped_copy(tmp_path: Path) -> None:
    plan: dict[str, Any] = {"meta": {"generated_at": "2026-08-29T00:00:00"}, "artists": []}
    p = tmp_path / "transfer_plan.toml"
    plan_io.save_plan(plan, p)
    backup = plan_io.backup_plan(p)
    assert re.match(r"transfer_plan\.\d{8}_\d{6}_\d{6}\.toml", backup.name)


def test_find_album_of_track() -> None:
    plan: dict[str, Any] = {
        "artists": [
            {
                "name": "A",
                "match_id": "a",
                "albums": [
                    {
                        "name": "B",
                        "match_id": "a/b",
                        "tracks": [{"tidal_id": 1}, {"tidal_id": 2}],
                    },
                    {"name": "C", "match_id": "a/c", "tracks": [{"tidal_id": 3}]},
                ],
            }
        ]
    }
    assert plan_io.find_album_of_track(plan, 2) == "a/b"
    assert plan_io.find_album_of_track(plan, 3) == "a/c"
    assert plan_io.find_album_of_track(plan, 999) is None


def test_update_track_returns_false_when_missing() -> None:
    empty_plan: dict[str, Any] = {"meta": {"generated_at": "2026-08-29T00:00:00"}, "artists": []}
    assert (
        plan_io.update_track_in_plan(
            empty_plan, 999999, {"yt_video_id": "AAAAAAAAAAA", "status": "pending"}
        )
        is False
    )


def test_save_plan_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan: dict[str, Any] = {"meta": {"generated_at": "2026-08-29T00:00:00"}, "artists": []}
    path = tmp_path / "transfer_plan.toml"
    plan_io.save_plan(plan, path)
    original_text = path.read_text(encoding="utf-8")

    real_write_text = Path.write_text

    def flaky_write_text(self: Path, data: str, *args: Any, **kwargs: Any) -> int:
        real_write_text(self, data[:10], *args, **kwargs)
        raise OSError("simulated mid-write crash")

    monkeypatch.setattr(Path, "write_text", flaky_write_text)
    with pytest.raises(OSError):
        plan_io.save_plan(plan, path)
    assert path.read_text(encoding="utf-8") == original_text


def test_backup_names_do_not_collide(tmp_path: Path) -> None:
    path = tmp_path / "transfer_plan.toml"
    path.write_text("plan", encoding="utf-8")
    first = plan_io.backup_plan(path)
    second = plan_io.backup_plan(path)
    assert first != second


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


@pytest.fixture
def plan_with_track() -> dict[str, Any]:
    return {
        "meta": {"generated_at": "2026-08-29T00:00:00"},
        "artists": [
            {
                "name": "Wren",
                "match_id": "wren",
                "albums": [
                    {
                        "name": "Cinder Child",
                        "match_id": "wren/cinder-child",
                        "tracks": [
                            {
                                "tidal_id": 123,
                                "title": "Muddle in the Puddle",
                                "yt_video_id": "AAAAAAAAAAA",
                                "status": "pending",
                            }
                        ],
                    }
                ],
            }
        ],
    }


def _plan_with_status(status: str) -> dict[str, Any]:
    return {
        "meta": {"generated_at": "2026-08-29T00:00:00"},
        "artists": [
            {
                "name": "Wren",
                "match_id": "wren",
                "albums": [
                    {
                        "name": "Cinder Child",
                        "match_id": "wren/cinder-child",
                        "tracks": [
                            {
                                "tidal_id": 123,
                                "title": "Muddle in the Puddle",
                                "yt_video_id": "AAAAAAAAAAA",
                                "status": status,
                            }
                        ],
                    }
                ],
            }
        ],
    }


@pytest.mark.parametrize(
    "url,expected",
    [
        ("CCCCCCCCCCC", "CCCCCCCCCCC"),
        ("https://www.youtube.com/watch?v=CCCCCCCCCCC", "CCCCCCCCCCC"),
        ("https://youtu.be/CCCCCCCCCCC?t=10", "CCCCCCCCCCC"),
        ("https://www.youtube.com/v/CCCCCCCCCCC?foo=1", "CCCCCCCCCCC"),
        ("https://music.youtube.com/watch?v=CCCCCCCCCCC&list=PL", "CCCCCCCCCCC"),
        ("https://m.youtube.com/watch?v=AAAAAAAAAAA", "AAAAAAAAAAA"),
        ("https://www.youtube.com/embed/AAAAAAAAAAA", "AAAAAAAAAAA"),
        ("https://www.youtube.com/shorts/AAAAAAAAAAA", "AAAAAAAAAAA"),
        ("https://www.youtube.com/live/AAAAAAAAAAA", "AAAAAAAAAAA"),
        ("https://www.youtube-nocookie.com/embed/AAAAAAAAAAA", "AAAAAAAAAAA"),
    ],
)
def test_extract_video_id_accepts_all_forms(url: str, expected: str) -> None:
    assert plan_io.extract_video_id(url) == expected


def test_find_track_by_video_id_normalizes_url(plan_with_track: dict[str, Any]) -> None:
    track = plan_io.find_track_by_video_id(plan_with_track, "https://youtu.be/AAAAAAAAAAA")
    assert track is not None
    assert track["tidal_id"] == 123


def test_find_track_by_video_id_duplicate_raises(plan_with_track: dict[str, Any]) -> None:
    plan_with_track["artists"][0]["albums"][0]["tracks"].append(
        {
            "tidal_id": 124,
            "title": "Drifting Away",
            "yt_video_id": "AAAAAAAAAAA",
            "status": "pending",
        }
    )
    with pytest.raises(ValueError):
        plan_io.find_track_by_video_id(plan_with_track, "AAAAAAAAAAA")


def test_load_plan_invalid_id_forces_review(tmp_path: Path) -> None:
    src = Path("tests/fixtures/sample_plan.toml").read_text(encoding="utf-8")
    plan_path = tmp_path / "transfer_plan.toml"
    plan_path.write_text(src.replace("CCCCCCCCCCC", "not-an-id"), encoding="utf-8")
    plan = plan_io.load_plan(plan_path)
    bad = next(t for t in plan_io.iter_tracks(plan) if t["tidal_id"] == 123)
    assert bad["yt_video_id"] == ""
    assert bad["status"] == "needs_review"


def test_update_plan_meta_rejects_unknown_status() -> None:
    with pytest.raises(ValueError):
        plan_io.update_plan_meta(_plan_with_status("bogus"))


def test_load_plan_malformed_toml_raises(tmp_path: Path) -> None:
    import tomllib

    plan_path = tmp_path / "transfer_plan.toml"
    plan_path.write_text("this is [ not toml", encoding="utf-8")
    with pytest.raises(tomllib.TOMLDecodeError):
        plan_io.load_plan(plan_path)


def test_backup_plan_failure_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Read-only directories are not portable on Windows; simulate the same
    # failure at the copy boundary instead.
    plan_path = tmp_path / "transfer_plan.toml"
    plan_path.write_text("plan", encoding="utf-8")

    def ro_copy2(src: Any, dst: Any) -> None:
        del src, dst
        raise PermissionError("destination is read-only")

    monkeypatch.setattr(plan_io.shutil, "copy2", ro_copy2)
    with pytest.raises(OSError):
        plan_io.backup_plan(plan_path)


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
