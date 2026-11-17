import { fireEvent, render, screen, within } from "@testing-library/svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Diagnostics from "./Diagnostics.svelte";
import { getAppInfo, getAuthStatus, getLogFiles, getSchemaVersion, getStatusCounts, setLogLevel } from "../lib/ipc";
import { startSignIn } from "../lib/auth-actions";
import { uiLogFile } from "../lib/ui-log";
import { openPath } from "@tauri-apps/plugin-opener";
import type { AuthStatus } from "../lib/types";

vi.mock("../lib/ipc");
vi.mock("../lib/auth-actions");
vi.mock("../lib/ui-log");
vi.mock("@tauri-apps/plugin-dialog", () => ({ open: vi.fn() }));
vi.mock("@tauri-apps/plugin-opener", () => ({ openPath: vi.fn() }));

const getAuth = vi.mocked(getAuthStatus);
const getFiles = vi.mocked(getLogFiles);
const getCounts = vi.mocked(getStatusCounts);
const getSchema = vi.mocked(getSchemaVersion);
const getInfo = vi.mocked(getAppInfo);
const getUiLog = vi.mocked(uiLogFile);

const STATUS = {
	tidal: { status: "valid" as const, expires_at: "2099-01-01T00:00:00Z", has_client_secret: true },
	ytm: { status: "signed_out" as const, expires_at: null, has_client_secret: false },
};

beforeEach(() => {
	getAuth.mockReset();
	getFiles.mockReset();
	getCounts.mockReset();
	getSchema.mockReset();
	getInfo.mockReset();
	getUiLog.mockReset();
	vi.mocked(startSignIn).mockReset();
	vi.mocked(openPath).mockReset();
	vi.mocked(setLogLevel).mockReset();

	getAuth.mockResolvedValue(STATUS);
	getFiles.mockResolvedValue([
		{ path: "/h.log", bytes: 0, is_dir: false },
		{ path: "/hist", bytes: 0, is_dir: true },
	]);
	getUiLog.mockResolvedValue({ path: "/f.log", bytes: 0 });
	getCounts.mockResolvedValue({ pending: 3 });
	getSchema.mockResolvedValue(7);
	getInfo.mockResolvedValue({ version: "0.7.0", database_path: "/data/tidal2ytm.db" });
});

