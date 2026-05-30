from __future__ import annotations
import argparse
import json
import os
import sys
import tidalapi
from ytmusicapi import YTMusic

TIDAL_TOKEN_FILE = "tidal_token.json"
YTM_AUTH_FILE = "ytm_auth.json"


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

    login_future, _ = session.login_oauth()
    print("Open the URL above in your browser to authorise Tidal.")
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
            "  ytmusicapi oauth\n"
            f"Then rename the output to '{YTM_AUTH_FILE}'."
        )
        sys.exit(1)
    return YTMusic(YTM_AUTH_FILE)


def cmd_transfer(args: argparse.Namespace) -> None:
    from .transfer import run_transfer
    run_transfer(_tidal_login(), _ytm_login(), dry_run=args.dry_run, max_tracks=args.limit)


def cmd_review(args: argparse.Namespace) -> None:
    from .review import run_review
    run_review(_ytm_login(), dry_run=args.dry_run)


def cmd_status(_args: argparse.Namespace) -> None:
    state_path = "transfer_state.json"
    review_path = "review.json"
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
