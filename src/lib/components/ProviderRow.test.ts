import { render, screen } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";
import ProviderRow from "./ProviderRow.svelte";
import type { ProviderState } from "../types";

const signedOut: ProviderState = { status: "signed_out", expires_at: null, has_client_secret: false };
const refreshable: ProviderState = {
	status: "refreshable",
	expires_at: "2099-01-01T00:00:00Z",
	has_client_secret: true,
};
const valid: ProviderState = { status: "valid", expires_at: "2099-01-01T00:00:00Z", has_client_secret: true };

describe("ProviderRow", () => {
	it("offers a sign-in button when signed out", () => {
		render(ProviderRow, { props: { label: "Tidal", state: signedOut } });
		expect(screen.getByRole("button", { name: /sign in/i })).toBeTruthy();
	});

	it("offers no sign-in button and names the expiry when refreshable", () => {
		render(ProviderRow, { props: { label: "Tidal", state: refreshable } });
		expect(screen.queryByRole("button", { name: /sign in/i })).toBeNull();
		expect(screen.getByText("2099-01-01T00:00:00Z")).toBeTruthy();
	});

	it("offers no sign-in button when valid", () => {
		render(ProviderRow, { props: { label: "Tidal", state: valid } });
		expect(screen.queryByRole("button", { name: /sign in/i })).toBeNull();
	});
});
