import type { ProviderState } from "./types";

/**
 * The three states a provider can be in. Named apart from the wire's
 * `ProviderState` on purpose: the wire carries a plain string, and a type
 * whose name sits one word away from the wire's shape is a cast waiting to
 * happen — a cast that throws the narrowing away.
 */
export type KnownProviderStatus = "valid" | "refreshable" | "signed_out";

/** The badge variant that repeats a state. */
export type ProviderVariant = "success" | "warning" | "danger";

/** What one state says and wears. */
type ProviderAppearance = {
	label: string;
	detail: string;
	dot: string;
	variant: ProviderVariant;
};

/**
 * What each state says and wears. One record owns all four, so the dot the
 * Home row shows, the sentence the Diagnostics row shows and the pill's
 * colour cannot drift apart, and a fourth state is one edit rather than two.
 */
export const PROVIDER_STATES: Record<KnownProviderStatus, ProviderAppearance> = {
	valid: { label: "Signed in", detail: "token refreshes itself", dot: "bg-state-ok", variant: "success" },
	refreshable: {
		label: "Signed in, will refresh",
		detail: "token expires soon",
		dot: "bg-state-warn",
		variant: "warning",
	},
	signed_out: {
		label: "Not signed in",
		detail: "not signed in — run auth to fix",
		dot: "bg-state-bad",
		variant: "danger",
	},
};

/**
 * The status of a provider, narrowed to the three the app knows. The wire
 * carries it as a plain string, so a status this build does not know is
 * treated as signed out: it is not a session that works, and that is the
 * direction to be wrong in.
 */
export function providerStatus(state: ProviderState): KnownProviderStatus {
	if (state.status === "valid") return "valid";
	if (state.status === "refreshable") return "refreshable";
	return "signed_out";
}

/** The badge variant that repeats a provider's state. */
export function providerVariant(state: ProviderState): ProviderVariant {
	return PROVIDER_STATES[providerStatus(state)].variant;
}

/** The colour of the dot that marks a provider's state. */
export function providerDotClass(state: ProviderState): string {
	return PROVIDER_STATES[providerStatus(state)].dot;
}

/** The text of the pill that states a provider's condition. */
export function providerPillLabel(state: ProviderState): string {
	return PROVIDER_STATES[providerStatus(state)].label;
}

/** The sentence beside a provider's name. */
export function providerDetail(state: ProviderState): string {
	return PROVIDER_STATES[providerStatus(state)].detail;
}
