import type { ProviderState } from "./types";

/** The three states a provider can be in. */
export type ProviderStatus = "valid" | "refreshable" | "signed_out";

/**
 * What each state says and wears. One record owns all three, so the dot the
 * Home row shows and the sentence the Diagnostics row shows cannot drift
 * apart, and neither can the pill's text.
 */
export const PROVIDER_STATES: Record<ProviderStatus, { label: string; detail: string; dot: string }> = {
	valid: { label: "Signed in", detail: "token refreshes itself", dot: "bg-state-ok" },
	refreshable: { label: "Signed in, will refresh", detail: "token expires soon", dot: "bg-state-warn" },
	signed_out: { label: "Not signed in", detail: "not signed in — run auth to fix", dot: "bg-state-bad" },
};

/**
 * The status of a provider, narrowed to the three the app knows. The wire
 * carries it as a plain string, so a status this build does not know is
 * treated as signed out: it is not a session that works, and that is the
 * direction to be wrong in.
 */
function statusOf(state: ProviderState): ProviderStatus {
	if (state.status === "valid") return "valid";
	if (state.status === "refreshable") return "refreshable";
	return "signed_out";
}

/** The colour of the dot that marks a provider's state. */
export function providerDotClass(state: ProviderState): string {
	return PROVIDER_STATES[statusOf(state)].dot;
}

/** The text of the pill that states a provider's condition. */
export function providerPillLabel(state: ProviderState): string {
	return PROVIDER_STATES[statusOf(state)].label;
}

/** The sentence beside a provider's name. */
export function providerDetail(state: ProviderState): string {
	return PROVIDER_STATES[statusOf(state)].detail;
}
