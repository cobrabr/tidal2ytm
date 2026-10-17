import { render, screen } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";
import AuthStatus from "./AuthStatus.svelte";

// These assert the rendered outcome through `role="status"`'s `data-state` and
// through whether a `<time>` element exists, never through the wording. Rewording
// either string cannot break this file, which is the point: five screens are about
// to copy the pattern, and copy assertions do not survive being copied.
//
// The four cases are the four answers the wire can carry — the three states and a
// valid provider that sends no expiry — each catching a mistake the others cannot.
describe("AuthStatus", () => {
	// A signed-in state derived from the presence of an expiry string is the failure
	// this file exists to catch: the sidecar sends `expires_at` for a cached token
	// whether or not that token still works, so a user told "signed in" walks into a
	// transfer that cannot authenticate.
	it("reports an expired token as signed out and shows no expiry", () => {
		const { container } = render(AuthStatus, {
			props: {
				label: "Tidal",
				state: { status: "signed_out", expires_at: "2020-01-01T00:00:00Z", has_client_secret: true },
			},
		});
		expect(screen.getByRole("status").dataset.state).toBe("signed-out");
		expect(container.querySelector("time")).toBeNull();
	});

	// Without this the card cannot answer the only question that matters once the
	// status is green: how long is that true? A dropped or mis-sourced expiry is
	// silent, so it is pinned here rather than left to review.
	it("reports a valid token as valid and dates its expiry", () => {
		const { container } = render(AuthStatus, {
			props: {
				label: "Tidal",
				state: { status: "valid", expires_at: "2099-01-01T00:00:00Z", has_client_secret: true },
			},
		});
		expect(screen.getByRole("status").dataset.state).toBe("valid");
		expect(container.querySelector("time")?.getAttribute("datetime")).toBe("2099-01-01T00:00:00Z");
	});

	// The mirror image of the first case: a provider that reports no expiry is still
	// signed in. A component that required an expiry to show one would report a
	// working YouTube Music token as broken and send the user to re-authenticate.
	it("reports a valid token as valid when the provider sends no expiry", () => {
		const { container } = render(AuthStatus, {
			props: {
				label: "YouTube Music",
				state: { status: "valid", expires_at: null, has_client_secret: false },
			},
		});
		expect(screen.getByRole("status").dataset.state).toBe("valid");
		expect(container.querySelector("time")).toBeNull();
	});

	// The state this file grew for. An access token that expired an hour ago
	// alongside a refresh token that lives for months is still a signed-in person,
	// and telling them otherwise sends them to re-authenticate over credentials
	// that still work. Collapsing it into either neighbour is the regression: into
	// `signed-out` it nags, into `valid` it overstates what is on disk.
	it("reports an expired token holding a refresh token as refreshable", () => {
		const { container } = render(AuthStatus, {
			props: {
				label: "Tidal",
				state: {
					status: "refreshable",
					expires_at: "2020-01-01T00:00:00Z",
					has_client_secret: true,
				},
			},
		});
		expect(screen.getByRole("status").dataset.state).toBe("refreshable");
		expect(container.querySelector("time")?.getAttribute("datetime")).toBe("2020-01-01T00:00:00Z");
	});
});
