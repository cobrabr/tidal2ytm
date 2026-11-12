# Desktop GUI design system implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the desktop GUI its hue-205 dark identity, restyle the shell, Home and Diagnostics to the approved mock layout, and let Diagnostics report where the data and the logs actually are.

**Architecture:** One token layer in `src/styles/app.css` holds every colour as a CSS custom property; shadcn-svelte's variable names alias that layer and Tailwind v4's `@theme inline` maps both onto utility classes, so the user retunes one file. shadcn-svelte vendors the accessible controls (button, badge, card, select, spinner, label) under `src/lib/components/ui/`; five components of our own carry the app's own visuals (panel, state pill, segment bar, count tiles, provider rows). The host gains two read-only commands so Diagnostics can name the app version, the database file and the log files instead of leaving the user to guess.

**Tech Stack:** Svelte 5 (runes), Tailwind CSS v4, shadcn-svelte on bits-ui, Vitest with @testing-library/svelte, Tauri 2 (Rust), rusqlite.

**Spec:** `docs/superpowers/specs/2026-10-30-desktop-gui-design-system.md` (the design system, which this plan implements) and `docs/superpowers/specs/2026-09-29-desktop-gui-migration-design.md` (the master migration design; lines 239–255 cover the screens).

**Branch:** `feat/desktop-gui-migration`. Four commits are already on it and unpushed (`5267809`, `20fd809`, `cb11d23`, `2b47dc6`); this plan's commits sit on top of them. Nothing is pushed without the user's explicit approval.

## Global Constraints

- Dark mode only. No light theme and no theme switcher.
- Every surface colour back-converts to exactly hue 205, near-black surfaces within ±2° at 8-bit precision. The values in the design document are the user's to retune by hand; the names and the structure are this plan's.
- Colour never carries meaning alone: every state carries a text label, and a shape where it matters.
- Monospace only for numerals where alignment matters and for technical strings (paths, sizes, versions, latencies). Timestamps and prose stay sans. `tabular-nums` on every mono numeral.
- Sentence case for buttons and headings. Uppercase is reserved for panel labels.
- The fonts are the generic keywords `sans-serif` and `monospace`. No webfonts. Form controls inherit the sans stack.
- shadcn's component defaults ship as they are: border radius, font sizes, widths, animation lengths and dropdown spacing are not fought in this pass.
- One primary action per screen. Focus is a visible accent ring and is never removed.
- `prefers-reduced-motion` is honoured for every animation, including shadcn's.
- No test asserts styling: no hue, font, colour, radius or copy assertion. Tests assert structure, roles, accessible names and outcomes.
- The Rust host, the Python package and `contracts/*.json` are unchanged apart from the two commands this plan adds.
- Every dependency pin in `package.json` is exact. A `^` or `~` in the diff is a defect.
- Gates before every commit: `pnpm format` → `pnpm lint:fix` → `pnpm lint` → `pnpm format:check` → `pnpm check` → `pnpm test` → `pnpm build`; `cargo fmt --all --check`; `cargo clippy -p tidal2ytm --all-targets -- -D warnings`; `cargo test -p tidal2ytm`; `uv run ruff check .`; `uv run ruff format --check .`; `uv run pyright`; `uv run pytest -q`.
- The untracked `docs/tidal2ytm-desktop-ui-migration-preliminary-plan.md` is read-only and stays untracked.

## Review Focus

Five failure modes the spec implies that no existing test exercises. Each line gets a test in the task named.

1. The database path the Diagnostics screen shows is not the file the host opened, so the user cannot find their data where the screen says it is — Task 2.
2. A log file that does not exist yet empties the Logs panel, so the one screen built to explain a failure cannot be read — Task 2 (host) and Task 8 (screen).
3. The library count in the Home heading disagrees with the sum of the count tiles, so one screen shows two truths — Task 7.
4. A status this build does not know is dropped from the plan summary, so the total disagrees with the database — Task 4.
5. A sync run with no total renders a determinate bar that lies about how much is left — Task 6.

---

### Task 1: Frontend toolchain — Tailwind v4, shadcn-svelte, path aliases

**Files:**
- Modify: `package.json` (dev dependencies)
- Modify: `vite.config.ts` (Tailwind plugin, `$lib` alias)
- Modify: `tsconfig.json` (`baseUrl`, `paths`)
- Create: `components.json`
- Create: `src/lib/utils.ts`
- Create: `src/lib/components/ui/**` (vendored by the shadcn-svelte CLI)

**Interfaces:**
- Consumes: nothing.
- Produces: `cn(...inputs: ClassValue[]): string` in `src/lib/utils.ts`; shadcn components at `src/lib/components/ui/<name>/index.ts`; `$lib` resolvable by both Vite and TypeScript.

- [ ] **Step 1: Install Tailwind v4 exactly**

Run: `pnpm info tailwindcss version` and note the release, then `pnpm add -D --save-exact tailwindcss@<release> @tailwindcss/vite@<release>`. The two packages ship in lockstep and must be the same version.

- [ ] **Step 2: Initialise shadcn-svelte**

Run: `pnpm dlx shadcn-svelte@latest init`. Answer: CSS path `src/styles/app.css`, TypeScript yes, base colour any (our token layer replaces the theme entirely), alias `$lib` → `src/lib`.

The CLI writes its own theme into `src/styles/app.css` and may touch `tsconfig.json` and `vite.config.ts`. Task 3 replaces that CSS wholesale; do not merge it.

- [ ] **Step 3: Add the components this pass uses**

Run: `pnpm dlx shadcn-svelte@latest add button badge card select spinner label`.

