import { describe, expect, it } from "vitest";
import { providerDetail, providerDotClass, providerPillLabel, providerStatus, providerVariant } from "./provider-state";
import type { ProviderState } from "./types";

/** A provider state as the wire carries it, with the status under test. */
function stateWith(status: string): ProviderState {
	return { status, expires_at: null, has_client_secret: true };
}

describe("provider-state", () => {
	// A status this build does not know is not a session that works, so it
	// must answer exactly as a signed-out one does — in every answer the
	// module gives. The comparison is between the two results, so no colour
	// and no wording is locked here.
	it("treats a status this build does not know as signed out", () => {
		const unknown = stateWith("something_new");
		const signedOut = stateWith("signed_out");

		expect(providerStatus(unknown)).toBe(providerStatus(signedOut));
		expect(providerPillLabel(unknown)).toBe(providerPillLabel(signedOut));
		expect(providerDetail(unknown)).toBe(providerDetail(signedOut));
		expect(providerVariant(unknown)).toBe(providerVariant(signedOut));
		expect(providerDotClass(unknown)).toBe(providerDotClass(signedOut));
	});

	// The two states this build does know stay distinguishable, so the rule
	// above cannot be met by collapsing every status into one answer.
	it("tells the states it knows apart", () => {
		const known = ["valid", "refreshable", "signed_out"].map((status) => providerStatus(stateWith(status)));

		expect(new Set(known).size).toBe(known.length);
	});
});
