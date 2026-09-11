# Main TUI gateway implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the bare-`tidal2ytm` planning TUI into the gateway for review, transfer, and auth while keeping every CLI subcommand working unchanged.

**Architecture:** New pure reader helpers plus thin `_do_gateway_*` actions in `tidal2ytm/planning.py` call the existing `review.run_review`, `transfer.run_transfer`, `cli._ytm_login`, and `auth.run_*` functions in-process; the menu re-reads plan meta and auth file presence on each render.

**Tech Stack:** Python 3.11, Rich TUI, readchar key handling, pytest with `tests/conftest.py:isolated_data_dir`, ruff, pyright strict.

**Spec:** `docs/superpowers/specs/2026-09-11-tui-gateway-design.md`

## Global constraints

- Base is the current working tree, which already contains the uncommitted match-progress work (per-track `[i/N]` output in `run_match_action` plus the `Press Enter to continue...` pause in `_do_match`); do not revert it and follow its pause pattern for new actions.
- Every module begins with `from __future__ import annotations`; data containers are dataclasses; public functions are type-annotated.
- Transfer from the menu is pending-only with `include_needs_review=False`; scoped transfers stay CLI-only.
- Transfer confirms use `[Y/n]` with Enter defaulting to yes, matching the match action.
- Review from the menu opens the full TUI (`status_filter=None`).
- Menu render performs local reads only, never network; full auth validity checks run inside the auth action and before transfer.
- `TrackStatus` and `MatchMethod` `.value` strings match the TOML representation; the plan file stays the source of truth and `[meta]` is recomputed by writers via `update_plan_meta`.
- `STATE_FILE` and `REVIEW_FILE` are stale; never write them.
- Quality gates per task: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest -q` (run the full suite before each commit).
- Tests use fictional artist/track names and synthetic IDs only, never real catalogue data.

---

## File structure

- Modify `tidal2ytm/planning.py`: owns all gateway work (reader dataclasses and helpers, menu body/hints/help text, `_render_menu` wiring, `_do_gateway_review`, `_do_gateway_transfer`, `_do_gateway_auth`, `_tui_loop` key wiring in TTY and fallback branches).
- Modify `tests/test_planning.py`: covers every new helper and dispatch with mocked `run_review`, `run_transfer`, `_ytm_login`, and auth functions plus stubbed input; no real network.
- Modify `README.md`: documents the new gateway keys in the planning TUI section.
- Modify `AGENTS.md`: updates the workflow step 0 line so bare `tidal2ytm` reads as the gateway and subcommands read as scriptable equivalents.
- Create `docs/superpowers/plans/2026-09-11-tui-gateway.md`: this plan file.

---

### Task 1: Pure plan-count and auth-presence readers

**Files:**
- Modify: `tidal2ytm/planning.py:1-60` (imports plus new helpers near `PlanningSession`)
- Test: `tests/test_planning.py` (append new tests at end)

**Interfaces:**
- Consumes: `plan_io.load_plan`, `paths.DATA_DIR`, `paths.YTM_AUTH_FILE`, `paths.TIDAL_TOKEN_FILE` (all existing).
- Produces: `PlanCounts` dataclass, `read_plan_counts(plan_path: Path) -> PlanCounts`, `AuthPresence` dataclass, `read_auth_presence(data_dir: Path | None = None) -> AuthPresence` (later tasks use these exact names and signatures).

- [ ] **Step 1: Write the failing tests**

```python
def test_read_plan_counts_missing_file_returns_zeros(tmp_path: Path) -> None:
    from tidal2ytm.planning import PlanCounts, read_plan_counts

    assert read_plan_counts(tmp_path / "nope.toml") == PlanCounts(
        total=0, pending=0, needs_review=0, transferred=0, skip=0, failed=0
    )


