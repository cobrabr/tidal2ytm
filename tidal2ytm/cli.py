from __future__ import annotations
import argparse
import json
import os
import sys
import webbrowser
import tidalapi
from ytmusicapi import YTMusic
from .paths import DATA_DIR, TIDAL_TOKEN_FILE, YTM_AUTH_FILE, STATE_FILE, REVIEW_FILE


def _tidal_login() -> tidalapi.Session:
    session = tidalapi.Session()
    if os.path.exists(TIDAL_TOKEN_FILE):
        with open(TIDAL_TOKEN_FILE) as f:
            token_data = json.load(f)
        try:
            session.load_oauth_session(
                token_data["token_type"],
                token_data["access_token"],
                token_data["refresh_token"],
                token_data.get("expiry_time"),
            )
            if session.check_login():
                return session
        except Exception:
            pass
        print("Cached token expired. Re-authenticating\u2026")

    link_login, login_future = session.login_oauth()
    url = f"https://{link_login.verification_uri_complete}"
    print(f"Opening Tidal authorisation URL in your browser: {url}")
    webbrowser.open(url)
    login_future.result()

    with open(TIDAL_TOKEN_FILE, "w") as f:
        json.dump({
            "token_type": session.token_type,
            "access_token": session.access_token,
            "refresh_token": session.refresh_token,
            "expiry_time": session.expiry_time.isoformat() if session.expiry_time else None,
        }, f, indent=2)
    return session


def _ytm_login() -> YTMusic:
    if not os.path.exists(YTM_AUTH_FILE):
        print(
            f"'{YTM_AUTH_FILE}' not found.\n"
            "Run once to create it:\n"
            "  uv run ytmusicapi oauth\n"
            f"Then rename the output to '{YTM_AUTH_FILE}'."
        )
        sys.exit(1)

    # Locate the client secrets file in data/
    secret_files = list(DATA_DIR.glob("client_secret_*.json"))
    if not secret_files:
        print(
            "Error: Google Cloud client secrets JSON file not found in 'data/' directory.\n"
            "Please download the client secrets JSON file from Google Cloud Console,\n"
            "save it in the 'data/' directory (e.g. 'data/client_secret_<details>.json'), and try again."
        )
        sys.exit(1)

    client_secret_file = secret_files[0]
    client_id = None
    client_secret = None
    try:
        with open(client_secret_file, encoding="utf-8") as f:
            data = json.load(f)
            # Support "installed", "web", or fallback to finding keys anywhere
            for key in ["installed", "web"]:
                if key in data:
                    client_id = data[key].get("client_id")
                    client_secret = data[key].get("client_secret")
                    break
            if not client_id or not client_secret:
                for val in data.values():
                    if isinstance(val, dict) and "client_id" in val and "client_secret" in val:
                        client_id = val["client_id"]
                        client_secret = val["client_secret"]
                        break
    except Exception as e:
        print(f"Error reading client secrets file '{client_secret_file.name}': {e}")
        sys.exit(1)

    if not client_id or not client_secret:
        print(f"Error: Could not parse 'client_id' and 'client_secret' from '{client_secret_file.name}'.")
        sys.exit(1)

    from ytmusicapi import OAuthCredentials
    creds = OAuthCredentials(client_id, client_secret)
    yt = YTMusic(str(YTM_AUTH_FILE), oauth_credentials=creds)

    # Probe the token immediately so an expired/revoked refresh token surfaces
    # here with a clear message rather than as a cryptic KeyError mid-run.
    try:
        _ = yt._token.access_token
    except (KeyError, Exception) as e:
        print(
            "Error: YouTube Music authentication token is expired or revoked.\n"
            "Re-authenticate by running:\n"
            "\n"
            "  Remove-Item data\\ytm_auth.json && uv run ytmusicapi oauth --file data/ytm_auth.json\n"
            "\n"
            "Enter your Client ID and Secret from your client_secret_*.json file when prompted."
        )
        sys.exit(1)

    # Use TVHTML5 clientName by default so authenticated calls (like rate_song) succeed.
    yt.context["context"]["client"].update({
        "clientName": "TVHTML5",
        "clientVersion": "7.20230924.01.00"
    })

    # Patch _session.post to strip auth headers and use WEB_REMIX for read endpoints (search, get_song).
    original_post = yt._session.post

    def patched_post(url, *args, **kwargs):
        is_unauth = "/search?" in url or "/player?" in url
        if is_unauth:
            import copy
            import time

            if "headers" in kwargs:
                headers = kwargs["headers"].copy()
                headers.pop("authorization", None)
                headers.pop("X-Goog-Request-Time", None)
                kwargs["headers"] = headers

            original_client = yt.context["context"]["client"].copy()
            yt.context["context"]["client"].update({
                "clientName": "WEB_REMIX",
                "clientVersion": "1." + time.strftime("%Y%m%d", time.gmtime()) + ".01.00"
            })

            if "json" in kwargs and isinstance(kwargs["json"], dict):
                kwargs["json"] = copy.deepcopy(kwargs["json"])
                body = kwargs["json"]
                if "context" in body and "client" in body["context"]:
                    body["context"]["client"].update({
                        "clientName": "WEB_REMIX",
                        "clientVersion": "1." + time.strftime("%Y%m%d", time.gmtime()) + ".01.00"
                    })
            try:
                return original_post(url, *args, **kwargs)
            finally:
                yt.context["context"]["client"].update(original_client)
        else:
            return original_post(url, *args, **kwargs)

    yt._session.post = patched_post

    return yt


def cmd_transfer(args: argparse.Namespace) -> None:
    from .transfer import run_transfer
    run_transfer(_tidal_login(), _ytm_login(), dry_run=args.dry_run, max_tracks=args.limit)


def cmd_review(args: argparse.Namespace) -> None:
    from .review import run_review
    run_review(_ytm_login(), dry_run=args.dry_run)


def cmd_status(_args: argparse.Namespace) -> None:
    state_path = STATE_FILE
    review_path = REVIEW_FILE
    if not os.path.exists(state_path):
        print("No state found. Run `tidal2ytm transfer` first.")
        return
    with open(state_path) as f:
        state = json.load(f)
    done = [v for v in state.values() if v.get("done")]
    pending = [v for v in state.values() if not v.get("done")]
    methods: dict[str, int] = {}
    for v in done:
        m = v.get("method", "unknown")
        methods[m] = methods.get(m, 0) + 1
    print(f"Tracked:  {len(state)}")
    print(f"Done:     {len(done)}")
    print(f"Pending:  {len(pending)}")
    print("Methods:")
    for m, n in sorted(methods.items()):
        print(f"  {m}: {n}")
    if os.path.exists(review_path):
        with open(review_path) as f:
            review = json.load(f)
        unreviewed = [v for v in review.values() if not v.get("confirmed") and not v.get("skipped")]
        print(f"\nReview queue: {len(review)} total, {len(unreviewed)} pending")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="tidal2ytm",
        description="Transfer liked tracks from Tidal to YouTube Music, accurately.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_t = sub.add_parser("transfer", help="Run the transfer (incremental).")
    p_t.add_argument("--dry-run", action="store_true")
    p_t.add_argument("--limit", type=int, default=None)
    p_t.set_defaults(func=cmd_transfer)

    p_r = sub.add_parser("review", help="Review low-confidence matches interactively.")
    p_r.add_argument("--dry-run", action="store_true")
    p_r.set_defaults(func=cmd_review)

    p_s = sub.add_parser("status", help="Show transfer progress.")
    p_s.set_defaults(func=cmd_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
