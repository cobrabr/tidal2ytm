import { open } from "@tauri-apps/plugin-dialog";
import { beginAuth, getAuthStatus, importClientSecret } from "./ipc";
import type { AuthStatus } from "./types";

/**
 * Start a provider's sign-in and return the status that follows it.
 *
 * Both screens offer the same two actions, and a copy of this per screen is how
 * one of them ends up re-reading the status and the other forgetting to. The
 * status is re-read here rather than left to the caller because a sign-in that
 * succeeded changes the state it just replaced.
 *
 * Failures reject: `ipc.ts` has already normalised whatever the boundary threw
 * into an `Error`, so a screen decides what to do with the text, never the shape.
 */
export async function startSignIn(provider: string): Promise<AuthStatus> {
	await beginAuth(provider);
	return getAuthStatus();
}

/**
 * Ask for a client-secret file, import it, and return the status that follows.
 *
 * `null` means the file dialog was dismissed: that is a person changing their
 * mind, not a failure, and reporting it as one puts an error on a screen where
 * nothing went wrong.
 */
export async function importClientSecretFile(): Promise<AuthStatus | null> {
	const chosen = await open({ filters: [{ name: "JSON", extensions: ["json"] }] });
	if (typeof chosen !== "string") return null;
	await importClientSecret(chosen);
	return getAuthStatus();
}
