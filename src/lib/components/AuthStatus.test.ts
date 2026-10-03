import { render, screen } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";
import AuthStatus from "./AuthStatus.svelte";

// These assert the rendered outcome through `role="status"`'s `data-state` and
// through whether a `<time>` element exists, never through the wording. Rewording
// either string cannot break this file, which is the point: five screens are about
// to copy the pattern, and copy assertions do not survive being copied.
//
// The three cases are the three cells of the (valid, has expiry) truth table that
// disagree with each other, each catching a one-sided mistake the other two cannot.
describe("AuthStatus", () => {
	// A signed-in state derived from the presence of an expiry string is the failure
	// this file exists to catch: the sidecar sends `expires_at` for a cached token
	// whether or not that token still works, so a user told "signed in" walks into a
	// transfer that cannot authenticate.
	it("reports an expired token as signed out and shows no expiry", () => {
		const { container } = render(AuthStatus, {
			props: { label: "Tidal", state: { valid: false, expires_at: "2020-01-01T00:00:00Z" } },
		});
		expect(screen.getByRole("status").dataset.state).toBe("signed-out");
		expect(container.querySelector("time")).toBeNull();
	});

	// Without this the card cannot answer the only question that matters once the
	// status is green: how long is that true? A dropped or mis-sourced expiry is
	// silent, so it is pinned here rather than left to review.
	it("reports a valid token as signed in and dates its expiry", () => {
		const { container } = render(AuthStatus, {
			props: { label: "Tidal", state: { valid: true, expires_at: "2099-01-01T00:00:00Z" } },
		});
		expect(screen.getByRole("status").dataset.state).toBe("signed-in");
		expect(container.querySelector("time")?.getAttribute("datetime")).toBe("2099-01-01T00:00:00Z");
	});

	// The mirror image of the first case: a provider that reports no expiry is still
	// signed in. A component that required an expiry to show one would report a
	// working YouTube Music token as broken and send the user to re-authenticate.
	it("reports a valid token as signed in when the provider sends no expiry", () => {
		const { container } = render(AuthStatus, {
			props: { label: "YouTube Music", state: { valid: true, expires_at: null } },
		});
		expect(screen.getByRole("status").dataset.state).toBe("signed-in");
		expect(container.querySelector("time")).toBeNull();
	});
});
