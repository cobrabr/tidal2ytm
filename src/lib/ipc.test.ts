import { invoke } from "@tauri-apps/api/core";
import { describe, expect, it, vi } from "vitest";
import { UNREADABLE_STATUS, getAppInfo, getAuthStatus, getLogFiles } from "./ipc";
import type { AuthStatus } from "./types";

// The host, not this module, is what `invoke` talks to. Stubbing the Tauri API
// keeps these assertions about this module's own job — pass the payload through,
// turn a rejection into an `Error` — rather than about what the host returns.
vi.mock("@tauri-apps/api/core");

const call = vi.mocked(invoke);

const PAYLOAD: AuthStatus = {
	tidal: { status: "valid", expires_at: "2099-01-01T00:00:00Z", has_client_secret: true },
	ytm: { status: "signed_out", expires_at: null, has_client_secret: false },
};

describe("getAuthStatus", () => {
	it("returns the host's payload without reshaping it", async () => {
		call.mockResolvedValue(PAYLOAD);

		expect(await getAuthStatus()).toBe(PAYLOAD);
	});

	// The defect this function exists to prevent. A Tauri command declared
	// `Result<T, String>` rejects with that bare string, so a caller checking
	// `instanceof Error` discards the host's explanation and shows a generic
	// line instead — and nothing fails, because the rejection was handled.
	//
	// Both assertions are load-bearing: `toThrowError` alone matches the text of
	// a plain string rejection too, so a wrapper that rethrows the raw value
	// passes it while still breaking every caller.
	it("turns a failure into an Error carrying the host's message", async () => {
		const host = "the sidecar started and then stopped: sidecar_lost";
		call.mockRejectedValue(host);

		await expect(getAuthStatus()).rejects.toBeInstanceOf(Error);
		await expect(getAuthStatus()).rejects.toThrowError(host);
	});

	// A rejection the boundary cannot read must still be an `Error`. Rethrowing
	// it unchanged would leave every screen's `catch` holding something that is
	// not one, which is how the message above gets lost in the first place.
	it("rejects with an Error even when the failure carries nothing readable", async () => {
		call.mockRejectedValue(undefined);

		await expect(getAuthStatus()).rejects.toBeInstanceOf(Error);
		await expect(getAuthStatus()).rejects.toThrowError(UNREADABLE_STATUS);
	});
});

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