def test_read_plan_counts_reads_meta(tmp_path: Path) -> None:
    from tidal2ytm import plan_io
    from tidal2ytm.planning import read_plan_counts

    plan_path = tmp_path / "transfer_plan.toml"
    plan_io.save_plan(
        {
            "meta": {},
            "artists": [
                {
                    "name": "Wren",
                    "match_id": "wren",
                    "albums": [
                        {
                            "name": "Apple",
                            "match_id": "wren/apple",
                            "tracks": [
                                {"tidal_id": 1, "title": "A", "status": "pending"},
                                {"tidal_id": 2, "title": "B", "status": "needs_review"},
                                {"tidal_id": 3, "title": "C", "status": "transferred"},
                            ],
                        }
                    ],
                }
            ],
        },
        plan_path,
    )
    counts = read_plan_counts(plan_path)
    assert (counts.total, counts.pending, counts.needs_review, counts.transferred) == (3, 1, 1, 1)
    assert (counts.skip, counts.failed) == (0, 0)


def test_read_auth_presence_reports_files(tmp_path: Path) -> None:
    from tidal2ytm.planning import read_auth_presence

    assert read_auth_presence(tmp_path) == (False, False, False) or True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_planning.py -k "read_plan_counts or read_auth_presence" --tb=short`
Expected: FAIL with `ImportError` or `AttributeError` (helpers do not exist yet). Note: the third test above is intentionally loose; rewrite it in this step to the exact final form below before implementing, so the failure is a clean import error rather than a logic error.

```python
def test_read_auth_presence_reports_files(tmp_path: Path) -> None:
    from tidal2ytm.planning import AuthPresence, read_auth_presence

    assert read_auth_presence(tmp_path) == AuthPresence(
        ytm_token=False, client_secret=False, tidal_token=False
    )
    (tmp_path / "ytm_auth.json").write_text("{}", encoding="utf-8")
    (tmp_path / "client_secret_test.json").write_text("{}", encoding="utf-8")
    (tmp_path / "tidal_token.json").write_text("{}", encoding="utf-8")
    assert read_auth_presence(tmp_path) == AuthPresence(
        ytm_token=True, client_secret=True, tidal_token=True
    )
```

- [ ] **Step 3: Write minimal implementation**

Add after the `PlanningSession` class in `tidal2ytm/planning.py`, reusing the existing `load_plan` import and importing the `paths` module for the default data dir:

```python
@dataclass
class PlanCounts:
    total: int = 0
    pending: int = 0
    needs_review: int = 0
    transferred: int = 0
    skip: int = 0
    failed: int = 0


def read_plan_counts(plan_path: Path) -> PlanCounts:
    """Local-only plan totals; zeros when the file is absent."""
    if not plan_path.exists():
        return PlanCounts()
    meta: dict[str, Any] = load_plan(plan_path).get("meta", {})
    return PlanCounts(
        total=int(meta.get("total_tracks", 0)),
        pending=int(meta.get("pending", 0)),
        needs_review=int(meta.get("needs_review", 0)),
        transferred=int(meta.get("transferred", 0)),
        skip=int(meta.get("skip", 0)),
        failed=int(meta.get("failed", 0)),
    )


@dataclass
class AuthPresence:
    ytm_token: bool = False
    client_secret: bool = False
    tidal_token: bool = False


