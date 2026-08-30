# tidal2ytm

Transfers your liked tracks from Tidal to YouTube Music **accurately** — matching the right version of every song, not just the first result with the same title.

## Why not Soundiiz or TuneMyMusic?

Those services pick the first result they find. They don't account for:

- Multiple album versions (original vs. remaster vs. deluxe vs. box set)
- Wildly different versions with the same title (e.g. *Chariots of Fire* at 3:29 vs. 20:41)
- Classical tracks where title alone is useless (every recording is labelled the same thing)

## Matching strategy

| Priority | Method           | How it works                                                                                    |
| -------- | ---------------- | ----------------------------------------------------------------------------------------------- |
| 1        | **ISRC**         | Exact recording identifier. Unambiguous. Confidence: 1.0                                        |
| 2        | **Duration**     | Title + artist + duration within ±4 s. Confidence: ~0.85                                        |
| 3        | **Fuzzy album**  | Among duration matches, prefers closest album name. Confidence: ~0.70                           |
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

### 3. Authenticate (once)

The Tidal flow opens your browser to a Tidal login page and waits there until you grant access. The YTM flow walks you through a Google sign-in. Do this once after install:

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
> This step cannot currently be done via the `gcloud` CLI tool. It requires using the Google Cloud Console web interface.

1. Under **[APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials)**, click **Create Credentials → OAuth client ID**.
2. Choose **TVs and Limited Input devices** as the application type.
3. Download the client secrets JSON file by clicking the download icon next to the client ID.
4. Save the downloaded JSON file to the `data/` directory. The file name will look like `client_secret_<fullClientSecret>.json`.

#### 3.3. Run `tidal2ytm auth`

```pwsh
tidal2ytm auth
# or: uv run tidal2ytm auth --ytm-only --re-auth --client-id X --client-secret Y
```

What to expect:

- Default authenticates both YTM and Tidal. Use `--ytm-only` or `--tidal-only` to restrict to one provider.
- A provider whose cached token still validates is skipped. Use `--re-auth` to force a fresh flow (e.g. after revoking access in your account settings).
- If no `data/client_secret_*.json` is present and you do not pass `--client-id`/`--client-secret`, the command prints the relevant gcloud or Console instructions and prompts for the ID and secret, saving a synthetic credentials file alongside your plan if you paste them in.
- The YTM flow opens your browser to Google's sign-in page. The Tidal flow prints a URL and opens it in your browser; the CLI waits there until you grant access.
- Subsequent runs of `tidal2ytm auth` reuse the cached tokens; you only need to re-auth when a token expires or is revoked.

#### Bare install (recommended for daily use)

```pwsh
uv tool install --from . tidal2ytm
tidal2ytm --help
tidal2ytm status
# update later:
uv tool update tidal2ytm
```

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

| File                                      | Purpose                                                             |
| ----------------------------------------- | ------------------------------------------------------------------- |
| `data/transfer_plan.toml`                 | The main transfer plan                                              |
| `data/transfer_plan.YYYYMMDD_HHMMSS.toml` | Automated backup created on first review write                      |
| `data/tidal_token.json`                   | Cached Tidal OAuth token (created by `tidal2ytm auth`)              |
| `data/ytm_auth.json`                      | Cached YTM OAuth token (created by `tidal2ytm auth`)                |
| `data/client_secret_*.json`               | Google Cloud OAuth client credentials you download from the Console |

## Notes

- **ISRC on YTM**: When the ISRC matches a YTM candidate, the match is unambiguous. When it doesn't, duration + fuzzy album takes over.
- **Classical music**: ISRC is your best friend here. Tracks where it doesn't resolve on YTM's side land in the review queue, which is the right outcome — you want to verify the correct recording manually.
- **Tracks not on YTM**: Show up as "No candidates found" in the review queue. Handle them by clearing the video ID and setting status to `needs_review`, or using the override (`o`) key in the review TUI.
- **Rate limiting**: Small delays are built in. For large libraries, just let it run overnight.