Deliberately not added: `progress` (the job bar stays a native `<progress>` element, which is a platform control with built-in semantics), `dialog`, `input`, `table`, `dropdown-menu` (Phase 4b adds them with the screens that need them). `separator` is not added either: a hairline divider is a `border-t` on the row that needs it, not a component.

- [ ] **Step 4: Fix `components.json` to the Svelte 5 alias set**

Set `aliases` to `components: "$lib/components"`, `utils: "$lib/utils"`, `ui: "$lib/components/ui"`, `hooks: "$lib/hooks"`, `lib: "$lib"`, and add `"registry": "https://shadcn-svelte.com/registry"` at the root. `tailwind.css` stays `src/styles/app.css`.

- [ ] **Step 5: Resolve `$lib` in TypeScript and Vite**

In `tsconfig.json` add `"baseUrl": "."` and `"paths": { "$lib": ["./src/lib"], "$lib/*": ["./src/lib/*"] }` to `compilerOptions`.

In `vite.config.ts` add `resolve.alias` mapping `$lib` to the absolute path of `src/lib` (`fileURLToPath(new URL("./src/lib", import.meta.url))`), and register the Tailwind plugin from `@tailwindcss/vite` in `plugins` ahead of `svelte()`.

- [ ] **Step 6: Write `src/lib/utils.ts`**

```ts
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
	return twMerge(clsx(inputs));
}
```

`clsx` and `tailwind-merge` are installed by the CLI; if it did not add them, `pnpm add --save-exact clsx tailwind-merge`.

- [ ] **Step 7: Pin every dependency the CLI touched**

Run: `git diff package.json`. Any `^` or `~` is a defect: `pnpm add --save-exact <package>@<version>` for each. The CLI writes caret ranges by default.

- [ ] **Step 8: Verify**

Run: `pnpm check`, `pnpm test`, `pnpm build`.

Expected: all three pass. `pnpm test` still reports the same 32 tests as before this plan — a change in the count means a vendored component shipped its own test file that does not belong here.

- [ ] **Step 9: Commit**

```bash
git add package.json pnpm-lock.yaml vite.config.ts tsconfig.json components.json src/lib/utils.ts src/lib/components/ui
git commit -m "build(ui): add tailwind v4 and shadcn-svelte to the frontend"
```

---

### Task 2: The host reports its version, its database file and its log files

