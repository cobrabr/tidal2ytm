import { render, screen } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";
import ProviderRow from "./ProviderRow.svelte";
import { providerDetail, providerPillLabel, providerStateAttribute } from "../provider-state";
import type { ProviderState } from "../types";

const signedOut: ProviderState = { status: "signed_out", expires_at: null, has_client_secret: false };
const refreshable: ProviderState = {
	status: "refreshable",
	expires_at: "2099-01-01T00:00:00Z",
	has_client_secret: true,
};
const valid: ProviderState = { status: "valid", expires_at: "2099-01-01T00:00:00Z", has_client_secret: true };

describe("ProviderRow", () => {
	// Two rows sit side by side. Without the group's name, a screen reader
	// cannot tell which provider a state announcement belongs to.
	it("names the group after the provider", () => {
		render(ProviderRow, { props: { label: "YouTube Music", state: signedOut } });

		expect(screen.getByRole("group", { name: "YouTube Music" })).toBeTruthy();
	});

	// The attribute carries the outcome without depending on the pill's wording,
	// which is free to change; an async state change is announced in this region.
	it("announces the state with the attribute the shared vocabulary owns", () => {
		render(ProviderRow, { props: { label: "Tidal", state: refreshable } });

		expect(screen.getByRole("status").getAttribute("data-state")).toBe(providerStateAttribute(refreshable));
	});

	// Home and Diagnostics must say the same thing about a state. Reading both
	// the pill and the detail from the one owner is what keeps them in step.
	it("states the condition in the shared vocabulary", () => {
		render(ProviderRow, { props: { label: "Tidal", state: valid } });

		expect(screen.getByText(providerPillLabel(valid))).toBeTruthy();
		expect(screen.getByText(providerDetail(valid))).toBeTruthy();
	});
});
