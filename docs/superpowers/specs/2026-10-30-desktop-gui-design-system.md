# Desktop GUI design system

Date: 2026-10-30. Status: proposed, awaiting approval. This document is the design output of a brainstorming pass; it is the brief for the implementation pass that restyles the desktop GUI (rail, Home, Diagnostics now; Match, Transfer, Review in Phase 4b).

## Intent and why

The desktop GUI shipped in Phase 4a with placeholder styling and no design pass; the user called the result unacceptable. This document defines the visual identity and the system that generates it, so that every screen — including the Phase 4b screens — comes out of one coherent language rather than per-screen improvisation.

The reference point the user named is Obsidian: sleek, responsive, intuitive, calm. The app must have a colour identity of its own, built on the user's favourite colour, Prussian blue (hue 205).

## Goals, non-goals, anti-goals

Goals: a dark-only visual identity in hue 205; a token system the user can retune by hand after sign-off; alignment and cohesion between every element; information-dense screens that never feel cluttered; the design system proven on Home and Diagnostics before Phase 4b reuses it.

Non-goals: light mode (dark mode only, per the master spec line 255); theming support or user-facing settings for colour; customizability (the user explicitly set this aside); CSS pre-processors; new navigation models (the left rail stays). Tailwind is not a non-goal — it is adopted with shadcn-svelte, and the token layer is what holds the identity.

Anti-goals: decoration that carries no information — glows, gradients, per-card colour flourishes; motion beyond state transitions; anything that makes two screens of this app look like they come from different apps; fighting shadcn-svelte's component defaults on specifics — border radius, font sizes, default widths, animation lengths, dropdown spacing. The design effort goes into the broad strokes of identity (colour, typography, structure, density); shadcn's specifics are accepted and tweaked only when a specific value offends.

## Constraints

Svelte 5 + Tailwind CSS v4 for utilities. Tokens are CSS custom properties in `src/styles/app.css`, mapped into Tailwind via `@theme inline`; no pre-processor. WCAG AA contrast minimums for text. `prefers-reduced-motion` honoured for every animation. Webview2 (Chromium) is the only target, but standard CSS only — no experimental features without a fallback story.

## Component foundation (decided)

The user's call, confirmed in brainstorming: adopt shadcn-svelte for the components that need real accessible behaviour — dropdowns, selects, dialogs, text inputs, grids — instead of hand-building them. shadcn-svelte vendors component source into the repo (no black-box dependency) on top of bits-ui primitives, so the look is ours to rewrite while the interaction and accessibility work is proven. Tailwind v4 ships with it.

The token layer remains the single source of colour. shadcn's semantic variable names (`--background`, `--foreground`, `--card`, `--primary`, `--secondary`, `--muted`, `--destructive`, `--ring`, `--input`, `--border`, `--radius` and the sidebar set) take their values from the hue-205 layer defined below; app-specific tokens (amber, green, pending) are added alongside as custom properties with their own `--color-*` mappings. Dark mode is the only mode: the `.dark` scope is applied unconditionally at the root, and `:root` carries the same values.

Installation shape (for the plan): `tailwindcss` v4 + `@tailwindcss/vite` as dev dependencies, `@import "tailwindcss"` at the top of `src/styles/app.css`, the Vite plugin wired in `vite.config.ts`, then `shadcn-svelte` CLI init and per-component `add`. The user retunes the flat token block by hand after sign-off; everything downstream follows.

## Colour

One accent (hue 205) plus semantic colours that carry meaning and nothing else. Every surface token must back-convert to exactly hue 205 where 8-bit precision allows — the user's picker catches drift. Method: choose tokens in rgb, verify by back-conversion; chroma locked to multiples of 12 makes the hue angle land exactly. Near-black surfaces may sit within ±2° of 205 at best; accent, borders and text hold 205 exactly.

Surface set (saturated — the approved starting point):

| Token | Value | Role |
| --- | --- | --- |
| `--shell` | `#050c11` | window background behind the inset frame |
| `--content` | `#060d12` | content panel background |
| `--panel` | `#0b1923` | rail, cards, inset panels |
| `--border` | `#143044` | hairline borders |
| `--navbg` | `#0e1c26` | active nav item background |
| `--text` | `#d6e4e8` | primary text |
| `--muted` | `#84929c` | metadata, secondary text |
| `--faint` | `#3c5160` | panel labels, quietest tier |
| `--pending` | `#b8c6d0` | pending state text |
| `--pendingbar` | `#586e7e` | pending segment fill |
| `--accent` | `#4298d4` | the one accent; interactive and identity |
| `--accent-strong` | `#1e5880` | primary button background |
| `--accent-text` | `#dcebf5` | text on accent-filled surfaces |

Semantic colours, fixed rgb with their own hues: amber `rgb(217,168,78)` = needs your attention (needs review rows, expiring tokens, refreshable state); red `rgb(217,106,90)` = failed/stop, with a filled tonal treatment (background `#3a1d18`, border `#5a2a20`, text `#e89a8b`); green `rgb(95,185,141)` = signed in, healthy; pending is the grey-blue above; skip is the faint tier. Provider dots and state pills use these consistently. Amber and red are the only colours besides the accent allowed on screen, and both mean something.

Bold, italic and colour must each state which meaning they carry. Colour is never the sole carrier: state pills have text, dots sit beside labels.

## Structure

The app floats in a darker window shell (`--shell`) with a visible gutter on all four sides; inside it, two inset panels: the rail (floating, rounded) and the content panel. Hairline borders (`--border`) divide; there are no card boxes inside cards, no double borders. A vertical rail is correct for this app because its screens are vertical lists in a wide window; the rail would be revised only if a screen ever needed the horizontal space it costs.

