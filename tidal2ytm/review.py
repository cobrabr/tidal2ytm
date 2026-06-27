from __future__ import annotations
import json
import os
from ytmusicapi import YTMusic
from ytmusicapi.models.content.enums import LikeStatus
from .paths import REVIEW_FILE, STATE_FILE


def _load(path: str) -> dict:
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def _save(data: dict, path: str) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def run_review(yt: YTMusic, dry_run: bool = False,
               review_path: str = REVIEW_FILE, state_path: str = STATE_FILE) -> None:
    items = _load(review_path)
    state = _load(state_path)
    pending = {k: v for k, v in items.items() if not v.get("confirmed") and not v.get("skipped")}

    if not pending:
        print("Nothing to review.")
        return

    print(f"{len(pending)} tracks need review.\n")

    for key, item in list(pending.items()):
        print("\u2500" * 60)
        print(f"Source:   {item['source_artist']} \u2013 {item['source_title']}")
        print(f"  Album:  {item['source_album']}")
        print(f"  ISRC:   {item.get('source_isrc') or 'n/a'}")
        print(f"  Dur:    {item['source_duration_sec']}s")
        print()
        if item.get("yt_video_id"):
            print(f"Suggest:  {item.get('yt_artist')} \u2013 {item.get('yt_title')}")
            print(f"  Album:  {item.get('yt_album') or 'n/a'}")
            print(f"  Dur:    {item.get('yt_duration_sec')}s")
            print(f"  URL:    https://music.youtube.com/watch?v={item['yt_video_id']}")
        else:
            print("Suggest:  (no match found)")
        print(f"Reason:   {item.get('review_reason')}")
        print()

        while True:
            choice = input("[c]onfirm  [s]kip  [o]verride videoId  [q]uit > ").strip().lower()
            if choice == "c":
                if not item.get("yt_video_id"):
                    print("  No video ID \u2014 use [o] to provide one.")
                    continue
                if not dry_run:
                    yt.rate_song(item["yt_video_id"], LikeStatus.LIKE)
                items[key]["confirmed"] = True
                state[key] = {"done": True, "method": "review_confirmed",
                              "yt_video_id": item["yt_video_id"]}
                print("  \u2713 Saved.")
                break
            elif choice == "s":
                items[key]["skipped"] = True
                state[key] = {"done": True, "method": "skipped"}
                print("  Skipped.")
                break
            elif choice == "o":
                vid = input("  videoId or URL: ").strip()
                if "watch?v=" in vid:
                    vid = vid.split("watch?v=")[-1].split("&")[0]
                if not dry_run:
                    yt.rate_song(vid, LikeStatus.LIKE)
                items[key]["override_video_id"] = vid
                items[key]["confirmed"] = True
                state[key] = {"done": True, "method": "review_override", "yt_video_id": vid}
                print(f"  \u2713 Saved with override: {vid}")
                break
            elif choice == "q":
                _save(items, review_path)
                _save(state, state_path)
                print("Progress saved. Resume with `tidal2ytm review`.")
                return
            else:
                print("  Invalid input.")

        _save(items, review_path)
        _save(state, state_path)

    print("\nReview complete.")
