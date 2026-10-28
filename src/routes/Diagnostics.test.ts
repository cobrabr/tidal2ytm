import { fireEvent, render, screen } from "@testing-library/svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Diagnostics from "./Diagnostics.svelte";
import { getAuthStatus, getLogPaths, getStatusCounts, getSchemaVersion } from "../lib/ipc";
import { startSignIn } from "../lib/auth-actions";

vi.mock("../lib/ipc");
vi.mock("../lib/auth-actions");
vi.mock("@tauri-apps/plugin-dialog", () => ({ open: vi.fn() }));
vi.mock("@tauri-apps/plugin-opener", () => ({ openPath: vi.fn() }));

const getAuth = vi.mocked(getAuthStatus);
const getPaths = vi.mocked(getLogPaths);
const getCounts = vi.mocked(getStatusCounts);
const getSchema = vi.mocked(getSchemaVersion);

const STATUS = {
	tidal: { status: "valid" as const, expires_at: "2099-01-01T00:00:00Z", has_client_secret: true },
	ytm: { status: "signed_out" as const, expires_at: null, has_client_secret: false },
};

beforeEach(() => {
	getAuth.mockReset();
	getPaths.mockReset();
	getCounts.mockReset();
	getSchema.mockReset();
	vi.mocked(startSignIn).mockReset();

	getAuth.mockResolvedValue(STATUS);
	getPaths.mockResolvedValue({ host_log: "/h.log", history: "/hist", frontend_log: "/f.log" });
	getCounts.mockResolvedValue({ pending: 3 });
	getSchema.mockResolvedValue(7);
});

describe("Diagnostics", () => {
	it("renders the two provider sign-in actions", async () => {
		render(Diagnostics);
		expect(await screen.findByRole("button", { name: /sign in to tidal/i })).toBeTruthy();
		expect(await screen.findByRole("button", { name: /sign in to youtube music/i })).toBeTruthy();
	});

	it("renders the level selector", async () => {
		render(Diagnostics);
		expect(await screen.findByRole("combobox")).toBeTruthy();
	});

	it("renders three log paths", async () => {
		render(Diagnostics);
		expect(await screen.findByText("/h.log")).toBeTruthy();
		expect(await screen.findByText("/hist")).toBeTruthy();
		expect(await screen.findByText("/f.log")).toBeTruthy();
	});

	it("renders the database counts", async () => {
		render(Diagnostics);
		expect(await screen.findByText(/pending: 3/i)).toBeTruthy();
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

	// This is the screen a failure gets investigated from, so losing the level
	// selector and the log paths to the error message removes the controls that
	// explain it, on the one screen built to keep them reachable.
	it("keeps the log controls when a sign-in fails", async () => {
		vi.mocked(startSignIn).mockRejectedValue(new Error("the flow was refused"));

		render(Diagnostics);

		await fireEvent.click(await screen.findByRole("button", { name: /sign in to tidal/i }));

		expect((await screen.findByRole("alert")).textContent).toContain("the flow was refused");
		expect(screen.getByRole("combobox")).toBeTruthy();
		expect(screen.getByText("/h.log")).toBeTruthy();
	});
});
