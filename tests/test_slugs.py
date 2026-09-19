from __future__ import annotations

import tidal2ytm.slugs as slugs


def test_artist_slug_unicode_accent() -> None:
    assert slugs.artist_slug("Skörvik") == "skorvik"


def test_album_slug_direct_under_15() -> None:
    assert slugs.album_slug("Cinder Child") == "cinder-child"


def test_album_slug_acronym_over_15() -> None:
    # 37-char name collapses to the first letter of each word
    assert slugs.album_slug("Ember Skies Remastered Deluxe Edition") == "esrde"


def test_album_slug_truncate_single_token() -> None:
    assert slugs.album_slug("Supercalifragilisticexpialidocious") == "supercalifragil"


def test_album_slug_non_latin_fallback() -> None:
    # non-latin name forces deterministic fallback "album-<6-hex>"
    assert slugs.album_slug("未命名專輯名稱測試長字串") == "album-96755d"


def test_dedup_slugs_appends_counter() -> None:
    assert slugs.dedup_slugs(["cinder-child", "cinder-child", "cinder-child"]) == [
        "cinder-child",
        "cinder-child-2",
        "cinder-child-3",
    ]


def test_slug_fallback_is_deterministic() -> None:
    assert slugs.album_slug("音楽アルバム") == slugs.album_slug("音楽アルバム")


def test_dedup_skips_taken_suffix() -> None:
    assert slugs.dedup_slugs(["ember", "ember-2", "ember"])[-1] == "ember-3"