**Files:**
- Modify: `src-tauri/src/logging.rs` (a file listing that names what exists)
- Modify: `src-tauri/src/state.rs` (`database_path`)
- Modify: `src-tauri/src/lib.rs` (the database file name moves here; commands registered)
- Modify: `src-tauri/src/commands.rs` (two new commands; `get_log_paths` retired)
- Create: `src/lib/format.ts`
- Modify: `src/lib/types.ts` (`AppInfo`, `LogFile`)
- Modify: `src/lib/ipc.ts` (`getAppInfo`, `getLogFiles`; `getLogPaths` retired)
- Modify: `src/lib/ui-log.ts` (the frontend's own log file)
- Test: `src/lib/ipc.test.ts`, `src/lib/format.test.ts`, `src/lib/ui-log.test.ts`

**Interfaces:**
- Consumes: nothing from Task 1 except the working toolchain.
- Produces:
  - Rust: `logging::LogFile { path: PathBuf, bytes: u64, is_dir: bool }`, `logging::files_in(dir: &Path) -> Vec<LogFile>`, `logging::files() -> Result<Vec<LogFile>, String>`, `state::DATABASE_FILE: &str`, `AppState::database_path(&self) -> PathBuf`.
  - Tauri commands: `get_app_info() -> AppInfo { version: String, database_path: String }`, `get_log_files() -> Vec<LogFile>`.
  - TypeScript: `AppInfo`, `LogFile` in `src/lib/types.ts`; `getAppInfo(): Promise<AppInfo>`; `getLogFiles(): Promise<LogFile[]>`; `formatBytes(bytes: number): string`; `uiLogFile(): Promise<{ path: string; bytes: number }>`.

- [ ] **Step 1: Write the failing Rust test for the file listing**

In `src-tauri/src/logging.rs`, add to the `tests` module:

```rust
/// A file that does not exist yet is still listed: the panel that shows the
/// logs must not lose a row because the first run has not written one.
#[test]
fn a_file_that_does_not_exist_is_listed_without_a_size() {
    let dir = std::env::temp_dir().join(format!("tidal2ytm-log-files-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("dir");

    let files = files_in(&dir);

    let host = files.iter().find(|f| f.path == dir.join("host.log")).expect("host log listed");
    assert_eq!(host.bytes, 0, "a log file that does not exist has no size to report");
    assert!(!host.is_dir);
    assert!(files.iter().any(|f| f.path == dir && f.is_dir), "the log directory is listed");
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cargo test -p tidal2ytm logging::tests::a_file_that_does_not_exist_is_listed_without_a_size`

Expected: FAIL — `files_in` does not exist.

- [ ] **Step 3: Implement the listing**

In `src-tauri/src/logging.rs`, replace `LogPaths` and `paths()` with:

```rust
/// One file the diagnostics screen can open, with the size it has now.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct LogFile {
    pub path: PathBuf,
    pub bytes: u64,
    pub is_dir: bool,
}

/// The log directory and the host log inside it.
///
/// A free function taking the directory, so it can be tested without touching
/// the global sink state.
pub fn files_in(dir: &Path) -> Vec<LogFile> {
    let host_log = dir.join("host.log");
    let bytes = std::fs::metadata(&host_log).map(|m| m.len()).unwrap_or(0);
    vec![
        LogFile { path: host_log, bytes, is_dir: false },
        LogFile { path: dir.to_path_buf(), bytes: 0, is_dir: true },
    ]
}

/// The same listing for the directory the installed sink writes to.
pub fn files() -> Result<Vec<LogFile>, String> {
    let state = STATE.lock().map_err(|e| e.to_string())?;
    let dir = state.dir.clone().ok_or_else(|| "logging not installed".to_string())?;
    Ok(files_in(&dir))
}
```

The `frontend_log` field goes away: nothing ever wrote that file. The frontend's own log is reported by the frontend, which is what owns it.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cargo test -p tidal2ytm logging::tests`

Expected: PASS.

- [ ] **Step 5: Write the failing Rust test for the database path**

In `src-tauri/src/state.rs`'s test module, add a test that builds a state whose `cwd` is a scratch directory, opens the store at `cwd.join(DATABASE_FILE)`, and asserts `state.database_path()` is that same file and that the file exists:

```rust
/// The path the host reports is the file it opened the database at.
#[tokio::test]
async fn the_database_path_is_the_file_the_store_was_opened_at() {
    let dir = scratch_dir("database-path");
    let path = dir.join(DATABASE_FILE);
    Store::open(&path).expect("open the database the host will report");
    let state = started_over_with_cwd(Store::open(&path).expect("open"), dir.clone());

    assert_eq!(state.database_path(), path);
    assert!(state.database_path().is_file());

    let _ = std::fs::remove_dir_all(&dir);
}
```

`started_over_with_cwd` is a small helper next to `started_over` in the same module: it builds a `SidecarConfig` whose `cwd` is the given directory (the existing `config()` helper at the bottom of the file is the pattern to copy) and calls `AppState::start`.

- [ ] **Step 6: Run the test to verify it fails**

Run: `cargo test -p tidal2ytm state::tests::the_database_path_is_the_file_the_store_was_opened_at`

Expected: FAIL — `database_path` does not exist.

- [ ] **Step 7: Implement the database path**

In `src-tauri/src/state.rs`, add:

```rust
/// The database's file name inside the directory the app runs in.
pub const DATABASE_FILE: &str = "tidal2ytm.db";
```

and, on `AppState`:

```rust
/// The database file the host opened.
///
/// The same `cwd` the sidecar was handed, so the path reported here is the
/// file the rows are actually in.
#[must_use]
pub fn database_path(&self) -> std::path::PathBuf {
    self.config.cwd.join(DATABASE_FILE)
}
```

In `src-tauri/src/lib.rs`, delete the private `DATABASE_FILE` constant and use `state::DATABASE_FILE` in `open_database` and in its own test.

- [ ] **Step 8: Run the test to verify it passes**

Run: `cargo test -p tidal2ytm`

Expected: PASS, including the two new tests and the existing `opening_the_database_leaves_it_migrated`.

- [ ] **Step 9: Add the two commands**

In `src-tauri/src/commands.rs`:

```rust
/// What the app is and where its data lives.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct AppInfo {
    pub version: String,
    pub database_path: PathBuf,
}

/// The version this build is, and the database file it opened.
#[tauri::command]
pub fn get_app_info(state: State<'_, AppState>) -> Result<AppInfo, String> {
    Ok(AppInfo {
        version: env!("CARGO_PKG_VERSION").to_string(),
        database_path: state.database_path(),
    })
}

/// The log files the diagnostics screen can open.
#[tauri::command]
pub fn get_log_files() -> Result<Vec<LogFile>, String> {
    logging::files()
}
```

In `src-tauri/src/lib.rs`, replace `commands::get_log_paths` with `commands::get_app_info` and `commands::get_log_files` in the `generate_handler!` list.

- [ ] **Step 10: Write the failing TypeScript test**

In `src/lib/ipc.test.ts`, add:

```ts
describe("getAppInfo", () => {
	it("returns the host's payload without reshaping it", async () => {
		const payload = { version: "0.7.0", database_path: "/data/tidal2ytm.db" };
		call.mockResolvedValue(payload);
		expect(await getAppInfo()).toBe(payload);
	});

	it("turns a failure into an Error carrying the host's message", async () => {
		call.mockRejectedValue("the store is locked");
		await expect(getAppInfo()).rejects.toBeInstanceOf(Error);
		await expect(getAppInfo()).rejects.toThrowError("the store is locked");
	});
});

describe("getLogFiles", () => {
	it("returns the host's payload without reshaping it", async () => {
		const payload = [{ path: "/logs/host.log", bytes: 42, is_dir: false }];
		call.mockResolvedValue(payload);
		expect(await getLogFiles()).toBe(payload);
	});
});
```

- [ ] **Step 11: Run the test to verify it fails**

Run: `pnpm vitest run src/lib/ipc.test.ts`

Expected: FAIL — `getAppInfo` is not exported.

- [ ] **Step 12: Implement the TypeScript boundary**

In `src/lib/types.ts`:

```ts
/** What the host is and where its data lives. */
export type AppInfo = { version: string; database_path: string };

/** One log file the diagnostics screen can open. */
export type LogFile = { path: string; bytes: number; is_dir: boolean };
```

In `src/lib/ipc.ts`, add `getAppInfo` and `getLogFiles` following the shape of the existing wrappers (invoke, then normalise a rejection into an `Error`), and delete `getLogPaths` and its `LogPaths` type.

- [ ] **Step 13: Run the test to verify it passes**

Run: `pnpm vitest run src/lib/ipc.test.ts`

Expected: PASS.

- [ ] **Step 14: Report the frontend's own log file**

In `src/lib/ui-log.ts`, add:

```ts
/** The frontend's own log file and its size, for the diagnostics screen. */
export async function uiLogFile(): Promise<{ path: string; bytes: number }> {
	const dir = await appDataDir();
	const path = `${dir}/logs/ui.log`;
	try {
		const info = await stat(LOG_PATH, { baseDir: BaseDirectory.AppData });
		return { path, bytes: info.size };
	} catch {
		return { path, bytes: 0 };
	}
}
```

with `appDataDir` imported from `@tauri-apps/api/path`. A file that does not exist yet reports zero bytes rather than failing: the panel must keep its row.

- [ ] **Step 15: Test the frontend's log file**

In `src/lib/ui-log.test.ts`, add a case where `stat` resolves with `{ size: 2048 }` and one where it rejects: the first reports 2048 bytes, the second reports 0 and does not throw. Mock `@tauri-apps/api/path`'s `appDataDir` to resolve `/appdata`.

- [ ] **Step 16: Write `src/lib/format.ts` and its test**

```ts
/** A byte count as a person reads it. */
export function formatBytes(bytes: number): string {
	if (bytes < 1024) return `${bytes} B`;
	if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
	return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
```

In `src/lib/format.test.ts`: `0` → `"0 B"`, `1024` → `"1.0 KB"`, `1024 * 1024` → `"1.0 MB"`.

- [ ] **Step 17: Verify and commit**

Run: `pnpm check`, `pnpm test`, `pnpm build`, `cargo fmt --all --check`, `cargo clippy -p tidal2ytm --all-targets -- -D warnings`, `cargo test -p tidal2ytm`.

Expected: all pass.

```bash
git add src-tauri/src src/lib
git commit -m "feat(diagnostics): report the app version, the database file and the log files"
```

---

### Task 3: The token layer

**Files:**
- Modify: `src/styles/app.css` (replaced wholesale)
- Modify: `index.html` (the dark scope)

**Interfaces:**
- Consumes: the toolchain from Task 1.
- Produces: the CSS custom properties below, available to Tailwind as `bg-*`, `text-*`, `border-*` and `shadow-*` utilities.

- [ ] **Step 1: Write the token layer**

Replace `src/styles/app.css` with this structure. The values are the design document's table; the names are this plan's, chosen so that none of them collides with a shadcn variable of a different meaning.

```css
@import "tailwindcss";
@import "tw-animate-css";

@custom-variant dark (&:is(.dark *));

:root {
	/* The identity: one hue, every surface. */
	--surface-shell: #050c11;
	--surface-content: #060d12;
	--surface-panel: #0b1923;
	--surface-subtle: #0e1c26;
	--line: #143044;

	--text-primary: #d6e4e8;
	--text-muted: #84929c;
	--text-faint: #3c5160;
	--text-pending: #b8c6d0;
	--fill-pending: #586e7e;

	--brand: #4298d4;
	--brand-strong: #1e5880;
	--brand-text: #dcebf5;

	--state-ok: #5fb98d;
	--state-warn: #d9a84e;
	--state-bad: #d96a5a;
	--state-bad-surface: #3a1d18;
	--state-bad-text: #e89a8b;

	/* shadcn's names, valued from ours. */
	--background: var(--surface-shell);
	--foreground: var(--text-primary);
	--card: var(--surface-panel);
	--card-foreground: var(--text-primary);
	--popover: var(--surface-panel);
	--popover-foreground: var(--text-primary);
	--primary: var(--brand-strong);
	--primary-foreground: var(--brand-text);
	--secondary: var(--surface-subtle);
	--secondary-foreground: var(--text-primary);
	--muted: var(--surface-subtle);
	--muted-foreground: var(--text-muted);
	--accent: var(--surface-subtle);
	--accent-foreground: var(--text-primary);
	--destructive: var(--state-bad-surface);
	--destructive-foreground: var(--state-bad-text);
	--border: var(--line);
	--input: var(--line);
	--ring: var(--brand);
}

@theme inline {
	--color-background: var(--background);
	--color-foreground: var(--foreground);
	--color-card: var(--card);
	--color-card-foreground: var(--card-foreground);
	--color-popover: var(--popover);
	--color-popover-foreground: var(--popover-foreground);
	--color-primary: var(--primary);
	--color-primary-foreground: var(--primary-foreground);
	--color-secondary: var(--secondary);
	--color-secondary-foreground: var(--secondary-foreground);
	--color-muted: var(--muted);
	--color-muted-foreground: var(--muted-foreground);
	--color-accent: var(--accent);
	--color-accent-foreground: var(--accent-foreground);
	--color-destructive: var(--destructive);
	--color-destructive-foreground: var(--destructive-foreground);
	--color-border: var(--border);
	--color-input: var(--input);
	--color-ring: var(--ring);

	--color-surface-shell: var(--surface-shell);
	--color-surface-content: var(--surface-content);
	--color-surface-panel: var(--surface-panel);
	--color-surface-subtle: var(--surface-subtle);
	--color-line: var(--line);
	--color-text-primary: var(--text-primary);
	--color-text-muted: var(--text-muted);
	--color-text-faint: var(--text-faint);
	--color-text-pending: var(--text-pending);
	--color-fill-pending: var(--fill-pending);
	--color-brand: var(--brand);
	--color-brand-strong: var(--brand-strong);
	--color-brand-text: var(--brand-text);
	--color-state-ok: var(--state-ok);
	--color-state-warn: var(--state-warn);
	--color-state-bad: var(--state-bad);

	--font-sans: sans-serif;
	--font-mono: monospace;
	--default-font-family: var(--font-sans);
}

body {
	background: var(--surface-shell);
	color: var(--text-primary);
}

@media (prefers-reduced-motion: reduce) {
	*,
	::before,
	::after {
		animation-duration: 0.01ms !important;
		animation-iteration-count: 1 !important;
		transition-duration: 0.01ms !important;
	}
}
```

`--radius` is left at shadcn's default. The font families are the generic keywords: the user asked for `sans-serif` and `monospace` and nothing else.

- [ ] **Step 2: Apply the dark scope**

In `index.html`, set `<html lang="en" class="dark">`. The app is dark only; the scope is unconditional, and `:root` carries the same values, so nothing depends on the class being absent.

- [ ] **Step 3: Verify**

Run: `pnpm build`, then `Select-String -Path "dist/assets/*.css" -Pattern "--surface-shell|--brand-strong|--font-mono"` and expect a match for each.

Expected: the built CSS carries the custom properties, which proves Tailwind processed the file rather than passing the `@theme` block through untouched.

- [ ] **Step 4: Commit**

```bash
git add src/styles/app.css index.html
git commit -m "feat(ui): the hue-205 token layer"
```

---

### Task 4: The app's own components

**Files:**
- Create: `src/lib/plan-status.ts`
- Create: `src/lib/provider-state.ts`
- Create: `src/lib/components/Panel.svelte`
- Create: `src/lib/components/StatePill.svelte`
- Create: `src/lib/components/SegmentBar.svelte`
- Create: `src/lib/components/CountTiles.svelte`
- Modify: `src/lib/components/ui/badge/index.svelte` (two variants)
- Test: `src/lib/components/SegmentBar.test.ts`, `src/lib/components/CountTiles.test.ts`

**Interfaces:**
- Consumes: the token layer from Task 3.
- Produces:
  - `plan-status.ts`: `PLAN_STATUS_ORDER`, `PLAN_STATUS_LABELS: Record<string, string>`, `planSegments(counts: Record<string, number>): PlanSegment[]`, `planTotal(counts: Record<string, number>): number`, where `PlanSegment = { key: string; label: string; count: number; fill: string; text: string }`.
  - `provider-state.ts`: `providerDotClass(state: ProviderState): string`, `providerPillLabel(state: ProviderState): string`, `providerDetail(state: ProviderState): string`.
  - `Panel.svelte`: props `{ label: string; children: Snippet }`.
  - `StatePill.svelte`: props `{ state: "valid" | "refreshable" | "signed_out" }`.
  - `SegmentBar.svelte`: props `{ counts: Record<string, number> }`.
  - `CountTiles.svelte`: props `{ counts: Record<string, number> }`.

- [ ] **Step 1: Write the status vocabulary**

`src/lib/plan-status.ts` owns the order, the labels and the colour of each plan status, so the bar and the tiles cannot disagree about either.

```ts
export const PLAN_STATUS_ORDER = ["pending", "transferred", "needs_review", "failed", "skip"] as const;

export const PLAN_STATUS_LABELS: Record<string, string> = {
	pending: "Pending",
	transferred: "Transferred",
	needs_review: "Needs review",
	failed: "Failed",
	skip: "Skipped",
};

export type PlanSegment = { key: string; label: string; count: number; fill: string; text: string };
```

`planSegments` returns the statuses the plan holds, in `PLAN_STATUS_ORDER`, then any status this build does not know, sorted by key — the same rule `StatusBreakdown` follows today, so a status the store holds is never hidden. The colour of each segment is a static class string so Tailwind can see it:

| status | fill | text |
| --- | --- | --- |
| pending | `bg-fill-pending` | `text-text-pending` |
| transferred | `bg-brand` | `text-brand` |
| needs_review | `bg-state-warn` | `text-state-warn` |
| failed | `bg-state-bad` | `text-state-bad` |
| skip | `bg-surface-subtle` | `text-text-faint` |
| anything else | `bg-surface-subtle` | `text-text-muted` |

The order is the design document's mock order, which differs from the old `StatusBreakdown` order (skip was third). The order is fixed, never sorted by count: rows that swap places as a transfer runs are unreadable at the moment the numbers matter.

- [ ] **Step 2: Write the failing test for an unknown status**

In `src/lib/components/SegmentBar.test.ts`:

```ts
it("gives a status this build does not know a segment of its own", () => {
	render(SegmentBar, { props: { counts: { pending: 3, something_new: 2 } } });

	const segments = screen.getAllByTestId("segment");
	expect(segments).toHaveLength(2);
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `pnpm vitest run src/lib/components/SegmentBar.test.ts`

Expected: FAIL — the component does not exist.

- [ ] **Step 4: Implement `SegmentBar.svelte`**

A row of segments whose widths are their share of the total, contiguous (no gap between them), inside a hairline border, each segment at least a few pixels wide so a sliver stays findable. Each segment carries `data-testid="segment"` and `aria-hidden="true"`: the numbers beside it are the readable form, and a bar of coloured blocks is not information a screen reader can use.

The component renders nothing when the total is zero: an empty bar would read as a bar that has not started.

- [ ] **Step 5: Implement `CountTiles.svelte`**

One tile per status in `planSegments`, each showing the count in `tabular-nums` and the label in uppercase, muted. No total tile: the total is the Home heading's number.

- [ ] **Step 6: Test the tiles**

In `src/lib/components/CountTiles.test.ts`: with `{ pending: 3, transferred: 1 }`, two tiles render, the counts `3` and `1` are on screen, and the labels are the ones from `PLAN_STATUS_LABELS`. A status the build does not know also gets a tile.

- [ ] **Step 7: Add the two Badge variants**

In `src/lib/components/ui/badge/index.svelte`, add `success` and `warning` variants alongside the ones the CLI shipped:

```ts
success: "border-transparent bg-state-ok/15 text-state-ok",
warning: "border-transparent bg-state-warn/15 text-state-warn",
```

- [ ] **Step 8: Implement `StatePill.svelte`**

Props `{ state: "valid" | "refreshable" | "signed_out" }`. Renders a Badge with the text `Signed in`, `Signed in, will refresh` or `Not signed in`, using the `success`, `warning` and `destructive` variants respectively. The text is the meaning; the colour only repeats it.

- [ ] **Step 9: Implement `Panel.svelte`**

Props `{ label: string; children: Snippet }`. Renders the uppercase panel label in the faint tier, then a shadcn `Card` holding the children. The Card is the panel's surface; the label is ours.

- [ ] **Step 10: Implement `provider-state.ts`**

`providerDotClass` returns the dot's class per state (`bg-state-ok`, `bg-state-warn`, `bg-state-bad`); `providerPillLabel` returns the pill's text; `providerDetail` returns the sentence beside the provider's name — `token refreshes itself` for a valid session, `token expires soon` for a refreshable one, `not signed in — run auth to fix` for a signed-out one. The wording lives here so the Home row and the Diagnostics row cannot drift.

- [ ] **Step 11: Verify and commit**

Run: `pnpm check`, `pnpm test`, `pnpm build`.

Expected: PASS, with the new tests included.

```bash
git add src/lib
git commit -m "feat(ui): the plan summary, the state pill and the panel"
```

---

### Task 5: The shell — the rail and the content panel

**Files:**
- Modify: `src/App.svelte`
- Modify: `src/lib/router.ts` (no change to behaviour; the six route ids stay)
- Test: `src/App.test.ts` (new)

**Interfaces:**
- Consumes: the token layer (Task 3), `getAppInfo` (Task 2).
- Produces: the shell every screen renders inside — a floating rail panel on the left and a content panel on the right, inside a gutter.

- [ ] **Step 1: Write the failing test for the shell**

In `src/App.test.ts` (new), mock `./lib/ipc` so the screens inside the shell do not call the host:

```ts
it("offers every section in the rail", async () => {
	render(App);
	const nav = await screen.findByRole("navigation");
	expect(within(nav).getAllByRole("button")).toHaveLength(6);
});

it("marks the section being shown", async () => {
	render(App);
	const nav = await screen.findByRole("navigation");
	expect(within(nav).getByRole("button", { name: "Home" }).getAttribute("aria-current")).toBe("page");
});

it("shows the version in the rail foot", async () => {
	vi.mocked(getAppInfo).mockResolvedValue({ version: "0.7.0", database_path: "/data/tidal2ytm.db" });
	render(App);
	expect(await screen.findByText("v0.7.0")).toBeTruthy();
});
```

`getAppInfo` is called by the rail foot only, so the mock resolves for it and rejects for everything else the screens ask for; the screens' own failures are their business and are not asserted here.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pnpm vitest run src/App.test.ts`

Expected: FAIL — the rail does not exist yet.

- [ ] **Step 3: Restructure `App.svelte`**

The markup becomes:

```svelte
<div class="shell">
	<nav class="rail" aria-label="Sections">
		<span class="brand">tidal2ytm</span>
		<ul>…</ul>
		<span class="foot">v{version}</span>
	</nav>
	<main class="content">
		…the routed screen…
	</main>
</div>
```

The layout, in Tailwind classes:

- `.shell`: `flex gap-3 p-3 h-screen bg-surface-shell` — the gutter is the window breathing room, and the app floats in it.
- `.rail`: `w-44 shrink-0 flex flex-col rounded-lg border border-line bg-surface-panel p-3`.
- `.brand`: `text-brand font-bold` — the accent, and no tagline.
- `.foot`: `mt-auto text-text-faint text-xs` at the rail's bottom.
- `.content`: `flex-1 min-w-0 overflow-auto rounded-lg border border-line bg-surface-content p-6`.
- The active nav item: `bg-surface-subtle text-text-primary shadow-[inset_2px_0_0_var(--brand)]`; the others `text-text-muted`.
- The four unbuilt routes keep their placeholder text inside the content panel, so no screen looks orphaned.

`aria-current="page"` stays on the active item.

- [ ] **Step 4: Read the version**

`App.svelte` calls `getAppInfo()` on mount and keeps the version. A failure is silent: the rail foot simply has no version, because a version number is not worth an error the user cannot act on. The database path it also returns is not shown here — Diagnostics shows it.

- [ ] **Step 5: Run the test to verify it passes**

Run: `pnpm vitest run src/App.test.ts`

Expected: PASS.

- [ ] **Step 6: Verify and commit**

Run: `pnpm check`, `pnpm test`, `pnpm build`.

```bash
git add src/App.svelte src/App.test.ts
git commit -m "feat(ui): the floating rail and the content panel"
```

---

### Task 6: The job progress row

**Files:**
- Modify: `src/lib/components/JobProgress.svelte`
- Modify: `src/lib/components/JobProgress.test.ts`

**Interfaces:**
- Consumes: the token layer (Task 3), shadcn's `Spinner` (Task 1).
- Produces: the same component, restyled. Its props do not change: `{ progress: Progress; onCancel?: () => void }`.

- [ ] **Step 1: Write the failing test for a run with no total**

In `src/lib/components/JobProgress.test.ts`, add:

```ts
// Tidal's favourites endpoint reports no count, and the count it can report
// includes tracks it will not hand back. A bar drawn against a total the run
// does not have would claim to know how much is left.
it("draws no bar while the total is unknown", () => {
	render(JobProgress, { props: { progress: { ...base, total: null } } });
	expect(screen.queryByLabelText("job progress")).toBeNull();
	expect(screen.getByText("5")).toBeTruthy();
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pnpm vitest run src/lib/components/JobProgress.test.ts`

Expected: it may already pass — the current component renders the `<progress>` element only when a total exists. If it passes, keep the test anyway: it pins the behaviour the restyle must not break, and the failing half of this task is the spinner swap below.

- [ ] **Step 3: Replace the hand-built spinner**

Delete the `.spinner` rule, the `@keyframes spin` block and the `span.spinner` element from `JobProgress.svelte`, and render shadcn's `Spinner` instead:

```svelte
import { Spinner } from "$lib/components/ui/spinner";
…
<Spinner aria-hidden="true" />
```

The native `<progress>` element stays: it is a platform control with built-in semantics, and the `aria-label="job progress"` on it is what the existing test finds. The reduced-motion rule in the global stylesheet (Task 3) stops its animation.

- [ ] **Step 4: Run the tests**

Run: `pnpm vitest run src/lib/components/JobProgress.test.ts`

Expected: PASS.

- [ ] **Step 5: Verify and commit**

Run: `pnpm check`, `pnpm test`, `pnpm build`.

```bash
git add src/lib/components/JobProgress.svelte src/lib/components/JobProgress.test.ts
git commit -m "refactor(ui): the job row uses the spinner it was given"
```

---

### Task 7: Home

**Files:**
- Modify: `src/routes/Home.svelte`
- Modify: `src/lib/components/ProviderRow.svelte`
- Modify: `src/routes/Home.test.ts`

**Interfaces:**
- Consumes: `Panel`, `SegmentBar`, `CountTiles`, `StatePill`, `provider-state.ts`, the token layer.
- Produces: Home with the layout of the approved mock: a page head with the library count, a Sync panel, a Transfer plan panel, a Providers panel.

- [ ] **Step 1: Write the failing test for the two counts agreeing**

In `src/routes/Home.test.ts`, add:

```ts
// Two numbers about the same library, one screen: a heading that says 2,287
// above tiles that add up to something else is the sort of disagreement that
// makes a person distrust the whole screen.
it("agrees with itself about how many tracks the library holds", async () => {
	answer.mockResolvedValue(BOTH_SIGNED_OUT);
	counts.mockResolvedValue({ pending: 12, transferred: 3, failed: 1 });

	render(Home);

	expect(await screen.findByText("16")).toBeTruthy();
	expect(screen.getAllByText("16")).toHaveLength(2);
});
```

The count appears once in the heading and once as the sum of the tiles' values; the test asserts the two are the same number rather than where they are.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pnpm vitest run src/routes/Home.test.ts`

Expected: FAIL — the heading has no count.

- [ ] **Step 3: Restructure `Home.svelte`**

The screen becomes, in order:

1. **Page head** — the title `Home`, and beside it the library count: `2,287 tracks in library`, the number in `tabular-nums` and the words muted.
2. **Sync panel** — label `Sync`; a primary `Sync now` button; the state line beneath it (`Never synced`, `Last sync did not finish`, `Synced`, `Synced (empty library)`); the job row (`JobProgress`) when a job is running or has finished.
3. **Transfer plan panel** — label `Transfer plan`; the `SegmentBar`; the `CountTiles`.
4. **Providers panel** — label `Providers`; one row per provider.

The "Last sync did not finish" wording replaces the old "Interrupted. Run sync again?" for the case where a run started and never completed. The timestamps the host stores are shown as they are stored; no relative-time formatting is added in this pass.

The existing behaviour stays exactly as it is: the alert region, the busy-gating, and the re-read of the counts and the meta after a job ends.

The sign-in action and the import-client-secret action both move to Diagnostics, which is where the master spec puts re-authentication (line 253) and which is the screen the mock gives them to. Home states the provider's condition and leaves the acting to Diagnostics; the detail sentence says where to go ("not signed in — run auth to fix").

- [ ] **Step 4: Restructure `ProviderRow.svelte`**

The row becomes the Home form: a state dot, the provider's name, and the detail sentence from `provider-state.ts`. The `role="group"` and its `aria-label` stay, so the two rows are still distinguishable to a screen reader. The `role="status"` region and its `data-state` attribute stay for the same reason.

The sign-in button moves to the Diagnostics row (Task 8), which is where the master spec puts re-authentication. The Home row keeps the provider's state and nothing else.

- [ ] **Step 5: Run the tests**

Run: `pnpm vitest run src/routes/Home.test.ts`

Expected: PASS, including the existing contracts: one card per provider, distinct accessible names, the failure explanation shown, the status breakdown, the empty plan, and the finished job's count.

Two of Home's existing tests move to Diagnostics in Task 8, because the actions they exercise move there: the one that clicks a sign-in button inside a provider row, and the one that asserts the import-client-secret action is on screen. Their contracts — a failed sign-in leaves the screen usable and shows the explanation — are re-expressed against the Diagnostics row.

- [ ] **Step 6: Verify and commit**

Run: `pnpm check`, `pnpm test`, `pnpm build`.

```bash
git add src/routes/Home.svelte src/routes/Home.test.ts src/lib/components/ProviderRow.svelte
git commit -m "feat(ui): the home screen in the new system"
```

---

### Task 8: Diagnostics

**Files:**
- Modify: `src/routes/Diagnostics.svelte`
- Create: `src/lib/components/ProviderAuthRow.svelte`
- Modify: `src/routes/Diagnostics.test.ts`

**Interfaces:**
- Consumes: `Panel`, `StatePill`, `provider-state.ts`, `getAppInfo`, `getLogFiles`, `uiLogFile`, `formatBytes`, shadcn's `Select`.
- Produces: Diagnostics with the Authentication, Log level, Storage and Logs panels.

- [ ] **Step 1: Write the failing test for a log file that does not exist**

In `src/routes/Diagnostics.test.ts`, replace the `getLogPaths` mock with `getLogFiles` and add `vi.mock("../lib/ui-log")` to the file's mock list. Then add:

```ts
// The first run has not written a log yet. A panel that drops the row
// because the file is missing is the one screen that cannot then explain
// why nothing was logged.
it("keeps the log rows when a log file does not exist yet", async () => {
	getLogFiles.mockResolvedValue([
		{ path: "/logs/host.log", bytes: 0, is_dir: false },
		{ path: "/logs", bytes: 0, is_dir: true },
	]);
	vi.mocked(uiLogFile).mockResolvedValue({ path: "/appdata/logs/ui.log", bytes: 0 });

	render(Diagnostics);

	expect(await screen.findByText("/logs/host.log")).toBeTruthy();
	expect(await screen.findByText("/appdata/logs/ui.log")).toBeTruthy();
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pnpm vitest run src/routes/Diagnostics.test.ts`

Expected: FAIL — the panel does not exist yet.

- [ ] **Step 3: Restructure `Diagnostics.svelte`**

Four panels, in this order:

1. **Authentication** — one row per provider: the state dot, the provider's name, the `StatePill`, the detail sentence, and a button. The button reads `Re-authenticate` and is a ghost button; when the provider is signed out it becomes the panel's primary button, because that is the one action the panel is for. It is disabled while a sign-in is running, and for YouTube Music while no client secret is present — the same rule the old screen had.
2. **Log level** — a shadcn `Select` with the five levels the host accepts (`OFF`, `ERROR`, `WARN`, `INFO`, `DEBUG`), and the hint that the sidecar picks the level up on its next start.
3. **Storage** — the database file's path in monospace, the schema version, and the number of tracks the plan holds.
4. **Logs** — one row per file: the path in monospace, the size through `formatBytes`, and an `Open` button that calls `openPath`. The rows are the host's files from `getLogFiles` plus the frontend's own log from `uiLogFile`.

The `role="alert"` region, the busy-gating and the import-client-secret action stay.

- [ ] **Step 4: Implement `ProviderAuthRow.svelte`**

Props `{ label: string; state: ProviderState; detail: string; busy: boolean; disabled?: boolean; onAction: () => void }`. It renders the dot, the name, the `StatePill`, the detail and the button. The button's variant depends on the state: primary when the provider is signed out, ghost otherwise.

- [ ] **Step 5: Surface a failed open**

`openPath` failures are reported in the screen's alert region instead of being swallowed. The `Open` button acts on a path the user may not be allowed to open — in a development build the log directory sits outside `$APPDATA`, which the opener capability is scoped to — and a button that does nothing and says nothing is the worst of the three outcomes.

- [ ] **Step 6: Rewrite the Diagnostics tests**

The existing tests keep their contracts with the new structure:

- the two sign-in actions are on screen;
- the level selector is on screen (now a shadcn `Select`, so the role is `combobox` still);
- the three log paths are on screen;
- the database counts are on screen;
- the import action appears only when the client secret is missing;
- a failed sign-in keeps the log controls on screen — and now also keeps the authentication rows.

The two tests that move from Home in Task 7 arrive here: one clicks a provider's sign-in button and asserts the failure is shown and the screen is still usable, and one asserts the import-client-secret action is on screen only when the secret is missing.

Add the case from Step 1, and one where the host reports a file size: the size is shown.

- [ ] **Step 7: Verify and commit**

Run: `pnpm check`, `pnpm test`, `pnpm build`.

```bash
git add src/routes/Diagnostics.svelte src/routes/Diagnostics.test.ts src/lib/components/ProviderAuthRow.svelte
git commit -m "feat(ui): the diagnostics screen in the new system"
```

---

### Task 9: The gates, the build, and the hand-off

**Files:**
- Modify: `src-tauri/Cargo.toml` only if the version moved.

**Interfaces:**
- Consumes: everything above.
- Produces: a build the user can look at.

- [ ] **Step 1: Run the whole gate set**

Run: `pnpm format` → `pnpm lint:fix` → `pnpm lint` → `pnpm format:check` → `pnpm check` → `pnpm test` → `pnpm build`.

Run: `cargo fmt --all --check` → `cargo clippy -p tidal2ytm --all-targets -- -D warnings` → `cargo test -p tidal2ytm`.

Run: `uv run ruff check .` → `uv run ruff format --check .` → `uv run pyright` → `uv run pytest -q`.

Expected: all pass. If `pnpm format` rewrote anything, stage it and commit it as its own commit before continuing.

- [ ] **Step 2: Build the app**

The host build needs the frontend bundle and a sidecar placeholder on disk, or it fails inside the build script before compiling a line of host code:

Run: `pnpm build` (the `dist/` the host's `frontendDist` points at).

Run: `$triple = (rustc -vV | Select-String '^host: ').ToString().Split(':')[1].Trim(); New-Item -ItemType File -Force -Path "src-tauri\binaries\tidal2ytm-sidecar-$triple.exe"` — an empty file, never committed (`.gitignore` already excludes `src-tauri/binaries/`).

Run: `pnpm tauri build`.

- [ ] **Step 3: Clear the stale sidecar**

Every `pnpm tauri build` recreates `target/release/tidal2ytm-sidecar.exe`. Delete it, or a later run picks up the stale one instead of the sidecar the build just made. Kill a running `tidal2ytm` process first — the delete fails with "os error 5" while the file is open.

- [ ] **Step 4: Hand the build to the user**

Report where the binary is and what to look at. The visual pass is the user's: the design document fixes the identity, and the specifics — radius, sizes, spacing, the destructive button's border, whether the Home heading's count wants to be there at all — are settled by looking, not by this plan.

Three of the outstanding manual QA items can now be checked: the log paths and their sizes, the schema version, and the Tidal re-authenticate button. The rest (DEBUG log growth, the restart sign-in) still need a build the user runs.

- [ ] **Step 5: Commit any leftovers**

```bash
git add -A
git commit -m "chore(ui): formatting"
```

Do not push. The four commits from before this plan are unpushed too; pushing waits for the user's approval.
