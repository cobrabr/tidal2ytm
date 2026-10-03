import { invoke } from "@tauri-apps/api/core";
import type { AuthStatus } from "./types";

/**
 * Shown when a failure carries nothing a person can act on.
 *
 * Owned here rather than in each screen: the boundary already decides what a
 * usable failure looks like, and two copies of this line is one too many.
 */
export const UNREADABLE_STATUS = "Could not read provider status.";

/**
 * Ask the host whether each provider has a usable cached token.
 *
 * Credentials never cross this boundary: the sidecar reports a boolean and an
 * expiry timestamp, the host forwards them unchanged, and the shape is the one
 * `contracts/auth_status.json` records. The host deserialises the answer into
 * its own `AuthStatus` before it gets here, so a payload that has drifted from
 * the contract fails at that boundary rather than arriving half-parsed.
 *
 * Failures are normalised into an `Error`. `invoke` rejects with whatever the
 * Rust command returned, and a command declared `Result<T, String>` rejects with
 * a bare string — so a screen that checked `instanceof Error` would silently
 * discard the host's explanation, which is the only text that says what actually
 * went wrong, and show a generic line instead. Normalising once, here, means a
 * screen never has to know how the boundary fails.
 */
export async function getAuthStatus(): Promise<AuthStatus> {
	try {
		return await invoke<AuthStatus>("get_auth_status");
	} catch (reason) {
		throw reason instanceof Error ? reason : new Error(message(reason));
	}
}

/** The text a rejection carries, or the generic line. */
function message(reason: unknown): string {
	return typeof reason === "string" && reason !== "" ? reason : UNREADABLE_STATUS;
}
