import { render, screen } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";
import AuthStatus from "./AuthStatus.svelte";

// A component that reports a signed-in state from the presence of an expiry
// string is the failure this file exists to catch. The sidecar sends
// `expires_at` for a cached token whether or not that token still works, so
// branching on the field's presence would report an expired session as a
// working one and send the user into a transfer that cannot authenticate.
describe("AuthStatus", () => {
	it("reports signed out when the token is expired even though an expiry is present", () => {
		render(AuthStatus, {
			props: { label: "Tidal", state: { valid: false, expires_at: "2020-01-01T00:00:00Z" } },
		});
		expect(screen.getByText("Not signed in")).toBeTruthy();
	});

	it("reports signed in for a valid token and shows the expiry", () => {
		render(AuthStatus, {
			props: { label: "Tidal", state: { valid: true, expires_at: "2099-01-01T00:00:00Z" } },
		});
		expect(screen.getByText("Signed in")).toBeTruthy();
		expect(screen.getByText(/2099-01-01/)).toBeTruthy();
	});

	it("reports signed in without an expiry when the provider reports none", () => {
		render(AuthStatus, {
			props: { label: "YouTube Music", state: { valid: true, expires_at: null } },
		});
		expect(screen.getByText("Signed in")).toBeTruthy();
	});

	it("names the provider it is reporting on", () => {
		render(AuthStatus, {
			props: { label: "Tidal", state: { valid: false, expires_at: null } },
		});
		expect(screen.getByText("Tidal")).toBeTruthy();
	});
});
