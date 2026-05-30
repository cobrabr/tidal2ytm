# tidal2ytm

Transfers your liked tracks from Tidal to YouTube Music **accurately** — matching the right version of every song, not just the first result with the same title.

## Why not Soundiiz / TuneMyMusic?

Those services pick the first result they find. They don't account for:
- Multiple album versions (original vs. remaster vs. deluxe vs. box set)
- Wildly different versions with the same title (e.g. *Chariots of Fire* at 3:29 vs. 20:41)
- Classical tracks where title alone is useless (every recording is labelled the same thing)

## Matching strategy

| Priority | Method | How it works |
|---|---|---|
| 1 | **ISRC** | Exact recording identifier. Unambiguous. Confidence: 1.0 |
| 2 | **Duration** | Title + artist + duration within ±4 s. Confidence: ~0.85 |
| 3 | **Fuzzy album** | Among duration matches, prefers closest album name. Confidence: ~0.70 |
| — | **Review queue** | Anything below threshold goes to `review.json` for manual confirmation. |

Nothing gets silently wrong-matched. If the tool isn't confident, it asks you.

## Setup

### 1. Install uv

If you haven't already, install [`uv`](https://docs.astral.sh/uv/getting-started/installation/):

```pwsh
irm https://astral.sh/uv/install.ps1 | iex
```

### 2. Install tidal2ytm

```pwsh
git clone https://github.com/cobrabr/tidal2ytm.git
cd tidal2ytm
uv sync
```

### 3. Authenticate YouTube Music (once)

```pwsh
uv run ytmusicapi oauth
mv oauth.json ytm_auth.json
```

### 4. Authenticate Tidal

No setup needed upfront. The first `transfer` run opens a browser for Tidal's OAuth device flow and caches the token in `tidal_token.json`.

## Usage

```pwsh
# Dry run — matches everything, saves nothing
uv run tidal2ytm transfer --dry-run

# Real run (incremental — safe to re-run)
uv run tidal2ytm transfer

# Review low-confidence / unmatched tracks interactively
uv run tidal2ytm review

# Check progress
uv run tidal2ytm status
```

### Review mode

For each track in the queue you'll see the source metadata + best YTM candidate + a URL. Options:

- **`c`** — confirm the suggestion and save it
- **`s`** — skip this track
- **`o`** — override: paste any YTM URL or videoId
- **`q`** — quit and save progress (resume anytime)

## Files created at runtime

| File | Purpose |
|---|---|
| `tidal_token.json` | Cached Tidal OAuth token |
| `ytm_auth.json` | YTM auth (you create this once) |
| `transfer_state.json` | Progress — which tracks are done |
| `review.json` | Tracks needing manual review |

## Notes

- **ISRC on YTM**: `get_song()` doesn't always return ISRC. When it does, the match is exact. When it doesn't, duration + fuzzy album takes over.
- **Classical music**: ISRC is your best friend. Tracks where it doesn't resolve on YTM's side land in the review queue — correct outcome, since you want to verify the right recording manually.
- **Tracks not on YTM**: Surface as "No candidates found" in the review queue. Handle via YTM's file upload.
- **Rate limiting**: Small delays are built in. For large libraries, run overnight.
