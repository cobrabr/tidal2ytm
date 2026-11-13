import { render, screen, within } from "@testing-library/svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App.svelte";
import { getAppInfo, getAuthStatus, getMeta, getSchemaVersion, getStatusCounts } from "./lib/ipc";
import type { AppInfo } from "./lib/types";

// The shell is the frame the screens render inside, so the screens' own calls
// to the host have to be answered here or the test fails for a reason that has
// nothing to do with the shell.
//
// Every mocked call carries a value. An automock answers `undefined`, and a
// screen that awaits `undefined` and then reads a field of it throws — a
// failure that looks like a broken shell and is not one.
vi.mock("./lib/ipc");

// Home subscribes to the host's job events, and that subscription reaches the
// Tauri event API. Mocked at the screen's own boundary, the shell test does
// not depend on a third-party module failing quietly to stay green.
vi.mock("./lib/jobs");

beforeEach(() => {
	vi.mocked(getAuthStatus).mockReset();
	vi.mocked(getStatusCounts).mockReset();
	vi.mocked(getMeta).mockReset();
	vi.mocked(getSchemaVersion).mockReset();
	vi.mocked(getAppInfo).mockReset();

	vi.mocked(getAuthStatus).mockResolvedValue({
		tidal: { status: "signed_out", expires_at: null, has_client_secret: true },
		ytm: { status: "signed_out", expires_at: null, has_client_secret: false },
	});
	vi.mocked(getStatusCounts).mockResolvedValue({});
	vi.mocked(getMeta).mockResolvedValue(null);
	vi.mocked(getSchemaVersion).mockResolvedValue(1);
	vi.mocked(getAppInfo).mockResolvedValue({ version: "0.7.0", database_path: "/data/tidal2ytm.db" });
});

describe("the shell", () => {
	// The rail is the whole navigation: a screen nobody can reach is a screen
	// that does not exist, and the six sections are the app's entire map.
	it("offers every section in the rail", async () => {
		render(App);

		const nav = await screen.findByRole("navigation");
		expect(within(nav).getAllByRole("button")).toHaveLength(6);
	});

	// The section being shown is marked, not merely tinted: a screen reader has
	// to be able to say which one it is on, and the marker cannot be colour.
	it("marks the section being shown", async () => {
		render(App);

		const nav = await screen.findByRole("navigation");
		expect(within(nav).getByRole("button", { name: "Home" }).getAttribute("aria-current")).toBe("page");
	});

	// The marker belongs to one section and no other. A rail that marked every
	// row would say to a screen reader that it is on all six at once, and the
	// marker would carry no information at all.
	it("marks only the section being shown", async () => {
		render(App);

		const nav = await screen.findByRole("navigation");
		expect(within(nav).getByRole("button", { name: "Library" }).hasAttribute("aria-current")).toBe(false);
	});

	// The shell is two halves and both have to be there: the rail a person
	// navigates with, and the panel the screen renders inside. A shell with no
	// content panel still answers every other test, and ships an app with
	// nothing in it.
	it("renders the routed screen inside the content panel", async () => {
		render(App);

		const panel = await screen.findByRole("main");
		// The default route is Home, whose two provider rows are the groups.
		expect(await within(panel).findAllByRole("group")).toHaveLength(2);
	});

	// The version comes from the host, which is the only place that knows it.
	it("shows the version in the rail foot", async () => {
		render(App);

		expect(await screen.findByText("v0.7.0")).toBeTruthy();
	});

	// A version number is not worth an error a person cannot act on. The rail
	// simply carries none, and everything else in the shell still stands.
	it("leaves the rail without a version when the host cannot say", async () => {
		vi.mocked(getAppInfo).mockRejectedValue(new Error("the host is not there"));

		render(App);

		const nav = await screen.findByRole("navigation");
		expect(within(nav).getAllByRole("button")).toHaveLength(6);
		expect(screen.queryByText(/^v\d/)).toBeNull();
	});

	// The host's answer is not validated at the boundary, so a payload that has
	// drifted can arrive carrying no version at all. The foot must then carry
	// none: the text "vundefined" tells a person nothing and looks broken.
	it("leaves the rail without a version when the host answers without one", async () => {
		vi.mocked(getAppInfo).mockResolvedValue({ database_path: "/data/tidal2ytm.db" } as AppInfo);

		render(App);

		const nav = await screen.findByRole("navigation");
		expect(within(nav).queryByText(/^v/)).toBeNull();
	});
});