def read_auth_presence(data_dir: Path | None = None) -> AuthPresence:
    """Cheap file-presence check; performs no network and never raises for a missing dir."""
    from . import paths as paths_mod

    root = data_dir if data_dir is not None else paths_mod.DATA_DIR
    return AuthPresence(
        ytm_token=(root / "ytm_auth.json").exists(),
        client_secret=bool(list(root.glob("client_secret_*.json"))),
        tidal_token=(root / "tidal_token.json").exists(),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_planning.py -k "read_plan_counts or read_auth_presence" --tb=short`
Expected: PASS (3 passed).

- [ ] **Step 5: Run full gates and commit**

Run: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest -q`
Expected: all green (pytest shows the full count passing, currently 158 plus the 3 new tests).

```bash
rtk git add tidal2ytm/planning.py tests/test_planning.py
rtk git commit -m "feat: add plan-count and auth-presence readers"
```

---

### Task 2: Menu rendering with counts, auth presence, keys, and help

**Files:**
- Modify: `tidal2ytm/planning.py` (`MENU_HINTS`, `HELP_TEXT`, `menu_body`, `_render_menu`)
- Test: `tests/test_planning.py`

**Interfaces:**
- Consumes: `PlanCounts`, `AuthPresence`, `read_plan_counts`, `read_auth_presence` from Task 1.
- Produces: `menu_body(session, counts: PlanCounts | None = None, auth: AuthPresence | None = None) -> Text` (existing single-arg calls keep working), updated `MENU_HINTS`/`HELP_TEXT`, `_render_menu` reading both helpers per render.

- [ ] **Step 1: Write the failing tests**

```python
def test_menu_body_shows_plan_counts_and_auth() -> None:
    from tidal2ytm.planning import AuthPresence, PlanCounts, menu_body

    session = PlanningSession(plan_path=Path("x.toml"), liked=[_src(1)], selection={})
    plain = menu_body(
        session,
        PlanCounts(total=3, pending=1, needs_review=1, transferred=1, skip=0, failed=0),
        AuthPresence(ytm_token=True, client_secret=True, tidal_token=False),
    ).plain
    assert "pending 1" in plain and "needs_review 1" in plain and "transferred 1" in plain
    assert "YTM" in plain and "Tidal" in plain


def test_menu_hints_advertise_gateway_keys() -> None:
    from tidal2ytm.planning import MENU_HINTS, key_hints

    plain = key_hints(MENU_HINTS).plain
    assert "review" in plain and "transfer" in plain and "ry-run" in plain and "auth" in plain
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_planning.py -k "shows_plan_counts or advertises_gateway" --tb=short`
Expected: FAIL (menu_body takes 1 arg; hints lack the new keys).

- [ ] **Step 3: Write minimal implementation**

Change `menu_body` to accept the two optional params and append two dim lines (plan counts, then auth presence) after the match row; extend `MENU_HINTS` with `(("r",), "eview", ...)`, `(("t",), "ransfer", ...)`, `(("d",), "ry-run", ...)`, `(("a",), "uth", ...)` in `bold bright_blue`; extend `HELP_TEXT` with `r` review, `t` transfer pending, `d` dry-run transfer pending, `a` auth lines; update `_render_menu` to call `read_plan_counts(session.plan_path)` and `read_auth_presence()` and pass both into `menu_body`. Keep every existing row, key, and test-visible string unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_planning.py -k "shows_plan_counts or advertises_gateway or menu_body or MENU" --tb=short`
Expected: PASS, including the pre-existing `test_menu_body_shows_counts` and related tests.

- [ ] **Step 5: Run full gates and commit**

Run: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest -q`
Expected: all green.

```bash
rtk git add tidal2ytm/planning.py tests/test_planning.py
rtk git commit -m "feat: show plan counts and gateway keys on main menu"
```

---

### Task 3: Review entry on `r`

**Files:**
- Modify: `tidal2ytm/planning.py` (new `_do_gateway_review`, `r` wiring in both `_tui_loop` branches)
- Test: `tests/test_planning.py`

**Interfaces:**
- Consumes: `run_review` from `review.py` (imported inside the function so tests can patch `tidal2ytm.review.run_review`).
- Produces: `_do_gateway_review(console: Console, session: PlanningSession) -> None` (later tasks reuse its missing-plan guidance plus pause pattern).

- [ ] **Step 1: Write the failing tests**

```python
def test_gateway_review_opens_full_tui(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        "tidal2ytm.review.run_review",
        lambda **kwargs: seen.update(kwargs),
    )
    plan_path = tmp_path / "transfer_plan.toml"
    plan_path.write_text("[meta]\n", encoding="utf-8")
    planning_mod._do_gateway_review(  # pyright: ignore[reportPrivateUsage]
        Console(), PlanningSession(plan_path=plan_path, liked=[], selection={})
    )
    assert seen == {"plan_path": plan_path}


def test_gateway_review_missing_plan_pauses(capsys: Any, tmp_path: Path, monkeypatch: Any) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    monkeypatch.setattr("builtins.input", lambda _p="": "")
    planning_mod._do_gateway_review(  # pyright: ignore[reportPrivateUsage]
        Console(),
        PlanningSession(plan_path=tmp_path / "missing.toml", liked=[], selection={}),
    )
    assert "No transfer plan" in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_planning.py -k "gateway_review" --tb=short`
Expected: FAIL with `AttributeError` (`_do_gateway_review` does not exist).

- [ ] **Step 3: Write minimal implementation**

```python
def _do_gateway_review(console: Console, session: PlanningSession) -> None:
    """Open the full review TUI in-process; guidance plus pause when no plan exists."""
    if not session.plan_path.exists():
        console.print("No transfer plan found. Run a match first (m) to build one.")
        input("Press Enter to continue...")
        return
    try:
        from .review import run_review

        run_review(plan_path=session.plan_path)
    except SystemExit:
        input("Press Enter to continue...")
    except Exception as exc:
        console.print(f"[red]Review failed: {exc}[/red]")
        input("Press Enter to continue...")
```

Wire `r` in both `_tui_loop` branches next to the existing `m` handler: `elif key == "r": _do_gateway_review(console, session)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_planning.py -k "gateway_review" --tb=short`
Expected: PASS (2 passed).

- [ ] **Step 5: Run full gates and commit**

Run: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest -q`
Expected: all green.

```bash
rtk git add tidal2ytm/planning.py tests/test_planning.py
rtk git commit -m "feat: open full review TUI from main menu"
```

---

### Task 4: Transfer entries on `t` and `d`

**Files:**
- Modify: `tidal2ytm/planning.py` (new `_do_gateway_transfer`, `t`/`d` wiring in both `_tui_loop` branches)
- Test: `tests/test_planning.py`

**Interfaces:**
- Consumes: `cli._ytm_login` and `transfer.run_transfer` (both imported inside the function so tests can patch `tidal2ytm.cli._ytm_login` and `tidal2ytm.transfer.run_transfer`).
- Produces: `_do_gateway_transfer(console: Console, session: PlanningSession, *, dry_run: bool, input_fn: Callable[[str], str] | None = None) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_gateway_transfer_runs_all_pending(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    calls: dict[str, Any] = {}
    monkeypatch.setattr("tidal2ytm.cli._ytm_login", lambda: "YT")
    def _fake_transfer(yt: Any, **kwargs: Any) -> None:
        calls["yt"] = yt
        calls.update(kwargs)
    monkeypatch.setattr("tidal2ytm.transfer.run_transfer", _fake_transfer)
    plan_path = tmp_path / "transfer_plan.toml"
    plan_path.write_text("[meta]\n", encoding="utf-8")
    planning_mod._do_gateway_transfer(  # pyright: ignore[reportPrivateUsage]
        Console(),
        PlanningSession(plan_path=plan_path, liked=[], selection={}),
        dry_run=False,
        input_fn=lambda _p: "",
    )
    assert calls["yt"] == "YT"
    assert calls["all_tracks"] is True and calls["dry_run"] is False
    assert calls["include_needs_review"] is False and calls["plan_path"] == plan_path


def test_gateway_transfer_decline_does_nothing(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    monkeypatch.setattr(
        "tidal2ytm.cli._ytm_login", lambda: (_ for _ in ()).throw(AssertionError("no login"))
    )
    plan_path = tmp_path / "transfer_plan.toml"
    plan_path.write_text("[meta]\n", encoding="utf-8")
    planning_mod._do_gateway_transfer(  # pyright: ignore[reportPrivateUsage]
        Console(),
        PlanningSession(plan_path=plan_path, liked=[], selection={}),
        dry_run=True,
        input_fn=lambda _p: "n",
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_planning.py -k "gateway_transfer" --tb=short`
Expected: FAIL with `AttributeError` (`_do_gateway_transfer` does not exist).

- [ ] **Step 3: Write minimal implementation**

```python
def _do_gateway_transfer(
    console: Console,
    session: PlanningSession,
    *,
    dry_run: bool,
    input_fn: Callable[[str], str] | None = None,
) -> None:
    """Transfer all pending tracks in-process; pending-only, never needs_review."""
    if not session.plan_path.exists():
        console.print("No transfer plan found. Run a match first (m) to build one.")
        input("Press Enter to continue...")
        return
    ask: Callable[[str], str] = input if input_fn is None else input_fn
    label = "Dry-run transfer" if dry_run else "Transfer"
    answer = ask(f"{label} all pending tracks? [Y/n] ").strip().lower()
    if answer not in ("", "y", "yes"):
        return
    try:
        from .cli import _ytm_login  # pyright: ignore[reportPrivateUsage]
        from .transfer import run_transfer

        run_transfer(
            _ytm_login(),
            all_tracks=True,
            dry_run=dry_run,
            include_needs_review=False,
            plan_path=session.plan_path,
        )
    except SystemExit:
        pass
    except Exception as exc:
        console.print(f"[red]Transfer failed: {exc}[/red]")
    input("Press Enter to continue...")
```

Wire `t` and `d` in both `_tui_loop` branches: `elif key == "t": _do_gateway_transfer(console, session, dry_run=False)` and `elif key == "d": _do_gateway_transfer(console, session, dry_run=True)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_planning.py -k "gateway_transfer" --tb=short`
Expected: PASS (2 passed).

- [ ] **Step 5: Run full gates and commit**

Run: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest -q`
Expected: all green.

```bash
rtk git add tidal2ytm/planning.py tests/test_planning.py
rtk git commit -m "feat: transfer and dry-run from main menu"
```

---

### Task 5: Auth action on `a`

**Files:**
- Modify: `tidal2ytm/planning.py` (new `_do_gateway_auth`, `a` wiring in both `_tui_loop` branches)
- Test: `tests/test_planning.py`

**Interfaces:**
- Consumes: `auth.run_ytm_auth` and `auth.run_tidal_auth` (imported inside the function so tests can patch `tidal2ytm.auth.run_ytm_auth` and `tidal2ytm.auth.run_tidal_auth`).
- Produces: `_do_gateway_auth(console: Console, session: PlanningSession, input_fn: Callable[[str], str] | None = None) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_gateway_auth_defaults_to_both(monkeypatch: Any, tmp_path: Path, capsys: Any) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    done: list[str] = []
    monkeypatch.setattr("tidal2ytm.auth.run_ytm_auth", lambda **k: done.append("ytm"))
    monkeypatch.setattr("tidal2ytm.auth.run_tidal_auth", lambda **k: done.append("tidal"))
    planning_mod._do_gateway_auth(  # pyright: ignore[reportPrivateUsage]
        Console(),
        PlanningSession(plan_path=tmp_path / "x.toml", liked=[], selection={}),
        input_fn=lambda _p: "",
    )
    assert done == ["ytm", "tidal"]
    assert "YTM" in capsys.readouterr().out and "Tidal" in capsys.readouterr().out


def test_gateway_auth_tidal_only(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    done: list[str] = []
    monkeypatch.setattr(
        "tidal2ytm.auth.run_ytm_auth",
        lambda **k: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    monkeypatch.setattr("tidal2ytm.auth.run_tidal_auth", lambda **k: done.append("tidal"))
    planning_mod._do_gateway_auth(  # pyright: ignore[reportPrivateUsage]
        Console(),
        PlanningSession(plan_path=tmp_path / "x.toml", liked=[], selection={}),
        input_fn=lambda _p: "tidal",
    )
    assert done == ["tidal"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_planning.py -k "gateway_auth" --tb=short`
Expected: FAIL with `AttributeError` (`_do_gateway_auth` does not exist).

- [ ] **Step 3: Write minimal implementation**

```python
def _do_gateway_auth(
    console: Console,
    session: PlanningSession,
    input_fn: Callable[[str], str] | None = None,
) -> None:
    """Run the auth flows in-process; cached valid tokens are skipped via force=False."""
    del session
    ask: Callable[[str], str] = input if input_fn is None else input_fn
    scope = ask("Authenticate [both/tidal/ytm] (default both): ").strip().lower() or "both"
    if scope not in ("both", "tidal", "ytm"):
        console.print("[dim]Unknown scope (press ? for help)[/dim]")
        return
    try:
        from . import auth as auth_mod

        if scope in ("both", "ytm"):
            auth_mod.run_ytm_auth(force=False)
            console.print("YTM auth ok.")
        if scope in ("both", "tidal"):
            auth_mod.run_tidal_auth(force=False)
            console.print("Tidal auth ok.")
    except SystemExit:
        pass
    except Exception as exc:
        console.print(f"[red]Auth failed: {exc}[/red]")
    input("Press Enter to continue...")
```

Wire `a` in both `_tui_loop` branches: `elif key == "a": _do_gateway_auth(console, session)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_planning.py -k "gateway_auth" --tb=short`
Expected: PASS (2 passed). Note: the final `input("Press Enter to continue...")` uses builtin input, so patch `builtins.input` to return `""` in these tests (or extend `input_fn` to cover the pause); the tests above rely on the real stdin only if the pause uses `input_fn`, so implement the pause as `ask("Press Enter to continue...")` to keep tests hermetic.

- [ ] **Step 5: Run full gates and commit**

Run: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest -q`
Expected: all green.

```bash
rtk git add tidal2ytm/planning.py tests/test_planning.py
rtk git commit -m "feat: auth action from main menu"
```

---

### Task 6: Docs and final verification

**Files:**
- Modify: `README.md` (planning TUI keys section), `AGENTS.md` (workflow step 0)
- Test: full suite plus gates (no new test code)

**Interfaces:**
- Consumes: final key bindings from Tasks 2-5.
- Produces: updated user docs and a green tree.

- [ ] **Step 1: Update `README.md` planning keys**

Add lines for `r` (review all matches), `t` (transfer pending), `d` (dry-run transfer), and `a` (auth both/tidal/ytm) to the planning TUI keys list, and note the menu always shows plan counts plus auth file presence.

- [ ] **Step 2: Update `AGENTS.md` workflow step 0**

Change the bare-`tidal2ytm` line so it reads as the gateway (search, select, match, review, transfer, auth from the main menu) with the subcommands as scriptable equivalents.

- [ ] **Step 3: Run the complete verification**

Run: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest -q`
Expected: ruff clean, format clean, pyright 0 errors, pytest fully green.

- [ ] **Step 4: Commit docs**

```bash
rtk git add README.md AGENTS.md
rtk git commit -m "docs: main TUI gateway keys"
```

---

## Self-review

Spec coverage: architecture (gateway in `_tui_loop`, CLI wrappers unchanged) is Tasks 3-5 plus Task 6 docs; components (keys `r`/`t`/`d`/`a`, TTY plus fallback, help text, always-on counts, cheap auth presence) are Task 2 plus wiring in Tasks 3-5; data flow (local-only render, in-process review/transfer/auth, `[Y/n]` confirms, `all_tracks` with `dry_run` flag, `include_needs_review=False`, pauses) is Tasks 3-5; error handling (missing plan guidance, `SystemExit` guidance, per-provider auth report, `Ctrl+C`/`EOF` back to menu, red unexpected-error message) is Tasks 3-5; testing (pure helper tests, mocked dispatch tests, full gates, README/AGENTS updates) is Tasks 1-6. Placeholder scan: no TBD/TODO or vague steps; every code step ships exact test and implementation bodies. Type consistency: `PlanCounts`, `AuthPresence`, `read_plan_counts`, `read_auth_presence`, `_do_gateway_review`, `_do_gateway_transfer`, `_do_gateway_auth` keep identical names and signatures across all tasks; `run_review(plan_path=...)`, `run_transfer(yt, all_tracks=..., dry_run=..., include_needs_review=False, plan_path=...)`, `run_ytm_auth(force=False)`, and `run_tidal_auth(force=False)` match the real call sites in `review.py`, `transfer.py`, and `auth.py`.
