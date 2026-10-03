import { invoke } from "@tauri-apps/api/core";
import { describe, expect, it, vi } from "vitest";
import { UNREADABLE_STATUS, getAuthStatus } from "./ipc";
import type { AuthStatus } from "./types";

// The host, not this module, is what `invoke` talks to. Stubbing the Tauri API
// keeps these assertions about this module's own job — pass the payload through,
// turn a rejection into an `Error` — rather than about what the host returns.
vi.mock("@tauri-apps/api/core");

const call = vi.mocked(invoke);

const PAYLOAD: AuthStatus = {
	tidal: { valid: true, expires_at: "2099-01-01T00:00:00Z" },
	ytm: { valid: false, expires_at: null },
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
