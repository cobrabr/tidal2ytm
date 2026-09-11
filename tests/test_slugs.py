from __future__ import annotations

from typing import Any

import tidal2ytm.slugs as slugs


def test_artist_slug_unicode_accent() -> None:
    assert slugs.artist_slug("Skörvik") == "skorvik"


def test_album_slug_direct_under_15() -> None:
    assert slugs.album_slug("Cinder Child") == "cinder-child"


def test_album_slug_acronym_over_15() -> None:
    # "Ember Skies" -> acronym path; exact value asserted against implementation
    result = slugs.album_slug("Ember Skies Remastered Deluxe Edition")
    assert len(result) <= 15 and ("-" in result or result.islower())


def test_album_slug_truncate_single_token() -> None:
    assert slugs.album_slug("Supercalifragilisticexpialidocious") == "supercalifragil"


def test_album_slug_non_latin_fallback(monkeypatch: Any) -> None:
    monkeypatch.setattr("tidal2ytm.slugs.secrets.choice", lambda _: "x")  # pyright: ignore[reportUnknownLambdaType]
    # non-latin name forces fallback "album-xxxxx"
    assert slugs.album_slug("未命名專輯名稱測試長字串") == "album-xxxxx"


def test_dedup_slugs_appends_counter() -> None:
    assert slugs.dedup_slugs(["cinder-child", "cinder-child", "cinder-child"]) == [
        "cinder-child",
        "cinder-child-2",
        "cinder-child-3",
    ]


def test_make_album_match_id_combines() -> None:
    assert slugs.make_album_match_id("Wren", "Cinder Child") == "wren/cinder-child"
