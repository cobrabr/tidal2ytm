from __future__ import annotations
import json
import os
import time
from dataclasses import asdict
from typing import Optional
import tidalapi
from ytmusicapi import YTMusic
from .tidal_source import get_liked_tracks
from .matcher import match_track
from .ytm_sink import save_to_library
from .paths import STATE_FILE, REVIEW_FILE


def _load(path: str) -> dict:
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def _save(data: dict, path: str) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _result_to_dict(r) -> dict:
    d = asdict(r)
    d["source_title"] = r.source.title
    d["source_artist"] = r.source.artist
    d["source_album"] = r.source.album
    d["source_isrc"] = r.source.isrc
    d["source_duration_sec"] = r.source.duration_sec
    return d


def run_transfer(
    tidal_session: tidalapi.Session,
    yt: YTMusic,
    dry_run: bool = False,
    state_path: str = STATE_FILE,
    review_path: str = REVIEW_FILE,
    max_tracks: Optional[int] = None,
) -> None:
    print("Fetching Tidal liked tracks\u2026")
    tracks = get_liked_tracks(tidal_session)
    print(f"Found {len(tracks)} tracks.")

    state = _load(state_path)
    review_items = _load(review_path)

    saved = reviewed = errors = 0

    for i, track in enumerate(tracks):
        if max_tracks and i >= max_tracks:
            break
        key = str(track.tidal_id)
        if state.get(key, {}).get("done"):
            continue

        print(f"[{i+1}/{len(tracks)}] {track.artist} \u2013 {track.title}")

        try:
            result = match_track(track, yt)
        except Exception as e:
            print(f"  [ERROR] {e}")
            state[key] = {"done": False, "error": str(e)}
            _save(state, state_path)
            errors += 1
            continue

        if result.needs_review:
            print(f"  \u2192 Review ({result.review_reason})")
            review_items[key] = _result_to_dict(result)
            _save(review_items, review_path)
            reviewed += 1
        else:
            ok = save_to_library(yt, result, dry_run=dry_run)
            if ok:
                print(f"  \u2713 {result.match_method} (conf {result.confidence:.2f})")
                saved += 1
            else:
                errors += 1

        state[key] = {
            "done": not result.needs_review,
            "method": result.match_method,
            "confidence": result.confidence,
            "yt_video_id": result.yt_video_id,
        }
        _save(state, state_path)
        time.sleep(0.1)

    print(f"\nDone.  Saved: {saved}  Needs review: {reviewed}  Errors: {errors}")
    if reviewed:
        print("Run `tidal2ytm review` to process the review queue.")