## Type

Font families are the generic CSS keywords: `sans-serif` for UI text, `monospace` for numerals and technical text. No bundled webfonts. In the Tailwind layer these are declared as `--font-sans: sans-serif` and `--font-mono: monospace`, with `--default-font-family` pointed at `--font-sans`; Tailwind's preflight already sets `font: inherit` on form controls, so buttons and inputs inherit the sans stack instead of the browser default.

Roles, not sizes: a page title (largest, semibold, tight tracking), body and control text at one shared size, secondary text one step down, and uppercase tracked labels reserved for panel labels — uppercase appears nowhere else. Sentence case everywhere else, including buttons and headings. The concrete sizes come from Tailwind's type scale and settle during the visual pass; what this document fixes is the hierarchy and the case rules.

Monospace scope — numerals where alignment or scanning matters (count tiles, live fetch counts, durations in tables, percentages) and technical strings (file paths, file sizes, schema versions, latencies, token expiry where tabular). Timestamps and prose stay sans. `font-variant-numeric: tabular-nums` on all mono numerals.

## Buttons and controls

Three treatments, by colour meaning: primary (accent-filled `--accent-strong` with `--accent-text`), ghost (transparent, hairline border, muted text) and destructive (filled tonal red). One action per screen is primary. Sentence case labels. Buttons take shadcn's own variant set and default metrics; this document fixes which treatment each action gets, not how the button is drawn. Focus is a visible accent ring — never removed, never colour-only.

## Density and spacing

Comfortable density: roomy rows, hairline row dividers, no zebra striping. Spacing runs on a small scale of consistent steps rather than ad-hoc gaps — panels use the same step scale internally and between themselves, and content panels get more breathing room than inner panels. Corner radius comes from the radius token, shared by our panels and shadcn's components alike, so the whole app rounds alike; pills and dots are fully round. Exact steps, row heights and paddings settle during the visual pass.

## Data display

The transfer-plan segment bar is contiguous (gaps would read as broken progress), hairline-bordered, with a visible pending fill, and sliver segments wide enough to find. Count tiles sit beneath it: tabular numerals, muted uppercase labels. Status is shown redundantly by design — shape at a glance (bar, dots), text for reading — because colour never carries meaning alone.

## Motion

Motion exists only to communicate state. Indeterminate work shows a spinner; a determinate bar appears only when a real total exists. Durations, easing and the spinner's own construction belong to shadcn and the platform — not to this document. Everything honours `prefers-reduced-motion` by collapsing to static.

## Screens

The approved mocks live outside the repo in the brainstorm session at `.superpowers/brainstorm/1888-1793412304/content/` (`home-v6.html`, `diagnostics-v1.html`). Both the prose here and the mocks are authoritative: the mocks win on layout detail (panel order, what sits in the page head, how the live count row is laid out), this document wins on system rules. If they conflict, the mock's layout is implemented inside this document's constraints.

Home: page head with library count; Sync panel (primary action, ghost cancel, live count row with spinner while running); Transfer plan panel (segment bar + count tiles); Providers panel (dots, state text, token health). The structure mocked in `home-v6.html`.

Diagnostics: Authentication panel (provider rows, state pill, re-auth ghost button — becomes primary only when a provider is signed out), Log level panel (the existing control, kept — the master spec line 253 places it here), Storage panel (DB path, schema version, track count), Logs panel (paths, sizes, open buttons). The structure mocked in `diagnostics-v1.html` plus the log-level panel the mock omitted.

Match, Transfer and Review are Phase 4b scope: they receive this system but are not designed here. Review is the design centrepiece per the master spec (line 247) — two panes, ranked candidates with per-component score bars — and gets its own mock pass inside 4b. Empty, loading, offline, auth-expired, interrupted and no-candidate states are designed per screen, not once globally (spec line 249).

## Brand

The rail brand is "tidal2ytm" in the accent colour, no tagline. Version number sits at the rail foot in the faint tier.

## Scope of the implementation pass

This pass restructures Home and Diagnostics to the mocked layout, not merely re-colours them — the existing markup does not match the mocks. Behavioural contracts survive the restructure consciously: `role="status"` regions, the busy-gating of sign-in buttons, and every live value keep their semantics. Existing component tests are rewritten to the new structure; the durable contracts they encode (state visibility, gating, status semantics) are re-expressed, not dropped. Per the user's global testing rules and explicit instruction, no test asserts styling — no hues, fonts, colours or copy are locked by tests.

The four unbuilt routes (Library, Match, Transfer, Review placeholders) are styled with the token system in this pass so the app never looks like two different apps; their real screens arrive in Phase 4b.

New host/IPC surface is in scope (user-confirmed): small Rust commands supply the DB path, log file sizes and the app version to the Storage and Logs panels, with the contract and tests that go with them.

## What is left to the builder

The document fixes identity, not pixels. Everything below is deliberately left open, to be settled by looking at the running app rather than by guesswork:

Exact type sizes, weights and tracking; spacing steps, row heights and paddings; corner radii; motion durations and easing; the spinner's construction; shadcn's component defaults — these stay as shipped until something in the running app earns a change. This document also does not pre-tune shadcn's components: they go in as they are, and the user's later visual pass adjusts what offends.

Also the builder's: our own visual components (Panel, StatePill, SegmentBar, CountTiles) on top of the shadcn controls; the CSS reset details; per-screen state designs for Phase 4b; the accessibility pass (contrast verification against the final values). Token values are the user's to retune after sign-off; the structure must make that a one-file edit.
