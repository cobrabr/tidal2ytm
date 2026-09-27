from __future__ import annotations

from tidal2ytm.models import SourceTrack
from tidal2ytm.track_scoring import duration_ok, score_track_fit


def _source() -> SourceTrack:
    return SourceTrack.from_dict(
        {
            "tidal_id": 31,
            "album_id": 7,
            "title": "Ember Fall",
            "artists": ["Vesper Vale"],
            "album": "Ashen Light",
            "duration": 209,
            "track_num": 4,
        }
    )


def test_duration_ok_relative_gate() -> None:
    assert duration_ok(209, 213) is True
    assert duration_ok(209, 300) is False
    assert duration_ok(0, 300) is True


def test_score_prefers_exact_position() -> None:
    src = _source()
    near = score_track_fit(src, "Ember Fall", ["Vesper Vale"], "", 209, 4)
    far = score_track_fit(src, "Ember Fall", ["Vesper Vale"], "", 209, 9)
    assert near.overall > far.overall
    assert near.track_num_match is True
    assert near.overall <= 1.0