describe("Diagnostics", () => {
	// One action per provider, in a row that says which provider it belongs
	// to. Two rows with the same button and nothing else would leave a person
	// — and a screen reader — re-authenticating the one that was already
	// signed in.
	it("renders one sign-in action per provider", async () => {
		render(Diagnostics);

		const groups = await screen.findAllByRole("group");
		expect(groups).toHaveLength(2);
		for (const group of groups) {
			expect(within(group).getByRole("button", { name: /re-authenticate/i })).toBeTruthy();
		}
	});

	// The async sign-in result is announced, and the group's name says which
	// provider it was. The attribute carries the outcome without depending on
	// the pill's wording, which is free to change.
	it("announces each provider's state with a stable attribute", async () => {
		render(Diagnostics);

		const tidal = await screen.findByRole("group", { name: "Tidal" });
		expect(within(tidal).getByRole("status").getAttribute("data-state")).toBe("valid");
		const ytm = await screen.findByRole("group", { name: "YouTube Music" });
		expect(within(ytm).getByRole("status").getAttribute("data-state")).toBe("signed-out");
	});

	it("renders the level selector", async () => {
		render(Diagnostics);
		expect(await screen.findByRole("combobox")).toBeTruthy();
	});

	// The selector has to reach the host, not merely look like a control: a
	// dropdown that renders and never sends anything leaves the level at INFO
	// while the screen says otherwise.
	it("sends the chosen level to the host", async () => {
		render(Diagnostics);

		await fireEvent.click(await screen.findByRole("combobox"));
		const warn = await screen.findByRole("option", { name: "Warn" });
		await fireEvent.pointerDown(warn);
		await fireEvent.pointerUp(warn);

		await vi.waitFor(() => expect(setLogLevel).toHaveBeenCalledWith("WARN"));
	});

	it("renders three log paths", async () => {
		render(Diagnostics);
		expect(await screen.findByText("/h.log")).toBeTruthy();
		expect(await screen.findByText("/hist")).toBeTruthy();
		expect(await screen.findByText("/f.log")).toBeTruthy();
	});

	// The database the host opened, named by the host. A path the screen
	// assembled itself is a path that can be wrong.
	it("shows the database file the host opened", async () => {
		render(Diagnostics);
		expect(await screen.findByText("/data/tidal2ytm.db")).toBeTruthy();
	});

	// The total, and not one status's count: three pending and two
	// transferred is five tracks, and a panel that reported either count on
	// its own would under-report the plan.
	it("shows how many tracks the plan holds", async () => {
		getCounts.mockResolvedValue({ pending: 3, transferred: 2 });

		render(Diagnostics);

		expect(await screen.findByText("5")).toBeTruthy();
	});

	it("offers the import action only when the client secret is missing", async () => {
		render(Diagnostics);
		expect(await screen.findByRole("button", { name: /import client secret/i })).toBeTruthy();
	});

	it("does not offer the import action when the client secret is present", async () => {
		getAuth.mockResolvedValue({
			tidal: { status: "valid", expires_at: null, has_client_secret: true },
			ytm: { status: "valid", expires_at: null, has_client_secret: true },
		});
		render(Diagnostics);
		expect(screen.queryByRole("button", { name: /import client secret/i })).toBeNull();
	});

	// A provider with no client secret cannot sign in, so the row must not
	// offer an action that can only come back as a refusal.
	it("disables the sign-in of a provider with no client secret", async () => {
		render(Diagnostics);

		const ytm = await screen.findByRole("group", { name: "YouTube Music" });
		expect(within(ytm).getByRole("button", { name: /re-authenticate/i })).toHaveProperty("disabled", true);
	});

	// Two flows at once race over one token file, and a person clicking again
	// because nothing visible happened yet is the usual way that happens.
	it("waits while a sign-in is already in flight", async () => {
		vi.mocked(startSignIn).mockImplementation(() => new Promise<AuthStatus>(() => {}));

		render(Diagnostics);

		const tidal = await screen.findByRole("group", { name: "Tidal" });
		await fireEvent.click(within(tidal).getByRole("button", { name: /re-authenticate/i }));

		await vi.waitFor(() =>
			expect(within(tidal).getByRole("button", { name: /re-authenticate/i })).toHaveProperty("disabled", true),
		);
	});

	// This is the screen a failure gets investigated from, so losing the level
	// selector, the log paths and the provider rows to the error message
	// removes the controls that explain it, on the one screen built to keep
	// them reachable.
	it("keeps the log controls and the provider rows when a sign-in fails", async () => {
		vi.mocked(startSignIn).mockRejectedValue(new Error("the flow was refused"));

		render(Diagnostics);

		const tidal = await screen.findByRole("group", { name: "Tidal" });
		await fireEvent.click(within(tidal).getByRole("button", { name: /re-authenticate/i }));

		expect((await screen.findByRole("alert")).textContent).toContain("the flow was refused");
		expect(screen.getByRole("combobox")).toBeTruthy();
		expect(screen.getByText("/h.log")).toBeTruthy();
		expect(screen.getAllByRole("group")).toHaveLength(2);
	});

	// The first run has not written a log yet. A panel that drops the row
	// because the file is missing is the one screen that cannot then explain
	// why nothing was logged.
	it("keeps the log rows when a log file does not exist yet", async () => {
		getFiles.mockResolvedValue([
			{ path: "/logs/host.log", bytes: 0, is_dir: false },
			{ path: "/logs", bytes: 0, is_dir: true },
		]);
		vi.mocked(uiLogFile).mockResolvedValue({ path: "/appdata/logs/ui.log", bytes: 0 });

		render(Diagnostics);

		expect(await screen.findByText("/logs/host.log")).toBeTruthy();
		expect(await screen.findByText("/appdata/logs/ui.log")).toBeTruthy();
	});

	// The size the host measured, not a size the screen guessed at.
	it("shows the size the host reports for a log file", async () => {
		getFiles.mockResolvedValue([{ path: "/h.log", bytes: 2048, is_dir: false }]);

		render(Diagnostics);

		expect(await screen.findByText("2.0 KB")).toBeTruthy();
	});

	// The path may sit outside the scope the opener is allowed, so the
	// refusal is reported. A button that does nothing and says nothing is the
	// worst of the three outcomes.
	it("reports a failure to open a log", async () => {
		vi.mocked(openPath).mockRejectedValue(new Error("the path is outside the app's scope"));

		render(Diagnostics);

		const opens = await screen.findAllByRole("button", { name: /open/i });
		await fireEvent.click(opens[0]);

		expect((await screen.findByRole("alert")).textContent).toContain("outside the app's scope");
	});
});
