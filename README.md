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

If the tool isn't confident about a match, it won't guess — it queues the track for you to review instead.

## Setup

### 1. Install uv

If you haven't already, install [`uv`](https://docs.astral.sh/uv/getting-started/installation/):

```pwsh
irm https://astral.sh/uv/install.ps1 | iex
```

### 2. Clone and install

```pwsh
git clone https://github.com/cobrabr/tidal2ytm.git
cd tidal2ytm
uv sync
```

### 3. Authenticate YouTube Music (once)

As of November 2024, ytmusicapi requires your own Google Cloud OAuth credentials. Do this once:

**Option A — via `gcloud` CLI** (install [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) first):

```pwsh
# Create a project (skip if you have one already)
gcloud projects create YOUR_PROJECT_ID --name="tidal2ytm"
gcloud config set project YOUR_PROJECT_ID

# Enable the YouTube Data API
gcloud services enable youtube.googleapis.com
```

> [!NOTE]
> Creating the OAuth client ID (type: **TVs and Limited Input devices**) cannot be done via `gcloud` — that one step requires the Cloud Console UI. Open [APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials), click **Create Credentials → OAuth client ID**, choose **TVs and Limited Input devices**, and note the **Client ID** and **Client Secret**.

**Option B — entirely via [Google Cloud Console](https://console.cloud.google.com/)**:

1. Create or select a project.
2. Enable the **YouTube Data API v3** under **APIs & Services → Library**.
3. Under **APIs & Services → Credentials**, click **Create Credentials → OAuth client ID**.
4. Choose **TVs and Limited Input devices** as the application type.
5. Note the generated **Client ID** and **Client Secret**.

Then run:

```pwsh
uv run ytmusicapi oauth
New-Item -Path "data" -Type Directory -ErrorAction SilentlyContinue
Move-Item oauth.json data/ytm_auth.json
```

The command will prompt you to enter your **Client ID** and **Client Secret**, then print a URL and a short code. Open the URL in a browser, sign in with the Google account that has YouTube Music, enter the code when asked, and grant the requested permissions. The terminal will detect the approval automatically and write `oauth.json`.

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

All runtime files are written to the `data/` directory (git-ignored).

| File | Purpose |
|---|---|
| `data/tidal_token.json` | Cached Tidal OAuth token |
| `data/ytm_auth.json` | YTM auth (you create this once) |
| `data/transfer_state.json` | Progress — which tracks are done |
| `data/review.json` | Tracks needing manual review |

## Notes

- **ISRC on YTM**: `get_song()` doesn't always return ISRC. When it does, the match is exact. When it doesn't, duration + fuzzy album takes over.
- **Classical music**: ISRC is your best friend here. Tracks where it doesn't resolve on YTM's side land in the review queue, which is the right outcome — you want to verify the correct recording manually.
- **Tracks not on YTM**: Show up as "No candidates found" in the review queue. Handle them via YTM's file upload feature.
- **Rate limiting**: Small delays are built in. For large libraries, just let it run overnight.
