# tidal2ytm

Transfers your liked tracks from Tidal to YouTube Music **accurately** — matching the right version of every song, not just the first result with the same title.

## Why not Soundiiz or TuneMyMusic?

Those services pick the first result they find. They don't account for:

- Multiple album versions (original vs. remaster vs. deluxe vs. box set)
- Wildly different versions with the same title (e.g. *Chariots of Fire* at 3:29 vs. 20:41)
- Classical tracks where title alone is useless (every recording is labelled the same thing)

## Matching strategy

| Priority | Method           | How it works                                                            |
|----------|------------------|-------------------------------------------------------------------------|
| 1        | **ISRC**         | Exact recording identifier. Unambiguous. Confidence: 1.0                |
| 2        | **Duration**     | Title + artist + duration within ±4 s. Confidence: ~0.85                |
| 3        | **Fuzzy album**  | Among duration matches, prefers closest album name. Confidence: ~0.70   |
| —        | **Review queue** | Anything below threshold marked `needs_review` in `transfer_plan.toml` for manual confirmation. |

If the tool isn't confident about a match, it won't guess — it sets the status to `needs_review` in the plan file.

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

As of November 2024, `ytmusicapi` requires your own Google Cloud OAuth credentials. Do this once:

#### 3.1. Create a Google Cloud Project

This can be done in one of two ways:

##### Option 1: via `gcloud` CLI

1. Install the [Google Cloud SDK](https://cloud.google.com/sdk/docs/install)
2. Run:

```pwsh
# Create a project (skip if you have one already)
gcloud projects create YOUR_PROJECT_ID --name="tidal2ytm"
gcloud config set project YOUR_PROJECT_ID

# Enable the YouTube Data API
gcloud services enable youtube.googleapis.com
```

##### Option 2: via [Google Cloud Console](https://console.cloud.google.com/)

1. Create a project.
2. Enable the **YouTube Data API v3** under **APIs & Services → Library**.

#### 3.2. Create an OAuth client ID

> [!NOTE]
> This step cannot currently be done via the `gcloud` CLI tool. It requires using the Google Cloud Console UI.

1. Under **[APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials)**, click **Create Credentials → OAuth client ID**.
2. Choose **TVs and Limited Input devices** as the application type.
3. Download the client secrets JSON file by clicking the download icon next to the client ID.
4. Save the downloaded JSON file to the `data/` directory. The file name will look like `client_secret_<fullClientSecret>.json`.

#### 3.3. Authenticate
Run:

```pwsh
New-Item -Path "data" -Type Directory -ErrorAction SilentlyContinue
uv run ytmusicapi oauth --file data/ytm_auth.json
```

1. Enter your **Client ID** and **Client Secret** (copy them from your downloaded JSON file) when prompted.
2. Open the printed URL in a browser, sign in with the Google account that has YouTube Music, enter the code when asked, and grant the requested permissions.
3. The terminal will detect the approval automatically, write `oauth.json`, and move it to `data/ytm_auth.json`.

### 4. Authenticate Tidal

No setup needed upfront. The first `plan` run opens a browser for Tidal's OAuth device flow and caches the token in `tidal_token.json`.

## Usage

The workflow consists of three steps: generating a plan, reviewing/resolving low-confidence matches, and executing the transfer.

```pwsh
# 1. Generate or update the transfer plan
uv run tidal2ytm plan

# 2. Interactively review low-confidence matches
uv run tidal2ytm review --needs-review

# 3. Transfer matches in scope
uv run tidal2ytm transfer --all
```

### 1. Plan (`plan`)
Scans Tidal liked tracks and searches YouTube Music to find matching candidates. Results are saved to `data/transfer_plan.toml`.

```pwsh
uv run tidal2ytm plan [--force]
```
- `--force`: Overwrites better matches found on subsequent runs without prompting.

### 2. Review (`review`)
Interactive Rich TUI to accept, skip, or override matches. Decisions are saved instantly. On first write, a backup of the plan is saved to `data/transfer_plan.YYYYMMDD_HHMMSS.toml`.

```pwsh
uv run tidal2ytm review [--needs-review | --pending | --failed | --skip | --transferred | --all-statuses]
                        [--artist <match_id>] [--album <match_id>]
```

#### TUI Keys:
- **Navigation**:
  - `k` / `]` / `↓` / `Enter`: Next track
  - `j` / `[` / `↑` / `Shift+Enter`: Previous track
  - `n` / `→` / `Tab`: Next album
  - `p` / `←` / `Shift+Tab`: Previous album
  - `N` / `P`: Next/Prev artist
  - `g <id>`: Jump to artist/album `match_id` or YouTube video ID
- **Decisions**:
  - `a`: Accept match (sets status to `pending`)
  - `s`: Skip track (sets status to `skip`)
  - `r`: Reject match (sets status to `needs_review`)
  - `o`: Override (prompts for YouTube video ID or URL, sets status to `pending`)
  - `t`: Mark as transferred manually
- **Other**:
  - `?` / `h`: Show help overlay
  - `q`: Quit TUI

### 3. Transfer (`transfer`)
Executes the transfer of matched tracks to your library. Requires a specific scope.

```pwsh
uv run tidal2ytm transfer (--track <video-id> | --album <match-id> | --artist <match-id> | --all)
                           [--dry-run] [--include-needs-review]
```
- `--track`: Bare 11-character YouTube video ID.
- `--album`: Album `match_id` (e.g. `jethro-tull/war-child`).
- `--artist`: Artist `match_id` (e.g. `jethro-tull`).
- `--all`: Transfer all pending tracks.
- `--dry-run`: Match and log actions without adding to YouTube Music library.
- `--include-needs-review`: Forces transfer of low-confidence matches without prior review. Shows a warning panel before starting.

### 4. Status (`status`)
Displays total counts and lists tracks needing review.

```pwsh
uv run tidal2ytm status [--artist <match-id>] [--album <match-id>]
```

## Files created at runtime

All runtime files are written to the `data/` directory (git-ignored).

| File | Purpose |
|----------------------------|----------------------------------|
| `data/tidal_token.json` | Cached Tidal OAuth token |
| `data/ytm_auth.json` | YTM auth (you create this once) |
| `data/transfer_plan.toml` | The main transfer plan |
| `data/transfer_plan.YYYYMMDD_HHMMSS.toml` | Automated backup created on first review write |

## Notes

- **ISRC on YTM**: `get_song()` and search candidate metadata are checked for ISRC. When it matches, confidence is 1.0. When it doesn't, duration + fuzzy album takes over.
- **Classical music**: ISRC is your best friend here. Tracks where it doesn't resolve on YTM's side land in the review queue, which is the right outcome — you want to verify the correct recording manually.
- **Tracks not on YTM**: Show up as "No candidates found" in the review queue. Handle them by putting an empty string or using override (`o`) in review.
- **Library Add vs Like**: The tool calls `edit_song_library_status` to add songs directly to your Library, not just thumb-up / like them.
- **Rate limiting**: Small delays are built in. For large libraries, just let it run overnight.

